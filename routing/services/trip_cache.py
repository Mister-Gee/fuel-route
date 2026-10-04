"""Revision-sensitive completed-trip keys."""
import hashlib
import json

from django.conf import settings
from django.core.cache import cache


def key(start, finish, revision):
    value = [start["latitude"], start["longitude"], finish["latitude"], finish["longitude"], revision.digest, revision.price_policy, settings.OSRM_BASE_URL, settings.OSRM_MIN_REQUEST_INTERVAL_SECONDS, settings.OSRM_TIMEOUT_SECONDS, settings.STATION_CORRIDOR_MILES, "snap:200", "vehicle:50:10"]
    return "trip:" + hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


def get(key_value):
    return cache.get(key_value)


def put(key_value, value):
    cache.set(key_value, value, timeout=86400)
