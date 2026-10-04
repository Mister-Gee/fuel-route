"""Resolve endpoint inputs using strict offline US membership checks."""
import hashlib
import math

import requests
from django.conf import settings
from django.core.cache import cache

from routing.services.geography import BoundaryUnavailable, in_supported_us


class ResolutionError(ValueError):
    pass


class GeocoderUnavailable(ResolutionError):
    pass


def _point(lat, lon, label):
    if not in_supported_us(lat, lon):
        raise ResolutionError("Endpoint is outside the supported 50 states and DC")
    return {"latitude": float(lat), "longitude": float(lon), "label": label}


def resolve_endpoint(value, *, metrics=None, session=requests):
    """Return a canonical point; metrics counts only actual HTTP calls."""
    if metrics is None:
        metrics = {}
    if isinstance(value, dict):
        if set(value) != {"latitude", "longitude"}:
            raise ResolutionError("Coordinate endpoint needs latitude and longitude only")
        try:
            return _point(value["latitude"], value["longitude"], None)
        except BoundaryUnavailable as exc:
            raise GeocoderUnavailable(str(exc)) from exc
    if not isinstance(value, str) or not value.strip():
        raise ResolutionError("Endpoint must be a place string or coordinate object")
    query = " ".join(value.split())
    try:
        key = "geocode:" + hashlib.sha256((settings.GEOCODING_BASE_URL + ":" + query.casefold()).encode()).hexdigest()
    except UnicodeEncodeError as exc:
        raise ResolutionError("Endpoint must be valid Unicode text") from exc
    cached = cache.get(key)
    if cached is not None:
        return cached
    if not settings.GEOCODING_API_KEY:
        raise GeocoderUnavailable("Geocoding API key is not configured")
    try:
        metrics["geocoding_calls"] = metrics.get("geocoding_calls", 0) + 1
        response = session.get(settings.GEOCODING_BASE_URL + "/search", params={"q": query, "format": "json", "addressdetails": 1, "countrycodes": "us", "limit": 5}, headers={"Authorization": "Bearer " + settings.GEOCODING_API_KEY, "User-Agent": "SpotterAssessment/1.0"}, timeout=settings.GEOCODING_TIMEOUT_SECONDS)
        response.raise_for_status()
        results = response.json()
    except (requests.RequestException, ValueError, RecursionError) as exc:
        raise GeocoderUnavailable("Geocoding provider unavailable or returned invalid data") from exc
    if not isinstance(results, list):
        raise GeocoderUnavailable("Geocoding provider returned invalid data")
    valid = []
    malformed = False
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("address"), dict):
            malformed = True
            continue
        country_code = result["address"].get("country_code")
        if not isinstance(country_code, str):
            malformed = True
            continue
        if country_code.lower() != "us":
            continue
        try:
            float(result["lat"]), float(result["lon"])
            point = _point(result["lat"], result["lon"], result.get("display_name", query))
            valid.append(point)
        except (KeyError, TypeError, ValueError, OverflowError, BoundaryUnavailable):
            malformed = True
            continue
    if malformed:
        raise GeocoderUnavailable("Geocoding provider returned invalid data")
    if not valid:
        raise ResolutionError("No supported US location found")
    first = valid[0]
    if len(valid) > 1:
        # Nearby duplicate points refer to the same place; separated hits need a more specific input.
        if any(math.hypot(item["latitude"] - first["latitude"], item["longitude"] - first["longitude"]) > 0.002 for item in valid[1:]):
            raise ResolutionError("Ambiguous place; provide a more specific address")
    cache.set(key, first, timeout=86400)
    return first
