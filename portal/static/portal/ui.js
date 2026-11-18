/* Общие значки и поведение оболочки. Данные заявок здесь не обрабатываются. */
const ICONS = { search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>', plus: '<path d="M12 5v14M5 12h14"/>', back: '<path d="m15 18-6-6 6-6"/>', chev: '<path d="m9 18 6-6-6-6"/>', clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', alert: '<path d="m10.3 3.9-8.2 14a2 2 0 0 0 1.7 3h16.4a2 2 0 0 0 1.7-3l-8.2-14a2 2 0 0 0-3.4 0z"/><path d="M12 9v4m0 4h.01"/>', check: '<path d="m5 12 4 4L19 6"/>', x: '<path d="M18 6 6 18M6 6l12 12"/>', refresh: '<path d="M20 11a8 8 0 1 0-2.4 6M20 4v7h-7"/>', people: '<path d="M16 20v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 10a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM22 20v-2a4 4 0 0 0-3-3.87M16 2.13a4 4 0 0 1 0 7.75"/>', file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M8 13h8M8 17h8"/>', info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>' };
Object.assign(ICONS, {
    shirt: '<path d="m8 3-6 4 3 5 3-2v11h8V10l3 2 3-5-6-4a4 4 0 0 1-8 0Z"/>',
    calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18"/>',
    equipment: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 7h6M9 11h6M10 17h4"/>',
    reconcile: '<path d="M8 3H4v18h16V3h-4M8 3v4h8V3zM8 12h8M8 16h5"/>',
    tasks: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 1 1 2-2M13 9h4m-10 6 1 1 2-2m3 1h4"/>',
    apps: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>'
});
export function iconSvg(name) {
    return `<svg class="vq-icon" width="16" height="16" viewBox="0 0 24 24" stroke="currentColor" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[{chevron:'chev',close:'x',history:'clock'}[name] || name] || ''}</svg>`;
}
export function decorateIcons(root=document) {
    for (const el of root.querySelectorAll('[data-vq-icon]')) {
        const name=el.dataset.vqIcon;
        if (!Object.hasOwn(ICONS,name)) continue;
        el.innerHTML=iconSvg(name);
    }
}
/** Снимаем только inert, установленный этим модулем. */
export function syncModalInert(root, overlay) {
    const active=!!overlay?.querySelector('[role="dialog"],[role="alertdialog"]');
    for (const child of root.children) {
        if (child===overlay || child.hasAttribute('aria-live')) continue;
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
    const tooltip=(event,show)=>{
        const wrap=event.target.closest?.('.vq-tooltip-wrap');
        if (!wrap || (!show && wrap.contains(event.relatedTarget))) return;
        const doc=root.ownerDocument || root;
        if (!show && event.type==='pointerout' && wrap.contains(doc.activeElement)) return;
        if (!show && event.type==='focusout' && wrap.matches(':hover')) return;
        const tip=wrap.querySelector('[role="tooltip"]');
        if (tip) tip.hidden=!show;
    };
    root.addEventListener('pointerover',event=>tooltip(event,true));
    root.addEventListener('pointerout',event=>tooltip(event,false));
    root.addEventListener('focusin',event=>tooltip(event,true));
    root.addEventListener('focusout',event=>tooltip(event,false));
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
        for (const tip of root.querySelectorAll('[role="tooltip"]')) tip.hidden=true;
        const opened=menus().find(el=>el.open);
        if (opened) {event.preventDefault();opened.open=false;opened.querySelector('summary')?.focus();}
    });
}
if (typeof document!=='undefined') {
    if (document.readyState==='loading') document.addEventListener('DOMContentLoaded',()=>{enhanceShell();decorateIcons();});
    else {enhanceShell();decorateIcons();}
}
