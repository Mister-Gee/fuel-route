import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='ImportRevision',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('digest', models.CharField(max_length=64, unique=True)),
                ('csv_sha256', models.CharField(max_length=64)),
                ('sidecar_sha256', models.CharField(max_length=64)),
                ('csv_count', models.PositiveIntegerField()),
                ('accepted_count', models.PositiveIntegerField()),
                ('unresolved_count', models.PositiveIntegerField()),
                ('imported_count', models.PositiveIntegerField()),
                ('price_policy', models.CharField(max_length=32)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name='Station',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('opis_id', models.CharField(max_length=64, unique=True)),
                ('name', models.TextField()),
                ('address', models.TextField()),
                ('city', models.CharField(max_length=160)),
                ('state', models.CharField(max_length=8)),
                ('price', models.DecimalField(decimal_places=8, max_digits=16)),
                ('raw_quotes', models.JSONField()),
                ('latitude', models.FloatField()),
                ('longitude', models.FloatField()),
                ('provenance', models.JSONField()),
                ('coordinate_evidence', models.CharField(max_length=128)),
                ('price_policy', models.CharField(max_length=32)),
                ('revision', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='routing.importrevision')),
            ],
        ),
        migrations.CreateModel(
            name='Trip',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('response', models.JSONField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('revision', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='routing.importrevision')),
            ],
        ),
    ]
