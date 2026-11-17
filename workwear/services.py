"""Транзакционные операции Workwear.

Публичные операции принимают actor и ожидаемую версию заявки. Проверка прав,
блокировка, предметное изменение и аудит выполняются в одной atomic-транзакции.
Сначала берётся advisory lock справочника, затем SELECT FOR UPDATE заявки.
PostgreSQL независимо защищает связи, жизненный цикл и неизменяемую историю.
Исключения ValidationError, PermissionDenied, WorkwearConflict и ошибки БД
не превращаются здесь в HTTP: транспортное отображение находится в api.py."""
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, F
from django.utils import timezone

from .eligibility import latest_import, require_current
from .limits import MAX_REQUEST_MEMBERS, MAX_ITEMS_PER_MEMBER, WRITE_BATCH_SIZE
from .ingest_lock import lock_for_membership
# RepeatWarning доступен вызывающему коду через модуль services.
from .repeats import RepeatWarning as RepeatWarning, scan_repeats

from .models import (
    Employee,
    Organization,
    Request,
    RequestAssignment,
    RequestEmployee,
    RequestStatus,
    WorkwearEvent,
    WorkwearItem,
)


class WorkwearConflict(Exception):
    """Конфликт версии. HTTP-слой преобразует его в 409; запись отменяется."""


@dataclass(frozen=True)
class CompletionResult:
    """Неизменяемый результат завершения: request_id, version и количества участников/назначений."""
    request_id: int
    version: int
    excluded_members: int
    final_members: int
    final_assignments: int


def _authorize(actor, permission):
    if (
        actor is None
        or not getattr(actor, "is_authenticated", False)
        or not getattr(actor, "is_active", False)
        or not actor.has_perm(f"workwear.{permission}")
    ):
        raise PermissionDenied("Недостаточно прав для операции со спецодеждой")


def _audit_entry(request_id, actor, event_type, **details):
    return WorkwearEvent(
        request_number=request_id,
        actor=actor,
        actor_username=actor.get_username(),
        event_type=event_type,
        details=details,
    )


def _audit(request_id, actor, event_type, **details):
    event = _audit_entry(request_id, actor, event_type, **details)
    event.save(force_insert=True)
    return event


def _lock_request(request_id, expected_version, *, status):
    # Общий lock с публикацией 1С берётся до блокировки строки заявки.
    # Тот же порядок действует для sync.save_draft через этот сервис.
    lock_for_membership()
    req = Request.objects.select_for_update().get(pk=request_id)
    if type(expected_version) is not int or expected_version < 1:
        raise ValidationError("Требуется текущая версия заявки")
    if req.version != expected_version:
        raise WorkwearConflict(
            f"Заявка изменена: ожидалась версия {expected_version}, "
            f"текущая версия {req.version}"
        )
    if req.status != status:
        raise ValidationError("Недопустимая операция для текущего статуса заявки")
    return req


def _bump_draft_version(req, step=1):
    Request.objects.filter(pk=req.pk).update(version=F("version") + step)
    req.version += step


@transaction.atomic
def create_request(*, organization_name, actor):
    """Создать черновик допустимой организации и событие created в одной транзакции.

    actor должен иметь add_request; возвращается Request с исходной версией 1.
    Недопустимая организация вызывает ValidationError; чужие права — PermissionDenied."""
    _authorize(actor, "add_request")
    if organization_name not in Organization.values:
        raise ValidationError("Недопустимая организация")
    req = Request.objects.create(
        organization_name=organization_name, created_by=actor,
    )
    _audit(req.pk, actor, "created", organization_name=organization_name)
    return req


