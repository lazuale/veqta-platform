"""Атомарная публикация справочников Employees/Positions/Units.

Каждый GUID в Employees считается присутствующим в принятой выгрузке.
Отсутствующие GUID не удаляются из истории. Подтверждённая дата и полнота
приходят извне; дата приёма не выдаётся за дату источника. Смена организации
одного GUID отвергается существующим контрактом составных внешних ключей."""
from dataclasses import dataclass
from datetime import date, datetime, time
import hashlib
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from .directories_1c import ParsedBatch
from ..ingest_lock import lock_for_acceptance, lock_for_membership
from ..models import Employee, EmployeeImport, Organization

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
def accept(batch: ParsedBatch, *, source_export_date: date,
           expected_employee_count=None, expected_previous_import_id=None,
           confirmed_missing_sha256=None) -> AcceptanceResult:
    """Опубликовать ParsedBatch атомарно под exclusive advisory lock.

    Нужна подтверждённая календарная source_export_date, точный employee_count,
    expected_previous_import_id (0 для первого), подтверждение digest выпадений.
    Возвращает AcceptanceResult; ValidationError/IntegrityError отменяют всё.
    Повтор последней тройки хешей с той же датой идемпотентен;
    изменённая выгрузка того же календарного дня допустима. Snapshot-поля
    заявок не изменяются; отсутствующие GUID остаются в историческом справочнике."""
    if type(source_export_date) is not date:
        raise ValidationError("Укажите только дату выгрузки YYYY-MM-DD без времени")
    if not batch.people:
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
        if existing.source_export_date != source_export_date:
            raise ValidationError("Дата повторного принятия не совпадает с исходной")
        return AcceptanceResult(existing.pk, 0, 0, len(batch.people),
                                Employee.objects.exclude(last_seen_import_id=existing.pk).count(), True)
    if source_export_date > timezone.localdate(timezone=timezone.get_default_timezone()):
        raise ValidationError("Дата выгрузки не может находиться в будущем")
    if type(expected_employee_count) is not int or expected_employee_count != len(batch.people):
        raise ValidationError("Не подтверждено точное количество сотрудников подготовленного пакета")
    if type(expected_previous_import_id) is not int or expected_previous_import_id != getattr(previous, 'pk', 0):
        raise ValidationError("Изменился принятый справочник. Повторите DRY_RUN и проверку публикации")
    checked = _review(batch, previous)
    if checked.missing_current_count and confirmed_missing_sha256 != checked.missing_current_sha256:
        raise ValidationError("Сотрудники исчезают из текущего справочника. Проверьте DRY_RUN и подтвердите точный SHA-256 списка GUID")
    if previous and source_export_date < previous.source_export_date:
        raise ValidationError("Дата выгрузки раньше последней принятой")
    by_guid = {p.guid: p for p in batch.people}
    if len(by_guid) != len(batch.people):
        raise ValidationError("Повтор GUID в подготовленном наборе")
    stored = {e.employee_guid: e for e in
              Employee.objects.select_for_update().filter(employee_guid__in=by_guid)}
    organizations = {}
    for external_id, source_name in sorted(batch.organizations.items()):
        organization = Organization.objects.select_for_update().filter(external_id=external_id).first()
        if organization is None:
            # Имя не доказывает идентичность. Ручную запись сначала связывает администратор.
            if Organization.objects.filter(external_id__isnull=True).filter(
                    models.Q(name=source_name) | models.Q(source_name=source_name)).exists():
                raise ValidationError(
                    f"Укажите orgExtId {external_id} у существующей организации через Django Admin")
            organization = Organization.objects.create(
                external_id=external_id, name=source_name, source_name=source_name,
            )
        elif organization.source_name != source_name:
            organization.source_name = source_name
            organization.save(update_fields=["source_name"])
        organizations[external_id] = organization
    for uid, person in by_guid.items():
        if person.organization_external_id not in organizations:
            raise ValidationError("Отсутствует организация подготовленного сотрудника")
        if uid in stored and stored[uid].organization_id != organizations[person.organization_external_id].pk:
            raise ValidationError(f"Смена организации без нового GUID: {uid}")
    snapshot = EmployeeImport.objects.create(
        **signatures, source_row_count=batch.source_row_count,
        employee_count=len(by_guid),
        # Существующая колонка timestamp кодирует календарный день в зоне проекта.
        # 00:00 не является утверждением о фактическом времени выгрузки.
        source_exported_at=datetime.combine(
            source_export_date, time.min, tzinfo=timezone.get_default_timezone()
        ),
    )
    create, update = [], []
    for uid, p in by_guid.items():
        if uid not in stored:
            create.append(Employee(
                employee_guid=uid, personnel_number=p.personnel_number,
                full_name=p.full_name,
                organization=organizations[p.organization_external_id],
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
