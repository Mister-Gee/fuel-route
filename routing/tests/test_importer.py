import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.cache import cache
from django.test import TestCase

from routing.models import ImportRevision, Station, Trip
from routing.services.importer import ImportValidationError, import_prices


class ImporterTests(TestCase):
    def setUp(self):
        cache.clear()
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.csv = Path(self.temp.name) / "prices.csv"
        self.sidecar = Path(self.temp.name) / "sidecar.json"
        with self.csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["OPIS Truckstop ID", "Truckstop Name", "Address", "City", "State", "Rack ID", "Retail Price"])
            writer.writerows([
                ["1", "Station A", "1 Main St", "New York", "NY", "1", "3.1"],
                ["1", "Station A", "1 Main St", "New York", "NY", "1", "3.3"],
                ["2", "Station B", "2 Main St", "New York", "NY", "1", "4.0"],
                ["3", "Station C", "3 Main St", "New York", "NY", "1", "5.0"],
            ])
        self.hash = hashlib.sha256(self.csv.read_bytes()).hexdigest()
        def accepted(id, name, address, prices):
            return dict(id=id, name=name, address=address, city="New York", state="NY", prices=prices, lat=40.7, lon=-73.98, source="test", sourceDataset="fixture", sourceRecordRef=id, coordinateEvidence="direct_official", matchMethod="test", verificationStatus="test")
        self.data = {"summary": {"csvStations": 3, "accepted": 2, "unresolved": 1}, "accepted": [accepted("1", "Station A", "1 Main St", ["3.1", "3.3"]), accepted("2", "Station B", "2 Main St", ["4.0"])], "unresolved": [{"id": "3"}]}
        self.save()

    def save(self):
        self.sidecar.write_text(json.dumps(self.data), encoding="utf-8")

    def run_import(self, policy="mean"):
        return import_prices(self.csv, self.sidecar, policy=policy, expected_csv_sha256=self.hash)

    def test_import_repeat_and_revision(self):
        revision, changed = self.run_import()
        self.assertTrue(changed)
        self.assertEqual(Station.objects.count(), 2)
        station = Station.objects.get(opis_id="1")
        self.assertEqual(str(station.price), "3.20000000")
        self.assertEqual(station.raw_quotes, ["3.1", "3.3"])
        self.assertEqual(station.provenance["sourceDataset"], "fixture")
        second, changed = self.run_import()
        self.assertFalse(changed)
        self.assertEqual(second.digest, revision.digest)
        self.assertEqual(ImportRevision.objects.count(), 1)
        self.data["accepted"][0]["lat"] = 40.71
        self.save()
        third, changed = self.run_import()
        self.assertTrue(changed)
        self.assertNotEqual(third.digest, revision.digest)
        self.assertEqual(Station.objects.get(opis_id="1").latitude, 40.71)
        self.run_import("exclude_ambiguous")
        self.assertEqual(set(Station.objects.values_list("opis_id", flat=True)), {"2"})

    def test_bad_data_rolls_back(self):
        self.run_import()
        old = Station.objects.get(opis_id="1").price
        for mutate in (
            lambda: self.data["accepted"].append(dict(self.data["accepted"][0])),
            lambda: self.data["accepted"][0].update(name="Wrong"),
            lambda: self.data["accepted"][0].update(lat=51.0),
            lambda: self.data["accepted"][0].pop("source"),
            lambda: self.data["accepted"][0].update(prices=["9.9"]),
        ):
            original = json.loads(json.dumps(self.data))
            mutate()
            self.save()
            with self.assertRaises(ImportValidationError):
                self.run_import()
            self.assertEqual(Station.objects.get(opis_id="1").price, old)
            self.data = original
        with self.assertRaises(ImportValidationError):
            import_prices(self.csv, self.sidecar, expected_csv_sha256="0" * 64)

    def test_nonpositive_csv_price_is_rejected(self):
        self.csv.write_text(self.csv.read_text(encoding="utf-8").replace(",3.1", ",-1.0", 1), encoding="utf-8")
        self.hash = hashlib.sha256(self.csv.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ImportValidationError, "Nonpositive"):
            self.run_import()

    def test_historical_policy_restoration_and_cache_trip_invalidation(self):
        mean, changed = self.run_import("mean")
        self.assertTrue(changed)
        first_trip = Trip.objects.create(revision=mean, response={"cached": "mean"})
        cache.set("trip:fixture", "mean")

        repeated, changed = self.run_import("mean")
        self.assertFalse(changed)
        self.assertEqual(repeated.pk, mean.pk)
        self.assertTrue(Trip.objects.filter(pk=first_trip.pk).exists())
        self.assertEqual(cache.get("trip:fixture"), "mean")

        minimum, changed = self.run_import("min")
        self.assertTrue(changed)
        self.assertNotEqual(minimum.pk, mean.pk)
        self.assertFalse(Trip.objects.filter(pk=first_trip.pk).exists())
        self.assertIsNone(cache.get("trip:fixture"))
        self.assertEqual(Station.objects.get(opis_id="1").price, Decimal("3.1"))
        self.assertEqual(Station.objects.get(opis_id="1").revision_id, minimum.pk)
        self.assertEqual(ImportRevision.objects.get(is_active=True).pk, minimum.pk)

        second_trip = Trip.objects.create(revision=minimum, response={"cached": "min"})
        cache.set("trip:fixture", "min")
        restored, changed = self.run_import("mean")
        self.assertTrue(changed)
        self.assertEqual(restored.pk, mean.pk)
        self.assertEqual(ImportRevision.objects.count(), 2)
        self.assertEqual(ImportRevision.objects.get(is_active=True).pk, mean.pk)
        self.assertEqual(Station.objects.get(opis_id="1").revision_id, mean.pk)
        self.assertEqual(Station.objects.get(opis_id="1").price, Decimal("3.2"))
        self.assertFalse(Trip.objects.filter(pk=second_trip.pk).exists())
        self.assertIsNone(cache.get("trip:fixture"))

        final_trip = Trip.objects.create(revision=mean, response={"cached": "restored"})
        cache.set("trip:fixture", "restored")
        unchanged, changed = self.run_import("mean")
        self.assertFalse(changed)
        self.assertEqual(unchanged.pk, mean.pk)
        self.assertEqual(ImportRevision.objects.count(), 2)
        self.assertTrue(Trip.objects.filter(pk=final_trip.pk).exists())
        self.assertEqual(cache.get("trip:fixture"), "restored")
