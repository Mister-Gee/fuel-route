import hashlib
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from routing.models import Station, Trip
from routing.services import candidates
from routing.services.importer import import_prices

FIXTURES = Path(__file__).parent / "fixtures"
MILE_M = 1609.344


def provider(points, legs):
    assert len(points) == len(legs) + 1
    return {"code": "Ok", "waypoints": [{"location": [lon, lat], "distance": 0} for lat, lon in points], "routes": [{"distance": sum(legs) * MILE_M, "duration": 36000, "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in points]}, "legs": [{"distance": miles * MILE_M, "duration": 100, "annotation": {"distance": [miles * MILE_M]}} for miles in legs]}]}


class IntegrationTests(TestCase):
    def setUp(self):
        cache.clear()
        candidates._INDEX = None
        csv_path = FIXTURES / "integration_prices.csv"
        self.revision, _ = import_prices(csv_path, FIXTURES / "integration_coordinates.json", expected_csv_sha256=hashlib.sha256(csv_path.read_bytes()).hexdigest())
        self.start = {"latitude": 40.0, "longitude": -75.0}
        self.finish = {"latitude": 40.6, "longitude": -75.0}

    @patch("routing.services.osrm.requests.get")
    def test_import_api_final_legs_map_and_warm_cache(self, get):
        first = provider([(40.0, -75.0), (40.3, -75.0), (40.6, -75.0)], [450, 450])
        baseline = {"code": "Ok", "waypoints": [first["waypoints"][0], first["waypoints"][-1]], "routes": [{"distance": 900 * MILE_M, "duration": 36000, "geometry": first["routes"][0]["geometry"], "legs": [{"distance": 900 * MILE_M, "duration": 36000, "annotation": {"distance": [450 * MILE_M, 450 * MILE_M]}}]}]}
        final = provider([(40.0, -75.0), (40.3, -75.0), (40.6, -75.0)], [480, 480])
        get.side_effect = [Mock(json=Mock(return_value=baseline)), Mock(json=Mock(return_value=final))]
        body = json.dumps({"start": self.start, "finish": self.finish})
        with patch("routing.services.provider_cache.pace"):
            response = self.client.post(reverse("optimize-fuel"), data=body, content_type="application/json", HTTP_HOST="localhost:8000")
        self.assertEqual(response.status_code, 200, response.content)
        trip = response.json()
        self.assertEqual(get.call_count, 2)
        self.assertEqual(trip["metrics"]["routing_calls"], 2)
        self.assertEqual(trip["metrics"]["geocoding_calls"], 0)
        self.assertEqual(trip["route"]["leg_miles"], [480.0, 480.0])
        self.assertEqual(Station.objects.count(), 1)
        self.assertEqual(trip["fuel_stops"][0]["opis_id"], "A")
        self.assertEqual(Decimal(trip["summary"]["initial_gallons"]) + Decimal(trip["summary"]["purchased_gallons"]) - Decimal(trip["summary"]["consumed_gallons"]), Decimal(trip["summary"]["ending_gallons"]))
        self.assertEqual(sum(Decimal(item["cost"]) for item in trip["fuel_stops"]), Decimal(trip["summary"]["purchase_cost"]))
        self.assertEqual(self.client.get(trip["map_url"], HTTP_HOST="localhost:8000").status_code, 200)
        warm = self.client.post(reverse("optimize-fuel"), data=body, content_type="application/json").json()
        self.assertEqual(warm["metrics"]["routing_calls"], 0)
        self.assertTrue(warm["metrics"]["cache_hit"])
        self.assertEqual(get.call_count, 2)

    @patch("routing.services.osrm.requests.get")
    def test_bad_provider_geometry_fails_without_trip(self, get):
        payload = provider([(40.0, -75.0), (40.6, -75.0)], [100])
        payload["routes"][0]["geometry"]["coordinates"][0] = [1000, 40]
        get.return_value = Mock(json=Mock(return_value=payload))
        with patch("routing.services.provider_cache.pace"):
            result = self.client.post(reverse("optimize-fuel"), data=json.dumps({"start": self.start, "finish": self.finish}), content_type="application/json")
        self.assertEqual(result.status_code, 502)
        self.assertEqual(Trip.objects.count(), 0)
