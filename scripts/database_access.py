"""Проверяем права PostgreSQL из служебного окружения VEQTA."""
import argparse
import hashlib
import os
from pathlib import Path

import django
from django.apps import apps
from django.conf import settings
from django.core.management.base import CommandError
from django.db import connection, transaction

from config.database import database_role, require_target, revoke_relation_grants

VERIFY_SQL = Path(__file__).resolve().parents[1] / "deploy" / "postgresql" / "verify.sql"


def verify_database(environment, *, append_tables=(), triggers=(), foreign_keys=(),
                    constraints=(), functions=None):
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
            trigger_tables = sorted({table for table, *_ in triggers})
            cursor.execute("""
                SELECT c.relname, t.tgname, p.proname, t.tgtype, t.tgenabled,
                       t.tgisinternal, t.tgqual IS NOT NULL, t.tgnargs,
                       t.tgattr::text, t.tgconstraint <> 0, p.prosecdef
                FROM pg_trigger AS t
                JOIN pg_class AS c ON c.oid = t.tgrelid
                JOIN pg_proc AS p ON p.oid = t.tgfoid
                WHERE c.relnamespace = 'public'::regnamespace
                  AND c.relname = ANY(%s)
                  AND NOT t.tgisinternal
            """, [trigger_tables])
            actual = {
                (table, name): (table, name, function, event, enabled, internal,
                                has_condition, argument_count, attributes,
                                is_constraint, security_definer)
                for (table, name, function, event, enabled, internal, has_condition,
                     argument_count, attributes, is_constraint, security_definer) in cursor.fetchall()
            }
            expected_names = set()
            for table, name, function, event in triggers:
                key = (table, name)
                expected_names.add(key)
                expected = (table, name, function, event, "O", False, False, 0, "", False, False)
                found = actual.get(key)
                if found is None:
                    problems.append(f"нет обязательного триггера: public.{table}.{name}")
                elif found != expected:
                    problems.append(f"неверная конфигурация триггера: public.{table}.{name}")
            for table, name in sorted(set(actual) - expected_names):
                problems.append(f"неожиданный триггер: public.{table}.{name}")

        if functions:
            names = sorted(functions)
            cursor.execute("""
                SELECT p.proname, p.prosrc, l.lanname, p.prosecdef,
                       p.prorettype = 'trigger'::regtype
                FROM pg_proc AS p
                JOIN pg_language AS l ON l.oid = p.prolang
                WHERE p.pronamespace = 'public'::regnamespace
                  AND p.proname = ANY(%s)
            """, [names])
            actual = {name: (body, language, security_definer, returns_trigger)
                      for name, body, language, security_definer, returns_trigger in cursor.fetchall()}
            for name, expected_hash in functions.items():
                found = actual.get(name)
                if found is None:
                    problems.append(f"нет защитной функции: public.{name}")
                    continue
                body, language, security_definer, returns_trigger = found
                actual_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
                if (actual_hash != expected_hash or language != "plpgsql"
                        or security_definer or not returns_trigger):
                    problems.append(f"неверное определение защитной функции: public.{name}")

        if constraints:
            constraint_tables = sorted({table for table, *_ in constraints})
            constraint_names = sorted({name for _, name, *_ in constraints})
            cursor.execute("""
                SELECT c.relname, k.conname, k.contype, k.convalidated,
                       k.conenforced, pg_get_constraintdef(k.oid)
                FROM pg_constraint AS k
                JOIN pg_class AS c ON c.oid = k.conrelid
                WHERE c.relnamespace = 'public'::regnamespace
                  AND c.relname = ANY(%s)
                  AND k.conname = ANY(%s)
            """, [constraint_tables, constraint_names])
            actual = {
                (table, name): (kind, validated, enforced, definition)
                for table, name, kind, validated, enforced, definition in cursor.fetchall()
            }
            for table, name, kind, markers in constraints:
                found = actual.get((table, name))
                if found is None:
                    problems.append(f"нет обязательного ограничения: public.{table}.{name}")
                    continue
                actual_kind, validated, enforced, definition = found
                normalized = " ".join(definition.split()).casefold()
                missing = [marker for marker in markers if marker.casefold() not in normalized]
                if actual_kind != kind or not validated or not enforced or missing:
                    problems.append(f"неверное определение ограничения: public.{table}.{name}")

        if foreign_keys:
            cursor.execute("""
                SELECT c.relname, k.conname, pg_get_constraintdef(k.oid)
                FROM pg_constraint AS k JOIN pg_class AS c ON c.oid = k.conrelid
                WHERE c.relnamespace = 'public'::regnamespace
                  AND k.contype = 'f' AND k.convalidated AND k.conenforced
            """)
            for table, name, _ in sorted(set(foreign_keys) - set(cursor.fetchall())):
                problems.append(f"нет обязательного внешнего ключа в ожидаемом состоянии: public.{table}.{name}")

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
            if apps.is_installed("workwear"):
                from workwear.database_access import (
                    CONSTRAINTS, EVENT_TABLE, FOREIGN_KEYS, FUNCTIONS,
                    IMPORT_TABLE, TRIGGERS,
                )
                verify_database(
                    environment,
                    append_tables=(EVENT_TABLE, IMPORT_TABLE),
                    triggers=TRIGGERS,
                    foreign_keys=FOREIGN_KEYS,
                    constraints=CONSTRAINTS,
                    functions=FUNCTIONS,
                )
            else:
                verify_database(environment)
            print("PostgreSQL: роли, права, триггеры, функции и ограничения проверены")
    except CommandError as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
