"""Синхронизация принятой выгрузки 1С и изменений состава.

Один ключ advisory xact lock: изменение состава берёт shared, публикация
exclusive. Блокировки удерживаются до конца транзакции и предшествуют
блокировкам строк, чтобы исключить инверсию порядка захвата."""

from django.db import connection, transaction

# Ключ принадлежит Workwear; другое приложение должно выбрать свой namespace.
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
    """Взять shared advisory xact lock до SELECT FOR UPDATE заявки.

    Требуется atomic и PostgreSQL, иначе RuntimeError. Освобождение — в конце
    транзакции; ожидание зависит от политики lock_timeout соединения."""
    _take("pg_advisory_xact_lock_shared")


def lock_for_acceptance():
    """Взять exclusive advisory xact lock публикации до блокировок Employee.

    Требуется atomic и PostgreSQL; сериализует публикации и новые включения."""
    _take("pg_advisory_xact_lock")
