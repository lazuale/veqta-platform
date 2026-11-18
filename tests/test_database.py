"""Проверяем ограничения PostgreSQL в обход прикладных сервисов."""
from uuid import uuid4
from django.db import connection, IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from workwear import services
from workwear.models import Employee, Request, RequestAssignment, RequestEmployee, WorkwearEvent
from workwear.sync import save_draft
from .factories import make_user, make_import, make_employees, make_items, make_organization


class DatabaseGuardsTests(TestCase):
    """Savepoint позволяет продолжать тест после ожидаемой ошибки БД."""
    def setUp(self):
        self.actor, self.snapshot = make_user(), make_import()
        self.people = make_employees(self.snapshot)
        self.items = make_items()
        self.req = services.create_request(organization_id=make_organization().pk, actor=self.actor)
        save_draft(request_id=self.req.pk, actor=self.actor, expected_version=1,
                   members=[{"employee_id": self.people[0].pk, "item_ids": [self.items[0].pk]}])
        self.req.refresh_from_db()
        self.member = self.req.members.get()
        self.assignment = self.member.assignments.get()

    def rejected(self, operation, constraint=None):
        """Проверяем ограничение самой БД, а не проверку Django."""
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic():
                operation()
        if constraint:
            self.assertEqual(caught.exception.__cause__.diag.constraint_name, constraint)

    def sql(self, sql):
        with connection.cursor() as cursor:
            cursor.execute(sql)

    def close(self):
        services.complete_request(request_id=self.req.pk, actor=self.actor, expected_version=self.req.version)
        self.req.refresh_from_db()

    def test_employee_guid_and_organization_immutable(self):
        for change in ({"employee_guid": uuid4()}, {"organization_id": make_organization("Тестовая организация Б").pk}):
            self.rejected(lambda: Employee.objects.filter(pk=self.people[0].pk).update(**change), "ww_employee_identity_immutable")

    def test_personnel_number_is_editable(self):
        Employee.objects.filter(pk=self.people[0].pk).update(personnel_number="TEST-UPDATED")
        self.assertEqual(Employee.objects.get(pk=self.people[0].pk).personnel_number, "TEST-UPDATED")

    def test_composite_employee_organization_fk(self):
        other = make_employees(self.snapshot, 1, make_organization("Тестовая организация Б"))[0]
        self.rejected(lambda: RequestEmployee.objects.create(request=self.req, employee=other,
                      organization_id=make_organization().pk), "ww_member_emp_org_fk")

    def test_composite_request_organization_fk(self):
        other = make_employees(self.snapshot, 1, make_organization("Тестовая организация Б"))[0]
        self.rejected(lambda: RequestEmployee.objects.create(request=self.req, employee=other,
                      organization_id=make_organization("Тестовая организация Б").pk), "ww_member_req_org_fk")

    def test_request_identity_immutable(self):
        self.rejected(lambda: Request.objects.filter(pk=self.req.pk).update(created_by=make_user()), "ww_request_identity_immutable")

    def test_member_cannot_be_reassigned(self):
        self.rejected(lambda: RequestEmployee.objects.filter(pk=self.member.pk).update(employee=self.people[1]), "ww_member_identity_immutable")

    def test_assignment_cannot_be_retargeted(self):
        self.rejected(lambda: RequestAssignment.objects.filter(pk=self.assignment.pk).update(item=self.items[1]), "ww_assignment_identity_immutable")

    def test_events_cannot_be_updated_or_deleted(self):
        event = WorkwearEvent.objects.first()
        self.rejected(lambda: WorkwearEvent.objects.filter(pk=event.pk).update(details={}), "ww_event_append_only")
        self.rejected(lambda: WorkwearEvent.objects.filter(pk=event.pk).delete(), "ww_event_append_only")

    def test_truncate_forbidden_including_cascade(self):
        # Проверяем отложенные внешние ключи до триггера TRUNCATE.
        self.sql("SET CONSTRAINTS ALL IMMEDIATE")
        for table in ("workwear_request", "workwear_requestemployee", "workwear_requestassignment", "workwear_workwearevent"):
            with self.subTest(table=table):
                self.rejected(lambda: self.sql(f"TRUNCATE {table} CASCADE"),
                              "ww_event_append_only" if table.endswith("workwearevent") else "ww_structure_no_truncate")

    def test_initial_closed_request_forbidden(self):
        self.rejected(lambda: Request.objects.create(organization_id=make_organization().pk, status="closed",
                      created_by=self.actor, completed_by=self.actor, completed_at=timezone.now()), "ww_request_initial_draft_guard")

    def test_status_change_requires_version_increment(self):
        self.rejected(lambda: Request.objects.filter(pk=self.req.pk).update(status="closed", completed_at=timezone.now(),
                      completed_by=self.actor), "ww_request_transition_version_guard")

    def test_member_snapshot_is_filled_and_cannot_be_rewritten(self):
        self.assertEqual(self.member.snapshot_full_name, self.people[0].full_name)
        self.rejected(lambda: RequestEmployee.objects.filter(pk=self.member.pk).update(snapshot_personnel_number=""),
                      "ww_member_identity_immutable")

    def test_member_insert_does_not_trust_client_snapshot(self):
        member = RequestEmployee.objects.create(request=self.req, employee=self.people[1],
            organization_id=self.req.organization_id, snapshot_full_name="Подменённое имя",
            snapshot_personnel_number="Подменённый номер", snapshot_organization_name="Подменённое название")
        member.refresh_from_db()
        self.assertEqual(member.snapshot_full_name, self.people[1].full_name)
        self.assertEqual(member.snapshot_personnel_number, self.people[1].personnel_number)
        self.assertEqual(member.snapshot_organization_name, self.people[1].organization.name)

    def test_employee_requires_a_source_import(self):
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic():
                Employee.objects.create(employee_guid=uuid4(), full_name="Тестовый сотрудник без источника",
                    personnel_number="TEST-NO-SOURCE", organization_id=self.req.organization_id)
        self.assertEqual(caught.exception.__cause__.diag.column_name, "last_seen_import_id")

    def test_version_cannot_decrease(self):
        self.rejected(lambda: Request.objects.filter(pk=self.req.pk).update(version=1),
                      "ww_request_version_monotonic_guard")

    def test_initial_version_must_be_one(self):
        self.rejected(lambda: Request.objects.create(organization_id=self.req.organization_id,
            created_by=self.actor, version=9), "ww_request_initial_version_guard")

    def test_close_requires_assignments_for_every_member(self):
        RequestEmployee.objects.create(request=self.req, employee=self.people[1], organization_id=make_organization().pk)
        self.rejected(lambda: Request.objects.filter(pk=self.req.pk).update(status="closed", version=self.req.version+1,
                      completed_at=timezone.now(), completed_by=self.actor), "ww_request_member_completeness_guard")

    def test_close_empty_request_forbidden(self):
        empty = services.create_request(organization_id=make_organization().pk, actor=self.actor)
        self.rejected(lambda: Request.objects.filter(pk=empty.pk).update(status="closed", version=2,
                      completed_at=timezone.now(), completed_by=self.actor), "ww_request_nonempty_guard")

    def test_closed_request_update_delete_and_child_writes_forbidden(self):
        self.close()
        for operation in (
            lambda: Request.objects.filter(pk=self.req.pk).update(version=self.req.version+1),
            lambda: Request.objects.filter(pk=self.req.pk).delete(),
            lambda: RequestEmployee.objects.filter(pk=self.member.pk).update(snapshot_full_name="Тестовая правка"),
            lambda: RequestEmployee.objects.create(request=self.req, employee=self.people[1], organization_id=make_organization().pk),
            lambda: RequestEmployee.objects.filter(pk=self.member.pk).delete(),
            lambda: RequestAssignment.objects.create(request_employee=self.member, item=self.items[1]),
            lambda: RequestAssignment.objects.filter(pk=self.assignment.pk).delete(),
        ):
            self.rejected(operation)

    def test_unique_members_and_assignments(self):
        self.rejected(lambda: RequestEmployee.objects.create(request=self.req, employee=self.people[0],
                      organization_id=make_organization().pk), "ww_member_request_emp_uniq")
        self.rejected(lambda: RequestAssignment.objects.create(request_employee=self.member,
                      item=self.items[0]), "ww_assignment_member_item_uniq")
