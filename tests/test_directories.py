"""Справочники, история, штатные права Admin и защита прямой записи в PostgreSQL."""
from datetime import timedelta
import csv
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from django.contrib import admin
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.utils import timezone

from workwear import services
from workwear.repeats import scan_repeats
from workwear.importers.acceptance import accept
from workwear.importers.directories_1c import parse
from workwear.models import (
    Employee, EmployeeImport, Organization, Request, RequestAssignment,
    RequestEmployee, WorkwearEvent, WorkwearItem,
)
from workwear.sync import save_draft
from .factories import make_employees, make_import, make_items, make_organization, make_user
from .test_import import make_sources


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class DirectoryTests(TestCase):
    def setUp(self):
        self.actor = make_user()
        self.client.force_login(self.actor)
        self.people = make_employees(make_import())
        self.organization = self.people[0].organization
        self.items = make_items()
        self.req = services.create_request(organization_id=self.organization.pk, actor=self.actor)

    def save(self, *, finish=False, item=None, request=None):
        request = request or self.req
        request.refresh_from_db()
        return save_draft(request_id=request.pk, actor=self.actor, expected_version=request.version,
            members=[{"employee_id": self.people[0].pk, "item_ids": [(item or self.items[0]).pk]}],
            finish=finish)

    def rejects(self, operation, constraint):
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic():
                operation()
        self.assertEqual(caught.exception.__cause__.diag.constraint_name, constraint)

    def test_new_organization_is_disabled_by_default(self):
        org = Organization.objects.create(name="Новая тестовая организация")
        self.assertFalse(org.requests_enabled)
        with self.assertRaises(ValidationError):
            services.create_request(organization_id=org.pk, actor=self.actor)
        self.rejects(lambda: Request.objects.create(organization=org, created_by=self.actor),
                     "ww_request_organization_enabled_guard")

    def test_disable_blocks_new_requests_but_preserves_existing(self):
        self.organization.requests_enabled = False
        self.organization.save()
        with self.assertRaises(ValidationError):
            services.create_request(organization_id=self.organization.pk, actor=self.actor)
        self.save(finish=True)
        metadata = self.client.get("/workwear/api/ui/metadata/").json()
        self.assertEqual(metadata["organizations"], [])
        self.assertEqual(metadata["registry_organizations"][0]["value"], self.organization.pk)
        self.assertEqual(self.client.get("/workwear/api/ui/requests/",
            {"organization_id": self.organization.pk}).json()["count"], 1)

    def test_rename_preserves_request_and_membership_names(self):
        original = self.req.snapshot_organization_name
        self.save(finish=True)
        self.organization.name = "Новое отображаемое название"
        self.organization.save()
        self.req.refresh_from_db()
        member = self.req.members.get()
        self.assertEqual(self.req.snapshot_organization_name, original)
        self.assertEqual(member.snapshot_organization_name, original)
        new = services.create_request(organization_id=self.organization.pk, actor=self.actor)
        self.save(request=new, finish=True)
        self.assertEqual(new.snapshot_organization_name, self.organization.name)
        self.assertEqual(new.members.get().organization_id, member.organization_id)

    def test_same_name_does_not_make_two_organizations_equivalent(self):
        other = Organization.objects.create(name=self.organization.name,
            external_id=str(uuid4()), requests_enabled=True)
        outsider = make_employees(self.people[0].last_seen_import, 1, other)[0]
        with self.assertRaises(ValidationError):
            services.add_employee(request_id=self.req.pk, employee_id=outsider.pk,
                actor=self.actor, expected_version=1)
        self.rejects(lambda: RequestEmployee.objects.create(request=self.req, employee=outsider,
            organization=self.organization), "ww_member_emp_org_fk")

    def test_organization_binding_is_immutable_after_initial_assignment(self):
        self.rejects(lambda: Organization.objects.filter(pk=self.organization.pk).update(external_id=str(uuid4())),
                     "ww_organization_identity_immutable")

    def test_direct_sql_cannot_include_employee_from_old_import(self):
        make_import()
        self.rejects(lambda: RequestEmployee.objects.create(request=self.req, employee=self.people[0],
            organization=self.organization),
            "ww_member_current_import_guard")

    def test_used_organization_and_item_are_protected(self):
        self.save()
        with self.assertRaises(ProtectedError):
            self.organization.delete()
        with self.assertRaises(ProtectedError):
            self.items[0].delete()

    def test_catalog_changes_preserve_assignment_snapshot_and_repeat_term(self):
        item = self.items[0]
        self.save(finish=True)
        original_name, original_category = item.name, item.category
        WorkwearItem.objects.filter(pk=item.pk).update(
            name="Новое тестовое название", category="Новая категория", validity_days=90, is_active=False)
        assignment = self.req.members.get().assignments.get()
        self.assertEqual((assignment.snapshot_item_name, assignment.snapshot_item_category,
                          assignment.snapshot_validity_days), (original_name, original_category, 365))
        data = self.client.get(f"/workwear/api/requests/{self.req.pk}/").json()["request"]
        self.assertEqual(data["members"][0]["assignments"][0]["name"], original_name)
        warning = scan_repeats(pairs={(self.people[0].pk, item.pk)},
            as_of_date=timezone.localdate() + timedelta(days=100))[(self.people[0].pk, item.pk)]
        self.assertEqual((warning.validity_days, warning.remaining_days), (365, 265))
        other = services.create_request(organization_id=self.organization.pk, actor=self.actor)
        history = self.client.get(f"/workwear/api/ui/requests/{other.pk}/employees/{self.people[0].pk}/history/")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["results"][0]["items"][0]["name"], original_name)

    def test_disabled_assignment_can_be_retained_and_removed_but_not_added(self):
        self.save()
        item = self.items[0]
        WorkwearItem.objects.filter(pk=item.pk).update(is_active=False)
        self.assertFalse(self.save().changed)
        self.req.refresh_from_db()
        with self.assertRaises(ValidationError):
            save_draft(request_id=self.req.pk, actor=self.actor, expected_version=self.req.version,
                members=[{"employee_id": self.people[0].pk, "item_ids": [item.pk]},
                         {"employee_id": self.people[1].pk, "item_ids": [item.pk]}])
        assignment = self.req.members.get().assignments.get()
        self.req.refresh_from_db()
        services.remove_assignment(request_id=self.req.pk, assignment_id=assignment.pk,
            actor=self.actor, expected_version=self.req.version)
        self.assertFalse(RequestAssignment.objects.filter(pk=assignment.pk).exists())

    def test_two_assignments_keep_individual_snapshots_after_admin_changes(self):
        self.save()
        item = self.items[0]
        self.assertEqual(self.client.post(f"/admin/workwear/workwearitem/{item.pk}/change/", {
            "code": item.code, "name": "Изменённое имя", "category": "Изменённая категория",
            "validity_days": 90, "sort_order": 0, "is_active": "on", "_save": "Сохранить",
        }).status_code, 302)
        self.req.refresh_from_db()
        members = [{"employee_id": person.pk, "item_ids": [item.pk]} for person in self.people]
        result = save_draft(request_id=self.req.pk, actor=self.actor,
            expected_version=self.req.version, members=members)
        WorkwearItem.objects.filter(pk=item.pk).update(is_active=False)
        save_draft(request_id=self.req.pk, actor=self.actor,
            expected_version=result.version, members=members, finish=True)
        data = self.client.get(f"/workwear/api/requests/{self.req.pk}/").json()["request"]
        snapshots = {m["employee_id"]: m["assignments"][0] for m in data["members"]}
        expected = [(item.name, item.category, item.validity_days),
                    ("Изменённое имя", "Изменённая категория", 90)]
        for person, values in zip(self.people, expected):
            snapshot = snapshots[person.pk]
            self.assertEqual((snapshot["name"], snapshot["category"], snapshot["validity_days"]), values)
            stored = RequestAssignment.objects.get(request_employee__request=self.req,
                request_employee__employee=person)
            self.assertEqual((stored.snapshot_item_name, stored.snapshot_item_category,
                              stored.snapshot_validity_days), values)
        metadata = self.client.get("/workwear/api/ui/metadata/").json()["items"]
        current = next(row for row in metadata if row["id"] == item.pk)
        self.assertEqual((current["name"], current["category"], current["validity_days"], current["is_active"]),
                         ("Изменённое имя", "Изменённая категория", 90, False))

    def test_postgresql_fills_snapshot_instead_of_trusting_insert_values(self):
        member = services.add_employee(request_id=self.req.pk, employee_id=self.people[0].pk,
            actor=self.actor, expected_version=1)
        assignment = RequestAssignment.objects.create(request_employee=member, item=self.items[0],
            snapshot_item_name="Подменённое имя", snapshot_validity_days=-1)
        assignment.refresh_from_db()
        self.assertEqual(assignment.snapshot_item_name, self.items[0].name)
        self.assertEqual(assignment.snapshot_validity_days, self.items[0].validity_days)
        self.rejects(lambda: RequestAssignment.objects.filter(pk=assignment.pk).update(snapshot_validity_days=1),
                     "ww_assignment_identity_immutable")

    def test_postgresql_rejects_inactive_assignment_and_changed_item_code(self):
        member = services.add_employee(request_id=self.req.pk, employee_id=self.people[0].pk,
            actor=self.actor, expected_version=1)
        WorkwearItem.objects.filter(pk=self.items[0].pk).update(is_active=False)
        self.rejects(lambda: RequestAssignment.objects.create(request_employee=member, item=self.items[0]),
                     "ww_assignment_active_guard")
        self.rejects(lambda: WorkwearItem.objects.filter(pk=self.items[0].pk).update(code="OTHER"),
                     "ww_item_identity_immutable")

    def test_terms_other_than_initial_catalog_are_supported(self):
        for days in (1, 90, 365, 730, 1095, 32767):
            item = WorkwearItem.objects.create(code=f"TERM-{days}", name=f"Тестовый срок {days}",
                category="Произвольная категория", validity_days=days)
            req = services.create_request(organization_id=self.organization.pk, actor=self.actor)
            self.save(request=req, item=item, finish=True)
            pair = (self.people[0].pk, item.pk)
            self.assertIn(pair, scan_repeats(pairs={pair},
                as_of_date=timezone.localdate() + timedelta(days=days - 1)))
            self.assertNotIn(pair, scan_repeats(pairs={pair},
                as_of_date=timezone.localdate() + timedelta(days=days)))
        self.rejects(lambda: WorkwearItem.objects.create(code="BAD", name="Тест", category="Тест", validity_days=0),
                     "ww_item_validity_ck")

    def test_new_api_uses_ids_and_rejects_name_and_invalid_ids(self):
        for value in (True, -1, 2**80, "1", [], None):
            with self.subTest(value=value):
                result = self.client.post("/workwear/api/requests/", {"organization_id": value},
                    content_type="application/json")
                self.assertEqual(result.status_code, 400)
        self.assertEqual(self.client.post("/workwear/api/requests/",
            {"organization_name": self.organization.name}, content_type="application/json").status_code, 400)
        self.organization.requests_enabled = False
        self.organization.save()
        self.assertEqual(self.client.post("/workwear/api/requests/",
            {"organization_id": self.organization.pk}, content_type="application/json").status_code, 400)


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class DirectoryAdminTests(TestCase):
    def setUp(self):
        self.actor = make_user()
        self.client.force_login(self.actor)

    def permission(self, user, *codes):
        user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="workwear", codename__in=codes))

    def test_admin_creates_catalog_and_organization(self):
        result = self.client.post("/admin/workwear/organization/add/", {
            "name": "Организация из Admin", "external_id": str(uuid4()).upper(),
            "requests_enabled": "on", "_save": "Сохранить"})
        self.assertEqual(result.status_code, 302)
        org = Organization.objects.get()
        self.assertTrue(org.requests_enabled)
        self.assertEqual(org.external_id, org.external_id.lower())
        result = self.client.post("/admin/workwear/workwearitem/add/", {
            "code": "ADMIN-NEW", "name": "Новая тестовая позиция", "category": "Новая категория",
            "validity_days": 90, "sort_order": 7, "is_active": "on", "_save": "Сохранить"})
        self.assertEqual(result.status_code, 302)
        item = WorkwearItem.objects.get()
        metadata = self.client.get("/workwear/api/ui/metadata/").json()
        self.assertEqual(metadata["organizations"][0]["value"], org.pk)
        self.assertEqual(metadata["items"][0]["id"], item.pk)

    def test_admin_rejects_duplicate_code_and_invalid_terms(self):
        make_items(1)
        for code, days in (("TEST-00", 365), ("NEW", 0), ("NEW", 32768)):
            result = self.client.post("/admin/workwear/workwearitem/add/", {
                "code": code, "name": "Тестовая позиция", "category": "Категория",
                "validity_days": days, "sort_order": 0, "is_active": "on"})
            self.assertEqual(result.status_code, 200)
        self.assertEqual(WorkwearItem.objects.count(), 1)

    def test_reader_has_readonly_admin_and_cannot_post(self):
        org = make_organization()
        item = make_items(1)[0]
        reader = make_user(administrator=False)
        reader.is_staff = True
        reader.save()
        self.permission(reader, "view_organization", "view_workwearitem")
        self.client.force_login(reader)
        for model, obj in (("organization", org), ("workwearitem", item)):
            path = f"/admin/workwear/{model}/"
            self.assertEqual(self.client.get(path).status_code, 200)
            self.assertEqual(self.client.get(f"{path}{obj.pk}/change/").status_code, 200)
            self.assertEqual(self.client.post(f"{path}{obj.pk}/change/", {"name": "Подмена"}).status_code, 403)
            self.assertEqual(self.client.post(path + "add/", {}).status_code, 403)

    def test_request_author_cannot_edit_directories(self):
        analyst = make_user(administrator=False)
        analyst.is_staff = True
        analyst.save()
        self.permission(analyst, "view_request", "add_request", "change_request")
        self.client.force_login(analyst)
        for model in ("organization", "workwearitem"):
            self.assertEqual(self.client.get(f"/admin/workwear/{model}/").status_code, 403)
            self.assertEqual(self.client.post(f"/admin/workwear/{model}/add/", {}).status_code, 403)

    def test_technical_models_are_not_editable_in_admin(self):
        for model in (Employee, EmployeeImport, Request, RequestEmployee, RequestAssignment, WorkwearEvent):
            self.assertFalse(admin.site.is_registered(model))

    def test_admin_fields_keep_stable_identifiers(self):
        org = make_organization()
        item = make_items(1)[0]
        org_form = {"name": "Новое название", "external_id": str(uuid4()), "requests_enabled": "on"}
        self.assertEqual(self.client.post(f"/admin/workwear/organization/{org.pk}/change/", org_form).status_code, 302)
        original_id = org.external_id
        org.refresh_from_db()
        self.assertEqual(org.external_id, original_id)
        self.assertEqual(org.name, "Новое название")
        result = self.client.post(f"/admin/workwear/workwearitem/{item.pk}/change/", {
            "code": "FORGED", "name": "Изменённая позиция", "category": "Категория",
            "validity_days": 1095, "sort_order": 0, "is_active": "on"})
        self.assertEqual(result.status_code, 302)
        original_code = item.code
        item.refresh_from_db()
        self.assertEqual(item.code, original_code)
        self.assertEqual(item.validity_days, 1095)


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class OrganizationImportTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.people = make_sources(self.path)
        self.exported = timezone.now() - timedelta(days=3)

    def publish(self, previous=0, *, batch=None, **kwargs):
        batch = batch or parse(self.path)
        return accept(batch, source_exported_at=self.exported + timedelta(days=bool(previous)),
            expected_employee_count=len(batch.people), expected_previous_import_id=previous, **kwargs)

    def test_unknown_organization_is_saved_once_and_not_enabled(self):
        first = self.publish()
        organization = Organization.objects.get()
        self.assertFalse(organization.requests_enabled)
        self.assertEqual(organization.external_id, parse(self.path).people[0].organization_external_id)
        self.assertTrue(self.publish().already_accepted)
        self.assertEqual(Organization.objects.count(), 1)
        self.assertEqual(Employee.objects.get().organization_id, organization.pk)
        self.assertEqual(first.created, 1)

    def test_source_rename_keeps_identity_and_display_override(self):
        first = self.publish()
        organization = Organization.objects.get()
        organization.name = "Отображаемое название администратора"
        organization.requests_enabled = True
        organization.save()
        actor = make_user()
        req = services.create_request(organization_id=organization.pk, actor=actor)
        item = make_items(1)[0]
        employee = Employee.objects.get()
        save_draft(request_id=req.pk, actor=actor, expected_version=1,
            members=[{"employee_id": employee.pk, "item_ids": [item.pk]}], finish=True)
        original_name = req.members.get().snapshot_organization_name
        make_sources(self.path, people=self.people, organization_name="Новое название источника")
        result = self.publish(first.import_id)
        organization.refresh_from_db()
        employee.refresh_from_db()
        self.assertEqual((result.created, Organization.objects.count(), Employee.objects.count()), (0, 1, 1))
        self.assertEqual(organization.name, "Отображаемое название администратора")
        self.assertEqual(organization.source_name, "Новое название источника")
        self.assertEqual(employee.organization.source_name, "Новое название источника")
        self.assertEqual(req.members.get().snapshot_organization_name, original_name)

    def test_different_external_id_cannot_move_existing_guid(self):
        first = self.publish()
        make_sources(self.path, people=self.people, organization_id=str(uuid4()))
        with self.assertRaises(ValidationError):
            self.publish(first.import_id)
        self.assertEqual(Organization.objects.count(), 1)
        self.assertEqual(EmployeeImport.objects.count(), 1)

    def test_manual_name_requires_explicit_admin_binding(self):
        organization = Organization.objects.create(name="Тестовая организация источника")
        with self.assertRaises(ValidationError):
            self.publish()
        self.assertEqual(EmployeeImport.objects.count(), 0)
        organization.external_id = parse(self.path).people[0].organization_external_id
        organization.save()
        self.publish()
        self.assertEqual(Organization.objects.count(), 1)
        self.assertEqual(Employee.objects.get().organization_id, organization.pk)

    def test_organization_external_id_is_not_assumed_to_be_uuid(self):
        make_sources(self.path, people=self.people, organization_id="Source-Company:Alpha")
        self.publish()
        self.assertEqual(Organization.objects.get().external_id, "source-company:alpha")

    def test_same_names_with_distinct_external_ids_remain_distinct(self):
        first = self.publish()
        old_person = Employee.objects.get()
        people = [*self.people, (str(uuid4()), "TEST-SECOND", "Второй тестовый сотрудник")]
        make_sources(self.path, people=people)
        employee_csv = self.path / "Employees.csv"
        rows = employee_csv.read_text().splitlines()
        other_id = str(uuid4())
        columns = rows[-1].split(";")
        columns[4] = ""  # У второго юрлица нет ссылки на подразделение первого.
        columns[5] = other_id
        rows[-1] = ";".join(columns)
        employee_csv.write_text("\n".join(rows) + "\n")
        self.publish(first.import_id)
        self.assertEqual(Organization.objects.count(), 2)
        self.assertNotEqual(Employee.objects.exclude(pk=old_person.pk).get().organization_id, old_person.organization_id)

    def test_full_admin_import_request_flow_needs_no_code_changes(self):
        actor = make_user()
        self.client.force_login(actor)
        external_id = parse(self.path).people[0].organization_external_id
        result = self.client.post("/admin/workwear/organization/add/", {
            "name": "Произвольное отображаемое название", "external_id": external_id,
            "requests_enabled": "on"})
        self.assertEqual(result.status_code, 302)
        self.publish()
        organization = Organization.objects.get()
        result = self.client.post("/admin/workwear/workwearitem/add/", {
            "code": "NEW-IMPORT", "name": "Произвольная позиция", "category": "Новая категория",
            "validity_days": 90, "sort_order": 0, "is_active": "on"})
        self.assertEqual(result.status_code, 302)
        created = self.client.post("/workwear/api/requests/", {"organization_id": organization.pk},
            content_type="application/json")
        self.assertEqual(created.status_code, 201)
        request_id = created.json()["request"]["id"]
        response = self.client.post(f"/workwear/api/ui/requests/{request_id}/sync/", {
            "expected_version": 1, "finish": True,
            "members": [{"employee_id": Employee.objects.get().pk, "item_ids": [WorkwearItem.objects.get().pk]}],
        }, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Request.objects.get().status, "closed")


