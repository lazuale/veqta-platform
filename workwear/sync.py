"""Атомарная синхронизация полного локального черновика Workwear.

Вход members задаёт желаемые employee_id/item_ids, а не готовые snapshot-поля.
Версия проверяется под блокировкой; сервер строит delta, сохраняет изменения
и аудит в одной транзакции. При finish завершение входит в ту же транзакцию.
Ошибка любого шага отменяет весь набор изменений."""
from dataclasses import asdict, dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from .limits import MAX_REQUEST_MEMBERS, MAX_ITEMS_PER_MEMBER, WRITE_BATCH_SIZE
from . import services
from .eligibility import latest_import, require_current
from .models import (
    Employee, RequestEmployee, RequestAssignment,
    RequestStatus, WorkwearItem,
)


@dataclass(frozen=True)
class SyncResult:
    """Результат полной синхронизации: id, подтверждённая version, changed и completion."""
    request_id: int
    version: int
    changed: bool
    completion: dict | None


def _validate(members):
    if not isinstance(members, list) or len(members) > MAX_REQUEST_MEMBERS:
        raise ValidationError("Некорректный состав заявки")
    employee_ids, all_item_ids, desired = [], set(), {}
    for entry in members:
        if not isinstance(entry, dict) or set(entry) != {"employee_id", "item_ids"}:
            raise ValidationError("Некорректная запись сотрудника")
        employee_id, item_ids = entry["employee_id"], entry["item_ids"]
        if type(employee_id) is not int or employee_id <= 0 or employee_id in desired:
            raise ValidationError("Дубликат или неверный идентификатор сотрудника")
        if not isinstance(item_ids, list) or len(item_ids) > MAX_ITEMS_PER_MEMBER or any(
            type(x) is not int or x <= 0 for x in item_ids
        ) or len(item_ids) != len(set(item_ids)):
            raise ValidationError("Некорректный список позиций")
        desired[employee_id] = set(item_ids)
        employee_ids.append(employee_id)
        all_item_ids.update(item_ids)
    return desired, employee_ids, all_item_ids


