import datetime

from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.approvals import services as wf
from apps.approvals.services import WorkflowError
from apps.attendance.request_services import submit_on_duty, submit_short_leave
from apps.notifications.models import EmailDeliveryLog, RecipientGroup

from .utils import make_employee, setup_reference

D = datetime.date
T = datetime.time


def at(d, hh, mm=0):
    return timezone.make_aware(datetime.datetime.combine(d, T(hh, mm)))


class ShortLeaveTests(TestCase):
    def setUp(self):
        setup_reference()
        self.mgr = make_employee("M1")
        self.emp = make_employee("E1", manager=self.mgr)
        self.peer = make_employee("E2", manager=self.mgr)
        self.other_mgr = make_employee("M2")
        RecipientGroup.objects.filter(code="HR").update(emails="hr@example.invalid")
        RecipientGroup.objects.filter(code="MANAGEMENT").update(emails="mgmt@example.invalid")

    def sl(self, d, start, end, kind="EARLY_DEPARTURE", now=None):
        return submit_short_leave(self.emp, self.emp.user, date=d, kind=kind, start_time=start, end_time=end, reason="Bank work",
                                  now=now or at(d, 8))

    def test_two_hour_boundary(self):
        self.sl(D(2026, 9, 1), T(16, 30), T(18, 30))  # exactly 120 min
        with self.assertRaises(ValidationError):
            self.sl(D(2026, 9, 2), T(16, 29), T(18, 30))  # 121 min

    def test_monthly_limit_excludes_rejected_and_cancelled(self):
        a = self.sl(D(2026, 9, 1), T(17, 0), T(18, 30))
        self.sl(D(2026, 9, 2), T(17, 0), T(18, 30))
        with self.assertRaises(ValidationError):
            self.sl(D(2026, 9, 3), T(17, 0), T(18, 30))
        wf.decide(a, self.mgr.user, "REJECT", "Not possible that day")
        self.sl(D(2026, 9, 3), T(17, 0), T(18, 30))  # rejected one no longer counts
        self.sl(D(2026, 10, 1), T(17, 0), T(18, 30))  # new month

    def test_late_arrival_same_day_deadline(self):
        d = D(2026, 9, 1)
        self.sl(d, T(9, 30), T(11, 0), kind="LATE_ARRIVAL", now=at(d, 23, 0))
        with self.assertRaises(ValidationError):
            self.sl(D(2026, 9, 2), T(9, 30), T(11, 0), kind="LATE_ARRIVAL", now=at(D(2026, 9, 3), 0, 1))

    def test_early_departure_must_be_requested_before(self):
        d = D(2026, 9, 1)
        with self.assertRaises(ValidationError):
            self.sl(d, T(16, 30), T(18, 30), now=at(d, 16, 45))

    def test_unauthorised_approval(self):
        req = self.sl(D(2026, 9, 1), T(17, 0), T(18, 30))
        with self.assertRaises(WorkflowError):
            wf.decide(req, self.other_mgr.user, "APPROVE")
        with self.assertRaises(WorkflowError):
            wf.decide(req, self.emp.user, "APPROVE")
        with self.assertRaises(WorkflowError):
            wf.decide(req, self.peer.user, "APPROVE")
        wf.decide(req, self.mgr.user, "APPROVE")

    def test_notifications_manager_team_and_hr_management_cc(self):
        with self.captureOnCommitCallbacks(execute=True):
            req = self.sl(D(2026, 9, 1), T(17, 0), T(18, 30))
        to_mgr = EmailDeliveryLog.objects.get(event_type="short_leave.submitted", recipient="m1@example.invalid")
        self.assertEqual(sorted(to_mgr.cc), ["hr@example.invalid", "mgmt@example.invalid"])
        team = EmailDeliveryLog.objects.get(event_type="short_leave.team_notice", recipient="e2@example.invalid")
        self.assertNotIn("Bank work", str(team.context))  # team members don't get the reason
        req.refresh_from_db()
        self.assertTrue(req.team_notified)
        sent = [m for m in mail.outbox if "m1@example.invalid" in m.to]
        self.assertEqual(sorted(sent[0].cc), ["hr@example.invalid", "mgmt@example.invalid"])


class OnDutyTests(TestCase):
    def setUp(self):
        setup_reference()
        self.mgr = make_employee("M1")
        self.emp = make_employee("E1", manager=self.mgr)

    def test_on_duty_requires_prior_submission(self):
        now = at(D(2026, 9, 1), 10)
        with self.assertRaises(ValidationError):
            submit_on_duty(self.emp, self.emp.user, start_at=at(D(2026, 9, 1), 9), end_at=at(D(2026, 9, 1), 17), destination="Client",
                           purpose="Install", now=now)
        r = submit_on_duty(self.emp, self.emp.user, start_at=at(D(2026, 9, 1), 11), end_at=at(D(2026, 9, 1), 17), destination="Client site",
                           purpose="Install", now=now)
        self.assertEqual(r.status, "PENDING_MANAGER")
