"""Проверяем подключение под прикладной ролью после чистой установки."""
from django.db import connection, DatabaseError, transaction
from workwear import services
from workwear.models import Request, WorkwearEvent
from .factories import make_employees, make_import, make_items, make_organization, make_user


def grant_test_runtime_access():
    """Права приложения выдаём только в одноразовой базе тестов."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), current_user")
        assert cursor.fetchone() == ("test_veqta_dev", "veqta_dev_owner")
        cursor.execute("""
            REVOKE ALL ON DATABASE test_veqta_dev FROM PUBLIC;
            GRANT CONNECT ON DATABASE test_veqta_dev TO veqta_dev;
            REVOKE ALL ON SCHEMA public FROM PUBLIC;
            GRANT USAGE ON SCHEMA public TO veqta_dev;
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO veqta_dev;
            GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO veqta_dev;
            REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
            GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO veqta_dev;
        """)


def check_runtime_permissions():
    from config.database import require_target
    require_target("dev", "veqta_dev")
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication,
                   pg_has_role(current_user, 'veqta_dev_owner', 'MEMBER'),
                   has_schema_privilege(current_user, 'public', 'CREATE'),
                   has_database_privilege(current_user, current_database(), 'CREATE'),
                   has_database_privilege(current_user, current_database(), 'TEMP')
            FROM pg_roles WHERE rolname=current_user
        """)
        assert cursor.fetchone() == (False,) * 8
        cursor.execute("""
            SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_roles r ON r.oid=c.relowner
            WHERE n.nspname='public' AND c.relkind IN ('r','S') AND r.rolname<>'veqta_dev_owner'
        """)
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT bool_and(NOT prosecdef) FROM pg_proc WHERE proname LIKE 'ww_%'")
        assert cursor.fetchone()[0] is True

    def denied(sql, params=(), state="42501"):
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(sql, params)
        except DatabaseError as error:
            assert error.__cause__.sqlstate == state, (sql, error)
        else:
            raise AssertionError(f"Прикладной роли разрешена защищённая операция: {sql}")

    for sql in (
        "ALTER TABLE workwear_workwearevent DISABLE TRIGGER USER",
        "ALTER TABLE workwear_request ADD COLUMN unexpected integer",
        "DROP TABLE workwear_workwearevent",
        "CREATE TABLE public.unexpected (id integer)",
        "CREATE SCHEMA unexpected",
        "DROP FUNCTION ww_reject_event_change() CASCADE",
        "SET session_replication_role = replica",
        "SET ROLE veqta_dev_owner",
        "TRUNCATE workwear_workwearevent CASCADE",
    ):
        denied(sql)

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
    denied("DELETE FROM workwear_workwearevent WHERE id=%s", [event.pk], "23514")
    denied("UPDATE workwear_workwearevent SET details='{}' WHERE id=%s", [event.pk], "23514")
    denied("UPDATE workwear_request SET version=version+1 WHERE id=%s", [req.pk], "23514")
    denied("DELETE FROM workwear_request WHERE id=%s", [req.pk], "23514")
    draft = services.create_request(organization_id=organization.pk, actor=actor)
    other = make_employees(source, 1, make_organization("Другая организация проверки прав"))[0]
    denied("""INSERT INTO workwear_requestemployee
        (request_id, employee_id, organization_id, snapshot_full_name,
         snapshot_personnel_number, snapshot_organization_name, snapshot_position_name, snapshot_department_name)
        VALUES (%s, %s, %s, '', '', '', '', '')""", [draft.pk, other.pk, organization.pk], "23503")
    assert Request.objects.get(pk=req.pk).status == "closed"
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'ww_%' AND NOT tgisinternal AND tgenabled='O'")
        assert cursor.fetchone()[0] == 13
    print("Прикладная роль: ORM/Admin/API работают; DDL, отключение guards, удаление истории, закрытые заявки и чужая организация запрещены")
