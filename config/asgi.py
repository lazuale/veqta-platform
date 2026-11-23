"""Точка входа ASGI. Рабочее развёртывание использует WSGI."""

import os

from django.core.asgi import get_asgi_application

from .database import require_runtime_settings

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

require_runtime_settings()
application = get_asgi_application()
