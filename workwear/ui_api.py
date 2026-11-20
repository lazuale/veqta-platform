"""API интерфейса использует общие сервисы для записи и предупреждений. Читать заявки могут аналитики всех организаций."""
from .eligibility import latest_import
from .repeats import scan_repeats
from .employee_lookup import employee_data, resolve_many
from datetime import date

from django.core.exceptions import ValidationError
from django.db.models import Case, CharField, Count, F, Func, IntegerField, Q, Value, When
from django.utils import timezone

from .limits import MAX_REQUEST_MEMBERS, MAX_ITEMS_PER_MEMBER, MAX_REPEAT_PAIRS, MAX_JSON_BYTES, MAX_SYNC_JSON_BYTES
from . import services
from .api import endpoint, payload, positive_int, require_permission, respond, version, query_organization_id
from .models import (
    Employee, Organization, Request, RequestEmployee,
    RequestStatus, WorkwearEvent, WorkwearItem,
)


def _query_text(request, key, *, limit=120):
    value = request.GET.get(key, "").strip()
    if len(value) > limit:
        raise ValidationError(f"Слишком длинный параметр {key}")
    return value


def _query_num(request, key, *, default, maximum):
    raw = request.GET.get(key, str(default))
    if not raw.isascii() or not raw.isdigit():
        raise ValidationError(f"Некорректный параметр {key}")
    digits = raw.lstrip("0") or "0"
    if len(digits) > len(str(maximum)):
        raise ValidationError(f"Параметр {key} вне допустимого диапазона")
    number = int(digits)
    if number > maximum:
        raise ValidationError(f"Параметр {key} вне допустимого диапазона")
    return number


@endpoint("GET")
def metadata(request):
    """Дата приёма выгрузки не подтверждает период исходных данных."""
    require_permission(request, "view_request")
    latest = latest_import()
    items = []
    if request.user.has_perm("workwear.view_workwearitem"):
        items = [
            {"id": item.pk, "code": item.code, "name": item.name,
             "category": item.category, "validity_days": item.validity_days, "is_active": item.is_active}
            for item in WorkwearItem.objects.all().order_by("sort_order", "pk")
        ]
    return respond({
        "organizations": [{"value": org.pk, "label": org.name}
                          for org in Organization.objects.filter(requests_enabled=True)],
        "registry_organizations": [{"value": org.pk, "label": org.name}
                                   for org in Organization.objects.all()],
        "items": items,
        "import": {
            "accepted_at": latest.accepted_at.isoformat() if latest else None,
            "employee_count": latest.employee_count if latest else None,
            # Дата приёма не подтверждает период выгрузки.
            "source_export_date": latest.source_export_date.isoformat() if latest else None,
        },
        "limits": {"request_members": MAX_REQUEST_MEMBERS, "items_per_member": MAX_ITEMS_PER_MEMBER,
                   "repeat_pairs": MAX_REPEAT_PAIRS, "json_bytes": MAX_JSON_BYTES, "sync_bytes": MAX_SYNC_JSON_BYTES},
        "permissions": {
            key: request.user.has_perm("workwear." + key)
            for key in (
                "view_request", "add_request", "change_request", "delete_request",
                "reopen_request", "view_employee", "view_workwearitem",
                "view_workwearevent",
            )
        },
    })


