"""Новое включение требует последней принятой выгрузки и совпадения организации. Старые участники сохраняются при обновлении справочника."""
from django.core.exceptions import ValidationError

from .models import EmployeeImport


def latest_import():
    return EmployeeImport.objects.order_by("-accepted_at", "-pk").first()


def is_current(employee, *, organization_id, latest):
    """Сверяем сотрудника с уже выбранной выгрузкой, не перечитывая справочник."""
    return (
        latest is not None
        and employee.organization_id == organization_id
        and employee.last_seen_import_id == latest.pk
    )


def require_current(employee, *, organization_id, latest=None):
    # Сервис передаёт данные из своей транзакции.
    # Отсутствие выгрузки не требует повторного чтения БД.
    """Вызывающий сервис удерживает блокировку импорта на время проверки."""
    if latest is None:
        raise ValidationError("Нет успешно принятой выгрузки сотрудников 1С")
    if employee.organization_id != organization_id:
        raise ValidationError("Организация сотрудника не совпадает с организацией заявки")
    if employee.last_seen_import_id != latest.pk:
        raise ValidationError("Сотрудник отсутствует в последней принятой выгрузке 1С")
