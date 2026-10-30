import datetime

from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Role
from apps.approvals import services as wf
from apps.approvals.services import WorkflowError
from apps.audit.models import AuditLog
from apps.employees.models import BankDetail, EmployeeDocument, IdentityDetail
from apps.leave_management.services import apply_for_leave, manual_adjustment

from .utils import PASSWORD, annual_leave, make_employee, setup_reference


class RolePermissionTests(TestCase):
    def setUp(self):
        cache.clear()
        setup_reference()
        self.admin = make_employee("ADM1", role=Role.ADMIN)
        self.mgr = make_employee("MGR1", role=Role.MANAGER)
        self.mgr2 = make_employee("MGR2", role=Role.MANAGER)
        self.emp = make_employee("EMP1", manager=self.mgr)
        self.other = make_employee("EMP2", manager=self.mgr2)
        IdentityDetail.objects.create(employee=self.emp, pan_number="ABCDE1234F", aadhaar_number="123412341234")
        BankDetail.objects.create(employee=self.emp, bank_name="Demo Bank", account_holder_name="Emp1", account_number="1234567890123",
                                  ifsc_code="DEMO0123456")

    def as_(self, emp):
        self.client.logout()
        self.assertTrue(self.client.login(username=emp.employee_id, password=PASSWORD))

    def url(self, emp, section="personal"):
        return reverse("employees:detail", args=[emp.pk, section])

    def test_employee_can_view_own_record(self):
        self.as_(self.emp)
        self.assertEqual(self.client.get(self.url(self.emp)).status_code, 200)
        self.assertEqual(self.client.get(self.url(self.emp, "identity")).status_code, 200)

    def test_employee_cannot_view_other_employee_by_changing_id(self):
        self.as_(self.emp)
        for section in ("personal", "identity", "bank", "documents"):
            self.assertEqual(self.client.get(self.url(self.other, section)).status_code, 404, section)

    def test_employee_cannot_open_directory_or_admin_pages(self):
        self.as_(self.emp)
        for name in ("employees:directory", "employees:admin_users", "imports:upload", "policies:index", "audit:list",
                     "core:admin_dashboard", "approvals:pending", "notifications:settings"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_identity_values_masked_by_default(self):
        self.as_(self.emp)
        r = self.client.get(self.url(self.emp, "identity"))
        self.assertNotContains(r, "123412341234")
        self.assertContains(r, "1234")  # last four only
        r = self.client.get(self.url(self.emp, "identity") + "?reveal=1")
        self.assertContains(r, "123412341234")
        self.assertTrue(AuditLog.objects.filter(action="sensitive.identity_viewed").exists())

    def test_manager_sees_team_member_but_not_sensitive_sections(self):
        self.as_(self.mgr)
        self.assertEqual(self.client.get(self.url(self.emp)).status_code, 200)
        self.assertEqual(self.client.get(self.url(self.emp, "bank")).status_code, 404)
        self.assertEqual(self.client.get(self.url(self.emp, "identity")).status_code, 404)

    def test_manager_cannot_access_unrelated_employee(self):
        self.as_(self.mgr)
        self.assertEqual(self.client.get(self.url(self.other)).status_code, 404)
        r = self.client.get(reverse("attendance:history") + f"?employee={self.other.pk}")
        self.assertEqual(r.status_code, 404)

    def test_manager_cannot_approve_unrelated_request(self):
        manual_adjustment(self.other, annual_leave(), 10, datetime.date(2026, 1, 1), "opening", None, "OPENING")
        req = apply_for_leave(self.other, self.other.user, leave_type=annual_leave(), start_date=datetime.date(2026, 9, 14),
                              end_date=datetime.date(2026, 9, 14), reason="family", today=datetime.date(2026, 9, 1))
        with self.assertRaises(WorkflowError):
            wf.decide(req, self.mgr.user, "APPROVE")
        # and through the HTTP layer: the request is not even visible
        self.as_(self.mgr)
        self.assertEqual(self.client.get(reverse("approvals:detail", args=["leave", req.pk])).status_code, 404)
        self.client.post(reverse("approvals:act", args=["leave", req.pk]), {"action": "APPROVE"})
        req.refresh_from_db()
        self.assertEqual(req.status, "PENDING_MANAGER")

    def test_employee_cannot_approve_own_request(self):
        manual_adjustment(self.mgr, annual_leave(), 10, datetime.date(2026, 1, 1), "opening", None, "OPENING")
        req = apply_for_leave(self.mgr, self.mgr.user, leave_type=annual_leave(), start_date=datetime.date(2026, 9, 14),
                              end_date=datetime.date(2026, 9, 14), reason="x", today=datetime.date(2026, 9, 1))
        self.assertEqual(req.status, "PENDING_HR")  # no manager → HR; never self
        with self.assertRaises(WorkflowError):
            wf.decide(req, self.mgr.user, "APPROVE")

    def test_admin_can_manage_everything(self):
        self.as_(self.admin)
        for url in (self.url(self.other), self.url(self.other, "bank"), reverse("employees:directory"), reverse("imports:upload"),
                    reverse("policies:index"), reverse("audit:list"), reverse("core:admin_dashboard")):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_hr_group_member_gets_hr_but_not_admin_only_pages(self):
        from django.contrib.auth.models import Group

        hr = make_employee("HR1")
        hr.user.groups.add(Group.objects.get(name="HR"))
        self.as_(hr)
        self.assertEqual(self.client.get(reverse("imports:upload")).status_code, 200)
        self.assertEqual(self.client.get(reverse("audit:list")).status_code, 403)  # admin only

    def test_payroll_section_restricted_to_finance_admin(self):
        self.as_(self.emp)
        self.assertEqual(self.client.get(self.url(self.emp, "payroll")).status_code, 404)
        self.as_(self.admin)
        self.assertEqual(self.client.get(self.url(self.emp, "payroll")).status_code, 200)

    def test_document_download_is_authorised(self):
        f = SimpleUploadedFile("cert.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        doc = EmployeeDocument.objects.create(employee=self.emp, category="EDUCATION", title="Cert", file=f, original_name="cert.pdf",
                                              size_bytes=12, sha256="x")
        url = reverse("employees:document_download", args=[self.emp.pk, doc.pk])
        self.as_(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.as_(self.emp)
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])

    def test_calendar_scope_enforced(self):
        self.as_(self.emp)
        r = self.client.get(reverse("lms:calendar_events"), {"start": "2026-09-01", "end": "2026-09-30", "scope": "company"})
        self.assertEqual(r.status_code, 403)
        r = self.client.get(reverse("lms:calendar_events"), {"start": "2026-09-01", "end": "2026-09-30", "employee": self.other.pk})
        self.assertEqual(r.status_code, 404)

    def test_reports_respect_scope(self):
        self.as_(self.emp)
        self.assertEqual(self.client.get(reverse("reporting:report", args=["leave-ledger"])).status_code, 403)
        r = self.client.get(reverse("reporting:report", args=["my-attendance"]) + f"?employee={self.other.pk}")
        self.assertEqual(r.status_code, 404)

    def test_audit_log_is_immutable(self):
        entry = AuditLog.objects.first()
        with self.assertRaises(PermissionDenied):
            entry.save()
        with self.assertRaises(PermissionDenied):
            entry.delete()
