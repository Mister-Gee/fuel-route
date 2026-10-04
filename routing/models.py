import uuid
from django.db import models


class ImportRevision(models.Model):
    digest = models.CharField(max_length=64, unique=True)
    is_active = models.BooleanField(default=False)
    csv_sha256 = models.CharField(max_length=64)
    sidecar_sha256 = models.CharField(max_length=64)
    csv_count = models.PositiveIntegerField()
    accepted_count = models.PositiveIntegerField()
    unresolved_count = models.PositiveIntegerField()
    imported_count = models.PositiveIntegerField()
    price_policy = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["is_active"], condition=models.Q(is_active=True), name="one_active_import_revision")]


class Station(models.Model):
    opis_id = models.CharField(max_length=64, unique=True)
    name = models.TextField()
    address = models.TextField()
    city = models.CharField(max_length=160)
    state = models.CharField(max_length=8)
    price = models.DecimalField(max_digits=16, decimal_places=8)
    raw_quotes = models.JSONField()
    latitude = models.FloatField()
    longitude = models.FloatField()
    provenance = models.JSONField()
    coordinate_evidence = models.CharField(max_length=128)
    price_policy = models.CharField(max_length=32)
    revision = models.ForeignKey(ImportRevision, on_delete=models.PROTECT)


class Trip(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    revision = models.ForeignKey(ImportRevision, on_delete=models.PROTECT)
    response = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)


class ProviderRouteCache(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    payload = models.JSONField()


class ProviderPace(models.Model):
    provider = models.CharField(max_length=32, primary_key=True)
    next_at = models.FloatField(default=0)
