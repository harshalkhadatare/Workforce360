from django.urls import path

from . import views

app_name = "policies"
urlpatterns = [
    path("", views.index, name="index"),
    path("policy/<str:kind>/new/", views.new_version, name="new_version"),
    path("policy/<int:pk>/status/", views.activate, name="activate"),
    path("policy/<int:pk>/confirm/", views.confirm_parameter, name="confirm_parameter"),
    path("approval-chains/", views.save_chain, name="save_chain"),
    path("leave-types/", views.leave_types, name="leave_types"),
    path("holidays/", views.holidays, name="holidays"),
    path("schedules/", views.schedules, name="schedules"),
]
