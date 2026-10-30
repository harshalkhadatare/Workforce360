from django.urls import path

from . import views

app_name = "queries"
urlpatterns = [
    path("", views.query_list, name="list"),
    path("new/", views.create, name="create"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/attachment/", views.attachment, name="attachment"),
    path("<int:pk>/attachment/<int:comment_id>/", views.attachment, name="comment_attachment"),
]
