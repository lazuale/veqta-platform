"""Проверяем формат справочников и атомарность импорта."""
import csv
from datetime import timedelta
from pathlib import Path
import tarfile
import tempfile
from unittest.mock import patch
from uuid import uuid4
from django.core.exceptions import ValidationError
from django.db import connection
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from workwear.importers.directories_1c import parse, ImportRejected
from workwear.importers.acceptance import accept, review
from workwear.models import Employee, EmployeeImport


def make_sources(directory, *, people=None,
                 organization_id="00000000-0000-0000-0000-000000000123",
                 organization_name="Тестовая организация источника"):
    position, unit, organization = str(uuid4()), str(uuid4()), organization_id
    people = people or [(str(uuid4()), "TEST-00001", "Тестовый сотрудник 00001")]
    records = {
        "Positions.csv": [["extId", "name"], [position, "Тестовая должность"]],
        "Units.csv": [["extId", "name", "orgExtId", "orgName"], [unit, "Тестовое подразделение", organization, organization_name]],
        "Employees.csv": [["extId", "tabNumber", "name", "positionExtId", "unitExtId", "orgExtId", "orgName"]]+
            [[uid, number, name, position, unit, organization, organization_name] for uid, number, name in people],
    }
    for name, rows in records.items():
        with (Path(directory)/name).open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream, delimiter=";").writerows(rows)
    return people


class ImportTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.people = make_sources(self.path)
        self.date = timezone.localdate(timezone=timezone.get_default_timezone()) - timedelta(days=2)

    def publish(self, batch=None, **kwargs):
        batch = batch or parse(self.path)
        return accept(batch, source_export_date=kwargs.pop("source_export_date", self.date),
                      expected_employee_count=len(batch.people),
                      expected_previous_import_id=kwargs.pop("previous", 0), **kwargs)

    def test_parse_and_accept(self):
        batch = parse(self.path)
        result = self.publish(batch)
        self.assertEqual((result.created, result.current), (1, 1))
        self.assertEqual(Employee.objects.get().personnel_number, "TEST-00001")
        self.assertEqual(EmployeeImport.objects.get().source_export_date, self.date)

    def test_tar_and_directory_have_same_hashes(self):
        target = self.path/"source.tar.gz"
        with tarfile.open(target, "w:gz") as archive:
            for name in ("Employees.csv", "Positions.csv", "Units.csv"):
                archive.add(self.path/name, arcname=name)
        self.assertEqual(parse(target), parse(self.path))

    @patch("workwear.importers.directories_1c.MAX_ROWS", 1)
    def test_csv_row_limit_excludes_header(self):
        self.assertEqual(parse(self.path).source_row_count, 1)
        make_sources(self.path, people=self.people * 2)
        with self.assertRaisesRegex(ImportRejected, r"Employees\.csv:3: invalid CSV row"):
            parse(self.path)

    def test_same_package_is_idempotent(self):
        self.publish()
        self.assertTrue(self.publish().already_accepted)
        self.assertEqual(EmployeeImport.objects.count(), 1)

    def test_replay_with_changed_date_rejected(self):
        self.publish()
        with self.assertRaises(ValidationError):
            self.publish(source_export_date=self.date+timedelta(days=1))

    def test_cli_requires_strict_calendar_day(self):
        for value in ("2026-11-18T00:00:00+03:00", "18.11.2026", "20261118", "2026-02-30"):
            with self.subTest(value=value), self.assertRaises(CommandError):
                call_command("workwear_import_1c", str(self.path),
                    environment="dev", database_name="veqta_dev", source_export_date=value)
        self.assertEqual(EmployeeImport.objects.count(), 0)

    def test_service_rejects_timestamp_instead_of_date(self):
        with self.assertRaises(ValidationError):
            self.publish(source_export_date=timezone.now())
        self.assertEqual(EmployeeImport.objects.count(), 0)

    def test_new_corrected_snapshot_in_same_day(self):
        first = self.publish()
        make_sources(self.path, people=[(
            self.people[0][0], "TEST-CHANGED", self.people[0][2])])
        result = self.publish(previous=first.import_id)
        self.assertEqual(result.updated, 1)
        self.assertFalse(result.already_accepted)
        self.assertEqual(EmployeeImport.objects.count(), 2)

    def test_native_date_column_in_coldstart_schema(self):
        self.assertEqual(
            EmployeeImport._meta.get_field("source_export_date").get_internal_type(),
            "DateField",
        )
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT data_type FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'workwear_employeeimport'
                  AND column_name = 'source_export_date'
            """)
            self.assertEqual(cursor.fetchone(), ("date",))
        self.assertNotIn(
            "source_exported_at",
            [field.name for field in EmployeeImport._meta.fields],
        )

    def test_older_calendar_day_rejected(self):
        first = self.publish()
        make_sources(self.path, people=[(
            self.people[0][0], "TEST-CHANGED", self.people[0][2])])
        with self.assertRaises(ValidationError):
            self.publish(source_export_date=self.date-timedelta(days=1),
                         previous=first.import_id)

    def test_publication_requires_count_and_previous(self):
        batch = parse(self.path)
        with self.assertRaises(ValidationError):
            accept(batch, source_export_date=self.date)
        self.assertEqual(EmployeeImport.objects.count(), 0)

    def test_correction_keeps_guid_identity(self):
        first = self.publish()
        make_sources(self.path, people=[(self.people[0][0], "TEST-UPDATED", "Тестовый сотрудник обновлён")])
        self.publish(source_export_date=self.date+timedelta(days=1), previous=first.import_id)
        self.assertEqual(Employee.objects.count(), 1)
        self.assertEqual(Employee.objects.get().personnel_number, "TEST-UPDATED")

    def test_missing_people_require_digest_and_are_preserved(self):
        first = self.publish()
        make_sources(self.path)
        batch = parse(self.path)
        with self.assertRaises(ValidationError):
            self.publish(batch, source_export_date=self.date+timedelta(days=1), previous=first.import_id)
        checked = review(batch)
        result = self.publish(batch, source_export_date=self.date+timedelta(days=1), previous=first.import_id,
                              confirmed_missing_sha256=checked.missing_current_sha256)
        self.assertEqual(result.historical_preserved, 1)
        self.assertEqual(Employee.objects.count(), 2)

    def test_invalid_uuid_and_orphan_rejected_before_writes(self):
        path = self.path/"Employees.csv"
        path.write_text(path.read_text().replace(self.people[0][0], "invalid-guid"))
        with self.assertRaises(ImportRejected):
            parse(self.path)
        self.assertEqual(EmployeeImport.objects.count(), 0)

    def test_conflicting_duplicate_rejected(self):
        make_sources(self.path, people=[self.people[0], (self.people[0][0], "TEST-CONFLICT", self.people[0][2])])
        with self.assertRaises(ImportRejected):
            parse(self.path)

    def test_unsafe_archive_rejected(self):
        target = self.path/"source.tar.gz"
        with tarfile.open(target, "w:gz") as archive:
            item = tarfile.TarInfo("Employees.csv")
            item.type = tarfile.SYMTYPE
            item.linkname = "/etc/passwd"
            archive.addfile(item)
        with self.assertRaises(ImportRejected):
            parse(target)

    def test_parser_rejects_arguments_outside_its_contract(self):
        with self.assertRaises(TypeError):
            parse(self.path, timesheets="unsupported")

    def test_commands_refuse_wrong_environment_before_parsing_sources(self):
        for command in ("veqta_initialize", "workwear_import_1c", "workwear_load_catalog"):
            with self.subTest(command=command), self.assertRaises(CommandError):
                args = [] if command == "veqta_initialize" else ["missing-source"]
                call_command(command, *args, environment="prod", database_name="veqta_dev")
