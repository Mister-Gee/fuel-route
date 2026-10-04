"""Import CSV prices using accepted station coordinates."""
import csv
import hashlib
import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.cache import cache
from django.db import transaction

from routing.models import ImportRevision, Station, Trip
from routing.services.geography import in_supported_us

CSV_SHA256 = "c704371f141ded9c54df6c32d488a0ba2ceb589f88c936c967daa5330e0cd241"
US_STATES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split())
IDENTITY = {"name": "Truckstop Name", "address": "Address", "city": "City", "state": "State"}
PROVENANCE = ("source", "sourceDataset", "sourceRecordRef", "coordinateEvidence", "matchMethod", "verificationStatus")


class ImportValidationError(ValueError):
    pass


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load_csv(path):
    rows = defaultdict(list)
    with open(path, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row["State"] not in US_STATES:
                continue
            station_id = row["OPIS Truckstop ID"].strip()
            if not station_id:
                raise ImportValidationError("Blank OPIS ID")
            try:
                price = Decimal(row["Retail Price"])
            except InvalidOperation as exc:
                raise ImportValidationError(f"Invalid price for ID {station_id}") from exc
            if not price.is_finite() or price <= 0:
                raise ImportValidationError(f"Nonpositive or nonfinite price for ID {station_id}")
            rows[station_id].append(row)
    return rows


def _validated(data, csv_rows, policy):
    if policy not in {"mean", "min", "exclude_ambiguous"}:
        raise ImportValidationError(f"Unknown price policy: {policy}")
    accepted = data.get("accepted")
    unresolved = data.get("unresolved")
    summary = data.get("summary", {})
    if not isinstance(accepted, list) or not isinstance(unresolved, list):
        raise ImportValidationError("Sidecar partition missing")
    if len(accepted) != summary.get("accepted") or len(unresolved) != summary.get("unresolved") or len(csv_rows) != summary.get("csvStations"):
        raise ImportValidationError("Sidecar summary counts disagree")
    ids = [str(row.get("id")) for row in accepted + unresolved]
    if len(ids) != len(set(ids)) or set(ids) != set(csv_rows):
        raise ImportValidationError("Sidecar partition has duplicate or missing IDs")
    prepared = []
    for row in accepted:
        station_id = str(row["id"])
        originals = [item for item in csv_rows[station_id] if all(row.get(key) == item[field].strip() for key, field in IDENTITY.items())]
        if not originals:
            raise ImportValidationError(f"Original identity mismatch for ID {station_id}")
        original = originals[0]
        quotes = sorted(set(item["Retail Price"] for item in csv_rows[station_id]), key=Decimal)
        if row.get("prices") != quotes:
            raise ImportValidationError(f"Raw quote mismatch for ID {station_id}")
        if any(not isinstance(row.get(field), (str, int)) or not str(row[field]).strip() for field in PROVENANCE):
            raise ImportValidationError(f"Missing provenance for ID {station_id}")
        if not in_supported_us(row.get("lat"), row.get("lon")):
            raise ImportValidationError(f"Coordinate outside supported US for ID {station_id}")
        prices = [Decimal(quote) for quote in quotes]
        if policy == "exclude_ambiguous" and len(prices) > 1:
            continue
        price = min(prices) if policy == "min" else sum(prices) / Decimal(len(prices))
        prepared.append((station_id, original, quotes, row, price))
    return prepared, len(accepted), len(unresolved)


def import_prices(csv_path, sidecar_path, *, policy="mean", expected_csv_sha256=CSV_SHA256):
    csv_hash = _sha(csv_path)
    if csv_hash != expected_csv_sha256:
        raise ImportValidationError(f"CSV SHA-256 mismatch: {csv_hash}")
    sidecar_hash = _sha(sidecar_path)
    csv_rows = _load_csv(csv_path)
    data = json.loads(Path(sidecar_path).read_text(encoding="utf-8"))
    prepared, accepted_count, unresolved_count = _validated(data, csv_rows, policy)
    digest = hashlib.sha256(f"{csv_hash}:{sidecar_hash}:{policy}".encode()).hexdigest()
    with transaction.atomic():
        current = ImportRevision.objects.filter(is_active=True).first()
        if current and current.digest == digest:
            return current, False
        revision, _ = ImportRevision.objects.get_or_create(
            digest=digest,
            defaults=dict(csv_sha256=csv_hash, sidecar_sha256=sidecar_hash, csv_count=len(csv_rows), accepted_count=accepted_count, unresolved_count=unresolved_count, imported_count=len(prepared), price_policy=policy),
        )
        Station.objects.all().delete()
        Station.objects.bulk_create([
            Station(opis_id=station_id, name=original["Truckstop Name"], address=original["Address"], city=original["City"], state=original["State"], price=price, raw_quotes=quotes, latitude=row["lat"], longitude=row["lon"], provenance=row, coordinate_evidence=row["coordinateEvidence"], price_policy=policy, revision=revision)
            for station_id, original, quotes, row, price in prepared
        ], batch_size=500)
        Trip.objects.all().delete()
        if current:
            current.is_active = False
            current.save(update_fields=["is_active"])
        revision.is_active = True
        revision.save(update_fields=["is_active"])
        cache.clear()
    return revision, True
