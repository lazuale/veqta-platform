"""Настройки DEV и PROD. Секреты поступают только из окружения.

Имя контура определяет точные имена базы и роли. Файлы .env читает оболочка.
Ошибка настройки останавливает запуск до подключения к базе.
"""
import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def required(name):
    """Получить обязательное значение, не раскрывая его в сообщении об ошибке."""
    value = os.environ.get(name, '').strip()
    if not value:
        raise ImproperlyConfigured(f'Не задана переменная {name}')
    return value


def boolean(name, default=False):
    """Разобрать флаг 0/1; опечатка не должна ослаблять защиту."""
    value = os.environ.get(name, '1' if default else '0')
    if value not in {'0', '1'}:
        raise ImproperlyConfigured(f'{name} должна иметь значение 0 или 1')
    return value == '1'


VEQTA_ENV = required('VEQTA_ENV')
if VEQTA_ENV not in {'dev', 'prod'}:
    raise ImproperlyConfigured('VEQTA_ENV должна быть dev или prod')
IS_PRODUCTION = VEQTA_ENV == 'prod'
EXPECTED_DATABASE = f'veqta_{VEQTA_ENV}'
if required('DB_NAME') != EXPECTED_DATABASE or required('DB_USER') != EXPECTED_DATABASE:
    raise ImproperlyConfigured('Контур, имя БД и роль подключения не совпадают')
SECRET_KEY = required('DJANGO_SECRET_KEY')
if len(SECRET_KEY) < 50 or SECRET_KEY.startswith('django-insecure-'):
    raise ImproperlyConfigured('DJANGO_SECRET_KEY должен содержать не менее 50 случайных символов')
DEBUG = boolean('DJANGO_DEBUG', default=not IS_PRODUCTION)
if IS_PRODUCTION and DEBUG:
    raise ImproperlyConfigured('DEBUG запрещён в PROD')
ALLOWED_HOSTS = [v.strip() for v in required('DJANGO_ALLOWED_HOSTS').split(',') if v.strip()]
if not ALLOWED_HOSTS or '*' in ALLOWED_HOSTS:
    raise ImproperlyConfigured('Нужен явный список DJANGO_ALLOWED_HOSTS без *')

INSTALLED_APPS = [
    'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
    'portal.apps.PortalConfig', 'workwear.apps.WorkwearConfig',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [], 'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
        'portal.context_processors.platform_navigation',
    ]},
}]
WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'
DATABASES = {'default': {
    'ENGINE': 'django.db.backends.postgresql', 'NAME': EXPECTED_DATABASE,
    'USER': required('DB_USER'), 'PASSWORD': required('DB_PASSWORD'),
    'HOST': required('DB_HOST'), 'PORT': required('DB_PORT'),
    'CONN_MAX_AGE': 60 if IS_PRODUCTION else 0, 'CONN_HEALTH_CHECKS': True,
    'OPTIONS': {'sslmode': os.environ.get('DB_SSLMODE', 'prefer')},
    'TEST': {'NAME': 'test_veqta_dev'},
}}
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
LANGUAGE_CODE = 'ru'
TIME_ZONE = 'Europe/Moscow'
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
STATIC_URL = '/static/'
STATIC_ROOT = Path(os.environ.get('DJANGO_STATIC_ROOT', str(BASE_DIR / 'staticfiles')))
# Почта не участвует в управлении внутренними учётными записями.
MAILERS = {}
# Отправка писем отключена намеренно; отсутствие почтового канала не ошибка запуска.
SILENCED_SYSTEM_CHECKS = ["mail.W001"]
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'
# Заголовок протокола допустим только при закрытом прямом доступе к Gunicorn.
TRUST_PROXY = boolean('DJANGO_TRUST_PROXY')
if IS_PRODUCTION and not TRUST_PROXY:
    raise ImproperlyConfigured('PROD требует доверенный прокси и DJANGO_TRUST_PROXY=1')
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https') if TRUST_PROXY else None
SECURE_COOKIES = boolean('DJANGO_SECURE_COOKIES', default=IS_PRODUCTION)
if IS_PRODUCTION and not SECURE_COOKIES:
    raise ImproperlyConfigured('PROD требует Secure cookies')
if SECURE_COOKIES and not TRUST_PROXY:
    raise ImproperlyConfigured('Secure cookies требуют доверенный HTTPS-прокси')
SESSION_COOKIE_SECURE = SECURE_COOKIES
CSRF_COOKIE_SECURE = SECURE_COOKIES
SECURE_SSL_REDIRECT = IS_PRODUCTION
SECURE_HSTS_SECONDS = 3600 if IS_PRODUCTION else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = IS_PRODUCTION
SECURE_HSTS_PRELOAD = IS_PRODUCTION
CSRF_TRUSTED_ORIGINS = [v.strip() for v in os.environ.get('DJANGO_CSRF_TRUSTED_ORIGINS', '').split(',') if v.strip()]
if SECURE_COOKIES and any(not v.startswith('https://') or '*' in v for v in CSRF_TRUSTED_ORIGINS):
    raise ImproperlyConfigured('PROD допускает только явные HTTPS-источники CSRF')
SESSION_COOKIE_NAME = f'veqta_{VEQTA_ENV}_session'
CSRF_COOKIE_NAME = f'veqta_{VEQTA_ENV}_csrf'
# HSTS относится ко всему хосту: DEV и PROD должны иметь разные имена хостов.
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
