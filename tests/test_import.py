"""Проверяем формат справочников и атомарность импорта."""
import csv
from dataclasses import replace
import json
from io import StringIO
from datetime import timedelta
from pathlib import Path
import tarfile
import tempfile
from unittest.mock import patch
from uuid import uuid4
from django.core.exceptions import ValidationError
from django.db import connection, IntegrityError
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
        with self.assertRaisesRegex(ImportRejected, r"Employees\.csv:3: некорректная строка CSV"):
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
        with patch("workwear.management.commands.workwear_import_1c.require_target"):
            for value in ("2026-11-18T00:00:00+03:00", "18.11.2026", "20261118", "2026-02-30"):
                with self.subTest(value=value), self.assertRaisesRegex(CommandError, r"Дата выгрузки|Некорректная дата"):
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
        with self.assertRaisesRegex(ImportRejected, "неверный UUID"):
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

    def test_command_dry_run_apply_and_input_guards(self):
        options = dict(environment="dev", database_name="veqta_dev",
                       source_export_date=self.date.isoformat())
        archive = self.path / "sources.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            for name in ("Employees.csv", "Positions.csv", "Units.csv"):
                tar.add(self.path / name, arcname=name)
        with patch("workwear.management.commands.workwear_import_1c.require_target"):
            output = StringIO()
            call_command("workwear_import_1c", str(self.path), stdout=output, **options)
            result = json.loads(output.getvalue())
            self.assertEqual(result["mode"], "DRY_RUN")
            self.assertEqual(result["unique_current_employees"], 1)
            self.assertEqual(EmployeeImport.objects.count(), 0)

            with self.assertRaisesRegex(CommandError, "APPLY requires"):
                call_command("workwear_import_1c", str(self.path), apply=True, **options)
            with self.assertRaises(CommandError):
                call_command("workwear_import_1c", str(self.path / "missing-source"), **options)
            output = StringIO()
            call_command("workwear_import_1c", str(archive), apply=True,
                         expected_employee_count=1, expected_previous_import_id=0,
                         stdout=output, **options)
            applied = json.loads(output.getvalue())
            self.assertEqual(applied["mode"], "APPLY")
            self.assertEqual(applied["acceptance"]["created"], 1)
            self.assertEqual(EmployeeImport.objects.count(), 1)

    def test_parser_rejects_invalid_csv_headers_and_encoding(self):
        file = self.path / "Positions.csv"
        original = file.read_bytes()
        for replacement in (b"\xff\xfe", b"extId;wrong\n", b"", b"extId;name\n"):
            with self.subTest(data=replacement):
                file.write_bytes(replacement)
                with self.assertRaises(ImportRejected):
                    parse(self.path)
        file.write_bytes(original)
        self.assertEqual(len(parse(self.path).people), 1)

    def test_parser_rejects_bad_organization_and_duplicate_position(self):
        source = self.path / "Units.csv"
        original = source.read_text()
        for changed in (original.replace("Тестовое подразделение", ""),
                        original.replace("00000000-0000-0000-0000-000000000123", "bad id")):
            with self.subTest(changed=changed[:20]):
                source.write_text(changed)
                with self.assertRaises(ImportRejected):
                    parse(self.path)
        source.write_text(original)
        positions = self.path / "Positions.csv"
        with positions.open("a", encoding="utf-8") as target:
            target.write(positions.read_text().splitlines()[1].replace("Тестовая должность", "Другая должность") + "\n")
        with self.assertRaisesRegex(ImportRejected, "разные названия должности"):
            parse(self.path)

    def test_parser_rejects_invalid_employee_attributes_and_links(self):
        source = self.path / "Employees.csv"
        with source.open(encoding="utf-8", newline="") as stream:
            original = list(csv.reader(stream, delimiter=";"))
        for field, value in (
            ("name", ""), ("tabNumber", ""),
            ("orgExtId", ""), ("orgName", ""),
            ("positionExtId", str(uuid4())), ("unitExtId", str(uuid4())),
            ("orgExtId", str(uuid4())), ("tabNumber", "N" * 65),
        ):
            with self.subTest(field=field, value=value[:20]):
                rows = [row.copy() for row in original]
                rows[1][rows[0].index(field)] = value
                with source.open("w", encoding="utf-8", newline="") as stream:
                    csv.writer(stream, delimiter=";").writerows(rows)
                with self.assertRaises(ImportRejected):
                    parse(self.path)
        with source.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream, delimiter=";").writerows(original)
        self.assertEqual(len(parse(self.path).people), 1)

    def test_duplicate_rows_and_complementary_employee_records(self):
        for name in ("Positions.csv", "Units.csv"):
            path = self.path / name
            rows = list(csv.reader(path.open(encoding="utf-8"), delimiter=";"))
            with path.open("a", encoding="utf-8", newline="") as stream:
                csv.writer(stream, delimiter=";").writerow(rows[1])
        employees = self.path / "Employees.csv"
        rows = list(csv.reader(employees.open(encoding="utf-8"), delimiter=";"))
        first, second = rows[1].copy(), rows[1].copy()
        first[rows[0].index("unitExtId")] = ""
        second[rows[0].index("positionExtId")] = ""
        with employees.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream, delimiter=";").writerows([rows[0], first, second])
        batch = parse(self.path)
        self.assertEqual(len(batch.people), 1)
        for key in ("duplicate_position_rows", "duplicate_unit_rows",
                    "duplicate_employee_rows", "partial_employee_merges"):
            self.assertEqual(batch.anomalies[key], 1)

    def test_conflicting_organization_and_unit_name_rejected(self):
        source = self.path / "Units.csv"
        original = source.read_text(encoding="utf-8")
        with self.subTest("организация"):
            source.write_text(original.replace("Тестовая организация источника",
                                               "Несовпадающая организация"), encoding="utf-8")
            with self.assertRaises(ImportRejected):
                parse(self.path)
        source.write_text(original, encoding="utf-8")
        rows = list(csv.reader(source.open(encoding="utf-8"), delimiter=";"))
        conflict = rows[1].copy()
        conflict[rows[0].index("name")] = "Другой участок"
        with source.open("a", encoding="utf-8", newline="") as stream:
            csv.writer(stream, delimiter=";").writerow(conflict)
        with self.assertRaises(ImportRejected):
            parse(self.path)

    def test_missing_unit_organization_is_observed_without_guessing(self):
        source = self.path / "Units.csv"
        rows = list(csv.reader(source.open(encoding="utf-8"), delimiter=";"))
        rows[1][rows[0].index("orgExtId")] = ""
        with source.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream, delimiter=";").writerows(rows)
        batch = parse(self.path)
        self.assertEqual(batch.people[0].department_name, "")
        self.assertEqual(batch.anomalies["unit_without_organization"], 1)

    def test_missing_files_size_and_archive_whitelist(self):
        source = self.path / "Positions.csv"
        original = source.read_bytes()
        source.unlink()
        with self.assertRaises(ImportRejected):
            parse(self.path)
        source.write_bytes(original)
        with patch("workwear.importers.directories_1c.MAX_FILE_BYTES", 4):
            with self.assertRaises(ImportRejected):
                parse(self.path)
        target = self.path / "incomplete.tar.gz"
        with tarfile.open(target, "w:gz") as archive:
            archive.add(self.path / "Positions.csv", arcname="Positions.csv")
            archive.add(self.path / "Units.csv", arcname="Units.csv")
            archive.add(self.path / "Employees.csv", arcname="unexpected.csv")
        with self.assertRaises(ImportRejected):
            parse(target)

    def test_empty_csv_and_bad_quoting(self):
        for name in ("Units.csv", "Employees.csv"):
            source = self.path / name
            original = source.read_text(encoding="utf-8")
            header = original.splitlines()[0]
            source.write_text(header + "\n", encoding="utf-8")
            with self.assertRaises(ImportRejected):
                parse(self.path)
            source.write_text(original, encoding="utf-8")
        source = self.path / "Positions.csv"
        original = source.read_text(encoding="utf-8")
        source.write_text('extId;name\n"unclosed;field\n', encoding="utf-8")
        with self.assertRaises(ImportRejected):
            parse(self.path)
        source.write_text(original, encoding="utf-8")
    def test_accept_rejects_malformed_batch_and_stale_approval(self):
        batch = parse(self.path)
        for malformed in (
            replace(batch, people=()),
            replace(batch, people=batch.people * 2),
            replace(batch, people=(replace(batch.people[0],
                    organization_external_id="unknown"),)),
        ):
            with self.subTest(malformed=malformed.people), self.assertRaises(ValidationError):
                self.publish(malformed)
        with self.assertRaises(ValidationError):
            self.publish(batch, source_export_date=timezone.localdate() + timedelta(days=1))
        with self.assertRaises(ValidationError):
            self.publish(batch, previous=777777)
        self.assertEqual(EmployeeImport.objects.count(), 0)

        first = self.publish(batch)
        make_sources(self.path, people=[(
            self.people[0][0], "TEST-UPDATED", self.people[0][2])])
        latest = self.publish(previous=first.import_id)
        with self.assertRaisesRegex(ValidationError, "Старая выгрузка"):
            self.publish(batch, previous=latest.import_id)
        self.assertEqual(EmployeeImport.objects.count(), 2)

    def test_import_cli_converts_validation_and_integrity_errors(self):
        archive = self.path / "source.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            for name in ("Employees.csv", "Positions.csv", "Units.csv"):
                tar.add(self.path / name, arcname=name)
        batch = parse(self.path)
        arguments = {
            "environment": "dev", "database_name": "veqta_dev",
            "source_export_date": self.date.isoformat(), "apply": True,
            "expected_employee_count": 1, "expected_previous_import_id": 0,
        }
        with patch("workwear.management.commands.workwear_import_1c.require_target"), \
             patch("workwear.management.commands.workwear_import_1c.parse", return_value=batch), \
             patch("workwear.management.commands.workwear_import_1c.accept") as publish:
            publish.side_effect = ValidationError("Проверка пакета")
            with self.assertRaisesRegex(CommandError, "Проверка пакета"):
                call_command("workwear_import_1c", str(archive), **arguments)
            publish.side_effect = IntegrityError("Ошибка ограничения")
            with self.assertRaisesRegex(CommandError, "Публикация полностью отменена"):
                call_command("workwear_import_1c", str(archive), **arguments)
        self.assertEqual(EmployeeImport.objects.count(), 0)

    def test_parser_rejects_oversized_organization_name_and_missing_position(self):
        source = self.path / "Units.csv"
        original = source.read_text(encoding="utf-8")
        source.write_text(original.replace("Тестовая организация источника",
                                           "Я" * 256), encoding="utf-8")
        with self.assertRaises(ImportRejected):
            parse(self.path)
        source.write_text(original, encoding="utf-8")
        employee_source = self.path / "Employees.csv"
        rows = list(csv.reader(employee_source.open(encoding="utf-8"), delimiter=";"))
        rows[1][rows[0].index("positionExtId")] = ""
        with employee_source.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream, delimiter=";").writerows(rows)
        batch = parse(self.path)
        self.assertEqual(batch.people[0].position_name, "")
        self.assertEqual(batch.anomalies["employees_without_position"], 1)

    def test_tar_member_length_is_checked_before_reading(self):
        archive = self.path / "source.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            for name in ("Employees.csv", "Positions.csv", "Units.csv"):
                tar.add(self.path / name, arcname=name)
        with patch("workwear.importers.directories_1c.MAX_FILE_BYTES", 4):
            with self.assertRaises(ImportRejected):
                parse(archive)

    def test_count_discrepancy_aborts_the_entire_import(self):
        batch = parse(self.path)
        with patch("workwear.importers.acceptance.Employee.objects.filter") as filtered:
            filtered.return_value.count.return_value = 0
            with self.assertRaisesRegex(ValidationError, "Количество сотрудников"):
                self.publish(batch)
        self.assertEqual(EmployeeImport.objects.count(), 0)
        self.assertEqual(Employee.objects.count(), 0)

    def test_empty_position_name_is_rejected(self):
        source = self.path / "Positions.csv"
        rows = list(csv.reader(source.open(encoding="utf-8"), delimiter=";"))
        rows[1][rows[0].index("name")] = ""
        with source.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream, delimiter=";").writerows(rows)
        with self.assertRaisesRegex(ImportRejected, "не указана должность"):
            parse(self.path)

    def test_identical_employee_rows_are_not_partial_merges(self):
        source = self.path / "Employees.csv"
        rows = list(csv.reader(source.open(encoding="utf-8"), delimiter=";"))
        with source.open("a", encoding="utf-8", newline="") as stream:
            csv.writer(stream, delimiter=";").writerow(rows[1])
        result = parse(self.path)
        self.assertEqual(len(result.people), 1)
        self.assertEqual(result.anomalies["duplicate_employee_rows"], 1)
        self.assertEqual(result.anomalies["partial_employee_merges"], 0)

    def test_commands_refuse_wrong_environment_before_parsing_sources(self):
        for command in ("veqta_initialize", "workwear_import_1c", "workwear_load_catalog"):
            with self.subTest(command=command), self.assertRaises(CommandError):
                args = [] if command == "veqta_initialize" else ["missing-source"]
                call_command(command, *args, environment="prod", database_name="veqta_dev")
