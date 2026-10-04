"""Strict OSRM route adapter. Coordinates at the boundary are (lat, lon)."""
import math

import requests
from django.conf import settings

from routing.services.provider_cache import get_route, pace, put_route, route_key

METERS_PER_MILE = 1609.344
SNAP_RADIUS_METERS = 200


class RouteError(ValueError):
    pass


class RouteUnavailable(RouteError):
    pass


class RouteInvalid(RouteError):
    pass


class RouteAccessInvalid(RouteInvalid):
    def __init__(self, message, waypoint_index):
        super().__init__(message)
        self.waypoint_index = waypoint_index


def _number(value):
    try:
        number = None if isinstance(value, bool) or not isinstance(value, (float, int)) else float(value)
    except OverflowError:
        number = None
    if number is None or not math.isfinite(number) or number < 0:
        raise RouteInvalid("OSRM returned a nonfinite or negative metric")
    return number


def _point(value):
    if not isinstance(value, list) or len(value) != 2:
        raise RouteInvalid("OSRM returned an invalid coordinate")
    if any(isinstance(x, bool) or not isinstance(x, (float, int)) for x in value):
        raise RouteInvalid("OSRM returned an invalid coordinate")
    try:
        lon, lat = (float(x) for x in value)
    except OverflowError as exc:
        raise RouteInvalid("OSRM returned an invalid coordinate") from exc
    if not all(math.isfinite(x) for x in (lon, lat)) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise RouteInvalid("OSRM returned an invalid coordinate")
    return (lat, lon)


def _separation_m(a, b):
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371000 * math.asin(min(1, math.sqrt(h)))


def parse_route(payload, requested, snap_radius=SNAP_RADIUS_METERS):
    try:
        if not isinstance(payload, dict) or payload.get("code") != "Ok":
            raise RouteInvalid("OSRM did not return a valid route")
        routes, waypoints = payload["routes"], payload["waypoints"]
        if not isinstance(routes, list) or len(routes) != 1 or not isinstance(waypoints, list) or len(waypoints) != len(requested):
            raise RouteInvalid("OSRM route or waypoint count mismatch")
        route = routes[0]
        geometry = route["geometry"]
        points = geometry["coordinates"]
        if not isinstance(geometry, dict) or geometry.get("type") != "LineString" or not isinstance(points, list) or len(points) < 2:
            raise RouteInvalid("OSRM geometry is missing")
        points = [_point(point) for point in points]
        legs = route["legs"]
        if not isinstance(legs, list) or len(legs) != len(requested) - 1:
            raise RouteInvalid("OSRM leg count mismatch")
        snapped = []
        for index, (given, waypoint) in enumerate(zip(requested, waypoints)):
            actual = _point(waypoint["location"])
            if _separation_m(given, actual) > snap_radius or _number(waypoint["distance"]) > snap_radius:
                if index == 0 or index == len(waypoints) - 1:
                    raise RouteInvalid("OSRM endpoint is too far from requested point")
                raise RouteAccessInvalid("OSRM station waypoint is too far from requested point", index - 1)
            snapped.append(actual)
        segments = []
        leg_meters = []
        boundary = 0
        for leg in legs:
            if not isinstance(leg, dict) or not isinstance(leg.get("annotation"), dict):
                raise RouteInvalid("OSRM leg annotations missing")
            meters = _number(leg["distance"])
            _number(leg["duration"])
            steps = leg["annotation"]["distance"]
            if not isinstance(steps, list) or not steps:
                raise RouteInvalid("OSRM segment annotations missing")
            steps = [_number(step) for step in steps]
            if abs(sum(steps) - meters) > max(10, meters * 0.001):
                raise RouteInvalid("OSRM leg annotations disagree with distance")
            segments.extend(steps)
            leg_meters.append(meters)
            boundary += len(steps)
            if boundary >= len(points) or _separation_m(points[boundary], snapped[len(leg_meters)]) > snap_radius:
                raise RouteInvalid("OSRM leg boundary does not match ordered waypoint geometry")
        total = _number(route["distance"])
        duration = _number(route["duration"])
        if len(segments) != len(points) - 1 or abs(sum(leg_meters) - total) > max(10, total * 0.001):
            raise RouteInvalid("OSRM geometry, annotations or legs are misaligned")
        if _separation_m(points[0], snapped[0]) > snap_radius or _separation_m(points[-1], snapped[-1]) > snap_radius:
            raise RouteInvalid("OSRM geometry endpoints are misaligned")
        return {"geometry": geometry, "points": points, "segment_miles": [x / METERS_PER_MILE for x in segments], "leg_miles": [x / METERS_PER_MILE for x in leg_meters], "distance_miles": sum(leg_meters) / METERS_PER_MILE, "duration_seconds": duration, "snapped": snapped}
    except (KeyError, TypeError, IndexError, OverflowError) as exc:
        raise RouteInvalid("OSRM response is incomplete") from exc


def _parse_int(text):
    try:
        return int(text)
    except ValueError as exc:
        raise RouteInvalid("OSRM response contains an out-of-range number") from exc


def route(coordinates, *, metrics=None, session=requests):
    if metrics is None:
        metrics = {}
    if len(coordinates) < 2:
        raise RouteInvalid("At least two route points are required")
    base = settings.OSRM_BASE_URL
    key = route_key(base, coordinates, SNAP_RADIUS_METERS)
    payload = get_route(key)
    if payload is None:
        path = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in coordinates)
        pace("osrm", max(1.0, settings.OSRM_MIN_REQUEST_INTERVAL_SECONDS))
        try:
            metrics["routing_calls"] = metrics.get("routing_calls", 0) + 1
            response = session.get(f"{base}/route/v1/driving/{path}", params={"overview": "full", "geometries": "geojson", "annotations": "distance", "steps": "false"}, headers={"User-Agent": "SpotterAssessment/1.0 (Django local assessment)"}, timeout=settings.OSRM_TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = response.json(parse_int=_parse_int)
        except RouteInvalid:
            raise
        except RecursionError as exc:
            raise RouteInvalid("OSRM response is nested too deeply") from exc
        except (requests.RequestException, ValueError) as exc:
            raise RouteUnavailable("Routing provider is unavailable") from exc
        parsed = parse_route(payload, coordinates)
        put_route(key, payload)
        return parsed
    return parse_route(payload, coordinates)