@transaction.atomic
def add_employees(*, request_id, employee_ids, actor, expected_version):
    """Добавить 1–1000 уникальных employee_ids и их исторические snapshots.

    actor: change_request; request_id/expected_version проверяются под блокировкой.
    Сотрудники должны входить в последнюю выгрузку нужной организации.
    Возвращает (список RequestEmployee, новая version); версия растёт на число включений.
    Любая ошибка отменяет участников, версию и соответствующие события целиком."""
    _authorize(actor, "change_request")
    if (not isinstance(employee_ids, list) or not 1 <= len(employee_ids) <= MAX_REQUEST_MEMBERS
            or any(type(value) is not int or value <= 0 for value in employee_ids)
            or len(set(employee_ids)) != len(employee_ids)):
        raise ValidationError("Требуется от 1 до 1000 уникальных идентификаторов сотрудников")
    req = _lock_request(request_id, expected_version, status=RequestStatus.DRAFT)
    people = Employee.objects.in_bulk(employee_ids)
    if len(people) != len(employee_ids):
        raise Employee.DoesNotExist("Сотрудник отсутствует в справочнике")
    latest = latest_import()
    for employee_id in employee_ids:
        require_current(people[employee_id], organization_name=req.organization_name, latest=latest)
    existing_ids = set(RequestEmployee.objects.filter(request_id=req.pk).values_list("employee_id", flat=True))
    if existing_ids.intersection(employee_ids):
        raise ValidationError("Сотрудник уже включён в заявку")
    if len(existing_ids) + len(employee_ids) > MAX_REQUEST_MEMBERS:
        raise ValidationError("В одной заявке допускается до1000 сотрудников")
    members = RequestEmployee.objects.bulk_create([RequestEmployee(
        request=req,
        employee=people[employee_id],
        organization_name=req.organization_name,
        snapshot_full_name=people[employee_id].full_name,
        snapshot_personnel_number=people[employee_id].personnel_number,
        snapshot_organization_name=people[employee_id].organization_name,
        snapshot_position_name=people[employee_id].position_name,
        snapshot_department_name=people[employee_id].department_name,
    ) for employee_id in employee_ids], batch_size=WRITE_BATCH_SIZE)
    _bump_draft_version(req, len(members))
    WorkwearEvent.objects.bulk_create([
        _audit_entry(req.pk, actor, "member_added", member_id=member.pk, employee_id=member.employee_id)
        for member in members
    ], batch_size=WRITE_BATCH_SIZE)
    return members, req.version


def add_employee(*, request_id, employee_id, actor, expected_version):
    """Добавить одного сотрудника через add_employees с теми же правами и блокировкой.

    Возвращает RequestEmployee; snapshot берётся сервером, а не из HTTP-полей."""
    members, _ = add_employees(request_id=request_id, employee_ids=[employee_id],
        actor=actor, expected_version=expected_version)
    return members[0]


@transaction.atomic
def remove_employee(*, request_id, member_id, actor, expected_version):
    """Исключить member_id из черновика, удалить назначения CASCADE и записать аудит.

    Требуются change_request и ожидаемая версия; возвращает новое число version.
    Ошибка проверки/БД отменяет все изменения атомарно."""
    _authorize(actor, "change_request")
    req = _lock_request(request_id, expected_version, status=RequestStatus.DRAFT)
    member = RequestEmployee.objects.get(pk=member_id, request_id=req.pk)
    # Назначения удаляются Django CASCADE в той же транзакции.
    employee_id = member.employee_id
    member.delete()
    _bump_draft_version(req)
    _audit(req.pk, actor, "member_removed", member_id=member_id, employee_id=employee_id)
    return req.version


@transaction.atomic
def add_assignment(*, request_id, member_id, item_id, actor, expected_version):
    """Назначить активный item_id участнику черновика с проверкой версии и аудитом.

    actor: change_request; возвращает RequestAssignment. Повтор той же позиции
    и неверное состояние отвергаются; предупреждение о прошлой выдаче не запрет."""
    _authorize(actor, "change_request")
    req = _lock_request(request_id, expected_version, status=RequestStatus.DRAFT)
    member = RequestEmployee.objects.get(pk=member_id, request_id=req.pk)
    item = WorkwearItem.objects.get(pk=item_id)
    if not item.is_active:
        raise ValidationError("Позиция спецодежды неактивна")
    if RequestAssignment.objects.filter(request_employee_id=member.pk, item_id=item_id).exists():
        raise ValidationError("Позиция уже назначена сотруднику")
    if member.assignments.count() >= MAX_ITEMS_PER_MEMBER:
        raise ValidationError("У сотрудника уже назначено максимальное число позиций")
    assignment = RequestAssignment.objects.create(request_employee=member, item=item)
    _bump_draft_version(req)
    _audit(
        req.pk, actor, "assignment_added",
        member_id=member.pk, assignment_id=assignment.pk, item_id=item_id,
    )
    return assignment


@transaction.atomic
def remove_assignment(*, request_id, assignment_id, actor, expected_version):
    """Удалить assignment_id черновика, повысить версию и сохранить событие атомарно.

    Требуются change_request, совпадение заявки и expected_version."""
    _authorize(actor, "change_request")
    req = _lock_request(request_id, expected_version, status=RequestStatus.DRAFT)
    assignment = RequestAssignment.objects.get(
        pk=assignment_id, request_employee__request_id=req.pk,
    )
    member_id, item_id = assignment.request_employee_id, assignment.item_id
    assignment.delete()
    _bump_draft_version(req)
    _audit(
        req.pk, actor, "assignment_removed",
        member_id=member_id, assignment_id=assignment_id, item_id=item_id,
    )
    return req.version


