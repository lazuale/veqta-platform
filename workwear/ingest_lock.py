"""Импорт и изменение заявок используют общую блокировку PostgreSQL. Её берём до блокировки строк, чтобы избежать взаимной блокировки."""

from django.db import connection, transaction

# Для другого приложения нужен отдельный ключ блокировки.
_NAMESPACE = 0x56515754
_SNAPSHOT = 1


def _take(function):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("1C roster lock requires transaction.atomic()")
    if connection.vendor != "postgresql":
        raise RuntimeError("Workwear roster lock requires PostgreSQL")
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {function}(%s, %s)", [_NAMESPACE, _SNAPSHOT])


def lock_for_membership():
    """Удерживаем общую блокировку, пока заявка проверяется и изменяется."""
    _take("pg_advisory_xact_lock_shared")


def lock_for_acceptance():
    """Публикация ждёт завершения операций с заявками."""
    _take("pg_advisory_xact_lock")
