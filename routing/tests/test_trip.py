from decimal import Decimal
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import TestCase

from routing.models import ImportRevision, Station, Trip
from routing.services.candidates import Candidate
from routing.services.osrm import RouteAccessInvalid, RouteInvalid
from routing.services.trip import TripInfeasible, plan_trip

START = {"latitude": 40.7, "longitude": -73.98}
FINISH = {"latitude": 40.8, "longitude": -73.9}


def route_result(legs):
    return {"leg_miles": legs, "distance_miles": sum(legs), "duration_seconds": 100, "geometry": {"type": "LineString", "coordinates": [[-73.98, 40.7], [-73.9, 40.8]]}, "points": [(40.7, -73.98), (40.8, -73.9)], "segment_miles": [sum(legs)]}


class TripTests(TestCase):
    def setUp(self):
        cache.clear()
        self.revision = ImportRevision.objects.create(digest="a" * 64, is_active=True, csv_sha256="b" * 64, sidecar_sha256="c" * 64, csv_count=2, accepted_count=2, unresolved_count=0, imported_count=2, price_policy="mean")
        self.stations = [Candidate("A", 40.75, -73.95, 450, Decimal("2"), ("A",)), Candidate("B", 40.76, -73.94, 700, Decimal("5"), ("B",))]

    @patch("routing.services.trip.osrm.route")
    def test_short_route_and_warm_cache(self, routing):
        routing.side_effect = lambda points, **kwargs: (kwargs["metrics"].__setitem__("routing_calls", kwargs["metrics"]["routing_calls"] + 1) or route_result([400]))
        first = plan_trip(START, FINISH)
        self.assertEqual(first["metrics"]["routing_calls"], 1)
        self.assertEqual(first["summary"]["purchase_cost"], "0")
        second = plan_trip(START, FINISH)
        self.assertEqual(second["trip_id"], first["trip_id"])
        self.assertEqual(second["metrics"]["routing_calls"], 0)
        self.assertTrue(second["metrics"]["cache_hit"])
        self.assertEqual(routing.call_count, 1)

    @patch("routing.services.trip.candidates.candidates")
    @patch("routing.services.trip.osrm.route")
    def test_normal_stop_uses_two_routes_and_final_legs(self, routing, candidates):
        candidates.return_value = self.stations
        routes = [route_result([900]), route_result([480, 480])]
        routing.side_effect = lambda points, **kwargs: (kwargs["metrics"].__setitem__("routing_calls", kwargs["metrics"]["routing_calls"] + 1) or routes.pop(0))
        response = plan_trip(START, FINISH)
        self.assertEqual(response["metrics"]["routing_calls"], 2)
        self.assertEqual(response["route"]["distance_miles"], 960)
        self.assertEqual(Decimal(response["summary"]["consumed_gallons"]), Decimal("96"))
        self.assertEqual(len(response["waypoints"]), 1)

    @patch("routing.services.trip.candidates.candidates")
    @patch("routing.services.trip.osrm.route")
    def test_inaccessible_waypoint_gets_one_replacement(self, routing, candidates):
        candidates.return_value = [self.stations[0], Candidate("C", 40.751, -73.951, 451, Decimal("3"), ("C",)), self.stations[1]]
        routes = [route_result([900]), RouteAccessInvalid("bad snap", 0), route_result([450, 450])]
        def fake(points, **kwargs):
            kwargs["metrics"]["routing_calls"] += 1
            value = routes.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        routing.side_effect = fake
        response = plan_trip(START, FINISH)
        self.assertEqual(response["metrics"]["routing_calls"], 3)
        self.assertEqual(response["waypoints"][0]["opis_id"], "C")

    @patch("routing.services.trip.candidates.candidates")
    @patch("routing.services.trip.osrm.route")
    def test_final_leg_recalculation_and_one_repair(self, routing, candidates):
        candidates.return_value = self.stations
        routes = [route_result([900]), route_result([450, 550]), route_result([450, 300, 250])]
        def fake(points, **kwargs):
            kwargs["metrics"]["routing_calls"] += 1
            return routes.pop(0)
        routing.side_effect = fake
        response = plan_trip(START, FINISH)
        self.assertEqual(response["metrics"]["routing_calls"], 3)
        self.assertEqual(len(response["waypoints"]), 2)
        self.assertEqual(response["route"]["leg_miles"], [450, 300, 250])
        self.assertEqual(Decimal(response["summary"]["initial_gallons"]) + Decimal(response["summary"]["purchased_gallons"]) - Decimal(response["summary"]["consumed_gallons"]), Decimal(response["summary"]["ending_gallons"]))
        self.assertEqual(sum(Decimal(stop["cost"]) for stop in response["fuel_stops"]), Decimal(response["summary"]["purchase_cost"]))
        self.assertEqual(routing.call_count, 3)

    @patch("routing.services.trip.candidates.candidates")
    @patch("routing.services.trip.osrm.route")
    def test_exhausted_repair_returns_infeasible(self, routing, candidates):
        candidates.return_value = self.stations[:1]
        routes = [route_result([900]), route_result([450, 550])]
        routing.side_effect = lambda points, **kwargs: (kwargs["metrics"].__setitem__("routing_calls", kwargs["metrics"]["routing_calls"] + 1) or routes.pop(0))
        with self.assertRaises(TripInfeasible):
            plan_trip(START, FINISH)
        self.assertEqual(routing.call_count, 2)
        self.assertEqual(Trip.objects.count(), 0)

    @patch("routing.services.trip.candidates.candidates")
    @patch("routing.services.trip.osrm.route")
    def test_failed_third_route_stores_no_trip(self, routing, candidates):
        candidates.return_value = self.stations
        routes = [route_result([900]), route_result([450, 550]), route_result([450, 550, 100])]
        routing.side_effect = lambda points, **kwargs: (kwargs["metrics"].__setitem__("routing_calls", kwargs["metrics"]["routing_calls"] + 1) or routes.pop(0))
        with self.assertRaises(TripInfeasible):
            plan_trip(START, FINISH)
        self.assertEqual(routing.call_count, 3)
        self.assertEqual(Trip.objects.count(), 0)

    @patch("routing.services.trip.osrm.route")
    def test_repeated_station_visit_remains_two_waypoints(self, routing):
        Station.objects.create(opis_id="A", latitude=40.3, longitude=-75, price=Decimal("3"), name="Station", address="Main", city="New York", state="NY", raw_quotes=["3"], provenance={}, coordinate_evidence="direct", price_policy="mean", revision=self.revision)
        start = {"latitude": 40, "longitude": -75}
        finish = {"latitude": 40.1, "longitude": -75}
        geometry = [(40, -75), (40.5, -75), (40, -75), (40.1, -75)]
        baseline = {"points": geometry, "segment_miles": [500, 500, 200], "distance_miles": 1200, "leg_miles": [1200], "duration_seconds": 100, "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in geometry]}}
        final = route_result([300, 400, 500])
        routes = [baseline, final]
        routing.side_effect = lambda points, **kwargs: (kwargs["metrics"].__setitem__("routing_calls", kwargs["metrics"]["routing_calls"] + 1) or routes.pop(0))
        response = plan_trip(start, finish)
        self.assertEqual([item["opis_id"] for item in response["waypoints"]], ["A", "A"])
        self.assertEqual(Decimal(response["summary"]["purchased_gallons"]), Decimal("70"))
        self.assertEqual(response["metrics"]["routing_calls"], 2)

    @patch("routing.services.trip.candidates.candidates")
    def test_malformed_final_geometry_cannot_store_trip(self, candidates):
        candidates.return_value = self.stations[:1]
        def provider(points, legs):
            return {"code": "Ok", "waypoints": [{"location": [lon, lat], "distance": 0} for lat, lon in points], "routes": [{"distance": sum(legs), "duration": 100, "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in points]}, "legs": [{"distance": leg, "duration": 20, "annotation": {"distance": [leg]}} for leg in legs]}]}
        first = provider([(START["latitude"], START["longitude"]), (FINISH["latitude"], FINISH["longitude"])], [900 * 1609.344])
        final = provider([(START["latitude"], START["longitude"]), (self.stations[0].latitude, self.stations[0].longitude), (FINISH["latitude"], FINISH["longitude"])], [450 * 1609.344, 450 * 1609.344])
        final["routes"][0]["geometry"]["coordinates"][1] = [-75, 40.75]
        responses = []
        for item in (first, final):
            response = Mock()
            response.json.return_value = item
            responses.append(response)
        session = Mock(get=Mock(side_effect=responses))
        with self.assertRaises(RouteInvalid):
            plan_trip(START, FINISH, router_session=session)
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(Trip.objects.count(), 0)

    @patch("routing.services.trip.osrm.route")
    def test_active_revision_not_latest_history_controls_cache(self, routing):
        routing.side_effect = lambda points, **kwargs: (kwargs["metrics"].__setitem__("routing_calls", kwargs["metrics"]["routing_calls"] + 1) or route_result([400]))
        first = plan_trip(START, FINISH)
        self.revision.is_active = False
        self.revision.save(update_fields=["is_active"])
        later = ImportRevision.objects.create(digest="d" * 64, is_active=True, csv_sha256="b" * 64, sidecar_sha256="c" * 64, csv_count=2, accepted_count=2, unresolved_count=0, imported_count=2, price_policy="min")
        second = plan_trip(START, FINISH)
        self.assertNotEqual(first["trip_id"], second["trip_id"])
        self.assertEqual(second["metrics"]["routing_calls"], 1)
        later.is_active = False
        later.save(update_fields=["is_active"])
        self.revision.is_active = True
        self.revision.save(update_fields=["is_active"])
        restored = plan_trip(START, FINISH)
        self.assertEqual(restored["trip_id"], first["trip_id"])
