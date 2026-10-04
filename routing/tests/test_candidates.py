from decimal import Decimal

from django.test import TestCase

from routing.models import ImportRevision, Station
from routing.services import candidates


class CandidateTests(TestCase):
    def setUp(self):
        self.revision = ImportRevision.objects.create(digest="a" * 64, is_active=True, csv_sha256="b" * 64, sidecar_sha256="c" * 64, csv_count=5, accepted_count=5, unresolved_count=0, imported_count=5, price_policy="mean")
        candidates._INDEX = None

    def station(self, id, lat, lon, price, *, name="Station", address="Main"):
        return Station.objects.create(opis_id=id, latitude=lat, longitude=lon, price=Decimal(price), name=name, address=address, city="New York", state="NY", raw_quotes=[price], provenance={}, coordinate_evidence="direct", price_policy="mean", revision=self.revision)

    def test_loop_order_and_grouping(self):
        self.station("1", 40.71, -73.98, "3")
        self.station("2", 40.71, -73.98, "4")
        self.station("3", 40.71, -73.98, "2", name="Directional West")
        self.station("4", 41.5, -72.5, "2")
        route = {"points": [(40.7, -73.98), (40.8, -73.98), (40.7, -73.98), (40.7, -73.90)], "segment_miles": [10, 20, 30]}
        found = candidates.candidates(route, self.revision)
        self.assertEqual({item.opis_id for item in found}, {"1", "3"})
        self.assertGreaterEqual(len([item for item in found if item.opis_id == "1"]), 2)
        self.assertEqual(len({item.route_mile for item in found if item.opis_id == "1"}), len([item for item in found if item.opis_id == "1"]))
        grouped = next(item for item in found if item.opis_id == "1")
        self.assertEqual(grouped.linked_ids, ("1", "2"))
        self.assertEqual(grouped.price, Decimal("4"))
        self.assertIsNotNone(grouped.warning)
        self.assertLess(grouped.route_mile, 10)

    def test_revision_refreshes_index(self):
        self.station("old", 40.71, -73.98, "3")
        path = {"points": [(40.7, -73.98), (40.8, -73.98)], "segment_miles": [10]}
        self.assertEqual([item.opis_id for item in candidates.candidates(path, self.revision)], ["old"])
        self.revision.is_active = False
        self.revision.save(update_fields=["is_active"])
        newer = ImportRevision.objects.create(digest="d" * 64, is_active=True, csv_sha256="b" * 64, sidecar_sha256="c" * 64, csv_count=1, accepted_count=1, unresolved_count=0, imported_count=1, price_policy="mean")
        Station.objects.create(opis_id="new", latitude=40.72, longitude=-73.98, price=Decimal("2"), name="Station", address="Main", city="New York", state="NY", raw_quotes=["2"], provenance={}, coordinate_evidence="direct", price_policy="mean", revision=newer)
        self.assertEqual([item.opis_id for item in candidates.candidates(path, newer)], ["new"])
