"""Проверяем адресат служебной операции до изменения данных."""
from django.conf import settings
from django.core.management.base import CommandError
from django.db import connection


def database_role_names(environment, *, owner=False):
    """Допустимые имена ролей выбранного контура."""
    if owner:
        return f"veqta_{environment}_owner", f"svc_platform_{environment}_migrator"
    return f"veqta_{environment}", f"svc_platform_{environment}"


def require_target(environment, database_name, *, owner=False):
    """Настройки и фактическое соединение PostgreSQL должны указывать на одну базу."""
    expected = f"veqta_{environment}"
    if environment not in {"dev", "prod"} or environment != settings.VEQTA_ENV:
        raise CommandError("Контур команды не совпадает с VEQTA_ENV")
    if database_name != expected or connection.vendor != "postgresql":
        raise CommandError("Неверная целевая база данных")
    configured_user = connection.settings_dict["USER"]
    if connection.settings_dict["NAME"] != expected or configured_user not in database_role_names(environment, owner=owner):
        raise CommandError("Имя базы или роль подключения не соответствует контуру")
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), current_user")
        if cursor.fetchone() != (expected, configured_user):
            raise CommandError("PostgreSQL подключила команду к другой БД или роли")
