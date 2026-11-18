"""Приёмочные проверки конкурентной записи и интерфейса на временной базе."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import secrets
from threading import Barrier
from time import perf_counter
from uuid import uuid4
from .browser_support import live_server, sign_in


def check_race(actor, people, items):
    """Из двух конкурентных записей одна должна получить конфликт версии."""
    from django.db import connections
    from django.contrib.auth import get_user_model
    from workwear import services
    from workwear.models import WorkwearEvent
    from workwear.sync import save_draft
    req = services.create_request(organization_id=people[0].organization_id, actor=actor)
    barrier = Barrier(2)
    from .factories import make_user
    users = [actor.pk, make_user().pk]

    def worker(arguments):
        try:
            person, user_id = arguments
            user = get_user_model().objects.get(pk=user_id)
            barrier.wait(timeout=10)
            save_draft(request_id=req.pk, actor=user, expected_version=1,
                       members=[{"employee_id": person.pk, "item_ids": [items[0].pk]}])
            return "saved"
        except services.WorkwearConflict:
            return "conflict"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, zip(people[:2], users)))
    assert sorted(results) == ["conflict", "saved"], results
    req.refresh_from_db()
    assert req.version == 2 and req.members.count() == 1
    assert WorkwearEvent.objects.filter(request_number=req.pk).count() == 2
    print("Конкурентное сохранение двух пользователей: одна успешная запись и один конфликт; частичных изменений нет")


def check_parallel_load(organization_id, employee_ids, item_ids, users=8):
    from django.db import connections
    from django.contrib.auth import get_user_model
    from django.test.utils import CaptureQueriesContext
    from workwear import services
    from workwear.models import RequestAssignment, WorkwearEvent
    from workwear.sync import save_draft
    from .factories import make_user
    actors = [make_user() for _ in range(users)]
    requests = [(user.pk, services.create_request(organization_id=organization_id, actor=user).pk) for user in actors]
    barrier = Barrier(users)
    members = [{"employee_id": person, "item_ids": item_ids} for person in employee_ids]

    def worker(arguments):
        connection = connections["default"]
        connection.settings_dict = {**connection.settings_dict, "USER": "veqta_dev",
                                    "PASSWORD": os.environ["DB_RUNTIME_PASSWORD"]}
        try:
            user_id, request_id = arguments
            user = get_user_model().objects.get(pk=user_id)
            barrier.wait(timeout=15)
            started = perf_counter()
            with CaptureQueriesContext(connection) as queries:
                result = save_draft(request_id=request_id, actor=user, expected_version=1, members=members)
            seconds = perf_counter() - started
            count = RequestAssignment.objects.filter(request_employee__request_id=request_id).count()
            assert count == len(employee_ids) * len(item_ids) and result.version == 2
            assert WorkwearEvent.objects.filter(request_number=request_id).count() == 2
            return {"seconds": round(seconds, 3), "sql_queries": len(queries), "assignments": count}
        finally:
            connections.close_all()

    started = perf_counter()
    with ThreadPoolExecutor(max_workers=users) as pool:
        results = list(pool.map(worker, requests))
    report = {"users": users, "employees_per_user": len(employee_ids), "items_per_employee": len(item_ids),
              "total_seconds": round(perf_counter()-started, 3), "sessions": results,
              "scope": "concurrent application-role service transactions; local PostgreSQL"}
    print(f"Параллельные прикладные сеансы: {users} × {len(employee_ids)} × {len(item_ids)} — успешно", flush=True)
    return report


def check_http_load(server, organization_id, employee_ids, item_ids):
    from http.cookiejar import CookieJar
    import json
    import ssl
    import urllib.error
    import urllib.parse
    import urllib.request
    from .browser_support import database_call
    from .browser_matrix import persisted
    from .factories import make_user

    def identities():
        result = []
        for _ in range(8):
            actor = make_user()
            password = secrets.token_urlsafe(32)
            actor.set_password(password)
            actor.save(update_fields=["password"])
            result.append((actor.username, password))
        return result

    users = database_call(identities)
    base = server.url
    context = ssl.create_default_context(cafile=str(server.ca_file)) if server.ca_file else None
    class Session:
        def __init__(self):
            self.cookies = CookieJar()
            self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                urllib.request.HTTPCookieProcessor(self.cookies), urllib.request.HTTPSHandler(context=context))
            self.csrf = None
            self.measurements = []

        def request(self, method, path, *, data=None, form=None):
            headers = {"Referer": base + "/workwear/", "Origin": base}
            if self.csrf:
                headers["X-CSRFToken"] = self.csrf
            body = None
            if data is not None:
                body = json.dumps(data).encode()
                headers["Content-Type"] = "application/json"
            elif form is not None:
                body = urllib.parse.urlencode(form).encode()
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                headers["Referer"] = base + "/login/"
            started = perf_counter()
            request = urllib.request.Request(base + path, body, headers, method=method)
            try:
                response = self.opener.open(request, timeout=45)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                payload = response.read()
                record = {"method": method, "path": path, "status": response.status,
                    "seconds": round(perf_counter()-started, 3), "response_bytes": len(payload),
                    "sql_queries": int(response.headers.get("X-Veqta-Test-SQL", "0")),
                    "database_role": response.headers.get("X-Veqta-Test-DB-User")}
                self.measurements.append(record)
                result = json.loads(payload) if response.headers.get_content_type() == "application/json" else None
                return response.status, result

        def login(self, username, password):
            assert self.request("GET", "/login/")[0] == 200
            token = next(cookie.value for cookie in self.cookies if cookie.name == "veqta_dev_csrf")
            assert self.request("POST", "/login/", form={"username": username, "password": password,
                "csrfmiddlewaretoken": token})[0] == 200
            status, session = self.request("GET", "/workwear/api/session/")
            assert status == 200 and session["username"] == username
            self.csrf = session["csrf_token"]
            if server.ca_file:
                assert all(cookie.secure for cookie in self.cookies
                           if cookie.name in {"veqta_dev_csrf", "veqta_dev_session"})

        def create(self):
            status, body = self.request("POST", "/workwear/api/requests/", data={"organization_id": organization_id})
            assert status == 201, (status, body)
            return body["request"]["id"]

    members = [{"employee_id": person, "item_ids": item_ids} for person in employee_ids]
    expected = sorted((person, item) for person in employee_ids for item in item_ids)
    barrier = Barrier(8)
    def worker(identity):
        client = Session()
        client.login(*identity)
        request_id = client.create()
        barrier.wait(timeout=30)
        status, body = client.request("POST", f"/workwear/api/ui/requests/{request_id}/sync/",
                                      data={"expected_version": 1, "members": members, "finish": False})
        saving = client.measurements[-1].copy()
        assert status == 200 and body["version"] == 2 and body["changed"] is True, (status, body)
        assert saving["database_role"] == "veqta_dev", saving
        status, stored = client.request("GET", f"/workwear/api/requests/{request_id}/")
        assert status == 200
        pairs = sorted((member["employee_id"], assignment["item_id"])
                       for member in stored["request"]["members"] for assignment in member["assignments"])
        assert pairs == expected
        status, unchanged = client.request("POST", f"/workwear/api/ui/requests/{request_id}/sync/",
                                          data={"expected_version": 2, "members": members, "finish": False})
        assert status == 200 and unchanged["version"] == 2 and unchanged["changed"] is False
        return client, request_id, saving

    started = perf_counter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(worker, users))
    elapsed = perf_counter()-started
    for _, request_id, _ in results:
        stored = database_call(lambda: persisted(request_id))
        assert stored["pairs"] == expected and stored["version"] == 2 and stored["events"] == 2

    # Конкурентные сессии должны получить 200 и 409 без частичной записи.
    shared = results[0][0].create()
    race = Barrier(2)
    def race_worker(index):
        client = results[index][0]
        race.wait(timeout=15)
        return client.request("POST", f"/workwear/api/ui/requests/{shared}/sync/", data={
            "expected_version": 1, "finish": False,
            "members": [{"employee_id": employee_ids[index], "item_ids": [item_ids[index]]}],
        })
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(race_worker, (0, 1)))
    assert sorted(status for status, _ in outcomes) == [200, 409], outcomes
    loser = next(body for status, body in outcomes if status == 409)
    assert loser["error"]["code"] == "stale_version"
    winner = next(index for index, (status, _) in enumerate(outcomes) if status == 200)
    stored = database_call(lambda: persisted(shared))
    assert stored["version"] == 2 and stored["events"] == 2
    assert stored["pairs"] == [(employee_ids[winner], item_ids[winner])]
    report = {"users": 8, "employees_per_user": len(employee_ids), "items_per_employee": len(item_ids),
        "total_seconds_including_authentication": round(elapsed, 3), "sync_responses": [r[2] for r in results],
        "request_count": sum(len(r[0].measurements) for r in results), "tls_verified": bool(server.ca_file),
        "database_pairs_per_user": len(expected), "idempotent_repeats": 8,
        "shared_request_conflict": {"statuses": [status for status, _ in outcomes], "version": 2,
                                    "events": 2, "partial_changes": False},
        "scope": "isolated HTTP acceptance; eight authenticated users; not a production capacity estimate"}
    print(f"HTTP-приёмка: 8 пользователей × {len(employee_ids)} × {len(item_ids)}, повтор и конфликт 200/409 — успешно", flush=True)
    return report


def check_registry(page, base, actor, organization_id, organization_name):
    from playwright.sync_api import expect
    from workwear.models import Request
    from workwear.services import create_request
    from .browser_support import database_call, delayed_requests

    page_size = 30

    def seed():
        previous_count = Request.objects.count()
        for _ in range(page_size):
            create_request(organization_id=organization_id, actor=actor)
        return previous_count + page_size

    total = database_call(seed)
    page.goto(base + "/workwear/")
    counter = page.locator("#registryCount")
    footer = page.locator("#registryFooter")
    rows = page.locator("tr.registry-row")
    organization = page.get_by_role("combobox", name="Организация", exact=True)
    status = page.get_by_role("combobox", name="Состояние")
    search = page.get_by_role("textbox", name="Номер, ФИО или табельный")

    def expect_results(matched, shown):
        expect(counter).to_have_text(f"Найдено: {matched}")
        expect(rows).to_have_count(shown)

    expect_results(total, page_size)
    expect(footer).to_contain_text(f"Показано 1–{page_size} из {total}")
    next_page = page.get_by_role("button", name="Далее →")
    with delayed_requests(page, "**/ui/requests/?*"):
        next_page.dblclick()
        expect(next_page).to_be_disabled()
    expect_results(total, total - page_size)
    expect(footer).to_contain_text(f"Показано {page_size + 1}–{total} из {total}")
    page.get_by_role("button", name="← Назад").click()
    expect_results(total, page_size)
    organization.select_option(str(organization_id))
    expect_results(page_size, page_size)
    status.select_option("closed")
    expect_results(0, 0)
    expect(footer).to_have_text("")
    organization.select_option("")
    expect_results(1, 1)
    status.select_option("")
    expect_results(total, page_size)
    search.fill(organization_name)
    expect_results(page_size, page_size)
    search.fill("Учебный запрос без совпадений")
    expect_results(0, 0)
    page.get_by_role("button", name="Сбросить фильтры").click()
    expect_results(total, page_size)

    page.get_by_role("button", name="Период и сортировка").click()
    page.get_by_label("Начало периода").fill("2000-01-02")
    with page.expect_response(lambda response: "/ui/requests/?" in response.url
                              and "date_to=2000-01-01" in response.url) as invalid_period:
        page.get_by_label("Конец периода").fill("2000-01-01")
    response = invalid_period.value
    assert response.status == 400
    expect(page.get_by_text("Не удалось загрузить заявки")).to_be_visible()
    expect(counter).to_have_text("")
    expect(footer).to_have_text("")
    page.get_by_label("Конец периода").fill("")
    expect_results(total, page_size)
    expect(page.locator("#vq-period-error")).to_have_text("")
    page.get_by_role("button", name="Сбросить период и порядок").click()
    expect_results(total, page_size)
    print("Реестр: счётчик, страницы, фильтры, поиск, пустой результат и ошибка периода — успешно", flush=True)
    return response.status, response.url


def check_browser(actor, people, executable, engine="chromium", server_options=None):
    from playwright.sync_api import sync_playwright, expect
    password = secrets.token_urlsafe(32)
    actor.set_password(password)
    actor.save(update_fields=["password"])
    with live_server(**(server_options or {})) as server, sync_playwright() as playwright:
        base = server.url
        options = {"headless": True}
        if executable:
            options["executable_path"] = executable
        browser = getattr(playwright, engine).launch(**options)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, ignore_https_errors=server.ca_file is not None)
        page.set_default_timeout(10000)
        errors, failures = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("response", lambda response: failures.append((response.status, response.url)) if response.status >= 400 else None)
        sign_in(page, base, actor.username, password)
        page.goto(base + "/profile/")
        expect(page.locator('h1')).to_have_text("Профиль")
        expect(page.locator('dl')).to_contain_text(actor.username)
        page.locator('.vq-profile a[href="/password/change/"]').click()
        page.locator('[name="old_password"]').fill(password)
        new_password = secrets.token_urlsafe(32)
        page.locator('[name="new_password1"]').fill(new_password)
        page.locator('[name="new_password2"]').fill(new_password)
        page.get_by_role('button', name='Сохранить новый пароль').click()
        expect(page).to_have_url(base + "/password/change/done/")
        page.goto(base + "/")
        expect(page).to_have_url(base + "/")
        page.locator('a[href="/workwear/"]:visible').first.click()
        expect(page.locator('h1')).to_have_text("Заявки на спецодежду")
        page.locator('[data-action="create"]').click()
        page.locator('[data-action="create-now"]').click()
        expect(page.locator('h1')).to_have_text("Состав заявки")
        page.locator('[data-action="add"]').first.click()
        page.locator('#bulkText').fill("\n".join(p.personnel_number for p in people[:2]))
        page.locator('[data-action="check-bulk"]').click()
        page.locator('[data-action="apply-bulk"]').click()
        expect(page.locator('tbody tr[data-row]')).to_have_count(2)
        page.locator('[data-check="all"]').check()
        page.locator('[data-action="batch-add"]').click()
        page.locator('input[data-choice]').first.check()
        page.locator('[data-action="apply-batch"]').click()
        page.locator('[data-action="save"]').click()
        expect(page.locator('[data-action="save"]')).to_be_disabled()
        page.locator('[data-mode="matrix"]').click()
        expect(page.locator('table.matrix')).to_be_visible()
        page.locator('[data-action="finish"]').click()
        page.locator('#finishAck').check()
        page.locator('[data-action="apply-finish"]').click()
        expect(page.locator('[data-action="reopen"]')).to_be_visible()
        expect(page.locator('input[data-check]')).to_have_count(0)
        page.locator('[data-action="reopen"]').click()
        page.locator('#reopenAck').check()
        page.locator('[data-action="apply-reopen"]').click()
        expect(page.locator('[data-action="save"]')).to_be_visible()
        page.set_viewport_size({"width": 390, "height": 844})
        page.reload()
        expect(page.locator('h1')).to_have_text("Состав заявки")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Горизонтальный выход страницы за экран"
        draft_url = page.url
        
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.goto(base + "/admin/workwear/organization/add/")
        page.locator('[name="name"]').fill("Организация из браузерного теста")
        page.locator('[name="external_id"]').fill(str(uuid4()))
        page.locator('[name="requests_enabled"]').check()
        page.locator('[name="_save"]').click()
        expect(page.locator('#changelist')).to_be_visible()
        page.goto(base + "/admin/workwear/workwearitem/add/")
        page.locator('[name="code"]').fill("BROWSER-90")
        page.locator('[name="name"]').fill("Позиция из браузерного теста")
        page.locator('[name="category"]').fill("Новая категория из Admin")
        page.locator('[name="validity_days"]').fill("90")
        page.locator('[name="sort_order"]').fill("0")
        page.locator('[name="is_active"]').check()
        page.locator('[name="_save"]').click()
        expect(page.locator('#changelist')).to_be_visible()
        metadata = page.request.get(base + "/workwear/api/ui/metadata/").json()
        item = next(item for item in metadata["items"] if item["code"] == "BROWSER-90")
        added_org = next(org for org in metadata["organizations"]
            if org["label"] == "Организация из браузерного теста")
        page.goto(base + "/workwear/")
        page.locator('[data-action="create"]').click()
        expect(page.locator(f'#newOrg option[value="{added_org["value"]}"]')).to_have_text(added_org["label"])
        page.locator('#newOrg').select_option(str(people[0].organization_id))
        page.locator('[data-action="create-now"]').click()
        expect(page.locator('h1')).to_have_text("Состав заявки")
        page.locator('[data-action="add"]').first.click()
        page.locator('#bulkText').fill(people[0].personnel_number)
        page.locator('[data-action="check-bulk"]').click()
        page.locator('[data-action="apply-bulk"]').click()
        page.locator('[data-check="all"]').check()
        page.locator('[data-action="batch-add"]').click()
        page.locator(f'input[data-choice="{item["id"]}"]').check()
        page.locator('[data-action="apply-batch"]').click()
        page.locator('[data-action="save"]').click()
        expect(page.locator('[data-action="save"]')).to_be_disabled()
        page.locator('[data-action="finish"]').click()
        page.locator('#finishAck').check()
        page.locator('[data-action="apply-finish"]').click()
        expect(page.locator('[data-action="reopen"]')).to_be_visible()
        closed_url = page.url
        page.goto(base + f"/admin/workwear/workwearitem/{item["id"]}/change/")
        page.locator('[name="name"]').fill("Изменённое название после завершения")
        page.locator('[name="validity_days"]').fill("1095")
        page.locator('[name="is_active"]').uncheck()
        page.locator('[name="_save"]').click()
        expect(page.locator('#changelist')).to_be_visible()
        page.goto(closed_url)
        expect(page.locator('.vq-assignment-tags .vitem')).to_have_text("Позиция из браузерного теста")
        page.locator('[data-mode="matrix"]').click()
        expect(page.locator('.matrix-leaf')).to_contain_text("Изменённое название после завершения")
        expect(page.locator('.matrix-leaf')).to_contain_text("1095 дн. · каталог")
        expect(page.locator('[data-cell]')).to_have_attribute('title',
            "Позиция из браузерного теста · Новая категория из Admin · 90 дней")
        expected_failure = check_registry(page, base, actor, added_org["value"], added_org["label"])
        failures.remove(expected_failure)
        
        metadata = page.request.get(base + "/workwear/api/ui/metadata/").json()
        for org in metadata["organizations"]:
            page.goto(base + f'/admin/workwear/organization/{org["value"]}/change/')
            page.locator('[name="requests_enabled"]').uncheck()
            page.locator('[name="_save"]').click()
            expect(page.locator('#changelist')).to_be_visible()
        for catalog_item in metadata["items"]:
            if not catalog_item["is_active"]:
                continue
            page.goto(base + f'/admin/workwear/workwearitem/{catalog_item["id"]}/change/')
            page.locator('[name="is_active"]').uncheck()
            page.locator('[name="_save"]').click()
            expect(page.locator('#changelist')).to_be_visible()
        page.goto(base + "/workwear/")
        page.locator('[data-action="create"]').click()
        expect(page.locator('[data-action="create-now"]')).to_be_disabled()
        expect(page.locator('.vq-modal-body')).to_contain_text("Нет организаций, для которых разрешены заявки")
        page.goto(draft_url)
        expect(page.locator('h1')).to_have_text("Состав заявки")
        expect(page.locator('.vq-banner[role="status"]')).to_contain_text("Нет активных позиций спецодежды")
        
        page.goto(base + "/admin/auth/group/add/")
        page.locator('[name="name"]').fill("Группа браузерного теста")
        options = page.locator('#id_permissions_from option').evaluate_all("nodes => nodes.map(n => ({value:n.value,label:n.textContent}))")
        permission = next(item for item in options if 'view' in item['label'].lower() and 'заявка на спецодежду' in item['label'].lower())
        page.locator('#id_permissions_from').select_option(permission['value'])
        page.locator('#id_permissions_add').click()
        page.locator('[name="_save"]').click()
        expect(page.locator('#changelist')).to_contain_text("Группа браузерного теста")
        page.goto(base + "/admin/auth/user/add/")
        page.locator('[name="username"]').fill("rc_browser_user")
        user_password = secrets.token_urlsafe(32)
        page.locator('[name="password1"]').fill(user_password)
        page.locator('[name="password2"]').fill(user_password)
        page.locator('[name="_save"]').click()
        expect(page.locator('[name="username"]')).to_have_value("rc_browser_user")
        page.locator('#id_groups_from').select_option(label="Группа браузерного теста")
        page.locator('#id_groups_add').click()
        page.locator('[name="_save"]').click()
        expect(page.locator('#changelist')).to_contain_text("rc_browser_user")
        reader_context = browser.new_context(ignore_https_errors=server.ca_file is not None)
        reader = reader_context.new_page()
        sign_in(reader, base, "rc_browser_user", user_password)
        reader.goto(base + "/workwear/")
        expect(reader.locator('h1')).to_have_text("Заявки на спецодежду")
        assert reader.locator('[data-action="create"]').count() == 0
        reader_context.close()
        assert not errors, errors
        assert not failures, failures
        print(f"Браузер {browser.version}: вход, Portal, подбор, назначение, сохранение, матрица, завершение, возврат, ширина 390 px — успешно")
        print("Django Admin и интерфейс: новая организация, новая позиция со сроком 90 дней, переименование/отключение и сохранение истории — успешно")
        print("Профиль, смена пароля, создание пользователя/группы и назначение штатного разрешения через Admin — успешно")
        print("Пустые состояния: создание без разрешённых организаций заблокировано, отсутствие активных СИЗ объяснено — успешно")
        browser.close()


def main():
    """Временную схему удаляем и после ошибки проверки."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--browser-executable")
    parser.add_argument("--server", choices=["runserver", "nginx"], default="runserver")
    parser.add_argument("--nginx-executable", help="Путь к изолированному Nginx для HTTPS-приёмки")
    parser.add_argument("--http-load", action="store_true", help="Восемь одновременных HTTP-пользователей; требует --matrix")
    parser.add_argument("--engine", choices=["chromium", "firefox", "webkit"], default="chromium")
    parser.add_argument("--axe-script", help="Локальный axe.min.js для расширенной проверки доступности")
    parser.add_argument("--matrix", action="store_true", help="Расширенная приёмка матрицы и профилей нагрузки")
    parser.add_argument("--artifacts", default="test-results/rc", help="Скриншоты и измерения браузера")
    args = parser.parse_args()
    if args.http_load and not args.matrix:
        parser.error("--http-load требует --matrix")
    server_options = {"stack": args.server, "nginx_executable": args.nginx_executable}
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    from config.database import require_target
    from django.db import connection
    from .factories import make_user, make_import, make_employees, make_items
    require_target("dev", "veqta_dev", owner=True)
    if (args.browser or args.matrix) and not os.environ.get("DB_RUNTIME_PASSWORD"):
        raise RuntimeError("Браузерный сервер требует DB_RUNTIME_PASSWORD прикладной роли")
    with connection._nodb_cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", ["test_veqta_dev"])
        if cursor.fetchone():
            raise RuntimeError("test_veqta_dev уже существует; проверьте её отдельно перед удалением")
    original_name = connection.settings_dict["NAME"]
    connection.creation.create_test_db(verbosity=0, autoclobber=False, serialize=False)
    try:
        from .database_roles import grant_test_runtime_access
        grant_test_runtime_access()
        actor, snapshot = make_user(), make_import()
        people, items = make_employees(snapshot), make_items()
        check_race(actor, people, items)
        if args.browser:
            check_browser(actor, people, args.browser_executable, args.engine,
                server_options | {"artifacts": Path(args.artifacts) / "servers/basic"})
        if args.matrix:
            from .browser_matrix import check_matrix
            check_matrix(actor, args.browser_executable, Path(args.artifacts), args.engine, args.axe_script,
                server_options=server_options | {"artifacts": Path(args.artifacts) / "servers/matrix"}, http_load=args.http_load)
    finally:
        connection.creation.destroy_test_db(original_name, verbosity=0)


if __name__ == "__main__":
    main()
