"""Единое правило нового включения сотрудника в заявку.

Нужны последняя принятая выгрузка и точное совпадение организации. Старые
участники сохраняют историю; повторное новое включение проверяется заново.
Сервисы берут общий advisory lock с публикацией 1С; прямой сторонний writer
обязан соблюдать тот же протокол, что остаётся отдельным gate интеграции."""
from django.core.exceptions import ValidationError

from .models import EmployeeImport


def latest_import():
    """Вернуть последний EmployeeImport по accepted_at, pk или None; только чтение."""
    return EmployeeImport.objects.order_by("-accepted_at", "-pk").first()


def is_current(employee, *, organization_name, latest):
    """Проверить точную организацию и last_seen_import относительно переданного latest.

    Чистый предикат; отсутствие последней выгрузки означает False."""
    return (
        latest is not None
        and employee.organization_name == organization_name
        and employee.last_seen_import_id == latest.pk
    )


def require_current(employee, *, organization_name, latest=None):
    # Вызывающий сервис явно передаёт snapshot из своей транзакции.
    # None означает отсутствие принятой выгрузки, а не команду перечитать БД.
    """Проверить допуск нового включения к переданной организации и latest.

    Отсутствие выгрузки, другая организация или старый snapshot — ValidationError.
    Вызывающий сервис удерживает shared advisory lock; здесь записи нет."""
    if latest is None:
        raise ValidationError("Нет успешно принятой выгрузки сотрудников 1С")
    if employee.organization_name != organization_name:
        raise ValidationError("Организация сотрудника не совпадает с организацией заявки")
    if employee.last_seen_import_id != latest.pk:
        raise ValidationError("Сотрудник отсутствует в последней принятой выгрузке 1С")