@endpoint("GET")
def registry(request):
    """Поиск по сохранённым данным заявок не зависит от текущего справочника."""
    require_permission(request, "view_request")
    org = _query_text(request, "organization_id", limit=19)
    status = _query_text(request, "status", limit=12)
    q = _query_text(request, "q", limit=120)
    if org:
        org = query_organization_id(org)
    if status and status not in RequestStatus.values:
        raise ValidationError("Недопустимый статус")
    offset = _query_num(request, "offset", default=0, maximum=1_000_000)
    limit = _query_num(request, "limit", default=30, maximum=100)
    if limit == 0:
        raise ValidationError("Лимит должен быть положительным")
    date_basis = _query_text(request, "date_basis", limit=16) or "created"
    date_from = _query_text(request, "date_from", limit=10)
    date_to = _query_text(request, "date_to", limit=10)
    sort_order = _query_text(request, "sort", limit=8) or "newest"
    if date_basis not in ("created", "completed") or sort_order not in ("newest", "oldest"):
        raise ValidationError("Недопустимые параметры сортировки или даты")
    def parse_date(raw):
        if not raw:
            return None
        try:
            if len(raw) != 10 or raw[4] != "-" or raw[7] != "-":
                raise ValueError
            return date.fromisoformat(raw)
        except ValueError:
            raise ValidationError("Дата должна иметь формат YYYY-MM-DD")
    since, until = parse_date(date_from), parse_date(date_to)
    if since and until and since > until:
        raise ValidationError("Начало периода позже окончания")
    qs = Request.objects.all()
    field = "created_at" if date_basis == "created" else "completed_at"
    if since:
        qs = qs.filter(**{f"{field}__date__gte": since})
    if until:
        qs = qs.filter(**{f"{field}__date__lte": until})
    if org:
        qs = qs.filter(organization_id=org)
    if status:
        qs = qs.filter(status=status)
    if q:
        query = Q(snapshot_organization_name__icontains=q) | Q(members__snapshot_full_name__icontains=q) | Q(
            members__snapshot_personnel_number__icontains=q
        )
        if q.isascii() and q.isdecimal() and len(q) <= 19:
            numeric_id = int(q)
            if numeric_id <= 9223372036854775807:
                query |= Q(pk=numeric_id)
        qs = qs.filter(query).distinct()
    count = qs.count()
    requests = list(qs.order_by("-created_at" if sort_order == "newest" else "created_at", "-pk" if sort_order == "newest" else "pk")[offset:offset + limit])
    ids = [r.pk for r in requests]
    member_counts = {
        row["request_id"]: row["total"]
        for row in RequestEmployee.objects.filter(request_id__in=ids)
        .values("request_id").annotate(total=Count("pk"))
    }
    return respond({
        "count": count,
        "offset": offset,
        "limit": limit,
        "results": [
            {"id": r.pk, "organization_id": r.organization_id, "snapshot_organization_name": r.snapshot_organization_name,
             "status": r.status, "version": r.version,
             "created_at": r.created_at.isoformat(),
             "completed_at": r.completed_at.isoformat() if r.completed_at else None,
             "members_count": member_counts.get(r.pk, 0)}
            for r in requests
        ],
    })


@endpoint("GET")
def employees(request):
    """Ранжируем результаты в SQL до применения LIMIT/OFFSET."""
    require_permission(request, "view_employee")
    org = _query_text(request, "organization_id", limit=19)
    org = query_organization_id(org)
    q = _query_text(request, "q", limit=120)
    offset = _query_num(request, "offset", default=0, maximum=1_000_000)
    limit = _query_num(request, "limit", default=40, maximum=40)
    if limit == 0:
        raise ValidationError("Лимит должен быть положительным")
    latest = latest_import()
    if len(q) < 2 or latest is None:
        return respond({"results": [], "min_query_length": 2,
                        "count": 0, "offset": offset, "limit": limit, "has_more": False,
                        "import_available": latest is not None})
    # Точное совпадение фамилии должно быть выше совпадения в отчестве.
    q = " ".join(q.split())
    surname = Func(F("full_name"), Value(" "), Value(1),
                   function="split_part", output_field=CharField())
    given_name = Func(F("full_name"), Value(" "), Value(2),
                      function="split_part", output_field=CharField())
    patronymic = Func(F("full_name"), Value(" "), Value(3),
                      function="split_part", output_field=CharField())
    qs = (Employee.objects.select_related("organization")
          .filter(organization_id=org, last_seen_import_id=latest.pk)
          .filter(Q(full_name__icontains=q) | Q(personnel_number__icontains=q))
          .annotate(_sr_surname=surname, _sr_given=given_name,
                    _sr_patronymic=patronymic)
          .annotate(_sr_rank=Case(
              When(personnel_number__iexact=q, then=Value(0)),
              When(_sr_surname__iexact=q, then=Value(0)),
              When(full_name__iexact=q, then=Value(0)),
              When(_sr_surname__istartswith=q, then=Value(1)),
              When(personnel_number__istartswith=q, then=Value(1)),
              When(full_name__istartswith=q, then=Value(1)),
              When(_sr_given__iexact=q, then=Value(2)),
              When(_sr_given__istartswith=q, then=Value(3)),
              When(_sr_patronymic__iexact=q, then=Value(4)),
              When(_sr_patronymic__istartswith=q, then=Value(5)),
              default=Value(6), output_field=IntegerField(),
          ))
          .order_by("_sr_rank", "full_name", "pk"))
    count = qs.count()
    result = list(qs[offset:offset + limit])
    return respond({"results": [employee_data(e, latest.pk) for e in result],
                    "count": count, "offset": offset, "limit": limit,
                    "has_more": offset + len(result) < count,
                    "import_available": True})


