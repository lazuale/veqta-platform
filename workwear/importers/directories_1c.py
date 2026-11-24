"""Читаем Employees.csv, Positions.csv и Units.csv без обращения к БД. Проверяем GUID, ссылки и повторяющиеся строки."""
from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import io
from pathlib import Path
import tarfile
from uuid import UUID


class ImportRejected(ValueError):
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
        raise ImportRejected(f"{where}: неверный UUID {v!r}") from exc


def _register_organization(names, identifier, name, where):
    if identifier and (len(identifier) > 255 or any(c.isspace() for c in identifier)):
        raise ImportRejected(f"{where}: некорректный orgExtId")
    if len(name) > 255:
        raise ImportRejected(f"{where}: значение orgName слишком длинное")
    if identifier and name:
        previous = names.setdefault(identifier, name)
        if previous != name:
            raise ImportRejected(f"{where}: разные значения orgName для orgExtId {identifier}")


def _sources(source):
    p = Path(source).resolve()
    if p.is_dir():
        result = {}
        for name in SOURCES:
            candidate = p / name
            if not candidate.is_file() or candidate.is_symlink():
                raise ImportRejected(f"Файл отсутствует или путь небезопасен: {name}")
            if candidate.stat().st_size > MAX_FILE_BYTES:
                raise ImportRejected(f"Файл превышает допустимый размер: {name}")
            result[name] = candidate.read_bytes()
        return result
    if not p.is_file() or not p.name.endswith((".tar.gz", ".tgz")):
        raise ImportRejected("Ожидается каталог или архив .tar.gz с файлами Employees.csv, Positions.csv и Units.csv")
    result = {}
    with tarfile.open(p, "r:gz") as archive:
        for item in archive.getmembers():
            if item.name not in SOURCES:
                continue  # В архиве используем только обычные файлы.
            if item.name in result or not item.isfile() or not 0 <= item.size <= MAX_FILE_BYTES:
                raise ImportRejected(f"Небезопасный или повторяющийся файл в архиве: {item.name}")
            with archive.extractfile(item) as f:
                result[item.name] = f.read(item.size)
    if set(result) != set(SOURCES):
        raise ImportRejected(f"В архиве должны быть файлы: {', '.join(SOURCES)}")
    return result


def _rows(data, name, required):
    try:
        decoded = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportRejected(f"{name}: требуется кодировка UTF-8 (BOM допускается)") from exc
    if "\x00" in decoded:
        raise ImportRejected(f"{name}: файл содержит нулевой символ")
    reader = csv.DictReader(io.StringIO(decoded, newline=""), delimiter=";", strict=True)
    try:
        fieldnames = reader.fieldnames
        if fieldnames is None or len(fieldnames) != len(set(fieldnames)):
            raise ImportRejected(f"{name}: отсутствуют заголовки CSV или есть повторяющиеся столбцы")
        missing = set(required) - set(fieldnames)
        if missing:
            raise ImportRejected(f"{name}: отсутствуют обязательные столбцы: {sorted(missing)}")
        for n, row in enumerate(reader, start=2):
            if n - 1 > MAX_ROWS or None in row or any(v is None for v in row.values()):
                raise ImportRejected(f"{name}:{n}: некорректная строка CSV")
            yield n, row
    except csv.Error as exc:
        raise ImportRejected(f"{name}: ошибка формата CSV: {exc}") from exc


@dataclass(frozen=True)
class Person:
    """GUID остаётся постоянным ключом сотрудника."""
    guid: UUID
    personnel_number: str
    full_name: str
    position_name: str
    department_name: str
    organization_external_id: str


@dataclass(frozen=True)
class ParsedBatch:
    people: tuple[Person, ...]
    sha256: dict[str, str]
    source_row_count: int
    anomalies: dict[str, int]
    organizations: dict[str, str]