@transaction.atomic
def complete_request(*, request_id, actor, expected_version):
    """Завершить черновик и вернуть CompletionResult в одной транзакции.

    Требуются change_request и expected_version. Сначала исключаются участники
    без назначений; если не осталось ни одного, все удаления откатываются.
    Время и автор задаются сервером; SQL guard проверяет полноту независимо."""
    _authorize(actor, "change_request")
    req = _lock_request(request_id, expected_version, status=RequestStatus.DRAFT)

    # Пустые участники исключаются до смены статуса: SQL guard отвергает
    # неполное завершение и запрещает удаление участников закрытой заявки.
    empty_ids = list(
        RequestEmployee.objects.filter(request_id=req.pk)
        .annotate(assigned_count=Count("assignments"))
        .filter(assigned_count=0)
        .values_list("pk", flat=True)
    )
    excluded_people = list(RequestEmployee.objects.filter(pk__in=empty_ids)
        .values("id", "employee_id", "snapshot_full_name", "snapshot_personnel_number"))
    if empty_ids:
        RequestEmployee.objects.filter(pk__in=empty_ids).delete()

    final_members = RequestEmployee.objects.filter(request_id=req.pk).count()
    final_assignments = RequestAssignment.objects.filter(
        request_employee__request_id=req.pk,
    ).count()
    if not final_members or not final_assignments:
        # Исключение откатывает и ранее выполненные удаления.
        raise ValidationError("Нельзя завершить заявку без сотрудников с назначениями")

    req.status = RequestStatus.CLOSED
    req.version += 1
    req.completed_at = timezone.now()
    req.completed_by = actor
    req.save(update_fields=["status", "version", "completed_at", "completed_by"])
    _audit(
        req.pk, actor, "completed", excluded_members=len(empty_ids),
        final_members=final_members, final_assignments=final_assignments,
        excluded_people=excluded_people,
    )
    return CompletionResult(
        request_id=req.pk, version=req.version,
        excluded_members=len(empty_ids), final_members=final_members,
        final_assignments=final_assignments,
    )


@transaction.atomic
def reopen_request(*, request_id, actor, expected_version, reason=""):
    # Возврат требует native workwear.reopen_request либо superuser.
    # HTTP-представление отдельно проверяет активную сессию.
    """Вернуть закрытую заявку в draft с обнулением полей завершения и аудитом.

    actor: native reopen_request (либо superuser); reason — строка до 500 символов.
    Проверка версии под блокировкой предотвращает конкурентный возврат."""
    _authorize(actor, "reopen_request")
    if not isinstance(reason, str) or len(reason) > 500:
        raise ValidationError("Причина возврата должна содержать не более 500 символов")
    req = _lock_request(request_id, expected_version, status=RequestStatus.CLOSED)
    old_completed_at = req.completed_at.isoformat() if req.completed_at else None
    old_completed_by_id = req.completed_by_id
    req.status = RequestStatus.DRAFT
    req.version += 1
    req.completed_at = None
    req.completed_by = None
    req.save(update_fields=["status", "version", "completed_at", "completed_by"])
    _audit(
        req.pk, actor, "reopened", previous_completed_at=old_completed_at,
        previous_completed_by_id=old_completed_by_id, reason=reason.strip(),
    )
    return req


@transaction.atomic
def delete_draft(*, request_id, actor, expected_version):
    """Удалить только черновик с delete_request и expected_version.

    Аудит draft_deleted записывается до удаления и сохраняется без FK к заявке.
    Удаление состава/назначений и событие входят в одну atomic-транзакцию."""
    _authorize(actor, "delete_request")
    req = _lock_request(request_id, expected_version, status=RequestStatus.DRAFT)
    # Событие сохраняется после удаления: у WorkwearEvent нет FK на Request.
    _audit(req.pk, actor, "draft_deleted", version=req.version)
    req.delete()


def repeat_warnings_for_pairs(*, pairs, as_of_date=None, exclude_request_id=None):
    """Рассчитать пакет предупреждений через repeats.scan_repeats.

    pairs: пары employee/item, as_of_date: календарная дата или localdate,
    exclude_request_id исключает текущую заявку. Нет записи и разрешения на выдачу."""
    return scan_repeats(
        pairs=pairs,
        as_of_date=as_of_date,
        exclude_request_id=exclude_request_id,
    )


def repeat_warnings_for(*, employee_id, item_id, as_of_date=None, exclude_request_id=None):
    """Совместимый одиночный расчёт повторов без решения о допустимости выдачи.

    Возвращает список из 0–1 предупреждения. Неизвестная позиция даёт
    DoesNotExist для прежнего HTTP 404; пакетный маршрут сохраняет свою 400."""
    # Сохраняем прежний одиночный 404 для неизвестной позиции.
    # Пакетный маршрут отдельно отвергает неверные пары через ValidationError.
    WorkwearItem.objects.get(pk=item_id)
    warning = repeat_warnings_for_pairs(
        pairs={(employee_id, item_id)},
        as_of_date=as_of_date,
        exclude_request_id=exclude_request_id,
    ).get((employee_id, item_id))
    return [warning] if warning else []
