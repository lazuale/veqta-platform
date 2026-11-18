"""Браузерные тесты запускаем только с настройками DEV."""
from .settings import *

if VEQTA_ENV != "dev":
    raise ImproperlyConfigured("Тестовый сервер разрешён только из DEV")
DATABASES["default"]["NAME"] = "test_veqta_dev"
# runserver раздаёт статику через DEBUG; по HTTPS её отдаёт Nginx.
_https = boolean("VEQTA_TEST_HTTPS")
DEBUG = not _https
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
SESSION_COOKIE_SECURE = _https
CSRF_COOKIE_SECURE = _https
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https') if _https else None
SECURE_SSL_REDIRECT = _https
SECURE_HSTS_SECONDS = 3600 if _https else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = _https
SECURE_HSTS_PRELOAD = _https
MIDDLEWARE = ["tests.browser_support.QueryCountMiddleware", *MIDDLEWARE]
