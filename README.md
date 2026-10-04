# Fuel Route API

Django API for planning a US driving route, selecting cost-effective fuel stops and calculating fuel purchases. It returns route geometry, a saved interactive map, station prices and a purchase breakdown.

The vehicle starts with a full 50-gallon tank, travels 10 miles per gallon and has a 500-mile range. Reported cost covers fuel purchased during the journey; it excludes fuel already in the starting tank.

## Quick start

Requires Python 3.12 or later. Run from the project directory in PowerShell:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.venv\Scripts\python.exe manage.py migrate
.venv\Scripts\python.exe manage.py import_fuel_prices
.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Coordinate inputs work without an API key. For place names, set `GEOCODING_API_KEY` in `.env` to a Maps.co geocoding key. For a nonlocal deployment, configure `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` and `DJANGO_DEBUG=false`. The development server and SQLite configuration are intended for local assessment use.

The importer loads 4,304 stations. Repeating it with identical data and price policy leaves the active revision unchanged. The database and `.env` are excluded from Git.

## API

### Health

`GET http://127.0.0.1:8000/api/v1/health/`

Returns readiness, the active dataset revision and imported/unresolved station counts.

### Plan a route

`POST http://127.0.0.1:8000/api/v1/routes/optimize-fuel/`

Set `Content-Type: application/json`. For New York to Chicago:

```json
{
  "start": {"latitude": 40.7128, "longitude": -74.0060},
  "finish": {"latitude": 41.8781, "longitude": -87.6298}
}
```

With geocoding configured, both endpoints can also be place strings:

```json
{
  "start": "New York, NY, USA",
  "finish": "Chicago, IL, USA"
}
```

Ambiguous names require a more specific address. Endpoints must be within the supported 50 states or DC.

| Response field | Contents |
| --- | --- |
| `trip_id`, `map_url` | Saved trip identifier and interactive map URL |
| `start`, `finish` | Resolved endpoint coordinates |
| `route` | GeoJSON geometry, total distance, duration and driving distance between stops |
| `fuel_stops` | Station identity, coordinates, provenance, price, purchased gallons and cost |
| `summary` | Initial, purchased, consumed and remaining gallons; `purchase_cost` |
| `vehicle` | Capacity, range, fuel economy and starting-fuel cost convention |
| `dataset`, `optimality` | Dataset coverage, price policy and optimization scope |
| `metrics` | Routing/geocoding call counts, cache status and elapsed milliseconds |

Open the returned `map_url` in a browser to see the route and fuel stops. A short trip can correctly return no purchases and a zero cost because the initial tank covers it. Displayed stop costs sum to the displayed purchase total.

### Postman

Import `postman/spotter-fuel-route.postman_collection.json` and `postman/spotter-local.postman_environment.json`, then select **Spotter Local**. Run **Health**, **Short coordinate route**, **Long coordinate route** and **Saved map**. Successful route requests save `trip_id` in the selected environment. The map endpoint returns HTML; use a browser to see the interactive map.

The alternate `spotter-fuel-route-vscode.postman_collection.json` contains no scripts and requires copying the returned trip ID into the map request. `postman/requests.http` provides the same requests for a VS Code HTTP client. Both JSON collections include explicit empty response arrays for compatibility with the Postman VS Code importer.

### Errors

Errors use a JSON envelope:

```json
{"error":{"code":"route_unavailable","message":"Routing provider is unavailable"}}
```

| Status | Meaning |
| --- | --- |
| 400 | Invalid request body or coordinate shape |
| 404 | Unknown trip or trip from an inactive dataset revision |
| 422 | Unsupported/ambiguous location or no feasible route through accepted stations |
| 502 | Routing or geocoding provider failure or invalid provider response |
| 503 | Required dataset or geocoder configuration unavailable |

## Approach

Station coordinates and prices are prepared offline. At request time, the API obtains a baseline route from OSRM, uses a spatial index to find nearby station candidates and calculates fuel purchases with Decimal arithmetic. For an ordered route, it buys enough to reach the next no-more-expensive reachable station; otherwise it fills the tank or buys only what is needed to finish.

Selected stations become routing waypoints. Purchases are recalculated using the final driving legs, with one bounded repair if a stop sequence is infeasible. A request makes one baseline routing call, normally two with stops and at most three. Cached trips make zero routing calls. Place-name geocoding calls are counted separately. A shared SQLite limiter spaces public routing requests at least one second apart.

## Data and assumptions

- `fuel-prices-for-be-assessment.csv` is the original price source. Its SHA-256 is `c704371f141ded9c54df6c32d488a0ba2ceb589f88c936c967daa5330e0cd241`.
- The CSV contains no coordinates. `data/station_coordinates.json` supplies a prepared coordinate ledger joined by OPIS Truckstop ID, with source references, matching methods and accepted/unresolved partitions. The importer validates identities and raw prices against the CSV before preparing station records.
- Coverage is **4,304 of 6,626 US stations (64.96%)**. The other 2,322 are excluded because their coordinates or identities remain unresolved. Accepted records have different evidence grades; they are not all independently OPIS verified.
- The default price is the mean of distinct CSV quotes per station. Fuel grade and quote date are unspecified, so this is an estimate rather than a confirmed purchasable quote. `FUEL_PRICE_POLICY` also supports `min` and `exclude_ambiguous`; reimport after changing it.
- Optimization is approximate across accepted candidates near the baseline route. Final purchases are optimized for the validated waypoint sequence. This does not establish a global minimum across all CSV stations or alternative routes; omitted stations may be cheaper.
- No fuel reserve is modeled. OSRM uses a car profile and does not guarantee truck restrictions. Generalized country boundaries can misclassify points near shorelines or borders.

The map uses [Leaflet](https://leafletjs.com/) with [OpenStreetMap tiles](https://www.openstreetmap.org/copyright). Routing uses the configured [OSRM service](https://routing.openstreetmap.de/about.html). The bundled boundary comes from the [US Census Bureau's 2024 cartographic state boundaries](https://www.census.gov/geographies/mapping-files/time-series/geo/carto-boundary-file.html). Public service availability affects live request latency and success.

## Verification

```powershell
.venv\Scripts\python.exe manage.py check
.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.venv\Scripts\python.exe manage.py test routing.tests
```

The suite covers imports, country validation, geocoding ambiguity, fuel optimization, routing geometry, range feasibility, bounded repairs, caching, concurrent provider pacing, API errors and map rendering.

Optional local performance measurement:

```powershell
.venv\Scripts\python.exe scripts/benchmark_trip.py
```

Optional live API smoke check (contacts the configured routing service):

```powershell
$env:SPOTTER_LIVE_SMOKE='1'
.venv\Scripts\python.exe scripts/live_smoke.py
```

`scripts/build_us_boundary.py` rebuilds the boundary from the Census download; the bundled file is sufficient for normal setup. Configuration is listed in `.env.example`. Django is pinned to 6.1.1 in `requirements.txt`.
