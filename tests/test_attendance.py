"""Attendance policy engine tests (Attendance Policy 002 R2 as configured).

Default schedule 09:30–18:30, Sunday off. Grace: 30 min/day, 60 min/month, cut-off 10:00.
Month used: September 2026 (Tue 1st).
"""
import datetime

from django.db import IntegrityError
from django.test import TestCase

from apps.approvals import services as wf
from apps.attendance.models import AttendanceAdjustment, AttendancePunch, AttendanceRecord, HalfDayReview
from apps.attendance.request_services import submit_missed_punch
from apps.attendance.services import LateDay, evaluate_grace_month, recalculate_month

from .utils import cover, day, make_employee, punch, set_policy, setup_reference

D = datetime.date
T = datetime.time
TODAY = D(2026, 9, 30)


def rec(emp, d):
    return AttendanceRecord.objects.get(employee=emp, date=d)


class GraceRuleUnitTests(TestCase):
    """Pure-function tests of the grace calculation (no database)."""

    def run_(self, lates, interpretation="PROSPECTIVE"):
        days = [LateDay(D(2026, 9, i + 1), T(9, 30 + m) if m < 30 else T(10, m - 30), m, T(9, 30)) for i, m in enumerate(lates)]
        return [r for _, r in sorted(evaluate_grace_month(days, daily_max=30, monthly_max=60, cutoff=T(10, 0), interpretation=interpretation).items())]

    def test_on_time(self):
        self.assertFalse(self.run_([0])[0].half_day)

    def test_monthly_boundary_exactly_60_is_allowed(self):
        r = self.run_([25, 25, 10])
        self.assertEqual([x.half_day for x in r], [False, False, False])
        self.assertEqual(r[-1].cumulative_after, 60)

    def test_prospective_flags_only_after_limit(self):
        r = self.run_([25, 25, 10, 1])
        self.assertEqual([x.half_day for x in r], [False, False, False, True])
        self.assertEqual(r[-1].flag_code, "EXCEEDS_MONTHLY")
        self.assertIn("awaiting HR confirmation", r[-1].explanation)

    def test_arrival_that_would_cross_limit_is_flagged_prospective(self):
        r = self.run_([25, 25, 20])
        self.assertEqual([x.half_day for x in r], [False, False, True])
        self.assertEqual(r[-1].grace_used, 0)

    def test_retrospective_flags_all_late_arrivals_from_first_occurrence(self):
        r = self.run_([25, 25, 15, 0], interpretation="RETROSPECTIVE")
        self.assertEqual([x.half_day for x in r], [True, True, True, False])
        self.assertTrue(all(x.flag_code == "MONTHLY_RETRO" for x in r[:3]))

    def test_retrospective_within_limit_flags_nothing(self):
        r = self.run_([20, 20, 20], interpretation="RETROSPECTIVE")
        self.assertFalse(any(x.half_day for x in r))


