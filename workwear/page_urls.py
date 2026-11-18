from django.urls import path
from . import pages

app_name = "workwear_page"
urlpatterns = [path("", pages.index, name="index")]
