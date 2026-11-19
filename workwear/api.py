"""Проверяем методы, сессию и CSRF. Ошибки БД не раскрываем в ответах."""
import json
from dataclasses import asdict
from functools import wraps

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, RequestDataTooBig, ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_protect

from . import services
from .models import Organization, Request, RequestStatus

from .limits import MAX_JSON_BYTES


def respond(data, status=200):
    return JsonResponse(data, status=status, json_dumps_params={"ensure_ascii": False})


def error(status, code, message):
    return respond({"error": {"code": code, "message": message}}, status)


def endpoint(*methods):
    """Проверяем метод, сессию и CSRF. Ошибки целостности возвращаем без подробностей SQL."""
    def decorate(view):
        @csrf_protect
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method not in methods:
                result = error(405, "method_not_allowed", "Метод не поддерживается")
                result["Allow"] = ", ".join(methods)
                return result
            if not request.user.is_authenticated or not request.user.is_active:
                return error(401, "unauthorized", "Необходим вход в систему")
            try:
                return view(request, *args, **kwargs)
            except services.WorkwearConflict:
                return error(409, "stale_version", "Заявка была изменена. Обновите данные")
            except RequestDataTooBig:
                return error(413, "payload_too_large", "Слишком большой запрос")
            except PermissionDenied:
                return error(403, "forbidden", "Недостаточно прав")
            except ValidationError as exc:
                return error(400, "validation_error", "; ".join(exc.messages))
            except ObjectDoesNotExist:
                return error(404, "not_found", "Объект не найден")
            except IntegrityError:
                # Не раскрываем SQL и имена ограничений в ответе.
                return error(409, "integrity_conflict", "Не удалось выполнить действие из-за конфликта данных")
        return wrapped
    return decorate


def require_permission(request, codename):
    if not request.user.has_perm("workwear." + codename):
        raise PermissionDenied


def _reject_json_constant(value):
    raise ValueError("JSON не поддерживает NaN и бесконечность")


def payload(request, *fields, optional_fields=(), max_bytes=MAX_JSON_BYTES):
    """Принимаем только JSON заданного размера и с разрешёнными полями."""
    if request.content_type != "application/json":
        raise ValidationError("Ожидается Content-Type: application/json")
    try:
        content_length = int(request.META.get("CONTENT_LENGTH", "0") or "0")
    except ValueError:
        raise ValidationError("Некорректная длина запроса")
    if content_length > max_bytes:
        raise RequestDataTooBig
    raw = request.body
    if len(raw) > max_bytes:
        raise RequestDataTooBig
    try:
        obj = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
    except (ValueError, RecursionError):
        raise ValidationError("Некорректный JSON")
    if (not isinstance(obj, dict)
            or not set(fields).issubset(obj)
            or not set(obj).issubset(set(fields) | set(optional_fields))):
        raise ValidationError("Неверный набор полей запроса")
    return obj


