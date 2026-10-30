from django.urls import path

from . import views

app_name = "employees"
urlpatterns = [
    path("directory/", views.directory, name="directory"),
    path("me/", views.me, name="me"),
    path("new/", views.create, name="create"),
    path("admin/", views.admin_users, name="admin_users"),
    path("admin/master-data/", views.master_data, name="master_data"),
    path("admin/<int:pk>/account/", views.account, name="account"),
    path("<int:pk>/actions/confirm/", views.confirm, name="confirm"),
    path("<int:pk>/actions/active/", views.set_active, name="set_active"),
    path("<int:pk>/documents/<int:doc_id>/download/", views.download_document, name="document_download"),
    path("<int:pk>/", views.detail, name="detail_default"),
    path("<int:pk>/<slug:section>/", views.detail, name="detail"),
]
