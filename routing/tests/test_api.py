import base64
import json
import re
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from routing.models import ImportRevision, Trip
from routing.services.osrm import RouteInvalid
from routing.services.trip import TripInfeasible


class ApiTests(TestCase):
    def setUp(self):
        self.revision = ImportRevision.objects.create(digest="a" * 64, is_active=True, csv_sha256="b" * 64, sidecar_sha256="c" * 64, csv_count=1, accepted_count=1, unresolved_count=0, imported_count=1, price_policy="mean")
        self.body = {"start": {"latitude": 40, "longitude": -75}, "finish": {"latitude": 40.1, "longitude": -75}}

    def post(self, body=None, **kwargs):
        return self.client.post(reverse("optimize-fuel"), data=json.dumps(self.body if body is None else body), content_type="application/json", **kwargs)

    def test_health_and_safe_map(self):
        self.assertEqual(self.client.get(reverse("health")).json()["dataset"]["revision"], self.revision.digest)
        payload = {"trip_id": "00000000-0000-0000-0000-000000000001", "start": self.body["start"], "finish": self.body["finish"], "route": {"geometry": {"type": "LineString", "coordinates": [[-75, 40], [-75, 40.1]]}, "distance_miles": 10.0}, "fuel_stops": [{"opis_id": "<script>alert(1)</script>", "latitude": 40.05, "longitude": -75, "gallons": "1", "price": "3"}], "summary": {"purchase_cost": "3.00"}}
        Trip.objects.create(id=payload["trip_id"], revision=self.revision, response=payload)
        with patch("routing.views.plan_trip", return_value=payload):
            response = self.post(HTTP_HOST="localhost:8000")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["map_url"], "http://localhost:8000/api/v1/routes/00000000-0000-0000-0000-000000000001/map/")
        page = self.client.get(response.json()["map_url"], HTTP_HOST="localhost:8000")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers["Referrer-Policy"], "strict-origin-when-cross-origin")
        self.assertContains(page, "OpenStreetMap contributors")
        self.assertContains(page, "L.geoJSON")
        self.assertNotIn(b"<script>alert(1)</script>", page.content)
        integrities = re.findall(r'integrity="(sha256|sha384|sha512)-([^"]+)"', page.content.decode())
        self.assertEqual(len(integrities), 2)
        for algorithm, digest in integrities:
            self.assertEqual(len(base64.b64decode(digest, validate=True)), {"sha256": 32, "sha384": 48, "sha512": 64}[algorithm])
        self.revision.is_active = False
        self.revision.save(update_fields=["is_active"])
        self.assertEqual(self.client.get(response.json()["map_url"], HTTP_HOST="localhost:8000").status_code, 404)

    def test_oversized_numeric_literals_return_400(self):
        for number in ("1" + "0" * 400, "9" * 5000):
            raw = ('{"start":{"latitude":%s,"longitude":0},"finish":{"latitude":1,"longitude":1}}' % number).encode()
            response = self.client.post(reverse("optimize-fuel"), data=raw, content_type="application/json")
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"]["code"], "invalid_request")

    def test_deep_nesting_surrogates_and_oversized_body_return_json_400(self):
        bodies = [b"[" * 3000 + b"]" * 3000, b'{"start":"Chicago \\ud800","finish":{"latitude":1,"longitude":1}}']
        bodies.append(('{"start":"%s","finish":"Dallas"}' % ("x" * 3_000_000)).encode())
        with patch("routing.views.plan_trip", side_effect=AssertionError("must not plan")):
            for raw in bodies:
                response = self.client.post(reverse("optimize-fuel"), data=raw, content_type="application/json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], "invalid_request")

    def test_bad_requests_and_typed_service_errors(self):
        for raw in (b"{", b"[]", b"null"):
            response = self.client.post(reverse("optimize-fuel"), data=raw, content_type="application/json")
            self.assertEqual(response.status_code, 400)
        for endpoint in ({"latitude": None, "longitude": -75}, {"latitude": True, "longitude": -75}, {"latitude": 100, "longitude": -75}, {"latitude": {}, "longitude": -75}):
            self.assertEqual(self.post({"start": endpoint, "finish": self.body["finish"]}).status_code, 400)
        self.assertEqual(self.client.post(reverse("optimize-fuel"), data="{}", content_type="text/plain").status_code, 400)
        for error, status in ((TripInfeasible("gap"), 422), (RouteInvalid("bad geometry"), 502)):
            with patch("routing.views.plan_trip", side_effect=error):
                self.assertEqual(self.post().status_code, status)
        with override_settings(OSRM_BASE_URL=""):
            self.assertEqual(self.post().status_code, 503)
        self.revision.is_active = False
        self.revision.save(update_fields=["is_active"])
        self.assertEqual(self.post().status_code, 503)
        self.assertFalse(self.client.get(reverse("health")).json()["ready"])