class CatalogCommandTests(TestCase):
    def test_explicit_catalog_size_and_other_terms_are_supported(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "catalog.csv"
            def write(name="Произвольная позиция"):
                with source.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.writer(stream, delimiter=";")
                    writer.writerow(["code", "name", "category", "validity_days", "sort_order", "is_active"])
                    writer.writerows([[f"CLI-{i}", f"{name} {i}", "Категория", 90, i, 1] for i in range(5)])
            write()
            # Проверка контура покрывается отдельно; TestCase работает в test_veqta_dev.
            with patch("workwear.management.commands.workwear_load_catalog.require_target"):
                options = dict(environment="dev", database_name="veqta_dev", expected_count=5, stdout=StringIO())
                call_command("workwear_load_catalog", str(source), **options)
                self.assertEqual(WorkwearItem.objects.count(), 0)
                call_command("workwear_load_catalog", str(source), apply=True, **options)
                call_command("workwear_load_catalog", str(source), apply=True, **options)
                self.assertEqual(WorkwearItem.objects.count(), 5)
                self.assertEqual(set(WorkwearItem.objects.values_list("validity_days", flat=True)), {90})
                write("Изменённое имя")
                with self.assertRaises(CommandError):
                    call_command("workwear_load_catalog", str(source), apply=True, **options)
                self.assertEqual(WorkwearItem.objects.get(code="CLI-0").name, "Произвольная позиция 0")
