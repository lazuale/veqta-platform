"""Проверяем жизненный цикл заявки, права и атомарность."""
from datetime import timedelta
from unittest.mock import patch
from time import perf_counter
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from workwear import services
from workwear.repeats import scan_repeats
from workwear.models import Employee, Request, RequestAssignment, RequestEmployee, WorkwearEvent
from workwear.sync import save_draft
from .factories import make_user, make_import, make_employees, make_items, make_organization


class LifecycleTests(TestCase):
    def setUp(self):
        self.actor = make_user()
        self.snapshot = make_import()
        self.people = make_employees(self.snapshot)
        self.items = make_items()
        self.req = services.create_request(organization_id=make_organization().pk, actor=self.actor)

    def save(self, *, finish=False, people=None, items=None):
        self.req.refresh_from_db()
        return save_draft(request_id=self.req.pk, actor=self.actor, expected_version=self.req.version,
                          members=[{"employee_id": e.pk, "item_ids": [i.pk for i in (items or self.items)]}
                                   for e in (people or self.people)], finish=finish)

    def test_create_draft_and_audit(self):
        self.assertEqual((self.req.status, self.req.version), ("draft", 1))
        self.assertEqual(WorkwearEvent.objects.get().event_type, "created")

    def test_create_requires_permission(self):
        with self.assertRaises(PermissionDenied):
            services.create_request(organization_id=make_organization().pk, actor=make_user(administrator=False))

    def test_invalid_organization(self):
        with self.assertRaises(ValidationError):
            services.create_request(organization_id=999999, actor=self.actor)

    def test_wrong_organization_rolls_back_whole_batch(self):
        other = make_employees(self.snapshot, 1, make_organization("Тестовая организация Б"))[0]
        with self.assertRaises(ValidationError):
            services.add_employees(request_id=self.req.pk, employee_ids=[self.people[0].pk, other.pk],
                                   actor=self.actor, expected_version=1)
        self.assertEqual(self.req.members.count(), 0)
        self.assertEqual(WorkwearEvent.objects.count(), 1)

    def test_outdated_employee_rejected(self):
        make_import()
        with self.assertRaises(ValidationError):
            services.add_employee(request_id=self.req.pk, employee_id=self.people[0].pk,
                                  actor=self.actor, expected_version=1)

    def test_save_and_complete(self):
        result = self.save(finish=True)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, "closed")
        self.assertEqual(result.completion["final_assignments"], 4)
        self.assertEqual(result.version, 3)

    def test_unchanged_save_keeps_version(self):
        self.save()
        self.assertFalse(self.save().changed)
        self.req.refresh_from_db()
        self.assertEqual(self.req.version, 2)

    def test_stale_version_rejected(self):
        self.save()
        with self.assertRaises(services.WorkwearConflict):
            save_draft(request_id=self.req.pk, actor=self.actor, expected_version=1, members=[])
        self.assertEqual(self.req.members.count(), 2)

    def test_closed_request_cannot_be_saved(self):
        self.save(finish=True)
        with self.assertRaises(ValidationError):
            self.save()

    def test_empty_completion_rolls_back_removed_members(self):
        services.add_employees(request_id=self.req.pk, employee_ids=[p.pk for p in self.people],
                               actor=self.actor, expected_version=1)
        self.req.refresh_from_db()
        with self.assertRaises(ValidationError):
            services.complete_request(request_id=self.req.pk, actor=self.actor, expected_version=self.req.version)
        self.assertEqual(self.req.members.count(), 2)

    def test_completion_excludes_only_empty_members(self):
        result = save_draft(request_id=self.req.pk, actor=self.actor, expected_version=1, finish=True, members=[
            {"employee_id": self.people[0].pk, "item_ids": [self.items[0].pk]},
            {"employee_id": self.people[1].pk, "item_ids": []},
        ])
        self.assertEqual(result.completion["excluded_members"], 1)
        self.assertEqual(self.req.members.count(), 1)

    def test_audit_failure_rolls_back_members_version_and_assignments(self):
        record = services._audit

        def fail_completion(request_id, actor, event_type, **details):
            if event_type == "completed":
                raise RuntimeError("Тестовый сбой записи завершения")
            return record(request_id, actor, event_type, **details)

        with patch("workwear.services._audit", side_effect=fail_completion):
            with self.assertRaises(RuntimeError):
                self.save(finish=True)
        self.req.refresh_from_db()
        self.assertEqual((self.req.status, self.req.version, self.req.members.count()), ("draft", 1, 0))
        self.assertEqual(RequestAssignment.objects.count(), 0)
        self.assertEqual(list(WorkwearEvent.objects.values_list("event_type", flat=True)), ["created"])

    def test_reopen_requires_separate_permission(self):
        result = self.save(finish=True)
        with self.assertRaises(PermissionDenied):
            services.reopen_request(request_id=self.req.pk, actor=make_user(administrator=False), expected_version=result.version)
        reopened = services.reopen_request(request_id=self.req.pk, actor=self.actor, expected_version=result.version, reason="Тестовый возврат")
        self.assertEqual(reopened.status, "draft")
        self.assertIsNone(reopened.completed_at)
        self.assertEqual(WorkwearEvent.objects.first().event_type, "reopened")

    def test_assignment_changes_do_not_lock_employee_import(self):
        self.save(people=[self.people[0]], items=[self.items[0]])
        member = self.req.members.get()
        assignment = member.assignments.get()
        with patch("workwear.services.lock_for_membership") as membership_lock:
            added = services.add_assignment(
                request_id=self.req.pk, member_id=member.pk, item_id=self.items[1].pk,
                actor=self.actor, expected_version=self.req.version,
            )
            self.req.refresh_from_db()
            services.remove_assignment(
                request_id=self.req.pk, assignment_id=added.pk,
                actor=self.actor, expected_version=self.req.version,
            )
            membership_lock.assert_not_called()
        self.assertTrue(RequestAssignment.objects.filter(pk=assignment.pk).exists())

    def test_membership_changes_keep_import_lock(self):
        with patch("workwear.services.lock_for_membership") as membership_lock:
            services.add_employee(
                request_id=self.req.pk, employee_id=self.people[0].pk,
                actor=self.actor, expected_version=self.req.version,
            )
            membership_lock.assert_called_once_with()

    def test_reopening_does_not_lock_employee_import(self):
        result = self.save(finish=True)
        with patch("workwear.services.lock_for_membership") as membership_lock:
            services.reopen_request(
                request_id=self.req.pk, actor=self.actor, expected_version=result.version,
                reason="Проверка блокировки",
            )
            membership_lock.assert_not_called()

    def test_deleting_draft_preserves_history(self):
        services.delete_draft(request_id=self.req.pk, actor=self.actor, expected_version=1)
        self.assertFalse(Request.objects.filter(pk=self.req.pk).exists())
        self.assertEqual(WorkwearEvent.objects.count(), 2)

    def test_current_personnel_number_does_not_change_snapshot(self):
        self.save()
        person = self.people[0]
        Employee.objects.filter(pk=person.pk).update(personnel_number="TEST-CORRECTED")
        self.assertEqual(RequestEmployee.objects.get(employee=person).snapshot_personnel_number, person.personnel_number)

    def test_inactive_item_rejected(self):
        self.items[0].is_active = False
        self.items[0].save()
        with self.assertRaises(ValidationError):
            self.save()
        self.assertEqual(self.req.members.count(), 0)

    def test_invalid_sync_payloads(self):
        for members in ([{"employee_id": True, "item_ids": []}],
                        [{"employee_id": self.people[0].pk, "item_ids": [self.items[0].pk]*2}],
                        [{"employee_id": self.people[0].pk, "item_ids": []}]*2):
            with self.subTest(members=members), self.assertRaises(ValidationError):
                save_draft(request_id=self.req.pk, actor=self.actor, expected_version=1, members=members)

    def test_repeat_validity_uses_calendar_days(self):
        self.save(finish=True)
        today = timezone.localdate()
        for item in self.items:
            pair = (self.people[0].pk, item.pk)
            self.assertIn(pair, scan_repeats(pairs={pair}, as_of_date=today+timedelta(days=item.validity_days-1)))
            self.assertNotIn(pair, scan_repeats(pairs={pair}, as_of_date=today+timedelta(days=item.validity_days)))

    def test_repeat_warning_does_not_block_new_assignment(self):
        self.save(finish=True)
        other = services.create_request(organization_id=make_organization().pk, actor=self.actor)
        result = save_draft(request_id=other.pk, actor=self.actor, expected_version=1,
                            members=[{"employee_id": self.people[0].pk, "item_ids": [self.items[0].pk]}], finish=True)
        self.assertEqual(result.completion["final_assignments"], 1)

    def test_future_local_date_does_not_hide_previous_issue(self):
        self.save(finish=True)
        other = services.create_request(organization_id=make_organization().pk, actor=self.actor)
        future = timezone.now()+timedelta(days=2)
        with patch("workwear.services.timezone.now", return_value=future):
            save_draft(request_id=other.pk, actor=self.actor, expected_version=1,
                       members=[{"employee_id": self.people[0].pk, "item_ids": [self.items[0].pk]}], finish=True)
        warning = scan_repeats(pairs={(self.people[0].pk, self.items[0].pk)})[(self.people[0].pk, self.items[0].pk)]
        self.assertEqual(warning.previous_request_id, self.req.pk)


class ScaleTests(TestCase):
    """Проверяем предельный набор из 30 вымышленных позиций."""
    def test_thousand_employees_thirty_thousand_assignments(self):
        actor, snapshot = make_user(), make_import()
        people, items = make_employees(snapshot, 1000), make_items(30)
        req = services.create_request(organization_id=make_organization().pk, actor=actor)
        members = [{"employee_id": e.pk, "item_ids": [i.pk for i in items]} for e in people]
        started = perf_counter()
        with CaptureQueriesContext(connection) as queries:
            result = save_draft(request_id=req.pk, actor=actor, expected_version=1, members=members, finish=True)
        self.assertEqual(result.completion["final_members"], 1000)
        self.assertEqual(result.completion["final_assignments"], 30000)
        print(f"\nСохранение и завершение 1000 × 30: {perf_counter()-started:.2f} с; SQL-запросов: {len(queries)}")
        with self.assertRaises(ValidationError):
            save_draft(request_id=req.pk, actor=actor, expected_version=result.version, members=members+members[:1])
