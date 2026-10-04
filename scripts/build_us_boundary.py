"""Rebuild the bundled 50-state/DC polygon from the US Census Bureau.

Source: 2024 Cartographic Boundary Files, cb_2024_us_state_500k.zip
https://www.census.gov/geographies/mapping-files/time-series/geo/carto-boundary-file.html
No geometry simplification is applied. Census boundaries are generalized at 1:500k.
"""
import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path

import shapefile

URL = "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_state_500k.zip"
EXCLUDED = {"60", "66", "69", "72", "78"}  # territories
OUT = Path(__file__).resolve().parents[1] / "data" / "us_states_50_dc.geojson"


def main():
    payload = urllib.request.urlopen(URL, timeout=30).read()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        shp = archive.read(next(n for n in archive.namelist() if n.endswith(".shp")))
        shx = archive.read(next(n for n in archive.namelist() if n.endswith(".shx")))
        dbf = archive.read(next(n for n in archive.namelist() if n.endswith(".dbf")))
    reader = shapefile.Reader(shp=io.BytesIO(shp), shx=io.BytesIO(shx), dbf=io.BytesIO(dbf))
    fields = [field[0] for field in reader.fields[1:]]
    features = []
    for row in reader.iterShapeRecords():
        record = dict(zip(fields, row.record))
        if record["STATEFP"] in EXCLUDED:
            continue
        features.append({"type": "Feature", "properties": {"statefp": record["STATEFP"], "name": record["NAME"]}, "geometry": row.shape.__geo_interface__})
    if len(features) != 51:
        raise RuntimeError(f"Expected 50 states plus DC, got {len(features)}")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"type": "FeatureCollection", "source": URL, "source_sha256": hashlib.sha256(payload).hexdigest(), "features": features}, separators=(",", ":")), encoding="utf-8")
    print(f"{OUT}: 51 features, source SHA-256 {hashlib.sha256(payload).hexdigest()}")


if __name__ == "__main__":
    main()
