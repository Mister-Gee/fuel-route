"""Opt-in real provider smoke through the Django API; never part of default tests."""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from django.test import Client
from routing.models import ImportRevision


def main():
    if os.environ.get("SPOTTER_LIVE_SMOKE") != "1":
        raise SystemExit("Set SPOTTER_LIVE_SMOKE=1 to opt into real public-provider requests")
    if not ImportRevision.objects.filter(is_active=True).exists():
        raise SystemExit("Import stations before live smoke")
    client = Client(raise_request_exception=False)
    routes = [
        ("short", {"latitude": 40.7128, "longitude": -74.0060}, {"latitude": 40.7357, "longitude": -74.1724}),
        ("long", {"latitude": 40.7128, "longitude": -74.0060}, {"latitude": 41.8781, "longitude": -87.6298}),
    ]
    for label, start, finish in routes:
        began = time.perf_counter()
        response = client.post("/api/v1/routes/optimize-fuel/", data=json.dumps({"start": start, "finish": finish}), content_type="application/json", HTTP_HOST="localhost:8000")
        data = response.json()
        print(json.dumps({"route": label, "status": response.status_code, "elapsed_ms": round((time.perf_counter() - began) * 1000, 3), "provider_metrics": data.get("metrics"), "final_leg_miles": data.get("route", {}).get("leg_miles"), "purchased_gallons": data.get("summary", {}).get("purchased_gallons"), "purchase_cost": data.get("summary", {}).get("purchase_cost"), "map_url": data.get("map_url"), "error": data.get("error")}, indent=2))


if __name__ == "__main__":
    main()
