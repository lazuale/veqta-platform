"""Домашняя страница платформы. Авторизация выполняется штатным login_required."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def home(request):
    """Отобразить портал вошедшему пользователю; ссылки фильтрует context processor."""
    return render(request, "portal/home.html", {"portal_is_home": request.path == "/"})


@login_required
def profile(request):
    """Просмотр своего профиля; изменения доступа выполняет штатный Django Admin."""
    return render(request, "portal/profile.html", {
        "profile_groups": request.user.groups.order_by("name"),
    })
