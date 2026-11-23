"""Проверяем роль и базу для подключения VEQTA."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import CommandError
from django.db import connection


def database_role(environment, *, migration=False):
    suffix = "_migrator" if migration else ""
    return f"svc_platform_{environment}{suffix}"


def require_runtime_settings():
    if settings.VEQTA_MIGRATION:
        raise ImproperlyConfigured("HTTP-сервер требует прикладную роль PostgreSQL")


def verify_connection(database, name, role):
    with database.cursor() as cursor:
        cursor.execute("""
            SELECT current_database(), session_user, current_user,
                   NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
            FROM pg_roles WHERE rolname = session_user
        """)
        if cursor.fetchone() != (name, role, role, True):
            raise CommandError("Подключение PostgreSQL использует другую БД или роль с недопустимыми правами")


def check_connection(sender, connection, **kwargs):
    role = database_role(settings.VEQTA_ENV, migration=settings.VEQTA_MIGRATION)
    try:
        verify_connection(connection, connection.settings_dict["NAME"], role)
    except CommandError:
        connection.close()
        raise


def require_target(environment, database_name, *, migration=False):
    """Проверяем конфигурацию и сеанс PostgreSQL перед служебной операцией."""
    if environment not in {"dev", "prod"} or environment != settings.VEQTA_ENV:
        raise CommandError("Контур команды не совпадает с VEQTA_ENV")
    expected = f"veqta_{environment}"
    if database_name != expected or connection.vendor != "postgresql":
        raise CommandError("Неверная целевая база данных")
    role = database_role(environment, migration=migration)
    if connection.settings_dict["NAME"] != expected or connection.settings_dict["USER"] != role:
        raise CommandError("Имя базы или роль подключения не соответствует контуру")
    verify_connection(connection, expected, role)
