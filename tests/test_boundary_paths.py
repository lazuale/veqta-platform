"""Проверяем реальные граничные случаи поиска, повторов и изменения заявок."""
from datetime import date
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.test import TestCase

from workwear import services
from workwear.eligibility import require_current
from workwear.employee_lookup import resolve_many
from workwear.models import Employee, WorkwearItem
from workwear.repeats import scan_repeats
from workwear.sync import save_draft

from .factories import make_employees, make_import, make_items, make_organization, make_user


class BoundaryPathsTests(TestCase):
    def setUp(self):
        self.actor = make_user()
        self.import_record = make_import()
        self.people = make_employees(self.import_record)
        self.items = make_items()
        self.organization = self.people[0].organization
        self.request = services.create_request(
            organization_id=self.organization.pk, actor=self.actor,
        )

    def test_search_without_import_and_ambiguous_numbers(self):
        unavailable = resolve_many(
            lines=["  TEST-00000  "], organization_id=self.organization.pk,
            latest=None, already=set(),
        )
        self.assertEqual(unavailable[0]["status"], "import_unavailable")
        self.assertEqual(unavailable[0]["input"], "TEST-00000")

        Employee.objects.filter(pk__in=[person.pk for person in self.people]).update(
            full_name="Совпадающий сотрудник",
        )
        older = make_import()
        stale = Employee.objects.create(
            employee_guid=uuid4(), personnel_number="OLD-1234",
            full_name="Устаревший сотрудник", organization=self.organization,
            last_seen_import=older,
        )
        results = resolve_many(
            lines=["Совпадающий сотрудник", "0000", "Устаревший сотрудник", "Нет совпадений"],
            organization_id=self.organization.pk,
            latest=self.import_record, already=set(),
        )
        self.assertEqual([result["status"] for result in results],
                         ["ambiguous", "matched", "outdated", "not_found"])
        self.assertEqual(results[0]["candidate_count"], 2)
        self.assertEqual(results[1]["candidates"][0]["id"], self.people[0].pk)
        self.assertEqual(results[2]["candidates"], [])
        self.assertEqual(stale.last_seen_import_id, older.pk)

    def test_history_filters_unrequested_pairs_and_handles_max_date(self):
        people, items = self.people, self.items
        save_draft(
            request_id=self.request.pk, actor=self.actor,
            expected_version=1, finish=True,
            members=[
                {"employee_id": people[0].pk, "item_ids": [items[0].pk]},
                {"employee_id": people[1].pk, "item_ids": [items[1].pk]},
            ],
        )
        crossed = {(people[0].pk, items[1].pk), (people[1].pk, items[0].pk)}
        self.assertEqual(scan_repeats(pairs=crossed), {})
        self.assertEqual(scan_repeats(
            pairs={(people[0].pk, items[0].pk)}, as_of_date=date.max,
        ), {})

    def test_repeat_scan_rejects_invalid_dates_and_pairs(self):
        for options in (
            {"pairs": {(self.people[0].pk, self.items[0].pk)}, "as_of_date": "bad"},
            {"pairs": {(True, self.items[0].pk)}},
            {"pairs": {(self.people[0].pk, 99999999)}},
            {"pairs": {(0, self.items[0].pk)}},
        ):
            with self.subTest(options=options), self.assertRaises(ValidationError):
                scan_repeats(**options)

    def test_full_sync_removes_members_and_assignments(self):
        initial = save_draft(
            request_id=self.request.pk, actor=self.actor,
            expected_version=1,
            members=[
                {"employee_id": self.people[0].pk,
                 "item_ids": [self.items[0].pk, self.items[1].pk]},
                {"employee_id": self.people[1].pk, "item_ids": [self.items[0].pk]},
            ],
        )
        changed = save_draft(
            request_id=self.request.pk, actor=self.actor,
            expected_version=initial.version,
            members=[{"employee_id": self.people[0].pk, "item_ids": [self.items[0].pk]}],
        )
        self.assertTrue(changed.changed)
        self.assertEqual(changed.version, initial.version + 1)
        self.assertEqual(self.request.members.count(), 1)
        self.assertEqual(self.request.members.get().assignments.count(), 1)

    def test_services_reject_invalid_versions_and_members(self):
        with self.assertRaises(ValidationError):
            services.create_request(organization_id=True, actor=self.actor)
        with self.assertRaises(ValidationError):
            services.add_employees(
                request_id=self.request.pk, employee_ids=[True],
                actor=self.actor, expected_version=1,
            )
        with self.assertRaises(Employee.DoesNotExist):
            services.add_employees(
                request_id=self.request.pk, employee_ids=[99999999],
                actor=self.actor, expected_version=1,
            )
        with self.assertRaises(ValidationError):
            services.complete_request(
                request_id=self.request.pk, actor=self.actor, expected_version=0,
            )

    def test_sync_validates_missing_employee_item_and_finish_type(self):
        cases = (
            ([{"employee_id": 99999999, "item_ids": []}], False),
            ([{"employee_id": self.people[0].pk, "item_ids": [99999999]}], False),
            ([{"employee_id": self.people[0].pk}], False),
            ([], "yes"),
        )
        for members, finish in cases:
            with self.subTest(members=members, finish=finish), self.assertRaises(ValidationError):
                save_draft(
                    request_id=self.request.pk, actor=self.actor,
                    expected_version=1, members=members, finish=finish,
                )
        self.assertEqual(self.request.members.count(), 0)

    def test_eligibility_and_reopen_reason_guards(self):
        with self.assertRaises(ValidationError):
            require_current(self.people[0], organization_id=self.organization.pk, latest=None)
        completed = save_draft(
            request_id=self.request.pk, actor=self.actor,
            expected_version=1, finish=True,
            members=[{"employee_id": self.people[0].pk, "item_ids": [self.items[0].pk]}],
        )
        for reason in (None, "x" * 501):
            with self.subTest(reason=reason), self.assertRaises(ValidationError):
                services.reopen_request(
                    request_id=self.request.pk, actor=self.actor,
                    expected_version=completed.version, reason=reason,
                )

    def test_assignment_limit_and_duplicate_detection(self):
        extra_items = [
            WorkwearItem.objects.create(
                code=f"BOUND-{index}", name=f"Тестовая позиция {index}",
                category="Проверка ограничения", validity_days=365,
            )
            for index in range(29)
        ]
        all_allowed = self.items + extra_items[:28]
        result = save_draft(
            request_id=self.request.pk, actor=self.actor,
            expected_version=1,
            members=[{"employee_id": self.people[0].pk,
                      "item_ids": [item.pk for item in all_allowed]}],
        )
        member_id = self.request.members.get().pk
        with self.assertRaises(ValidationError):
            services.add_assignment(
                request_id=self.request.pk, member_id=member_id,
                item_id=all_allowed[0].pk, actor=self.actor, expected_version=result.version,
            )
        with self.assertRaises(ValidationError):
            services.add_assignment(
                request_id=self.request.pk, member_id=member_id,
                item_id=extra_items[-1].pk, actor=self.actor, expected_version=result.version,
            )
        self.assertEqual(self.request.members.get().assignments.count(), 30)

    def test_employee_str_includes_personnel_number(self):
        self.assertIn(self.people[0].personnel_number, str(self.people[0]))
