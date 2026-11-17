"""Штатная точка входа WSGI для Gunicorn; настройки выбираются через окружение."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_wsgi_application()
