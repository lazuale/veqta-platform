"""Поиск по ФИО и номеру 1С не меняет данные. Ключ сотрудника — GUID, а не совпадение имени."""
from collections import defaultdict
import re

from .eligibility import is_current
from .models import Employee


def employee_data(employee, latest_id=None):
    return {
        "id": employee.pk,
        "full_name": employee.full_name,
        "personnel_number": employee.personnel_number,
        "position_name": employee.position_name,
        "department_name": employee.department_name,
        "organization_name": employee.organization.name,
        "organization_id": employee.organization_id,
        "is_current": latest_id is not None and employee.last_seen_import_id == latest_id,
    }


def normalize_name(value):
    """Для поиска приравниваем «ё» к «е», не меняя сохранённое ФИО."""
    return re.sub(r"\s+", " ", str(value).strip()).casefold().replace("ё", "е")


def normalize_number(value):
    code = re.sub(r"[\s\-–—]+", "", str(value)).upper()
    return re.sub(r"^([А-ЯA-Z]{2})00(?=\d)", r"\1", code)


def resolve_many(*, lines, organization_id, latest, already):
    """Неоднозначные совпадения помечаем отдельно. Перед добавлением сотрудника сервис повторно проверяет его данные."""
    if latest is None:
        return [
            {"input": line.strip(), "status": "import_unavailable",
             "candidates": [], "candidate_count": 0}
            for line in lines
        ]

    name_map = defaultdict(list)
    number_map = defaultdict(list)
    suffix_map = defaultdict(list)
    queryset = Employee.objects.select_related("organization").only(
        "id", "full_name", "personnel_number", "organization__name", "organization_id",
        "last_seen_import_id", "position_name", "department_name",
    )
    for employee in queryset.iterator(chunk_size=2000):
        name_map[normalize_name(employee.full_name)].append(employee)
        number = normalize_number(employee.personnel_number)
        number_map[number].append(employee)
        if len(number) >= 4:
            suffix_map[number[-4:]].append(employee)

    used_ids = set()
    results = []
    for line in lines:
        text = line.strip()
        number = normalize_number(text)
        candidates = name_map.get(normalize_name(text), []) or number_map.get(number, [])
        if not candidates and number.isascii() and number.isdecimal() and len(number) == 4:
            candidates = suffix_map.get(number, [])
        relevant = [x for x in candidates if x.organization_id == organization_id]
        current = list({x.pk: x for x in relevant if is_current(
            x, organization_id=organization_id, latest=latest
        )}.values())
        if len(current) > 1:
            state = "ambiguous"
        elif len(current) == 1:
            employee_id = current[0].pk
            state = ("included" if employee_id in already else
                     "duplicate" if employee_id in used_ids else "matched")
            used_ids.add(employee_id)
        elif relevant:
            state = "outdated"
        elif candidates:
            state = "other_organization"
        else:
            state = "not_found"
        results.append({
            "input": text,
            "status": state,
            "candidates": [employee_data(x, latest.pk) for x in current[:10]],
            "candidate_count": len(current),
        })
    return results
