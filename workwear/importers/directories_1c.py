"""Заменяемый адаптер трёх справочников 1С без обращений к Django/БД.

Employees.csv + Positions.csv + Units.csv: UTF-8/BOM, разделитель «;».
Парсер читает каталог или выбранные обычные файлы tar.gz без распаковки,
проверяет лимиты, GUID и согласованность непустых атрибутов дублей.
Timesheets и выдуманный трёхмесячный период в этом контракте отсутствуют."""
from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import io
from pathlib import Path
import tarfile
from uuid import UUID


class ImportRejected(ValueError):
    """Ошибка формата/связей исходных справочников до любых записей в базу."""
    pass


EMPLOYEES_FIELDS = (
    "extId", "tabNumber", "name", "positionExtId", "unitExtId", "orgExtId", "orgName",
)
POSITIONS_FIELDS = ("extId", "name")
UNITS_FIELDS = ("extId", "name", "orgExtId", "orgName")
SOURCES = ("Employees.csv", "Positions.csv", "Units.csv")
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_ROWS = 1_000_000


def _clean(v):
    # Строка NULL в выгрузке означает отсутствие значения,
    # а не GUID, организацию или название должности.
    text = " ".join(str(v or "").split())
    return "" if text.casefold() == "null" else text


def _guid(v, where):
    try:
        return UUID(_clean(v))
    except (ValueError, AttributeError) as exc:
        raise ImportRejected(f"{where}: invalid UUID {v!r}") from exc


def _register_organization(names, identifier, name, where):
    if identifier and (len(identifier) > 255 or any(c.isspace() for c in identifier)):
        raise ImportRejected(f"{where}: invalid orgExtId")
    if len(name) > 255:
        raise ImportRejected(f"{where}: orgName exceeds DB limit")
    if identifier and name:
        previous = names.setdefault(identifier, name)
        if previous != name:
            raise ImportRejected(f"{where}: inconsistent orgName for orgExtId {identifier}")


