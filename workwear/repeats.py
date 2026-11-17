"""Единый расчёт предупреждений по завершённым заявкам.

Сравниваются календарные даты в активном часовом поясе Django, точная позиция
и последнее нефутурное завершение; 365/730 дней определяются каталогом.
Предупреждение информирует пользователя и не запрещает повторную выдачу."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import RequestAssignment, RequestStatus, WorkwearItem


@dataclass(frozen=True)
class RepeatWarning:
    """Результат контроля: предыдущая заявка/дата, прошедшие, нормативные и оставшиеся дни."""
    previous_request_id: int
    previous_completion_date: date
    elapsed_days: int
    validity_days: int
    remaining_days: int


def scan_repeats(*, pairs, as_of_date=None, exclude_request_id=None):
    """Вернуть словарь (employee_pk, item_pk) → RepeatWarning для не более 1200 пар.

    Входные ID — положительные int; as_of_date по умолчанию timezone.localdate().
    Учитывается последнее нефутурное завершение; просроченное последнее назначение
    не заменяется более ранним. Неизвестный item или неверный вход — ValidationError.
    Записи в БД нет; API проверяет права и организацию до вызова."""
    reference = as_of_date if as_of_date is not None else timezone.localdate()
    if not isinstance(reference, date):
        raise ValidationError("Неверная дата проверки повторов")

    wanted = set(pairs)
    if not wanted:
        return {}
    if len(wanted) > 1200 or any(
        not isinstance(pair, tuple)
        or len(pair) != 2
        or any(type(pk) is not int or pk <= 0 for pk in pair)
        for pair in wanted
    ):
        raise ValidationError("Некорректные пары для проверки повторов")

    people = {employee for employee, _ in wanted}
    items = {item for _, item in wanted}
    validities = dict(
        WorkwearItem.objects.filter(pk__in=items)
        .values_list("pk", "validity_days")
    )
    if set(validities) != items:
        raise ValidationError("Неизвестная позиция спецодежды")

    history = (
        RequestAssignment.objects.filter(
            request_employee__employee_id__in=people,
            item_id__in=items,
            request_employee__request__status=RequestStatus.CLOSED,
        )
    )
    if exclude_request_id is not None:
        history = history.exclude(request_employee__request_id=exclude_request_id)
    # Учитывается весь календарный день, в том числе время позже текущего.
    # Будущие местные даты исключаются до DISTINCT ON, иначе будущая запись
    # может скрыть последнюю подходящую завершённую заявку.
    if reference < date.max:
        next_day = timezone.make_aware(
            datetime.combine(reference + timedelta(days=1), time.min),
            timezone.get_current_timezone(),
        )
        history = history.filter(request_employee__request__completed_at__lt=next_day)
    # PostgreSQL выбирает последнее назначение каждой пары. Получаем четыре
    # нужных значения вместо создания связанных объектов для всей истории.
    history = history.order_by(
        "request_employee__employee_id", "item_id",
        "-request_employee__request__completed_at", "-pk",
    ).distinct("request_employee__employee_id", "item_id").values_list(
        "request_employee__employee_id", "item_id",
        "request_employee__request_id", "request_employee__request__completed_at",
    )
    warnings = {}
    for employee_id, item_id, request_id, completed_at in history.iterator(chunk_size=1000):
        key = (employee_id, item_id)
        if key not in wanted:
            continue
        if completed_at is None:
            continue
        completion_date = timezone.localtime(completed_at).date()
        elapsed = (reference - completion_date).days
        if elapsed < 0:
            continue
        validity = validities[item_id]
        if elapsed < validity:
            warnings[key] = RepeatWarning(
                previous_request_id=request_id,
                previous_completion_date=completion_date,
                elapsed_days=elapsed,
                validity_days=validity,
                remaining_days=validity - elapsed,
            )
    return warnings
