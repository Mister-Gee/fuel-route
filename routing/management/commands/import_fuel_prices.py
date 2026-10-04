from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from routing.services.importer import ImportValidationError, import_prices


class Command(BaseCommand):
    help = "Import original CSV prices joined only to accepted coordinate evidence"

    def add_arguments(self, parser):
        parser.add_argument("--csv", default=str(settings.BASE_DIR / "fuel-prices-for-be-assessment.csv"))
        parser.add_argument("--sidecar", default=str(settings.BASE_DIR / "data" / "station_coordinates.json"))
        parser.add_argument("--price-policy", choices=("mean", "min", "exclude_ambiguous"), default=settings.FUEL_PRICE_POLICY)

    def handle(self, *args, **options):
        try:
            revision, changed = import_prices(options["csv"], options["sidecar"], policy=options["price_policy"])
        except (OSError, ValueError, KeyError, ImportValidationError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"{'Imported' if changed else 'Unchanged'}: {revision.imported_count} stations; {revision.unresolved_count} unresolved excluded; revision {revision.digest}")
