from django.urls import path

from . import views

app_name = "notifications"
urlpatterns = [
    path("", views.notification_list, name="list"),
    path("admin/settings/", views.settings_page, name="settings"),
    path("admin/email-log/", views.email_logs, name="email_logs"),
    path("admin/email-log/<int:pk>/retry/", views.retry_email, name="retry_email"),
]
