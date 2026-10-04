"""Bounded baseline, waypoint validation and one deterministic repair."""
import time

from django.conf import settings

from routing.models import ImportRevision, Station, Trip
from routing.services import candidates, fuel_optimizer, osrm, trip_cache
from routing.services.geocoding import resolve_endpoint


class TripInfeasible(ValueError):
    pass


def _point(point):
    return (point["latitude"], point["longitude"])


def _optimize_route(route, selected):
    miles = route["leg_miles"]
    positions = []
    total = 0.0
    for leg in miles[:-1]:
        total += leg
        positions.append(total)
    if any(b <= a for a, b in zip([0.0] + positions, positions + [route["distance_miles"]])):
        raise TripInfeasible("Waypoint order or leg distance is invalid")
    try:
        return fuel_optimizer.optimize(route["distance_miles"], [(mile, station.price, station.opis_id) for mile, station in zip(positions, selected)])
    except fuel_optimizer.FuelInfeasible as exc:
        raise TripInfeasible(str(exc)) from exc


def _repair(selected, available, route=None, *, inaccessible_index=None):
    used = {(station.opis_id, round(station.route_mile, 7)) for station in selected}
    if inaccessible_index is not None and selected:
        # Prefer the closest same-progress station not already attempted.
        old = selected[inaccessible_index]
        alternate = min((candidate for candidate in available if (candidate.opis_id, round(candidate.route_mile, 7)) not in used), key=lambda candidate: (abs(candidate.route_mile - old.route_mile), candidate.opis_id), default=None)
        if alternate and abs(alternate.route_mile - old.route_mile) <= 50:
            return sorted(selected[:inaccessible_index] + selected[inaccessible_index + 1:] + [alternate], key=lambda item: (item.route_mile, item.opis_id))
        return None
    if route is None:
        return None
    running = 0.0
    legs = route["leg_miles"]
    for index, leg in enumerate(legs):
        if leg > 500:
            left = selected[index - 1].route_mile if index else 0.0
            right = selected[index].route_mile if index < len(selected) else route["distance_miles"]
            midpoint = (left + right) / 2
            option = min((candidate for candidate in available if (candidate.opis_id, round(candidate.route_mile, 7)) not in used and left < candidate.route_mile < right), key=lambda candidate: (abs(candidate.route_mile - midpoint), candidate.opis_id), default=None)
            if option:
                return sorted(selected + [option], key=lambda item: (item.route_mile, item.opis_id))
            return None
        running += leg
    return None


