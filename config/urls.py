"""Маршруты Django Admin, Portal и API Workwear."""
from django.contrib import admin
from django.urls import include, path


urlpatterns = [
    path("admin/", admin.site.urls),
    path("workwear/api/", include("workwear.urls")),
    path("workwear/", include("workwear.page_urls")),
    path("", include("portal.urls")),
]