def parse(path):
    """Принимаем каталог или tar.gz. Ошибки чтения файлов передаём вызывающей команде."""
    data = _sources(path)
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in data.items()}
    anomalies = {
        "duplicate_employee_rows": 0, "partial_employee_merges": 0,
        "duplicate_position_rows": 0, "duplicate_unit_rows": 0,
        "employees_without_position": 0, "employees_without_unit": 0,
        "unit_without_organization": 0,
    }

    positions = {}
    for line, row in _rows(data["Positions.csv"], "Positions.csv", POSITIONS_FIELDS):
        uid = _guid(row["extId"], f"Positions.csv:{line}")
        label = _clean(row["name"])
        if not label or len(label) > 255:
            raise ImportRejected(f"Positions.csv:{line}: не указана должность или её название слишком длинное")
        if uid in positions:
            if positions[uid] != label:
                raise ImportRejected(f"Positions.csv:{line}: разные названия должности для GUID {uid}")
            anomalies["duplicate_position_rows"] += 1
        positions[uid] = label
    if not positions:
        raise ImportRejected("Positions.csv: справочник должностей пуст")

    units = {}
    organization_names = {}
    for line, row in _rows(data["Units.csv"], "Units.csv", UNITS_FIELDS):
        uid = _guid(row["extId"], f"Units.csv:{line}")
        org = _clean(row["orgExtId"]).casefold()
        _register_organization(organization_names, org, _clean(row["orgName"]), f"Units.csv:{line}")
        name = _clean(row["name"])
        if not name or len(name) > 255:
            raise ImportRejected(f"Units.csv:{line}: не указано подразделение или его название слишком длинное")
        # Один GUID подразделения может встречаться в разных юрлицах.
        key = (uid, org)
        if key in units:
            if units[key] != name:
                raise ImportRejected(f"Units.csv:{line}: разные названия подразделения для GUID и организации {key}")
            anomalies["duplicate_unit_rows"] += 1
        units[key] = name
    if not units:
        raise ImportRejected("Units.csv: справочник подразделений пуст")

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
                raise ImportRejected(f"Employees.csv:{line}: разные значения поля {key} для GUID {uid}")
            merged[key] = oldval or newval
        if merged != old:
            anomalies["partial_employee_merges"] += 1
        employees[uid] = merged
    if not employees:
        raise ImportRejected("Employees.csv: справочник сотрудников пуст")

    people = []
    for uid, row in employees.items():
        number, full_name, org_name = (
            row["tabNumber"], row["name"], row["orgName"])
        org_id = row["orgExtId"]
        if not (full_name and org_id and org_name):
            raise ImportRejected(f"Employees.csv: не указаны ФИО или организация для GUID {uid}")
        if len(number) > 64 or len(full_name) > 255 or len(org_name) > 255:
            raise ImportRejected(f"Employees.csv: значение поля сотрудника слишком длинное для GUID {uid}")
        if not number:
            raise ImportRejected(f"Employees.csv: не указан табельный номер для GUID {uid}")
        _register_organization(organization_names, org_id.casefold(), org_name, f"Employees.csv:{uid}")
        position, department = "", ""
        if row["positionExtId"]:
            pkey = _guid(row["positionExtId"], f"Employees.csv:{uid}:positionExtId")
            if pkey not in positions:
                raise ImportRejected(f"Employees.csv: не найдена должность positionExtId для GUID {uid}")
            position = positions[pkey]
        else:
            anomalies["employees_without_position"] += 1
        if row["unitExtId"]:
            ukey = (_guid(row["unitExtId"], f"Employees.csv:{uid}:unitExtId"), org_id.casefold())
            if ukey not in units:
                # Строка подразделения без организации не разрешает брать
                # название из другого юрлица.
                # Неизвестный GUID означает нарушенную ссылку.
                matches = [k for k in units if k[0] == ukey[0]]
                if not matches:
                    raise ImportRejected(f"Employees.csv: не найдено подразделение (unitExtId, orgExtId) для GUID {uid}")
                if any(k[1] for k in matches):
                    raise ImportRejected(f"Employees.csv: организация подразделения не совпадает с организацией сотрудника GUID {uid}")
                anomalies["unit_without_organization"] += 1
            else:
                department = units[ukey]
        else:
            anomalies["employees_without_unit"] += 1
        people.append(Person(uid, number, full_name, position, department, org_id.casefold()))
    return ParsedBatch(
        people=tuple(sorted(people, key=lambda x: x.guid.hex)),
        sha256=hashes, source_row_count=employee_rows,
        anomalies=anomalies,
        organizations=organization_names,
    )
