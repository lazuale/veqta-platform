"""Проверки конкуренции и браузера в отдельной временной БД.

Запуск: python -m tests.acceptance [--browser] [--browser-executable /usr/bin/chromium].
База test_veqta_dev создаётся перед проверками и удаляется целиком после них.
Существующая тестовая база не перезаписывается. Рабочий справочник не изменяется.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
from threading import Barrier
import time
import urllib.request


def check_race(actor, people, items):
    """Две транзакции одной версии: ровно одна сохраняет состав, другая получает конфликт."""
    from django.db import connections
    from django.contrib.auth import get_user_model
    from workwear import services
    from workwear.models import Organization, WorkwearEvent
    from workwear.sync import save_draft
    req = services.create_request(organization_name=Organization.ALMAZ, actor=actor)
    barrier = Barrier(2)

    def worker(person):
        """Работать через отдельное соединение и закрыть его после транзакции."""
        try:
            user = get_user_model().objects.get(pk=actor.pk)
            barrier.wait(timeout=10)
            save_draft(request_id=req.pk, actor=user, expected_version=1,
                       members=[{"employee_id": person.pk, "item_ids": [items[0].pk]}])
            return "saved"
        except services.WorkwearConflict:
            return "conflict"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, people[:2]))
    assert sorted(results) == ["conflict", "saved"], results
    req.refresh_from_db()
    assert req.version == 2 and req.members.count() == 1
    assert WorkwearEvent.objects.filter(request_number=req.pk).count() == 2
    print("Конкурентное сохранение: одна успешная запись и один конфликт; частичных изменений нет")


def check_browser(actor, people, executable):
    """Пройти вход, массовый подбор, назначение, сохранение, завершение и возврат."""
    from playwright.sync_api import sync_playwright, expect
    password = secrets.token_urlsafe(32)
    actor.set_password(password)
    actor.save(update_fields=["password"])
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryFile() as log:
        server = subprocess.Popen([
            sys.executable, "manage.py", "runserver", f"127.0.0.1:{port}",
            "--noreload", "--settings=config.testing",
        ], cwd=root, stdout=log, stderr=log)
        try:
            for attempt in range(100):
                try:
                    urllib.request.urlopen(base+"/login/", timeout=1).close()
                    break
                except OSError:
                    if server.poll() is not None:
                        raise RuntimeError("Тестовый сервер завершился до запуска")
                    time.sleep(0.1)
            else:
                raise RuntimeError("Тестовый сервер не ответил")
            with sync_playwright() as playwright:
                options = {"headless": True}
                if executable:
                    options["executable_path"] = executable
                browser = playwright.chromium.launch(**options)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.set_default_timeout(10000)
                errors, failures = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("response", lambda response: failures.append((response.status, response.url)) if response.status >= 400 else None)
                page.goto(base+"/login/")
                page.locator('[name="username"]').fill(actor.username)
                page.locator('[name="password"]').fill(password)
                page.locator('button[type="submit"]').click()
                expect(page).to_have_url(base+"/")
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
                assert not errors, errors
                assert not failures, failures
                print(f"Браузер {browser.version}: вход, Portal, подбор, назначение, сохранение, матрица, завершение, возврат, ширина 390 px — успешно")
                browser.close()
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


def main():
    """Проверить DEV, создать временную схему и удалить её даже после ошибки теста."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--browser-executable")
    args = parser.parse_args()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    from config.database import require_target
    from django.db import connection
    from .factories import make_user, make_import, make_employees, make_items
    require_target("dev", "veqta_dev")
    with connection._nodb_cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", ["test_veqta_dev"])
        if cursor.fetchone():
            raise RuntimeError("test_veqta_dev уже существует; проверьте её отдельно перед удалением")
    original_name = connection.settings_dict["NAME"]
    connection.creation.create_test_db(verbosity=0, autoclobber=False, serialize=False)
    try:
        actor, snapshot = make_user(), make_import()
        people, items = make_employees(snapshot), make_items()
        check_race(actor, people, items)
        if args.browser:
            check_browser(actor, people, args.browser_executable)
    finally:
        connection.creation.destroy_test_db(original_name, verbosity=0)


if __name__ == "__main__":
    main()
