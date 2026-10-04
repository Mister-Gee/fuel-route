"""Public JSON and map boundary for the route planning services."""
import json
import math

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import Http404, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt

from routing.models import ImportRevision, Trip
from routing.services.geocoding import GeocoderUnavailable, ResolutionError
from routing.services.osrm import RouteInvalid, RouteUnavailable
from routing.services.trip import TripInfeasible, plan_trip


def _error(status, code, message):
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def _valid_endpoint(value):
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return False
        return bool(value.strip())
    if not isinstance(value, dict) or set(value) != {"latitude", "longitude"}:
        return False
    lat, lon = value["latitude"], value["longitude"]
    if not all(isinstance(number, (int, float)) and not isinstance(number, bool) for number in (lat, lon)):
        return False
    try:
        lat, lon = float(lat), float(lon)
    except OverflowError:
        return False
    return math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180


@csrf_exempt
def optimize_fuel(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if request.content_type != "application/json":
        return _error(400, "invalid_request", "Content-Type must be application/json")
    try:
        data = json.loads(request.body)
    except RequestDataTooBig:
        return _error(400, "invalid_request", "Request body is too large")
    except (UnicodeDecodeError, ValueError, RecursionError):
        return _error(400, "invalid_request", "Request body must be valid JSON")
    if not isinstance(data, dict) or set(data) != {"start", "finish"} or not all(_valid_endpoint(data[key]) for key in ("start", "finish")):
        return _error(400, "invalid_request", "Provide start and finish as place strings or latitude/longitude objects")
    if not ImportRevision.objects.filter(is_active=True).exists():
        return _error(503, "dataset_unavailable", "Import the station dataset before planning a route")
    if not settings.OSRM_BASE_URL:
        return _error(503, "router_unavailable", "Routing provider is not configured")
    try:
        result = plan_trip(data["start"], data["finish"])
    except GeocoderUnavailable as exc:
        return _error(503, "geocoder_unavailable", str(exc))
    except ResolutionError as exc:
        return _error(422, "endpoint_unresolved", str(exc))
    except TripInfeasible as exc:
        return _error(422, "trip_infeasible", str(exc))
    except RouteUnavailable as exc:
        return _error(502, "route_unavailable", str(exc))
    except RouteInvalid as exc:
        return _error(502, "route_invalid", str(exc))
    result["map_url"] = request.build_absolute_uri(reverse("trip-map", kwargs={"trip_id": result["trip_id"]}))
    return JsonResponse(result)


def trip_map(request, trip_id):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    trip = Trip.objects.filter(pk=trip_id, revision__is_active=True).first()
    if trip is None:
        raise Http404("Trip not found or its dataset revision is stale")
    response = render(request, "routing/map.html", {"trip": trip.response})
    response["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


def health(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    revision = ImportRevision.objects.filter(is_active=True).first()
    return JsonResponse({"ready": revision is not None and revision.imported_count > 0, "dataset": None if revision is None else {"revision": revision.digest, "accepted_count": revision.accepted_count, "imported_count": revision.imported_count, "unresolved_count": revision.unresolved_count, "price_policy": revision.price_policy}})
