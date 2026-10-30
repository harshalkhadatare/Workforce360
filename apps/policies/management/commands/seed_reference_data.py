"""Idempotent reference data needed for the portal to operate.

Creates (only if missing): the default 09:30–18:30 schedule, initial leave and
attendance policy versions from the documented policy summary, default leave
types, permission groups, recipient groups, and sample departments. Contains NO
employee or personal data. Safe to run repeatedly and in production.
"""
import datetime

from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.employees.models import Department, Designation
from apps.leave_management.models import LeaveType
from apps.notifications.models import NotificationSettings, RecipientGroup
from apps.policies.defaults import ATTENDANCE_SOURCE, DEFAULT_APPROVAL_CHAINS, LEAVE_SOURCE, default_parameters
from apps.policies.models import ApprovalChainConfig, PolicyVersion, WorkSchedule


class Command(BaseCommand):
    help = "Create reference data (schedules, policies, leave types, groups). Idempotent."

    def add_arguments(self, parser):
        parser.add_argument("--with-sample-departments", action="store_true", help="Also create sample departments/designations")

    @transaction.atomic
    def handle(self, *args, **opts):
        out = []
        if not WorkSchedule.objects.exists():
            WorkSchedule.objects.create(code="GEN", name="General shift (HO)", start_time=datetime.time(9, 30), end_time=datetime.time(18, 30),
                                        weekly_off_days=[6], effective_from=datetime.date(2026, 1, 1), is_default=True,
                                        change_reason="Initial setup per Attendance Policy 002 R2 (weekly off = Sunday, AWAITING HR CONFIRMATION)")
            out.append("work schedule GEN 09:30–18:30 (Sunday off — confirm with HR)")
        if not PolicyVersion.objects.filter(kind="LEAVE").exists():
            PolicyVersion.objects.create(kind="LEAVE", name="Leave Policy for HO", source_document=LEAVE_SOURCE,
                                         effective_from=datetime.date(2026, 1, 1), status="ACTIVE", parameters=default_parameters("LEAVE"),
                                         change_reason="Initial configuration from the policy summary in the project brief. "
                                                       "Effective from the start of leave year 2026 for set-up; confirm effective date with HR "
                                                       "(document dated 01-05-2026).")
            out.append("leave policy version")
        if not PolicyVersion.objects.filter(kind="ATTENDANCE").exists():
            PolicyVersion.objects.create(kind="ATTENDANCE", name="Attendance Policy (amended)", source_document=ATTENDANCE_SOURCE,
                                         effective_from=datetime.date(2026, 7, 1), status="ACTIVE", parameters=default_parameters("ATTENDANCE"),
                                         change_reason="Initial configuration; amended policy applies from 01-07-2026.")
            out.append("attendance policy version (from 01 Jul 2026)")
        lt_defaults = [
            ("AL", "Annual Leave", dict(tracks_balance=True, is_paid=True, is_policy_entitlement=True, display_order=1,
                                        description="Entitlement per Leave Policy 001 R2 (22 days confirmed; probation credits).")),
            ("LWP", "Leave Without Pay", dict(tracks_balance=False, is_paid=False, display_order=9,
                                              description="No balance. Approved days are flagged for payroll review.")),
        ]
        for code, name, extra in lt_defaults:
            _, created = LeaveType.objects.get_or_create(code=code, defaults={"name": name, **extra})
            if created:
                out.append(f"leave type {code}")
        for g in ("HR", "Finance", "Management"):
            Group.objects.get_or_create(name=g)
        for code, name in (("HR", "HR team"), ("MANAGEMENT", "Management")):
            RecipientGroup.objects.get_or_create(code=code, defaults={"name": name})
        ns = NotificationSettings.load()
        if not ns.short_leave_cc_groups.exists():
            ns.short_leave_cc_groups.set(RecipientGroup.objects.filter(code__in=["HR", "MANAGEMENT"]))
            ns.import_notify_groups.set(RecipientGroup.objects.filter(code="HR"))
            ns.query_notify_groups.set(RecipientGroup.objects.filter(code="HR"))
        for k, steps in DEFAULT_APPROVAL_CHAINS.items():
            ApprovalChainConfig.objects.get_or_create(request_type=k, defaults={"steps": steps})
        if opts["with_sample_departments"]:
            for code, name in (("HO-ADM", "Administration"), ("HO-FIN", "Finance & Accounts"), ("HO-HR", "Human Resources"),
                               ("HO-OPS", "Operations"), ("HO-SAL", "Sales & Service"), ("HO-IT", "Information Technology")):
                Department.objects.get_or_create(code=code, defaults={"name": name})
            for n in ("Executive", "Senior Executive", "Assistant Manager", "Manager", "Senior Manager", "Engineer", "Officer"):
                Designation.objects.get_or_create(name=n)
            out.append("sample departments & designations")
        self.stdout.write(self.style.SUCCESS("Reference data ready." + (" Created: " + ", ".join(out) if out else " Nothing new.")))
