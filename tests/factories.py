"""Все данные тестов вымышленные."""
import secrets
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.utils import timezone
from workwear.models import Employee, EmployeeImport, Organization, WorkwearItem


def make_user(*, administrator=True):
    return get_user_model().objects.create_user(
        username=f"test_{secrets.token_hex(6)}", is_staff=administrator,
        is_superuser=administrator,
    )


def make_import():
    return EmployeeImport.objects.create(
        employees_sha256=secrets.token_hex(32), positions_sha256=secrets.token_hex(32),
        units_sha256=secrets.token_hex(32), source_row_count=1000, employee_count=1000,
        source_export_date=timezone.localdate(),
    )


def make_organization(name="Тестовая организация А", *, enabled=True, external_id=None):
    return Organization.objects.get_or_create(name=name, defaults={
        "external_id": external_id or str(uuid4()), "requests_enabled": enabled,
    })[0]


def make_employees(snapshot, count=2, organization=None):
    organization = organization or make_organization()
    return Employee.objects.bulk_create([
        Employee(employee_guid=uuid4(), personnel_number=f"TEST-{i:05d}",
                 full_name=f"Тестовый сотрудник {i:05d}", organization=organization,
                 last_seen_import=snapshot) for i in range(count)
    ])


def make_items(count=2):
    return WorkwearItem.objects.bulk_create([
        WorkwearItem(code=f"TEST-{i:02d}", name=f"Тестовая позиция {i:02d}",
                     category="Тестовая категория", validity_days=365 if i % 2 == 0 else 730,
                     sort_order=i) for i in range(count)
    ])
