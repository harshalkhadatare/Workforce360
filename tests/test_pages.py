"""Render every page for each role to catch template/view errors, and check role-based visibility."""
import datetime

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.approvals.registry import request_models
from apps.attendance.request_services import submit_missed_punch, submit_short_leave
from apps.attendance.services import recalculate_month
from apps.imports.models import AttendanceImportBatch
from apps.leave_management.services import apply_for_leave, post_entitlements
from apps.queries.models import EmployeeQuery
from apps.reporting.reports import REPORTS

from .utils import PASSWORD, annual_leave, cover, day, make_employee, setup_reference

D = datetime.date


@override_settings(STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                             "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class PageRenderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        setup_reference()
        cls.admin = make_employee("ADM1", role="ADMIN")
        cls.mgr = make_employee("MGR1", role="MANAGER")
        cls.emp = make_employee("EMP1", manager=cls.mgr)
        post_entitlements(cls.emp, 2026, None, as_of=D(2026, 9, 1))
        cover(D(2026, 9, 1), D(2026, 9, 10))
        day(cls.emp, D(2026, 9, 1), "10:12", "18:30")
        day(cls.emp, D(2026, 9, 2), "09:31", None)
        recalculate_month(cls.emp, 2026, 9, today=D(2026, 9, 10))
        cls.leave = apply_for_leave(cls.emp, cls.emp.user, leave_type=annual_leave(), start_date=D(2026, 9, 14), end_date=D(2026, 9, 15),
                                    reason="Trip", today=D(2026, 9, 1))
        cls.mp = submit_missed_punch(cls.emp, cls.emp.user, date=D(2026, 9, 2), issue_type="MISSING_OUT",
                                     requested_out=datetime.time(18, 30), reason="Forgot", today=D(2026, 9, 10))
        from django.utils import timezone

        cls.sl = submit_short_leave(cls.emp, cls.emp.user, date=D(2026, 9, 3), kind="EARLY_DEPARTURE", start_time=datetime.time(17, 0),
                                    end_time=datetime.time(18, 30), reason="Doctor",
                                    now=timezone.make_aware(datetime.datetime(2026, 9, 3, 9, 0)))
        cls.query = EmployeeQuery.objects.create(employee=cls.emp, category="LEAVE", subject="Balance?", description="Please check")

    def setUp(self):
        cache.clear()

    def login(self, emp):
        self.client.logout()
        self.assertTrue(self.client.login(username=emp.employee_id, password=PASSWORD))

    def assert_ok(self, urls):
        for url in urls:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, f"{url} -> {r.status_code}")

    def employee_urls(self):
        e = self.emp
        urls = [reverse("core:home"), reverse("notifications:list"), reverse("accounts:password_change"),
                reverse("queries:list"), reverse("queries:create"), reverse("queries:detail", args=[self.query.pk]),
                reverse("lms:dashboard"), reverse("lms:calendar"), reverse("lms:balance"), reverse("lms:apply"), reverse("lms:apply") + "?emergency=1",
                reverse("lms:my_requests"), reverse("lms:holidays"), reverse("attendance:history"), reverse("attendance:day", args=["2026-09-01"]),
                reverse("attendance:day", args=["2026-09-25"]), reverse("attendance:short_leave"), reverse("attendance:missed_punch") + "?date=2026-09-02",
                reverse("attendance:on_duty"), reverse("attendance:half_day_flags"), reverse("reporting:index"),
                reverse("approvals:detail", args=["leave", self.leave.pk]), reverse("approvals:detail", args=["missed-punch", self.mp.pk]),
                reverse("approvals:detail", args=["short-leave", self.sl.pk])]
        urls += [reverse("employees:detail", args=[e.pk, s]) for s in ("personal", "contact", "identity", "qualifications", "employment",
                                                                        "bank", "family", "documents", "history")]
        urls += [reverse("employees:detail", args=[e.pk, s]) + "?edit=1" for s in ("personal", "contact", "family", "documents")]
        urls += [reverse("reporting:report", args=[k]) for k, r in REPORTS.items() if r.audience == "employee"]
        urls += [reverse("reporting:report", args=["my-attendance"]) + "?month=2026-09&format=xlsx",
                 reverse("reporting:report", args=["my-leave-history"]) + "?year=2026&format=csv"]
        urls.append(reverse("lms:calendar_events") + "?start=2026-08-31&end=2026-10-05")
        return urls

    def test_employee_pages(self):
        self.login(self.emp)
        self.assertRedirects(self.client.get(reverse("employees:me")), reverse("employees:detail", args=[self.emp.pk, "personal"]))
        self.assert_ok(self.employee_urls())
        r = self.client.get(reverse("lms:calendar_events") + "?start=2026-08-31&end=2026-10-05")
        cats = {e["extendedProps"]["category"] for e in r.json()}
        self.assertTrue({"HALF_DAY_FLAG", "INCOMPLETE", "PENDING_LEAVE", "WEEKLY_OFF", "SHORT_LEAVE"} <= cats, cats)

    def test_manager_pages(self):
        self.login(self.mgr)
        urls = [reverse("approvals:manager_dashboard"), reverse("approvals:pending"), reverse("attendance:team"),
                reverse("lms:calendar") + "?scope=team", reverse("lms:calendar_events") + "?start=2026-08-31&end=2026-10-05&scope=team",
                reverse("employees:directory"), reverse("employees:detail", args=[self.emp.pk, "personal"]),
                reverse("attendance:history") + f"?employee={self.emp.pk}", reverse("attendance:day", args=["2026-09-01"]) + f"?employee={self.emp.pk}",
                reverse("lms:balance") + f"?employee={self.emp.pk}"]
        urls += [reverse("approvals:detail", args=[m.URL_SLUG, obj.pk]) for m in request_models().values() for obj in m.objects.filter(employee=self.emp)]
        urls += [reverse("reporting:report", args=[k]) for k, r in REPORTS.items() if r.audience == "manager"]
        self.assert_ok(urls)
        pend = self.client.get(reverse("approvals:pending"))
        self.assertContains(pend, "Annual Leave, 14 Sep")
        self.assertNotContains(pend, "Trip")  # reasons stay on the detail page
        self.assertEqual(self.client.get(reverse("imports:upload")).status_code, 403)

    def test_admin_pages(self):
        self.login(self.admin)
        call_command("seed_reference_data", stdout=__import__("io").StringIO())
        b = AttendanceImportBatch.objects.first()
        urls = [reverse("core:admin_dashboard"), reverse("core:admin_dashboard") + "?date=2026-09-01", reverse("imports:upload"),
                reverse("imports:history"), reverse("imports:results", args=[b.pk]), reverse("employees:admin_users"),
                reverse("employees:create"), reverse("employees:account", args=[self.emp.pk]), reverse("policies:index"),
                reverse("policies:new_version", args=["leave"]), reverse("policies:new_version", args=["attendance"]),
                reverse("policies:leave_types"), reverse("policies:holidays"), reverse("policies:schedules"),
                reverse("notifications:settings"), reverse("notifications:email_logs"), reverse("audit:list"),
                reverse("lms:calendar") + "?scope=company", reverse("lms:calendar_events") + "?start=2026-08-31&end=2026-10-05&scope=company",
                reverse("attendance:team") + "?date=2026-09-01", reverse("employees:directory"), reverse("queries:list"),
                reverse("queries:detail", args=[self.query.pk])]
        urls += [reverse("employees:detail", args=[self.emp.pk, s]) + "?edit=1" for s in ("personal", "contact", "identity", "employment", "bank", "payroll")]
        urls += [reverse("reporting:report", args=[k]) for k in REPORTS]
        urls += [reverse("reporting:report", args=[k]) + "?format=xlsx" for k in REPORTS]
        self.assert_ok(urls)

    def test_admin_creates_employee_and_policy_version_via_ui(self):
        self.login(self.admin)
        r = self.client.post(reverse("employees:create"), {"employee_id": "new01", "first_name": "New", "last_name": "Joiner",
                                                           "official_email": "new01@example.invalid", "date_of_joining": "2026-10-01",
                                                           "employment_type": "PERMANENT", "reporting_manager": self.mgr.pk,
                                                           "create_login": "on", "role": "EMPLOYEE"})
        self.assertEqual(r.status_code, 302)
        from apps.employees.models import Employee

        new = Employee.objects.get(employee_id="NEW01")
        self.assertTrue(hasattr(new, "user"))
        self.assertFalse(new.user.has_usable_password())  # no password set by HR
        from apps.policies.defaults import DEFAULTS

        data = {"name": "Attendance v2", "effective_from": "2026-11-01", "change_reason": "HR confirmed prospective reading", "confirm": "on",
                "activate": "on"}
        for k, spec in DEFAULTS["ATTENDANCE"].items():
            if spec["type"] == "bool":
                if spec["default"]:
                    data[f"p_{k}"] = "on"
            else:
                data[f"p_{k}"] = spec["default"]
        r = self.client.post(reverse("policies:new_version", args=["attendance"]), data)
        self.assertRedirects(r, reverse("policies:index"))
        from apps.policies.models import PolicyVersion

        v = PolicyVersion.objects.get(name="Attendance v2")
        self.assertEqual((v.status, v.parameters["grace_monthly_max_minutes"], v.parameters["half_day_cutoff_time"]), ("ACTIVE", 60, "10:00"))
