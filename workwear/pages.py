"""Для просмотра Workwear нужны вход и право чтения заявок."""
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render


@login_required
@permission_required("workwear.view_request", raise_exception=True)
def index(request):
    return render(request, "workwear/index.html")
