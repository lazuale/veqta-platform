/* Делегированные события Workwear. installController(ctx) связывает действия
 * DOM с публичными функциями модулей; владелец данных — settings из core.
 * Один обработчик на контейнер позволяет заменять строку без перевешивания
 * listeners. Таймеры поиска и epochs защищают контекст; клавиатура матрицы
 * поддерживает один tabindex=0, стрелки и видимость активной ячейки.
 * Сохранение блокирует изменение черновика, beforeunload предупреждает о dirty. */
import { cycleFocus } from '../../portal/ui.js';

export function installController(ctx) {
    const {
        settings, main, root, overlays, api, openRequest, openRegistry, esc, openDrawer,
        renderOverlay, renderEditor, chooseAssignment, $, loadRegistry, renderRegistry,
        showOverlay, create, dirty, save, notice, resolveBulk, addBulk, scanBatchBeforeAdd,
        batchChange, newSelection, scanRepeats, searchEmployees, patchSelection, copy, finishRoute,
        fail, refreshMemberRows, memberVisible, patchSearchResults, $$, syncUrl, editable, can,
        isDraft, setBusy,
    } = ctx;
    const view = root.ownerDocument.defaultView;
    let memberFrame = null;
    // Локальные фильтр и поиск используют последний ввод к следующему кадру.
    // Нет сетевого запроса, поэтому отдельная пауза debounce здесь не нужна.
    // Несколько изменений до отрисовки объединяются в одно обновление таблицы.
    function scheduleMemberRows() {
        if (memberFrame !== null) view.cancelAnimationFrame(memberFrame);
        const request = settings.request;
        memberFrame = view.requestAnimationFrame(() => {
            memberFrame = null;
            if (settings.screen === 'editor' && settings.request === request && !settings.saving)
                refreshMemberRows({ viewOnly: true });
        });
    }
    const writeActions = new Set([
        'save', 'retry-save', 'finish', 'apply-finish', 'add', 'check-bulk',
        'apply-bulk', 'apply-one', 'picker-clear', 'picker-remove',
        'picker-select-visible', 'batch-add', 'batch-remove', 'apply-batch',
        'remove-selected', 'remove-one', 'apply-remove', 'save-leave', 'select-filtered'
    ]);
    async function start() {
        try {
            settings.session = await api('session/');
            settings.meta = await api('ui/metadata/');
            const id = new URLSearchParams(location.search).get('request');
            if (id && /^\d+$/.test(id))
                await openRequest(Number(id), { route: false });
            else
                await openRegistry({ route: false });
        }
        catch (err) {
            main.innerHTML = `<section class="vq-head"><h1>Не удалось загрузить заявки</h1></section><div class="vq-notice error">${esc(err.message)} <a href="/login/">Войти</a></div>`;
        }
    }
    function rememberMatrixFocus(button) {
        $$('[data-cell][tabindex="0"]').forEach(el => { if (el !== button) el.tabIndex = -1; });
        const [employeeId, itemId] = button.dataset.cell.split(':').map(Number);
        settings.matrixFocus = { employeeId, itemId };
        button.tabIndex = 0;
    }
    root.addEventListener('focusin', event => {
        const button = event.target.closest('[data-cell]');
        if (button && !button.disabled) rememberMatrixFocus(button);
    });
    // Делегирование сохраняет обработчики при адресной замене компонента.
    root.addEventListener('click', async (e) => {
        const target = e.target.closest('button,[data-open],[data-person],[data-prior]');
        if (!target || target.disabled)
            return;
        if (settings.busy && (settings.saving || target.dataset.action !== 'cancel')) return;
        const actionEpoch = ++settings.actionEpoch;
        try {
            if (target.dataset.open) {
                await openRequest(Number(target.dataset.open));
                return;
            }
            if (target.dataset.person) {
                await openDrawer(Number(target.dataset.person));
                return;
            }
            if (target.dataset.prior) {
                if (!can('view_workwearevent')) return;
                const records = settings.history.get(ctx.historyKey(settings.drawer)) || [];
                settings.archive = records.find(r => r.request_id === Number(target.dataset.prior));
                renderOverlay();
                return;
            }
            if (target.dataset.mode) {
                settings.tableMode = target.dataset.mode;
                renderEditor();
                return;
            }
            if (target.dataset.tab) {
                if (!editable() || settings.modal !== 'add') return;
                ++settings.dialogEpoch;
                settings.tab = target.dataset.tab;
                renderOverlay();
                return;
            }
            if (target.dataset.history) {
                if (!can('view_workwearevent')) return;
                const id = Number(target.dataset.history);
                settings.historyShown = settings.historyShown === id ? null : id;
                renderOverlay();
                return;
            }
            if (target.dataset.cell) {
                if (!editable()) return;
                const [employee, item] = target.dataset.cell.split(':').map(Number);
                rememberMatrixFocus(target);
                setBusy(true);
                await chooseAssignment(employee, item);
                return;
            }
            if (!target.dataset.action)
                return;
            const action = target.dataset.action;
            if (writeActions.has(action) && !editable()) return;
            if (['delete-draft', 'confirm-delete'].includes(action) &&
                    (!editable() || !can('delete_request'))) return;
            if (['reopen', 'apply-reopen'].includes(action) &&
                    (isDraft() || !can('reopen_request'))) return;
            if (['create', 'create-now'].includes(action) && !can('add_request')) return;
            if (action === 'retry-repeat' && !can('view_employee')) return;
            if (['events', 'events-prev', 'events-next', 'events-retry'].includes(action) && !can('view_workwearevent')) return;
            switch (action) {
                case 'events':
                    showOverlay('events');
                    await ctx.loadEvents();
                    break;
                case 'events-prev':
                case 'events-next':
                case 'events-retry':
                    if (settings.modal === 'events' && !settings.eventLog?.loading)
                        await ctx.loadEvents(Math.max(0, (settings.eventLog?.offset || 0) +
                            (action === 'events-next' ? 25 : action === 'events-prev' ? -25 : 0)));
                    break;
                case 'members-prev':
                case 'members-next':
                    settings.memberPageIndex += action === 'members-next' ? 1 : -1;
                    refreshMemberRows({ resetScroll: true });
                    if ($(`[data-action="${action}"]`)?.disabled) $('#editorPager button:not(:disabled)')?.focus({ preventScroll: true });
                    break;
                case 'columns-prev':
                case 'columns-next':
                    settings.columnPageIndex += action === 'columns-next' ? 1 : -1;
                    refreshMemberRows();
                    break;
                case 'select-filtered':
                    for (const member of memberVisible()) settings.selected.add(member.employee_id);
                    patchSelection();
                    break;
                case 'toggle-filters':
                    settings.registryOpen = !settings.registryOpen;
                    $('.vq-advanced-fields').hidden = !settings.registryOpen;
                    target.setAttribute('aria-expanded', String(settings.registryOpen));
                    break;
                case 'registry-prev':
                    settings.registry.offset = Math.max(0, settings.registry.offset - settings.registry.limit);
                    await loadRegistry();
                    break;
                case 'registry-next':
                    settings.registry.offset += settings.registry.limit;
                    await loadRegistry();
                    break;
                case 'reset-filters':
                    settings.registry = { q: '', status: '', organization_id: '', date_basis: 'created', date_from: '', date_to: '', sort: 'newest', offset: 0, limit: 30 };
                    renderRegistry();
                    await loadRegistry();
                    break;
                case 'create':
                    showOverlay('create');
                    break;
                case 'create-now':
                    setBusy(true);
                    await create();
                    break;
                case 'back':
                    if (dirty()) {
                        settings.pendingRoute = null;
                        showOverlay('leave');
                    }
                    else
                        await openRegistry();
                    break;
                case 'save':
                    setBusy(true);
                    await save();
                    break;
                case 'retry-save':
                    setBusy(true);
                    await save();
                    break;
                case 'retry-refresh':
                    if (dirty()) {
                        notice('Локальные изменения сохранены в редакторе. Сначала сохраните их.', true);
                    } else {
                        setBusy(true);
                        await openRequest(settings.request.id, { route: false, resetView: false });
                    }
                    break;
                case 'finish':
                    showOverlay('finish');
                    break;
                case 'apply-finish':
                    if (settings.ack) {
                        setBusy(true);
                        await save({ finish: true });
                    }
                    break;
                case 'reopen':
                    showOverlay('reopen');
                    break;
                case 'apply-reopen':
                    if (settings.ack) {
                        setBusy(true);
                        await api(`ui/requests/${settings.request.id}/reopen/`, { method: 'POST', data: { expected_version: settings.request.version, reason: settings.reopenReason } });
                        settings.modal = null;
                        await openRequest(settings.request.id, { route: false });
                        notice('Заявка возвращена в работу');
                    }
                    break;
                case 'delete-draft':
                    showOverlay('delete-draft');
                    break;
                case 'confirm-delete':
                    setBusy(true);
                    await api(`requests/${settings.request.id}/`, { method: 'DELETE', data: { expected_version: settings.request.version } });
                    settings.modal = null;
                    await openRegistry();
                    notice('Черновик удалён');
                    break;
                case 'add':
                    settings.tab = 'bulk';
                    settings.bulkText = '';
                    settings.bulkChecked = false;
                    settings.resolved = [];
                    settings.picked.clear();
                    settings.pickedEmployees.clear();
                    settings.searchEpoch++;
                    settings.singleQuery = '';
                    settings.singleResults = [];
                    settings.singleTotal = 0;
                    settings.singleHasMore = false;
                    settings.singleLoading = false;
                    showOverlay('add');
                    break;
                case 'check-bulk':
                    setBusy(true);
                    await resolveBulk();
                    break;
                case 'apply-bulk':
                    addBulk();
                    break;
                case 'apply-one': {
                    const existing = new Set(settings.request.members.map(m => m.employee_id));
                    const candidates = [...settings.picked].map(id => settings.pickedEmployees.get(id)).filter(e => e && !existing.has(e.id));
                    const added = ctx.addLocals(candidates);
                    if (added) {
                        settings.picked.clear(); settings.pickedEmployees.clear(); settings.modal = null;
                        renderEditor(); renderOverlay(); notice(`Добавлено в черновик: ${added}. Сохраните заявку.`);
                    } else notice('Не удалось добавить выбранных сотрудников', true);
                    break;
                }
                case 'picker-clear':
                    settings.picked.clear();
                    settings.pickedEmployees.clear();
                    patchSearchResults();
                    overlays.querySelector('#oneSearch')?.focus({ preventScroll: true });
                    break;
                case 'picker-remove': {
                    const id = Number(target.dataset.id);
                    settings.picked.delete(id);
                    settings.pickedEmployees.delete(id);
                    patchSearchResults();
                    (overlays.querySelector('[data-action="picker-remove"]') ||
                        overlays.querySelector('#oneSearch'))?.focus({ preventScroll: true });
                    break;
                }
                case 'picker-select-visible': {
                    const existing = new Set(settings.request.members.map(m => m.employee_id));
                    for (const e of settings.singleResults) {
                        if (existing.has(e.id) || e.is_current === false ||
                                e.organization_id !== settings.request.organization_id)
                            continue;
                        settings.picked.add(e.id);
                        settings.pickedEmployees.set(e.id, e);
                    }
                    patchSearchResults();
                    overlays.querySelector('#oneSearch')?.focus({ preventScroll: true });
                    break;
                }
                case 'batch-add':
                    settings.choiceMode = 'add';
                    settings.choice.clear();
                    showOverlay('batch');
                    break;
                case 'batch-remove':
                    settings.choiceMode = 'remove';
                    settings.choice.clear();
                    showOverlay('batch');
                    break;
                case 'apply-batch':
                    if (settings.choice.size) {
                        setBusy(true);
                        if (settings.choiceMode === 'add')
                            await scanBatchBeforeAdd();
                        else
                            batchChange('remove');
                    }
                    break;
                case 'remove-selected':
                    showOverlay('remove');
                    break;
                case 'remove-one':
                    showOverlay('remove-one');
                    break;
                case 'apply-remove': {
                    const ids = settings.modal === 'remove-one' ? [settings.drawer] : newSelection();
                    const removed = new Set(ids);
                    settings.request.members = settings.request.members.filter(m => !removed.has(m.employee_id));
                    for (const key of settings.warnings.keys()) if (removed.has(Number(key.split(':')[0]))) settings.warnings.delete(key);
                    ids.forEach(id => settings.selected.delete(id));
                    settings.drawer = null;
                    settings.modal = null;
                    renderEditor();
                    renderOverlay();
                    break;
                }
                case 'retry-repeat':
                    setBusy(true);
                    await scanRepeats();
                    break;
                case 'more-employees':
                    setBusy(true);
                    await searchEmployees(settings.singleQuery, true);
                    break;
                case 'show-selected':
                    settings.memberPageIndex = 0;
                    settings.memberFilter = 'selected';
                    settings.memberQuery = '';
                    renderEditor();
                    break;
                case 'clear-selection':
                    settings.selected.clear();
                    patchSelection();
                    break;
                case 'reset-member-search':
                    settings.memberQuery = '';
                    settings.memberFilter = 'all';
                    renderEditor();
                    break;
                case 'show-empty':
                    settings.modal = null;
                    settings.memberPageIndex = 0;
                    settings.memberFilter = 'empty';
                    renderEditor();
                    renderOverlay();
                    break;
                case 'close-drawer':
                    settings.drawer = null;
                    settings.archive = null;
                    settings.historyShown = null;
                    renderOverlay();
                    break;
                case 'history-back':
                    settings.archive = null;
                    renderOverlay();
                    break;
                case 'close-history':
                    settings.historyShown = null;
                    renderOverlay();
                    break;
                case 'reset-period':
                    settings.registry.date_basis = 'created';
                    settings.registry.date_from = '';
                    settings.registry.date_to = '';
                    settings.registry.sort = 'newest';
                    settings.registry.offset = 0;
                    renderRegistry();
                    await loadRegistry();
                    break;
                case 'cancel':
                    ctx.cancelDialog();
                    break;
                case 'discard': {
                    const id = settings.pendingRoute;
                    settings.modal = null;
                    settings.request = copy(settings.base);
                    if (id)
                        finishRoute(id);
                    else
                        await openRegistry();
                    break;
                }
                case 'save-leave': {
                    setBusy(true);
                    const id = settings.pendingRoute;
                    if (await save())
                        finishRoute(id);
                    break;
                }
                case 'reload':
                    if (settings.reloadAck) {
                        settings.modal = null;
                        settings.error = null;
                        await openRequest(settings.request.id, { route: false });
                    }
                    break;
            }
        }
        catch (err) {
            fail(err);
        }
        finally {
            if (actionEpoch === settings.actionEpoch) setBusy(false);
        }
    });
    root.addEventListener('change', e => {
        if (settings.saving) return;
        const target = e.target;
        if (target.dataset.filter) {
            if (target.dataset.filter === 'memberFilter') {
                settings.memberFilter = target.value;
                settings.memberPageIndex = 0;
                scheduleMemberRows();
            }
            else {
                settings.registry[target.dataset.filter] = target.value;
                settings.registry.offset = 0;
                const advanced = $('[data-action="toggle-filters"]');
                if (advanced)
                    advanced.textContent = 'Период и сортировка' + (settings.registry.date_basis !== 'created' || settings.registry.date_from || settings.registry.date_to || settings.registry.sort !== 'newest' ? ' · заданы' : '');
                loadRegistry();
            }
        }
        if (target.dataset.check && editable()) {
            const ids = target.dataset.check === 'all' ? ctx.memberPage().rows.map(m => m.employee_id) : [Number(target.dataset.check)];
            for (const id of ids) {
                if (target.checked)
                    settings.selected.add(id);
                else
                    settings.selected.delete(id);
            }
            patchSelection();
        }
        if (target.dataset.choice && editable()) {
            const id = Number(target.dataset.choice);
            if (target.checked)
                settings.choice.add(id);
            else
                settings.choice.delete(id);
            const apply = $('[data-action="apply-batch"]', overlays);
            if (apply) {
                apply.disabled = !settings.choice.size;
                apply.textContent = `${settings.choiceMode === 'add' ? 'Добавить' : 'Убрать'} ${settings.choice.size} видов`;
            }
            const note = $('#batchChoiceCount', overlays);
            if (note)
                note.textContent = `Выбрано видов: ${settings.choice.size}`;
        }
        if (target.dataset.pick && editable() && can('view_employee')) {
            const id = Number(target.dataset.pick);
            if (target.checked) {
                const person = settings.singleResults.find(e => e.id === id);
                const exists = settings.request.members.some(m => m.employee_id === id);
                if (person && !exists && person.is_current !== false &&
                        person.organization_id === settings.request.organization_id) {
                    settings.picked.add(id);
                    settings.pickedEmployees.set(id, person);
                }
            } else {
                settings.picked.delete(id);
                settings.pickedEmployees.delete(id);
            }
            patchSearchResults();
            overlays.querySelector(`[data-pick="${id}"]`)?.focus({ preventScroll: true });
        }
        if (target.dataset.resolve && editable() && can('view_employee')) {
            settings.resolved[Number(target.dataset.resolve)].chosen = target.value ? Number(target.value) : null;
            const ready = ctx.bulkCandidates().length;
            const apply = $('[data-action="apply-bulk"]', overlays);
            if (apply) {
                apply.disabled = !ready;
                apply.textContent = `Добавить найденных (${ready})`;
            }
            const note = $('#bulkReadyNote', overlays);
            if (note) note.textContent = `Готовы к добавлению: ${ready}`;
        }
        if (target.dataset.item && editable()) {
            void chooseAssignment(settings.drawer, Number(target.dataset.item), target.checked).catch(fail);
        }
        if (target.id === 'finishAck' || target.id === 'reopenAck') {
            settings.ack = target.checked;
            renderOverlay();
        }
        if (target.id === 'reloadAck') {
            settings.reloadAck = target.checked;
            renderOverlay();
        }
    });
    let debounce = null;
    root.addEventListener('input', e => {
        if (settings.saving) return;
        const t = e.target;
        if (t.dataset.input === 'registry-search') {
            settings.registry.q = t.value;
            settings.registry.offset = 0;
            clearTimeout(debounce);
            debounce = setTimeout(loadRegistry, 260);
        }
        if (t.dataset.input === 'member-search') {
            settings.memberQuery = t.value;
            settings.memberPageIndex = 0;
            clearTimeout(debounce);
            scheduleMemberRows();
        }
        if (t.dataset.input === 'one-search' && editable() && can('view_employee')) {
            settings.singleAbort?.abort();
            settings.singleQuery = t.value;
            settings.searchEpoch++;
            settings.singleResults = [];
            settings.singleTotal = 0;
            settings.singleHasMore = false;
            settings.singleLoading = settings.singleQuery.trim().length >= 2;
            patchSearchResults();
            clearTimeout(debounce);
            debounce = setTimeout(() => searchEmployees(settings.singleQuery), 250);
        }
        if (t.id === 'bulkText') {
            settings.bulkAbort?.abort();
            ++settings.bulkEpoch;
            settings.bulkText = t.value;
            settings.resolved = [];
            settings.bulkChecked = false;
            const check = $('[data-action="check-bulk"]');
            if (check)
                check.disabled = !t.value.trim();
        }
        if (t.id === 'reopenReason') {
            settings.reopenReason = t.value;
        }
    });
    root.addEventListener('keydown', e => {
        const cellButton = e.target.closest('[data-cell]');
        if (cellButton && !settings.modal && settings.drawer === null &&
                ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown'].includes(e.key)) {
            e.preventDefault();
            const [employeeId, itemId] = cellButton.dataset.cell.split(':').map(Number);
            const rows = memberVisible(), items = ctx.byItem();
            let row = rows.findIndex(m => m.employee_id === employeeId), column = items.findIndex(it => it.id === itemId);
            if (row < 0 || column < 0) return;
            if (e.key === 'ArrowLeft') column = Math.max(0, column - 1);
            if (e.key === 'ArrowRight') column = Math.min(items.length - 1, column + 1);
            if (e.key === 'ArrowUp') row = Math.max(0, row - 1);
            if (e.key === 'ArrowDown') row = Math.min(rows.length - 1, row + 1);
            if (e.key === 'PageUp') row = Math.max(0, row - ctx.pageRowSize);
            if (e.key === 'PageDown') row = Math.min(rows.length - 1, row + ctx.pageRowSize);
            if (e.key === 'Home') { column = 0; if (e.ctrlKey) row = 0; }
            if (e.key === 'End') { column = items.length - 1; if (e.ctrlKey) row = rows.length - 1; }
            const available = () => ctx.cellAvailable(rows[row], items[column]);
            const vertical = ['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown'].includes(e.key);
            const step = ['ArrowLeft', 'ArrowUp', 'PageUp', 'End'].includes(e.key) ? -1 : 1;
            while (row >= 0 && row < rows.length && column >= 0 && column < items.length && !available()) {
                if (vertical) row += step;
                else column += step;
            }
            if (row < 0 || row >= rows.length || column < 0 || column >= items.length) return;
            const rowPage = Math.floor(row / ctx.pageRowSize), colPage = Math.floor(column / ctx.pageColumnSize);
            if (rowPage !== settings.memberPageIndex || colPage !== settings.columnPageIndex) {
                settings.memberPageIndex = rowPage; settings.columnPageIndex = colPage;
                settings.matrixFocus = { employeeId: rows[row].employee_id, itemId: items[column].id };
                refreshMemberRows({ resetScroll: true });
            }
            const next = $(`[data-cell="${rows[row].employee_id}:${items[column].id}"]`);
            if (next && !next.disabled) {
                rememberMatrixFocus(next);
                next.focus({ preventScroll: true });
                next.scrollIntoView({ block: 'nearest', inline: 'nearest' });
                // Закреплённые колонки имени и итогов не должны закрывать активную ячейку.
                const viewport = next.closest('.vq-tablewrap');
                const bounds = next.getBoundingClientRect(), rowElement = next.closest('tr');
                const left = rowElement.querySelector('.sticky')?.getBoundingClientRect().right;
                const right = rowElement.querySelector('.sumcol')?.getBoundingClientRect().left;
                if (right !== undefined && bounds.right > right - 4)
                    viewport.scrollLeft += bounds.right - right + 4;
                else if (left !== undefined && bounds.left < left + 4)
                    viewport.scrollLeft -= left - bounds.left + 4;
                const headerBottom = viewport.querySelector('thead')?.getBoundingClientRect().bottom;
                if (headerBottom !== undefined && bounds.top < headerBottom + 4)
                    viewport.scrollTop -= headerBottom - bounds.top + 4;
            }
            return;
        }
        const modalRoot = overlays.querySelector('.vq-modal,.vq-drawer');
        if (modalRoot) cycleFocus(e, modalRoot);
        if (e.key === 'Escape' && settings.modal) {
            ctx.cancelDialog();
        }
        else if (e.key === 'Escape' && settings.drawer !== null) {
            settings.drawer = null;
            renderOverlay();
        }
        if (e.key === 'Enter' && e.target.matches('[data-open]')) {
            e.preventDefault();
            openRequest(Number(e.target.dataset.open));
        }
    });
    window.addEventListener('popstate', () => {
        const incoming = new URLSearchParams(location.search).get('request');
        const id = incoming && /^\d+$/.test(incoming) ? Number(incoming) : null;
        if (dirty()) {
            settings.pendingRoute = id;
            syncUrl(settings.request.id, true);
            showOverlay('leave');
        }
        else
            finishRoute(id);
    });
    window.addEventListener('beforeunload', e => { if (dirty()) {
        e.preventDefault();
        e.returnValue = '';
    } });
    return start;
}
