from django.urls import path

from . import views

app_name = "attendance"
urlpatterns = [
    path("day/<str:day>/", views.day_detail, name="day"),
    path("history/", views.history, name="history"),
    path("team/", views.team, name="team"),
    path("short-leave/", views.short_leave, name="short_leave"),
    path("missed-punch/", views.missed_punch, name="missed_punch"),
    path("on-duty/", views.on_duty, name="on_duty"),
    path("half-day-flags/", views.half_day_flags, name="half_day_flags"),
]
