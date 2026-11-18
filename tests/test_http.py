"""Штатная авторизация, права, CSRF и контракты HTTP."""
from django.contrib.auth.models import Permission
from workwear.models import EmployeeImport
from django.test import Client, TestCase, override_settings
from workwear import services
from .factories import make_user, make_import, make_employees, make_items, make_organization


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class HttpTests(TestCase):
    """Клиент использует ту же маршрутизацию и middleware, что и приложение."""
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

    def test_logout_requires_post(self):
        self.assertEqual(self.client.get("/logout/").status_code, 405)
        self.assertEqual(self.client.post("/logout/").status_code, 302)
