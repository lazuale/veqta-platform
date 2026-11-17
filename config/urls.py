"""Корневые маршруты монолита: native Admin, API/страницы Workwear и Portal."""
from django.contrib import admin
from django.urls import include, path


urlpatterns = [
    path("admin/", admin.site.urls),
    path("workwear/api/", include("workwear.urls")),
    path("workwear/", include("workwear.page_urls")),
    path("", include("portal.urls")),
]
