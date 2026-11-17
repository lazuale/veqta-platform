/* Ограниченные read-only пакеты Workwear, без внешнего scheduler/state manager.
 * Пакетами и отменой владеет вызов readChunks; общий бюджет ctx — два Fetch.
 * Данные полного черновика не разбиваются при записи: sync остаётся атомарным.
 * Контекст и AbortSignal задаёт вызывающий модуль. При первой ошибке новые
 * пакеты не запускаются, выполняющиеся отменяются, все Promise дожидаются
 * завершения. Частичный результат никогда не выдаётся за полный.
 */

/** Разделить JSON-массив с точным бюджетом UTF-8 тела и количеством элементов.
 * buildData([]) содержит остальные неизменные поля; вход — JSON-совместимые
 * строки/объекты без toJSON. Подсчёт линейный, без повторной сериализации префиксов.
 */
export function partitionRequests(rows, buildData, maxItems, maxBytes) {
    if (!Number.isInteger(maxItems) || maxItems < 1 || !Number.isInteger(maxBytes) || maxBytes < 1)
        throw new Error('Неверные ограничения пакета');
    const encoder = new TextEncoder(), base = encoder.encode(JSON.stringify(buildData([]))).length;
    const batches = []; let batch = [], bytes = base;
    for (const row of rows) {
        const serialized = JSON.stringify(row);
        if (serialized === undefined) throw new Error('Некорректные данные пакета');
        const size = encoder.encode(serialized).length;
        if (base + size > maxBytes) throw new Error('Одна запись превышает безопасный размер запроса');
        if (batch.length && (batch.length >= maxItems || bytes + 1 + size > maxBytes)) {
            batches.push(batch); batch = []; bytes = base;
        }
        bytes += size + (batch.length ? 1 : 0); batch.push(row);
    }
    if (batch.length) batches.push(batch);
    return batches;
}

/** Подключить readChunks/repeatPairs к одному экземпляру Workwear ctx. */
export function installBatching(ctx) {
    const aborted = () => Object.assign(new Error('Проверка отменена'), { name: 'AbortError' });
    // Общий бюджет двух пакетных запросов между всеми read jobs одного ctx.
    // Ожидающий permit снимается по AbortSignal, не занимая сетевой слот.
    let active = 0;
    const waiting = new Set();
    function takePermit(signal) {
        return new Promise((resolve, reject) => {
            const entry = {
                run() {
                    waiting.delete(entry); signal.removeEventListener('abort', cancel);
                    if (signal.aborted) { reject(aborted()); return; }
                    active++;
                    resolve(() => {
                        active--;
                        while (active < 2 && waiting.size) waiting.values().next().value.run();
                    });
                }
            };
            const cancel = () => { waiting.delete(entry); reject(aborted()); };
            if (signal.aborted) { reject(aborted()); return; }
            if (active < 2) entry.run();
            else { waiting.add(entry); signal.addEventListener('abort', cancel, { once: true }); }
        });
    }
    const limits = () => ctx.settings.meta.limits;
    async function readChunks(path, rows, buildData, { maxItems, signal, valid = () => true, progress = () => {} } = {}) {
        if (path !== 'ui/resolve/' && !/^ui\/requests\/\d+\/repeat-scan\/$/.test(path))
            throw new Error('Пакетная обработка разрешена только для чтения');
        const batches = partitionRequests(rows, buildData, maxItems, limits().json_bytes);
        const controller = new AbortController(), results = new Array(batches.length);
        const cancel = () => controller.abort();
        signal?.addEventListener('abort', cancel, { once: true });
        let next = 0, done = 0, failure = null;
        if (signal?.aborted) controller.abort();
        progress({ done, total: rows.length });
        async function worker() {
            while (next < batches.length && !failure) {
                const index = next++;
                try {
                    if (controller.signal.aborted || !valid()) throw aborted();
                    const release = await takePermit(controller.signal);
                    let result;
                    try {
                        if (controller.signal.aborted || !valid()) throw aborted();
                        result = await ctx.api(path, { method: 'POST', data: buildData(batches[index]), signal: controller.signal });
                    }
                    finally { release(); }
                    if (controller.signal.aborted || !valid()) throw aborted();
                    results[index] = result; done += batches[index].length;
                    progress({ done, total: rows.length });
                } catch (err) { failure ??= err; controller.abort(); }
            }
        }
        try {
            await Promise.all(Array.from({ length: Math.min(2, batches.length) }, worker));
            if (failure) throw failure;
            return results;
        } finally { signal?.removeEventListener('abort', cancel); }
    }
    /** Проверить до1000×30 уникальных пар; server batch/64KiB предел не увеличивается.
     * Результат применим только после всех ответов одной календарной даты.
     * Автоматических retry нет; повторный запуск чтения безопасен по действию пользователя.
     */
    async function repeatPairs(pairs, options = {}) {
        const cap = limits();
        if (pairs.length > cap.request_members * cap.items_per_member ||
                new Set(pairs.map(p => `${p.employee_id}:${p.item_id}`)).size !== pairs.length)
            throw new Error('Некорректный набор пар проверки повторов');
        const id = ctx.settings.request.id;
        const responses = await readChunks(`ui/requests/${id}/repeat-scan/`, pairs, rows => ({ pairs: rows }),
            { ...options, maxItems: cap.repeat_pairs });
        if (responses.some(r => !Array.isArray(r.warnings) || !/^\d{4}-\d{2}-\d{2}$/.test(r.as_of_date || '')))
            throw new Error('Неполный ответ проверки повторов');
        const dates = new Set(responses.map(r => r.as_of_date));
        if (dates.size > 1) throw new Error('Во время проверки изменилась календарная дата. Повторите проверку.');
        return { warnings: responses.flatMap(r => r.warnings), checked_pairs: pairs.length, as_of_date: [...dates][0] ?? null };
    }
    Object.assign(ctx, { readChunks, repeatPairs, limits });
}
