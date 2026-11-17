"""Атомарная публикация справочников Employees/Positions/Units.

Каждый GUID в Employees считается присутствующим в принятой выгрузке.
Отсутствующие GUID не удаляются из истории. Подтверждённая дата и полнота
приходят извне; дата приёма не выдаётся за дату источника. Смена организации
одного GUID отвергается существующим контрактом составных внешних ключей."""
from dataclasses import dataclass
from datetime import datetime
import hashlib
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .directories_1c import ParsedBatch
from ..ingest_lock import lock_for_acceptance, lock_for_membership
from ..models import Employee, EmployeeImport

@dataclass(frozen=True)
class AcceptanceResult:
    """Агрегированный результат публикации с import_id и признаком идемпотентного приёма."""
    import_id: int
    created: int
    updated: int
    current: int
    historical_preserved: int
    already_accepted: bool


@dataclass(frozen=True)
class PublicationReview:
    """План сверки: предыдущий import, количество и digest выпавших GUID.

    missing_current_guids содержит кадровые идентификаторы для уполномоченного
    оператора; это не обезличенный внешний отчёт."""
    previous_import_id: int
    employee_count: int
    missing_current_count: int
    missing_current_sha256: str
    missing_current_guids: tuple[str, ...]


def _review(batch, previous):
    missing = tuple(sorted(str(value) for value in Employee.objects.filter(
        last_seen_import_id=previous.pk,
    ).exclude(employee_guid__in={p.guid for p in batch.people}).values_list("employee_guid", flat=True))) if previous else ()
    return PublicationReview(
        previous.pk if previous else 0, len(batch.people), len(missing),
        hashlib.sha256("\n".join(missing).encode("ascii")).hexdigest(), missing,
    )


@transaction.atomic
def review(batch: ParsedBatch) -> PublicationReview:
    """Прочитать план публикации под shared advisory lock в atomic-транзакции.

    batch — ParsedBatch; возвращает PublicationReview, БД не изменяется.
    План стабилен только на время вызова: accept заново проверяет previous id."""
    lock_for_membership()
    previous = EmployeeImport.objects.order_by('-accepted_at', '-pk').first()
    return _review(batch, previous)


def _chunks(items, size=500):
    for i in range(0, len(items), size):
        yield items[i:i+size]