def _sources(source):
    p = Path(source).resolve()
    if p.is_dir():
        result = {}
        for name in SOURCES:
            candidate = p / name
            if not candidate.is_file() or candidate.is_symlink():
                raise ImportRejected(f"Missing/unsafe source: {name}")
            if candidate.stat().st_size > MAX_FILE_BYTES:
                raise ImportRejected(f"Source exceeds size limit: {name}")
            result[name] = candidate.read_bytes()
        return result
    if not p.is_file() or not p.name.endswith((".tar.gz", ".tgz")):
        raise ImportRejected("Expected a directory or .tar.gz with Employees/Positions/Units CSV")
    result = {}
    with tarfile.open(p, "r:gz") as archive:
        for item in archive.getmembers():
            if item.name not in SOURCES:
                continue  # Без распаковки; посторонние метаданные не используются.
            if item.name in result or not item.isfile() or item.size > MAX_FILE_BYTES:
                raise ImportRejected(f"Unsafe/duplicate archive source: {item.name}")
            with archive.extractfile(item) as f:
                data = f.read(MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES:
                    raise ImportRejected(f"Source exceeds size limit: {item.name}")
                result[item.name] = data
    if set(result) != set(SOURCES):
        raise ImportRejected(f"Archive must contain {', '.join(SOURCES)}")
    return result


def _rows(data, name, required):
    try:
        decoded = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportRejected(f"{name}: UTF-8 with optional BOM required") from exc
    reader = csv.DictReader(io.StringIO(decoded, newline=""), delimiter=";", strict=True)
    if reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise ImportRejected(f"{name}: missing/duplicate CSV headers")
    missing = set(required) - set(reader.fieldnames)
    if missing:
        raise ImportRejected(f"{name}: missing required columns: {sorted(missing)}")
    try:
        for n, row in enumerate(reader, start=2):
            if n > MAX_ROWS or None in row or any(v is None for v in row.values()):
                raise ImportRejected(f"{name}:{n}: invalid CSV row")
            yield n, row
    except csv.Error as exc:
        raise ImportRejected(f"{name}: invalid CSV syntax: {exc}") from exc


@dataclass(frozen=True)
class Person:
    """Проверенная запись сотрудника из адаптера; GUID — постоянная идентичность."""
    guid: UUID
    personnel_number: str
    full_name: str
    organization_name: str
    position_name: str
    department_name: str
    organization_external_id: str
    current: bool = True


@dataclass(frozen=True)
class ParsedBatch:
    """Неизменяемый контейнер записей, исходных SHA-256, количества и агрегатов разбора."""
    people: tuple[Person, ...]
    sha256: dict[str, str]
    source_row_count: int
    current_count: int
    anomalies: dict[str, int]
    organizations: dict[str, str]


def parse(path, **_obsolete):
    # Старые параметры табеля отвергаются явно: три справочника
    # не доказывают наличие периода кадрового табеля.
    """Прочитать три CSV из path и вернуть ParsedBatch без побочных записей.

    path — каталог либо tar.gz/tgz с обычными файлами в корне. Невалидные GUID,
    конфликтующие непустые атрибуты дублей, сломанные ссылки и лимиты вызывают
    ImportRejected; ошибки доступа к файлу передаются вызывающей CLI. Дату
    выгрузки и полноту адаптер не изобретает; obsolete-аргументы отвергаются."""
    if _obsolete:
        raise ImportRejected("Timesheet/coverage arguments are obsolete: import three directories only")
    data = _sources(path)
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in data.items()}
    anomalies = {
        "duplicate_employee_rows": 0, "partial_employee_merges": 0,
        "duplicate_position_rows": 0, "duplicate_unit_rows": 0,
        "employees_without_position": 0, "employees_without_unit": 0,
        "employees_without_tabnumber": 0, "unit_without_organization": 0,
    }

    positions = {}
    for line, row in _rows(data["Positions.csv"], "Positions.csv", POSITIONS_FIELDS):
        uid = _guid(row["extId"], f"Positions.csv:{line}")
        label = _clean(row["name"])
        if not label or len(label) > 255:
            raise ImportRejected(f"Positions.csv:{line}: invalid job title")
        if uid in positions:
            if positions[uid] != label:
                raise ImportRejected(f"Positions.csv:{line}: conflicting position GUID {uid}")
            anomalies["duplicate_position_rows"] += 1
        positions[uid] = label
    if not positions:
        raise ImportRejected("Positions.csv: empty dictionary")

    units = {}
    organization_names = {}
    for line, row in _rows(data["Units.csv"], "Units.csv", UNITS_FIELDS):
        uid = _guid(row["extId"], f"Units.csv:{line}")
        org = _clean(row["orgExtId"]).casefold()
        _register_organization(organization_names, org, _clean(row["orgName"]), f"Units.csv:{line}")
        name = _clean(row["name"])
        if not name or len(name) > 255:
            raise ImportRejected(f"Units.csv:{line}: invalid unit name")
        # Один GUID подразделения может встречаться в разных юрлицах.
        key = (uid, org)
        if key in units:
            if units[key] != name:
                raise ImportRejected(f"Units.csv:{line}: ambiguous unit and organization {key}")
            anomalies["duplicate_unit_rows"] += 1
        units[key] = name
    if not units:
        raise ImportRejected("Units.csv: empty dictionary")

    employee_rows = 0
    employees = {}
    # Допустимы одинаковые и взаимодополняющие строки одного GUID.
    # Непустые атрибуты обязаны совпасть; порядок строк не решает конфликт.
    fields = EMPLOYEES_FIELDS[1:]
    for line, row in _rows(data["Employees.csv"], "Employees.csv", EMPLOYEES_FIELDS):
        employee_rows += 1
        uid = _guid(row["extId"], f"Employees.csv:{line}")
        values = {col: _clean(row[col]) for col in fields}
        old = employees.get(uid)
        if old is None:
            employees[uid] = values
            continue
        anomalies["duplicate_employee_rows"] += 1
        merged = {}
        for key in fields:
            oldval, newval = old[key], values[key]
            if oldval and newval and oldval != newval:
                raise ImportRejected(f"Employees.csv:{line}: conflicting {key} for GUID {uid}")
            merged[key] = oldval or newval
        if merged != old:
            anomalies["partial_employee_merges"] += 1
        employees[uid] = merged
    if not employees:
        raise ImportRejected("Employees.csv: empty roster")

    people = []
    for uid, row in employees.items():
        number, full_name, org_name = (
            row["tabNumber"], row["name"], row["orgName"])
        org_id = row["orgExtId"]
        if not (full_name and org_id and org_name):
            raise ImportRejected(f"Employees.csv: missing identity/organization for GUID {uid}")
        if len(number) > 64 or len(full_name) > 255 or len(org_name) > 255:
            raise ImportRejected(f"Employees.csv: employee field exceeds DB limit for GUID {uid}")
        if not number:
            raise ImportRejected(f"Employees.csv: missing tabNumber for GUID {uid}")
        _register_organization(organization_names, org_id.casefold(), org_name, f"Employees.csv:{uid}")
        position, department = "", ""
        if row["positionExtId"]:
            pkey = _guid(row["positionExtId"], f"Employees.csv:{uid}:positionExtId")
            if pkey not in positions:
                raise ImportRejected(f"Employees.csv: orphan positionExtId for GUID {uid}")
            position = positions[pkey]
        else:
            anomalies["employees_without_position"] += 1
        if row["unitExtId"]:
            ukey = (_guid(row["unitExtId"], f"Employees.csv:{uid}:unitExtId"), org_id.casefold())
            if ukey not in units:
                # Строка подразделения без организации не разрешает брать
                # имя чужого юрлица или произвольную first().
                # Неизвестный GUID означает нарушенную ссылку.
                matches = [k for k in units if k[0] == ukey[0]]
                if not matches:
                    raise ImportRejected(f"Employees.csv: orphan (unitExtId, orgExtId) for GUID {uid}")
                if any(k[1] for k in matches):
                    raise ImportRejected(f"Employees.csv: organization mismatch for unit of GUID {uid}")
                anomalies["unit_without_organization"] += 1
            else:
                department = units[ukey]
        else:
            anomalies["employees_without_unit"] += 1
        people.append(Person(uid, number, full_name, org_name, position, department, org_id.casefold()))
    return ParsedBatch(
        people=tuple(sorted(people, key=lambda x: x.guid.hex)),
        sha256=hashes, source_row_count=employee_rows,
        current_count=len(people), anomalies=anomalies,
        organizations=organization_names,
    )
