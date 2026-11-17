"""Штатная Django-страница Workwear: login и native view_request до шаблона."""
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render


@login_required
@permission_required("workwear.view_request", raise_exception=True)
def index(request):
    """Отобразить оболочку Workwear после login и workwear.view_request; БД не меняется."""
    return render(request, "workwear/index.html")
