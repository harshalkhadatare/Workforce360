"""Leave policy engine tests (Leave Policy 001 R2 as configured).

Calendar used: September 2026 — Fri 04, Sat 05, Sun 06 (weekly off), Mon 07.
Default schedule: Sunday is the only weekly off (awaiting HR confirmation).
"""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.approvals import services as wf
from apps.approvals.services import WorkflowError
from apps.attendance.models import AttendanceRecord
from apps.leave_management.models import LeaveEntitlement, LeaveLedgerEntry, LeaveRequest
from apps.leave_management.services import (apply_for_leave, balance, compute_entitlements, count_leave_days,
                                            flag_overdue_emergency_forms, manual_adjustment, post_entitlements, post_leave_debit,
                                            upload_signed_form)
from apps.policies.models import Holiday, PolicyVersion
from apps.policies.services import get_policy

from .utils import annual_leave, make_employee, set_policy, setup_reference

D = datetime.date
TODAY = D(2026, 9, 1)


def pdf(name="form.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 signed", content_type="application/pdf")


class EntitlementTests(TestCase):
    def setUp(self):
        setup_reference()

    def test_confirmed_employee_gets_annual_22(self):
        emp = make_employee("E1", doj=D(2024, 1, 1), confirmed=D(2024, 7, 1), login=False)
        items = compute_entitlements(emp, 2026, as_of=D(2026, 9, 1))
        self.assertEqual([(i["basis"], i["quantity"]) for i in items], [("ANNUAL", Decimal("22"))])

    def test_prorated_from_confirmation_date_months_method(self):
        emp = make_employee("E2", doj=D(2026, 1, 10), confirmed=D(2026, 7, 15), login=False)
        items = [i for i in compute_entitlements(emp, 2026, as_of=D(2026, 9, 1)) if i["basis"] == "PRORATED"]
        self.assertEqual(items[0]["quantity"], Decimal("11.0"))  # 22 × 6/12 (Jul–Dec)

    def test_prorated_days_method_rounds_to_half_day(self):
        set_policy("LEAVE", confirmed_proration_method="DAYS_REMAINING")
        emp = make_employee("E3", doj=D(2026, 1, 10), confirmed=D(2026, 7, 15), login=False)
        q = [i for i in compute_entitlements(emp, 2026, as_of=D(2026, 9, 1)) if i["basis"] == "PRORATED"][0]["quantity"]
        # 22 × 170/365 = 10.246… → rounded to nearest 0.5 = 10.0
        self.assertEqual(q, Decimal("10.0"))

    def test_probation_credit_default_schedule(self):
        # Joined 10 Feb 2026 (service month 1 = Feb). Default: from month 3, 1/month, capped at 4.
        emp = make_employee("E4", doj=D(2026, 2, 10), confirmed=None, login=False)
        items = compute_entitlements(emp, 2026, as_of=D(2026, 9, 1))
        self.assertEqual([i["period_key"] for i in items], ["2026-04", "2026-05", "2026-06", "2026-07"])
        self.assertEqual(sum(i["quantity"] for i in items), Decimal("4"))

    def test_probation_credit_configurable_first_month(self):
        set_policy("LEAVE", probation_first_credit_month=4)
        emp = make_employee("E5", doj=D(2026, 2, 10), confirmed=None, login=False)
        items = compute_entitlements(emp, 2026, as_of=D(2026, 9, 1))
        self.assertEqual([i["period_key"] for i in items], ["2026-05", "2026-06", "2026-07", "2026-08"])

    def test_probation_then_confirmation_carries_forward(self):
        emp = make_employee("E6", doj=D(2026, 2, 10), confirmed=None, login=False)
        from apps.employees.services import confirm_employee

        confirm_employee(emp, D(2026, 9, 1), None, "confirmed")
        items = compute_entitlements(emp, 2026, as_of=D(2026, 9, 2))
        prob = [i for i in items if i["basis"] == "PROBATION"]
        pro = [i for i in items if i["basis"] == "PRORATED"]
        self.assertEqual(sum(i["quantity"] for i in prob), Decimal("4"))  # carried, same ledger
        self.assertEqual(pro[0]["quantity"], Decimal("7.5"))  # 22 × 4/12 = 7.33 → 7.5

    def test_posting_is_idempotent_and_ledger_based(self):
        emp = make_employee("E7", login=False)
        first = post_entitlements(emp, 2026, None, as_of=D(2026, 9, 1))
        second = post_entitlements(emp, 2026, None, as_of=D(2026, 9, 1))
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(LeaveLedgerEntry.objects.filter(employee=emp).count(), 1)
        self.assertEqual(balance(emp, annual_leave(), 2026)["closing"], Decimal("22"))

    def test_ledger_entries_are_immutable(self):
        emp = make_employee("E8", login=False)
        post_entitlements(emp, 2026, None, as_of=D(2026, 9, 1))
        entry = LeaveLedgerEntry.objects.get(employee=emp)
        entry.quantity = 99
        with self.assertRaises(ValueError):
            entry.save()


