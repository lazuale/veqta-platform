"""Проверяем чистую установку в одноразовой базе."""
from io import StringIO
import os
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
from uuid import uuid4


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    from django.core.management import call_command
    from django.db import connection
    from django.conf import settings
    from django.test import Client
    from django.utils import timezone
    from config.database import require_target
    from workwear.models import (
        Organization, EmployeeImport, Employee, Request, RequestEmployee,
        RequestAssignment, WorkwearItem, WorkwearEvent,
    )
    from .test_import import make_sources

    environment, database = settings.VEQTA_ENV, settings.EXPECTED_DATABASE
    require_target(environment, database)
    for model in (Organization, EmployeeImport, Employee, Request, RequestEmployee,
                  RequestAssignment, WorkwearItem, WorkwearEvent):
        assert not model.objects.exists(), model.__name__
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname LIKE 'ww_%'")
        assert cursor.fetchone()[0] == 10
        cursor.execute("SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'ww_%' AND NOT tgisinternal AND tgenabled='O'")
        assert cursor.fetchone()[0] == 13
        cursor.execute("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conname IN (%s,%s)",
            ["ww_member_emp_org_fk", "ww_member_req_org_fk"])
        constraints = cursor.fetchall()
        assert len(constraints) == 2 and all("organization_id" in definition for _, definition in constraints)
        for table in ("workwear_employee", "workwear_requestemployee"):
            columns = {col.name for col in connection.introspection.get_table_description(cursor, table)}
            assert "organization_name" not in columns
        columns = {col.name for col in connection.introspection.get_table_description(cursor, "workwear_request")}
        assert "snapshot_organization_name" in columns and "organization_name" not in columns
    print(f"{environment.upper()} Cold Start: пустые справочники, 10 функций, 13 триггеров и составные FK")

    password = os.environ["DJANGO_SUPERUSER_PASSWORD"]
    client = Client(HTTP_HOST="localhost", HTTP_X_FORWARDED_PROTO="https")
    assert client.login(username="coldstart_admin", password=password)
    metadata = client.get("/workwear/api/ui/metadata/").json()
    assert metadata["organizations"] == [] and metadata["items"] == []
    assert client.get("/admin/").status_code == 200
    assert client.get("/workwear/").status_code == 200
    assert client.post("/workwear/api/requests/", {"organization_id": 1}, content_type="application/json").status_code == 400
    external_id = str(uuid4())
    assert client.post("/admin/workwear/organization/add/", {
        "name": "Организация нового тестового контура", "external_id": external_id,
        "requests_enabled": "on",
    }).status_code == 302
    assert client.post("/admin/workwear/workwearitem/add/", {
        "code": "COLDSTART-90", "name": "Тестовая позиция чистой установки", "category": "Тестовая категория",
        "validity_days": 90, "sort_order": 0, "is_active": "on",
    }).status_code == 302
    with TemporaryDirectory() as directory:
        source = Path(directory)
        make_sources(source, organization_id=external_id, organization_name="Название из синтетического источника")
        archive_path = source / "source.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            for filename in ("Employees.csv", "Positions.csv", "Units.csv"):
                archive.add(source / filename, arcname=filename)
        options = dict(environment=environment, database_name=database, stdout=StringIO())
        call_command("workwear_import_1c", str(archive_path), **options)
        call_command("workwear_import_1c", str(archive_path), apply=True,
            source_export_date=timezone.localdate().isoformat(), expected_employee_count=1,
            expected_previous_import_id=0, **options)
    organization = Organization.objects.get()
    created = client.post("/workwear/api/requests/", {"organization_id": organization.pk}, content_type="application/json")
    assert created.status_code == 201
    req = created.json()["request"]
    assert req["snapshot_organization_name"] == organization.name
    result = client.post(f"/workwear/api/ui/requests/{req['id']}/sync/", {
        "expected_version": 1, "finish": True,
        "members": [{"employee_id": Employee.objects.get().pk, "item_ids": [WorkwearItem.objects.get().pk]}],
    }, content_type="application/json")
    assert result.status_code == 200, result.content
    assert Request.objects.get().status == "closed"
    assert RequestEmployee.objects.get().snapshot_organization_name == organization.name
    assert RequestAssignment.objects.get().snapshot_validity_days == 90
    from .database_roles import check_runtime_permissions
    check_runtime_permissions()
    print("Cold Start: createsuperuser, Admin, пустые состояния, справочники, импорт CLI и первая завершённая заявка — успешно")


if __name__ == "__main__":
    main()
