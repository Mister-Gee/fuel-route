"""Durable route responses and cross-process SQLite request spacing."""
import hashlib
import json
import time

from django.db import transaction

from routing.models import ProviderPace, ProviderRouteCache


def route_key(base_url, coordinates, snap_radius):
    canonical = [base_url, [[round(float(lon), 6), round(float(lat), 6)] for lat, lon in coordinates], snap_radius]
    return hashlib.sha256(json.dumps(canonical, separators=(",", ":")).encode()).hexdigest()


def get_route(key):
    for attempt in range(40):
        try:
            row = ProviderRouteCache.objects.filter(pk=key).first()
            return row.payload if row else None
        except Exception as exc:
            if "locked" not in str(exc).lower() or attempt == 39:
                raise
            time.sleep(0.05)


def put_route(key, payload):
    for attempt in range(40):
        try:
            ProviderRouteCache.objects.update_or_create(key=key, defaults={"payload": payload})
            return
        except Exception as exc:
            if "locked" not in str(exc).lower() or attempt == 39:
                raise
            time.sleep(0.05)


def pace(provider, interval):
    """Serialize reservations in SQLite, sleeping outside the write transaction."""
    for attempt in range(40):
        try:
            with transaction.atomic():
                row, _ = ProviderPace.objects.get_or_create(provider=provider)
                now = time.time()
                start = max(now, row.next_at)
                row.next_at = start + max(0.0, interval)
                row.save(update_fields=["next_at"])
            break
        except Exception as exc:
            # Retry concurrent SQLite writes without bypassing the rate limiter.
            if "locked" not in str(exc).lower():
                raise
            if attempt == 39:
                raise
            time.sleep(0.05)
    wait = start - time.time()
    if wait > 0:
        time.sleep(wait)
