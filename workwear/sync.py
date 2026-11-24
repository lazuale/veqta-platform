"""Сохраняем весь состав одной транзакцией. Снимки создаёт сервер; ошибка откатывает все изменения."""
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
    request_id: int
    version: int
    changed: bool
    completion: dict | None


def _validate(members):
    if not isinstance(members, list) or len(members) > MAX_REQUEST_MEMBERS:
        raise ValidationError("Некорректный состав заявки")
    all_item_ids, desired = set(), {}
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
        all_item_ids.update(item_ids)
    return desired, all_item_ids


@transaction.atomic
def save_draft(*, request_id, actor, expected_version, members, finish=False):
    """Сверяем версию под блокировкой. Завершение заявки и история входят в ту же транзакцию."""
    services._authorize(actor, "change_request")
    if type(finish) is not bool:
        raise ValidationError("Некорректный признак завершения")
    desired, item_ids = _validate(members)
    req = services._lock_request(request_id, expected_version, status=RequestStatus.DRAFT, membership_lock=True)
    items = dict(WorkwearItem.objects.filter(pk__in=item_ids).values_list("pk", "is_active"))
    if set(items) != item_ids:
        raise ValidationError("Неизвестная позиция спецодежды")
    existing = list(RequestEmployee.objects.filter(request_id=req.pk).prefetch_related("assignments"))
    existing_by_employee = {m.employee_id: m for m in existing}
    additions = sorted(desired.keys() - existing_by_employee.keys())
    employees = {}
    if additions:
        employees = {
            employee.pk: employee
            for employee in Employee.objects.select_related("organization")
            .filter(pk__in=additions).order_by()
        }
        if set(employees) != set(additions):
            raise ValidationError("Некоторые сотрудники отсутствуют в справочнике")
        latest = latest_import()
        # Составные FK PostgreSQL защищают организацию; выгрузку проверяем для новых участников.
        for employee_id in additions:
            require_current(employees[employee_id], organization_id=req.organization_id, latest=latest)
    # До записи рассчитываем изменения и сохраняем данные исключаемых участников для истории.
    old_items = {m.employee_id: {a.item_id for a in m.assignments.all()} for m in existing}
    added_item_ids = {
        employee_id: requested - old_items.get(employee_id, set())
        for employee_id, requested in desired.items()
    }
    # Отключённые позиции можно сохранить в прежнем составе, но нельзя назначить заново.
    if any(not items[item_id] for added in added_item_ids.values() for item_id in added):
        raise ValidationError("Нельзя назначить неактивную позицию")
    removed_members = [
        existing_by_employee[employee_id]
        for employee_id in sorted(existing_by_employee.keys() - desired.keys())
    ]
    assignment_added = [
        {"employee_id": employee_id, "item_id": item_id}
        for employee_id in sorted(added_item_ids)
        for item_id in sorted(added_item_ids[employee_id])
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
        "snapshot_organization_name": employees[employee_id].organization.name,
    } for employee_id in additions]
    removed_people = [{
        "employee_id": m.employee_id,
        "full_name": m.snapshot_full_name,
        "personnel_number": m.snapshot_personnel_number,
        "snapshot_organization_name": m.snapshot_organization_name,
        "item_ids": sorted(old_items[m.employee_id]),
    } for m in removed_members]
    changed = False
    if removed_members:
        RequestEmployee.objects.filter(pk__in=[m.pk for m in removed_members]).delete()
        changed = True
    # Новых участников создаём пакетом; триггеры PostgreSQL срабатывают и для него.
    new_members = []
    for employee_id in desired:
        if employee_id not in existing_by_employee:
            e = employees[employee_id]
            new_members.append(RequestEmployee(
                request=req, employee=e, organization_id=req.organization_id,
            ))
    if new_members:
        RequestEmployee.objects.bulk_create(new_members, batch_size=WRITE_BATCH_SIZE)
        existing_by_employee.update((m.employee_id, m) for m in new_members)
        changed = True
    # Назначения меняем пакетно; ошибка откатывает весь состав.
    assignment_ids_to_remove = [a.pk for m in existing if m.employee_id in desired
        for a in m.assignments.all() if a.item_id not in desired[m.employee_id]]
    if assignment_ids_to_remove:
        RequestAssignment.objects.filter(pk__in=assignment_ids_to_remove).delete()
        changed = True
    to_create = [
        RequestAssignment(request_employee_id=existing_by_employee[employee_id].pk, item_id=item_id)
        for employee_id, added in added_item_ids.items()
        for item_id in sorted(added)
    ]
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
    completion = asdict(services._complete_locked_draft(req, actor)) if finish else None
    return SyncResult(req.pk, req.version, changed, completion)
