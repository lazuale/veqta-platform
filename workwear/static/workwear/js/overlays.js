/* Ответ закрытого диалога не должен открывать его снова. История не даёт права изменять заявку. */
import { syncModalInert } from '../../portal/ui.js';

export function installOverlays(ctx) {
    const {
        settings, overlays, root, fmt, esc, btn, icon, plural, byItem, currentMembers,
        assignedCount, warningsCount, emptyCount, newSelection, assignments, itemNames, api,
        groups, warning, editable, can, isDraft, dirty, patchSearchResults, notice,
    } = ctx;
    function modalAllowed(modal) {
        if (modal === 'create') return can('add_request');
        if (modal === 'delete-draft') return editable() && can('delete_request');
        if (modal === 'reopen') return !isDraft() && can('reopen_request');
        if (modal === 'events') return can('view_request') && can('view_workwearevent');
        if (['add', 'batch', 'finish', 'remove', 'remove-one', 'leave'].includes(modal)) return editable();
        return true;
    }
    function showOverlay(modal) {
        if (!modalAllowed(modal)) return;
        settings.eventLog?.abort.abort(); settings.eventLog = null;
        settings.bulkAbort?.abort(); settings.batchAbort?.abort(); settings.singleAbort?.abort();
        ++settings.dialogEpoch;
        ++settings.bulkEpoch;
        settings.modal = modal;
        settings.ack = false;
        settings.reopenReason = '';
        settings.reloadAck = false;
        renderOverlay();
    }
    /** Подтверждённую запись не повторяем при закрытии диалога. */
    function cancelDialog() {
        if (settings.saving) return;
        settings.eventLog?.abort.abort(); settings.eventLog = null;
        settings.bulkAbort?.abort(); settings.batchAbort?.abort(); settings.singleAbort?.abort();
        ++settings.dialogEpoch; ++settings.bulkEpoch; ++settings.actionEpoch;
        settings.modal = null; renderOverlay(); ctx.setBusy(false);
    }
    function rosterHint() {
        return `<div class="vq-picker-source"><span><strong>${esc(settings.request.snapshot_organization_name)}</strong></span><span>Справочник 1С · ${esc(ctx.importSummary())}</span></div>`;
    }
    const eventLabels = {
        created: 'Заявка создана', draft_synced: 'Черновик сохранён',
        completed: 'Заявка завершена', reopened: 'Заявка возвращена в работу',
        member_added: 'Сотрудник добавлен', member_removed: 'Сотрудник исключён',
        assignment_added: 'Позиция назначена', assignment_removed: 'Назначение снято',
        draft_deleted: 'Черновик удалён',
    };
    const eventTime = new Intl.DateTimeFormat('ru-RU', { timeZone: 'Europe/Moscow', dateStyle: 'short', timeStyle: 'medium' });
    function eventDescription(event) {
        const d = event.details || {};
        if (event.event_type === 'created') return d.snapshot_organization_name || '';
        if (event.event_type === 'reopened') return d.reason ? `Причина: ${d.reason}` : 'Причина не указана';
        if (event.event_type === 'completed')
            return `Сотрудников: ${d.final_members}. Назначений: ${d.final_assignments}. Исключено без позиций: ${d.excluded_members}.`;
        if (event.event_type === 'draft_synced') {
            const changes = d.changes || {};
            return `Сотрудников: ${d.members}. Назначений: ${d.assignments}. ` +
                `Добавлено сотрудников: ${changes.members_added?.length || 0}. Исключено: ${changes.members_removed?.length || 0}. ` +
                `Добавлено назначений: ${changes.assignments_added?.length || 0}. Снято: ${changes.assignments_removed?.length || 0}.`;
        }
        return '';
    }
    async function loadEvents(offset = 0) {
        if (settings.modal !== 'events' || !modalAllowed('events') || !settings.request) return;
        settings.eventLog?.abort.abort();
        const log = { requestId: settings.request.id, offset, results: [], count: 0,
            loading: true, error: null, abort: new AbortController() };
        settings.eventLog = log;
        const current = () => settings.eventLog === log && settings.modal === 'events' && settings.request?.id === log.requestId;
        renderOverlay();
        try {
            const data = await api(`ui/requests/${log.requestId}/events/?offset=${offset}&limit=25`, { signal: log.abort.signal });
            if (!current()) return;
            if (!Array.isArray(data.results) || data.results.length > 25 ||
                    !Number.isSafeInteger(data.count) || data.count < 0 || data.offset !== offset ||
                    typeof data.has_more !== 'boolean' || data.results.some(event =>
                        !Number.isSafeInteger(event.id) || typeof event.event_type !== 'string' ||
                        typeof event.actor_username !== 'string' || !Number.isFinite(Date.parse(event.occurred_at))))
                throw new Error('Некорректный ответ журнала');
            Object.assign(log, {results:data.results, count:data.count, offset:data.offset, has_more:data.has_more});
        } catch (err) {
            if (current()) log.error = 'Историю не удалось загрузить. Повторите попытку.';
        } finally {
            if (current()) {
                log.loading = false;
                renderOverlay();
                const body = overlays.querySelector('.vq-modal-body');
                if (body) body.scrollTop = 0;
            }
        }
    }
    function modalMarkup() {
        const m = settings.modal;
        if (!m || !modalAllowed(m))
            return '';
        let title = '', subtitle = '', body = '', footer = '', tabs = '', size = '';
        const actions = (...buttons) => `<span></span><div class="vq-actions">${buttons.join('')}</div>`;
        if (m === 'events') {
            const log = settings.eventLog;
            title = 'История операций';
            subtitle = `Заявка № ${settings.request.id}. Только сохранённые действия, время московское.`;
            body = log?.error ? `<div class="vq-notice error" role="alert">${esc(log.error)}</div>${btn('Повторить загрузку', 'events-retry')}` :
                !log || log.loading ? '<p role="status">Загрузка истории…</p>' :
                !log.results.length ? '<p class="vq-caption">Сохранённых операций пока нет.</p>' :
                `<ol class="vq-event-list">${log.results.map(event => `<li data-event="${event.id}"><strong>${esc(eventLabels[event.event_type] || event.event_type)}</strong><div class="secondary"><time datetime="${esc(event.occurred_at)}">${esc(eventTime.format(new Date(event.occurred_at)))}</time> · ${esc(event.actor_username)}</div><p class="vq-caption">${esc(eventDescription(event))}</p></li>`).join('')}</ol>`;
            const ready = log && !log.loading && !log.error;
            footer = `<span class="note" role="status">${ready ? `Показано ${log.results.length ? log.offset + 1 : 0}–${log.offset + log.results.length} из ${log.count}` : ''}</span><div class="vq-actions">${btn('Назад', 'events-prev', 'small', !ready || !log.offset ? 'disabled' : '')}${btn('Далее', 'events-next', 'small', !ready || !log.has_more ? 'disabled' : '')}${btn('Закрыть', 'cancel', 'primary')}</div>`;
        }
        else if (m === 'create') {
            size = 'sm';
            title = 'Новая заявка';
            subtitle = 'Выберите организацию для новой заявки.';
            body = `<label class="vq-field-label" for="newOrg">Организация</label><select class="vq-select vq-select-wide" id="newOrg">${settings.meta.organizations.map(o => `<option value="${esc(o.value)}" ${o.value === settings.meta.organizations[0].value ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}</select><p class="vq-caption">Дата и время создания фиксируются при создании черновика.</p>`;
            footer = actions(btn('Отмена', 'cancel'), btn('Создать черновик', 'create-now', 'primary', settings.meta.organizations.length ? '' : 'disabled'));
            if (!settings.meta.organizations.length) body += '<p class="vq-caption">Нет организаций, для которых разрешены заявки. Обратитесь к администратору.</p>';
        }
        else if (m === 'add') {
            title = 'Добавить сотрудников';
            subtitle = 'Доступны сотрудники организации заявки из последней принятой выгрузки 1С.';
            size = settings.tab === 'single' ? 'vq-employee-picker vq-single-picker' : 'vq-employee-picker';
            tabs = `<div class="vq-modal-tabs" role="group" aria-label="Способ подбора"><button type="button" data-tab="bulk" aria-pressed="${settings.tab === 'bulk'}">Массовый поиск</button><button type="button" data-tab="single" aria-pressed="${settings.tab === 'single'}">Поиск и выбор</button></div>`;
            if (settings.tab === 'bulk') {
                const rows = settings.bulkChecked ? settings.resolved : [];
                const found = rows.filter(r => r.status === 'matched');
                const attention = rows.filter(r => ['ambiguous', 'not_found', 'other_organization', 'outdated', 'import_unavailable'].includes(r.status));
                const others = rows.filter(r => r.status === 'duplicate' || r.status === 'included');
                const resultIndices = new Map(rows.map((r, i) => [r, i]));
                const renderGroup = (label, part) => part.length ? `<div class="vq-result-header">${label}<span>${part.length}</span></div><div class="vq-result-list">${part.map(r => { const i = resultIndices.get(r); const candidate = r.candidates?.[0]; return `<div class="vq-result ${r.status === 'ambiguous' ? 'vq-resolved' : ''}"><div class="who"><strong>${esc(candidate?.full_name || r.input)}</strong><small>${esc(candidate?.personnel_number || '')}</small>${r.status === 'ambiguous' ? `<select class="vq-select vq-result-select" data-resolve="${i}" aria-label="Уточнить сотрудника по записи «${esc(r.input)}»"><option value="">Выберите сотрудника по табельному номеру</option>${(r.candidates || []).map(c => `<option value="${c.id}" ${r.chosen === c.id ? 'selected' : ''}>${esc(c.full_name)} · ${esc(c.personnel_number)} · ${esc(c.department_name || '')}</option>`).join('')}</select>` : ''}</div><span class="result-tag ${r.status === 'matched' ? 'good' : 'warn'}">${esc(({ matched: 'Найден', duplicate: 'Дубликат ввода', included: 'Уже в заявке', ambiguous: 'Неоднозначно', not_found: 'Не найден', other_organization: 'Другая организация', outdated: 'Нет в актуальном справочнике', import_unavailable: 'Нет принятой выгрузки 1С' })[r.status] || r.status)}</span></div>`; }).join('')}</div>` : '';
                const resultCounts = settings.bulkChecked ?
                    `<span data-bulk-result>Найдено <b>${found.length}</b></span>` +
                    `<span data-bulk-result>Требуют внимания <b>${attention.length}</b></span>` +
                    `<span data-bulk-result>Пропущено <b>${others.length}</b></span>` : '';
                const resultRows = settings.bulkChecked ? `<div class="vq-result-scroll" data-bulk-result>
                    ${renderGroup('Готовы к добавлению', found)}${renderGroup('Требуют внимания', attention)}
                    ${renderGroup('Уже включены и дубликаты', others)}</div>` : '';
                body = rosterHint() +
                    `<label class="vq-field-label" for="bulkText">ФИО или табельный номер — по одной записи в строке</label>` +
                    `<textarea class="vq-textarea monospace" id="bulkText" placeholder="Вставьте список из любого источника">${esc(settings.bulkText)}</textarea>` +
                    `<p id="bulkReadProgress" class="vq-caption" role="status"></p>` +
                    `<div class="vq-counts"><span>Строк <b id="bulkLineCount">${ctx.bulkLines().length}</b></span>${resultCounts}</div>${resultRows}`;
                const ready = ctx.bulkCandidates().length;
                const bulkActions = settings.bulkChecked
                    ? btn('Проверить повторно', 'check-bulk', 'primary') +
                        btn(`Добавить найденных (${ready})`, 'apply-bulk', 'primary', `data-bulk-result ${!ready ? 'disabled' : ''}`)
                    : btn('Проверить список', 'check-bulk', 'primary', !settings.bulkText.trim() ? 'disabled' : '');
                footer = `<span class="note" id="bulkReadyNote">${settings.bulkChecked ? `Готовы к добавлению: ${ready}` : 'Введите список и проверьте совпадения'}</span>` +
                    `<div class="vq-actions">${btn('Отмена', 'cancel')}${bulkActions}</div>`;
            }
            else {
                body = rosterHint() + `<div class="vq-picker-search"><label class="vq-field-label" for="oneSearch">Найти сотрудников</label><div class="vq-field vq-picker-input">${icon('search')}<input id="oneSearch" type="search" data-input="one-search" placeholder="Фамилия, ФИО или табельный номер" autocomplete="off" value="${esc(settings.singleQuery)}"></div><p class="vq-picker-help">От 2 символов. Отмечайте нескольких сотрудников, затем нажмите «Добавить».</p></div><div class="vq-picker-selected" id="singleSelected" hidden></div><div class="vq-picker-summary" id="singleMatchCount" aria-live="polite"></div><div class="vq-result-scroll vq-picker-results" id="singleResults"></div>`;
                footer = `<span class="note" id="singlePickedNote">Выбрано ${settings.picked.size}</span><div class="vq-actions">${btn('Отмена', 'cancel')}${btn(`Добавить (${settings.picked.size})`, 'apply-one', 'primary', !settings.picked.size ? 'disabled' : '')}</div>`;
            }
        }
        else if (m === 'batch') {
            title = settings.choiceMode === 'add' ? 'Назначить позиции' : 'Убрать позиции';
            subtitle = `Выбрано: ${settings.selected.size} ${plural(settings.selected.size, ['сотрудник', 'сотрудника', 'сотрудников'])}. За пределами показанной страницы: ${ctx.selectedOutsidePage()}. ${settings.choiceMode === 'add' ? 'Выбранные позиции будут добавлены к текущим назначениям.' : 'Будут сняты только выбранные позиции.'}`;
            body = `<p id="batchReadProgress" class="vq-caption" role="status"></p>` + `<div class="vq-select-choices">${byItem().filter(it => settings.choiceMode !== 'add' || it.is_active).map(it => `<label class="vq-choice"><input type="checkbox" data-choice="${it.id}" ${settings.choice.has(it.id) ? 'checked' : ''}><span><strong>${esc(it.name)}</strong><small>Срок действия: ${it.validity_days} ${plural(it.validity_days, ['день', 'дня', 'дней'])}</small></span></label>`).join('')}</div>`;
            footer = `<span class="note" id="batchChoiceCount">Выбрано позиций: ${settings.choice.size}</span><div class="vq-actions">${btn('Отмена', 'cancel')}${btn(`${settings.choiceMode === 'add' ? 'Добавить' : 'Убрать'} (${settings.choice.size})`, 'apply-batch', 'primary', !settings.choice.size ? 'disabled' : '')}</div>`;
        }
        else if (m === 'finish') {
            size = 'sm';
            title = 'Завершить заявку';
            subtitle = 'После завершения заявку можно будет только просматривать.';
            body = `<div class="vq-counts"><span>Сотрудники: <b>${currentMembers().length}</b></span><span>Назначения: <b>${assignedCount()}</b></span><span>Повторы: <b>${warningsCount()}</b></span><span>Без позиций: <b>${emptyCount()}</b></span></div>${emptyCount() ? `<div class="vq-notice"><b>Автоматическое исключение</b>Сотрудники без позиций будут исключены: ${emptyCount()}. Останется сотрудников: ${currentMembers().length - emptyCount()}.</div>` : ''}${!assignedCount() ? '<div class="vq-notice error"><b>Нет назначений</b>Завершение невозможно.</div>' : ''}${warningsCount() ? '<div class="vq-notice"><b>Повторные назначения</b>Повторы не мешают завершить заявку.</div>' : ''}${settings.repeatState !== 'verified' ? '<div class="vq-notice error"><b>Проверка повторов не завершена</b>Результат проверки недоступен, поэтому отсутствие повторов не подтверждено.</div>' : ''}<label class="vq-confirm"><input type="checkbox" id="finishAck" ${settings.ack ? 'checked' : ''}><span>Подтверждаю завершение заявки с указанным составом и назначениями.</span></label>`;
            footer = actions(btn('Продолжить работу', 'cancel'), emptyCount() ? btn('Показать без позиций', 'show-empty', 'small') : '', btn('Подтвердить завершение', 'apply-finish', 'primary', !settings.ack || !assignedCount() ? 'disabled' : ''));
        }
        else if (m === 'reopen') {
            size = 'sm';
            title = 'Вернуть заявку в работу?';
            subtitle = `Заявка № ${settings.request.id} станет черновиком. Дата завершения останется в истории.`;
            body = `<div class="vq-notice"><b>Важно</b>После возврата заявку снова можно редактировать.</div><label class="vq-field-label" for="reopenReason">Причина возврата (необязательно, до 500 символов)</label><textarea id="reopenReason" class="vq-textarea" maxlength="500" rows="3" placeholder="Например: необходимо скорректировать состав">${esc(settings.reopenReason)}</textarea><label class="vq-confirm"><input type="checkbox" id="reopenAck" ${settings.ack ? 'checked' : ''}><span>Подтверждаю возврат завершённой заявки в работу.</span></label>`;
            footer = actions(btn('Отмена', 'cancel'), btn('Вернуть в работу', 'apply-reopen', 'primary', !settings.ack ? 'disabled' : ''));
        }
        else if (m === 'remove' || m === 'remove-one') {
            const ids = m === 'remove-one' ? [settings.drawer] : newSelection();
            size = 'sm';
            title = ids.length === 1 ? 'Исключить сотрудника?' : 'Исключить сотрудников?';
            const count = ids.length;
            const members = new Map(currentMembers().map(row => [row.employee_id, row]));
            subtitle = 'Будут удалены записи только из текущего черновика; завершённые заявки не изменятся.';
            body = `<div class="vq-notice"><b>Будет исключено: ${count}</b>Назначенные позиции удаляются. Если добавить сотрудника снова, позиции не восстановятся автоматически.</div><div class="vq-result-scroll"><div class="vq-result-list">${ids.map(id => { const m = members.get(id); return m ? `<div class="vq-result"><div class="who"><strong>${esc(m.full_name)}</strong><small>${esc(m.personnel_number)}</small></div></div>` : ''; }).join('')}</div></div>`;
            footer = actions(btn('Отмена', 'cancel'), btn('Исключить (' + count + ')', 'apply-remove', 'danger'));
        }
        else if (m === 'leave') {
            size = 'sm';
            title = 'Несохранённые изменения';
            subtitle = 'Изменения в составе заявки ещё не сохранены.';
            body = '<p class="vq-caption">Выберите: сохранить изменения, выйти без сохранения или продолжить редактирование.</p>';
            footer = actions(btn('Продолжить', 'cancel'), btn('Не сохранять', 'discard'), btn('Сохранить и выйти', 'save-leave', 'primary'));
        }
        else if (m === 'delete-draft') {
            size = 'sm';
            title = 'Удалить черновик?';
            subtitle = `Черновик ${settings.request.id} будет удалён вместе с назначениями. Завершённые заявки не затрагиваются.`;
            body = `<div class="vq-notice error"><b>Удаление черновика</b>${currentMembers().length} ${plural(currentMembers().length, ['сотрудник', 'сотрудника', 'сотрудников'])} и все их позиции исчезнут из этой заявки. Это действие нельзя отменить.</div>`;
            footer = actions(btn('Отмена', 'cancel'), btn('Удалить черновик', 'confirm-delete', 'danger'));
        }
        else if (m === 'conflict') {
            size = 'sm';
            title = 'Конфликт изменений';
            subtitle = 'На сервере появилась более новая версия. Автоматическое объединение не выполняется.';
            const local = settings.request, remote = settings.remote;
            const personDiff = [], itemDiff = [];
            if (remote) {
                const lm = new Map(local.members.map(x => [x.employee_id, x]));
                const rm = new Map(remote.members.map(x => [x.employee_id, x]));
                for (const [id, m] of lm)
                    if (!rm.has(id))
                        personDiff.push(`Только локально: ${m.full_name} · ${m.personnel_number}`);
                for (const [id, m] of rm)
                    if (!lm.has(id))
                        personDiff.push(`Только на сервере: ${m.full_name} · ${m.personnel_number}`);
                for (const [id, m] of lm)
                    if (rm.has(id)) {
                        const mine = new Set(assignments(m)), other = new Set(assignments(rm.get(id)));
                        const added = [...mine].filter(i => !other.has(i));
                        const missing = [...other].filter(i => !mine.has(i));
                        if (added.length || missing.length)
                            itemDiff.push(`${m.full_name}: ${[...added.map(x => 'локально + ' + itemNames([x])[0]), ...missing.map(x => 'на сервере + ' + itemNames([x])[0])].join('; ')}`);
                    }
            }
            const list = (arr, empty) => arr.length ? `<ul>${arr.slice(0, 15).map(x => `<li>${esc(x)}</li>`).join('')}${arr.length > 15 ? `<li>И ещё ${arr.length - 15}</li>` : ''}</ul>` : `<p class="vq-caption">${empty}</p>`;
            body = `<div class="vq-notice"><b>Локальные изменения не записаны</b>Загрузка серверной версии заменит их без автоматического слияния.</div>${remote ? `<div class="vq-conflict-section"><b>Состав сотрудников</b>${list(personDiff, 'Состав одинаковый')}</div><div class="vq-conflict-section"><b>Назначенные позиции</b>${list(itemDiff, 'Расхождений не найдено')}</div>` : '<p class="vq-caption">Сравнение с серверной версией загружается…</p>'}<label class="vq-confirm"><input type="checkbox" id="reloadAck" ${settings.reloadAck ? 'checked' : ''}><span>Понимаю, что несохранённые изменения будут потеряны.</span></label>`;
            footer = actions(btn('Вернуться', 'cancel'), btn('Загрузить серверную версию', 'reload', 'primary', !settings.reloadAck ? 'disabled' : ''));
        }
        return `<div class="vq-scrim" data-backdrop="1"><div class="vq-modal ${size}" tabindex="-1" role="dialog" aria-modal="true" aria-labelledby="modalTitle" aria-describedby="modalDescription"><div class="vq-modal-head"><h2 id="modalTitle">${title}</h2><p id="modalDescription">${subtitle}</p></div>${tabs}<div class="vq-modal-body">${body}</div><div class="vq-modal-footer">${footer}</div></div></div>`;
    }
    async function loadHistory(employeeId) {
        if (!can('view_workwearevent')) return [];
        const requestId = settings.request.id, key = ctx.historyKey(employeeId), epoch = settings.historyEpoch;
        if (settings.history.has(key))
            return settings.history.get(key);
        const data = await api(`ui/requests/${requestId}/employees/${employeeId}/history/`);
        if (settings.request?.id !== requestId || epoch !== settings.historyEpoch || key !== ctx.historyKey(employeeId)) return [];
        settings.history.set(key, data.results);
        while (settings.history.size > 64) settings.history.delete(settings.history.keys().next().value);
        return data.results;
    }
    function drawerMarkup() {
        if (settings.drawer === null)
            return '';
        const member = currentMembers().find(m => m.employee_id === settings.drawer);
        if (!member)
            return '';
        if (settings.archive) {
            const record = settings.archive, row = record.items;
            return `<div class="vq-drawer-back" data-drawerback="1"><div class="vq-drawer" role="dialog" tabindex="-1" aria-modal="true" aria-label="Просмотр предыдущей заявки"><div class="vq-drawer-head"><div><button type="button" class="vq-name-action vq-preview-back" data-action="history-back">${icon('back')} Назад к сотруднику</button><div class="label vq-preview-label">Завершённая заявка · Только просмотр</div><h2>Заявка № ${record.request_id}</h2><div class="meta">${fmt(record.completed_at)} · ${esc(record.snapshot_organization_name)}</div></div>${btn(icon('x'), 'close-drawer', 'small iconbtn', 'aria-label="Закрыть просмотр"')}</div><div class="vq-drawer-body"><div class="vq-inline-hint">Просмотр не изменяет состав текущего черновика.</div><div class="vq-sectionline">Сотрудник в предыдущей заявке</div><div class="vq-preview-person"><strong>${esc(record.full_name)}</strong><div class="secondary">${esc(record.personnel_number)} · ${esc(record.department_name || '')}</div></div><div class="vq-divider"></div><div class="vq-sectionline"><span>Назначенные позиции</span><span>${row.length}</span></div>${row.map(it => `<div class="vq-item-row"><div class="center"><strong>${esc(it.name)}</strong><div class="detail">Срок действия: ${it.validity_days} ${plural(it.validity_days, ['день', 'дня', 'дней'])}</div></div></div>`).join('')}<div class="vq-divider"></div><div class="vq-sectionline">Сведения о заявке</div><div class="vq-preview-facts"><span>Завершена</span><strong>${fmt(record.completed_at)}</strong><span>Организация</span><strong>${esc(record.snapshot_organization_name)}</strong></div></div><div class="vq-drawer-foot"><a href="?request=${record.request_id}" target="_blank" rel="noopener noreferrer" class="vq-btn primary vq-archive-link">Полная заявка · новая вкладка ${icon('chev')}</a>${btn('Назад', 'history-back', 'small')}</div></div></div>`;
        }
        const historic = settings.history.get(ctx.historyKey(member.employee_id)) || [];
        let rows = '';
        for (const [category, items] of groups()) {
            rows += `<div class="vq-item-group">${esc(category)}</div>`;
            for (const catalogItem of items) {
                const it = ctx.assignmentItem(member, catalogItem);
                const on = member.assignments.some(a => a.item_id === it.id), w = warning(member.employee_id, it.id);
                const prior = historic.filter(h => h.items.some(x => x.id === it.id));
                rows += `<div class="vq-item-row"><input type="checkbox" id="drawer-item-${it.id}" data-item="${it.id}" ${on ? 'checked' : ''} ${editable() && (it.is_active || on) && !settings.saving && !settings.pendingAssignments.has(ctx.assignmentKey(member.employee_id, it.id)) ? '' : 'disabled'}><div class="center"><label class="label" for="drawer-item-${it.id}">${esc(it.name)}</label><div class="detail ${w ? 'warning' : ''}">${w ? `Повтор · осталось ${w.remaining_days} дн.` : esc(it.category || '') + ' · ' + it.validity_days + ' ' + plural(it.validity_days, ['день', 'дня', 'дней'])}</div></div>${can('view_workwearevent') ? `<button class="vq-history-action ${w ? 'warning' : ''}" type="button" data-history="${it.id}" aria-expanded="${settings.historyShown === it.id}" aria-label="История назначений: ${esc(it.name)}" title="История назначений: ${esc(it.name)}">${icon('clock')} <span>${prior.length ? prior.length + ' зап.' : ''}</span></button>` : ''}</div>`;
                if (settings.historyShown === it.id)
                    rows += `<div class="vq-item-history" role="region" aria-label="История выбранной позиции"><div class="vq-history-navigation">${btn(icon('back') + ' Назад к позициям', 'close-history', 'small')}</div><b>История · ${esc(it.name)}</b>${prior.length ? prior.map(rec => `<div class="prior-row ${w ? 'within-period' : ''}"><button type="button" class="vq-name-action" data-prior="${rec.request_id}">Заявка № ${rec.request_id} ${icon('chev')}</button><div>Завершена: ${fmt(rec.completed_at)}</div><div>Срок действия: ${rec.items.find(item => item.id === it.id).validity_days} ${plural(rec.items.find(item => item.id === it.id).validity_days, ['день', 'дня', 'дней'])}</div></div>`).join('') : '<p class="vq-history-empty">Завершённых заявок по этой позиции не найдено.</p>'}</div>`;
            }
        }
        return `<div class="vq-drawer-back" data-drawerback="1"><div class="vq-drawer ${settings.historyShown !== null ? 'vq-history-mode' : ''}" role="dialog" tabindex="-1" aria-modal="true" aria-label="Карточка сотрудника"><div class="vq-drawer-head"><div><div class="label">Сотрудник · ${esc(member.personnel_number)}</div><h2>${esc(member.full_name)}</h2><div class="meta">${esc(member.department_name || '')}</div></div>${btn(icon('x'), 'close-drawer', 'small iconbtn', 'aria-label="Закрыть карточку"')}</div><div class="vq-drawer-body"><div class="vq-sectionline"><span>${can('view_workwearevent') ? 'Позиции · история и контроль повторов' : 'Назначенные позиции'}</span><span data-drawer-assigned>${byItem().length ? `Назначено позиций: ${member.assignments.length} из ${byItem().length}` : `Назначений: ${member.assignments.length}`}</span></div>${byItem().length ? rows : `<p class="vq-inline-hint">${can('view_workwearitem') ? 'Нет доступных позиций. Обратитесь к администратору для заполнения каталога.' : 'Для просмотра каталога необходимо соответствующее разрешение.'}</p>`}</div><div class="vq-drawer-foot">${editable() ? btn('Исключить из черновика', 'remove-one', 'small danger') : '<span class="secondary">Только просмотр</span>'}<div class="vq-drawer-actions"><div class="vq-drawer-save">${dirty() ? 'Есть изменения. Сохраните заявку после закрытия карточки.' : 'Изменения сохранены'}</div>${btn('Закрыть', 'close-drawer', 'primary')}</div></div></div></div>`;
    }
    function renderOverlay() {
        const had = overlays.firstElementChild;
        const oldFocus = overlays.contains(document.activeElement) ? document.activeElement : null;
        if (!had && (settings.modal || settings.drawer !== null)) {
            const element = document.activeElement;
            settings.overlayReturn = { element, selector: ctx.controlSelector(element) };
        }
        const scroll = overlays.querySelector('.vq-drawer-body,.vq-modal-body')?.scrollTop || 0;
        const key = ctx.controlSelector(oldFocus);
        overlays.innerHTML = settings.modal ? modalMarkup() : settings.drawer !== null ? drawerMarkup() : '';
        syncModalInert(root, overlays);
        const area = overlays.querySelector('.vq-drawer-body,.vq-modal-body');
        if (area && had)
            area.scrollTop = scroll;
        if (settings.modal === 'add' && settings.tab === 'single')
            patchSearchResults();
        const target = overlays.querySelector('.vq-modal,.vq-drawer');
        if (target) {
            const restore = key && overlays.querySelector(key);
            if (restore?.getClientRects().length && !restore.disabled)
                restore.focus({ preventScroll: true });
            else
                target.focus({ preventScroll: true });
        }
        else if (had && settings.overlayReturn) {
            const { element, selector } = settings.overlayReturn;
            const restore = element.isConnected ? element : selector && root.querySelector(selector);
            if (restore?.getClientRects().length && !restore.disabled)
                restore.focus({ preventScroll: true });
            else
                ctx.main.querySelector('h1')?.focus({ preventScroll: true });
            settings.overlayReturn = null;
        }
        if (settings.busy) ctx.setBusy(true);
    }
    async function openDrawer(employeeId) {
        const requestId = settings.request?.id, epoch = settings.historyEpoch;
        settings.drawer = employeeId;
        settings.historyShown = null;
        settings.archive = null;
        renderOverlay();
        if (can('view_workwearevent')) {
            try {
                await loadHistory(employeeId);
                if (settings.drawer === employeeId && settings.request?.id === requestId && epoch === settings.historyEpoch)
                    renderOverlay();
            }
            catch (err) {
                notice('История не загружена: ' + err.message, true);
            }
        }
    }
    Object.assign(ctx, {
        showOverlay, cancelDialog, renderOverlay, openDrawer, loadEvents,
    });
}
