"""Контракт трёх справочников, идемпотентность и целостность публикации."""
import csv
from datetime import timedelta
from pathlib import Path
import tarfile
import tempfile
from uuid import uuid4
from django.core.exceptions import ValidationError
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
    """Записать только вымышленные строки с новым набором случайных GUID."""
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
    """Данные хранятся во временном каталоге и откатываемой транзакции."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.people = make_sources(self.path)
        self.date = timezone.now()-timedelta(days=2)

    def publish(self, batch=None, **kwargs):
        """Принять тестовый пакет с точным подтверждением количества и предыдущего ID."""
        batch = batch or parse(self.path)
        return accept(batch, source_exported_at=kwargs.pop("source_exported_at", self.date),
                      expected_employee_count=len(batch.people),
                      expected_previous_import_id=kwargs.pop("previous", 0), **kwargs)

    def test_parse_and_accept(self):
        batch = parse(self.path)
        result = self.publish(batch)
        self.assertEqual((result.created, result.current), (1, 1))
        self.assertEqual(Employee.objects.get().personnel_number, "TEST-00001")
        self.assertIsNone(EmployeeImport.objects.get().coverage_start)

    def test_tar_and_directory_have_same_hashes(self):
        target = self.path/"source.tar.gz"
        with tarfile.open(target, "w:gz") as archive:
            for name in ("Employees.csv", "Positions.csv", "Units.csv"):
                archive.add(self.path/name, arcname=name)
        self.assertEqual(parse(target), parse(self.path))

    def test_same_package_is_idempotent(self):
        self.publish()
        self.assertTrue(self.publish().already_accepted)
        self.assertEqual(EmployeeImport.objects.count(), 1)

    def test_replay_with_changed_date_rejected(self):
        self.publish()
        with self.assertRaises(ValidationError):
            self.publish(source_exported_at=self.date+timedelta(hours=1))

    def test_publication_requires_count_and_previous(self):
        batch = parse(self.path)
        with self.assertRaises(ValidationError):
            accept(batch, source_exported_at=self.date)
        self.assertEqual(EmployeeImport.objects.count(), 0)

    def test_correction_keeps_guid_identity(self):
        first = self.publish()
        make_sources(self.path, people=[(self.people[0][0], "TEST-UPDATED", "Тестовый сотрудник обновлён")])
        self.publish(source_exported_at=self.date+timedelta(days=1), previous=first.import_id)
        self.assertEqual(Employee.objects.count(), 1)
        self.assertEqual(Employee.objects.get().personnel_number, "TEST-UPDATED")

    def test_missing_people_require_digest_and_are_preserved(self):
        first = self.publish()
        make_sources(self.path)
        batch = parse(self.path)
        with self.assertRaises(ValidationError):
            self.publish(batch, source_exported_at=self.date+timedelta(days=1), previous=first.import_id)
        checked = review(batch)
        result = self.publish(batch, source_exported_at=self.date+timedelta(days=1), previous=first.import_id,
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

    def test_old_timesheet_arguments_rejected(self):
        with self.assertRaises(ImportRejected):
            parse(self.path, timesheets="unsupported")

    def test_commands_refuse_wrong_environment_before_parsing_sources(self):
        for command in ("veqta_initialize", "workwear_import_1c", "workwear_load_catalog"):
            with self.subTest(command=command), self.assertRaises(CommandError):
                args = [] if command == "veqta_initialize" else ["missing-source"]
                call_command(command, *args, environment="prod", database_name="veqta_dev")
