"""Проверяем матрицу в браузере и сверяем изменения с PostgreSQL."""
from collections import Counter
from datetime import timedelta
import json
from pathlib import Path
import secrets
from time import perf_counter
import traceback
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import uuid4

from .browser_support import database_call, delayed_requests, live_server, sign_in
from .browser_ui import check_ui


VIEWPORTS = [(1920, 1080), (1440, 900), (1366, 768), (1024, 768), (768, 1024), (390, 844)]


def seed_matrix(actor):
    from django.contrib.auth.models import Permission
    from django.utils import timezone
    from workwear import services
    from workwear.models import Employee, Organization, WorkwearItem
    from workwear.sync import save_draft
    from .factories import make_import, make_user
    WorkwearItem.objects.update(is_active=False)
    Organization.objects.update(requests_enabled=False)
    organization = Organization.objects.create(
        name="Испытания матрицы — обособленное производственно-эксплуатационное подразделение",
        external_id=str(uuid4()), requests_enabled=True,
    )
    source = make_import()
    people = Employee.objects.bulk_create([
        Employee(employee_guid=uuid4(), personnel_number=f"RC-{i:04d}", full_name=f"Сотрудник Испытаний {i:04d}",
            position_name="Тестовая должность", department_name=f"Тестовое подразделение {i % 7}",
            organization=organization, last_seen_import=source) for i in range(1000)
    ])
    items = WorkwearItem.objects.bulk_create([
        WorkwearItem(code=f"RC-{i:03d}", name=f"Тестовая позиция {i:02d} — длинное рабочее название",
            category=f"Категория {'АБВ'[i % 3]}", validity_days=(90, 365, 730)[i % 3], sort_order=100+i)
        for i in range(62)
    ])
    prior = services.create_request(organization_id=organization.pk, actor=actor)
    with patch("workwear.services.timezone.now", return_value=timezone.now()-timedelta(days=10)):
        save_draft(request_id=prior.pk, actor=actor, expected_version=1,
            members=[{"employee_id": people[0].pk, "item_ids": [items[0].pk]}], finish=True)
    req = services.create_request(organization_id=organization.pk, actor=actor)
    save_draft(request_id=req.pk, actor=actor, expected_version=1, members=[
        {"employee_id": person.pk, "item_ids": [items[0].pk] if i == 0 else
            [items[1].pk, items[2].pk] if i == 1 else [] if i == 2 else [items[i % 6].pk]}
        for i, person in enumerate(people[:210])
    ])
    benchmarks = {}
    for size in (1, 10, 100, 500, 1000):
        target = services.create_request(organization_id=organization.pk, actor=actor)
        save_draft(request_id=target.pk, actor=actor, expected_version=1, members=[
            {"employee_id": person.pk, "item_ids": [item.pk for item in items[:30]]}
            for person in people[:size]
        ])
        benchmarks[size] = target.pk
    # У первой строки эта позиция не назначена; у второй уже есть в истории состава.
    WorkwearItem.objects.filter(pk=items[1].pk).update(is_active=False)
    extra = {}
    for name in ("network", "conflict", "completion", "lookup"):
        target = services.create_request(organization_id=organization.pk, actor=actor)
        if name != "lookup":
            save_draft(request_id=target.pk, actor=actor, expected_version=1, members=[
                {"employee_id": people[0].pk, "item_ids": [items[0].pk]},
                {"employee_id": people[1].pk, "item_ids": [items[2].pk]},
                {"employee_id": people[2].pk, "item_ids": []},
            ])
        extra[name] = target.pk
    journal = services.create_request(organization_id=organization.pk, actor=actor)
    version = journal.version
    for index in range(28):
        result = save_draft(request_id=journal.pk, actor=actor, expected_version=version,
            members=[{"employee_id":people[0].pk,"item_ids":[items[0].pk]+([items[2].pk] if index % 2 else [])}])
        version = result.version
    completed = services.complete_request(request_id=journal.pk, actor=actor, expected_version=version)
    journal_reason = 'Проверка <img src=x onerror="window.rcAuditXss=1">'
    services.reopen_request(request_id=journal.pk, actor=actor, expected_version=completed.version, reason=journal_reason)
    extra['journal'] = journal.pk
    # Старая выгрузка получает дату до публикации, история не меняется после вставки.
    old_source = make_import(accepted_at=source.accepted_at-timedelta(days=1))
    Employee.objects.create(employee_guid=uuid4(), personnel_number="RC-OLD", full_name="Устаревший Тестовый Сотрудник",
        organization=organization, last_seen_import=old_source)
    other_org = Organization.objects.create(name="Другая тестовая организация", external_id=str(uuid4()))
    Employee.objects.create(employee_guid=uuid4(), personnel_number="RC-OTHER", full_name="Чужой Тестовый Сотрудник",
        organization=other_org, last_seen_import=source)
    Employee.objects.filter(pk__in=[p.pk for p in people[-2:]]).update(full_name="Совпадающий Тестовый Сотрудник")
    password = secrets.token_urlsafe(32)
    actor.set_password(password)
    actor.save(update_fields=["password"])
    reader = make_user(administrator=False)
    reader.user_permissions.add(*Permission.objects.filter(content_type__app_label="workwear", codename__in=[
        "view_request", "view_employee", "view_workwearitem", "view_workwearevent"]))
    reader_password = secrets.token_urlsafe(32)
    reader.set_password(reader_password)
    reader.save(update_fields=["password"])
    return {"request": req.pk, "previous_request": prior.pk, "people": [p.pk for p in people],
        "organization": organization.pk, "extra": extra, "journal_reason":journal_reason, "items": [{"id": i.pk, "name": i.name, "category": i.category,
            "validity_days": i.validity_days} for i in items], "benchmarks": benchmarks,
        "username": actor.username, "password": password, "reader": reader.username, "reader_password": reader_password}


def persisted(request_id):
    from workwear.models import Request, RequestAssignment, WorkwearEvent
    request = Request.objects.get(pk=request_id)
    return {"version": request.version, "status": request.status, "members": request.members.count(),
        "events": WorkwearEvent.objects.filter(request_number=request_id).count(),
        "pairs": sorted(RequestAssignment.objects.filter(request_employee__request_id=request_id)
                        .values_list("request_employee__employee_id", "item_id"))}


