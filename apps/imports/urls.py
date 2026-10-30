from django.urls import path

from . import views

app_name = "imports"
urlpatterns = [
    path("", views.upload, name="upload"),
    path("history/", views.history, name="history"),
    path("sample.xlsx", views.sample, name="sample"),
    path("<int:pk>/mapping/", views.mapping, name="mapping"),
    path("<int:pk>/preview/", views.preview, name="preview"),
    path("<int:pk>/confirm/", views.confirm, name="confirm"),
    path("<int:pk>/results/", views.results, name="results"),
    path("<int:pk>/retry/", views.retry, name="retry"),
    path("<int:pk>/rollback/", views.rollback, name="rollback"),
    path("<int:pk>/report.xlsx", views.error_report, name="error_report"),
    path("<int:pk>/rows/<int:row_id>/reconcile/", views.reconcile, name="reconcile"),
]
