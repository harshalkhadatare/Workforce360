from django.urls import path

from . import views

app_name = "lms"
urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("calendar/", views.calendar_page, name="calendar"),
    path("api/calendar-events/", views.calendar_events, name="calendar_events"),
    path("balance/", views.balance_page, name="balance"),
    path("apply/", views.apply, name="apply"),
    path("requests/", views.my_requests, name="my_requests"),
    path("holidays/", views.holidays, name="holidays"),
]
