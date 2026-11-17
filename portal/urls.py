"""Маршруты портала и штатных auth views Django; logout использует POST/CSRF."""
from django.contrib.auth.views import LoginView, LogoutView, PasswordChangeView, PasswordChangeDoneView
from django.urls import path

from .views import home, profile


urlpatterns = [
    path("", home, name="home"),
    path("apps/", home, name="applications"),
    path("profile/", profile, name="profile"),
    path(
        "login/",
        LoginView.as_view(template_name="portal/login.html"),
        name="login",
    ),
    path(
        "logout/",
        LogoutView.as_view(next_page="login"),
        name="logout",
    ),
    path("password/change/", PasswordChangeView.as_view(template_name="portal/password_change.html"), name="password_change"),
    path("password/change/done/", PasswordChangeDoneView.as_view(template_name="portal/password_change_done.html"), name="password_change_done"),
]
