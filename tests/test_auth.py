import re

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.employees import services as emp_services

from .utils import PASSWORD, make_employee, setup_reference


class AuthenticationTests(TestCase):
    def setUp(self):
        cache.clear()
        setup_reference()
        self.emp = make_employee("EMP100")

    def login(self, eid, pwd):
        return self.client.post(reverse("accounts:login"), {"employee_id": eid, "password": pwd})

    def test_valid_login_with_employee_id_case_insensitive(self):
        r = self.login("emp100", PASSWORD)
        self.assertRedirects(r, reverse("core:home"))
        self.assertTrue(self.client.get(reverse("core:home")).context["user"].is_authenticated)

    def test_invalid_login_shows_generic_message(self):
        r = self.login("EMP100", "wrong-password")
        self.assertContains(r, "Invalid Employee ID or password.")
        r2 = self.login("NOSUCH", "whatever-123")
        self.assertContains(r2, "Invalid Employee ID or password.")  # same message: no account enumeration

    def test_disabled_account_cannot_login_and_gets_generic_message(self):
        emp_services.set_active(self.emp, False, None, "left company")
        r = self.login("EMP100", PASSWORD)
        self.assertContains(r, "Invalid Employee ID or password.")

    def test_lockout_after_repeated_failures_even_with_correct_password(self):
        for _ in range(5):
            self.login("EMP100", "bad-password")
        r = self.login("EMP100", PASSWORD)
        self.assertContains(r, "Too many unsuccessful sign-in attempts")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_unauthenticated_access_redirects_to_login(self):
        for name in ["core:home", "lms:dashboard", "employees:directory", "imports:upload", "audit:list"]:
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302, name)
            self.assertIn(reverse("accounts:login"), r["Location"])

    def test_calendar_api_requires_authentication(self):
        r = self.client.get(reverse("lms:calendar_events"), {"start": "2026-09-01", "end": "2026-09-30"})
        self.assertIn(r.status_code, (401, 403))

    def test_logout_requires_post(self):
        self.login("EMP100", PASSWORD)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        self.client.post(reverse("accounts:logout"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_password_reset_token_is_single_use(self):
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(reverse("accounts:password_reset"), {"employee_id": "EMP100"})
        self.assertRedirects(r, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn(PASSWORD, mail.outbox[0].body)  # never send passwords
        path = re.search(r"https?://[^/]+(/accounts/password/reset/[^\s\"]+/)", mail.outbox[0].body).group(1)
        r = self.client.get(path, follow=True)
        self.assertTrue(r.context["validlink"])
        set_url = r.redirect_chain[-1][0]
        r = self.client.post(set_url, {"new_password1": "Brand-New-Pass-77", "new_password2": "Brand-New-Pass-77"})
        self.assertRedirects(r, reverse("accounts:password_reset_complete"))
        self.assertEqual(self.login("EMP100", "Brand-New-Pass-77").status_code, 302)
        self.client.post(reverse("accounts:logout"))
        r = self.client.get(path, follow=True)  # reuse the same link
        self.assertFalse(r.context["validlink"])

    def test_password_reset_for_unknown_id_looks_identical_and_sends_nothing(self):
        r = self.client.post(reverse("accounts:password_reset"), {"employee_id": "UNKNOWN9"})
        self.assertRedirects(r, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(SESSION_IDLE_TIMEOUT_SECONDS=-1)
    def test_idle_session_expires(self):
        self.login("EMP100", PASSWORD)
        r = self.client.get(reverse("core:home"))
        self.assertEqual(r.status_code, 302)

    def test_password_change(self):
        self.login("EMP100", PASSWORD)
        r = self.client.post(reverse("accounts:password_change"), {"old_password": PASSWORD, "new_password1": "Another-Pass-881",
                                                                   "new_password2": "Another-Pass-881"})
        self.assertRedirects(r, reverse("core:home"))
        self.emp.user.refresh_from_db()
        self.assertTrue(self.emp.user.check_password("Another-Pass-881"))
