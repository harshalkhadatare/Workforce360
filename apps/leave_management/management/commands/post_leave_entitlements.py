from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.employees.models import Employee
from apps.leave_management.services import compute_entitlements, post_entitlements


class Command(BaseCommand):
    help = "Post leave entitlement credits that are due (idempotent). Run monthly (e.g. 1st of each month) and on confirmations."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        year = opts["year"] or timezone.localdate().year
        total = 0
        for emp in Employee.objects.filter(is_active=True):
            if opts["dry_run"]:
                for item in compute_entitlements(emp, year):
                    self.stdout.write(f"{emp.employee_id} {item['basis']} {item['period_key']} {item['quantity']}: {item['explanation']}")
            else:
                total += len(post_entitlements(emp, year, None))
        if not opts["dry_run"]:
            self.stdout.write(self.style.SUCCESS(f"{total} new entitlement credit(s) posted for {year}."))
