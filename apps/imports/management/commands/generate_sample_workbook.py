from django.core.management.base import BaseCommand

from apps.imports.services import sample_workbook


class Command(BaseCommand):
    help = "Write the documented sample attendance workbook (synthetic data) to a file."

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?", default="docs/attendance_import_sample.xlsx")

    def handle(self, *args, **opts):
        with open(opts["path"], "wb") as fh:
            fh.write(sample_workbook().read())
        self.stdout.write(self.style.SUCCESS(f"Sample workbook written to {opts['path']}"))
