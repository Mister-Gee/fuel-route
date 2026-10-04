from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('routing', '0002_importrevision_is_active_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProviderPace',
            fields=[
                ('provider', models.CharField(max_length=32, primary_key=True, serialize=False)),
                ('next_at', models.FloatField(default=0)),
            ],
        ),
        migrations.CreateModel(
            name='ProviderRouteCache',
            fields=[
                ('key', models.CharField(max_length=64, primary_key=True, serialize=False)),
                ('payload', models.JSONField()),
            ],
        ),
    ]