@endpoint("POST")
def resolve(request):
    """Поиск ничего не записывает. Новое включение повторно проверяет сервис."""
    require_permission(request, "view_employee")
    obj = payload(request, "organization_id", "lines",
                  optional_fields=("request_id", "present_employee_ids"))
    org, lines = positive_int(obj["organization_id"], "organization_id"), obj["lines"]
    if not Organization.objects.filter(pk=org).exists():
        raise ValidationError("Недопустимая организация")
    if not isinstance(lines, list) or not 1 <= len(lines) <= 100:
        raise ValidationError("Список должен содержать от 1 до 100 строк")
    if any(not isinstance(line, str) or len(line) > 255 or not line.strip() for line in lines):
        raise ValidationError("Каждая строка должна содержать ФИО или табельный номер")
    if "request_id" in obj:
        require_permission(request, "view_request")
    latest = latest_import()
    already = set()
    if "request_id" in obj:
        request_id = positive_int(obj["request_id"], "request_id")
        parent = Request.objects.get(pk=request_id)
        if parent.organization_id != org:
            raise ValidationError("Организация заявки не совпадает с запросом")
        already.update(RequestEmployee.objects.filter(request_id=request_id).values_list("employee_id", flat=True))
    staged = obj.get("present_employee_ids", [])
    if not isinstance(staged, list) or len(staged) > 1000 or any(type(x) is not int or x <= 0 for x in staged):
        raise ValidationError("Недопустимый список сотрудников черновика")
    already.update(staged)
    return respond({"import_id": latest.pk if latest else None, "results": resolve_many(
        lines=lines,
        organization_id=org,
        latest=latest,
        already=already,
    )})


@endpoint("POST")
def bulk_add(request, request_id):
    obj = payload(request, "employee_ids", "expected_version")
    ids = obj["employee_ids"]
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_REQUEST_MEMBERS:
        raise ValidationError("Требуется от 1 до 1000 сотрудников")
    if any(type(value) is not int or value <= 0 for value in ids):
        raise ValidationError("Некорректные идентификаторы сотрудников")
    if len(ids) != len(set(ids)):
        raise ValidationError("Повторяющиеся сотрудники в списке")
    members, next_version = services.add_employees(
        request_id=request_id, employee_ids=ids, expected_version=version(obj), actor=request.user,
    )
    added = [{"member_id": member.pk, "employee_id": member.employee_id} for member in members]
    return respond({"added": added, "version": next_version}, 201)


@endpoint("GET")
def events(request, request_id):
    """События удалённого черновика остаются доступны в истории."""
    require_permission(request, "view_request")
    require_permission(request, "view_workwearevent")
    offset = _query_num(request, "offset", default=0, maximum=1_000_000)
    limit = _query_num(request, "limit", default=100, maximum=100)
    if limit == 0:
        raise ValidationError("Лимит должен быть положительным")
    qs = WorkwearEvent.objects.filter(request_number=request_id)
    if not Request.objects.filter(pk=request_id).exists() and not qs.exists():
        from django.core.exceptions import ObjectDoesNotExist
        raise ObjectDoesNotExist("Неизвестная заявка")
    count = qs.count()
    rows = qs.order_by("-occurred_at", "-pk")[offset:offset + limit]
    return respond({"results": [
        {"id": r.pk, "event_type": r.event_type,
         "occurred_at": r.occurred_at.isoformat(),
         "actor_username": r.actor_username, "details": r.details}
        for r in rows
    ], "count": count, "offset": offset, "limit": limit,
       "has_more": offset + len(rows) < count})


