/* Общие значки и поведение оболочки. Данные заявок здесь не обрабатываются. */
const ICONS = {
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    back: '<path d="m15 18-6-6 6-6"/>',
    chev: '<path d="m9 18 6-6-6-6"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    alert: '<path d="m10.3 3.9-8.2 14a2 2 0 0 0 1.7 3h16.4a2 2 0 0 0 1.7-3l-8.2-14a2 2 0 0 0-3.4 0z"/><path d="M12 9v4m0 4h.01"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    people: '<path d="M16 20v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 10a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM22 20v-2a4 4 0 0 0-3-3.87M16 2.13a4 4 0 0 1 0 7.75"/>',
    shirt: '<path d="m8 3-6 4 3 5 3-2v11h8V10l3 2 3-5-6-4a4 4 0 0 1-8 0Z"/>',
    'chevron-down': '<path d="m6 9 6 6 6-6"/>',
    check: '<path d="m5 12 4 4L19 6"/>'
};
export function iconSvg(name) {
    if (!Object.hasOwn(ICONS, name)) return '';
    return `<svg xmlns="http://www.w3.org/2000/svg" class="vq-icon" width="16" height="16" viewBox="0 0 24 24" stroke="currentColor" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${ICONS[name]}</svg>`;
}
export function installIconTokens(root=document) {
    const element = root.documentElement;
    if (!element) return;
    const image = svg => `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;
    for (const name of ['chevron-down', 'check', 'alert']) {
        element.style.setProperty(`--vq-icon-${name}`, image(iconSvg(name)));
    }
    // У нативного select нет отдельного элемента для маски стрелки.
    const color = root.defaultView.getComputedStyle(element).getPropertyValue('--vq-text-muted').trim();
    element.style.setProperty('--vq-select-chevron', image(iconSvg('chevron-down').replace('currentColor', color)));
}
export function decorateIcons(root=document) {
    for (const el of root.querySelectorAll('[data-vq-icon]')) {
        const svg = iconSvg(el.dataset.vqIcon);
        if (svg) el.innerHTML = svg;
    }
}
/** Снимаем только inert, установленный этим модулем. */
export function syncModalInert(root, overlay) {
    const active=!!overlay?.querySelector('[role="dialog"],[role="alertdialog"]');
    const skip = root.ownerDocument.querySelector('.vq-skip');
    for (const child of [skip, ...root.children]) {
        if (!child || child===overlay || child.hasAttribute('aria-live')) continue;
        if (active && !child.inert) {child.inert=true;child.dataset.vqModalInert='1';}
        else if (!active && child.dataset.vqModalInert) {child.inert=false;delete child.dataset.vqModalInert;}
    }
}
/* Управление фокусом не зависит от состояния заявок. */
/** Не выпускаем фокус за пределы открытого диалога. */
export function cycleFocus(event, container) {
    if (event.key !== 'Tab' || !container) return false;
    const focusable = [...container.querySelectorAll('a[href],button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]')]
        .filter(el => el.getClientRects().length && !el.closest('[hidden],[inert]'));
    if (!focusable.length) { event.preventDefault(); container.focus(); return true; }
    const first=focusable[0],last=focusable[focusable.length-1],active=container.ownerDocument.activeElement;
    if (event.shiftKey && (active===first || active===container)) {event.preventDefault();last.focus();return true;}
    if (!event.shiftKey && (active===last || !container.contains(active))) {event.preventDefault();first.focus();return true;}
    return false;
}
export function enhanceShell(root=document) {
    if (root.documentElement?.dataset.vqShellReady) return;
    if (root.documentElement) root.documentElement.dataset.vqShellReady='1';
    const menus=()=>[...root.querySelectorAll('details.vq-menu')];
    root.addEventListener('click',event=>{
        for (const menu of menus()) if (!menu.contains(event.target)) menu.open=false;
    });
    root.addEventListener('toggle',event=>{
        if (event.target.matches?.('details.vq-menu') && event.target.open)
            for (const other of menus()) if (other!==event.target) other.open=false;
    },true);
    root.addEventListener('keydown',event=>{
        if (event.key!=='Escape') return;
        const opened=menus().find(el=>el.open);
        if (opened) {event.preventDefault();opened.open=false;opened.querySelector('summary')?.focus();}
    });
}
if (typeof document!=='undefined') {
    const initialize=()=>{installIconTokens();enhanceShell();decorateIcons();};
    if (document.readyState==='loading') document.addEventListener('DOMContentLoaded',initialize);
    else initialize();
}
