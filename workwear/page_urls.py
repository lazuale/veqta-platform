"""Маршруты HTML-интерфейса Workwear с отдельным namespace workwear_page."""
from django.urls import path
from . import pages

app_name = "workwear_page"
urlpatterns = [path("", pages.index, name="index")]