class LeaveWorkflowTests(TestCase):
    def setUp(self):
        setup_reference()
        self.mgr = make_employee("M1")
        self.emp = make_employee("E1", manager=self.mgr)
        post_entitlements(self.emp, 2026, None, as_of=TODAY)
        self.al = annual_leave()

    def apply(self, start, end, **kw):
        return apply_for_leave(self.emp, self.emp.user, leave_type=self.al, start_date=start, end_date=end, reason="personal",
                               today=kw.pop("today", TODAY), **kw)

    def test_balance_pending_is_reserved_not_debited(self):
        self.apply(D(2026, 9, 14), D(2026, 9, 15))
        b = balance(self.emp, self.al, 2026)
        self.assertEqual((b["closing"], b["pending"], b["available"]), (Decimal("22"), Decimal("2"), Decimal("20")))

    def test_approval_debits_exactly_once(self):
        req = self.apply(D(2026, 9, 14), D(2026, 9, 15))
        wf.decide(req, self.mgr.user, "APPROVE", "ok")
        with self.assertRaises(WorkflowError):
            wf.decide(req, self.mgr.user, "APPROVE", "again")  # status is no longer pending
        post_leave_debit(LeaveRequest.objects.get(pk=req.pk), self.mgr.user)  # replayed side effect
        debits = LeaveLedgerEntry.objects.filter(leave_request=req, entry_type="DEBIT")
        self.assertEqual(debits.count(), 1)
        self.assertEqual(debits.get().quantity, Decimal("-2"))
        self.assertEqual(balance(self.emp, self.al, 2026)["available"], Decimal("20"))

    def test_rejection_requires_reason_and_does_not_debit(self):
        req = self.apply(D(2026, 9, 14), D(2026, 9, 14))
        with self.assertRaises(WorkflowError):
            wf.decide(req, self.mgr.user, "REJECT", "")
        wf.decide(req, self.mgr.user, "REJECT", "Busy week")
        self.assertFalse(LeaveLedgerEntry.objects.filter(leave_request=req).exists())
        self.assertEqual(balance(self.emp, self.al, 2026)["available"], Decimal("22"))

    def test_clarification_round_trip(self):
        req = self.apply(D(2026, 9, 14), D(2026, 9, 14))
        wf.decide(req, self.mgr.user, "REQUEST_INFO", "Who covers your work?")
        req.refresh_from_db()
        self.assertEqual(req.status, "NEEDS_INFORMATION")
        wf.respond(req, self.emp.user, "Colleague X")
        req.refresh_from_db()
        self.assertEqual(req.status, "PENDING_MANAGER")

    def test_cancellation_of_pending_by_employee_and_approved_by_hr_reverses(self):
        req = self.apply(D(2026, 9, 14), D(2026, 9, 14))
        wf.cancel(req, self.emp.user)
        self.assertEqual(LeaveRequest.objects.get(pk=req.pk).status, "CANCELLED")
        req2 = self.apply(D(2026, 9, 16), D(2026, 9, 17))
        wf.decide(req2, self.mgr.user, "APPROVE")
        with self.assertRaises(WorkflowError):
            wf.cancel(req2, self.emp.user, "changed my mind")  # employee cannot cancel an approved request
        admin = make_employee("A1", role="ADMIN")
        wf.cancel(req2, admin.user, "Trip cancelled, approved by manager by email")
        self.assertEqual(LeaveLedgerEntry.objects.filter(leave_request=req2, entry_type="REVERSAL").get().quantity, Decimal("2"))
        self.assertEqual(balance(self.emp, self.al, 2026)["closing"], Decimal("22"))

    def test_insufficient_balance_blocked(self):
        with self.assertRaises(ValidationError):
            self.apply(D(2026, 9, 14), D(2026, 10, 17))  # > 22 working days

    def test_overlapping_request_blocked(self):
        self.apply(D(2026, 9, 14), D(2026, 9, 15))
        with self.assertRaises(ValidationError):
            self.apply(D(2026, 9, 15), D(2026, 9, 16))

    def test_five_day_advance_notice(self):
        with self.assertRaises(ValidationError):
            self.apply(D(2026, 9, 5), D(2026, 9, 5))  # 4 days ahead
        ok = self.apply(D(2026, 9, 7), D(2026, 9, 7))  # 6 days ahead
        self.assertFalse(ok.late_application)
        boundary = self.apply(D(2026, 9, 8), D(2026, 9, 8), today=D(2026, 9, 3))  # exactly 5 days
        self.assertEqual(boundary.status, "PENDING_MANAGER")

    def test_advance_notice_flag_mode(self):
        set_policy("LEAVE", advance_notice_enforcement="FLAG")
        req = self.apply(D(2026, 9, 3), D(2026, 9, 3))
        self.assertTrue(req.late_application)

    def test_regular_leave_cannot_be_backdated(self):
        with self.assertRaises(ValidationError):
            self.apply(D(2026, 8, 28), D(2026, 8, 28))

    def test_emergency_leave_on_time_and_late_form(self):
        req = self.apply(D(2026, 9, 2), D(2026, 9, 3), is_emergency=True, rejoining_date=D(2026, 9, 4))
        self.assertEqual(req.chain, ["MANAGER", "HR"])
        self.assertEqual(req.form_deadline, D(2026, 9, 6))
        upload_signed_form(req, pdf(), self.emp.user, today=D(2026, 9, 6))
        self.assertFalse(LeaveRequest.objects.get(pk=req.pk).deadline_breached)
        late = self.apply(D(2026, 9, 9), D(2026, 9, 9), is_emergency=True, rejoining_date=D(2026, 9, 10), today=D(2026, 9, 9))
        upload_signed_form(late, pdf(), self.emp.user, today=D(2026, 9, 13))
        late.refresh_from_db()
        self.assertTrue(late.deadline_breached)
        self.assertTrue(late.payroll_review_required)
        self.assertEqual(late.status, "PENDING_MANAGER")  # flagged, never silently changed

    def test_overdue_emergency_forms_are_flagged_not_rejected(self):
        req = self.apply(D(2026, 9, 2), D(2026, 9, 2), is_emergency=True, rejoining_date=D(2026, 9, 3))
        flagged = flag_overdue_emergency_forms(today=D(2026, 9, 10))
        req.refresh_from_db()
        self.assertIn(req.pk, flagged)
        self.assertTrue(req.deadline_breached)
        self.assertEqual(req.status, "PENDING_MANAGER")

    def test_manual_adjustment_requires_reason_and_blocks_negative(self):
        with self.assertRaises(ValidationError):
            manual_adjustment(self.emp, self.al, 1, D(2026, 9, 1), "", None)
        with self.assertRaises(ValidationError):
            manual_adjustment(self.emp, self.al, -30, D(2026, 9, 1), "correction", None)
        manual_adjustment(self.emp, self.al, Decimal("1.5"), D(2026, 9, 1), "comp-off approved by HR", None)
        self.assertEqual(balance(self.emp, self.al, 2026)["adjustments"], Decimal("1.5"))


