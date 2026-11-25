"""Проверяем Portal и Workwear через браузер на общей контрольной выборке."""
import base64
import json
from pathlib import Path

from .browser_support import live_server, sign_in


def axe_report(page, script):
    if not page.evaluate("typeof axe !== 'undefined'"):
        page.add_script_tag(path=str(Path(script).resolve()))
    return page.evaluate("""async () => {
        const {violations, incomplete, passes} = await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}
        });
        const describe = ({id, impact, nodes}) => ({
            id, impact, nodes: nodes.map(({target, failureSummary}) => ({target, failureSummary}))
        });
        return {
            version: axe.version,
            violations: violations.map(describe),
            incomplete: incomplete.map(describe),
            passed: passes.length
        };
    }""")


def capture_screens(browser, server, data, viewports, destination, axe_script, *, reference=False):
    from playwright.sync_api import expect

    destination.mkdir(parents=True, exist_ok=True)
    audits, geometry, errors = {}, {}, []
    context = browser.new_context(locale="ru-RU", timezone_id="Europe/Moscow", reduced_motion="reduce",
        ignore_https_errors=server.ca_file is not None)
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    base = server.url
    sign_in(page, base, data["username"], data["password"])
    editor = f"/workwear/?request={data['request']}"
    request_row = page.locator(f'tr[data-open="{data["request"]}"] button')
    cell_header = page.locator('.matrix thead th.sticky')
    modal = page.locator('.vq-modal')
    drawer = page.locator('.vq-drawer')

    def open_page(path, ready):
        page.goto(base + path)
        expect(page.locator(ready).first).to_be_visible()
        expect(page.locator("html")).to_have_attribute("data-vq-shell-ready", "1")

    def capture(name, *, detailed=False):
        filename = f"{width}-{name}"
        page.mouse.move(0, 0)
        page.screenshot(path=destination / f"{filename}.png", animations="disabled", caret="hide")
        geometry[filename] = page.evaluate("""() => ({
            document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
            containers: [...document.querySelectorAll('.vq-main, .vq-toolbar, .vq-modal-body, .vq-modal-footer, .vq-drawer-body, .vq-drawer-foot')]
                .filter(el => el.clientWidth > 0)
                .map(el => ({element: el.className, overflow: el.scrollWidth - el.clientWidth}))
        })""")
        if axe_script and detailed:
            audits[filename] = axe_report(page, axe_script)

    try:
        if not reference:
            expect(page.locator("html")).to_have_attribute("data-vq-shell-ready", "1")
            painted = page.evaluate("""async () => {
                const styles = getComputedStyle(document.documentElement);
                const canvas = document.createElement('canvas');
                canvas.width = canvas.height = 24;
                const context = canvas.getContext('2d');
                const result = {};
                for (const name of ['icon-check', 'icon-alert', 'icon-chevron-down', 'select-chevron']) {
                    const url = styles.getPropertyValue('--vq-' + name).trim();
                    const image = new Image();
                    image.src = url.slice(5, -2);
                    await image.decode();
                    context.clearRect(0, 0, 24, 24);
                    context.drawImage(image, 0, 0, 24, 24);
                    result[name] = context.getImageData(0, 0, 24, 24).data.some((value, i) => i % 4 === 3 && value);
                }
                return result;
            }""")
            assert all(painted.values()), painted
        for width, height in viewports:
            page.set_viewport_size({"width": width, "height": height})
            detailed = width in (1440, 390)
            open_page("/login/", '[name="username"]')
            capture("login", detailed=detailed)
            if detailed:
                page.locator('[name="username"]').fill(data["username"])
                page.locator('[name="password"]').fill("Неверный пароль")
                page.locator('main form button[type="submit"]').click()
                expect(page.get_by_role("alert")).to_contain_text("Проверьте логин и пароль")
                capture("login-error", detailed=True)

            open_page("/", ".vq-app-entry")
            capture("portal", detailed=detailed)
            if detailed:
                menus = page.locator("details.vq-menu")
                for index, name in enumerate(("apps-menu", "account-menu")):
                    trigger = menus.nth(index).locator("summary")
                    trigger.press("Enter")
                    expect(menus.nth(index).locator(".vq-menu-popover")).to_be_visible()
                    capture(name, detailed=True)
                    page.keyboard.press("Escape")
                    expect(trigger).to_be_focused()
                    expect(menus.nth(index).locator(".vq-menu-popover")).to_be_hidden()

            open_page("/profile/", ".vq-profile")
            capture("profile", detailed=detailed)
            open_page("/password/change/", '[name="old_password"]')
            capture("password", detailed=detailed)
            if detailed:
                page.locator('[name="old_password"]').fill("Неверный пароль")
                page.locator('[name="new_password1"]').fill("ПроверочнаяСтрока2026")
                page.locator('[name="new_password2"]').fill("НеСовпадает2026")
                page.locator('main form button[type="submit"]').click()
                expect(page.locator('[name="old_password"]')).to_have_attribute("aria-invalid", "true")
                capture("password-error", detailed=True)

            open_page("/workwear/", ".vq-registry-table")
            capture("registry", detailed=detailed)
            filters = page.locator('[data-action="toggle-filters"]')
            filters.press("Enter")
            expect(filters).to_have_attribute("aria-expanded", "true")
            capture("registry-filters", detailed=detailed)
            if detailed:
                page.locator('[data-filter="date_from"]').fill("2026-11-25")
                page.locator('[data-filter="date_to"]').fill("2026-11-01")
                expect(page.locator("#vq-period-error")).not_to_be_empty()
                capture("registry-error", detailed=True)
                page.locator('[data-action="reset-period"]').click()
                expect(page.locator(".vq-registry-table")).to_be_visible()

                create = page.locator('[data-action="create"]')
                create.press("Enter")
                expect(modal).to_be_focused()
                if not reference:
                    for selector in (".vq-skip", ".vq-header", "#main"):
                        expect(page.locator(selector)).to_have_js_property("inert", True)
                capture("create", detailed=True)
                page.keyboard.press("Shift+Tab")
                expect(modal.locator('[data-action="create-now"]')).to_be_focused()
                page.keyboard.press("Tab")
                expect(modal.locator("#newOrg")).to_be_focused()
                page.keyboard.press("Escape")
                expect(create).to_be_focused()
                if not reference:
                    expect(page.locator(".vq-skip")).to_have_js_property("inert", False)

                request_row.press("Enter")
                expect(page).to_have_url(base + editor)
                if not reference:
                    expect(page.get_by_role("heading", level=1)).to_be_focused()
            else:
                open_page(editor, ".vq-member-list, #editorTable table")

            expect(page.locator(".vq-repeat-trigger").first).to_be_visible()
            capture("list", detailed=detailed)
            page.locator('[data-mode="matrix"]').click()
            expect(cell_header).to_be_visible()
            expect(page.locator(".gridpick.on .vq-cell-check").first).to_be_attached()
            capture("matrix", detailed=detailed)
            if detailed:
                viewport = page.locator(".vq-tablewrap:has(.matrix)")
                viewport.evaluate("el => { el.scrollTop = 300; el.scrollLeft = 270; }")
                capture("matrix-scrolled")
                alignment = viewport.evaluate("""el => {
                    const row = el.querySelector('tbody tr');
                    return Math.abs(row.querySelector('.sticky').getBoundingClientRect().top -
                        row.getBoundingClientRect().top);
                }""")
                geometry[f"{width}-matrix-scrolled"]["row_header_vertical_offset"] = alignment
                if not reference:
                    assert alignment <= 1, {"width": width, "row_header_vertical_offset": alignment}
                viewport.evaluate("el => { el.scrollTop = 0; el.scrollLeft = 0; }")

            add = page.locator('.vq-toolbar [data-action="add"]')
            add.press("Enter")
            expect(modal).to_be_visible()
            capture("bulk-picker", detailed=detailed)
            if detailed:
                page.locator("#bulkText").fill("RC-0998\nНЕ НАЙДЕНО")
                page.locator('[data-action="check-bulk"]').click()
                expect(page.locator(".vq-result-header").first).to_be_visible()
                capture("bulk-results", detailed=True)
            single = page.locator('[data-tab="single"]')
            single.click()
            if not reference:
                expect(single).to_have_attribute("aria-pressed", "true")
            page.locator("#oneSearch").fill("RC-0998")
            expect(page.locator("[data-pick]")).to_be_visible()
            capture("employee-picker", detailed=detailed)
            if detailed:
                page.locator("[data-pick]").check()
                expect(page.locator('[data-action="picker-remove"]')).to_be_visible()
                capture("employee-selection", detailed=True)
            page.keyboard.press("Escape")
            expect(add).to_be_focused()

            person = page.locator(f'[data-person="{data["people"][0]}"]').first
            person.press("Enter")
            expect(drawer).to_be_visible()
            history = drawer.locator(f'[data-history="{data["items"][0]["id"]}"]')
            expect(history).to_contain_text("1 зап.")
            capture("employee-card", detailed=detailed)
            if detailed:
                history.press("Enter")
                previous = drawer.locator("[data-prior]").first
                expect(previous).to_be_visible()
                capture("item-history", detailed=True)
                previous.click()
                expect(drawer.get_by_role("link", name="Полная заявка · новая вкладка")).to_be_visible()
                capture("archive-preview", detailed=True)
            page.keyboard.press("Escape")
            expect(person).to_be_focused()

            if detailed:
                journal = page.locator('[data-action="events"]')
                journal.press("Enter")
                expect(modal.locator(".vq-event-list")).to_be_visible()
                capture("journal", detailed=True)
                page.keyboard.press("Escape")
                expect(journal).to_be_focused()

                page.locator('[data-action="finish"]').press("Enter")
                expect(modal).to_be_visible()
                capture("finish", detailed=True)
                page.keyboard.press("Escape")
    except Exception:
        page.screenshot(path=destination.parent / f"{destination.name}-failure.png", animations="disabled")
        raise
    finally:
        (destination / "geometry.json").write_text(json.dumps(geometry, ensure_ascii=False, indent=2))
        (destination / "accessibility.json").write_text(json.dumps(audits, ensure_ascii=False, indent=2))
        (destination / "javascript-errors.json").write_text(json.dumps(errors, ensure_ascii=False, indent=2))
        context.close()

    violations = {name: result["violations"] for name, result in audits.items() if result["violations"]}
    overflow = {name: result for name, result in geometry.items() if result["document"] > 1 or
                any(container["overflow"] > 1 for container in result["containers"])}
    return {"screens": len(geometry), "axe_scans": len(audits), "violations": violations,
            "overflow": overflow, "javascript_errors": errors}


