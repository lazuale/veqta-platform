/* Редактор Workwear: локальный состав, назначения и отправка полного черновика.
 * installEditor(ctx) публикует render/open/save/scan/chooseAssignment; зависимости
 * приходят через ctx, состояние принадлежит settings из core. repeatEpoch
 * отбрасывает старый общий scan. pendingAssignments хранит последнее намерение
 * на пару; save ждёт его завершения. Подтверждение COMMIT отделено от GET
 * обновления: ошибка GET не превращает успешную запись в несохранённую.
 * patchOneEmployee меняет одну строку; общий scan обновляет только предупреждения.
 * renderEditor сохраняет прокрутку/фокус только при той же заявке. */
export function installEditor(ctx) {
    const { settings, main, overlays } = ctx;
    const api = (...args) => ctx.api(...args);
    const copy = (...args) => ctx.copy(...args);
    const syncUrl = (...args) => ctx.syncUrl(...args);
    const renderOverlay = (...args) => ctx.renderOverlay(...args);
    const fail = (...args) => ctx.fail(...args);
    const currentMembers = (...args) => ctx.currentMembers(...args);
    const notice = (...args) => ctx.notice(...args);
    const norm = (...args) => ctx.norm(...args);
    const byItem = (...args) => ctx.byItem(...args);
    const esc = (...args) => ctx.esc(...args);
    const itemNames = (...args) => ctx.itemNames(...args);
    const icon = (...args) => ctx.icon(...args);
    const plural = (...args) => ctx.plural(...args);
    const editable = (...args) => ctx.editable(...args);
    const btn = (...args) => ctx.btn(...args);
    const $ = (...args) => ctx.$(...args);
    const $$ = (...args) => ctx.$$(...args);
    const isDraft = (...args) => ctx.isDraft(...args);
    const tone = (...args) => ctx.tone(...args);
    const fmt = (...args) => ctx.fmt(...args);
    const fmtTime = (...args) => ctx.fmtTime(...args);
    const can = (...args) => ctx.can(...args);
    const dirty = (...args) => ctx.dirty(...args);
    const assignedCount = (...args) => ctx.assignedCount(...args);
    const field = (...args) => ctx.field(...args);
    const emptyCount = (...args) => ctx.emptyCount(...args);
    const normalizedDraft = (...args) => ctx.normalizedDraft(...args);
    function adoptRequest(request, { resetView = true } = {}) {
        if (!request || !Array.isArray(request.members) || !Number.isInteger(request.version))
            throw new Error('Некорректный ответ при загрузке заявки');
        settings.repeatAbort?.abort(); settings.singleAbort?.abort();
        settings.assignmentAbort?.abort(); settings.assignmentAbort = new AbortController();
        settings.bulkAbort?.abort(); settings.batchAbort?.abort();
        // После Save сервер возвращает строки по PK включения. Отображаемая
        // локальная последовательность сохраняется, чтобы сотрудник не переехал
        // на другую страницу только из-за первого получения server IDs.
        if (!resetView && settings.request?.id === request.id) {
            const rank = new Map(settings.request.members.map((m, i) => [m.employee_id, i]));
            request = { ...request, members: [...request.members].sort((a, b) =>
                (rank.get(a.employee_id) ?? Number.MAX_SAFE_INTEGER) - (rank.get(b.employee_id) ?? Number.MAX_SAFE_INTEGER)) };
        }
        settings.request = request;
        settings.base = copy(request);
        settings.screen = 'editor';
        settings.modal = null;
        settings.drawer = null;
        settings.archive = null;
        settings.error = null;
        ++settings.historyEpoch;
        ++settings.dialogEpoch;
        ++settings.bulkEpoch;
        settings.history.clear();
        settings.pendingAssignments.clear();
        settings.searchEpoch++;
        settings.singleQuery = '';
        settings.singleResults = [];
        settings.singleTotal = 0;
        settings.singleHasMore = false;
        settings.singleLoading = false;
        settings.picked.clear();
        settings.pickedEmployees.clear();
        if (resetView) {
            settings.memberQuery = '';
            settings.memberPageIndex = 0;
            settings.columnPageIndex = 0;
            settings.memberFilter = 'all';
            settings.tableMode = 'list';
            settings.matrixFocus = null;
            settings.selected.clear();
        } else {
            const present = new Set(request.members.map(m => m.employee_id));
            for (const id of settings.selected) if (!present.has(id)) settings.selected.delete(id);
        }
        settings.warnings.clear();
        settings.repeatProgress = null;
        settings.repeatEpoch++;
        settings.repeatState = can('view_employee') ? 'pending' : 'unavailable';
        renderEditor();
        renderOverlay();
        if (can('view_employee')) void scanRepeats();
    }
    async function openRequest(id, { route = true, resetView = true } = {}) {
        const epoch = ++settings.refreshEpoch;
        try {
            const data = await api(`requests/${id}/`);
            if (epoch !== settings.refreshEpoch)
                return;
            adoptRequest(data.request, { resetView });
            if (route)
                syncUrl(id);
            return true;
        }
        catch (err) {
            if (epoch !== settings.refreshEpoch) return false;
            fail(err);
            return false;
        }
    }
    async function scanRepeats() {
        if (!settings.request)
            return;
        if (!can('view_employee')) {
            ++settings.repeatEpoch;
            settings.warnings.clear();
            settings.repeatState = 'unavailable';
            refreshRepeatView();
            return;
        }
        settings.repeatAbort?.abort();
        const controller = new AbortController(); settings.repeatAbort = controller;
        const id = settings.request.id, epoch = ++settings.repeatEpoch;
        const pairs = [];
        for (const m of currentMembers())
            for (const a of m.assignments)
                pairs.push({ employee_id: m.employee_id, item_id: a.item_id });
        const nextState = pairs.length ? 'pending' : 'verified';
        const resetView = settings.repeatState !== nextState || settings.warnings.size > 0;
        settings.repeatState = nextState;
        settings.warnings.clear();
        if (resetView) refreshRepeatView();
        if (!pairs.length)
            return;
        try {
            const result = await ctx.repeatPairs(pairs, { signal: controller.signal,
                valid: () => settings.request?.id === id && settings.repeatEpoch === epoch,
                progress: value => { settings.repeatProgress = value; updateEditorSummary(); } });
            if (settings.request?.id !== id || settings.repeatEpoch !== epoch)
                return;
            for (const w of result.warnings)
                settings.warnings.set(`${w.employee_id}:${w.item_id}`, w);
            settings.repeatState = 'verified';
            refreshRepeatView();
        }
        catch (err) {
            if (settings.request?.id !== id || settings.repeatEpoch !== epoch)
                return;
            settings.repeatState = 'failed';
            refreshRepeatView();
            notice('Не удалось проверить историю повторов: ' + err.message, true);
        }
    }
    const warning = (employeeId, itemId) => settings.warnings.get(`${employeeId}:${itemId}`);
    /** Обновляет результат общего scan, сохраняя строки, фокус и прокрутку.
     * Только фильтр «С повторами» меняет состав видимых строк и требует rebuild.
     * Ответ уже проверен по request id и repeatEpoch; бизнес-решений здесь нет.
     */
    function refreshRepeatView() {
        if (settings.screen !== 'editor' || !settings.request) return;
        if (settings.memberFilter === 'warn') {
            refreshMemberRows();
            return;
        }
        const members = new Map(currentMembers().map(m => [m.employee_id, m]));
        const items = new Map(byItem().map(it => [it.id, it]));
        const content = (el, html) => { if (el && el.innerHTML !== html) el.innerHTML = html; };
        for (const row of $$('#editorTable [data-row]')) {
            const member = members.get(Number(row.dataset.row));
            if (!member) continue;
            if (settings.tableMode === 'matrix') {
                for (const button of row.querySelectorAll('[data-cell].on')) {
                    const itemId = Number(button.dataset.cell.split(':')[1]), item = items.get(itemId);
                    if (!item) continue;
                    const repeated = !!warning(member.employee_id, itemId);
                    button.classList.toggle('warn', repeated);
                    button.setAttribute('aria-label', `${item.name}: назначено — ${member.full_name}${repeated ? ' — повтор' : ''}`);
                    content(button, `${['pending', 'failed'].includes(settings.repeatState) ? '<span aria-hidden="true">…</span>' : '<span class="vq-cell-check" aria-hidden="true"></span>'}${repeated ? '<sup aria-hidden="true">!</sup>' : ''}`);
                }
            } else {
                content(row.querySelector('.vq-assignment-tags'), rowTags(member, items));
                content(row.querySelector('.vq-repeat-status'), rowWarning(member));
            }
        }
        updateEditorSummary();
    }
    const warningsCount = () => currentMembers().reduce((n, m) => n + m.assignments.filter(a => warning(m.employee_id, a.item_id)).length, 0);
    // Нормализация не зависит от фильтра, страницы, назначений и выбора.
    // WeakMap не удерживает участников уже закрытой вкладки. Сверка исходного
    // текста сохраняет корректность и при обновлении кадровых подписей.
    const searchText = new WeakMap();
    function memberVisible() {
        const q = norm(settings.memberQuery);
        return currentMembers().filter(m => {
            if (q) {
                const source = m.full_name + ' ' + m.personnel_number;
                let cached = searchText.get(m);
                if (!cached || cached.source !== source) {
                    cached = { source, value: norm(source) };
                    searchText.set(m, cached);
                }
                if (!cached.value.includes(q)) return false;
            }
            if (settings.memberFilter === 'selected')
                return settings.selected.has(m.employee_id);
            if (settings.memberFilter === 'empty')
                return !m.assignments.length;
            if (settings.memberFilter === 'warn')
                return m.assignments.some(a => warning(m.employee_id, a.item_id));
            return true;
        });
    }
    function rowTags(m, items = new Map(byItem().map(it => [it.id, it]))) {
        return m.assignments.length ? m.assignments.map(a => { const item = a.name ? a : items.get(a.item_id); return `<span class="vitem ${warning(m.employee_id, a.item_id) ? 'warn' : ''}" title="${esc(item?.name || '')}">${esc(item?.name || 'Позиция')}</span>`; }).join('') : '<span class="quiet">Не назначены</span>';
    }
    function rowWarning(m) {
        const ws = m.assignments.filter(a => warning(m.employee_id, a.item_id));
        if (!m.assignments.length)
            return '<span class="quiet">—</span>';
        if (settings.repeatState === 'pending')
            return '<span class="quiet" role="status">Проверка повторов…</span>';
        if (settings.repeatState === 'unavailable')
            return '<span class="quiet">Недоступно для этой роли</span>';
        if (settings.repeatState === 'failed')
            return `<button type="button" class="vq-name-action" data-action="retry-repeat">Проверка недоступна · Повторить</button>`;
        if (!ws.length)
            return '<span class="vq-no-repeat">Без повторов</span>';
        const names = itemNames(ws.map(x => x.item_id));
        return `<div class="warning-names"><button type="button" class="vq-repeat-trigger" data-person="${m.employee_id}">${icon('alert')} ${ws.length} ${plural(ws.length, ['повтор', 'повтора', 'повторов'])} ${icon('chev')}</button><span class="vq-repeat-items">${esc(names.slice(0, 3).join(' · '))}</span></div>`;
    }
    function memberList(rows, locked) {
        const items = new Map(byItem().map(it => [it.id, it]));
        return `<div class="vq-tablewrap"><table><thead><tr><th class="select-col">${locked ? '' : `<input type="checkbox" data-check="all" aria-label="Выбрать сотрудников показанной страницы" ${rows.length && rows.every(m => settings.selected.has(m.employee_id)) ? 'checked' : ''}>`}</th><th>Сотрудник</th><th class="dept-col">Подразделение</th><th>Назначено</th><th>Контроль</th><th></th></tr></thead><tbody>${rows.map(m => `<tr data-row="${m.employee_id}" class="${settings.selected.has(m.employee_id) ? 'selected' : ''}"><td class="select-col">${locked ? '' : `<input type="checkbox" data-check="${m.employee_id}" aria-label="Выбрать ${esc(m.full_name)}" ${settings.selected.has(m.employee_id) ? 'checked' : ''}>`}</td><td><button type="button" class="vq-name-action" data-person="${m.employee_id}">${esc(m.full_name)}</button><div class="secondary">${esc(m.personnel_number)}<span class="small-dept"> · ${esc(m.department_name || '')}</span></div></td><td class="dept-col">${esc(m.department_name || '—')}</td><td><div class="vq-assignment-tags">${rowTags(m, items)}</div></td><td class="vq-repeat-status">${rowWarning(m)}</td><td class="quiet">›</td></tr>`).join('')}</tbody></table></div>`;
    }
    function groups() {
        const map = new Map();
        for (const it of byItem()) {
            const key = it.category || 'Прочее';
            if (!map.has(key))
                map.set(key, []);
            map.get(key).push(it);
        }
        return [...map.entries()];
    }
    function cellAvailable(member, item) {
        return editable() &&
            (item.is_active || member.assignments.some(a => a.item_id === item.id)) &&
            !settings.pendingAssignments.has(ctx.assignmentKey(member.employee_id, item.id));
    }
    function cell(m, it, locked) {
        const assigned = m.assignments.some(a => a.item_id === it.id), w = warning(m.employee_id, it.id);
        const tab = settings.matrixFocus?.employeeId === m.employee_id && settings.matrixFocus?.itemId === it.id ? 0 : -1;
        return `<td><button type="button" class="gridpick ${assigned ? 'on' : ''} ${assigned && w ? 'warn' : ''}" data-cell="${m.employee_id}:${it.id}" tabindex="${tab}" aria-pressed="${assigned}" aria-label="${esc(it.name)}: ${assigned ? 'назначено' : 'не назначено'} — ${esc(m.full_name)}${w ? ' — повтор' : ''}" ${locked || !cellAvailable(m, it) ? 'disabled aria-disabled="true"' : ''}>${assigned ? (settings.repeatState === 'pending' || settings.repeatState === 'failed' ? '<span aria-hidden="true">…</span>' : '<span class="vq-cell-check" aria-hidden="true"></span>') : ''}${assigned && w ? '<sup aria-hidden="true">!</sup>' : ''}${!assigned ? '<span aria-hidden="true">—</span>' : ''}</button></td>`;
    }
    function matrix(rows, locked) {
        const visibleItems = ctx.matrixItems(), gg = [];
        // Каталог может чередовать категории. Каждый заголовок покрывает только
        // соседние колонки, сохраняя заданный администратором порядок позиций.
        for (const item of visibleItems) {
            const category = item.category || 'Прочее', last = gg.at(-1);
            if (last?.[0] === category) last[1].push(item);
            else gg.push([category, [item]]);
        }
        return `<div class="vq-matrix-legend">✓ Назначено · ✓! Назначено с повтором · — Не назначено · … Проверяется история.</div><div class="vq-tablewrap" tabindex="0" role="region" aria-label="Матрица назначений. Горизонтальная прокрутка для всех столбцов."><table class="matrix"><thead><tr><th class="sticky" rowspan="2" style="text-align:left"><span style="display:inline-flex;align-items:center;gap:10px">${locked ? '' : `<input type="checkbox" data-check="all" aria-label="Выбрать сотрудников показанной страницы" ${rows.length && rows.every(m => settings.selected.has(m.employee_id)) ? 'checked' : ''}>`} Сотрудник</span></th>${gg.map(([name, items]) => `<th class="matrix-group" scope="colgroup" colspan="${items.length}">${esc(name)}</th>`).join('')}<th rowspan="2" class="sumcol">Всего</th></tr><tr>${visibleItems.map(it => `<th class="matrix-leaf" scope="col" title="${esc(it.name)}">${esc(it.name)}<small>${it.validity_days} дн.</small></th>`).join('')}</tr></thead><tbody>${rows.map(m => `<tr data-row="${m.employee_id}" class="${settings.selected.has(m.employee_id) ? 'selected' : ''}"><td class="sticky"><span style="display:flex;align-items:center;gap:9px">${locked ? '' : `<input type="checkbox" data-check="${m.employee_id}" aria-label="Выбрать ${esc(m.full_name)}" ${settings.selected.has(m.employee_id) ? 'checked' : ''}>`}<span><button type="button" class="vq-name-action" data-person="${m.employee_id}">${esc(m.full_name)}</button><span class="secondary" style="display:block">${esc(m.personnel_number)}</span></span></span></td>${visibleItems.map(it => cell(m, it, locked)).join('')}<td class="sumcol">${m.assignments.length}</td></tr>`).join('')}</tbody></table></div>`;
    }
    function selectionBar() {
        if (!settings.selected.size || !editable())
            return '';
        const hidden = ctx.selectedOutsidePage();
        return `<div class="vq-selection"><div><strong>Выбрано ${settings.selected.size} из ${currentMembers().length}</strong><div class="minor">${hidden ? `За пределами показанной страницы: ${hidden}. Действие затронет всех выбранных.` : 'Массовые операции применяются ко всем выбранным.'}</div></div><div class="vq-actions">${btn('Показать выбранных', 'show-selected', 'small subtle')}${btn('Назначить', 'batch-add', 'small accent')}${btn('Убрать позиции', 'batch-remove', 'small')}${btn('Исключить', 'remove-selected', 'small danger')}${btn('Снять выбор', 'clear-selection', 'small ghost')}</div></div>`;
    }
    function patchSelection() {
        if (settings.screen !== 'editor')
            return;
        if (settings.memberFilter === 'selected') {
            refreshMemberRows();
            return;
        }
        const region = $('#editorSelection');
        if (region)
            region.innerHTML = selectionBar();
        const rows = ctx.memberPage().rows, all = $('[data-check="all"]');
        if (all) {
            all.checked = rows.length > 0 && rows.every(m => settings.selected.has(m.employee_id));
            all.indeterminate = rows.some(m => settings.selected.has(m.employee_id)) && !all.checked;
        }
        $$('#editorTable [data-row]').forEach(row => {
            const id = Number(row.dataset.row);
            row.classList.toggle('selected', settings.selected.has(id));
            const check = row.querySelector('[data-check]');
            if (check)
                check.checked = settings.selected.has(id);
        });
        const footer = $('#editorFooter');
        if (footer)
            footer.textContent = `${ctx.pageFooter()}${settings.selected.size ? ' · Выбрано ' + settings.selected.size : ''}`;
    }
    function renderMemberBody(rows, locked) {
        if (settings.tableMode === 'matrix') {
            const items = ctx.matrixItems();
            const focusedMember = rows.find(m => m.employee_id === settings.matrixFocus?.employeeId);
            const focusedItem = items.find(item => item.id === settings.matrixFocus?.itemId);
            if (!focusedMember || !focusedItem || !cellAvailable(focusedMember, focusedItem)) {
                settings.matrixFocus = null;
                for (const member of rows) {
                    const item = items.find(item => cellAvailable(member, item));
                    if (item) {
                        settings.matrixFocus = { employeeId: member.employee_id, itemId: item.id };
                        break;
                    }
                }
            }
        }
        if (!currentMembers().length)
            return locked
                ? `<div class="vq-empty"><div class="symbol">${icon('people')}</div><h3>В заявке нет сотрудников</h3><p>Заявка доступна только для просмотра.</p></div>`
                : `<div class="vq-empty"><div class="symbol">${icon('people')}</div><h3>Добавьте первых сотрудников</h3><p>Поиск по ФИО или табельному номеру. Можно вставить список сразу из любого источника — по одной записи на строку.</p>${btn(icon('plus') + ' Добавить сотрудников', 'add', 'primary')}</div>`;
        if (!rows.length && settings.memberFilter === 'warn' && settings.repeatState !== 'verified')
            return `<div class="vq-empty" role="status"><h3>${settings.repeatState === 'pending' ? 'Проверяем повторы' : 'Проверка повторов не завершена'}</h3><p>Результат фильтра будет полным после проверки всей заявки.</p>${btn('Повторить проверку', 'retry-repeat')}</div>`;
        return rows.length
            ? (settings.tableMode === 'matrix' ? matrix(rows, locked) : memberList(rows, locked))
            : `<div class="vq-empty"><div class="symbol">⌕</div><h3>Ничего не найдено</h3><p>Измените поисковый запрос или сбросьте фильтр.</p>${btn('Сбросить поиск', 'reset-member-search')}</div>`;
    }
    function renderEditor() {
        const r = settings.request, finished = !isDraft(), locked = !editable(), rows = ctx.memberPage().rows;
        // Сохранение и ошибка сети обновляют ту же заявку: контекст таблицы остаётся.
        // При переходе к другой заявке прежняя прокрутка не переносится.
        const previous = main.dataset.screen === 'editor' && main.dataset.requestId === String(r.id)
            ? $('#editorTable .vq-tablewrap') : null;
        const position = previous && { top: previous.scrollTop, left: previous.scrollLeft };
        const focus = previous && main.contains(document.activeElement) ? ctx.controlSelector(document.activeElement) : null;
        const title = `<section class="vq-head"><div><div class="vq-crumb"><button type="button" data-action="back">Заявки</button><span>›</span><span>№ ${r.id}</span></div><div class="vq-titleline"><h1>Состав заявки</h1><span class="vq-badge ${finished ? 'finished' : ''}">${tone(r.status)}</span></div><div class="vq-sub">№ ${r.id} <span style="padding:0 6px">·</span> ${esc(r.snapshot_organization_name)} <span style="padding:0 6px">·</span> Создана ${fmt(r.created_at)} ${fmtTime(r.created_at)}${finished ? ' · Завершена ' + fmt(r.completed_at) + ' ' + fmtTime(r.completed_at) : ''}</div></div><div class="vq-actions">${finished ? (can('reopen_request') ? btn('Вернуть в работу', 'reopen', 'primary') : '') : (editable() ? btn('Сохранить', 'save', '', !dirty() ? 'disabled' : '') + btn('Завершить заявку', 'finish', 'primary', !assignedCount() ? 'disabled' : '') : '')}</div></section>`;
        const digest = `<div class="vq-digest" id="editorDigest">${renderDigest()}</div>`;
        const toolbar = `<div class="vq-toolbar">${field('ФИО или табельный номер', settings.memberQuery, 'member-search')}<select class="vq-select" data-filter="memberFilter" aria-label="Фильтр сотрудников"><option value="all">Все сотрудники</option>${locked ? '' : `<option value="selected" ${settings.memberFilter === 'selected' ? 'selected' : ''}>Выбранные</option>`}<option value="warn" ${settings.memberFilter === 'warn' ? 'selected' : ''}>С повторами</option><option value="empty" ${settings.memberFilter === 'empty' ? 'selected' : ''}>Без позиций</option></select>${locked ? '' : btn(icon('plus') + ' Добавить', 'add', '')}<div class="vq-grow"></div><div class="vq-toggle" role="group" aria-label="Вид таблицы"><button type="button" data-mode="list" class="${settings.tableMode === 'list' ? 'active' : ''}" aria-pressed="${settings.tableMode === 'list'}">Список</button>${byItem().length ? `<button type="button" data-mode="matrix" class="${settings.tableMode === 'matrix' ? 'active' : ''}" aria-pressed="${settings.tableMode === 'matrix'}">Матрица</button>` : ''}</div></div>`;
        const inner = renderMemberBody(rows, locked);
        const footer = `<div class="footer"><span id="editorFooter">${ctx.pageFooter()}${settings.selected.size ? ' · Выбрано ' + settings.selected.size : ''}</span><span>${finished ? 'Завершённая заявка доступна только для просмотра' : locked ? 'Черновик доступен только для просмотра' : can('delete_request') ? btn('Удалить черновик', 'delete-draft', 'small ghost danger') : ''}</span></div>`;
        const errorBanner = settings.error === 'save' ? `<div class="vq-banner error"><span class="ico">!</span><div class="copy"><b>Изменения не сохранены</b><p>Сервер не подтвердил сохранение. Изменения остались в редакторе.</p></div>${btn('Повторить', 'retry-save', 'small')}</div>` : '';
        const refreshBanner = settings.error === 'refresh' ? `<div class="vq-banner"><div class="copy"><b>Сохранение подтверждено сервером</b><p>Не удалось обновить сведения. Новые локальные правки сохранены в редакторе.</p></div>${btn('Обновить сведения', 'retry-refresh', 'small')}</div>` : '';
        const catalogBanner = editable() && can('view_workwearitem') && !settings.meta.items.some(item => item.is_active)
            ? '<div class="vq-banner" role="status"><div class="copy"><b>Нет активных позиций спецодежды</b><p>Новые назначения недоступны. Обратитесь к администратору для заполнения каталога.</p></div></div>' : '';
        main.innerHTML = title + digest + catalogBanner + errorBanner + refreshBanner + `<section class="vq-card">${toolbar}<div id="editorSelection">${selectionBar()}</div><div id="editorPager" class="vq-editor-pages">${ctx.pagerMarkup()}</div><div id="editorTable">${inner}</div>${footer}</section>`;
        main.dataset.screen = 'editor';
        main.dataset.requestId = String(r.id);
        main.classList.toggle('vq-short-content', rows.length <= 5);
        updateEditorSummary();
        if (settings.busy) ctx.setBusy(true);
        const wrap = $('#editorTable .vq-tablewrap');
        if (position && wrap && (position.top || position.left)) { wrap.scrollTop = position.top; wrap.scrollLeft = position.left; }
        const restore = focus && $(focus);
        if (restore && !restore.disabled) restore.focus({ preventScroll: true });
    }
    function renderDigest() {
        const r = settings.request;
        const changed = dirty(), count = assignedCount();
        return `<span data-stat="n"><b>${r.members.length}</b> ${plural(r.members.length, ['сотрудник', 'сотрудника', 'сотрудников'])}</span><span class="sep"></span><span><b>${count}</b> ${plural(count, ['назначение', 'назначения', 'назначений'])}</span><span class="sep"></span><span class="warn"><b>${settings.repeatState === 'verified' ? warningsCount() : '—'}</b> ${settings.repeatState === 'pending' ? `Проверка повторов…${settings.repeatProgress ? ` ${settings.repeatProgress.done} из ${settings.repeatProgress.total}` : ''}` : settings.repeatState === 'unavailable' ? 'Контроль повторов недоступен' : settings.repeatState === 'failed' ? 'Проверка недоступна' : plural(warningsCount(), ['повторное назначение', 'повторных назначения', 'повторных назначений'])}</span><span class="sep"></span><span><b>${emptyCount()}</b> без позиций</span><span class="save-state ${settings.error ? 'bad' : changed ? 'unsaved' : ''}">${!editable() ? 'Только просмотр' : settings.error === 'conflict' ? 'Конфликт версий' : settings.error === 'refresh' && !changed ? 'Сохранено · требуется обновление' : changed ? 'Есть несохранённые изменения' : 'Сохранено'}</span>`;
    }
    function refreshMemberRows({ resetScroll = false, viewOnly = false } = {}) {
        if (settings.screen !== 'editor')
            return;
        const wrap = $('#editorTable .vq-tablewrap');
        const pos = wrap ? { top: wrap.scrollTop, left: wrap.scrollLeft } : null;
        const previous = ctx.controlSelector(document.activeElement);
        const rows = ctx.memberPage().rows;
        // Фильтр часто оставляет ту же страницу (например, все строки имеют
        // повторы). В этом случае существующие строки уже точны и сохраняют
        // фокус, доступные имена и геометрию без повторного разбора HTML.
        const shown = viewOnly ? $$('#editorTable [data-row]') : [];
        if (!viewOnly || shown.length !== rows.length ||
                shown.some((row, index) => Number(row.dataset.row) !== rows[index].employee_id) ||
                !rows.length) {
            $('#editorTable').innerHTML = renderMemberBody(rows, !editable());
        }
        const next = $('#editorTable .vq-tablewrap');
        if (pos && next) {
            next.scrollTop = resetScroll ? 0 : pos.top;
            next.scrollLeft = pos.left;
        }
        const restored = previous && $(previous);
        if (restored && !restored.disabled)
            restored.focus({ preventScroll: true });
        updateEditorSummary({ viewOnly });
        if (settings.saving) ctx.setBusy(true);
    }
    function updateEditorSummary({ viewOnly = false } = {}) {
        if (settings.screen !== 'editor' || !settings.request) return;
        const rows = ctx.memberPage().rows;
        const digest = $('#editorDigest'), footer = $('#editorFooter'), selection = $('#editorSelection');
        if (digest && !viewOnly) {
            const html = renderDigest();
            if (digest.innerHTML !== html) digest.innerHTML = html;
        }
        if (footer) footer.textContent = `${ctx.pageFooter()}${settings.selected.size ? ' · Выбрано ' + settings.selected.size : ''}`;
        if (selection) selection.innerHTML = selectionBar();
        const all = $('[data-check="all"]');
        if (all) {
            all.checked = rows.length > 0 && rows.every(m => settings.selected.has(m.employee_id));
            all.indeterminate = rows.some(m => settings.selected.has(m.employee_id)) && !all.checked;
        }
        ctx.updatePager();
        if (viewOnly) return;
        const pending = settings.pendingAssignments.size > 0;
        const saveButton = $('[data-action="save"]'), finishButton = $('[data-action="finish"]');
        if (saveButton) saveButton.disabled = pending || !dirty();
        if (finishButton) finishButton.disabled = pending || !assignedCount();
    }
    function patchOneEmployee(employeeId) {
        if (settings.screen !== 'editor' || !settings.request) return;
        const member = currentMembers().find(m => m.employee_id === employeeId);
        const previous = $(`[data-row="${employeeId}"]`, main);
        const rows = ctx.memberPage().rows, visible = rows.some(m => m.employee_id === employeeId);
        const focus = ctx.controlSelector(document.activeElement);
        const actual = $$('#editorTable [data-row]').map(row => Number(row.dataset.row));
        if (currentMembers().length > ctx.pageRowSize && actual.join(',') !== rows.map(m => m.employee_id).join(',')) {
            refreshMemberRows(); return;
        }
        if (!visible) {
            previous?.remove();
            if (!rows.length) $('#editorTable').innerHTML = renderMemberBody(rows, !editable());
        } else if (member) {
            const wrapper = document.createElement('div');
            wrapper.innerHTML = settings.tableMode === 'matrix' ? matrix([member], !editable()) : memberList([member], !editable());
            const row = wrapper.querySelector(`[data-row="${employeeId}"]`);
            if (previous && row) previous.replaceWith(row);
            else {
                const body = $('#editorTable tbody');
                if (!body) $('#editorTable').innerHTML = renderMemberBody(rows, !editable());
                else if (row) {
                    const index = rows.findIndex(m => m.employee_id === employeeId);
                    const next = rows.slice(index + 1).map(m => $(`[data-row="${m.employee_id}"]`, body)).find(Boolean);
                    body.insertBefore(row, next || null);
                }
            }
        }
        updateEditorSummary();
        if (settings.tableMode === 'matrix' && !$('#editorTable [data-cell][tabindex="0"]:not(:disabled)')) {
            // Снятая неактивная позиция становится недоступной. Оставляем
            // точку входа с клавиатуры в той же строке, затем в соседних.
            const next = $(`[data-row="${employeeId}"] [data-cell]:not(:disabled)`) || $('#editorTable [data-cell]:not(:disabled)');
            $$('#editorTable [data-cell][tabindex="0"]').forEach(el => { el.tabIndex = -1; });
            settings.matrixFocus = null;
            if (next) {
                const [employeeId, itemId] = next.dataset.cell.split(':').map(Number);
                settings.matrixFocus = { employeeId, itemId };
                next.tabIndex = 0;
            }
        }
        const restore = focus && $(focus);
        if (restore && !restore.disabled) restore.focus({ preventScroll: true });
        else if (focus?.startsWith('[data-cell='))
            $('#editorTable [data-cell][tabindex="0"]:not(:disabled)')?.focus({ preventScroll: true });
    }
    function assignmentPending(employeeId, itemId, value) {
        const selectors = [`[data-cell="${employeeId}:${itemId}"]`];
        if (settings.drawer === employeeId) selectors.push(`[data-item="${itemId}"]`);
        for (const selector of selectors) {
            const control = $(selector);
            if (control) {
                const member = currentMembers().find(m => m.employee_id === employeeId);
                const item = byItem().find(item => item.id === itemId);
                control.disabled = value || !member || !item || !cellAvailable(member, item);
                control.setAttribute('aria-busy', String(value));
            }
        }
        updateEditorSummary();
    }
    async function chooseAssignment(employeeId, itemId, desired) {
        if (!editable() || settings.saving) return false;
        const request = settings.request, member = currentMembers().find(m => m.employee_id === employeeId);
        if (!member) return false;
        const assigned = member.assignments.some(a => a.item_id === itemId);
        const wanted = desired === undefined ? !assigned : !!desired;
        const key = ctx.assignmentKey(employeeId, itemId), current = settings.pendingAssignments.get(key);
        if (current) {
            current.desired = wanted;
            return current.promise;
        }
        if (assigned === wanted) return true;
        if (wanted && !settings.meta?.items.some(item => item.id === itemId && item.is_active)) return false;
        if (wanted && member.assignments.length >= ctx.limits().items_per_member) {
            notice('У сотрудника уже назначено максимальное число позиций.', true); return false;
        }
        if (!wanted) {
            member.assignments = member.assignments.filter(a => a.item_id !== itemId);
            settings.warnings.delete(`${employeeId}:${itemId}`);
            patchOneEmployee(employeeId);
            if (settings.drawer === employeeId) renderOverlay();
            return true;
        }
        const operation = { desired: wanted, promise: null };
        settings.pendingAssignments.set(key, operation);
        assignmentPending(employeeId, itemId, true);
        operation.promise = (async () => {
            try {
                const signal = (settings.assignmentAbort ??= new AbortController()).signal;
                const scan = await ctx.repeatPairs([{ employee_id: employeeId, item_id: itemId }], { signal, valid: () => settings.request === request && currentMembers().includes(member) && !settings.saving });
                if (settings.request !== request || !currentMembers().includes(member) || settings.saving) return false;
                if (operation.desired && !member.assignments.some(a => a.item_id === itemId)) {
                    // Другая ожидавшая проверка могла занять последнюю позицию.
                    // Решение проверяется вновь непосредственно перед локальным commit.
                    if (member.assignments.length >= ctx.limits().items_per_member) {
                        notice('У сотрудника уже назначено максимальное число позиций.', true); return false;
                    }
                    member.assignments.push({ id: null, item_id: itemId });
                    settings.warnings.delete(`${employeeId}:${itemId}`);
                    for (const warning of scan.warnings) settings.warnings.set(`${warning.employee_id}:${warning.item_id}`, warning);
                    if (scan.warnings.length) notice(`Повтор: ${member.full_name}. Назначение ещё не сохранено.`, true);
                }
                return true;
            } catch (err) {
                if (settings.request === request) notice('История не проверена; назначение не изменено: ' + err.message, true);
                return false;
            } finally {
                if (settings.pendingAssignments.get(key) === operation) settings.pendingAssignments.delete(key);
                if (settings.request === request) {
                    assignmentPending(employeeId, itemId, false);
                    patchOneEmployee(employeeId);
                    if (settings.drawer === employeeId) renderOverlay();
                }
            }
        })();
        return operation.promise;
    }
    /** Добавить проверенный набор локально целиком; запись выполняется только sync. */
    function addLocals(people) {
        if (!editable() || settings.saving) return 0;
        const existing = new Set(currentMembers().map(m => m.employee_id)), pending = [];
        for (const e of people) {
            if (existing.has(e.id)) continue;
            if (e.organization_id !== settings.request.organization_id || e.is_current === false)
                throw new Error('Сотрудник не соответствует организации или актуальной выгрузке');
            existing.add(e.id); pending.push(e);
        }
        if (existing.size > ctx.limits().request_members) throw new Error(`В заявке допускается до ${ctx.limits().request_members} сотрудников. Состав не изменён.`);
        const members = pending.map(e => ({ id: null, employee_id: e.id, full_name: e.full_name,
            personnel_number: e.personnel_number, snapshot_organization_name: e.organization_name, organization_id: e.organization_id,
            position_name: e.position_name, department_name: e.department_name, assignments: [] }));
        settings.request.members.push(...members);
        for (const e of pending) settings.selected.add(e.id);
        settings.memberQuery = ''; settings.memberFilter = 'all';
        return members.length;
    }
    async function save({ finish = false } = {}) {
        if (!editable() || settings.saving) return false;
        if (settings.pendingAssignments.size) {
            notice('Дождитесь проверки выбранных позиций.', true);
            return false;
        }
        const id = settings.request.id, epoch = settings.refreshEpoch;
        const submitted = copy(settings.request), sent = JSON.stringify(normalizedDraft(submitted));
        const inContext = () => settings.request?.id === id && settings.refreshEpoch === epoch;
        let acknowledged = false;
        settings.saving = true;
        ctx.setBusy(true);
        try {
            const result = await api(`ui/requests/${id}/sync/`, { method: 'POST', data: {
                expected_version: settings.base.version, finish, members: normalizedDraft(submitted),
            } });
            acknowledged = true;
            if (!inContext()) return false;
            const changedAfterSend = JSON.stringify(normalizedDraft(settings.request)) !== sent;
            const confirmed = copy(submitted);
            confirmed.version = result.version;
            if (finish) {
                confirmed.status = 'closed';
                confirmed.members = confirmed.members.filter(m => m.assignments.length);
                // Дату завершения подтверждает только последующее чтение сервера.
                confirmed.completed_at = null;
                confirmed.completed_by_id = null;
            }
            settings.base = confirmed;
            if (!changedAfterSend) settings.request = copy(confirmed);
            else {
                settings.request.version = result.version;
                if (finish) settings.request.status = 'closed';
            }
            settings.modal = null;
            settings.error = null;
            ++settings.historyEpoch;
            settings.history.clear();
            try {
                const fresh = await api(`requests/${id}/`);
                if (!inContext()) return false;
                if (changedAfterSend || JSON.stringify(normalizedDraft(settings.request)) !== JSON.stringify(normalizedDraft(confirmed))) {
                    settings.base = copy(fresh.request);
                    settings.request.version = fresh.request.version;
                    if (finish) {
                        settings.request.completed_at = fresh.request.completed_at;
                        settings.request.completed_by_id = fresh.request.completed_by_id;
                        settings.error = 'conflict';
                        settings.remote = fresh.request;
                        settings.modal = 'conflict';
                    }
                    renderEditor();
                    renderOverlay();
                    notice('Отправленная версия сохранена. Последующие локальные правки не перезаписаны.', true);
                } else {
                    adoptRequest(fresh.request, { resetView: false });
                    notice(finish ? `Заявка завершена. Исключено сотрудников: ${result.completion.excluded_members}` : 'Заявка сохранена');
                }
            } catch (err) {
                if (!inContext()) return false;
                settings.error = 'refresh';
                renderEditor();
                renderOverlay();
                notice('Сохранение подтверждено. Обновление сведений не удалось: ' + err.message, true);
            }
            return true;
        } catch (err) {
            if (!inContext()) return false;
            if (!acknowledged && err.status !== 409) {
                settings.error = 'save';
                renderEditor();
            }
            fail(err);
            return false;
        } finally {
            settings.saving = false;
            ctx.setBusy(false);
        }
    }
    async function create() {
        const org = $('#newOrg', overlays)?.value;
        if (!org)
            return;
        const data = await api('requests/', { method: 'POST', data: { organization_id: Number(org) } });
        settings.modal = null;
        await openRequest(data.request.id);
    }
    Object.assign(ctx, {
        openRequest, scanRepeats, warning, warningsCount, memberVisible, rowTags, rowWarning, memberList, groups, cell, cellAvailable, matrix, selectionBar, patchSelection, renderEditor, renderDigest, refreshMemberRows, patchOneEmployee, chooseAssignment, addLocals, save, create
    });
}
