"""Штатная регистрация Workwear без startup-записей в базу данных."""
from django.apps import AppConfig


class WorkwearConfig(AppConfig):
    """Конфигурация Workwear; миграции и permissions создаются штатными средствами Django."""
    name = 'workwear'
