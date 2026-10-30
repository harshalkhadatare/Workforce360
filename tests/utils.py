"""Shared test helpers. All data is synthetic."""
import datetime
import io

from django.core.management import call_command
from django.utils import timezone

from apps.accounts.models import Role
from apps.attendance.models import AttendancePunch
from apps.employees import services as emp_services
from apps.employees.models import Department, Employee
from apps.imports.models import AttendanceImportBatch
from apps.imports.parsing import business_key
from apps.leave_management.models import LeaveType
from apps.policies.models import PolicyVersion

PASSWORD = "Synthetic-Pass-2026!"


def setup_reference():
    call_command("seed_reference_data", "--with-sample-departments", stdout=io.StringIO())


def make_employee(eid, *, manager=None, role=Role.EMPLOYEE, doj=datetime.date(2024, 1, 1), confirmed=datetime.date(2024, 7, 1),
                  login=True, dept_code="HO-OPS"):
    emp = Employee(employee_id=eid, first_name=eid.title(), last_name="Test", date_of_joining=doj, reporting_manager=manager,
                   official_email=f"{eid.lower()}@example.invalid", department=Department.objects.filter(code=dept_code).first())
    emp_services.save_employee(emp, None, is_new=True)
    if confirmed:
        emp_services.confirm_employee(emp, confirmed, None, "test")
    if login:
        user = emp_services.create_login(emp, None, role=role, send_setup_email=False)
        user.set_password(PASSWORD)
        user.save()
    emp.refresh_from_db()
    return emp


def punch(emp, d, hhmm, ptype="IN"):
    h, m = [int(x) for x in hhmm.split(":")[:2]]
    s = int(hhmm.split(":")[2]) if hhmm.count(":") == 2 else 0
    naive = datetime.datetime.combine(d, datetime.time(h, m, s))
    return AttendancePunch.objects.create(employee=emp, punch_time=timezone.make_aware(naive), punch_date=d, punch_type=ptype,
                                          source_key=business_key(emp.employee_id, naive, ptype), device="TEST")


def day(emp, d, tin=None, tout=None):
    if tin:
        punch(emp, d, tin, "IN")
    if tout:
        punch(emp, d, tout, "OUT")


def cover(start, end):
    """Mark a date range as covered by a completed import (so missing days are 'absent', not 'not imported')."""
    return AttendanceImportBatch.objects.create(original_name="test-coverage", sha256="0" * 64, size_bytes=0, status="COMPLETED", file="",
                                                date_from=start, date_to=end, format_mode="DAILY")


def set_policy(kind, **params):
    """Create a newer active version with overridden parameters (same effective date as the current one)."""
    base = PolicyVersion.objects.filter(kind=kind, status="ACTIVE").order_by("-effective_from", "-id").first()
    return PolicyVersion.objects.create(kind=kind, name="test override", effective_from=base.effective_from, status="ACTIVE",
                                        parameters={**base.parameters, **params}, change_reason="test")


def annual_leave():
    return LeaveType.objects.get(code="AL")
