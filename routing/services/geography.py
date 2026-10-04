"""Offline 50-state and DC membership. The Census 1:500k edge is approximate."""
import json
import math
from functools import lru_cache

from django.conf import settings
from shapely.geometry import Point, shape
from shapely.ops import unary_union
from shapely.prepared import prep


class BoundaryUnavailable(ValueError):
    pass


@lru_cache(maxsize=1)
def us_boundary():
    path = settings.BASE_DIR / "data" / "us_states_50_dc.geojson"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if len(data["features"]) != 51:
            raise ValueError("boundary must contain 51 features")
        return prep(unary_union([shape(feature["geometry"]) for feature in data["features"]]))
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise BoundaryUnavailable("US boundary is unavailable or invalid") from exc


def in_supported_us(latitude, longitude):
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        return False
    try:
        lat, lon = float(latitude), float(longitude)
    except (TypeError, ValueError, OverflowError):
        return False
    if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return False
    return bool(us_boundary().contains(Point(lon, lat)))
