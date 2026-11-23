"""Штатная точка входа WSGI для Gunicorn; настройки выбираются через окружение."""

import os

from django.core.wsgi import get_wsgi_application

from .database import require_runtime_settings

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

require_runtime_settings()
application = get_wsgi_application()
