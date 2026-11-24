"""Переводим сообщения защитных функций на русский без изменения их логики."""
from django.conf import settings
from django.db import migrations

from config.database import database_role, verify_connection


REPLACEMENTS = {
    "ww_guard_employee_identity": (
        ("Employee identity is immutable", "Идентичность сотрудника неизменяема"),
    ),
    "ww_guard_request_identity": (
        ("Request identity and origin are immutable", "Идентичность и источник заявки неизменяемы"),
    ),
    "ww_reject_event_change": (
        ("Workwear event history is append-only", "История событий спецодежды доступна только для добавления"),
    ),
    "ww_guard_member_write": (
        ("Employee is absent from the latest accepted roster", "Сотрудник отсутствует в последнем принятом справочнике"),
        ("Membership identity is immutable", "Состав заявки нельзя менять после включения сотрудника"),
        ("Parent request does not exist", "Заявка не найдена"),
        ("Closed request membership cannot be modified", "Состав завершённой заявки нельзя менять"),
    ),
    "ww_guard_assignment_write": (
        ("Assignment identity is immutable", "Назначение нельзя менять после сохранения"),
        ("Parent membership does not exist", "Сотрудник не найден в заявке"),
        ("Parent request does not exist", "Заявка не найдена"),
        ("Closed request assignment cannot be modified", "Назначения завершённой заявки нельзя менять"),
        ("Unknown catalog item", "Позиция спецодежды не найдена"),
        ("Inactive item cannot be assigned", "Нельзя назначить неактивную позицию спецодежды"),
    ),
    "ww_guard_request_lifecycle": (
        ("Closed request cannot be deleted", "Завершённую заявку нельзя удалить"),
        ("Closed request cannot be edited", "Завершённую заявку нельзя изменять"),
        ("Request version cannot decrease", "Версия заявки не может уменьшаться"),
        ("Status transition must increment version", "При смене состояния версия заявки должна увеличиваться"),
        ("Request has no assignments", "В заявке нет назначений"),
        ("Request members are incomplete", "В заявке есть сотрудники без назначений"),
    ),
    "ww_reject_structure_truncate": (
        ("Workwear structural TRUNCATE is forbidden", "Очистка защищённых таблиц Workwear запрещена"),
    ),
    "ww_guard_request_insert": (
        ("New requests must start as drafts", "Новая заявка должна создаваться черновиком"),
        ("New requests must start at version 1", "Новая заявка должна начинаться с версии 1"),
        ("Organization is not enabled for new requests", "Для организации запрещено создавать новые заявки"),
    ),
    "ww_guard_organization_identity": (
        ("Organization identity is immutable", "Идентификатор организации нельзя менять"),
    ),
    "ww_guard_item_identity": (
        ("Item identity is immutable", "Идентификатор позиции спецодежды нельзя менять"),
    ),
}


def translate_messages(apps, schema_editor):
    database = schema_editor.connection
    runtime = database_role(settings.VEQTA_ENV)
    verify_connection(database, database.settings_dict["NAME"], f"{runtime}_migrator")

    with database.cursor() as cursor:
        names = sorted(REPLACEMENTS)
        cursor.execute("""
            SELECT p.proname, p.prosrc, pg_get_functiondef(p.oid)
            FROM pg_proc AS p
            WHERE p.pronamespace = 'public'::regnamespace
              AND p.proname = ANY(%s)
        """, [names])
        functions = {
            name: (body, definition)
            for name, body, definition in cursor.fetchall()
        }

        for name, messages in REPLACEMENTS.items():
            if name not in functions:
                raise RuntimeError(f"Не найдена защитная функция public.{name}")

            body, definition = functions[name]
            translated = body
            for source, target in messages:
                if translated.count(source) != 1:
                    raise RuntimeError(f"Не найдено исходное сообщение в public.{name}: {source}")
                translated = translated.replace(source, target)

            if definition.count(body) != 1:
                raise RuntimeError(f"Не удалось однозначно обновить public.{name}")
            cursor.execute(definition.replace(body, translated))


class Migration(migrations.Migration):
    dependencies = [("workwear", "0004_import_history_append_only")]
    operations = [
        migrations.RunPython(translate_messages, migrations.RunPython.noop),
    ]