@transaction.atomic
def save_draft(*, request_id, actor, expected_version, members, finish=False):
    """Сохранить желаемые members и, при finish=True, завершить ту же заявку.

    actor: change_request; expected_version проверяется под блокировкой заявки.
    members — до 1000 уникальных сотрудников, у каждого до 30 уникальных позиций.
    Новые включения проверяются по последней выгрузке, снимки формирует сервер.
    Возвращает SyncResult. ValidationError/PermissionDenied/WorkwearConflict/ошибка
    БД откатывают delta, исключения пустых участников, завершение и аудит целиком."""
    services._authorize(actor, "change_request")
    if type(finish) is not bool:
        raise ValidationError("Некорректный признак завершения")
    desired, employee_ids, item_ids = _validate(members)
    req = services._lock_request(request_id, expected_version, status=RequestStatus.DRAFT)
    employees = {e.pk: e for e in Employee.objects.filter(pk__in=employee_ids)}
    if set(employees) != set(employee_ids):
        raise ValidationError("Некоторые сотрудники отсутствуют в справочнике")
    items = dict(WorkwearItem.objects.filter(pk__in=item_ids).values_list("pk", "is_active"))
    if set(items) != item_ids:
        raise ValidationError("Неизвестная позиция спецодежды")
    existing = list(RequestEmployee.objects.filter(request_id=req.pk).prefetch_related("assignments"))
    existing_by_employee = {m.employee_id: m for m in existing}
    latest = latest_import()
    for employee_id in desired:
        employee = employees[employee_id]
        # Исторический состав остаётся доступен; новое включение проходит
        # ту же проверку, что services.add_employee.
        if employee_id not in existing_by_employee:
            require_current(employee, organization_id=req.organization_id, latest=latest)
        elif employee.organization_id != req.organization_id:
            # Смена организации одного GUID требует отдельного решения
            # о составных FK; текущий контракт её не разрешает.
            raise ValidationError("Организация сотрудника не совпадает с заявкой")
    # Полная детерминированная delta вычисляется до записи. Снимки
    # исключаемых участников сохраняются в событии до удаления их строк.
    old_items = {m.employee_id: {a.item_id for a in m.assignments.all()} for m in existing}
    # Отключённые позиции можно сохранить в прежнем составе, но нельзя назначить заново.
    if any(not items[item_id] for employee_id in desired
           for item_id in desired[employee_id] - old_items.get(employee_id, set())):
        raise ValidationError("Нельзя назначить неактивную позицию")
    old_people = {m.employee_id: {
        "employee_id": m.employee_id,
        "full_name": m.snapshot_full_name,
        "personnel_number": m.snapshot_personnel_number,
        "organization_name": m.snapshot_organization_name,
        "item_ids": sorted(old_items[m.employee_id]),
    } for m in existing}
    additions = sorted(set(desired) - set(old_people))
    removals = sorted(set(old_people) - set(desired))
    assignment_added = [
        {"employee_id": employee_id, "item_id": item_id}
        for employee_id in sorted(desired)
        for item_id in sorted(desired[employee_id] - old_items.get(employee_id, set()))
    ]
    assignment_removed = [
        {"employee_id": employee_id, "item_id": item_id}
        for employee_id in sorted(old_items)
        for item_id in sorted(old_items[employee_id] - desired.get(employee_id, set()))
    ]
    added_people = [{
        "employee_id": employee_id,
        "full_name": employees[employee_id].full_name,
        "personnel_number": employees[employee_id].personnel_number,
        "organization_name": employees[employee_id].organization_name,
    } for employee_id in additions]
    removed_people = [old_people[employee_id] for employee_id in removals]
    changed = False
    removing = [m.pk for m in existing if m.employee_id not in desired]
    if removing:
        RequestEmployee.objects.filter(pk__in=removing).delete()
        changed = True
    # Снимки и ID новых участников создаются одним bulk-этапом. Все INSERT
    # продолжают исполнять обычные PostgreSQL triggers в той же транзакции.
    new_members = []
    for employee_id in desired:
        if employee_id not in existing_by_employee:
            e = employees[employee_id]
            new_members.append(RequestEmployee(
                request=req, employee=e, organization_id=req.organization_id, organization_name=req.organization_name,
                snapshot_full_name=e.full_name, snapshot_personnel_number=e.personnel_number,
                snapshot_organization_name=e.organization_name,
                snapshot_position_name=e.position_name, snapshot_department_name=e.department_name,
            ))
    if new_members:
        RequestEmployee.objects.bulk_create(new_members, batch_size=WRITE_BATCH_SIZE)
        existing_by_employee.update((m.employee_id, m) for m in new_members)
        changed = True
    # Удаление позиций и создание назначений пакетные по всей заявке, а не
    # отдельный SQL на каждого сотрудника. Любая ошибка отменяет весь delta.
    assignment_ids_to_remove = [a.pk for m in existing if m.employee_id in desired
        for a in m.assignments.all() if a.item_id not in desired[m.employee_id]]
    if assignment_ids_to_remove:
        RequestAssignment.objects.filter(pk__in=assignment_ids_to_remove).delete()
        changed = True
    to_create = [RequestAssignment(request_employee_id=existing_by_employee[e].pk, item_id=i)
                 for e, selected_items in desired.items()
                 for i in sorted(selected_items - old_items.get(e, set()))]
    if to_create:
        RequestAssignment.objects.bulk_create(to_create, batch_size=WRITE_BATCH_SIZE)
        changed = True
    if changed:
        services._bump_draft_version(req)
        services._audit(
            req.pk, actor, "draft_synced", members=len(desired),
            assignments=sum(map(len, desired.values())),
            changes={
                "members_added": added_people,
                "members_removed": removed_people,
                "assignments_added": assignment_added,
                "assignments_removed": assignment_removed,
            },
        )
    completion = None
    if finish:
        # Нормализация, исключение пустых, завершение и аудит — одна транзакция.
        completion = asdict(services.complete_request(
            request_id=req.pk, actor=actor, expected_version=req.version,
        ))
        req.version = completion["version"]
    return SyncResult(req.pk, req.version, changed, completion)