class AttendanceEngineTests(TestCase):
    def setUp(self):
        setup_reference()
        self.mgr = make_employee("M1")
        self.emp = make_employee("E1", manager=self.mgr)
        cover(D(2026, 9, 1), D(2026, 9, 29))

    def calc(self):
        recalculate_month(self.emp, 2026, 9, today=TODAY)

    def test_normal_attendance(self):
        day(self.emp, D(2026, 9, 1), "09:25", "18:40")
        self.calc()
        r = rec(self.emp, D(2026, 9, 1))
        self.assertEqual((r.status, r.late_minutes, r.grace_minutes_used, r.worked_minutes), ("PRESENT", 0, 0, 555))

    def test_late_arrival_uses_grace(self):
        day(self.emp, D(2026, 9, 1), "09:42", "18:35")
        self.calc()
        r = rec(self.emp, D(2026, 9, 1))
        self.assertEqual((r.status, r.late_minutes, r.grace_minutes_used, r.half_day_flag), ("PRESENT", 12, 12, False))
        self.assertIn("covered by grace", r.explanation)

    def test_daily_boundary_10_00_is_within_grace_10_01_is_not(self):
        day(self.emp, D(2026, 9, 1), "10:00:45", "18:30")  # seconds ignored → 10:00, 30 min late
        day(self.emp, D(2026, 9, 2), "10:01", "18:30")
        self.calc()
        a, b = rec(self.emp, D(2026, 9, 1)), rec(self.emp, D(2026, 9, 2))
        self.assertEqual((a.late_minutes, a.half_day_flag, a.grace_minutes_used), (30, False, 30))
        self.assertEqual((b.half_day_flag, b.flag_code, b.status), (True, "AFTER_CUTOFF", "HALF_DAY_FLAG"))
        self.assertIn("after the 10:00 cut-off", b.explanation)

    def test_monthly_limit_exhausted_flags_half_day(self):
        for d, tin in ((1, "09:55"), (2, "09:55"), (3, "09:40"), (4, "09:35")):  # 25+25+10 = 60, then 5
            day(self.emp, D(2026, 9, d), tin, "18:30")
        self.calc()
        self.assertEqual(rec(self.emp, D(2026, 9, 3)).month_grace_cumulative, 60)
        r4 = rec(self.emp, D(2026, 9, 4))
        self.assertTrue(r4.half_day_flag)
        self.assertEqual(r4.flag_code, "EXCEEDS_MONTHLY")

    def test_half_day_flag_creates_review_and_decision_survives_recalc(self):
        day(self.emp, D(2026, 9, 1), "10:15", "18:30")
        self.calc()
        review = HalfDayReview.objects.get(attendance_record__date=D(2026, 9, 1), employee=self.emp)
        self.assertEqual(review.status, "PENDING_MANAGER")
        wf.decide(review, self.mgr.user, "REJECT", "Confirmed: no prior intimation")  # reject = confirm the half day
        review.refresh_from_db()
        self.assertEqual((review.status, review.outcome), ("REJECTED", "CONFIRMED"))
        self.calc()  # recalculation must not create a second review or undo the decision
        live = HalfDayReview.objects.filter(attendance_record__date=D(2026, 9, 1)).exclude(status="CANCELLED")
        self.assertEqual(live.count(), 1)
        self.assertEqual(live.get().outcome, "CONFIRMED")

    def test_waiver_needs_manager_then_hr(self):
        day(self.emp, D(2026, 9, 1), "10:15", "18:30")
        self.calc()
        review = HalfDayReview.objects.get(attendance_record__date=D(2026, 9, 1))
        wf.decide(review, self.mgr.user, "APPROVE", "Client visit")
        review.refresh_from_db()
        self.assertEqual(review.status, "PENDING_HR")  # "awaiting HR review"
        admin = make_employee("A1", role="ADMIN")
        wf.decide(review, admin.user, "APPROVE", "OK")
        review.refresh_from_db()
        self.assertEqual((review.status, review.outcome), ("APPROVED", "WAIVED"))

    def test_missing_in_and_missing_out_are_not_absence(self):
        day(self.emp, D(2026, 9, 1), None, "18:30")
        day(self.emp, D(2026, 9, 2), "09:30", None)
        self.calc()
        for d in (1, 2):
            r = rec(self.emp, D(2026, 9, d))
            self.assertEqual(r.status, "INCOMPLETE")
            self.assertIn("not treated as absence", r.explanation)

    def test_absent_only_when_data_was_imported(self):
        self.calc()
        self.assertEqual(rec(self.emp, D(2026, 9, 1)).status, "ABSENT")
        self.assertEqual(rec(self.emp, D(2026, 9, 6)).status, "WEEKLY_OFF")
        self.assertEqual(rec(self.emp, D(2026, 9, 30)).status, "NOT_IMPORTED")  # outside covered range

    def test_multiple_punches_use_first_in_and_last_out(self):
        d = D(2026, 9, 1)
        punch(self.emp, d, "09:20", "IN")
        punch(self.emp, d, "13:00", "OUT")
        punch(self.emp, d, "13:45", "IN")
        punch(self.emp, d, "18:45", "OUT")
        self.calc()
        r = rec(self.emp, d)
        self.assertEqual((r.punch_count, r.worked_minutes, r.status), (4, 565, "PRESENT"))

    def test_duplicate_punch_rejected_by_database(self):
        punch(self.emp, D(2026, 9, 1), "09:20", "IN")
        with self.assertRaises(IntegrityError):
            punch(self.emp, D(2026, 9, 1), "09:20", "IN")

    def test_approved_missed_punch_creates_adjustment_and_preserves_original(self):
        d = D(2026, 9, 2)
        day(self.emp, d, "09:28", None)
        self.calc()
        req = submit_missed_punch(self.emp, self.emp.user, date=d, issue_type="MISSING_OUT", requested_out=T(18, 35),
                                  reason="Forgot to punch out", today=TODAY)
        self.assertEqual(req.assigned_manager, self.mgr)
        wf.decide(req, self.mgr.user, "APPROVE", "Verified with CCTV")
        adj = AttendanceAdjustment.objects.get(missed_punch_request=req)
        self.assertIsNone(adj.original_out)
        self.assertEqual(AttendancePunch.objects.filter(employee=self.emp, punch_date=d).count(), 1)  # raw evidence untouched
        r = rec(self.emp, d)
        self.assertEqual((r.status, r.is_adjusted), ("PRESENT", True))
        from apps.attendance.services import apply_missed_punch

        apply_missed_punch(req, self.mgr.user)  # replay is a no-op
        self.assertEqual(AttendanceAdjustment.objects.filter(missed_punch_request=req).count(), 1)

    def test_correction_clears_half_day_flag_and_cancels_open_review(self):
        d = D(2026, 9, 3)
        day(self.emp, d, "10:20", "18:30")  # wrong In captured
        self.calc()
        self.assertTrue(rec(self.emp, d).half_day_flag)
        req = submit_missed_punch(self.emp, self.emp.user, date=d, issue_type="INCORRECT_IN", requested_in=T(9, 31),
                                  reason="Device was down at entry", today=TODAY)
        wf.decide(req, self.mgr.user, "APPROVE")
        self.assertFalse(rec(self.emp, d).half_day_flag)
        self.assertEqual(HalfDayReview.objects.get(attendance_record__date=d).status, "CANCELLED")

    def test_holiday_and_policy_version_recorded(self):
        from apps.policies.models import Holiday

        Holiday.objects.create(date=D(2026, 9, 1), name="Test holiday")
        self.calc()
        r = rec(self.emp, D(2026, 9, 1))
        self.assertEqual(r.status, "HOLIDAY")
        self.assertIsNotNone(rec(self.emp, D(2026, 9, 2)).policy_version_id)

    def test_new_policy_version_does_not_rewrite_decided_reviews(self):
        day(self.emp, D(2026, 9, 1), "10:15", "18:30")
        self.calc()
        review = HalfDayReview.objects.get(attendance_record__date=D(2026, 9, 1))
        wf.decide(review, self.mgr.user, "APPROVE", "Waived — client visit")
        admin = make_employee("A1", role="ADMIN")
        wf.decide(review, admin.user, "APPROVE", "HR agrees")
        review.refresh_from_db()
        self.assertEqual(review.outcome, "WAIVED")
        set_policy("ATTENDANCE", grace_daily_max_minutes=60, half_day_cutoff_time="11:00")
        self.calc()
        review.refresh_from_db()
        self.assertEqual((review.status, review.outcome), ("APPROVED", "WAIVED"))
