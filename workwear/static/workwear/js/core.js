/* Ядро локального UI Workwear. installCore(ctx) создаёт settings, DOM-ссылки и
 * HTTP-клиент; остальные install-функции получают их явно через ctx. settings
 * принадлежит этому сеансу страницы, base — последнему подтверждённому снимку.
 * api передаёт CSRF; dirty сравнивает нормализованный черновик без записи в БД.
 * busy блокирует ввод только во время сохранения и восстанавливает фокус.
 * page/search/refresh/bulk/dialog/history/repeat Epoch отбрасывают поздние ответы.
 * Описание публичного ctx и инвалидации: docs/architecture.md. */
export function installCore(ctx) {
    const renderOverlay = (...args) => ctx.renderOverlay(...args);
    const root = document.getElementById('veq-app');
    if (!root || root.dataset.ready)
        return;
    root.dataset.ready = '1';
    const main = document.getElementById('main');
    const overlays = document.getElementById('overlays');
    const feedback = document.getElementById('feedback');
    const API = root.dataset.apiBase;
    const $ = (selector, parent = root) => parent.querySelector(selector);
    const $$ = (selector, parent = root) => [...parent.querySelectorAll(selector)];
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const copy = value => JSON.parse(JSON.stringify(value));
    const plural = (n, forms) => forms[n % 100 >= 11 && n % 100 <= 14 ? 2 : n % 10 === 1 ? 0 : n % 10 >= 2 && n % 10 <= 4 ? 1 : 2];
    const fmt = iso => iso ? new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'Europe/Moscow' }).format(new Date(iso)) : '—';
    const fmtTime = iso => iso ? new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' }).format(new Date(iso)) : '';
    const norm = str => String(str || '').trim().replace(/\s+/g, ' ').toLocaleLowerCase('ru-RU').replace(/ё/g, 'е');
    const icon = key => ctx.platformIcon(key);
    const btn = (title, action, cls = '', extra = '') => `<button type="button" class="vq-btn ${cls}" data-action="${action}" ${extra}>${title}</button>`;
    const field = (placeholder, value, id) => `<label class="vq-field">${icon('search')}<input data-input="${id}" autocomplete="off" value="${esc(value)}" placeholder="${esc(placeholder)}" aria-label="${esc(placeholder)}"></label>`;
    const settings = {
        screen: 'registry', session: null, meta: null, request: null, base: null,
        registry: { q: '', status: '', organization_id: '', date_basis: 'created', date_from: '', date_to: '', sort: 'newest', offset: 0, limit: 30 },
        registryOpen: false, registryData: null, pageEpoch: 0, searchEpoch: 0, refreshEpoch: 0,
        bulkEpoch: 0, dialogEpoch: 0, historyEpoch: 0, matrixFocus: null, pendingAssignments: new Map(),
        memberQuery: '', memberFilter: 'all', tableMode: 'list', memberPageIndex: 0, columnPageIndex: 0, actionEpoch: 0, selected: new Set(),
        warnings: new Map(), repeatState: 'idle', repeatEpoch: 0, repeatAbort: null, repeatProgress: null, bulkAbort: null, batchAbort: null, singleAbort: null,
        history: new Map(), historyShown: null, archive: null,
        modal: null, drawer: null, tab: 'bulk', bulkText: '', bulkChecked: false, resolved: [], singleQuery: '', singleResults: [], singleTotal: 0, singleHasMore: false, singleLoading: false, picked: new Set(), pickedEmployees: new Map(),
        choice: new Set(), choiceMode: 'add', ack: false, reopenReason: '', reloadAck: false,
        saving: false, error: null, remote: null, busy: false, pendingRoute: null, toastTimer: null, overlayReturn: null,
    };
    const can = permission => !!settings.meta?.permissions?.[permission];
    const historyKey = employeeId => `${settings.request?.id}:${settings.request?.version}:${employeeId}`;
    const assignmentKey = (employeeId, itemId) => `${settings.request?.id}:${employeeId}:${itemId}`;
    const controlSelector = el => {
        if (!el) return null;
        for (const attr of ['data-cell', 'data-action', 'data-history', 'data-item', 'data-choice', 'data-pick', 'data-resolve', 'data-input', 'data-filter', 'data-check', 'data-person', 'data-mode', 'data-tab', 'id']) {
            const value = el.getAttribute?.(attr);
            if (value !== null && value !== undefined) return `[${attr}="${CSS.escape(value)}"]`;
        }
        return null;
    };
    const frozenControls = new Map();
    let busyFocus = null;
    function setBusy(value) {
        if (value && !settings.busy) busyFocus = controlSelector(document.activeElement);
        settings.busy = value;
        root.setAttribute('aria-busy', String(value));
        if (value && settings.saving) {
            for (const el of $$('button,input,select,textarea')) {
                if (!frozenControls.has(el)) frozenControls.set(el, !!el.disabled);
                el.disabled = true;
            }
        } else if (!value) {
            for (const [el, disabled] of frozenControls) el.disabled = disabled;
            frozenControls.clear();
            const restore = busyFocus && $(busyFocus);
            if (restore && !restore.disabled) restore.focus({ preventScroll: true });
            busyFocus = null;
        }
    }
    const shortItem = name => name;
    let catalogCache = null;
    const byItem = () => {
        const catalog = settings.meta?.items || [];
        // Снимок сервера меняется при загрузке/сохранении. Индекс строится один раз,
        // чтобы отрисовка каждой строки не сканировала до 30 000 назначений.
        if (catalogCache?.catalog === catalog && catalogCache?.base === settings.base)
            return catalogCache.items.slice();
        const assigned = new Map();
        for (const member of settings.base?.members || []) for (const a of member.assignments) {
            if (a.name && !assigned.has(a.item_id)) assigned.set(a.item_id, a);
        }
        const items = catalog.filter(it => (isDraft() && it.is_active) || assigned.has(it.id)).map(it => {
            const snapshot = assigned.get(it.id);
            return snapshot ? { ...it, name: snapshot.name, category: snapshot.category,
                validity_days: snapshot.validity_days, code: snapshot.code } : it;
        });
        const known = new Set(items.map(it => it.id));
        for (const [id, snapshot] of assigned) if (!known.has(id))
            items.push({ ...snapshot, id, is_active: false });
        catalogCache = { catalog, base: settings.base, items };
        return items.slice();
    };
    const currentMembers = () => settings.request?.members || [];
    const importSummary = () => {
        const data = settings.meta?.import;
        if (!data?.accepted_at) return 'Справочник 1С не загружен';
        return `Принят: ${fmt(data.accepted_at)}`;
    };
    const allOrgOptions = (value, all = false) => (all ? `<option value="" ${!value ? 'selected' : ''}>Все организации</option>` : '') + (settings.meta?.registry_organizations || []).map(o => `<option value="${esc(o.value)}" ${String(o.value) === String(value) ? 'selected' : ''}>${esc(o.label)}</option>`).join('');
    const tone = (n) => n === 'closed' ? 'Завершена' : 'Черновик';
    const isDraft = () => settings.request?.status === 'draft';
    const editable = () => isDraft() && can('change_request');
    const newSelection = () => {
        const present = new Set(currentMembers().map(m => m.employee_id));
        return [...settings.selected].filter(id => present.has(id));
    };
    const itemNames = itemIds => itemIds.map(id => byItem().find(it => it.id === id)?.name).filter(Boolean);
    const assignments = member => member.assignments.map(a => a.item_id);
    const assignedCount = () => currentMembers().reduce((n, m) => n + m.assignments.length, 0);
    const emptyCount = () => currentMembers().filter(m => !m.assignments.length).length;
    function normalizedDraft(r) {
        return r.members.map(m => ({ employee_id: m.employee_id, item_ids: assignments(m).sort((a, b) => a - b) })).sort((a, b) => a.employee_id - b.employee_id);
    }
    function dirty() {
        return isDraft() && settings.base && JSON.stringify(normalizedDraft(settings.request)) !== JSON.stringify(normalizedDraft(settings.base));
    }
    function notice(message, bad = false) {
        clearTimeout(settings.toastTimer);
        feedback.className = 'vq-toast' + (bad ? ' error' : '');
        feedback.textContent = message;
        settings.toastTimer = setTimeout(() => { feedback.textContent = ''; feedback.className = ''; }, 4800);
    }
    async function api(path, { method = 'GET', data, signal } = {}) {
        const headers = { 'Accept': 'application/json' };
        if (data !== undefined) {
            headers['Content-Type'] = 'application/json';
            headers['X-CSRFToken'] = settings.session?.csrf_token || '';
        }
        const response = await fetch(API + path, { method, credentials: 'same-origin', headers, signal, ...(data === undefined ? {} : { body: JSON.stringify(data) }) });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
            const error = new Error(result.error?.message || `Ошибка HTTP ${response.status}`);
            error.status = response.status;
            error.code = result.error?.code;
            throw error;
        }
        return result;
    }
    function fail(err) {
        if (err.status === 409) {
            settings.error = 'conflict';
            settings.remote = null;
            settings.modal = 'conflict';
            renderOverlay();
            if (settings.request)
                api(`requests/${settings.request.id}/`).then(data => {
                    if (settings.modal === 'conflict' && settings.request?.id === data.request.id) {
                        settings.remote = data.request;
                        renderOverlay();
                    }
                }).catch(() => { });
        }
        else if (err.status === 401)
            notice('Сессия завершена. Выполните вход заново.', true);
        else
            notice(err.name === 'TypeError' && /fetch|NetworkError|Load failed/i.test(err.message || '')
                    ? 'Не удалось связаться с сервером. Повторите попытку.'
                    : err.message || 'Ошибка операции', true);
    }
    Object.assign(ctx, {
        root, main, overlays, feedback, API, $, $$, esc, copy, plural, fmt, fmtTime, norm, icon, btn, field, settings, can, historyKey, assignmentKey, controlSelector, setBusy, shortItem, byItem, currentMembers, importSummary, allOrgOptions, tone, isDraft, editable, newSelection, itemNames, assignments, assignedCount, emptyCount, normalizedDraft, dirty, notice, api, fail
    });
    return true;
}
