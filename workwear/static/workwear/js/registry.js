/* Реестр и URL-навигация Workwear. installRegistry(ctx) публикует функции
 * маршрутизации/загрузки/фильтрации через ctx; DOM меняется только внутри main.
 * settings.registry хранит поиск, фильтры и страницу. pageEpoch защищает
 * ответы списка, refreshEpoch отменяет открытие заявки после перехода назад.
 * Несохранённые изменения требуют явного решения; историю URL ведёт History API. */
export function installRegistry(ctx) {
    const { settings, main } = ctx;
    const openRequest = (...args) => ctx.openRequest(...args);
    const can = (...args) => ctx.can(...args);
    const btn = (...args) => ctx.btn(...args);
    const icon = (...args) => ctx.icon(...args);
    const field = (...args) => ctx.field(...args);
    const allOrgOptions = (...args) => ctx.allOrgOptions(...args);
    const esc = (...args) => ctx.esc(...args);
    const renderOverlay = (...args) => ctx.renderOverlay(...args);
    const $ = (...args) => ctx.$(...args);
    const api = (...args) => ctx.api(...args);
    const fmt = (...args) => ctx.fmt(...args);
    const fmtTime = (...args) => ctx.fmtTime(...args);
    const tone = (...args) => ctx.tone(...args);
    function routeUrl(id) { const u = new URL(location.href); if (id)
        u.searchParams.set('request', id);
    else
        u.searchParams.delete('request'); return u.pathname + u.search + u.hash; }
    function syncUrl(id, replace = false) { const url = routeUrl(id); if (location.pathname + location.search + location.hash !== url)
        history[replace ? 'replaceState' : 'pushState']({ request: id }, '', url); }
    function finishRoute(id) { settings.pendingRoute = null; syncUrl(id, true); if (id)
        openRequest(id, { route: false });
    else
        openRegistry({ route: false }); }
    function toolbarOptionsStatus(v) { return `<option value="" ${v === '' ? 'selected' : ''}>Все состояния</option><option value="draft" ${v === 'draft' ? 'selected' : ''}>Черновики</option><option value="closed" ${v === 'closed' ? 'selected' : ''}>Завершённые</option>`; }
    function renderRegistry() {
        const f = settings.registry;
        const heading = `<section class="vq-head"><div><div class="vq-eyebrow">Рабочее пространство</div><h1>Заявки на спецодежду</h1><div class="vq-sub">Реестр заявок и контроль повторного включения спецодежды</div><p class="vq-caption">Справочник 1С · ${esc(ctx.importSummary())}</p></div>${can('add_request') ? btn(icon('plus') + ' Новая заявка', 'create', 'primary') : ''}</section>`;
        const toolbar = `<div class="vq-toolbar vq-registry-tools">${field('Номер, ФИО или табельный', f.q, 'registry-search')}<select class="vq-select" data-filter="status" aria-label="Состояние">${toolbarOptionsStatus(f.status)}</select><select class="vq-select" data-filter="organization_id" aria-label="Организация">${allOrgOptions(f.organization_id, true)}</select><button type="button" class="vq-btn vq-filter-toggle" data-action="toggle-filters" aria-controls="vq-period-fields" aria-expanded="${settings.registryOpen}">Период и сортировка${f.date_basis !== 'created' || f.date_from || f.date_to || f.sort !== 'newest' ? ' · заданы' : ''}</button><span class="vq-countnote" id="registryCount">Загрузка…</span><div class="vq-advanced-fields" id="vq-period-fields" role="group" aria-label="Период и порядок заявок" ${settings.registryOpen ? '' : 'hidden'}><label class="vq-date-label">Дата <select class="vq-select" data-filter="date_basis" aria-label="Тип даты"><option value="created" ${f.date_basis === 'created' ? 'selected' : ''}>Создания</option><option value="completed" ${f.date_basis === 'completed' ? 'selected' : ''}>Завершения</option></select></label><label class="vq-date-label">С <input type="date" data-filter="date_from" aria-label="Начало периода" value="${esc(f.date_from)}"></label><label class="vq-date-label">По <input type="date" data-filter="date_to" aria-label="Конец периода" value="${esc(f.date_to)}"></label><label>Порядок <select class="vq-select" data-filter="sort" aria-label="Порядок заявок"><option value="newest" ${f.sort === 'newest' ? 'selected' : ''}>Сначала новые</option><option value="oldest" ${f.sort === 'oldest' ? 'selected' : ''}>Сначала старые</option></select></label><button type="button" class="vq-advanced-reset" data-action="reset-period">Сбросить период и порядок</button><p class="vq-filter-error" id="vq-period-error" role="status"></p></div></div>`;
        main.innerHTML = heading + `<section class="vq-card">${toolbar}<div id="registryRegion" class="vq-tablewrap"><div class="vq-empty"><div class="symbol">⌕</div><h3>Загрузка заявок…</h3></div></div><div class="footer"><span id="registryFooter"></span></div></section>`;
        main.dataset.screen = 'registry';
    }
    async function openRegistry({ route = true } = {}) {
        settings.assignmentAbort?.abort();
        settings.repeatAbort?.abort();
        settings.bulkAbort?.abort();
        settings.singleAbort?.abort();
        settings.batchAbort?.abort();
        ++settings.refreshEpoch;
        ++settings.historyEpoch;
        ++settings.dialogEpoch;
        ++settings.bulkEpoch;
        settings.history.clear();
        settings.pendingAssignments.clear();
        settings.eventLog?.abort.abort(); settings.eventLog = null;
        settings.error = null;
        settings.screen = 'registry';
        settings.request = null;
        settings.base = null;
        settings.drawer = null;
        settings.modal = null;
        settings.selected.clear();
        settings.warnings.clear();
        settings.repeatEpoch++;
        settings.repeatState = 'idle';
        settings.archive = null;
        if (route)
            syncUrl(null);
        renderRegistry();
        renderOverlay();
        await loadRegistry();
    }
    async function loadRegistry() {
        const id = ++settings.pageEpoch;
        const query = new URLSearchParams();
        for (const [name, v] of Object.entries(settings.registry))
            if (v !== '' && v !== null)
                query.set(name, String(v));
        const body = $('#registryRegion');
        if (!body)
            return;
        try {
            const result = await api('ui/requests/?' + query.toString());
            if (id !== settings.pageEpoch || settings.screen !== 'registry')
                return;
            settings.registryData = result;
            $('#registryCount').textContent = `${result.count} из ${result.count}`;
            body.innerHTML = result.results.length ? `<table class="vq-registry-table"><colgroup><col style="width:100px"><col><col style="width:156px"><col style="width:156px"><col style="width:112px"><col style="width:150px"><col style="width:36px"></colgroup><thead><tr><th>Заявка</th><th>Организация</th><th>Создана</th><th>Завершена</th><th>Сотрудники</th><th>Состояние</th><th></th></tr></thead><tbody>${result.results.map(r => `<tr data-open="${r.id}" class="registry-row"><td class="registry-number"><button type="button" class="vq-name-action" data-open="${r.id}" aria-label="Открыть заявку ${r.id}">№ ${r.id}</button></td><td>${esc(r.snapshot_organization_name)}</td><td>${fmt(r.created_at)}<div class="secondary">${fmtTime(r.created_at)}</div></td><td>${r.completed_at ? `${fmt(r.completed_at)}<div class="secondary">${fmtTime(r.completed_at)}</div>` : '<span class="quiet">—</span>'}</td><td>${r.members_count}</td><td><span class="status-inline ${r.status === 'closed' ? 'finished' : ''}">${tone(r.status)}</span></td><td class="row-open">›</td></tr>`).join('')}</tbody></table>` : `<div class="vq-empty"><div class="symbol">⌕</div><h3>Совпадений нет</h3><p>Измените запрос или сбросьте фильтры, чтобы вернуться к полному списку.</p>${btn('Сбросить фильтры', 'reset-filters')}</div>`;
            const begin = result.count ? result.offset + 1 : 0, end = result.offset + result.results.length;
            $('#registryFooter').innerHTML = result.count <= result.limit ? 'Показаны все заявки' : `Показано ${begin}–${end} из ${result.count} · <span class="vq-inline-pagination">${btn('← Назад', 'registry-prev', 'small', result.offset === 0 ? 'disabled' : '')} ${btn('Далее →', 'registry-next', 'small', end >= result.count ? 'disabled' : '')}</span>`;
            main.classList.toggle('vq-short-content', result.results.length <= 7);
        }
        catch (err) {
            if (id !== settings.pageEpoch)
                return;
            body.innerHTML = `<div class="vq-banner error"><span class="ico">!</span><div class="copy"><b>Не удалось загрузить заявки</b><p>${esc(err.message)}</p></div></div>`;
            const message = $('#vq-period-error');
            if (message)
                message.textContent = err.message;
        }
    }
    Object.assign(ctx, {
        routeUrl, syncUrl, finishRoute, toolbarOptionsStatus, renderRegistry, openRegistry, loadRegistry
    });
}