# Снимки каждого движка сравниваем только с ним же. Допуск по цвету убирает шум сглаживания.
DIFF_SCRIPT = """async ([first, second]) => {
    async function load(data) {
        const image = new Image();
        image.src = 'data:image/png;base64,' + data;
        await image.decode();
        return image;
    }
    const [before, after] = await Promise.all([load(first), load(second)]);
    const canvas = document.createElement('canvas');
    canvas.width = before.width;
    canvas.height = before.height;
    if (before.width !== after.width || before.height !== after.height) {
        return {before: [before.width, before.height], after: [after.width, after.height], ratio: 1};
    }
    const context = canvas.getContext('2d', {willReadFrequently: true});
    context.drawImage(before, 0, 0);
    const original = context.getImageData(0, 0, canvas.width, canvas.height);
    context.drawImage(after, 0, 0);
    const current = context.getImageData(0, 0, canvas.width, canvas.height);
    const {width, height} = canvas;
    const close = (a, i, b, j) => Math.abs(a[i] - b[j]) <= 16 &&
        Math.abs(a[i + 1] - b[j + 1]) <= 16 && Math.abs(a[i + 2] - b[j + 2]) <= 16;
    function matchesNearby(a, i, b, x, y) {
        for (let row = Math.max(0, y - 2); row <= Math.min(height - 1, y + 2); row++) {
            for (let col = Math.max(0, x - 2); col <= Math.min(width - 1, x + 2); col++) {
                if (close(a, i, b, (row * width + col) * 4)) return true;
            }
        }
        return false;
    }
    const annotated = new ImageData(new Uint8ClampedArray(current.data), width, height);
    let changed = 0, shifted = 0;
    for (let i = 0; i < original.data.length; i += 4) {
        if (!close(original.data, i, current.data, i)) {
            changed++;
            const x = (i / 4) % width, y = Math.floor(i / 4 / width);
            if (matchesNearby(original.data, i, current.data, x, y) &&
                matchesNearby(current.data, i, original.data, x, y)) shifted++;
            annotated.data.set([204, 33, 78, 255], i);
        }
    }
    context.putImageData(annotated, 0, 0);
    return {ratio: (changed - shifted) / (width * height), raw_ratio: changed / (width * height),
        png: changed ? canvas.toDataURL('image/png').split(',')[1] : null};
}"""


