"""Запрещаем менять и удалять историю принятых кадровых выгрузок."""
from django.conf import settings
from django.db import migrations
from psycopg import sql

from config.database import database_role, revoke_relation_grants, verify_connection


def protect_import_history(apps, schema_editor):
    database = schema_editor.connection
    runtime = database_role(settings.VEQTA_ENV)
    verify_connection(database, database.settings_dict["NAME"], f"{runtime}_migrator")

    with database.cursor() as cursor:
        revoke_relation_grants(cursor, "workwear_employeeimport")

        cursor.execute("""
            CREATE FUNCTION public.ww_reject_import_change()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
BEGIN
    RAISE EXCEPTION
        'История принятых выгрузок неизменяема'
        USING
            ERRCODE = '23514',
            CONSTRAINT = 'ww_import_append_only';
    RETURN NULL;
END;
$$
        """)
        cursor.execute("""
            CREATE TRIGGER ww_import_no_update_delete
            BEFORE UPDATE OR DELETE ON public.workwear_employeeimport
            FOR EACH ROW
            EXECUTE FUNCTION public.ww_reject_import_change()
        """)
        cursor.execute("""
            CREATE TRIGGER ww_import_no_truncate
            BEFORE TRUNCATE ON public.workwear_employeeimport
            FOR EACH STATEMENT
            EXECUTE FUNCTION public.ww_reject_import_change()
        """)
        cursor.execute(sql.SQL(
            "REVOKE ALL ON FUNCTION public.ww_reject_import_change() FROM PUBLIC, {}"
        ).format(sql.Identifier(runtime)))
        cursor.execute(sql.SQL(
            "GRANT SELECT, INSERT ON public.workwear_employeeimport TO {}"
        ).format(sql.Identifier(runtime)))


class Migration(migrations.Migration):
    dependencies = [("workwear", "0003_event_access")]
    operations = [
        migrations.RunPython(protect_import_history, migrations.RunPython.noop),
    ]