def check_matrix(actor, executable, artifacts, engine="chromium", axe_script=None, *, server_options=None,
                 http_load=False, ui_baseline=None):
    from playwright.sync_api import sync_playwright, expect
    data = seed_matrix(actor)
    artifacts = Path(artifacts).resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    from .acceptance import check_parallel_load
    from workwear.models import WorkwearItem
    concurrency = check_parallel_load(data['organization'], data['people'][:100],
        list(WorkwearItem.objects.filter(is_active=True).order_by('sort_order','pk').values_list('pk', flat=True)[:30]))
    (artifacts/'concurrent-service-results.json').write_text(json.dumps(concurrency,ensure_ascii=False,indent=2))
    results, errors, requests, failures, expected_http, console, sql_samples = [], [], [], [], [], [], []
    with live_server(**(server_options or {})) as server, sync_playwright() as playwright:
        base = server.url
        if http_load:
            from .acceptance import check_http_load
            active_ids = database_call(lambda: list(WorkwearItem.objects.filter(is_active=True)
                .order_by('sort_order','pk').values_list('pk', flat=True)[:30]))
            measurements = check_http_load(server, data['organization'], data['people'][:100], active_ids)
            (artifacts/'http-load-results.json').write_text(json.dumps(measurements,ensure_ascii=False,indent=2))
        options = {"headless": True}
        if executable:
            options["executable_path"] = executable
        engines = {name:Path(getattr(playwright,name).executable_path).is_file() for name in ("chromium","firefox","webkit")}
        engines[engine] = bool(executable) or engines[engine]
        browser = getattr(playwright, engine).launch(**options)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=server.ca_file is not None)
        page = context.new_page()
        page.set_default_timeout(15000)
        page.on("console",lambda message: console.append({"type":message.type,"text":message.text}) if message.type in ("warning","error") else None)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("response", lambda response: sql_samples.append(int(response.headers.get('x-veqta-test-sql', 0))))
        page.on("response", lambda response: failures.append((response.status, response.url)) if response.status >= 400 else None)
        page.on("request", lambda req: requests.append({"method": req.method, "url": req.url}))
        page.on("dialog", lambda dialog: dialog.accept())
        sign_in(page, base, data["username"], data["password"])

        def open_matrix(request_id=None):
            page.goto(base + f'/workwear/?request={request_id or data["request"]}')
            expect(page.locator('h1')).to_have_text("Состав заявки")
            page.locator('[data-mode="matrix"]').click()
            expect(page.locator('table.matrix')).to_be_visible()
            page.wait_for_function("!document.querySelector('[data-cell].on')?.textContent.includes('…')")
            return page

        def run(name, callback):
            started = perf_counter()
            try:
                details = callback() or {}
                results.append({"scenario": name, "status": "passed", "seconds": round(perf_counter()-started, 3), **details})
                print(f"Матрица: {name} — успешно", flush=True)
            except Exception as exc:
                path = artifacts / (name + "-failure.png")
                try:
                    page.screenshot(path=str(path), full_page=True)
                except Exception:
                    pass
                results.append({"scenario": name, "status": "failed", "error": str(exc), "traceback": traceback.format_exc()})
                print(f"Матрица: {name} — ОШИБКА: {exc}", flush=True)
            (artifacts / "matrix-results.json").write_text(json.dumps({"results": results, "javascript_errors": errors, "engines_available":engines, "console":console}, ensure_ascii=False, indent=2))

        def headers():
            open_matrix()
            columns = page.locator('th.matrix-leaf').evaluate_all("nodes => nodes.map(n => n.title)")
            groups = page.locator('th.matrix-group').evaluate_all("nodes => nodes.flatMap(n => Array(n.colSpan).fill(n.textContent.trim()))")
            by_name = {item["name"]: item for item in data["items"]}
            assert len(columns) == len(groups) == 30
            expected = [by_name[name]["category"] for name in columns]
            assert groups == expected, {"headers": groups, "columns": expected}
            api = page.request.get(base + f'/workwear/api/requests/{data["request"]}/').json()["request"]
            actual = database_call(lambda: persisted(data["request"]))
            api_pairs = sorted((member["employee_id"], assignment["item_id"]) for member in api["members"] for assignment in member["assignments"])
            assert api_pairs == actual["pairs"]
            for member in api["members"][:100]:
                row = page.locator(f'tr[data-row="{member["employee_id"]}"]')
                assert row.locator('td.sumcol').inner_text() == str(len(member["assignments"]))
                expected_ids = {a["item_id"] for a in member["assignments"]}
                for cell in row.locator('[data-cell]').all():
                    item_id = int(cell.get_attribute('data-cell').split(':')[1])
                    assert cell.get_attribute('aria-pressed') == str(item_id in expected_ids).lower()
            return {"rows": 210, "catalog": 62, "database_pairs": len(api_pairs)}

        def keyboard():
            open_matrix()
            first, second = data["people"][:2]
            first_item, inactive, third_item = [i["id"] for i in data["items"][:3]]
            cell = page.locator(f'[data-cell="{first}:{first_item}"]')
            cell.focus()
            assert cell.get_attribute('aria-label')
            page.keyboard.press('Shift+Tab')
            assert not cell.evaluate('el => el===document.activeElement')
            page.keyboard.press('Tab')
            expect(cell).to_be_focused()
            page.keyboard.press('Tab')
            assert not cell.evaluate('el => el===document.activeElement')
            page.keyboard.press('Shift+Tab')
            expect(cell).to_be_focused()
            page.keyboard.press('ArrowRight')
            expect(page.locator(f'[data-cell="{first}:{third_item}"]')).to_be_focused()
            page.keyboard.press('ArrowDown')
            expect(page.locator(f'[data-cell="{second}:{third_item}"]')).to_be_focused()
            page.keyboard.press('Home')
            expect(page.locator(f'[data-cell="{second}:{first_item}"]')).to_be_focused()
            page.keyboard.press('End')
            expect(page.locator(f'[data-cell="{second}:{data["items"][-1]["id"]}"]')).to_be_focused()
            page.keyboard.press('Control+End')
            last = page.locator(f'[data-cell="{data["people"][209]}:{data["items"][-1]["id"]}"]')
            expect(last).to_be_focused()
            page.keyboard.press('ArrowRight')
            page.keyboard.press('ArrowDown')
            expect(last).to_be_focused()
            page.keyboard.press('Control+Home')
            expect(page.locator(f'[data-cell="{first}:{first_item}"]')).to_be_focused()
            page.keyboard.press('PageDown')
            expect(page.locator(f'[data-cell="{data["people"][100]}:{first_item}"]')).to_be_focused()
            page.keyboard.press('PageUp')
            expect(page.locator(f'[data-cell="{first}:{first_item}"]')).to_be_focused()
            assert page.locator('[data-cell][tabindex="0"]').count() == 1
            for _ in range(20):
                page.keyboard.press('ArrowRight')
            bounds = page.locator('[data-cell]:focus').evaluate('''el => {
                const r=el.getBoundingClientRect(), row=el.closest('tr');
                return {left:r.left,right:r.right,sticky:row.querySelector('.sticky').getBoundingClientRect().right,
                    sum:row.querySelector('.sumcol').getBoundingClientRect().left};
            }''')
            assert bounds['left'] >= bounds['sticky'] and bounds['right'] <= bounds['sum'], bounds
            page.locator('[data-cell]:focus').hover()
            page.screenshot(path=str(artifacts/'matrix-keyboard-focus.png'),full_page=True)
            inactive_cell = page.locator(f'[data-cell="{second}:{inactive}"]')
            inactive_cell.focus()
            inactive_cell.press('Space')
            expect(inactive_cell).to_be_disabled()
            assert page.locator('[data-cell][tabindex="0"]:not(:disabled)').count() == 1
            assert page.locator('[data-cell]:focus:not(:disabled)').count() == 1
            open_matrix()
            return {"keys":["Tab","Shift+Tab","arrows","Home","End","Ctrl+Home","Ctrl+End","PageUp","PageDown"],"horizontal_focus_visible":True}

        def geometry():
            measurements = []
            for width, height in VIEWPORTS:
                page.set_viewport_size({"width": width, "height": height})
                open_matrix()
                page.screenshot(path=str(artifacts / f'matrix-{width}x{height}.png'), full_page=True)
                geometry = page.evaluate("""() => {
                    const w=document.querySelector('#editorTable .vq-tablewrap');
                    const h=document.querySelector('table.matrix thead');
                    const r=w.getBoundingClientRect();
                    return {pageWidth:document.documentElement.scrollWidth, viewport:innerWidth,
                        tableWidth:r.width,height:r.height,scrollWidth:w.scrollWidth,scrollHeight:w.scrollHeight,
                        headerHeight:h.getBoundingClientRect().height};
                }""")
                assert geometry["pageWidth"] <= width + 1, geometry
                assert geometry["height"] > geometry["headerHeight"] + 48, geometry
                assert geometry["scrollWidth"] > geometry["tableWidth"], geometry
                free_width = page.locator('table.matrix tbody tr').first.evaluate("row => row.closest('.vq-tablewrap').clientWidth-row.querySelector('.sticky').getBoundingClientRect().width-row.querySelector('.sumcol').getBoundingClientRect().width")
                assert free_width >= 92, {"free_width": free_width, **geometry}
                page.locator('#editorTable .vq-tablewrap').evaluate('el => {el.scrollLeft=800;el.scrollTop=400}')
                if width < 760:
                    page.locator('#main').evaluate("el => {const t=el.querySelector('#editorTable .vq-tablewrap');el.scrollTop+=t.getBoundingClientRect().top-el.getBoundingClientRect().top;}")
                page.screenshot(path=str(artifacts / f'matrix-{width}x{height}-scrolled.png'), full_page=True)
                geometry.update(width=width, viewport_height=height)
                measurements.append(geometry)
            return {"measurements": measurements}

        def api_request(request_id):
            return page.request.get(base + f'/workwear/api/requests/{request_id}/').json()["request"]

        def api_pairs(request_id):
            return sorted((m["employee_id"], a["item_id"]) for m in api_request(request_id)["members"] for a in m["assignments"])

        def save_and_check(request_id, expected_pairs, *, completion_timeout_ms=5_000):
            with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith(f'/requests/{request_id}/sync/')) as saved:
                page.locator('[data-action="save"]').click()
            assert saved.value.status == 200
            expect(page.locator('[data-action="save"]')).to_be_disabled()
            # HTTP 200 может прийти до обновления большой матрицы.