def positive_int(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > 9223372036854775807:
        raise ValidationError(f"Поле {field} должно быть положительным целым числом")
    return value


def query_organization_id(value):
    """Организацию определяем по ID, а не по названию."""
    if not value or not value.isascii() or not value.isdecimal() or len(value) > 19:
        raise ValidationError("Недопустимая организация")
    number = int(value)
    if number <= 0 or number > 9223372036854775807 or not Organization.objects.filter(pk=number).exists():
        raise ValidationError("Недопустимая организация")
    return number


def version(obj):
    return positive_int(obj["expected_version"], "expected_version")


def as_request(req):
    return {
        "id": req.pk, "organization_id": req.organization_id, "snapshot_organization_name": req.snapshot_organization_name,
        "status": req.status, "version": req.version,
        "created_at": req.created_at.isoformat(),
        "completed_at": req.completed_at.isoformat() if req.completed_at else None,
        "created_by_id": req.created_by_id,
        "completed_by_id": req.completed_by_id,
    }


@endpoint("GET")
def session(request):
    return respond({"csrf_token": get_token(request), "username": request.user.get_username()})


@endpoint("GET", "POST")
def request_collection(request):
    if request.method == "POST":
        obj = payload(request, "organization_id")
        req = services.create_request(organization_id=positive_int(obj["organization_id"], "organization_id"), actor=request.user)
        return respond({"request": as_request(req)}, 201)
    require_permission(request, "view_request")
    organization, status = request.GET.get("organization_id"), request.GET.get("status")
    if organization is not None:
        organization = query_organization_id(organization)
    if status is not None and status not in RequestStatus.values:
        raise ValidationError("Недопустимый статус")
    qs = Request.objects.order_by("-created_at", "-pk")
    if organization:
        qs = qs.filter(organization_id=organization)
    if status:
        qs = qs.filter(status=status)
    return respond({"results": [as_request(r) for r in qs[:50]], "limit": 50})


@endpoint("GET", "DELETE")
def request_detail(request, request_id):
    if request.method == "DELETE":
        obj = payload(request, "expected_version")
        services.delete_draft(request_id=request_id, actor=request.user, expected_version=version(obj))
        return respond({"deleted": True})
    require_permission(request, "view_request")
    req = Request.objects.get(pk=request_id)
    members = req.members.prefetch_related("assignments").order_by("pk")
    result = as_request(req)
    result["members"] = [
        {"id": m.pk, "employee_id": m.employee_id,
         "full_name": m.snapshot_full_name,
         "personnel_number": m.snapshot_personnel_number,
         "snapshot_organization_name": m.snapshot_organization_name, "organization_id": m.organization_id,
         "position_name": m.snapshot_position_name,
         "department_name": m.snapshot_department_name,
         "assignments": [{"id": a.pk, "item_id": a.item_id,
                          "code": a.snapshot_item_code, "name": a.snapshot_item_name,
                          "category": a.snapshot_item_category, "validity_days": a.snapshot_validity_days}
                         for a in sorted(m.assignments.all(), key=lambda x: x.pk)]}
        for m in members
    ]
    return respond({"request": result})


@endpoint("POST")
@transaction.atomic
def member_add(request, request_id):
    obj = payload(request, "employee_id", "expected_version")
    member = services.add_employee(request_id=request_id,
        employee_id=positive_int(obj["employee_id"], "employee_id"),
        expected_version=version(obj), actor=request.user)
    return respond({"member_id": member.pk,
                    "version": Request.objects.values_list("version", flat=True).get(pk=request_id)}, 201)


@endpoint("DELETE")
def member_remove(request, request_id, member_id):
    obj = payload(request, "expected_version")
    result = services.remove_employee(request_id=request_id, member_id=member_id,
                                      actor=request.user, expected_version=version(obj))
    return respond({"deleted": True, "version": result})


@endpoint("POST")
@transaction.atomic
def assignment_add(request, request_id):
    obj = payload(request, "member_id", "item_id", "expected_version")
    a = services.add_assignment(request_id=request_id,
        member_id=positive_int(obj["member_id"], "member_id"),
        item_id=positive_int(obj["item_id"], "item_id"),
        expected_version=version(obj), actor=request.user)
    return respond({"assignment_id": a.pk,
                    "version": Request.objects.values_list("version", flat=True).get(pk=request_id)}, 201)


@endpoint("DELETE")
def assignment_remove(request, request_id, assignment_id):
    obj = payload(request, "expected_version")
    result = services.remove_assignment(request_id=request_id, assignment_id=assignment_id,
                                        actor=request.user, expected_version=version(obj))
    return respond({"deleted": True, "version": result})


@endpoint("POST")
def complete(request, request_id):
    obj = payload(request, "expected_version")
    result = services.complete_request(request_id=request_id, actor=request.user,
                                       expected_version=version(obj))
    return respond({"completion": asdict(result)})


@endpoint("POST")
def reopen(request, request_id):
    obj = payload(request, "expected_version")
    req = services.reopen_request(request_id=request_id, actor=request.user,
                                  expected_version=version(obj))
    return respond({"request": as_request(req)})
