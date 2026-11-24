"""Проверяем права PostgreSQL из служебного окружения VEQTA."""
import argparse
import os
from pathlib import Path

import django
from django.apps import apps
from django.conf import settings
from django.core.management.base import CommandError
from django.db import connection, transaction

from config.database import database_role, require_target, revoke_relation_grants

VERIFY_SQL = Path(__file__).resolve().parents[1] / "deploy" / "postgresql" / "verify.sql"


def verify_database(environment, *, append_tables=(), triggers=()):
    other_environment = "prod" if environment == "dev" else "dev"
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cursor.execute("SET LOCAL search_path = pg_catalog")
        cursor.execute("SET LOCAL statement_timeout = '30s'")
        cursor.execute(VERIFY_SQL.read_text(encoding="utf-8"), {
            "runtime": database_role(environment),
            "other_runtime": database_role(other_environment),
            "other_database": f"veqta_{other_environment}",
            "append_tables": list(append_tables),
        })
        problems = [problem for problem, in cursor.fetchall()]
        if triggers:
            cursor.execute("""
                SELECT c.relname, t.tgname, p.proname, t.tgtype
                FROM pg_trigger AS t JOIN pg_class AS c ON c.oid = t.tgrelid
                JOIN pg_proc AS p ON p.oid = t.tgfoid
                WHERE c.relnamespace = 'public'::regnamespace AND p.pronamespace = c.relnamespace
                    AND p.prorettype = 'trigger'::regtype AND NOT p.prosecdef
                    AND t.tgenabled = 'O' AND NOT t.tgisinternal AND t.tgqual IS NULL
                    AND t.tgnargs = 0 AND t.tgattr::text = '' AND t.tgconstraint = 0
            """)
            for table, trigger, *_ in sorted(set(triggers) - set(cursor.fetchall())):
                problems.append(f"нет обязательного триггера в ожидаемом состоянии: public.{table}.{trigger}")
    if problems:
        raise CommandError("Отклонения в PostgreSQL:\n" + "\n".join(problems))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protect-migrations", action="store_true",
                        help="Отозвать посторонние права журнала миграций и его последовательности")
    args = parser.parse_args()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.migration")
    django.setup()
    try:
        environment = settings.VEQTA_ENV
        require_target(environment, settings.EXPECTED_DATABASE, migration=True)
        if args.protect_migrations:
            with transaction.atomic(), connection.cursor() as cursor:
                revoke_relation_grants(cursor, "django_migrations")
                revoke_relation_grants(cursor, "django_migrations_id_seq", sequence=True)
            print("PostgreSQL: права журнала миграций применены")
        else:
            # Здесь собирается состав проверки установленных приложений.
            if apps.is_installed("workwear"):
                from workwear.database_access import EVENT_TABLE, TRIGGERS
                verify_database(environment, append_tables=(EVENT_TABLE,), triggers=TRIGGERS)
            else:
                verify_database(environment)
            print("PostgreSQL: версия, роли, права и триггеры проверены")
    except CommandError as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
