"""Проверяем подключение под прикладной ролью после чистой установки."""
from django.conf import settings
from django.db import connection, DatabaseError, transaction
from psycopg import sql

from config.database import database_role, require_target
from workwear import services
from workwear.models import Request, WorkwearEvent
from .factories import make_employees, make_import, make_items, make_organization, make_user


def check_runtime_permissions():
    require_target(settings.VEQTA_ENV, settings.EXPECTED_DATABASE)
    migrator = database_role(settings.VEQTA_ENV, migration=True)

    def denied(sql, params=(), state="42501"):
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(sql, params)
        except DatabaseError as error:
            assert error.__cause__.sqlstate == state, (sql, error)
        else:
            raise AssertionError(f"Прикладной роли разрешена защищённая операция: {sql}")

    for statement in (
        "ALTER TABLE workwear_workwearevent DISABLE TRIGGER USER",
        "ALTER TABLE workwear_request ADD COLUMN unexpected integer",
        "DROP TABLE workwear_workwearevent",
        "CREATE TABLE public.unexpected (id integer)",
        "CREATE SCHEMA unexpected",
        "DROP FUNCTION ww_reject_event_change() CASCADE",
        "SET session_replication_role = replica",
        sql.SQL("SET ROLE {}").format(sql.Identifier(migrator)),
        "SELECT id FROM django_migrations",
        "INSERT INTO django_migrations (app, name, applied) VALUES ('probe', 'probe', now())",
        "SELECT nextval('public.django_migrations_id_seq')",
        "SELECT last_value FROM workwear_workwearevent_id_seq",
        "SELECT setval('workwear_workwearevent_id_seq', 1)",
        "TRUNCATE workwear_workwearevent CASCADE",
    ):
        denied(statement)

    actor, source = make_user(), make_import()
    organization = make_organization("Синтетическая проверка прикладных прав")
    people = make_employees(source, organization=organization)
    items = make_items()
    req = services.create_request(organization_id=organization.pk, actor=actor)
    services.add_employee(request_id=req.pk, employee_id=people[0].pk, actor=actor, expected_version=1)
    # Запись проверяем через тот же сервис, что использует интерфейс.
    from workwear.sync import save_draft
    req.refresh_from_db()
    save_draft(request_id=req.pk, actor=actor, expected_version=req.version,
        members=[{"employee_id": people[0].pk, "item_ids": [items[0].pk]}], finish=True)
    assert Request.objects.get(pk=req.pk).status == "closed"
    event = WorkwearEvent.objects.filter(request_number=req.pk).first()
    denied("DELETE FROM workwear_workwearevent WHERE id=%s", [event.pk])
    denied("UPDATE workwear_workwearevent SET details='{}' WHERE id=%s", [event.pk])
    denied("UPDATE workwear_request SET version=version+1 WHERE id=%s", [req.pk], "23514")
    denied("DELETE FROM workwear_request WHERE id=%s", [req.pk], "23514")
    draft = services.create_request(organization_id=organization.pk, actor=actor)
    other = make_employees(source, 1, make_organization("Другая организация проверки прав"))[0]
    denied("""INSERT INTO workwear_requestemployee
        (request_id, employee_id, organization_id, snapshot_full_name,
         snapshot_personnel_number, snapshot_organization_name, snapshot_position_name, snapshot_department_name)
        VALUES (%s, %s, %s, '', '', '', '', '')""", [draft.pk, other.pk, organization.pk], "23503")
    assert Request.objects.get(pk=req.pk).status == "closed"
    print("Прикладная роль: ORM/Admin/API работают; DDL, отключение guards, удаление истории, закрытые заявки и чужая организация запрещены")
