"""Переход 0001 → 0002 на отдельной PostgreSQL с синтетической историей.

Запуск из изолированного DEV: python -m tests.upgrade.
Создаёт только новую test_veqta_dev, отказывается от уже существующей,
после проверки удаляет её целиком. Рабочую схему не изменяет.
"""
import os
from uuid import uuid4


TARGET = "test_veqta_dev"
OLD = [("workwear", "0001_initial")]
NEW = [("workwear", "0002_managed_directories")]


def seed(apps, *, ambiguous=False):
    from django.utils import timezone
    User = apps.get_model("auth", "User")
    Import = apps.get_model("workwear", "EmployeeImport")
    Employee = apps.get_model("workwear", "Employee")
    Request = apps.get_model("workwear", "Request")
    Member = apps.get_model("workwear", "RequestEmployee")
    Item = apps.get_model("workwear", "WorkwearItem")
    Assignment = apps.get_model("workwear", "RequestAssignment")
    Event = apps.get_model("workwear", "WorkwearEvent")
    # Старый CHECK принимает только значения исторической схемы.
    # Имена сотрудников, GUID, номера и вся история созданы этим тестом.
    name = Request._meta.get_field("organization_name").choices[0][0]
    actor = User.objects.create(username="synthetic_upgrade")
    source = Import.objects.create(employees_sha256="a" * 64, positions_sha256="b" * 64,
        units_sha256="c" * 64, source_row_count=2, employee_count=2)
    person = Employee.objects.create(employee_guid=uuid4(), full_name="Тестовый сотрудник миграции",
        personnel_number="TEST-UPGRADE", organization_name=name, last_seen_import=source)
    historical = Employee.objects.create(employee_guid=uuid4(), full_name="Исторический тестовый сотрудник",
        personnel_number="TEST-OLD", organization_name=name.lower() if ambiguous else "Историческая тестовая организация")
    item = Item.objects.create(code="UPGRADE", name="Тестовая позиция до миграции",
        category="Тестовая категория", validity_days=730)
    req = Request.objects.create(organization_name=name, created_by=actor)
    member = Member.objects.create(request=req, employee=person, organization_name=name,
        snapshot_full_name=person.full_name, snapshot_personnel_number=person.personnel_number,
        snapshot_organization_name=name)
    assignment = Assignment.objects.create(request_employee=member, item=item)
    req.status, req.version = "closed", 2
    req.completed_at, req.completed_by = timezone.now(), actor
    req.save()
    event = Event.objects.create(request_number=req.pk, event_type="synthetic_completed",
        actor=actor, actor_username=actor.username, details={"synthetic": True})
    return {"request": req.pk, "member": member.pk, "assignment": assignment.pk,
            "employee": person.pk, "historical": historical.pk, "item": item.pk,
            "event": event.pk, "name": name, "completed_at": req.completed_at}


def verify(connection, ids):
    from django.db import IntegrityError, transaction
    from workwear.models import Organization, Employee, Request, RequestAssignment, WorkwearEvent
    req = Request.objects.get(pk=ids["request"])
    member = req.members.get()
    assignment = RequestAssignment.objects.get(pk=ids["assignment"])
    assert (req.version, req.status, req.organization_name, req.completed_at) == (
        2, "closed", ids["name"], ids["completed_at"])
    assert req.organization_id == member.organization_id == Employee.objects.get(pk=ids["employee"]).organization_id
    assert member.pk == ids["member"] and member.snapshot_organization_name == ids["name"]
    assert (assignment.snapshot_item_code, assignment.snapshot_item_name, assignment.snapshot_validity_days) == (
        "UPGRADE", "Тестовая позиция до миграции", 730)
    assert WorkwearEvent.objects.get(pk=ids["event"]).details == {"synthetic": True}
    assert Organization.objects.count() == 2
    assert Organization.objects.get(pk=req.organization_id).requests_enabled
    assert not Employee.objects.get(pk=ids["historical"]).organization.requests_enabled
    assert not Organization.objects.exclude(external_id=None).exists()
    with connection.cursor() as cursor:
        cursor.execute("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conname IN (%s, %s)",
            ["ww_member_emp_org_fk", "ww_member_req_org_fk"])
        constraints = cursor.fetchall()
        assert len(constraints) == 2 and all("organization_id" in definition for _, definition in constraints)
        cursor.execute("SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'ww_%' AND tgenabled = 'D'")
        assert cursor.fetchone()[0] == 0
    for operation in (
        lambda: Request.objects.filter(pk=req.pk).update(version=3),
        lambda: RequestAssignment.objects.filter(pk=assignment.pk).update(snapshot_item_name="Подмена"),
        lambda: WorkwearEvent.objects.filter(pk=ids["event"]).update(details={}),
    ):
        try:
            with transaction.atomic():
                operation()
        except IntegrityError:
            pass
        else:
            raise AssertionError("Прямая запись обошла защиту истории")


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    from config.database import require_target
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    from psycopg import sql
    require_target("dev", "veqta_dev")
    original = connection.settings_dict["NAME"]
    with connection._nodb_cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", [TARGET])
        if cursor.fetchone():
            raise RuntimeError(f"{TARGET} уже существует; тест её не перезаписывает")
    for scenario in ("fresh", "populated", "ambiguous"):
        with connection._nodb_cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(TARGET), sql.Identifier(connection.settings_dict["USER"])))
        connection.close()
        connection.settings_dict["NAME"] = TARGET
        try:
            executor = MigrationExecutor(connection)
            if scenario == "fresh":
                executor.migrate(NEW)
                from workwear.models import Organization, WorkwearItem
                assert not Organization.objects.exists() and not WorkwearItem.objects.exists()
            else:
                executor.migrate(OLD)
                ids = seed(executor.loader.project_state(OLD).apps, ambiguous=scenario == "ambiguous")
                executor = MigrationExecutor(connection)
                if scenario == "populated":
                    executor.migrate(NEW)
                    verify(connection, ids)
                else:
                    try:
                        executor.migrate(NEW)
                    except RuntimeError as exc:
                        assert "Неоднозначные" in str(exc)
                    else:
                        raise AssertionError("Неоднозначные данные не остановили миграцию")
                    assert "workwear_organization" not in connection.introspection.table_names()
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT status, version FROM workwear_request WHERE id=%s", [ids["request"]])
                        assert cursor.fetchone() == ("closed", 2)
                        cursor.execute("SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'ww_%' AND tgenabled='D'")
                        assert cursor.fetchone()[0] == 0
                        cursor.execute("SELECT count(*) FROM pg_constraint WHERE conname IN (%s,%s)",
                            ["ww_member_emp_org_fk", "ww_member_req_org_fk"])
                        assert cursor.fetchone()[0] == 2
            print(f"Миграция: {scenario} — успешно")
        finally:
            connection.close()
            connection.settings_dict["NAME"] = original
            with connection._nodb_cursor() as cursor:
                cursor.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(TARGET)))


if __name__ == "__main__":
    main()
