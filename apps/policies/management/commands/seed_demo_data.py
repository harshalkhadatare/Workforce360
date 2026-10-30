"""SYNTHETIC demonstration data for local development only.

Refuses to run unless DEBUG is on or --allow-non-debug is given. All names,
IDs and emails are fictitious (example.invalid domain). Demo accounts get a
random password printed once to the console — nothing is hardcoded.
"""
import datetime
import random
import secrets

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role
from apps.attendance.models import AttendancePunch
from apps.attendance.services import recalculate_month
from apps.employees import services as emp_services
from apps.employees.models import Department, Designation, Employee
from apps.imports.parsing import business_key
from apps.leave_management.models import LeaveType
from apps.leave_management.services import manual_adjustment, post_entitlements
from apps.policies.models import Holiday

PEOPLE = [
    ("DEMO-M01", "Kavita", "Rao", "HO-OPS", "Manager", None, Role.MANAGER),
    ("DEMO-E01", "Arjun", "Mehta", "HO-OPS", "Engineer", "DEMO-M01", Role.EMPLOYEE),
    ("DEMO-E02", "Sneha", "Kulkarni", "HO-OPS", "Senior Executive", "DEMO-M01", Role.EMPLOYEE),
    ("DEMO-E03", "Rohit", "Patil", "HO-OPS", "Executive", "DEMO-M01", Role.EMPLOYEE),
    ("DEMO-M02", "Imran", "Shaikh", "HO-FIN", "Manager", None, Role.MANAGER),
    ("DEMO-E04", "Pooja", "Joshi", "HO-FIN", "Officer", "DEMO-M02", Role.EMPLOYEE),
    ("DEMO-H01", "Meera", "Iyer", "HO-HR", "Assistant Manager", None, Role.ADMIN),
]


class Command(BaseCommand):
    help = "Create SYNTHETIC demo employees, accounts, holidays and punches (development only)."

    def add_arguments(self, parser):
        parser.add_argument("--allow-non-debug", action="store_true")
        parser.add_argument("--days", type=int, default=45)

    @transaction.atomic
    def handle(self, *args, **opts):
        if not settings.DEBUG and not opts["allow_non_debug"]:
            raise CommandError("Refusing to create demo data with DEBUG off. Use --allow-non-debug only on a test server.")
        call_command("seed_reference_data", "--with-sample-departments")
        rng = random.Random(2026)
        today = timezone.localdate()
        created_logins = []
        for eid, first, last, dept, desig, mgr, role in PEOPLE:
            if Employee.objects.filter(employee_id=eid).exists():
                continue
            doj = datetime.date(2024, 4, 1) if eid != "DEMO-E03" else today.replace(day=1) - datetime.timedelta(days=150)
            emp = Employee(employee_id=eid, first_name=first, last_name=last, department=Department.objects.get(code=dept),
                           designation=Designation.objects.get(name=desig), date_of_joining=doj,
                           official_email=f"{eid.lower()}@example.invalid", mobile=f"+91 90000 0{rng.randint(1000, 9999)}",
                           reporting_manager=Employee.objects.filter(employee_id=mgr).first(), work_location="Head Office")
            emp_services.save_employee(emp, None, is_new=True)
            if eid != "DEMO-E03":
                emp_services.confirm_employee(emp, doj + datetime.timedelta(days=180), None, "Demo data")
            user = emp_services.create_login(emp, None, role=role, send_setup_email=False)
            pwd = secrets.token_urlsafe(10)
            user.set_password(pwd)
            user.save()
            if role == Role.ADMIN:
                from django.contrib.auth.models import Group

                user.groups.add(Group.objects.get(name="HR"))
            created_logins.append((eid, pwd, role))
        for d, name in ((datetime.date(today.year, 1, 26), "Republic Day"), (datetime.date(today.year, 8, 15), "Independence Day"),
                        (datetime.date(today.year, 10, 2), "Gandhi Jayanti"), (datetime.date(today.year, 5, 1), "Maharashtra Day")):
            Holiday.objects.get_or_create(date=d, location="", defaults={"name": f"{name} (demo)"})
        # Punches for the last N days for every demo employee
        al = LeaveType.objects.get(code="AL")
        start = today - datetime.timedelta(days=opts["days"])
        for emp in Employee.objects.filter(employee_id__startswith="DEMO-"):
            post_entitlements(emp, today.year, None)
            if not emp.leave_ledger.filter(entry_type="OPENING").exists():
                manual_adjustment(emp, al, 3, datetime.date(today.year, 1, 1), "Demo opening balance", None, "OPENING")
            d = max(start, emp.date_of_joining)
            if AttendancePunch.objects.filter(employee=emp, device="DEMO").exists():
                d = today  # already seeded — keep the demo idempotent
            while d < today:
                if d.weekday() != 6 and not Holiday.objects.filter(date=d).exists():
                    roll = rng.random()
                    if roll < 0.05:
                        d += datetime.timedelta(days=1)
                        continue  # absent
                    in_t = datetime.datetime.combine(d, datetime.time(9, 15)) + datetime.timedelta(minutes=rng.choice([0, 0, 0, 0, 0, 0, 0, 3, 5, 8, 12, 20, 48]))
                    out_t = datetime.datetime.combine(d, datetime.time(18, 30)) + datetime.timedelta(minutes=rng.randint(-25, 40))
                    pairs = [(in_t, "IN")] + ([] if roll < 0.08 else [(out_t, "OUT")])
                    for ts, pt in pairs:
                        AttendancePunch.objects.get_or_create(source_key=business_key(emp.employee_id, ts, pt), is_void=False, defaults=dict(
                            employee=emp, punch_time=timezone.make_aware(ts), punch_date=d, punch_type=pt, device="DEMO"))
                d += datetime.timedelta(days=1)
        # Mark the demo range as covered by an "import" so missing days show as absent, not "not imported"
        from apps.imports.models import AttendanceImportBatch

        if not AttendanceImportBatch.objects.filter(original_name="demo-seed").exists():
            AttendanceImportBatch.objects.create(original_name="demo-seed", sha256="0" * 64, size_bytes=0, status="COMPLETED", file="",
                                                 date_from=start, date_to=today - datetime.timedelta(days=1), format_mode="PUNCH_LOG")
        for emp in Employee.objects.filter(employee_id__startswith="DEMO-"):
            m = start.replace(day=1)
            while m <= today:
                recalculate_month(emp, m.year, m.month)
                m = (m + datetime.timedelta(days=32)).replace(day=1)
        self.stdout.write(self.style.WARNING("SYNTHETIC DEMO DATA — do not use in production."))
        for eid, pwd, role in created_logins:
            self.stdout.write(f"  {eid:<10} {role:<9} password: {pwd}")
        if not created_logins:
            self.stdout.write("  Demo employees already existed; passwords unchanged. Use 'Forgot password' or recreate the database.")
