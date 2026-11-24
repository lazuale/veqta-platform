"""Проверяем роль и базу для подключения VEQTA."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import CommandError
from django.db import connection
from psycopg import sql


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
                   AND current_setting('session_replication_role') = 'origin'
            FROM pg_roles WHERE rolname = session_user
        """)
        if cursor.fetchone() != (name, role, role, True):
            raise CommandError("Подключение PostgreSQL использует неверную БД, роль или режим триггеров")


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


def revoke_relation_grants(cursor, name, *, sequence=False):
    """Отзываем явные права всех получателей, кроме владельца объекта."""
    cursor.execute("""
        SELECT relowner = current_user::regrole AND current_user = session_user,
               ARRAY(SELECT DISTINCT roles.rolname FROM (
                   SELECT grantee FROM aclexplode(relacl)
                   UNION
                   SELECT grant_entry.grantee FROM pg_attribute AS attribute
                   CROSS JOIN LATERAL aclexplode(attribute.attacl) AS grant_entry
                   WHERE attrelid = relation.oid AND attnum > 0 AND NOT attisdropped
               ) AS grants LEFT JOIN pg_roles AS roles ON roles.oid = grants.grantee
               WHERE grants.grantee <> relowner)
        FROM pg_class AS relation
        WHERE relnamespace = 'public'::regnamespace AND relname = %s AND relkind = %s
    """, [name, "S" if sequence else "r"])
    result = cursor.fetchone()
    if not result or not result[0]:
        raise CommandError(f"Объект public.{name} отсутствует или не принадлежит роли подключения")
    if result[1]:
        # REVOKE на таблице также отзывает права её столбцов. CASCADE снимает выданные получателями права.
        grantees = [sql.Identifier(role) if role is not None else sql.SQL("PUBLIC") for role in result[1]]
        cursor.execute(sql.SQL("REVOKE ALL ON {} {} FROM {} CASCADE").format(
            sql.SQL("SEQUENCE" if sequence else "TABLE"), sql.Identifier("public", name),
            sql.SQL(", ").join(grantees),
        ))
