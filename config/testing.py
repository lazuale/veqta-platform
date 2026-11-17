"""Настройки временного сервера для браузерных тестов; разрешены только из DEV."""
from .settings import *

if VEQTA_ENV != "dev":
    raise ImproperlyConfigured("Тестовый сервер разрешён только из DEV")
DATABASES["default"]["NAME"] = "test_veqta_dev"
# Только временный runserver на loopback: Django должен обслуживать staticfiles.
DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
# Браузерные тесты используют временный HTTP-сервер на localhost.
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
