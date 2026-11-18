"""Проверяем адресат служебной операции до изменения данных."""
from django.conf import settings
from django.core.management.base import CommandError
from django.db import connection


def require_target(environment, database_name, *, owner=False):
    """Настройки и фактическое соединение PostgreSQL должны указывать на одну базу."""
    expected = f"veqta_{environment}"
    expected_user = f"{expected}_owner" if owner else expected
    if environment not in {"dev", "prod"} or environment != settings.VEQTA_ENV:
        raise CommandError("Контур команды не совпадает с VEQTA_ENV")
    if database_name != expected or connection.vendor != "postgresql":
        raise CommandError("Неверная целевая база данных")
    if connection.settings_dict["NAME"] != expected or connection.settings_dict["USER"] != expected_user:
        raise CommandError("Имя базы или роль подключения не соответствует контуру")
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), current_user")
        if cursor.fetchone() != (expected, expected_user):
            raise CommandError("PostgreSQL подключила команду к другой БД или роли")
