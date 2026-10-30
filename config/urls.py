from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("employees/", include("apps.employees.urls")),
    path("payroll/", include("apps.payroll.urls")),
    path("attendance/", include("apps.attendance.urls")),
    path("lms/", include("apps.leave_management.urls")),
    path("approvals/", include("apps.approvals.urls")),
    path("imports/", include("apps.imports.urls")),
    path("notifications/", include("apps.notifications.urls")),
    path("queries/", include("apps.queries.urls")),
    path("reports/", include("apps.reporting.urls")),
    path("settings/", include("apps.policies.urls")),
    path("audit/", include("apps.audit.urls")),
    path("", include("apps.core.urls")),
]

handler403 = "apps.core.views.permission_denied"
handler404 = "apps.core.views.not_found"
handler500 = "apps.core.views.server_error"
