"""Проверки конкуренции и браузера в отдельной временной БД.

Запуск: python -m tests.acceptance [--browser] [--matrix] [--artifacts test-results/rc].
Свой браузер: --browser-executable /usr/bin/chromium.
База test_veqta_dev создаётся перед проверками и удаляется целиком после них.
Существующая тестовая база не перезаписывается. Рабочий справочник не изменяется.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import secrets
from threading import Barrier
from uuid import uuid4
from .browser_support import live_server


def check_race(actor, people, items):
    """Две транзакции одной версии: ровно одна сохраняет состав, другая получает конфликт."""
    from django.db import connections
    from django.contrib.auth import get_user_model
    from workwear import services
    from workwear.models import WorkwearEvent
    from workwear.sync import save_draft
    req = services.create_request(organization_id=people[0].organization_id, actor=actor)
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
    with live_server() as base, sync_playwright() as playwright:
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
        # Добавляем справочники нативными формами и используем новую позицию в UI.
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
        expect(page.locator('.matrix-leaf')).to_contain_text("Позиция из браузерного теста")
        expect(page.locator('.matrix-leaf')).to_contain_text("90 дн.")
        # Отсутствие доступных справочников проверяем через реальные формы Admin.
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
        # Штатные GroupAdmin и UserAdmin: создаём группу и пользователя,
        # назначаем стандартное разрешение и проверяем вход в отдельном сеансе.
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
        reader_context = browser.new_context()
        reader = reader_context.new_page()
        reader.goto(base + "/login/")
        reader.locator('[name="username"]').fill("rc_browser_user")
        reader.locator('[name="password"]').fill(user_password)
        reader.locator('button[type="submit"]').click()
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
    """Проверить DEV, создать временную схему и удалить её даже после ошибки теста."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--browser-executable")
    parser.add_argument("--matrix", action="store_true", help="Расширенная приёмка матрицы и профилей нагрузки")
    parser.add_argument("--artifacts", default="test-results/rc", help="Скриншоты и измерения браузера")
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
        if args.matrix:
            from .browser_matrix import check_matrix
            check_matrix(actor, args.browser_executable, Path(args.artifacts))
    finally:
        connection.creation.destroy_test_db(original_name, verbosity=0)


if __name__ == "__main__":
    main()
