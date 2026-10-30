from django.urls import path

from . import views

app_name = "accounts"
urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("password/change/", views.password_change, name="password_change"),
    path("password/reset/", views.PasswordResetView.as_view(), name="password_reset"),
    path("password/reset/sent/", views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("password/reset/<uidb64>/<token>/", views.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("password/reset/complete/", views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
]