# Ждём с ограничением и сверяем результат по БД и API.
            expect(page.locator('#veq-app')).to_have_attribute(
                'aria-busy', 'false', timeout=completion_timeout_ms)
            page.wait_for_function("!document.querySelector('[data-cell].on')?.textContent.includes('…')")
            assert database_call(lambda: persisted(request_id))["pairs"] == sorted(expected_pairs)
            assert api_pairs(request_id) == sorted(expected_pairs)

        def cells_and_modes():
            page.set_viewport_size({"width": 1440, "height": 900})
            open_matrix()
            request_id = data["request"]
            original = database_call(lambda: persisted(request_id))
            person, item = data["people"][0], data["items"][3]["id"]
            selector = f'[data-cell="{person}:{item}"]'
            cell = page.locator(selector)
            expect(cell).to_have_attribute('aria-pressed', 'false')
            page.evaluate("""() => {window.rcNodes={main:document.querySelector('#main'),table:document.querySelector('table.matrix'),other:document.querySelectorAll('tr[data-row]')[1]};}""")
            before = len([r for r in requests if 'repeat-scan' in r["url"]])
            with delayed_requests(page, "**/repeat-scan/"):
                cell.dblclick()
                expect(page.locator(selector)).to_have_attribute('aria-busy', 'true')
                expect(page.locator('[data-action="save"]')).to_be_disabled()
                assert database_call(lambda: persisted(request_id)) == original
            expect(page.locator(selector)).to_have_attribute('aria-pressed', 'true')
            expect(page.locator('[data-action="save"]')).to_be_enabled()
            assert len([r for r in requests if 'repeat-scan' in r["url"]])-before == 1
            assert page.locator('[data-cell][tabindex="0"]').count() == 1
            assert page.evaluate("rcNodes.main===document.querySelector('#main') && rcNodes.table===document.querySelector('table.matrix') && rcNodes.other===document.querySelectorAll('tr[data-row]')[1]")
            expect(page.locator(selector)).to_be_focused()
            page.locator(selector).press('Space')
            expect(page.locator(selector)).to_have_attribute('aria-pressed', 'false')
            expect(page.locator('[data-action="save"]')).to_be_disabled()
            for index in (3, 4, 5):
                target = page.locator(f'[data-cell="{person}:{data["items"][index]["id"]}"]')
                target.click()
                expect(target).to_have_attribute('aria-pressed', 'true')
            second_person = data["people"][1]
            other_cell = page.locator(f'[data-cell="{second_person}:{item}"]')
            other_cell.click()
            expect(other_cell).to_have_attribute('aria-pressed','true')
            assert page.locator('[data-cell][tabindex="0"]').count()==1
            expect(other_cell).to_be_focused()
            expected = set(original["pairs"]) | {(person, data["items"][i]["id"]) for i in (3, 4, 5)} | {(second_person,item)}
            page.locator('[data-mode="list"]').click()
            expect(page.locator(f'tr[data-row="{person}"] .vitem')).to_have_count(4)
            page.locator(f'[data-person="{person}"]').first.click()
            expect(page.locator('.vq-drawer')).to_be_visible()
            expect(page.locator(f'input[data-item="{item}"]')).to_be_checked()
            page.keyboard.press('Escape')
            expect(page.locator('.vq-drawer')).to_have_count(0)
            page.locator('[data-mode="matrix"]').click()
            expect(page.locator(selector)).to_have_attribute('aria-pressed', 'true')
            save_and_check(request_id, expected)
            expect(page.locator(selector)).to_be_enabled()
            open_matrix()
            expect(page.locator(selector)).to_have_attribute('aria-pressed', 'true')
            assert database_call(lambda: persisted(request_id))["pairs"] == sorted(expected)
            # Enter снимает назначение; возвратом проверяем тот же обработчик мыши/клавиатуры.
            page.locator(selector).press('Enter')
            expect(page.locator(selector)).to_have_attribute('aria-pressed', 'false')
            save_and_check(request_id, expected-{(person,item)})
            return {"operations":"double click, pending, Space, Enter, add/remove, list/drawer/matrix, save/reload", "saved_pairs":len(expected)-1}

        def pagination_and_batch():
            open_matrix()
            request_id = data["request"]
            baseline = database_call(lambda: persisted(request_id))
            page.locator(f'[data-check="{data["people"][0]}"]').check()
            page.locator('[data-action="members-next"]').click()
            expect(page.locator('tr[data-row]').first).to_have_attribute('data-row', str(data["people"][100]))
            page.locator('[data-check="all"]').check()
            page.locator('[data-action="members-next"]').click()
            expect(page.locator('tr[data-row]')).to_have_count(10)
            expect(page.locator('#editorSelection')).to_contain_text('Выбрано 101')
            page.locator('[data-action="columns-next"]').click()
            expect(page.locator('th.matrix-leaf').first).to_have_attribute('title', data["items"][30]["name"])
            page.locator('[data-action="columns-next"]').click()
            expect(page.locator('th.matrix-leaf')).to_have_count(2)
            expect(page.locator('[data-action="columns-next"]')).to_be_disabled()
            page.locator('[data-action="columns-prev"]').click()
            page.locator('[data-action="columns-prev"]').click()
            assert database_call(lambda: persisted(request_id)) == baseline
            selected = {data["people"][0], *data["people"][100:200]}
            item_id = data["items"][3]["id"]
            def batch(mode):
                page.locator(f'[data-action="batch-{mode}"]').click()
                page.locator(f'[data-choice="{item_id}"]').check()
                page.locator('[data-action="apply-batch"]').click()
                expect(page.locator('.vq-modal')).to_have_count(0)
            batch('add')
            batch('add')
            expected = set(baseline["pairs"]) | {(person,item_id) for person in selected}
            save_and_check(request_id, expected)
            batch('remove')
            expected -= {(person,item_id) for person in selected}
            save_and_check(request_id, expected)
            open_matrix()
            assert database_call(lambda: persisted(request_id))["pairs"] == sorted(expected)
            page.locator('[data-check="all"]').check()
            page.locator('[data-check="all"]').uncheck()
            expect(page.locator('#editorSelection')).to_be_empty()
            return {"row_pages":3,"column_pages":3,"selected":101,"unselected_unchanged":True}

        def lookup_and_cancellation():
            request_id = data['extra']['lookup']
            page.goto(base + f'/workwear/?request={request_id}')
            expect(page.locator('h1')).to_have_text('Состав заявки')
            expect(page.locator('[data-action="finish"]')).to_be_disabled()
            original = database_call(lambda: persisted(request_id))
            page.locator('[data-action="add"]').first.click()
            page.locator('#bulkText').fill('\n'.join([
                'RC-0000', 'RC-0000', 'Совпадающий Тестовый Сотрудник',
                'RC-OLD', 'RC-OTHER', 'НЕСУЩЕСТВУЮЩИЙ-НОМЕР',
            ]))
            with page.expect_response(lambda r: r.url.endswith('/ui/resolve/')) as resolved:
                page.locator('[data-action="check-bulk"]').click()
            assert resolved.value.status == 200
            statuses = [r['status'] for r in resolved.value.json()['results']]
            assert statuses == ['matched','duplicate','ambiguous','outdated','other_organization','not_found'], statuses
            for text in ('Дубликат ввода','Неоднозначно','Нет в актуальном справочнике','Другая организация','Не найден'):
                expect(page.locator('.vq-modal-body')).to_contain_text(text)
            assert database_call(lambda: persisted(request_id)) == original
            page.locator('select[data-resolve]').select_option(str(data['people'][998]))
            expect(page.locator('#bulkReadyNote')).to_contain_text('2')
            page.locator('select[data-resolve]').select_option('')
            expect(page.locator('[data-action="apply-bulk"]')).to_contain_text('(1)')
            page.locator('select[data-resolve]').select_option(str(data['people'][998]))
            expect(page.locator('[data-action="apply-bulk"]')).to_contain_text('(2)')
            page.screenshot(path=str(artifacts/'bulk-lookup-states.png'),full_page=True)
            bulk_text = page.locator('#bulkText')
            revised = bulk_text.input_value() + '\nНЕСУЩЕСТВУЮЩИЙ-НОМЕР-2'
            bulk_text.fill(revised)
            expect(bulk_text).to_be_focused()
            expect(page.locator('.vq-result')).to_have_count(0)
            expect(page.locator('[data-action="apply-bulk"]')).to_be_hidden()
            expect(page.locator('#bulkLineCount')).to_have_text('7')
            expect(bulk_text).to_have_value(revised)
            page.locator('[data-action="check-bulk"]').click()
            page.locator('select[data-resolve]').select_option(str(data['people'][998]))
            page.locator('[data-action="apply-bulk"]').click()
            expect(page.locator('tr[data-row]')).to_have_count(2)
            page.locator('[data-action="add"]').first.click()
            page.locator('[data-tab="single"]').click()
            for index in (2,3):
                page.locator('#oneSearch').fill(f'000{index}')
                page.locator(f'[data-pick="{data["people"][index]}"]').check()
            expect(page.locator('#singlePickedNote')).to_contain_text('2')
            page.locator('[data-tab="bulk"]').click()
            page.locator('[data-tab="single"]').click()
            expect(page.locator('#singlePickedNote')).to_contain_text('2')
            page.locator('[data-action="apply-one"]').click()
            expect(page.locator('tr[data-row]')).to_have_count(4)
            page.locator('[data-check="all"]').check()
            page.locator('[data-action="batch-add"]').click()
            page.locator(f'[data-choice="{data["items"][0]["id"]}"]').check()
            page.locator('[data-action="apply-batch"]').click()
            expect(page.locator('.vq-modal')).to_have_count(0)
            expected = {(data['people'][i],data['items'][0]['id']) for i in (0,998,2,3)}
            save_and_check(request_id,expected)
            open_matrix(request_id)
            warning = page.locator(f'[data-cell="{data["people"][0]}:{data["items"][0]["id"]}"]')
            expect(warning).to_have_class(__import__('re').compile(r'warn'))
            saved = database_call(lambda: persisted(request_id))
            # Отмена во время чтения не добавляет найденных сотрудников позже.
            page.locator('[data-action="add"]').first.click()
            page.locator('#bulkText').fill('RC-0004')
            with delayed_requests(page, "**/ui/resolve/"):
                page.locator('[data-action="check-bulk"]').click()
                page.locator('[data-action="cancel"]').click()
            expect(page.locator('.vq-modal')).to_have_count(0)
            expect(page.locator('tr[data-row]')).to_have_count(4)
            assert database_call(lambda: persisted(request_id)) == saved
            # Массовый подбор учитывает удаление из локального черновика.
            person = data['people'][0]
            page.locator(f'[data-check="{person}"]').check()
            page.locator('[data-action="remove-selected"]').click()
            page.locator('[data-action="apply-remove"]').click()
            expect(page.locator('tr[data-row]')).to_have_count(3)
            page.locator('[data-action="add"]').first.click()
            page.locator('#bulkText').fill('RC-0000')
            page.locator('[data-action="check-bulk"]').click()
            expect(page.locator('[data-action="apply-bulk"]')).to_be_enabled()
            page.locator('[data-action="apply-bulk"]').click()
            expect(page.locator('tr[data-row]')).to_have_count(4)
            warning.click()
            expect(warning).to_have_attribute('aria-pressed', 'true')
            expect(page.locator('[data-action="save"]')).to_be_disabled()
            assert database_call(lambda: persisted(request_id)) == saved
            return {'statuses':statuses,'ambiguous_chosen_by_id':True,'partial_search':True,
                    'tab_selection_preserved':True,'cancelled_read_unchanged':True,'local_removal_resolved':True,
                    'edited_list_invalidates_old_results':True}

        def registry_after_delete():
            from workwear import services
            from workwear.models import Organization, Request

            def seed_page():
                organization = Organization.objects.create(name="Проверка страниц реестра", requests_enabled=True)
                drafts = [services.create_request(organization_id=organization.pk, actor=actor) for _ in range(31)]
                return organization.pk, drafts[0].pk

            organization_id, oldest_id = database_call(seed_page)
            page.goto(base + '/workwear/')
            page.locator('[data-filter="organization_id"]').select_option(str(organization_id))
            expect(page.locator('#registryCount')).to_have_text('Найдено: 31')
            page.locator('[data-action="registry-next"]').press('Enter')
            rows = page.locator('tr.registry-row')
            expect(rows).to_have_count(1)
            expect(page.locator('[data-action="registry-prev"]')).to_be_focused()
            rows.get_by_role('button', name=f'Открыть заявку {oldest_id}').click()
            page.locator('[data-action="delete-draft"]').click()
            page.locator('[data-action="confirm-delete"]').click()
            expect(page.locator('#registryCount')).to_have_text('Найдено: 30')
            expect(rows).to_have_count(30)
            expect(page.locator('[data-filter="organization_id"]')).to_have_value(str(organization_id))
            assert not database_call(lambda: Request.objects.filter(pk=oldest_id).exists())
            return {'visible_requests_after_delete': 30, 'organization_filter_preserved': True}

        def unsaved_navigation():
            request_id=data['extra']['lookup']
            open_matrix(request_id)
            original=database_call(lambda:persisted(request_id))
            person,item=data['people'][0],data['items'][4]['id']
            selector=f'[data-cell="{person}:{item}"]'
            page.locator(selector).click()
            expect(page.locator(selector)).to_have_attribute('aria-pressed','true')
            expect(page.locator('[data-action="save"]')).to_be_enabled()
            page.locator('[data-action="back"]').click()
            expect(page.locator('#modalTitle')).to_have_text('Несохранённые изменения')
            page.locator('[data-action="cancel"]').click()
            expect(page.locator(selector)).to_have_attribute('aria-pressed','true')
            assert database_call(lambda:persisted(request_id)) == original
            page.locator('[data-action="back"]').click()
            page.locator('[data-action="discard"]').click()
            expect(page.locator('h1')).to_have_text('Заявки на спецодежду')
            open_matrix(request_id)
            expect(page.locator(selector)).to_have_attribute('aria-pressed','false')
            assert database_call(lambda:persisted(request_id)) == original
            page.locator(selector).click()
            expect(page.locator('[data-action="save"]')).to_be_enabled()
            page.locator('[data-action="back"]').click()
            page.locator('[data-action="save-leave"]').click()
            expect(page.locator('h1')).to_have_text('Заявки на спецодежду')
            expected=set(original['pairs'])|{(person,item)}
            assert database_call(lambda:persisted(request_id))['pairs'] == sorted(expected)
            open_matrix(request_id)
            expect(page.locator(selector)).to_have_attribute('aria-pressed','true')
            return {'continue_preserved':True,'discard_unchanged':True,'save_and_leave_persisted':True}

        def network_and_conflict():
            request_id = data["extra"]["network"]
            open_matrix(request_id)
            original = database_call(lambda: persisted(request_id))
            person, item = data["people"][0], data["items"][3]["id"]
            selector=f'[data-cell="{person}:{item}"]'
            page.locator(selector).click()
            expect(page.locator(selector)).to_have_attribute('aria-pressed','true')
            endpoint=base+f'/workwear/api/ui/requests/{request_id}/sync/'
            expected_http.append((500,endpoint))
            page.route(endpoint, lambda route: route.fulfill(status=500,content_type='application/json',body='{}'), times=1)
            page.locator('[data-action="save"]').click()
            expect(page.locator('.vq-banner.error')).to_contain_text('Изменения не сохранены')
            assert database_call(lambda: persisted(request_id)) == original
            with page.expect_response(lambda r: r.request.method == 'POST' and r.url == endpoint) as retried:
                page.locator('[data-action="retry-save"]').click()
            assert retried.value.status == 200
            expect(page.locator('#veq-app')).to_have_attribute('aria-busy','false')
            expect(page.locator('[data-action="save"]')).to_be_disabled()
            expected=set(original["pairs"])|{(person,item)}
            assert database_call(lambda: persisted(request_id))["pairs"] == sorted(expected)
            page.locator(selector).click()
            expect(page.locator(selector)).to_have_attribute('aria-pressed','false')
            page.route(endpoint,lambda route:route.abort('connectionfailed'),times=1)
            page.locator('[data-action="save"]').click()
            expect(page.locator('.vq-banner.error')).to_contain_text('Изменения не сохранены')
            assert database_call(lambda: persisted(request_id))["pairs"] == sorted(expected)
            with page.expect_response(lambda r: r.request.method == 'POST' and r.url == endpoint) as retried:
                page.locator('[data-action="retry-save"]').click()
            assert retried.value.status == 200
            expect(page.locator('#veq-app')).to_have_attribute('aria-busy','false')
            expect(page.locator('[data-action="save"]')).to_be_disabled()
            assert database_call(lambda: persisted(request_id))["pairs"] == original["pairs"]
            # После успешного сохранения ошибка чтения не должна вызвать повторный POST.
            page.locator(selector).click()
            expect(page.locator(selector)).to_have_attribute('aria-pressed','true')
            page.route(base+f'/workwear/api/requests/{request_id}/',lambda route:route.abort('connectionfailed'),times=1)
            post_count=len([r for r in requests if r["method"]=='POST' and r["url"]==endpoint])
            page.locator('[data-action="save"]').click()
            expect(page.locator('.vq-banner')).to_contain_text('Сохранение подтверждено сервером')
            assert database_call(lambda: persisted(request_id))["pairs"] == sorted(expected)
            page.locator('[data-action="retry-refresh"]').click()
            expect(page.locator('.vq-banner')).to_have_count(0)
            assert len([r for r in requests if r["method"]=='POST' and r["url"]==endpoint])==post_count+1
            # Второй браузер сохраняет более новую версию той же заявки.
            request_id=data["extra"]["conflict"]
            open_matrix(request_id)
            second=page.context.new_page()
            second.goto(base+f'/workwear/?request={request_id}')
            second.locator('[data-mode="matrix"]').click()
            local=page.locator(f'[data-cell="{person}:{data["items"][4]["id"]}"]')
            local.click();expect(local).to_have_attribute('aria-pressed','true')
            remote=second.locator(f'[data-cell="{person}:{data["items"][5]["id"]}"]')
            remote.click();expect(remote).to_have_attribute('aria-pressed','true')
            second.locator('[data-action="save"]').click()
            expect(second.locator('#veq-app')).to_have_attribute('aria-busy','false')
            expect(second.locator('[data-action="save"]')).to_be_disabled()
            committed=database_call(lambda:persisted(request_id))
            endpoint=base+f'/workwear/api/ui/requests/{request_id}/sync/'
            expected_http.append((409,endpoint))
            page.locator('[data-action="save"]').click()
            expect(page.locator('#modalTitle')).to_have_text('Конфликт изменений')
            assert database_call(lambda:persisted(request_id))==committed
            page.locator('#reloadAck').check()
            page.locator('[data-action="reload"]').click()
            expect(page.locator('.vq-modal')).to_have_count(0)
            assert api_pairs(request_id)==committed["pairs"]
            page.locator('[data-mode="matrix"]').click()
            expect(page.locator(f'[data-cell="{person}:{data["items"][5]["id"]}"]')).to_have_attribute('aria-pressed','true')
            second.close()
            return {"faults":["HTTP 500","connection failure","GET after COMMIT failure","two-tab conflict"],"silent_loss":False}

        def close_history_reader():
            request_id=data["extra"]["completion"]
            open_matrix(request_id)
            initial=database_call(lambda:persisted(request_id))
            warning_cell=page.locator(f'[data-cell="{data["people"][0]}:{data["items"][0]["id"]}"]')
            expect(warning_cell).to_have_class(__import__('re').compile(r'warn'))
            page.locator(f'[data-person="{data["people"][0]}"]').first.click()
            page.locator(f'[data-history="{data["items"][0]["id"]}"]').click()
            expect(page.locator('.vq-item-history')).to_contain_text(f'Заявка № {data["previous_request"]}')
            page.locator(f'[data-prior="{data["previous_request"]}"]').click()
            expect(page.locator('.vq-preview-facts')).to_be_visible()
            page.keyboard.press('Escape')
            page.locator('[data-action="finish"]').click()
            expect(page.locator('.vq-modal-body')).to_contain_text('Автоматическое исключение')
            page.locator('#finishAck').check()
            page.locator('[data-action="apply-finish"]').click()
            expect(page.locator('[data-action="reopen"]')).to_be_visible()
            saved=database_call(lambda:persisted(request_id))
            assert saved["status"]=='closed' and saved["members"]==2 and saved["pairs"]==initial["pairs"]
            assert page.locator('[data-cell]:not(:disabled)').count()==0
            assert page.locator('[data-check]').count()==0
            # Меняем справочник штатным Admin; закрытая матрица сохраняет снимок.
            item=data["items"][0]
            person=data["people"][0]
            page.goto(base+f'/admin/workwear/workwearitem/{item["id"]}/change/')
            page.locator('[name="name"]').fill('Новое название после закрытия RC')
            page.locator('[name="category"]').fill('Новая категория после закрытия')
            page.locator('[name="validity_days"]').fill('1095')
            page.locator('[name="is_active"]').uncheck()
            page.locator('[name="_save"]').click()
            expect(page.locator('#changelist')).to_be_visible()
            open_matrix(request_id)
            expect(page.locator('th.matrix-leaf').first).to_have_attribute('title','Новое название после закрытия RC')
            expect(page.locator(f'[data-cell="{person}:{item["id"]}"]')).to_have_attribute('title',
                f'{item["name"]} · {item["category"]} · {item["validity_days"]} дней')
            expect(page.locator('th.matrix-leaf').first).to_contain_text('1095 дн. · каталог')
            page.screenshot(path=str(artifacts/'matrix-closed.png'),full_page=True)
            context=browser.new_context(viewport={"width":1440,"height":900}, ignore_https_errors=server.ca_file is not None)
            reader=context.new_page()
            sign_in(reader, base, data["reader"], data["reader_password"])
            reader.goto(base+f'/workwear/?request={data["request"]}')
            reader.locator('[data-mode="matrix"]').click()
            expect(reader.locator('table.matrix')).to_be_visible()
            assert reader.locator('[data-cell]:not(:disabled)').count()==0
            assert reader.locator('[data-action="save"]').count()==0
            session=reader.request.get(base+'/workwear/api/session/').json()
            result=reader.request.post(base+'/workwear/api/requests/',data={"organization_id":data["organization"]},headers={"X-CSRFToken":session['csrf_token'], "Referer": base+"/workwear/", "Origin": base})
            assert result.status==403 and result.json()["error"]["code"] == "forbidden"
            context.close()
            page.locator('[data-action="reopen"]').click()
            page.locator('#reopenAck').check()
            page.locator('[data-action="apply-reopen"]').click()
            expect(page.locator('[data-action="save"]')).to_be_visible()
            assert database_call(lambda:persisted(request_id))["status"]=='draft'
            events=page.request.get(base+f'/workwear/api/ui/requests/{request_id}/events/').json()["results"]
            assert {"created","draft_synced","completed","reopened"}<={event['event_type'] for event in events}
            return {"excluded_empty":1,"history_immutable":True,"reader_write_status":403}

        def event_journal():
            from workwear.models import WorkwearEvent
            from django.contrib.auth import get_user_model
            from django.contrib.auth.models import Permission
            request_id=data['extra']['journal']
            original=database_call(lambda:persisted(request_id))
            expected_ids=database_call(lambda:list(WorkwearEvent.objects.filter(request_number=request_id)
                .order_by('-occurred_at','-pk').values_list('pk',flat=True)))
            assert len(expected_ids)==31
            open_matrix(request_id)
            selector=f'[data-cell="{data["people"][0]}:{data["items"][4]["id"]}"]'
            page.locator(selector).click()
            expect(page.locator('[data-action="save"]')).to_be_enabled()
            page.evaluate('window.rcJournalMain=document.querySelector("#main")')
            page.locator('[data-action="events"]').click()
            expect(page.locator('#modalTitle')).to_have_text('История операций')
            expect(page.locator('[data-event]')).to_have_count(25)
            assert page.locator('[data-event]').evaluate_all('nodes=>nodes.map(n=>Number(n.dataset.event))')==expected_ids[:25]
            expect(page.locator('.vq-modal-body')).to_contain_text('Заявка возвращена в работу')
            expect(page.locator('.vq-modal-body')).to_contain_text('Заявка завершена')
            expect(page.locator('.vq-modal-body')).to_contain_text(data['journal_reason'])
            expect(page.locator('.vq-modal-body')).to_contain_text(data['username'])
            assert page.locator('.vq-modal img,.vq-modal script').count()==0
            assert page.evaluate('window.rcAuditXss===undefined')
            # Tab не выходит из журнала; сам журнал не изменяет черновик.
            page.locator('.vq-modal').focus()
            page.keyboard.press('Shift+Tab')
            expect(page.locator('[data-action="cancel"]')).to_be_focused()
            page.keyboard.press('Tab')
            expect(page.locator('[data-action="events-next"]')).to_be_focused()
            page.screenshot(path=str(artifacts/'event-journal-desktop.png'),full_page=True)
            page.locator('[data-action="events-next"]').click()
            expect(page.locator('[data-event]')).to_have_count(6)
            assert page.locator('[data-event]').evaluate_all('nodes=>nodes.map(n=>Number(n.dataset.event))')==expected_ids[25:]
            expect(page.locator('.vq-modal-body')).to_contain_text('Заявка создана')
            expect(page.locator('[data-action="events-next"]')).to_be_disabled()
            page.locator('[data-action="events-prev"]').click()
            expect(page.locator('[data-event]')).to_have_count(25)
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(artifacts/'event-journal-mobile.png'),full_page=True)
            page.keyboard.press('Escape')
            expect(page.locator('[data-action="events"]')).to_be_focused()
            expect(page.locator(selector)).to_have_attribute('aria-pressed','true')
            expect(page.locator('[data-action="save"]')).to_be_enabled()
            assert page.evaluate('window.rcJournalMain===document.querySelector("#main")')
            assert database_call(lambda:persisted(request_id))==original
            # Ошибка чтения и её повтор не записывают событие и не меняют состав.
            endpoint=base+f'/workwear/api/ui/requests/{request_id}/events/?offset=0&limit=25'
            expected_http.append((500,endpoint))
            page.route(endpoint,lambda route:route.fulfill(status=500,content_type='application/json',body='{}'),times=1)
            page.locator('[data-action="events"]').click()
            expect(page.get_by_role('alert')).to_contain_text('Историю не удалось загрузить')
            page.locator('[data-action="events-retry"]').click()
            expect(page.locator('[data-event]')).to_have_count(25)
            page.keyboard.press('Escape')
            page.route(endpoint,lambda route:route.fulfill(status=200,content_type='application/json',body='{}'),times=1)
            page.locator('[data-action="events"]').click()
            expect(page.get_by_role('alert')).to_contain_text('Историю не удалось загрузить')
            page.locator('[data-action="events-retry"]').click()
            expect(page.locator('[data-event]')).to_have_count(25)
            page.keyboard.press('Escape')
            # Отмена чтения во время задержки не открывает диалог повторно.
            with delayed_requests(page, endpoint):
                page.locator('[data-action="events"]').click()
                expect(page.get_by_role('status').filter(has_text='Загрузка истории')).to_be_visible()
                page.keyboard.press('Escape')
            page.locator('[data-action="events"]').click()
            expect(page.locator('[data-event]')).to_have_count(25)
            page.keyboard.press('Escape')
            assert database_call(lambda:persisted(request_id))==original
            # У читателя с view_workwearevent есть журнал без права изменения.
            reader_context=browser.new_context(viewport={'width':1440,'height':900}, ignore_https_errors=server.ca_file is not None)
            reader=reader_context.new_page()
            sign_in(reader, base, data['reader'], data['reader_password'])
            reader.goto(base+f'/workwear/?request={request_id}')
            reader.locator('[data-action="events"]').click()
            expect(reader.locator('[data-event]')).to_have_count(25)
            assert reader.locator('[data-action="save"]').count()==0
            def remove_history_permission():
                user=get_user_model().objects.get(username=data['reader'])
                user.user_permissions.remove(Permission.objects.get(content_type__app_label='workwear',codename='view_workwearevent'))
            database_call(remove_history_permission)
            reader.reload()
            expect(reader.locator('h1')).to_have_text('Состав заявки')
            assert reader.locator('[data-action="events"]').count()==0
            assert reader.request.get(endpoint).status==403
            reader_context.close()
            page.set_viewport_size({'width':1440,'height':900})
            return {'events':31,'pages':2,'database_unchanged':True,'unsaved_preserved':True,
                'retry_and_cancel':True,'malformed_response_rejected':True,'escaped_reason':True,'reader_and_denied_permissions':True}

        def historical_snapshots():
            from workwear import services
            from workwear.models import RequestAssignment, WorkwearItem
            item_id, request_id = database_call(lambda: (
                WorkwearItem.objects.create(code="RC-SNAPSHOT", name="Исходное имя позиции",
                    category="Исходная категория", validity_days=365, sort_order=0).pk,
                services.create_request(organization_id=data['organization'], actor=actor).pk,
            ))
            page.set_viewport_size({'width':1440,'height':900})
            page.goto(base + f'/workwear/?request={request_id}')

            def add_and_assign(index):
                page.locator('[data-action="add"]').first.click()
                page.locator('#bulkText').fill(f'RC-{index:04d}')
                page.locator('[data-action="check-bulk"]').click()
                page.locator('[data-action="apply-bulk"]').click()
                person = data['people'][index]
                page.locator(f'[data-person="{person}"]').first.click()
                page.locator(f'[data-item="{item_id}"]').check()
                page.locator('[data-action="close-drawer"]').last.click()
                # Успех записи проверяем по HTTP и БД, а не по состоянию кнопки.
                save_and_check(request_id, {(data['people'][i], item_id) for i in range(index+1)})

            add_and_assign(0)
            admin = context.new_page()
            def change_catalog(active):
                admin.goto(base + f'/admin/workwear/workwearitem/{item_id}/change/')
                admin.locator('[name="name"]').fill('Новое имя позиции')
                admin.locator('[name="category"]').fill('Новая категория')
                admin.locator('[name="validity_days"]').fill('90')
                admin.locator('[name="is_active"]').set_checked(active)
                admin.locator('[name="_save"]').click()
                expect(admin.locator('#changelist')).to_be_visible()
            change_catalog(True)
            page.reload()
            expect(page.locator('tr[data-row]')).to_have_count(1)
            add_and_assign(1)
            expected = {data['people'][0]:('Исходное имя позиции','Исходная категория',365),
                        data['people'][1]:('Новое имя позиции','Новая категория',90)}
            def verify():
                api = api_request(request_id)
                for member in api['members']:
                    a = member['assignments'][0]
                    assert (a['name'],a['category'],a['validity_days']) == expected[member['employee_id']]
                stored = database_call(lambda: list(RequestAssignment.objects.filter(
                    request_employee__request_id=request_id).values_list(
                    'request_employee__employee_id','snapshot_item_name','snapshot_item_category','snapshot_validity_days')))
                actual = {row[0]:row[1:] for row in stored}
                assert actual == expected, {'stored':actual, 'expected':expected}
                page.locator('[data-mode="list"]').click()
                for person, (name,category,days) in expected.items():
                    tag = page.locator(f'tr[data-row="{person}"] .vitem')
                    expect(tag).to_have_text(name)
                    expect(tag).to_have_attribute('title',f'{name} · {category} · {days} дней')
                page.locator('[data-mode="matrix"]').click()
                header = page.locator(f'#matrix-item-{item_id}')
                expect(header).to_have_attribute('title','Новое имя позиции')
                expect(header).to_contain_text('90 дн. · каталог')
                for person, (name,category,days) in expected.items():
                    cell = page.locator(f'[data-cell="{person}:{item_id}"]')
                    expect(cell).to_have_attribute('aria-pressed','true')
                    expect(cell).to_have_attribute('title',f'{name} · {category} · {days} дней')
                    expect(cell).to_have_attribute('aria-label',__import__('re').compile(f'^{name} · {category} · {days} дней: назначено'))
                    page.locator(f'[data-person="{person}"]').first.click()
                    row = page.locator('.vq-item-row').filter(has=page.locator(f'[data-item="{item_id}"]'))
                    expect(row.locator('label')).to_have_text(name)
                    expect(row.locator('.detail')).to_have_text(f'{category} · {days} дней')
                    page.locator('[data-action="close-drawer"]').last.click()
                return stored
            verify()
            change_catalog(False)
            page.reload()
            expect(page.locator('tr[data-row]')).to_have_count(2)
            verify()
            page.locator('[data-action="finish"]').click()
            page.locator('#finishAck').check()
            page.locator('[data-action="apply-finish"]').click()
            expect(page.locator('[data-action="reopen"]')).to_be_visible()
            verify()
            page.reload()
            expect(page.locator('tr[data-row]')).to_have_count(2)
            stored = verify()
            page.screenshot(path=str(artifacts/'historical-snapshots.png'),full_page=True)
            current = next(item for item in page.request.get(base+'/workwear/api/ui/metadata/').json()['items'] if item['id']==item_id)
            assert (current['name'],current['category'],current['validity_days'],current['is_active']) == ('Новое имя позиции','Новая категория',90,False)
            admin.close()
            return {'individual_snapshots':stored,'catalog':current,'closed_and_reloaded':True}

        def accessibility():
            open_matrix()
            structure = page.locator('table.matrix').evaluate('''table => ({
                rowHeaders:table.querySelectorAll('tbody th[scope="row"]').length,
                rows:table.tBodies[0].rows.length,
                colHeaders:table.querySelectorAll('thead th[scope="col"]').length,
                groups:table.querySelectorAll('th[scope="colgroup"]').length,
                colgroups:table.querySelectorAll('colgroup').length,
                caption:table.caption?.textContent,
                missingHeaders:[...table.querySelectorAll('tbody td[headers]')].filter(td=>
                    td.headers.split(' ').some(id=>!table.querySelector('[id="'+id+'"]'))).length,
                missingNames:[...table.querySelectorAll('button')].filter(b=>!b.textContent.trim()&&!b.getAttribute('aria-label')).length
            })''')
            assert structure['rowHeaders'] == structure['rows'] == 100, structure
            assert structure['colHeaders'] == 32 and structure['colgroups'] == structure['groups']+2, structure
            assert structure['missingHeaders'] == structure['missingNames'] == 0 and structure['caption'], structure
            snapshot = page.locator('table.matrix').aria_snapshot()
            assert 'rowheader' in snapshot and 'columnheader' in snapshot
            (artifacts/'matrix-accessibility.txt').write_text(snapshot,encoding='utf-8')
            return structure

        def benchmarks():
            measurements=[]
            page.set_viewport_size({"width":1440,"height":900})
            for size,request_id in data["benchmarks"].items():
                before=len(requests);before_sql=len(sql_samples);started=perf_counter()
                page.goto(base+f'/workwear/?request={request_id}')
                expect(page.locator('tr[data-row]')).to_have_count(min(100,size))
                opening=perf_counter()-started
                started=perf_counter();page.locator('[data-mode="matrix"]').click()
                expect(page.locator('table.matrix')).to_be_visible()
                page.wait_for_function("!document.querySelector('[data-cell].on')?.textContent.includes('…')")
                matrix=perf_counter()-started
                assert page.locator('[data-cell]').count()==min(100,size)*30
                paging=0
                if size>100:
                    started=perf_counter();page.locator('[data-action="members-next"]').click()
                    expect(page.locator('tr[data-row]').first).to_have_attribute('data-row',str(data['people'][100]))
                    paging=perf_counter()-started
                    page.locator('[data-action="members-prev"]').click()
                person,item=data['people'][0],data['items'][3]['id']
                cell=page.locator(f'[data-cell="{person}:{item}"]')
                started=perf_counter();cell.click();expect(cell).to_have_attribute('aria-pressed','false')
                mutation=perf_counter()-started
                expected=set(api_pairs(request_id))-{(person,item)}
                page.locator('[data-check="all"]').check()
                batch_item=data['items'][4]['id']
                started=perf_counter()
                page.locator('[data-action="batch-remove"]').click()
                page.locator(f'[data-choice="{batch_item}"]').check()
                page.locator('[data-action="apply-batch"]').click()
                expect(page.locator('.vq-modal')).to_have_count(0)
                batch=perf_counter()-started
                expected-={(p,batch_item) for p in data['people'][:min(size,100)]}
                started=perf_counter()
                save_and_check(request_id, expected, completion_timeout_ms=30_000 if size >= 500 else 5_000)
                saving=perf_counter()-started
                assert len(database_call(lambda:persisted(request_id))['pairs'])==size*30-1-min(size,100)
                measurements.append({"employees":size,"assignments_before":size*30,"opening_seconds":round(opening,3),
                    "matrix_seconds":round(matrix,3),"page_seconds":round(paging,3),"cell_seconds":round(mutation,3),
                    "batch_seconds":round(batch,3),"batch_employees":min(size,100),"save_seconds":round(saving,3),"requests":len(requests)-before,"api_requests":len([r for r in requests[before:] if "/workwear/api/" in r["url"]]),"dom_cells":min(100,size)*30,"dom_elements":page.locator("#veq-app *").count(),"server_sql_queries":sum(sql_samples[before_sql:]),
                    "request_counts":dict(Counter(r["method"]+" "+urlsplit(r["url"]).path for r in requests[before:]))})
                print(f"Нагрузка матрицы: {size} × 30 — " + str({k:v for k,v in measurements[-1].items() if k!='request_counts'}),flush=True)
            (artifacts/'matrix-performance.json').write_text(json.dumps(measurements,ensure_ascii=False,indent=2))
            return {"measurements":measurements}

        run("portal-and-workwear", lambda: check_ui(browser, server, data, VIEWPORTS,
            artifacts, axe_script, ui_baseline, server_options))
        run("headers-and-data", headers)
        run("accessibility", accessibility)
        run("keyboard-navigation", keyboard)
        run("responsive-geometry", geometry)
        run("cells-modes-save", cells_and_modes)
        run("pagination-and-batch", pagination_and_batch)
        run("bulk-lookup-and-cancel", lookup_and_cancellation)
        run("registry-pagination-after-delete", registry_after_delete)
        run("unsaved-navigation", unsaved_navigation)
        run("network-and-conflict", network_and_conflict)
        run("close-history-reader", close_history_reader)
        run("event-journal", event_journal)
        run("historical-snapshots", historical_snapshots)
        run("large-requests", benchmarks)
        summary = {
            "results": results, "completed": True,
            "passed": all(r['status']=='passed' for r in results) and not errors and sorted(failures)==sorted(expected_http),
            "server_stack": "nginx", "browser_engine": engine, "browser_version": browser.version, "engines_available": engines,
            "javascript_errors": errors, "console": console,
            "expected_http_errors": [{"status":status,"path":urlsplit(url).path} for status,url in expected_http],
            "observed_http_errors": [{"status":status,"path":urlsplit(url).path} for status,url in failures],
        }
        (artifacts/'matrix-results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
        assert not errors, errors
        assert sorted(failures)==sorted(expected_http), {"unexpected_http":failures,"expected_http":expected_http}
        browser.close()
    failed = [r for r in results if r["status"] == "failed"]
    if failed:
        raise AssertionError(f"Не пройдено сценариев матрицы: {len(failed)}; см. {artifacts / 'matrix-results.json'}")
