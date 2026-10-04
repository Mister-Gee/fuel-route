from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from routing.services.geocoding import GeocoderUnavailable, ResolutionError, resolve_endpoint
from routing.services.geography import BoundaryUnavailable, in_supported_us, us_boundary


class GeocodingTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_offline_membership(self):
        for lat, lon in ((40.7, -73.98), (61.2, -149.9), (21.3, -157.8)):
            self.assertTrue(in_supported_us(lat, lon))
            self.assertEqual(resolve_endpoint({"latitude": lat, "longitude": lon})["latitude"], lat)
        for lat, lon in ((43.7, -79.4), (19.4, -99.1), (18.4, -66.1), (0, 0), (40.7, -74.0)):
            self.assertFalse(in_supported_us(lat, lon))
            with self.assertRaises(ResolutionError):
                resolve_endpoint({"latitude": lat, "longitude": lon})

    @override_settings(GEOCODING_API_KEY="private-test-key")
    def test_header_cache_and_ambiguity(self):
        response = Mock()
        response.json.return_value = [{"lat": "40.7", "lon": "-73.98", "address": {"country_code": "us"}, "display_name": "NYC"}]
        session = Mock()
        session.get.return_value = response
        metrics = {}
        self.assertEqual(resolve_endpoint("New York", metrics=metrics, session=session)["label"], "NYC")
        self.assertEqual(metrics["geocoding_calls"], 1)
        resolve_endpoint(" New   York ", metrics=metrics, session=session)
        self.assertEqual(metrics["geocoding_calls"], 1)
        args, kwargs = session.get.call_args
        self.assertNotIn("private-test-key", args[0])
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer private-test-key")
        response.json.return_value.append({"lat": "42.0", "lon": "-73.0", "address": {"country_code": "us"}, "display_name": "Other"})
        with self.assertRaisesRegex(ResolutionError, "Ambiguous"):
            resolve_endpoint("Springfield", session=session)

    def test_missing_boundary_fails_closed(self):
        us_boundary.cache_clear()
        with patch("routing.services.geography.settings.BASE_DIR") as base:
            base.__truediv__.return_value.__truediv__.return_value.read_text.side_effect = OSError("missing")
            with self.assertRaises(BoundaryUnavailable):
                in_supported_us(40.7, -73.98)
        us_boundary.cache_clear()

    @override_settings(GEOCODING_API_KEY="private-test-key")
    def test_malformed_provider_response_fails(self):
        response = Mock()
        response.json.return_value = {"unexpected": "shape"}
        session = Mock()
        session.get.return_value = response
        with self.assertRaises(GeocoderUnavailable):
            resolve_endpoint("Malformed result", session=session)

    @override_settings(GEOCODING_API_KEY="private-test-key")
    def test_malformed_nested_country_types_fail_as_provider_errors(self):
        response = Mock()
        session = Mock()
        session.get.return_value = response
        for value in (None, 123, ["us"]):
            response.json.return_value = [{"lat": "40.7", "lon": "-73.98", "address": {"country_code": value}}]
            with self.subTest(country_code=value):
                with self.assertRaises(GeocoderUnavailable):
                    resolve_endpoint("Malformed country", session=session)

    @override_settings(GEOCODING_API_KEY="private-test-key")
    def test_surrogates_enormous_numbers_and_deep_nesting_are_typed(self):
        with self.assertRaises(ResolutionError):
            resolve_endpoint("Chicago \ud800", session=Mock())
        response = Mock()
        session = Mock()
        session.get.return_value = response
        response.json.return_value = [{"lat": 10**400, "lon": -73.98, "address": {"country_code": "us"}}]
        with self.assertRaises(GeocoderUnavailable):
            resolve_endpoint("Huge lat", session=session)
        response.json.side_effect = RecursionError
        with self.assertRaises(GeocoderUnavailable):
            resolve_endpoint("Deep nesting", session=session)
