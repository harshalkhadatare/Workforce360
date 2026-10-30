from django.urls import path

from . import views

app_name = "approvals"
urlpatterns = [
    path("pending/", views.pending, name="pending"),
    path("manager/", views.manager_dashboard, name="manager_dashboard"),
    path("r/<slug:slug>/<int:pk>/", views.detail, name="detail"),
    path("r/<slug:slug>/<int:pk>/act/", views.act, name="act"),
    path("r/<slug:slug>/<int:pk>/file/<str:field>/", views.attachment, name="attachment"),
]