class WeeklyOffHolidayCountingTests(TestCase):
    """Weekly off / holiday between two leave days, with the presence exception."""

    def setUp(self):
        setup_reference()
        self.emp = make_employee("E1")
        self.policy = get_policy(PolicyVersion.Kind.LEAVE, TODAY, self.emp)

    def count(self, start, end, policy=None, duration="FULL"):
        return count_leave_days(self.emp, start, end, duration, "", policy or self.policy)

    def test_weekly_off_between_two_leave_days_is_counted(self):
        total, breakdown, _ = self.count(D(2026, 9, 5), D(2026, 9, 7))  # Sat, Sun(off), Mon
        self.assertEqual(total, Decimal("3"))
        self.assertIn("counted as leave", breakdown[1]["label"])

    def test_holiday_and_weekly_off_block_counted_without_presence(self):
        Holiday.objects.create(date=D(2026, 9, 5), name="Test holiday")
        total, _, _ = self.count(D(2026, 9, 4), D(2026, 9, 7))  # Fri, Sat(hol), Sun(off), Mon
        self.assertEqual(total, Decimal("4"))

    def test_presence_on_adjoining_working_day_makes_it_paid(self):
        Holiday.objects.create(date=D(2026, 9, 5), name="Test holiday")
        AttendanceRecord.objects.create(employee=self.emp, date=D(2026, 9, 4), status="PRESENT")
        total, breakdown, _ = self.count(D(2026, 9, 4), D(2026, 9, 7))
        self.assertEqual(total, Decimal("2"))  # Fri + Mon only
        self.assertIn("paid (policy exception)", breakdown[1]["label"])

    def test_non_working_day_at_edge_of_range_not_counted(self):
        total, _, _ = self.count(D(2026, 9, 6), D(2026, 9, 8))  # Sun, Mon, Tue — Sat not on leave
        self.assertEqual(total, Decimal("2"))

    def test_rule_can_be_disabled(self):
        p = set_policy("LEAVE", sandwich_rule_enabled=False)
        total, _, _ = self.count(D(2026, 9, 5), D(2026, 9, 7), policy=p)
        self.assertEqual(total, Decimal("2"))

    def test_cross_request_sandwich_is_flagged_not_charged(self):
        post_entitlements(self.emp, 2026, None, as_of=TODAY)
        apply_for_leave(self.emp, self.emp.user, leave_type=annual_leave(), start_date=D(2026, 9, 12), end_date=D(2026, 9, 12),
                        reason="x", today=TODAY)  # Saturday
        total, _, warnings = self.count(D(2026, 9, 14), D(2026, 9, 14))  # Monday; Sunday between
        self.assertEqual(total, Decimal("1"))
        self.assertTrue(warnings and "flagged for HR review" in warnings[0])

    def test_half_day(self):
        total, _, _ = self.count(D(2026, 9, 7), D(2026, 9, 7), duration="HALF")
        self.assertEqual(total, Decimal("0.5"))
        with self.assertRaises(ValidationError):
            self.count(D(2026, 9, 6), D(2026, 9, 6), duration="HALF")  # weekly off