def compare_screens(browser, destination):
    before = {path.name: path for path in (destination / "before").glob("*.png")}
    after = {path.name: path for path in (destination / "after").glob("*.png")}
    assert before and before.keys() == after.keys(), sorted(before.keys() ^ after.keys())
    diff = destination / "diff"
    diff.mkdir(exist_ok=True)
    page = browser.new_page()
    comparisons = {}
    try:
        for name in sorted(before):
            images = [before[name].read_bytes(), after[name].read_bytes()]
            if images[0] == images[1]:
                comparisons[name] = {"ratio": 0}
                continue
            result = page.evaluate(DIFF_SCRIPT, [base64.b64encode(image).decode("ascii") for image in images])
            if png := result.pop("png", None):
                (diff / name).write_bytes(base64.b64decode(png))
            comparisons[name] = result
    finally:
        page.close()
    return comparisons


def check_touch_controls(browser, server, data, destination):
    from playwright.sync_api import expect

    context = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True,
        locale="ru-RU", reduced_motion="reduce", ignore_https_errors=server.ca_file is not None)
    page = context.new_page()
    measured = {}

    def check(name, selector, *, input_selector=None):
        control = page.locator(selector).first
        expect(control).to_be_visible()
        height = control.evaluate("el => el.getBoundingClientRect().height")
        assert height >= 44, {name: height}
        reading = {"height": height}
        if input_selector:
            font = page.locator(input_selector).first.evaluate("el => getComputedStyle(el).fontSize")
            assert font == "16px", {name: font}
            reading["font_size"] = font
        measured[name] = reading

    try:
        page.goto(server.url + "/login/")
        assert page.evaluate("matchMedia('(pointer: coarse)').matches")
        check("login_input", '[name="username"]', input_selector='[name="username"]')
        check("login_submit", 'main button[type="submit"]')
        page.screenshot(path=destination / "touch-login.png", animations="disabled")
        sign_in(page, server.url, data["username"], data["password"])
        check("navigation", '.vq-menu-trigger')

        page.goto(server.url + "/workwear/")
        check("registry_search", '.vq-registry-tools .vq-field', input_selector='[data-input="registry-search"]')
        check("registry_select", '[data-filter="status"]', input_selector='[data-filter="status"]')
        trigger = page.locator('[data-action="toggle-filters"]')
        trigger.tap()
        expect(trigger).to_have_attribute("aria-expanded", "true")
        check("registry_date", '[data-filter="date_from"]', input_selector='[data-filter="date_from"]')
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=destination / "touch-filters.png", animations="disabled")

        page.goto(server.url + f"/workwear/?request={data['request']}")
        page.locator('.vq-toolbar [data-action="add"]').tap()
        expect(page.get_by_role("dialog")).to_be_visible()
        check("bulk_input", "#bulkText", input_selector="#bulkText")
        page.locator('[data-tab="single"]').tap()
        check("picker_search", '.vq-picker-input', input_selector='#oneSearch')
        assert page.locator('.vq-modal-body').evaluate("el => el.scrollWidth <= el.clientWidth + 1")
        page.screenshot(path=destination / "touch-picker.png", animations="disabled")
    finally:
        (destination / "touch.json").write_text(json.dumps(measured, ensure_ascii=False, indent=2))
        context.close()
    return measured


def check_ui(browser, server, data, viewports, artifacts, axe_script, reference, server_options):
    target = artifacts / "ui"
    current = capture_screens(browser, server, data, viewports, target / "after", axe_script)
    current["touch"] = check_touch_controls(browser, server, data, target)
    summary = {"current": current}
    if reference:
        with live_server(source_root=reference, nginx_executable=(server_options or {}).get("nginx_executable")) as original:
            summary["reference"] = capture_screens(browser, original, data, viewports,
                target / "before", axe_script, reference=True)
        summary["comparisons"] = compare_screens(browser, target)
    (target / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    assert not current["violations"], current["violations"]
    assert not current["overflow"], current["overflow"]
    assert not current["javascript_errors"], current["javascript_errors"]
    # Большие сдвиги требуют осмотра снимков. Геометрия и фокус проверяются отдельно.
    regressions = {name: diff for name, diff in summary.get("comparisons", {}).items() if diff["ratio"] > .05}
    assert not regressions, regressions
    return {"screens": current["screens"], "axe_scans": current["axe_scans"],
            "visual_comparisons": len(summary.get("comparisons", {}))}
