"""Закрепляем права защищённых объектов, в том числе после ранее применённой 0001."""
from django.db import migrations
from psycopg import sql


def restrict_runtime_access(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), session_user, current_user")
        database, login, role = cursor.fetchone()
        if database in {"veqta_dev", "test_veqta_dev"}:
            runtime = "svc_platform_dev"
        elif database == "veqta_prod":
            runtime = "svc_platform_prod"
        else:
            raise RuntimeError("Права VEQTA применяются только в базе выбранного контура")
        if login != role or role != f"{runtime}_migrator":
            raise RuntimeError("Изменение прав VEQTA требует прямого подключения Migrator")

        cursor.execute("""
            SELECT bool_and(relowner = current_user::regrole)
            FROM pg_class WHERE oid IN (
                'public.django_migrations'::regclass, 'public.workwear_workwearevent'::regclass
            )
        """)
        if cursor.fetchone() != (True,):
            raise RuntimeError("Доступ к журналам должен настраивать их владелец")

        cursor.execute(sql.SQL("""
            REVOKE ALL ON public.django_migrations, public.workwear_workwearevent FROM PUBLIC, {runtime};
            GRANT SELECT, INSERT ON public.workwear_workwearevent TO {runtime};
            REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC;
            REVOKE SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public FROM {runtime};
            REVOKE ALL ON SEQUENCE public.django_migrations_id_seq FROM {runtime};
            ALTER DEFAULT PRIVILEGES IN SCHEMA public
                REVOKE SELECT, UPDATE ON SEQUENCES FROM {runtime};
            ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
            ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM {runtime};
            REVOKE EXECUTE ON FUNCTION
                public.ww_guard_employee_identity(), public.ww_guard_request_identity(),
                public.ww_reject_event_change(), public.ww_guard_member_write(),
                public.ww_guard_assignment_write(), public.ww_guard_request_lifecycle(),
                public.ww_reject_structure_truncate(), public.ww_guard_request_insert(),
                public.ww_guard_organization_identity(), public.ww_guard_item_identity()
                FROM PUBLIC, {runtime};
        """).format(runtime=sql.Identifier(runtime)))


class Migration(migrations.Migration):
    dependencies = [("workwear", "0001_initial")]
    operations = [migrations.RunPython(restrict_runtime_access, migrations.RunPython.noop)]
