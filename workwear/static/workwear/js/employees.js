/* Поиск и пакетный подбор сотрудников Workwear. installEmployees(ctx) публикует
 * single/bulk операции; выбранные picked/pickedEmployees принадлежат settings
 * и переживают смену запроса. searchEpoch и dialogEpoch отменяют старые ответы,
 * bulkEpoch инвалидируется изменением текста. Разрешение списка не пишет в БД;
 * addLocal меняет локальный состав, окончательная проверка выполняется sync.
 * Ошибка сети сохраняет выбор и текст; DOM обновляется внутри панели подбора. */
export function installEmployees(ctx) {
    const { settings, overlays } = ctx;
    const api = (...args) => ctx.api(...args);
    const notice = (...args) => ctx.notice(...args);
    const $ = (...args) => ctx.$(...args);
    const currentMembers = (...args) => ctx.currentMembers(...args);
    const esc = (...args) => ctx.esc(...args);
    const renderOverlay = (...args) => ctx.renderOverlay(...args);
    const addLocal = (...args) => ctx.addLocal(...args);
    const refreshMemberRows = (...args) => ctx.refreshMemberRows(...args);
    async function searchEmployees(q, append = false) {
        settings.singleAbort?.abort();
        const controller = new AbortController(); settings.singleAbort = controller;
        const epoch = ++settings.searchEpoch;
        if (q.trim().length < 2) {
            settings.singleResults = [];
            settings.singleTotal = 0;
            settings.singleHasMore = false;
            settings.singleLoading = false;
            patchSearchResults();
            return;
        }
        const offset = append ? settings.singleResults.length : 0;
        settings.singleLoading = true;
        patchSearchResults();
        try {
            const data = await api('ui/employees/?' + new URLSearchParams({ q, organization_name: settings.request.organization_name, offset, limit: 40 }), { signal: controller.signal });
            if (epoch !== settings.searchEpoch || settings.modal !== 'add' || settings.singleQuery !== q)
                return;
            settings.singleResults = append ? [...settings.singleResults, ...data.results] : data.results;
            settings.singleTotal = data.count || 0;
            settings.singleHasMore = !!data.has_more;
            settings.singleLoading = false;
            patchSearchResults();
        }
        catch (err) {
            if (epoch === settings.searchEpoch) {
                settings.singleLoading = false;
                notice(err.message, true);
            }
        }
    }
    function patchSearchResults() {
        const region = $('#singleResults');
        if (!region) return;
        const already = new Set(currentMembers().map(m => m.employee_id));
        const options = settings.singleResults;
        const q = settings.singleQuery.trim();
        const valid = q.length >= 2;
        const available = options.filter(e => !already.has(e.id) && e.is_current !== false);
        const picked = [...settings.pickedEmployees.values()]
            .filter(e => settings.picked.has(e.id) && !already.has(e.id));
        const selected = $('#singleSelected');
        if (selected) {
            selected.hidden = picked.length === 0;
            selected.innerHTML = picked.length ? `<div class="vq-picker-selected-head"><strong>К добавлению · ${picked.length}</strong><button type="button" class="vq-picker-text-btn" data-action="picker-clear">Очистить выбор</button></div><div class="vq-picker-chips">${picked.map(e => `<span class="vq-picker-chip"><span title="${esc(e.full_name)}">${esc(e.full_name)} <small>${esc(e.personnel_number)}</small></span><button type="button" data-action="picker-remove" data-id="${e.id}" aria-label="Убрать ${esc(e.full_name)} из выбранных">×</button></span>`).join('')}</div>` : '';
        }
        const status = $('#singleMatchCount');
        if (status) {
            status.textContent = !q ? 'Введите минимум 2 символа' : !valid ? 'Нужно ещё символов: ' + (2-q.length) :
                settings.singleLoading ? 'Поиск…' : `Найдено: ${settings.singleTotal}`;
        }
        const unchecked = available.filter(e => !settings.picked.has(e.id)).length;
        const toolbar = valid && !settings.singleLoading && options.length ?
            `<div class="vq-picker-result-head"><span>Результаты поиска</span>${unchecked ? `<button type="button" class="vq-picker-text-btn" data-action="picker-select-visible">Выбрать показанных (${unchecked})</button>` : ''}</div>` : '';
        let content = '';
        if (!valid) content = `<div class="vq-picker-empty">Найдите сотрудника по ФИО или табельному номеру. Можно выбрать нескольких из разных запросов.</div>`;
        else if (settings.singleLoading) content = '<div class="vq-picker-empty" role="status">Ищем сотрудников…</div>';
        else if (!options.length) content = `<div class="vq-picker-empty">По запросу «${esc(q)}» совпадений нет. Проверьте написание или табельный номер.</div>`;
        else content = `<div class="vq-picker-list">${options.map(e => {
            const included = already.has(e.id);
            const inactive = e.is_current === false;
            const disabled = included || inactive;
            const checked = settings.picked.has(e.id);
            const detail = `${esc(e.personnel_number)}${e.position_name ? ' · ' + esc(e.position_name) : ''}`;
            return `<label class="vq-picker-row${checked ? ' is-selected' : ''}${disabled ? ' is-disabled' : ''}"><input type="checkbox" data-pick="${e.id}" ${checked ? 'checked' : ''} ${disabled ? 'disabled' : ''} aria-label="Выбрать ${esc(e.full_name)}"><span class="vq-picker-person"><strong>${esc(e.full_name)}</strong><small>${detail}</small>${e.department_name ? `<small>${esc(e.department_name)}</small>` : ''}</span>${disabled ? `<span class="vq-picker-status">${included ? 'В заявке' : 'Недоступен'}</span>` : ''}</label>`;
        }).join('')}</div>`;
        const more = valid && options.length && !settings.singleLoading && settings.singleHasMore &&
            options.length < settings.singleTotal ?
            `<button type="button" class="vq-btn small vq-picker-more" data-action="more-employees">Показать ещё · ${options.length} из ${settings.singleTotal}</button>` : '';
        region.innerHTML = toolbar + content + more;
        const note = $('#singlePickedNote');
        if (note) note.textContent = `Выбрано: ${picked.length}`;
        const apply = $('[data-action="apply-one"]', overlays);
        if (apply) {
            apply.disabled = picked.length === 0;
            apply.textContent = `Добавить (${picked.length})`;
        }
    }
    /** Подготовить весь список без записи; смена текста/диалога отменяет очередь. */
    async function resolveBulk() {
        settings.bulkAbort?.abort();
        const controller = new AbortController(); settings.bulkAbort = controller;
        const epoch = ++settings.bulkEpoch, dialog = settings.dialogEpoch;
        const text = settings.bulkText, requestId = settings.request.id;
        const lines = text.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
        if (!lines.length || lines.length > ctx.limits().request_members) {
            notice('Вставьте от1 до1000 строк', true); return;
        }
        const valid = () => settings.request?.id === requestId && settings.modal === 'add' && settings.tab === 'bulk' &&
            settings.bulkEpoch === epoch && settings.dialogEpoch === dialog && settings.bulkText === text;
        const progress = value => {
            const target = $('#bulkReadProgress');
            if (target) target.textContent = `Проверено строк: ${value.done} из ${value.total}. Состав ещё не изменён.`;
        };
        settings.bulkChecked = false;
        const apply = $('[data-action="apply-bulk"]'); if (apply) apply.disabled = true;
        try {
            const present = currentMembers().map(m => m.employee_id), org = settings.request.organization_name;
            const responses = await ctx.readChunks('ui/resolve/', lines, rows => ({ organization_name: org, lines: rows,
                request_id: requestId, present_employee_ids: present }), { maxItems: 100, signal: controller.signal, valid, progress });
            if (!valid()) return;
            if (responses.some(r => !Array.isArray(r.results) || !Object.hasOwn(r, 'import_id')) ||
                    new Set(responses.map(r => r.import_id)).size > 1)
                throw new Error('Справочник изменился во время проверки. Проверьте весь список повторно.');
            const used = new Set(present);
            settings.resolved = responses.flatMap(r => r.results).map(row => {
                if (row.status === 'matched') {
                    const id = row.candidates[0].id;
                    if (used.has(id)) row = { ...row, status: 'duplicate' };
                    else used.add(id);
                }
                return { ...row, chosen: null };
            });
            settings.bulkChecked = true; renderOverlay();
        } catch (err) { if (valid()) throw err; }
    }
    /** Единый список уникальных готовых кандидатов для счётчика и применения. */
    function bulkCandidates() {
        const existing = new Set(currentMembers().map(m => m.employee_id)), result = [];
        for (const row of settings.resolved) {
            const candidate = row.status === 'matched' ? row.candidates[0] : row.status === 'ambiguous' && row.chosen ? row.candidates.find(x => x.id === row.chosen) : null;
            if (candidate && !existing.has(candidate.id)) { existing.add(candidate.id); result.push(candidate); }
        }
        return result;
    }
    function addBulk() {
        const count = ctx.addLocals(bulkCandidates());
        settings.modal = null; settings.resolved = []; settings.bulkText = '';
        refreshMemberRows(); renderOverlay();
        notice(`Добавлено в черновик: ${count}. Не забудьте сохранить заявку.`);
    }
    function batchFits(items) {
        const wanted = new Set(items);
        return currentMembers().filter(m => settings.selected.has(m.employee_id))
            .every(m => new Set([...m.assignments.map(a => a.item_id), ...wanted]).size <= ctx.limits().items_per_member);
    }
    /** Применить массовое локальное действие ко всем выбранным, включая другие страницы. */
    function batchChange(operation) {
        if (settings.saving || !settings.choice.size) return;
        const items = new Set(settings.choice);
        if (operation === 'add' && !batchFits(items)) {
            notice('Превышен максимум позиций у сотрудника. Состав не изменён.', true); return;
        }
        for (const m of currentMembers().filter(m => settings.selected.has(m.employee_id))) {
            if (operation === 'remove') {
                m.assignments = m.assignments.filter(a => !items.has(a.item_id));
                for (const id of items) settings.warnings.delete(`${m.employee_id}:${id}`);
            } else {
                const assigned = new Set(m.assignments.map(a => a.item_id));
                for (const id of items) if (!assigned.has(id)) m.assignments.push({ id: null, item_id: id });
            }
        }
        settings.modal = null; settings.choice.clear();
        refreshMemberRows(); renderOverlay();
        notice('Массовые изменения подготовлены. Сохраните заявку.');
    }
    async function scanBatchBeforeAdd() {
        if (!batchFits(settings.choice)) { notice('Превышен максимум позиций у сотрудника. Состав не изменён.', true); return; }
        settings.batchAbort?.abort();
        const controller = new AbortController(); settings.batchAbort = controller;
        const request = settings.request, dialog = settings.dialogEpoch;
        const selection = JSON.stringify([...settings.selected].sort((a, b) => a - b));
        const choice = JSON.stringify([...settings.choice].sort((a, b) => a - b));
        const pairs = [];
        for (const m of currentMembers().filter(m => settings.selected.has(m.employee_id)))
            for (const itemId of settings.choice)
                if (!m.assignments.some(a => a.item_id === itemId))
                    pairs.push({ employee_id: m.employee_id, item_id: itemId });
        if (pairs.length) {
            const valid = () => settings.request === request && settings.modal === 'batch' && settings.dialogEpoch === dialog &&
                JSON.stringify([...settings.selected].sort((a,b) => a-b)) === selection && JSON.stringify([...settings.choice].sort((a,b) => a-b)) === choice;
            let result;
            try { result = await ctx.repeatPairs(pairs, { signal: controller.signal, valid, progress: value => {
                const target = $('#batchReadProgress'); if (target) target.textContent = `Проверено назначений: ${value.done} из ${value.total}.`;
            } }); } catch (err) { if (valid()) throw err; return; }
            if (settings.request !== request || settings.modal !== 'batch' || settings.dialogEpoch !== dialog ||
                    JSON.stringify([...settings.selected].sort((a, b) => a - b)) !== selection ||
                    JSON.stringify([...settings.choice].sort((a, b) => a - b)) !== choice) {
                notice('Выбор изменился. Повторите проверку перед назначением.', true);
                return;
            }
            for (const w of result.warnings)
                settings.warnings.set(`${w.employee_id}:${w.item_id}`, w);
            if (result.warnings.length)
                notice(`Обнаружено повторных назначений: ${result.warnings.length}. Сроки показаны в карточке сотрудника.`, true);
        }
        batchChange('add');
    }
    Object.assign(ctx, {
        searchEmployees, patchSearchResults, resolveBulk, bulkCandidates, addBulk, batchChange, scanBatchBeforeAdd
    });
}
