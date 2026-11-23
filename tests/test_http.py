"""Проверяем вход, права, CSRF и HTTP API."""
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.core.exceptions import RequestDataTooBig, ValidationError
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings

from workwear import services
from workwear.api import payload
from workwear.limits import MAX_JSON_BYTES, MAX_SYNC_JSON_BYTES
from workwear.models import EmployeeImport, Request
from workwear.sync import save_draft

from .factories import make_user, make_import, make_employees, make_items, make_organization


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class HttpTests(TestCase):
    def setUp(self):
        self.actor = make_user()
        self.client.force_login(self.actor)
        self.people = make_employees(make_import())
        self.items = make_items()

    def test_portal_and_workwear_pages(self):
        for path in ("/", "/profile/", "/workwear/", "/password/change/", "/admin/auth/user/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_anonymous_login_redirect_and_api_unauthorized(self):
        client = Client()
        self.assertEqual(client.get("/").status_code, 302)
        self.assertEqual(client.get("/workwear/api/session/").status_code, 401)

    def test_password_change_uses_django_field_names(self):
        body = self.client.get("/password/change/").content.decode()
        for name in ("old_password", "new_password1", "new_password2"):
            self.assertIn(f'name="{name}"', body)

    def test_reader_sees_all_organizations(self):
        for name in ("Тестовая организация А", "Тестовая организация Б", "Тестовая организация В"):
            services.create_request(organization_id=make_organization(name).pk, actor=self.actor)
        reader = make_user(administrator=False)
        reader.user_permissions.add(Permission.objects.get(content_type__app_label="workwear", codename="view_request"))
        self.client.force_login(reader)
        self.assertEqual(self.client.get("/workwear/api/ui/requests/").json()["count"], 3)
        result = self.client.post("/workwear/api/requests/", {"organization_id": make_organization().pk}, content_type="application/json")
        self.assertEqual(result.status_code, 403)

    def test_csrf_required_for_writes(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        result = client.post("/workwear/api/requests/", {"organization_id": make_organization().pk}, content_type="application/json")
        self.assertEqual(result.status_code, 403)

    def test_search_by_partial_current_personnel_number(self):
        result = self.client.get("/workwear/api/ui/employees/", {"q": "00000", "organization_id": make_organization().pk})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["results"][0]["id"], self.people[0].pk)

    def test_wrong_methods_and_json_rejected(self):
        self.assertEqual(self.client.post("/workwear/api/session/").status_code, 405)
        for payload in ('{"organization_id":NaN}', '{"organization_id":"x","extra":1}', '[]'):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post("/workwear/api/requests/", payload, content_type="application/json").status_code, 400)

    def test_payload_budgets_return_json_errors_without_writes(self):
        organization = self.people[0].organization
        create = json.dumps({"organization_id": organization.pk})
        result = self.client.post("/workwear/api/requests/", create.ljust(MAX_JSON_BYTES + 1),
                                  content_type="application/json")
        self.assertEqual(result.status_code, 413)
        self.assertEqual(result.json()["error"]["code"], "payload_too_large")
        self.assertFalse(Request.objects.exists())

        request = services.create_request(organization_id=organization.pk, actor=self.actor)
        path = f"/workwear/api/ui/requests/{request.pk}/sync/"
        sync = json.dumps({"members": [], "expected_version": request.version, "finish": False})
        allowed = self.client.post(path, sync.ljust(MAX_JSON_BYTES + 1), content_type="application/json")
        self.assertEqual(allowed.status_code, 200)
        self.assertFalse(allowed.json()["changed"])
        rejected = self.client.post(path, sync.ljust(MAX_SYNC_JSON_BYTES + 1), content_type="application/json")
        self.assertEqual(rejected.status_code, 413)
        self.assertEqual(rejected.json()["error"]["code"], "payload_too_large")
        self.assertEqual(Request.objects.get(pk=request.pk).version, request.version)

    def test_http_stale_version_returns_conflict(self):
        created = self.client.post("/workwear/api/requests/", {"organization_id": make_organization().pk}, content_type="application/json").json()["request"]
        path = f"/workwear/api/ui/requests/{created['id']}/sync/"
        payload = {"expected_version": 1, "finish": False, "members": [{"employee_id": self.people[0].pk, "item_ids": [self.items[0].pk]}]}
        self.assertEqual(self.client.post(path, payload, content_type="application/json").status_code, 200)
        result = self.client.post(path, payload, content_type="application/json")
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()["error"]["code"], "stale_version")

    def test_metadata_reports_source_date_without_inventing_period(self):
        data = self.client.get("/workwear/api/ui/metadata/").json()
        self.assertNotIn("coverage_start", data["import"])
        self.assertEqual(data["import"]["source_export_date"],
                         EmployeeImport.objects.get().source_export_date.isoformat())
        self.assertRegex(data["import"]["source_export_date"], r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
        self.assertNotIn("source_exported_at", data["import"])
        self.assertEqual(data["limits"]["request_members"], 1000)
        self.assertEqual(data["limits"]["items_per_member"], 30)

    def test_individual_api_lifecycle_and_preserved_events(self):
        organization = self.people[0].organization
        base = "/workwear/api/requests/"
        created = self.client.post(base, {"organization_id": organization.pk}, content_type="application/json")
        self.assertEqual(created.status_code, 201)
        request_id = created.json()["request"]["id"]
        url = f"{base}{request_id}/"
        self.assertEqual(self.client.get(url).json()["request"]["members"], [])

        added = self.client.post(url + "members/", {
            "employee_id": self.people[0].pk, "expected_version": 1,
        }, content_type="application/json")
        self.assertEqual(added.status_code, 201)
        member_id, version = added.json()["member_id"], added.json()["version"]
        assignment = self.client.post(url + "assignments/", {
            "member_id": member_id, "item_id": self.items[0].pk, "expected_version": version,
        }, content_type="application/json")
        self.assertEqual(assignment.status_code, 201)
        assignment_id, version = assignment.json()["assignment_id"], assignment.json()["version"]
        removed = self.client.delete(url + f"assignments/{assignment_id}/", {
            "expected_version": version,
        }, content_type="application/json")
        self.assertEqual(removed.status_code, 200)
        version = removed.json()["version"]
        assignment = self.client.post(url + "assignments/", {
            "member_id": member_id, "item_id": self.items[0].pk, "expected_version": version,
        }, content_type="application/json")
        self.assertEqual(assignment.status_code, 201)
        version = assignment.json()["version"]
        self.assertEqual(len(self.client.get(url).json()["request"]["members"][0]["assignments"]), 1)

        completed = self.client.post(url + "complete/", {"expected_version": version},
                                     content_type="application/json")
        self.assertEqual(completed.status_code, 200)
        version = completed.json()["completion"]["version"]
        reopened = self.client.post(url + "reopen/", {"expected_version": version},
                                    content_type="application/json")
        self.assertEqual(reopened.status_code, 200)
        version = reopened.json()["request"]["version"]
        removed = self.client.delete(url + f"members/{member_id}/", {
            "expected_version": version,
        }, content_type="application/json")
        self.assertEqual(removed.status_code, 200)
        deleted = self.client.delete(url, {"expected_version": removed.json()["version"]},
                                     content_type="application/json")
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(Request.objects.filter(pk=request_id).exists())
        events = self.client.get(f"/workwear/api/ui/requests/{request_id}/events/")
        self.assertEqual(events.status_code, 200)
        self.assertGreaterEqual(events.json()["count"], 6)

    def test_resolve_bulk_add_and_search_states(self):
        organization = self.people[0].organization
        foreign_org = make_organization("Другая тестовая организация")
        foreign = make_employees(self.people[0].last_seen_import, count=1, organization=foreign_org)[0]
        foreign.full_name = "Чужой сотрудник"
        foreign.save(update_fields=["full_name"])
        request = services.create_request(organization_id=organization.pk, actor=self.actor)
        result = self.client.post("/workwear/api/ui/resolve/", {
            "organization_id": organization.pk,
            "lines": ["TEST-00000", "TEST-00000", "Тестовый сотрудник 00001",
                      "Чужой сотрудник", "Нет такого сотрудника"],
            "present_employee_ids": [self.people[1].pk],
        }, content_type="application/json")
        self.assertEqual(result.status_code, 200)
        self.assertEqual([entry["status"] for entry in result.json()["results"]],
                         ["matched", "duplicate", "included", "other_organization", "not_found"])

        bulk = self.client.post(f"/workwear/api/ui/requests/{request.pk}/bulk-add/", {
            "employee_ids": [self.people[0].pk, self.people[1].pk],
            "expected_version": request.version,
        }, content_type="application/json")
        self.assertEqual(bulk.status_code, 201)
        self.assertEqual(len(bulk.json()["added"]), 2)
        self.assertEqual(len(self.client.get(f"/workwear/api/requests/{request.pk}/").json()
                             ["request"]["members"]), 2)
        duplicate = self.client.post(f"/workwear/api/ui/requests/{request.pk}/bulk-add/", {
            "employee_ids": [self.people[0].pk, self.people[0].pk],
            "expected_version": bulk.json()["version"],
        }, content_type="application/json")
        self.assertEqual(duplicate.status_code, 400)

    def test_registry_history_repeat_scan_and_reopen_reason(self):
        organization = self.people[0].organization
        prior = services.create_request(organization_id=organization.pk, actor=self.actor)
        completed = save_draft(request_id=prior.pk, actor=self.actor, expected_version=prior.version,
                               members=[{"employee_id": self.people[0].pk,
                                         "item_ids": [self.items[0].pk]}], finish=True)
        draft = services.create_request(organization_id=organization.pk, actor=self.actor)
        base = f"/workwear/api/ui/requests/{draft.pk}/"
        history = self.client.get(base + f"employees/{self.people[0].pk}/history/")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["results"][0]["request_id"], prior.pk)
        scan = self.client.post(base + "repeat-scan/", {
            "pairs": [{"employee_id": self.people[0].pk, "item_id": self.items[0].pk}],
        }, content_type="application/json")
        self.assertEqual(scan.status_code, 200)
        self.assertEqual(scan.json()["warnings"][0]["previous_request_id"], prior.pk)
        self.assertEqual(self.client.post(base + "repeat-scan/", {
            "pairs": [{"employee_id": self.people[0].pk, "item_id": self.items[0].pk}] * 2,
        }, content_type="application/json").status_code, 400)
        registry = self.client.get("/workwear/api/ui/requests/", {
            "status": "closed", "q": "TEST-00000", "date_basis": "completed", "sort": "oldest",
        })
        self.assertEqual(registry.status_code, 200)
        self.assertEqual(registry.json()["count"], 1)
        self.assertEqual(registry.json()["results"][0]["id"], prior.pk)
        for parameters in ({"date_from": "wrong"}, {"sort": "sideways"},
                           {"limit": "0"}, {"date_basis": "invalid"}):
            with self.subTest(parameters=parameters):
                self.assertEqual(self.client.get("/workwear/api/ui/requests/",
                                                 parameters).status_code, 400)
        reopened = self.client.post(f"/workwear/api/ui/requests/{prior.pk}/reopen/", {
            "expected_version": completed.version, "reason": "Исправление тестовой заявки",
        }, content_type="application/json")
        self.assertEqual(reopened.status_code, 200)
        self.assertEqual(reopened.json()["request"]["status"], "draft")

    def test_request_list_filters_and_missing_request(self):
        organization = self.people[0].organization
        requested = services.create_request(organization_id=organization.pk, actor=self.actor)
        services.create_request(organization_id=make_organization("Другой объект").pk, actor=self.actor)
        result = self.client.get("/workwear/api/requests/", {
            "status": "draft", "organization_id": str(organization.pk),
        })
        self.assertEqual(result.status_code, 200)
        self.assertEqual([row["id"] for row in result.json()["results"]], [requested.pk])
        for query in ({"status": "invalid"}, {"organization_id": "abc"},
                      {"organization_id": "0"}, {"organization_id": "9999999999"}):
            with self.subTest(query=query):
                self.assertEqual(self.client.get("/workwear/api/requests/", query).status_code, 400)
        self.assertEqual(self.client.get("/workwear/api/requests/999999999/").status_code, 404)

    def test_http_validation_and_accessible_errors(self):
        org = self.people[0].organization
        path = "/workwear/api/requests/"
        self.assertEqual(self.client.get("/workwear/api/session/").status_code, 200)
        self.assertEqual(self.client.post(path, {"organization_id": org.pk}, content_type="text/plain").status_code, 400)
        self.assertEqual(self.client.post(path, "invalid-json", content_type="application/json").status_code, 400)
        req = services.create_request(organization_id=org.pk, actor=self.actor)
        self.assertEqual(self.client.post(f"{path}{req.pk}/members/", {
            "employee_id": self.people[0].pk, "expected_version": -1,
        }, content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post(f"{path}{req.pk}/members/", {
            "employee_id": self.people[0].pk, "expected_version": 1,
        }, content_type="application/json").status_code, 201)
        req.refresh_from_db()
        self.assertEqual(self.client.post(f"{path}{req.pk}/members/", {
            "employee_id": self.people[0].pk, "expected_version": req.version,
        }, content_type="application/json").status_code, 400)
        first_member = req.members.first()
        self.items[0].is_active = False
        self.items[0].save(update_fields=["is_active"])
        self.assertEqual(self.client.post(f"{path}{req.pk}/assignments/", {
            "member_id": first_member.pk, "item_id": self.items[0].pk, "expected_version": req.version,
        }, content_type="application/json").status_code, 400)

    def test_registry_date_number_and_permission_guards(self):
        org = self.people[0].organization
        req = services.create_request(organization_id=org.pk, actor=self.actor)
        base = "/workwear/api/ui/requests/"
        today = req.created_at.date().isoformat()
        filtered = self.client.get(base, {
            "date_from": today, "date_to": today, "organization_id": str(org.pk),
            "q": str(req.pk), "sort": "oldest",
        })
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(filtered.json()["count"], 1)
        for query in (
            {"q": "x" * 121}, {"status": "invalid"}, {"offset": "-1"},
            {"offset": "1000001"}, {"offset": "99999999999999"},
            {"date_from": "2026-02-30"}, {"date_from": "2026-10-02", "date_to": "2026-10-01"},
            {"organization_id": "nope"}, {"date_basis": "nope"},
        ):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(base, query).status_code, 400)
        self.assertEqual(self.client.get("/workwear/api/ui/employees/", {
            "q": "a", "organization_id": str(org.pk),
        }).json()["results"], [])
        self.assertEqual(self.client.get("/workwear/api/ui/requests/999999999/events/").status_code, 404)
        reader = make_user(administrator=False)
        reader.user_permissions.add(Permission.objects.get(
            content_type__app_label="workwear", codename="view_request"))
        self.client.force_login(reader)
        metadata = self.client.get("/workwear/api/ui/metadata/")
        self.assertEqual(metadata.status_code, 200)
        self.assertEqual(metadata.json()["items"], [])
        self.assertEqual(self.client.get("/workwear/api/ui/employees/", {
            "q": "TEST", "organization_id": str(org.pk),
        }).status_code, 403)

    def test_resolve_context_and_bad_inputs(self):
        org = self.people[0].organization
        req = services.create_request(organization_id=org.pk, actor=self.actor)
        url = "/workwear/api/ui/resolve/"
        payload = {"organization_id": org.pk, "lines": ["TEST-00000"], "request_id": req.pk}
        valid = self.client.post(url, payload, content_type="application/json")
        self.assertEqual(valid.status_code, 200)
        self.assertEqual(valid.json()["results"][0]["status"], "matched")
        invalid_cases = (
            {"organization_id": 999999, "lines": ["TEST-00000"]},
            {"organization_id": org.pk, "lines": []},
            {"organization_id": org.pk, "lines": [123]},
            {"organization_id": org.pk, "lines": ["TEST-00000"], "present_employee_ids": [False]},
            {"organization_id": org.pk, "lines": ["TEST-00000"], "request_id": 999999999},
        )
        for invalid in invalid_cases:
            with self.subTest(invalid=invalid):
                response = self.client.post(url, invalid, content_type="application/json")
                self.assertIn(response.status_code, {400, 404})

    def test_bulk_search_respects_local_removal_before_save(self):
        employee = self.people[0]
        draft = services.create_request(organization_id=employee.organization_id, actor=self.actor)
        services.add_employee(request_id=draft.pk, employee_id=employee.pk,
                              actor=self.actor, expected_version=draft.version)
        data = {"request_id": draft.pk, "organization_id": employee.organization_id,
                "lines": [employee.personnel_number, employee.personnel_number]}
        url = "/workwear/api/ui/resolve/"
        for present, expected in (
            ({}, ["included", "included"]),
            ({"present_employee_ids": []}, ["matched", "duplicate"]),
            ({"present_employee_ids": [employee.pk]}, ["included", "included"]),
        ):
            with self.subTest(present=present):
                response = self.client.post(url, data | present, content_type="application/json")
                self.assertEqual(response.status_code, 200)
                self.assertEqual([row["status"] for row in response.json()["results"]], expected)
        self.assertEqual(draft.members.count(), 1)

    def test_repeat_scan_rejects_foreign_employee_and_malformed_pairs(self):
        org = self.people[0].organization
        req = services.create_request(organization_id=org.pk, actor=self.actor)
        path = f"/workwear/api/ui/requests/{req.pk}/repeat-scan/"
        other = make_employees(self.people[0].last_seen_import, count=1,
                               organization=make_organization("Чужая организация"))[0]
        for pairs in (
            [{"employee_id": other.pk, "item_id": self.items[0].pk}],
            [{"employee_id": self.people[0].pk}],
            "invalid",
            [{"employee_id": -1, "item_id": self.items[0].pk}],
            [{"employee_id": 99999999, "item_id": self.items[0].pk}],
        ):
            with self.subTest(pairs=pairs):
                self.assertEqual(self.client.post(path, {"pairs": pairs},
                                                  content_type="application/json").status_code, 400)
        empty = self.client.post(path, {"pairs": []}, content_type="application/json")
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["warnings"], [])

    def test_http_validation_handles_corrupt_length_and_integrity(self):
        bad_length = SimpleNamespace(
            content_type="application/json", META={"CONTENT_LENGTH": "wrong"},
            body=b"{}",
        )
        with self.assertRaises(ValidationError):
            payload(bad_length, "organization_id")
        understated = SimpleNamespace(
            content_type="application/json", META={"CONTENT_LENGTH": "0"},
            body=b"x" * (MAX_JSON_BYTES + 1),
        )
        with self.assertRaises(RequestDataTooBig):
            payload(understated, "organization_id")
        with patch("workwear.services.create_request", side_effect=IntegrityError("synthetic")):
            result = self.client.post("/workwear/api/requests/", {
                "organization_id": self.people[0].organization_id,
            }, content_type="application/json")
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()["error"]["code"], "integrity_conflict")
        self.assertNotIn("synthetic", result.content.decode())

    def test_ui_boundary_queries_and_organization_guards(self):
        organization = self.people[0].organization
        request = services.create_request(organization_id=organization.pk, actor=self.actor)
        foreign = services.create_request(
            organization_id=make_organization("Чужая организация").pk, actor=self.actor,
        )
        self.assertEqual(self.client.get("/workwear/api/requests/").status_code, 200)
        self.assertEqual(self.client.get("/workwear/api/requests/", {
            "organization_id": str(organization.pk),
        }).status_code, 200)
        self.assertEqual(self.client.get("/workwear/api/ui/requests/", {
            "q": "9223372036854775808",
        }).status_code, 200)
        self.assertEqual(self.client.get("/workwear/api/ui/employees/", {
            "organization_id": str(organization.pk), "limit": "0",
        }).status_code, 400)
        self.assertEqual(self.client.get(
            f"/workwear/api/ui/requests/{request.pk}/events/", {"limit": "0"},
        ).status_code, 400)
        result = self.client.post("/workwear/api/ui/resolve/", {
            "organization_id": organization.pk, "request_id": foreign.pk,
            "lines": ["TEST-00000"],
        }, content_type="application/json")
        self.assertEqual(result.status_code, 400)
        for ids in ([], [True], "not-a-list"):
            with self.subTest(ids=ids):
                result = self.client.post(
                    f"/workwear/api/ui/requests/{request.pk}/bulk-add/",
                    {"employee_ids": ids, "expected_version": request.version},
                    content_type="application/json",
                )
                self.assertEqual(result.status_code, 400)

    def test_logout_requires_post(self):
        self.assertEqual(self.client.get("/logout/").status_code, 405)
        self.assertEqual(self.client.post("/logout/").status_code, 302)
