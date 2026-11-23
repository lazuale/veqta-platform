"""Браузерные тесты запускаем только с настройками DEV."""
from .settings import *

if VEQTA_ENV != "dev":
    raise ImproperlyConfigured("Тестовый сервер разрешён только из DEV")
DATABASES["default"]["NAME"] = "test_veqta_dev"
DEBUG = False
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
MIDDLEWARE = ["tests.browser_support.QueryCountMiddleware", *MIDDLEWARE]
