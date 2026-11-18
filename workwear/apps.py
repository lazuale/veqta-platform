"""При запуске Workwear не создаём записи; миграции и права ведёт Django."""
from django.apps import AppConfig


class WorkwearConfig(AppConfig):
    name = 'workwear'
