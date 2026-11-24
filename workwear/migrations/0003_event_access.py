"""Оставляем прикладной роли чтение и добавление событий журнала."""
from django.conf import settings
from django.db import migrations
from psycopg import sql

from config.database import database_role, revoke_relation_grants, verify_connection


def restrict_event_access(apps, schema_editor):
    database = schema_editor.connection
    runtime = database_role(settings.VEQTA_ENV)
    verify_connection(database, database.settings_dict["NAME"], f"{runtime}_migrator")
    with database.cursor() as cursor:
        revoke_relation_grants(cursor, "workwear_workwearevent")
        cursor.execute(sql.SQL("GRANT SELECT, INSERT ON public.workwear_workwearevent TO {}").format(
            sql.Identifier(runtime),
        ))


class Migration(migrations.Migration):
    dependencies = [("workwear", "0002_database_acl")]
    operations = [migrations.RunPython(restrict_event_access, migrations.RunPython.noop)]
