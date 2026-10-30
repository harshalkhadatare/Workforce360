import datetime
import smtplib
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.leave_management.services import apply_for_leave, post_entitlements
from apps.notifications import services
from apps.notifications.models import EmailDeliveryLog, Notification

from .utils import annual_leave, make_employee, setup_reference

D = datetime.date


class EmailTests(TestCase):
    def setUp(self):
        setup_reference()
        self.mgr = make_employee("M1")
        self.emp = make_employee("E1", manager=self.mgr)
        post_entitlements(self.emp, 2026, None, as_of=D(2026, 9, 1))

    def submit_leave(self):
        with self.captureOnCommitCallbacks(execute=True):
            return apply_for_leave(self.emp, self.emp.user, leave_type=annual_leave(), start_date=D(2026, 9, 14),
                                   end_date=D(2026, 9, 14), reason="Personal", today=D(2026, 9, 1))

    def test_submission_notifies_assigned_manager_in_portal_and_by_email(self):
        self.submit_leave()
        self.assertTrue(Notification.objects.filter(user=self.mgr.user, event_type="request.submitted").exists())
        log = EmailDeliveryLog.objects.get(event_type="leave.submitted")
        self.assertEqual((log.recipient, log.status, log.attempts), ("m1@example.invalid", "SENT", 1))
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertIn("E1", msg.subject)
        self.assertEqual(msg.alternatives[0][1], "text/html")  # HTML + plain-text alternative
        self.assertIn("/approvals/r/leave/", msg.body)

    def test_decision_emails_employee(self):
        from apps.approvals import services as wf

        req = self.submit_leave()
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            wf.decide(req, self.mgr.user, "APPROVE", "Enjoy")
        self.assertEqual([m.to for m in mail.outbox], [["e1@example.invalid"]])
        self.assertIn("Enjoy", mail.outbox[0].body)

    def test_duplicate_notification_prevented(self):
        kw = dict(event_type="t", recipient="x@example.invalid", subject="s", context={"heading": "h", "intro": "i"}, dedupe_key="same-key")
        with self.captureOnCommitCallbacks(execute=True):
            services.queue_email(**kw)
            services.queue_email(**kw)
        self.assertEqual(EmailDeliveryLog.objects.filter(dedupe_key="same-key").count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_transient_failure_retries_then_succeeds(self):
        with mock.patch("django.core.mail.EmailMultiAlternatives.send", side_effect=smtplib.SMTPServerDisconnected("down")):
            with self.captureOnCommitCallbacks(execute=True):
                services.queue_email(event_type="t", recipient="x@example.invalid", subject="s", context={}, dedupe_key="k1")
        log = EmailDeliveryLog.objects.get(dedupe_key="k1")
        self.assertEqual((log.status, log.attempts), ("RETRYING", 1))
        self.assertIn("SMTPServerDisconnected", log.last_error)
        self.assertEqual(services.retry_due_emails(now=timezone.now()), 0)  # not due yet (backoff)
        self.assertEqual(services.retry_due_emails(now=timezone.now() + timedelta(minutes=5)), 1)
        log.refresh_from_db()
        self.assertEqual((log.status, log.attempts), ("SENT", 2))

    @override_settings(EMAIL_MAX_ATTEMPTS=2)
    def test_permanent_failure_after_max_attempts(self):
        with mock.patch("django.core.mail.EmailMultiAlternatives.send", side_effect=smtplib.SMTPServerDisconnected("down")):
            with self.captureOnCommitCallbacks(execute=True):
                services.queue_email(event_type="t", recipient="x@example.invalid", subject="s", context={}, dedupe_key="k2")
            services.retry_due_emails(now=timezone.now() + timedelta(hours=1))
        self.assertEqual(EmailDeliveryLog.objects.get(dedupe_key="k2").status, "FAILED")

    def test_non_transient_error_fails_immediately(self):
        with mock.patch("django.core.mail.EmailMultiAlternatives.send", side_effect=smtplib.SMTPRecipientsRefused({})):
            with self.captureOnCommitCallbacks(execute=True):
                services.queue_email(event_type="t", recipient="x@example.invalid", subject="s", context={}, dedupe_key="k3")
        self.assertEqual(EmailDeliveryLog.objects.get(dedupe_key="k3").status, "FAILED")

    @override_settings(EMAIL_ENABLED=False)
    def test_unconfigured_email_never_reported_as_sent(self):
        self.submit_leave()
        log = EmailDeliveryLog.objects.get(event_type="leave.submitted")
        self.assertEqual(log.status, "NOT_CONFIGURED")
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(Notification.objects.filter(user=self.mgr.user).exists())  # in-portal notice still created

    def test_secrets_not_logged(self):
        from apps.employees.services import send_account_setup_email

        entry = send_account_setup_email(self.emp.user)
        self.assertNotIn("link", entry.context)
        self.assertNotIn("/password/reset/", str(entry.context))
        self.assertIn("/password/reset/", mail.outbox[-1].body)
