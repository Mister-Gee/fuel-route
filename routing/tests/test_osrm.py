import copy
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import requests

from django.test import TestCase, TransactionTestCase, override_settings

from routing.services.osrm import RouteAccessInvalid, RouteInvalid, RouteUnavailable, parse_route, route


def payload(points, leg_meters):
    return {"code": "Ok", "waypoints": [{"location": [lon, lat], "distance": 0} for lat, lon in points], "routes": [{"distance": sum(leg_meters), "duration": 100, "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in points]}, "legs": [{"distance": distance, "duration": 20, "annotation": {"distance": [distance]}} for distance in leg_meters]}]}


class OsrmTests(TestCase):
    @override_settings(OSRM_MIN_REQUEST_INTERVAL_SECONDS=0)
    def test_route_parses_and_caches_three_legs(self):
        points = [(40.7, -73.98), (40.8, -73.9), (40.9, -73.8), (41.0, -73.7)]
        response = Mock()
        response.json.return_value = payload(points, [1000, 2000, 3000])
        session = Mock(get=Mock(return_value=response))
        metrics = {}
        first = route(points, metrics=metrics, session=session)
        second = route(points, metrics=metrics, session=session)
        self.assertEqual(first["leg_miles"], second["leg_miles"])
        self.assertEqual(len(first["segment_miles"]), 3)
        self.assertEqual(metrics["routing_calls"], 1)
        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(session.get.call_args.kwargs["params"]["annotations"], "distance")

    def test_enormous_or_nonfinite_provider_numbers_are_route_invalid(self):
        points = [(40.7, -73.98), (40.8, -73.9)]
        base = payload(points, [1000])
        for mutate in (
            lambda b: b["routes"][0].__setitem__("distance", 10**400),
            lambda b: b["routes"][0].__setitem__("duration", 10**400),
            lambda b: b["routes"][0]["legs"][0].__setitem__("distance", 10**400),
            lambda b: b["routes"][0]["legs"][0]["annotation"].__setitem__("distance", [10**400]),
            lambda b: b["routes"][0]["geometry"]["coordinates"].__setitem__(0, [10**400, 40.7]),
            lambda b: b["waypoints"][0].__setitem__("location", [-73.98, 10**400]),
            lambda b: b["routes"][0].__setitem__("distance", float("inf")),
        ):
            broken = copy.deepcopy(base)
            mutate(broken)
            with self.assertRaises(RouteInvalid):
                parse_route(broken, points)

    @override_settings(OSRM_MIN_REQUEST_INTERVAL_SECONDS=0)
    def test_unparseable_large_integer_or_deep_nesting_is_route_invalid(self):
        points = [(40.7, -73.98), (40.8, -73.9)]
        for body in (b'{"code":"Ok","routes":[{"distance":' + b"9" * 5000 + b"}]}", b"[" * 100000 + b"]" * 100000):
            response = requests.Response()
            response.status_code = 200
            response._content = body
            with self.assertRaises(RouteInvalid):
                route(points, session=Mock(get=Mock(return_value=response)))

    @override_settings(OSRM_MIN_REQUEST_INTERVAL_SECONDS=0)
    def test_malformed_data_and_access_failure(self):
        points = [(40.7, -73.98), (40.8, -73.9)]
        base = payload(points, [1000])
        broken = copy.deepcopy(base)
        broken["routes"][0]["legs"][0]["annotation"] = None
        with self.assertRaises(RouteInvalid):
            from routing.services.osrm import parse_route
            parse_route(broken, points)
        broken = copy.deepcopy(base)
        broken["routes"][0]["geometry"]["coordinates"].append([-73.7, 41.0])
        with self.assertRaises(RouteInvalid):
            parse_route(broken, points)
        for field in ("distance", "duration"):
            broken = copy.deepcopy(base)
            broken["routes"][0][field] = float("nan")
            with self.assertRaises(RouteInvalid):
                parse_route(broken, points)
        interior = [(40.7, -73.98), (40.8, -73.9), (40.9, -73.8), (41.0, -73.7)]
        broken = payload(interior, [1000, 1000, 1000])
        broken["routes"][0]["geometry"]["coordinates"][1] = [-74.0, 40.8]
        with self.assertRaises(RouteInvalid):
            parse_route(broken, interior)
        broken = payload(interior, [1000, 1000, 1000])
        broken["routes"][0]["geometry"]["coordinates"][1:3] = list(reversed(broken["routes"][0]["geometry"]["coordinates"][1:3]))
        with self.assertRaises(RouteInvalid):
            parse_route(broken, interior)
        broken = payload(interior, [1000, 1000, 1000])
        broken["routes"][0]["legs"].pop()
        with self.assertRaises(RouteInvalid):
            parse_route(broken, interior)
        middle = (40.75, -73.94)
        broken = payload([points[0], middle, points[1]], [500, 500])
        broken["waypoints"][1]["distance"] = 500
        with self.assertRaises(RouteAccessInvalid):
            parse_route(broken, [points[0], middle, points[1]])
        response = Mock()
        response.json.return_value = base
        session = Mock(get=Mock(return_value=response))
        session.get.side_effect = __import__("requests").Timeout()
        with self.assertRaises(RouteUnavailable):
            route(points, session=session)


class PaceTests(TransactionTestCase):
    def test_concurrent_uncached_http_requests_are_spaced(self):
        points = [(40.7, -73.98), (40.8, -73.9)]
        sent = []
        def request(points):
            response = Mock()
            response.json.return_value = payload(points, [1000])
            def get(*args, **kwargs):
                sent.append(time.monotonic())
                return response
            metrics = {}
            route(points, metrics=metrics, session=Mock(get=get))
            return metrics["routing_calls"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            calls = list(pool.map(request, (points, [(40.71, -73.98), (40.81, -73.9)])))
        self.assertEqual(calls, [1, 1])
        self.assertEqual(len(sent), 2)
        self.assertGreaterEqual(abs(sent[1] - sent[0]), 0.95)
