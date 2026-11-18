"""При запуске Portal не создаём записи в БД."""
from django.apps import AppConfig


class PortalConfig(AppConfig):
    name = 'portal'
