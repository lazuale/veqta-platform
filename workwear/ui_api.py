"""Предметные JSON-представления для интерфейса Workwear.

Чтение не ограничивается организацией пользователя; поиск сотрудников и новое
включение ограничены организацией заявки. Изменения передаются services/sync.
Транспортные лимиты, права и ошибки описаны в docs/workwear.md. Здесь нет копии
алгоритма повторов: оба маршрута обращаются к одному серверному расчёту."""
from .eligibility import latest_import
from .employee_lookup import employee_data, resolve_many
from dataclasses import asdict
from datetime import date

from django.core.exceptions import ValidationError
from django.db.models import Case, CharField, Count, F, Func, IntegerField, Q, Value, When
from django.utils import timezone

from .limits import MAX_REQUEST_MEMBERS, MAX_ITEMS_PER_MEMBER, MAX_REPEAT_PAIRS, MAX_JSON_BYTES, MAX_SYNC_JSON_BYTES
from . import services
from .api import endpoint, payload, positive_int, require_permission, respond, version
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
    if number < 0 or number > maximum:
        raise ValidationError(f"Параметр {key} вне допустимого диапазона")
    return number


def _latest_import():
    return latest_import()


@endpoint("GET")
def metadata(request):
    """GET view_request: организации, доступный каталог, 8 флагов native permissions и дата импорта.

    Каталог выдаётся только с view_workwearitem; период не выводится из accepted_at."""
    require_permission(request, "view_request")
    latest = _latest_import()
    items = []
    if request.user.has_perm("workwear.view_workwearitem"):
        items = [
            {"id": item.pk, "code": item.code, "name": item.name,
             "category": item.category, "validity_days": item.validity_days}
            for item in WorkwearItem.objects.filter(is_active=True).order_by("sort_order", "pk")
        ]
    return respond({
        "organizations": [{"value": code, "label": label} for code, label in Organization.choices],
        "items": items,
        "import": {
            "accepted_at": latest.accepted_at.isoformat() if latest else None,
            "employee_count": latest.employee_count if latest else None,
            # accepted_at не доказывает период источника; отсутствующие даты не выдумываются.
            "source_exported_at": latest.source_exported_at.isoformat() if latest and latest.source_exported_at else None,
            "coverage_start": latest.coverage_start.isoformat() if latest and latest.coverage_start else None,
            "coverage_end": latest.coverage_end.isoformat() if latest and latest.coverage_end else None,
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
    """GET view_request: фильтры/поиск по историческим snapshot-полям, пагинация до 100.

    Все организации доступны читателю; ограничения изменения проверяются отдельно."""
    require_permission(request, "view_request")
    org = _query_text(request, "organization_name", limit=255)
    status = _query_text(request, "status", limit=12)
    q = _query_text(request, "q", limit=120)
    if org and org not in Organization.values:
        raise ValidationError("Недопустимая организация")
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
        qs = qs.filter(organization_name=org)
    if status:
        qs = qs.filter(status=status)
    if q:
        query = Q(organization_name__icontains=q) | Q(members__snapshot_full_name__icontains=q) | Q(
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
            {"id": r.pk, "organization_name": r.organization_name,
             "status": r.status, "version": r.version,
             "created_at": r.created_at.isoformat(),
             "completed_at": r.completed_at.isoformat() if r.completed_at else None,
             "members_count": member_counts.get(r.pk, 0)}
            for r in requests
        ],
    })


@endpoint("GET")
def employees(request):
    """GET view_employee: поиск актуального справочника по явной организации и строке ≥2 символов.

    Ранжирование выполняется SQL до LIMIT/OFFSET; страница до 40 записей."""
    require_permission(request, "view_employee")
    org = _query_text(request, "organization_name", limit=255)
    if org not in Organization.values:
        raise ValidationError("Выберите организацию")
    q = _query_text(request, "q", limit=120)
    offset = _query_num(request, "offset", default=0, maximum=1_000_000)
    limit = _query_num(request, "limit", default=40, maximum=40)
    if limit == 0:
        raise ValidationError("Лимит должен быть положительным")
    latest = _latest_import()
    if len(q) < 2 or latest is None:
        return respond({"results": [], "min_query_length": 2,
                        "count": 0, "offset": offset, "limit": limit, "has_more": False,
                        "import_available": latest is not None})
    # Ранжирование выполняется SQL до LIMIT/OFFSET. Формат ФИО:
    # фамилия, имя, отчество. Совпадение внутри отчества не должно
    # вытеснять точную фамилию с первой страницы результатов.
    q = " ".join(q.split())
    surname = Func(F("full_name"), Value(" "), Value(1),
                   function="split_part", output_field=CharField())
    given_name = Func(F("full_name"), Value(" "), Value(2),
                      function="split_part", output_field=CharField())
    patronymic = Func(F("full_name"), Value(" "), Value(3),
                      function="split_part", output_field=CharField())
    qs = (Employee.objects
          .filter(organization_name=org, last_seen_import_id=latest.pk)
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
    """POST view_employee: разрешить до 100 строк без изменения БД.

    Для request_id дополнительно требуются view_request и совпадение организации.
    present_employee_ids учитывает локально добавленных участников, не разрешая запись."""
    require_permission(request, "view_employee")
    obj = payload(request, "organization_name", "lines",
                  optional_fields=("request_id", "present_employee_ids"))
    if hasattr(obj, "status_code"):
        return obj
    org, lines = obj["organization_name"], obj["lines"]
    if org not in Organization.values:
        raise ValidationError("Недопустимая организация")
    if not isinstance(lines, list) or not 1 <= len(lines) <= 100:
        raise ValidationError("Список должен содержать от 1 до 100 строк")
    if any(not isinstance(line, str) or len(line) > 255 or not line.strip() for line in lines):
        raise ValidationError("Каждая строка должна содержать ФИО или табельный номер")
    if "request_id" in obj:
        require_permission(request, "view_request")
    latest = _latest_import()
    already = set()
    if "request_id" in obj:
        request_id = positive_int(obj["request_id"], "request_id")
        parent = Request.objects.get(pk=request_id)
        if parent.organization_name != org:
            raise ValidationError("Организация заявки не совпадает с запросом")
        already.update(RequestEmployee.objects.filter(request_id=request_id).values_list("employee_id", flat=True))
    staged = obj.get("present_employee_ids", [])
    if not isinstance(staged, list) or len(staged) > 1000 or any(type(x) is not int or x <= 0 for x in staged):
        raise ValidationError("Недопустимый список сотрудников черновика")
    already.update(staged)
    return respond({"import_id": latest.pk if latest else None, "results": resolve_many(
        lines=lines,
        organization_name=org,
        latest=latest,
        already=already,
    )})


@endpoint("POST")
def bulk_add(request, request_id):
    """POST change_request: атомарно включить до1000 employee_ids с expected_version."""
    obj = payload(request, "employee_ids", "expected_version")
    if hasattr(obj, "status_code"):
        return obj
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
    """GET view_request + view_workwearevent: журнал по номеру заявки, до 100 событий.

    События удалённого черновика остаются доступны при наличии журнала."""
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
def repeat(request, request_id):
    """GET view_request + view_employee: предупреждение для существующего участника заявки."""
    require_permission(request, "view_request")
    require_permission(request, "view_employee")
    e = _query_num(request, "employee_id", default=0, maximum=9223372036854775807)
    i = _query_num(request, "item_id", default=0, maximum=9223372036854775807)
    if not e or not i:
        raise ValidationError("Требуются employee_id и item_id")
    RequestEmployee.objects.get(request_id=request_id, employee_id=e)
    alerts = services.repeat_warnings_for(
        employee_id=e, item_id=i, as_of_date=timezone.localdate(), exclude_request_id=request_id,
    )
    return respond({"warnings": [
        {**asdict(w), "previous_completion_date": w.previous_completion_date.isoformat()}
        for w in alerts
    ]})


@endpoint("GET")
def employee_history(request, request_id, employee_id):
    """GET view_request + view_workwearevent: до 100 прежних закрытых snapshot-заявок.

    Сотрудник может быть ещё локальным участником; сервер проверяет организацию
    текущей заявки и сотрудника до выдачи истории."""
    require_permission(request, "view_request")
    require_permission(request, "view_workwearevent")
    # Новый участник может пока существовать только в локальном черновике.
    # История нужна до сохранения, поэтому наличие RequestEmployee
    # не обязательно; организация проверяется сервером независимо.
    req = Request.objects.get(pk=request_id)
    Employee.objects.get(pk=employee_id, organization_name=req.organization_name)
    rows = list(
        RequestEmployee.objects.filter(
            employee_id=employee_id,
            request__status=RequestStatus.CLOSED,
        ).exclude(request_id=request_id)
        .select_related("request")
        .prefetch_related("assignments__item")
        .order_by("-request__completed_at", "-request_id")[:100]
    )
    return respond({"results": [{
        "request_id": m.request_id,
        "completed_at": m.request.completed_at.isoformat(),
        "organization_name": m.snapshot_organization_name,
        "full_name": m.snapshot_full_name,
        "personnel_number": m.snapshot_personnel_number,
        "department_name": m.snapshot_department_name,
        "items": [{
            "id": a.item_id, "name": a.item.name,
            "validity_days": a.item.validity_days,
        } for a in m.assignments.all()],
    } for m in rows], "limit": 100})


@endpoint("POST")
def sync_request(request, request_id):
    """POST change_request: строгий members/expected_version/finish → sync.save_draft.

    HTTP-слой не строит delta и не завершает заявку самостоятельно."""
    from .sync import save_draft
    obj = payload(request, "members", "expected_version", "finish", max_bytes=MAX_SYNC_JSON_BYTES)
    if hasattr(obj, "status_code"):
        return obj
    result = save_draft(
        request_id=request_id, actor=request.user,
        expected_version=version(obj), members=obj["members"],
        finish=obj["finish"],
    )
    return respond({"request_id": result.request_id, "version": result.version,
                    "changed": result.changed, "completion": result.completion})


@endpoint("POST")
def repeat_scan(request, request_id):
    """POST view_request + view_employee: до 1200 уникальных пар, включая локальные назначения.

    Проверяет ID, организацию и позиции; вычисления выполняет общий сервис без записи."""
    require_permission(request, "view_request")
    require_permission(request, "view_employee")
    obj = payload(request, "pairs")
    if hasattr(obj, "status_code"):
        return obj
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
    people = dict(Employee.objects.filter(pk__in={x[0] for x in wanted}).values_list("pk", "organization_name"))
    if len(people) != len({x[0] for x in wanted}) or any(org != req.organization_name for org in people.values()):
        raise ValidationError("Недопустимая организация сотрудника")
    # Проверяем ID до расчёта; календарные правила принадлежат общему
    # сервису одиночных и пакетных предупреждений.
    reference_date = timezone.localdate()
    warnings = services.repeat_warnings_for_pairs(
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
    """POST reopen_request: версия и reason до 500 символов; журнал формирует сервис."""
    from .api import as_request
    obj = payload(request, "expected_version", "reason")
    if hasattr(obj, "status_code"):
        return obj
    req = services.reopen_request(request_id=request_id, actor=request.user,
        expected_version=version(obj), reason=obj["reason"])
    return respond({"request": as_request(req)})
