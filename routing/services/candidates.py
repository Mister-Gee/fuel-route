"""Indexed, approximate corridor discovery; road access is checked by OSRM later."""
import math
from dataclasses import dataclass

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from routing.models import Station

CORRIDOR_MILES = 2.0
_INDEX = None


@dataclass(frozen=True)
class Candidate:
    opis_id: str
    latitude: float
    longitude: float
    route_mile: float
    price: object
    linked_ids: tuple
    warning: str | None = None
    evidence: str = ""


def _station_index(revision):
    global _INDEX
    if _INDEX is not None and _INDEX[0] == revision.digest:
        return _INDEX[1:]
    stations = list(Station.objects.filter(revision=revision).order_by("opis_id"))
    points = [Point(station.longitude, station.latitude) for station in stations]
    tree = STRtree(points)
    _INDEX = (revision.digest, stations, points, tree)
    return stations, points, tree


def candidates(route, revision, *, corridor_miles=CORRIDOR_MILES):
    stations, points, tree = _station_index(revision)
    coords = route["points"]
    segment_miles = route["segment_miles"]
    if len(coords) != len(segment_miles) + 1:
        raise ValueError("Route segment alignment is invalid")
    # Account for longitude scaling when measuring the route corridor.
    lat0 = sum(lat for lat, _ in coords) / len(coords)
    lon_scale = max(0.1, math.cos(math.radians(lat0))) * 69.172
    xy = [(lon * lon_scale, lat * 69.0) for lat, lon in coords]
    line = LineString(xy)
    # Query the index once to avoid scanning every station for every segment.
    degrees = corridor_miles / (69.0 * max(0.1, math.cos(math.radians(lat0))))
    geographic_line = LineString([(lon, lat) for lat, lon in coords])
    possible = tree.query(geographic_line.buffer(degrees))
    segments = [LineString([a, b]) for a, b in zip(xy, xy[1:])]
    segment_tree = STRtree(segments)
    road = [0.0]
    for miles in segment_miles:
        road.append(road[-1] + miles)
    projected = []
    for index in possible:
        station = stations[int(index)]
        point = Point(station.longitude * lon_scale, station.latitude * 69.0)
        if line.distance(point) > corridor_miles:
            continue
        for segment in segment_tree.query(point.buffer(corridor_miles)):
            segment = int(segment)
            if segments[segment].distance(point) > corridor_miles:
                continue
            span = segments[segment].length
            fraction = segments[segment].project(point) / span if span else 0.0
            mile = road[segment] + segment_miles[segment] * fraction
            if mile <= 1e-7 or mile >= road[-1] - 1e-7:
                continue
            projected.append((mile, segment, station))
    projected.sort(key=lambda item: (item[0], item[1], item[2].opis_id))
    grouped = {}
    for mile, segment, station in projected:
        # Proximity alone cannot merge facilities across a divided highway.
        identity = (round(mile, 7), round(station.latitude, 8), round(station.longitude, 8), station.name.casefold().strip(), station.city.casefold().strip(), station.state)
        grouped.setdefault(identity, []).append((mile, station))
    result = []
    for records in grouped.values():
        mile, representative = min(records, key=lambda item: (item[0], item[1].opis_id))
        price = max(station.price for _, station in records)
        ids = tuple(sorted(station.opis_id for _, station in records))
        warning = "Conflicting CSV prices at same facility; conservative maximum used" if len({station.price for _, station in records}) > 1 else None
        result.append(Candidate(representative.opis_id, representative.latitude, representative.longitude, mile, price, ids, warning, representative.coordinate_evidence))
    return sorted(result, key=lambda item: (item.route_mile, item.price, item.opis_id))
