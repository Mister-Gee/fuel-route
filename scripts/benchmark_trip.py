"""Measure local candidate discovery and fuel optimization against imported stations."""
import json
import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from routing.models import ImportRevision, Station
from routing.services.candidates import candidates
from routing.services.fuel_optimizer import FuelInfeasible, optimize


def main():
    revision = ImportRevision.objects.filter(is_active=True).first()
    if revision is None:
        raise SystemExit("Import stations first: python manage.py import_fuel_prices")
    anchors = []
    for longitude in (-101, -96, -91, -86, -81, -76):
        stations = Station.objects.filter(revision=revision, longitude__gte=longitude - 1, longitude__lte=longitude + 1, latitude__gte=39, latitude__lte=42)
        selected = min(stations, key=lambda row: abs(row.longitude - longitude) + abs(row.latitude - 40.5), default=None)
        if selected is None:
            raise SystemExit(f"No representative station near longitude {longitude}")
        anchors.append((selected.latitude, selected.longitude))
    points = []
    for start, end in zip(anchors, anchors[1:]):
        points.extend((start[0] + (end[0] - start[0]) * step / 30, start[1] + (end[1] - start[1]) * step / 30) for step in range(30))
    points.append(anchors[-1])
    segment_miles = [math.hypot((b[0] - a[0]) * 69, (b[1] - a[1]) * 53) * 1.15 for a, b in zip(points, points[1:])]
    route = {"points": points, "segment_miles": segment_miles, "distance_miles": sum(segment_miles), "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in points]}}
    began = time.perf_counter()
    found = candidates(route, revision)
    candidate_ms = (time.perf_counter() - began) * 1000
    began = time.perf_counter()
    by_mile = {}
    for item in found:
        key = round(item.route_mile, 7)
        if key not in by_mile or (item.price, item.opis_id) < (by_mile[key].price, by_mile[key].opis_id):
            by_mile[key] = item
    try:
        plan = optimize(route["distance_miles"], [(item.route_mile, item.price, item.opis_id) for item in by_mile.values()])
        status = "feasible"
        purchase_count = sum(item.gallons > 0 for item in plan["stops"])
    except FuelInfeasible as exc:
        status = "infeasible: " + str(exc)
        purchase_count = 0
    optimize_ms = (time.perf_counter() - began) * 1000
    print(json.dumps({"station_count": Station.objects.filter(revision=revision).count(), "route_miles": round(route["distance_miles"], 2), "geometry_points": len(points), "geometry_bytes": len(json.dumps(route["geometry"]).encode()), "candidate_count": len(found), "candidate_ms": round(candidate_ms, 3), "optimization_ms": round(optimize_ms, 3), "local_total_ms": round(candidate_ms + optimize_ms, 3), "purchase_count": purchase_count, "status": status, "provider_calls": 0, "geocoding_calls": 0}, indent=2))


if __name__ == "__main__":
    main()
