"""Штатная регистрация приложения Portal без побочных эффектов ready()."""
from django.apps import AppConfig


class PortalConfig(AppConfig):
    """Конфигурация Portal; startup не пишет в БД и не создаёт роли."""
    name = 'portal'
