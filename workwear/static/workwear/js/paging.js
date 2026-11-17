/* Ограниченное представление полного локального черновика: до100 строк и30 колонок.
 * Native pagination выбрана вместо virtual scroll: переменные строки/закреплённые
 * колонки/клавиатура не требуют геометрического cache и наблюдателей прокрутки.
 * Фильтрация и selected относятся ко всему request, а checkbox заголовка — только
 * к показанной странице. При небольшом составе дополнительные controls не показываются.
 * Это бюджет DOM, а не ограничение серверного состава или размера каталога.
 */
export function installPaging(ctx) {
    const { settings } = ctx;
    const pageRowSize = 100, pageColumnSize = 30;
    /** Вернуть окно глобально отфильтрованного состава; локальные данные не удаляются. */
    function memberPage() {
        const all = ctx.memberVisible(), pages = Math.max(1, Math.ceil(all.length / pageRowSize));
        settings.memberPageIndex = Math.max(0, Math.min(settings.memberPageIndex || 0, pages - 1));
        const offset = settings.memberPageIndex * pageRowSize;
        return { rows: all.slice(offset, offset + pageRowSize), offset, total: all.length, pages };
    }
    /** Страница колонок независима от максимума назначений на одного человека. */
    function matrixItems() {
        const items = ctx.byItem(), pages = Math.max(1, Math.ceil(items.length / pageColumnSize));
        settings.columnPageIndex = Math.max(0, Math.min(settings.columnPageIndex || 0, pages - 1));
        return items.slice(settings.columnPageIndex * pageColumnSize, (settings.columnPageIndex + 1) * pageColumnSize);
    }
    function selectedOutsidePage() {
        const shown = new Set(memberPage().rows.map(m => m.employee_id));
        return [...settings.selected].filter(id => !shown.has(id)).length;
    }
    function pageFooter() {
        const page = memberPage();
        return page.pages > 1
            ? `Показано ${page.offset + 1}–${page.offset + page.rows.length} из ${page.total} найденных · Всего ${ctx.currentMembers().length}`
            : `Показано ${page.rows.length} из ${ctx.currentMembers().length}`;
    }
    /** Навигация не меняет выбранных/назначения; глобальный выбор всегда явный. */
    function pagerMarkup() {
        const page = memberPage(), columns = ctx.byItem();
        const rowNav = page.pages > 1 ? `<nav class="vq-actions" aria-label="Страницы сотрудников">${ctx.btn('Назад', 'members-prev', 'small', !settings.memberPageIndex ? 'disabled' : '')}<span aria-live="polite">Страница ${settings.memberPageIndex + 1} из ${page.pages}</span>${ctx.btn('Далее', 'members-next', 'small', settings.memberPageIndex + 1 >= page.pages ? 'disabled' : '')}${ctx.editable() ? ctx.btn(`Выбрать всех найденных (${page.total})`, 'select-filtered', 'small subtle') : ''}</nav>` : '';
        const colNav = settings.tableMode === 'matrix' && columns.length > pageColumnSize ? `<nav class="vq-actions" aria-label="Страницы позиций">${ctx.btn('Предыдущие позиции', 'columns-prev', 'small', !settings.columnPageIndex ? 'disabled' : '')}<span>Позиции ${settings.columnPageIndex * pageColumnSize + 1}–${Math.min(columns.length, (settings.columnPageIndex + 1) * pageColumnSize)} из ${columns.length}</span>${ctx.btn('Следующие позиции', 'columns-next', 'small', (settings.columnPageIndex + 1) * pageColumnSize >= columns.length ? 'disabled' : '')}</nav>` : '';
        return rowNav + colNav;
    }
    function updatePager() {
        const target = ctx.$('#editorPager');
        if (!target) return;
        const html = pagerMarkup();
        target.hidden = !html;
        if (target.innerHTML !== html) target.innerHTML = html;
    }
    Object.assign(ctx, { memberPage, matrixItems, selectedOutsidePage, pageFooter, pagerMarkup, updatePager, pageRowSize, pageColumnSize });
}