@endpoint("GET")
def employee_history(request, request_id, employee_id):
    """Историю сотрудника можно посмотреть до сохранения его в заявке."""
    require_permission(request, "view_request")
    require_permission(request, "view_workwearevent")
    # История доступна до сохранения сотрудника; организацию проверяет сервер.
    req = Request.objects.get(pk=request_id)
    Employee.objects.get(pk=employee_id, organization_id=req.organization_id)
    rows = list(
        RequestEmployee.objects.filter(
            employee_id=employee_id,
            request__status=RequestStatus.CLOSED,
        ).exclude(request_id=request_id)
        .select_related("request")
        .prefetch_related("assignments")
        .order_by("-request__completed_at", "-request_id")[:100]
    )
    return respond({"results": [{
        "request_id": m.request_id,
        "completed_at": m.request.completed_at.isoformat(),
        "snapshot_organization_name": m.snapshot_organization_name,
        "full_name": m.snapshot_full_name,
        "personnel_number": m.snapshot_personnel_number,
        "department_name": m.snapshot_department_name,
        "items": [{
            "id": a.item_id, "name": a.snapshot_item_name,
            "category": a.snapshot_item_category, "code": a.snapshot_item_code,
            "validity_days": a.snapshot_validity_days,
        } for a in m.assignments.all()],
    } for m in rows], "limit": 100})


@endpoint("POST")
def sync_request(request, request_id):
    from .sync import save_draft
    obj = payload(request, "members", "expected_version", "finish", max_bytes=MAX_SYNC_JSON_BYTES)
    result = save_draft(
        request_id=request_id, actor=request.user,
        expected_version=version(obj), members=obj["members"],
        finish=obj["finish"],
    )
    return respond({"request_id": result.request_id, "version": result.version,
                    "changed": result.changed, "completion": result.completion})


@endpoint("POST")
def repeat_scan(request, request_id):
    """Для локальных назначений используем тот же расчёт повторов."""
    require_permission(request, "view_request")
    require_permission(request, "view_employee")
    obj = payload(request, "pairs")
    pairs = obj["pairs"]
    if not isinstance(pairs, list) or len(pairs) > MAX_REPEAT_PAIRS:
        raise ValidationError("Слишком много позиций для проверки")
    normalized = []
    for pair in pairs:
        if not isinstance(pair, dict) or set(pair) != {"employee_id", "item_id"}:
            raise ValidationError("Некорректная пара для проверки")
        normalized.append((positive_int(pair["employee_id"], "employee_id"),
                           positive_int(pair["item_id"], "item_id")))
    wanted = set(normalized)
    if len(wanted) != len(normalized):
        raise ValidationError("Дублирующиеся пары")
    req = Request.objects.get(pk=request_id)
    people = dict(Employee.objects.filter(pk__in={x[0] for x in wanted}).values_list("pk", "organization_id"))
    if len(people) != len({x[0] for x in wanted}) or any(org != req.organization_id for org in people.values()):
        raise ValidationError("Недопустимая организация сотрудника")

    reference_date = timezone.localdate()
    warnings = scan_repeats(
        pairs=wanted,
        as_of_date=reference_date,
        exclude_request_id=request_id,
    )
    return respond({"as_of_date": reference_date.isoformat(), "warnings": [
        {
            "employee_id": employee_id,
            "item_id": item_id,
            "previous_request_id": warning.previous_request_id,
            "previous_completion_date": warning.previous_completion_date.isoformat(),
            "remaining_days": warning.remaining_days,
            "validity_days": warning.validity_days,
        }
        for (employee_id, item_id), warning in sorted(warnings.items())
    ]})


@endpoint("POST")
def reopen_with_reason(request, request_id):
    from .api import as_request
    obj = payload(request, "expected_version", "reason")
    req = services.reopen_request(request_id=request_id, actor=request.user,
        expected_version=version(obj), reason=obj["reason"])
    return respond({"request": as_request(req)})