def plan_trip(start_input, finish_input, *, geocoder_session=None, router_session=None):
    began = time.monotonic()
    metrics = {"routing_calls": 0, "geocoding_calls": 0, "cache_hit": False}
    geo_args = {"metrics": metrics}
    if geocoder_session is not None:
        geo_args["session"] = geocoder_session
    start = resolve_endpoint(start_input, **geo_args)
    finish = resolve_endpoint(finish_input, **geo_args)
    revision = ImportRevision.objects.filter(is_active=True).first()
    if revision is None:
        raise TripInfeasible("No imported station revision is active")
    trip_key = trip_cache.key(start, finish, revision)
    cached_id = trip_cache.get(trip_key)
    if cached_id:
        cached = Trip.objects.filter(pk=cached_id, revision=revision).first()
        if cached:
            response = dict(cached.response)
            response["start"] = start
            response["finish"] = finish
            response["metrics"] = {"routing_calls": 0, "geocoding_calls": metrics["geocoding_calls"], "cache_hit": True, "elapsed_ms": round((time.monotonic() - began) * 1000, 3)}
            return response
    route_args = {"metrics": metrics}
    if router_session is not None:
        route_args["session"] = router_session
    baseline = osrm.route([_point(start), _point(finish)], **route_args)
    if baseline["distance_miles"] <= 500:
        final = baseline
        selected = []
        ledger = _optimize_route(final, selected)
    else:
        available = candidates.candidates(baseline, revision, corridor_miles=settings.STATION_CORRIDOR_MILES)
        if not available:
            raise TripInfeasible("No accepted station in the route corridor")
        projected = {}
        for item in available:
            projected.setdefault(round(item.route_mile, 7), item)
            if (item.price, item.opis_id) < (projected[round(item.route_mile, 7)].price, projected[round(item.route_mile, 7)].opis_id):
                projected[round(item.route_mile, 7)] = item
        preliminary_candidates = sorted(projected.values(), key=lambda item: (item.route_mile, item.opis_id))
        try:
            preliminary = fuel_optimizer.optimize(baseline["distance_miles"], [(item.route_mile, item.price, item.opis_id) for item in preliminary_candidates])
        except fuel_optimizer.FuelInfeasible as exc:
            raise TripInfeasible("No feasible accepted-station coverage: " + str(exc)) from exc
        selected = [item for item, stop in zip(preliminary_candidates, preliminary["stops"]) if stop.gallons > 0]
        if not selected:
            raise TripInfeasible("No reachable fuel purchase was found")
        points = [_point(start)] + [(item.latitude, item.longitude) for item in selected] + [_point(finish)]
        try:
            final = osrm.route(points, **route_args)
            ledger = _optimize_route(final, selected)
        except osrm.RouteAccessInvalid as exc:
            replacement = _repair(selected, available, inaccessible_index=exc.waypoint_index)
            if replacement is None:
                raise TripInfeasible("Selected station is not accessible within the route budget")
            selected = replacement
            try:
                final = osrm.route([_point(start)] + [(item.latitude, item.longitude) for item in selected] + [_point(finish)], **route_args)
            except osrm.RouteAccessInvalid as second_exc:
                raise TripInfeasible("Replacement station is not accessible within the route budget") from second_exc
            ledger = _optimize_route(final, selected)
        except TripInfeasible:
            replacement = _repair(selected, available, final)
            if replacement is None:
                raise
            selected = replacement
            try:
                final = osrm.route([_point(start)] + [(item.latitude, item.longitude) for item in selected] + [_point(finish)], **route_args)
            except osrm.RouteAccessInvalid as second_exc:
                raise TripInfeasible("Repair station is not accessible within the route budget") from second_exc
            ledger = _optimize_route(final, selected)
    identities = Station.objects.in_bulk({item.opis_id for item in selected}, field_name="opis_id")
    descriptions = []
    for item, purchase in zip(selected, ledger["display_stops"]):
        purchase = dict(purchase)
        station = identities.get(item.opis_id)
        purchase.update({"latitude": item.latitude, "longitude": item.longitude, "linked_ids": list(item.linked_ids), "coordinate_evidence": item.evidence, "price_warning": item.warning, "name": station.name if station else None, "address": station.address if station else None, "city": station.city if station else None, "state": station.state if station else None})
        descriptions.append(purchase)
    response = {"start": start, "finish": finish, "route": {"geometry": final["geometry"], "distance_miles": final["distance_miles"], "duration_seconds": final["duration_seconds"], "leg_miles": final["leg_miles"]}, "waypoints": [{"opis_id": item.opis_id, "latitude": item.latitude, "longitude": item.longitude} for item in selected], "fuel_stops": descriptions, "summary": {"initial_gallons": "50", "purchased_gallons": str(ledger["purchased_gallons"]), "consumed_gallons": str(ledger["consumed_gallons"]), "ending_gallons": str(ledger["ending_gallons"]), "purchase_cost": str(ledger["display_cost"])}, "vehicle": {"capacity_gallons": 50, "range_miles": 500, "miles_per_gallon": 10, "starting_fuel_included_in_cost": False}, "dataset": {"revision": revision.digest, "price_policy": revision.price_policy, "accepted_count": revision.accepted_count, "unresolved_count": revision.unresolved_count}, "optimality": "Fixed-route accepted-candidate approximation; final purchases optimized for the validated waypoint sequence", "metrics": {**metrics, "elapsed_ms": round((time.monotonic() - began) * 1000, 3)}}
    trip = Trip.objects.create(revision=revision, response=response)
    response["trip_id"] = str(trip.pk)
    trip.response = response
    trip.save(update_fields=["response"])
    trip_cache.put(trip_key, str(trip.pk))
    return response
