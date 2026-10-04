from django.db import migrations, models


def activate_current_revision(apps, schema_editor):
    Revision = apps.get_model("routing", "ImportRevision")
    Station = apps.get_model("routing", "Station")
    station = Station.objects.order_by("pk").first()
    revision = station.revision if station else Revision.objects.order_by("-created_at", "-pk").first()
    if revision:
        Revision.objects.filter(pk=revision.pk).update(is_active=True)


class Migration(migrations.Migration):

    dependencies = [
        ('routing', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='importrevision',
            name='is_active',
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(activate_current_revision, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='importrevision',
            constraint=models.UniqueConstraint(condition=models.Q(('is_active', True)), fields=('is_active',), name='one_active_import_revision'),
        ),
    ]