@transaction.atomic
def accept(batch: ParsedBatch, *, source_exported_at: datetime | None = None,
           expected_employee_count=None, expected_previous_import_id=None,
           confirmed_missing_sha256=None, acknowledge_undated_previous=False) -> AcceptanceResult:
    """Опубликовать ParsedBatch атомарно под exclusive advisory lock.

    Нужны достоверная source_exported_at с зоной, точный employee_count,
    expected_previous_import_id (0 для первого), подтверждение digest выпадений
    и явное acknowledge для прежней недатированной выгрузки при необходимости.
    Возвращает AcceptanceResult; ValidationError/IntegrityError отменяют всё.
    Повтор последней тройки хешей с той же датой идемпотентен. Snapshot-поля
    заявок не изменяются; отсутствующие GUID остаются в историческом справочнике."""
    if source_exported_at is not None and (not isinstance(source_exported_at, datetime) or timezone.is_naive(source_exported_at)):
        raise ValidationError("Дата выгрузки должна содержать часовой пояс")
    if len(batch.people) < 1 or batch.current_count != len(batch.people):
        raise ValidationError("Неверное количество актуальных сотрудников")
    lock_for_acceptance()
    signatures = {
        'employees_sha256': batch.sha256['Employees.csv'],
        'positions_sha256': batch.sha256['Positions.csv'],
        'units_sha256': batch.sha256['Units.csv'],
    }
    previous = EmployeeImport.objects.order_by('-accepted_at', '-pk').first()
    existing = EmployeeImport.objects.filter(**signatures).first()
    if existing is not None:
        if existing.pk != getattr(previous, 'pk', None):
            raise ValidationError("Старая выгрузка уже принималась")
        if existing.source_exported_at != source_exported_at:
            raise ValidationError("Дата повторного принятия не совпадает с исходной")
        return AcceptanceResult(existing.pk, 0, 0, len(batch.people),
                                Employee.objects.exclude(last_seen_import_id=existing.pk).count(), True)
    if source_exported_at is None:
        raise ValidationError("Для публикации требуется проверенная дата выгрузки с часовым поясом")
    if source_exported_at > timezone.now():
        raise ValidationError("Дата выгрузки не может находиться в будущем")
    if type(expected_employee_count) is not int or expected_employee_count != len(batch.people):
        raise ValidationError("Не подтверждено точное количество сотрудников подготовленного пакета")
    if type(expected_previous_import_id) is not int or expected_previous_import_id != getattr(previous, 'pk', 0):
        raise ValidationError("Изменился принятый справочник. Повторите DRY_RUN и проверку публикации")
    if type(acknowledge_undated_previous) is not bool:
        raise ValidationError("Некорректное подтверждение предыдущей недатированной выгрузки")
    if previous and previous.source_exported_at is None and not acknowledge_undated_previous:
        raise ValidationError("У предыдущей выгрузки нет даты источника; требуется явное подтверждение перехода")
    checked = _review(batch, previous)
    if checked.missing_current_count and confirmed_missing_sha256 != checked.missing_current_sha256:
        raise ValidationError("Сотрудники исчезают из текущего справочника. Проверьте DRY_RUN и подтвердите точный SHA-256 списка GUID")
    if (previous and previous.source_exported_at and source_exported_at and
            source_exported_at <= previous.source_exported_at):
        raise ValidationError("Дата выгрузки не новее последней принятой")
    by_guid = {p.guid: p for p in batch.people}
    if len(by_guid) != len(batch.people):
        raise ValidationError("Повтор GUID в подготовленном наборе")
    stored = {e.employee_guid: e for e in
              Employee.objects.select_for_update().filter(employee_guid__in=by_guid)}
    for uid, person in by_guid.items():
        org = person.organization_name
        # Справочник 1С может содержать другие юрлица; только Request ограничен
        # тремя организациями. Сохранение справочника не разрешает
        # включение сотрудников чужой организации.
        if not isinstance(org, str) or not org.strip() or len(org) > 255 or org.strip().upper() == "NULL":
            raise ValidationError(f"Недопустимая организация сотрудника {uid}: {org!r}")
        if uid in stored and stored[uid].organization_name != person.organization_name:
            raise ValidationError(f"Смена организации без нового GUID: {uid}")
    snapshot = EmployeeImport.objects.create(
        **signatures, source_row_count=batch.source_row_count,
        employee_count=len(by_guid), source_exported_at=source_exported_at,
        coverage_start=None, coverage_end=None,
    )
    create, update = [], []
    for uid, p in by_guid.items():
        if uid not in stored:
            create.append(Employee(
                employee_guid=uid, personnel_number=p.personnel_number,
                full_name=p.full_name, organization_name=p.organization_name,
                position_name=p.position_name, department_name=p.department_name,
                last_seen_import=snapshot,
            ))
        else:
            employee = stored[uid]
            employee.personnel_number = p.personnel_number
            employee.full_name = p.full_name
            employee.position_name = p.position_name
            employee.department_name = p.department_name
            employee.last_seen_import = snapshot
            update.append(employee)
    for chunk in _chunks(create):
        Employee.objects.bulk_create(chunk, batch_size=500)
    for chunk in _chunks(update):
        Employee.objects.bulk_update(chunk,
            ['personnel_number', 'full_name', 'position_name',
             'department_name', 'last_seen_import'], batch_size=500)
    count = Employee.objects.filter(last_seen_import=snapshot).count()
    if count != len(batch.people):
        raise ValidationError("Количество сотрудников после импорта не совпало")
    historical = Employee.objects.exclude(last_seen_import=snapshot).count()
    return AcceptanceResult(snapshot.pk, len(create), len(update), count, historical, False)
