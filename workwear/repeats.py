"""Предупреждаем о повторной выдаче, но не запрещаем её."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

from .limits import MAX_REPEAT_PAIRS
from .models import RequestAssignment, RequestStatus, WorkwearItem


@dataclass(frozen=True)
class RepeatWarning:
    previous_request_id: int
    previous_completion_date: date
    elapsed_days: int
    validity_days: int
    remaining_days: int


def scan_repeats(*, pairs, as_of_date=None, exclude_request_id=None):
    """Исключаем будущие даты, чтобы они не скрыли последнюю завершённую выдачу."""
    reference = as_of_date if as_of_date is not None else timezone.localdate()
    if isinstance(reference, datetime) or not isinstance(reference, date):
        raise ValidationError("Неверная дата проверки повторов")

    wanted = set(pairs)
    if not wanted:
        return {}
    if len(wanted) > MAX_REPEAT_PAIRS or any(
        not isinstance(pair, tuple)
        or len(pair) != 2
        or any(type(pk) is not int or pk <= 0 for pk in pair)
        for pair in wanted
    ):
        raise ValidationError("Некорректные пары для проверки повторов")

    people = {employee for employee, _ in wanted}
    items = {item for _, item in wanted}
    known_items = set(WorkwearItem.objects.filter(pk__in=items).values_list("pk", flat=True))
    if known_items != items:
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
    # Исключаем будущие даты до DISTINCT ON, иначе они могут скрыть последнюю выдачу.
    if reference < date.max:
        next_day = timezone.make_aware(
            datetime.combine(reference + timedelta(days=1), time.min),
            timezone.get_current_timezone(),
        )
        history = history.filter(request_employee__request__completed_at__lt=next_day)
    # DISTINCT ON оставляет последнюю выдачу для каждой пары.
    history = history.order_by(
        "request_employee__employee_id", "item_id",
        "-request_employee__request__completed_at", "-pk",
    ).distinct("request_employee__employee_id", "item_id").values_list(
        "request_employee__employee_id", "item_id",
        "request_employee__request_id", "request_employee__request__completed_at", "snapshot_validity_days",
    )
    warnings = {}
    for employee_id, item_id, request_id, completed_at, validity in history.iterator(chunk_size=1000):
        key = (employee_id, item_id)
        if key not in wanted:
            continue
        completion_date = timezone.localtime(completed_at).date()
        elapsed = (reference - completion_date).days
        if elapsed < validity:
            warnings[key] = RepeatWarning(
                previous_request_id=request_id,
                previous_completion_date=completion_date,
                elapsed_days=elapsed,
                validity_days=validity,
                remaining_days=validity - elapsed,
            )
    return warnings
