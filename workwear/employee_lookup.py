"""Поиск по ФИО и номеру 1С не меняет данные. Ключ сотрудника — GUID, а не совпадение имени."""
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

    lookups = [(text, normalize_name(text), normalize_number(text)) for text in map(str.strip, lines)]
    name_map = {name: [] for _, name, _ in lookups}
    number_map = {number: [] for _, _, number in lookups}
    suffix_map = {number: [] for number in number_map
                  if len(number) == 4 and number.isascii() and number.isdecimal()}
    queryset = Employee.objects.select_related("organization").only(
        "id", "full_name", "personnel_number", "organization__name", "organization_id",
        "last_seen_import_id", "position_name", "department_name",
    )
    # Сохраняем только совпадения со строками запроса. Для статуса «Другая организация»
    # проверяем весь справочник, включая сотрудников предыдущих выгрузок.
    for employee in queryset.iterator(chunk_size=2000):
        name = normalize_name(employee.full_name)
        if name in name_map:
            name_map[name].append(employee)
        number = normalize_number(employee.personnel_number)
        if number in number_map:
            number_map[number].append(employee)
        suffix = number[-4:]
        if suffix in suffix_map:
            suffix_map[suffix].append(employee)

    used_ids = set()
    results = []
    for text, name, number in lookups:
        candidates = name_map[name] or number_map[number] or suffix_map.get(number, [])
        relevant = [x for x in candidates if x.organization_id == organization_id]
        current = [x for x in relevant if is_current(
            x, organization_id=organization_id, latest=latest
        )]
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
