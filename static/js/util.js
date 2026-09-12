/* Small DOM / formatting helpers shared by every view. */

/** Create an element. `props` accepts class, text, html, dataset, style, on* handlers. */
export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key === 'style') Object.assign(node.style, value);
    else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) node.setAttribute(key, '');
    else node.setAttribute(key, value);
  }
  append(node, children);
  return node;
}

export function append(parent, children) {
  for (const child of children.flat(4)) {
    if (child === null || child === undefined || child === false) continue;
    parent.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return parent;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

/** Replace a node's content.  Unlike DOM append(), null/false children are skipped. */
export function fill(node, ...children) {
  clear(node);
  return append(node, children);
}

export function svgEl(tag, props = {}, ...children) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'text') node.textContent = value;
    else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else node.setAttribute(key, value);
  }
  append(node, children);
  return node;
}

/* ---------------- dates ---------------- */

export const MS_DAY = 86400000;

export function parseDate(value) {
  if (!value) return null;
  const [y, m, d] = String(value).slice(0, 10).split('-').map(Number);
  if (!y || !m || !d) return null;
  return new Date(y, m - 1, d);
}

export function toISO(date) {
  if (!date) return null;
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

export function today() {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

export function addDays(date, days) {
  const copy = new Date(date.getTime());
  copy.setDate(copy.getDate() + days);
  return copy;
}

export function daysBetween(a, b) {
  return Math.round((b.getTime() - a.getTime()) / MS_DAY);
}

const WEEKDAY = ['日', '月', '火', '水', '木', '金', '土'];

export function weekday(date) {
  return WEEKDAY[date.getDay()];
}

export function isWeekend(date) {
  return date.getDay() === 0 || date.getDay() === 6;
}

export function formatDate(value, withWeekday = false) {
  const date = parseDate(value);
  if (!date) return '—';
  const base = `${date.getMonth() + 1}/${date.getDate()}`;
  const year = date.getFullYear() !== new Date().getFullYear() ? `${date.getFullYear()}/` : '';
  return year + base + (withWeekday ? `(${weekday(date)})` : '');
}

export function formatDateTime(value) {
  if (!value) return '—';
  const iso = String(value).replace(' ', 'T');
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(value);
  const now = new Date();
  const diff = (now - date) / 1000;
  if (diff >= 0 && diff < 60) return 'たった今';
  if (diff >= 0 && diff < 3600) return `${Math.floor(diff / 60)}分前`;
  if (diff >= 0 && diff < 86400) return `${Math.floor(diff / 3600)}時間前`;
  const y = date.getFullYear() !== now.getFullYear() ? `${date.getFullYear()}/` : '';
  return `${y}${date.getMonth() + 1}/${date.getDate()} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

/** Days until the due date: negative when overdue, null when not set. */
export function dueDelta(due) {
  const date = parseDate(due);
  if (!date) return null;
  return daysBetween(today(), date);
}

export function dueClass(due, status) {
  if (status === 'done') return '';
  const delta = dueDelta(due);
  if (delta === null) return '';
  if (delta < 0) return 'overdue';
  if (delta === 0) return 'today';
  return '';
}

export function dueLabel(due, status) {
  const delta = dueDelta(due);
  if (delta === null) return '';
  if (status === 'done') return '';
  if (delta < 0) return `${-delta}日超過`;
  if (delta === 0) return '本日';
  if (delta <= 3) return `あと${delta}日`;
  return '';
}

/* ---------------- misc ---------------- */

export function initials(name) {
  if (!name) return '?';
  const trimmed = name.trim();
  if (/^[\x20-\x7e]+$/.test(trimmed)) {
    return trimmed.split(/\s+/).slice(0, 2).map((w) => w[0].toUpperCase()).join('');
  }
  return trimmed.slice(0, 1);
}

export function avatar(user, size = '') {
  const name = user?.name || user?.assignee_name || '';
  return el('span', {
    class: `avatar ${size}`.trim(),
    style: { background: user?.avatar_color || user?.assignee_color || '#98a2b3' },
    title: name || '未割当',
  }, name ? initials(name) : '–');
}

export function formatBytes(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function debounce(fn, wait = 250) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

/* ---------------- toasts, dialogs ---------------- */

let toastHost;

export function toast(message, kind = '') {
  if (!toastHost) {
    toastHost = el('div', { class: 'toast-host' });
    document.body.appendChild(toastHost);
  }
  const node = el('div', { class: `toast ${kind}`.trim(), text: message });
  toastHost.appendChild(node);
  setTimeout(() => {
    node.style.transition = 'opacity .25s';
    node.style.opacity = '0';
    setTimeout(() => node.remove(), 250);
  }, kind === 'error' ? 4200 : 2400);
}

/* Every open overlay registers its close function so the router can dismiss them. */
const openOverlays = new Set();

/** Close every open modal/drawer — used when the route changes. */
export function closeAllOverlays() {
  for (const close of [...openOverlays]) close();
  openOverlays.clear();
}

/** Modal dialog. `build(close)` returns the body; returns a promise resolved by close(value). */
export function openModal({ title, build, footer, wide = false, onClose }) {
  let resolveFn;
  const promise = new Promise((resolve) => { resolveFn = resolve; });
  const overlay = el('div', { class: 'overlay center' });
  const close = (value) => {
    overlay.remove();
    openOverlays.delete(close);
    document.removeEventListener('keydown', onKey);
    if (onClose) onClose(value);
    resolveFn(value);
  };
  openOverlays.add(close);
  const onKey = (event) => { if (event.key === 'Escape') close(undefined); };
  const modal = el('div', { class: `modal${wide ? ' wide' : ''}` },
    el('div', { class: 'modal-head' },
      el('h2', { text: title }),
      el('button', { class: 'icon-btn', title: '閉じる', onClick: () => close(undefined) }, '×')),
    el('div', { class: 'modal-body' }, build(close)));
  if (footer) modal.appendChild(el('div', { class: 'modal-foot' }, footer(close)));
  overlay.appendChild(modal);
  overlay.addEventListener('mousedown', (event) => {
    if (event.target === overlay) close(undefined);
  });
  document.addEventListener('keydown', onKey);
  document.body.appendChild(overlay);
  const focusable = modal.querySelector('input, textarea, select, button.btn-primary');
  if (focusable) setTimeout(() => focusable.focus(), 30);
  return promise;
}

export function openDrawer({ title, build, onClose }) {
  const overlay = el('div', { class: 'overlay' });
  const close = () => {
    overlay.remove();
    openOverlays.delete(close);
    document.removeEventListener('keydown', onKey);
    if (onClose) onClose();
  };
  openOverlays.add(close);
  const onKey = (event) => { if (event.key === 'Escape') close(); };
  const drawer = el('div', { class: 'drawer' });
  overlay.appendChild(drawer);
  overlay.addEventListener('mousedown', (event) => {
    if (event.target === overlay) close();
  });
  document.addEventListener('keydown', onKey);
  document.body.appendChild(overlay);
  build(drawer, close);
  return { close, drawer };
}

export async function confirmDialog(message, { title = '確認', danger = false, okLabel = 'OK' } = {}) {
  const result = await openModal({
    title,
    build: () => el('p', { text: message, style: { whiteSpace: 'pre-wrap' } }),
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(false) }, 'キャンセル'),
      el('button', {
        class: `btn ${danger ? 'btn-danger' : 'btn-primary'}`,
        onClick: () => close(true),
      }, okLabel),
    ],
  });
  return result === true;
}

export function promptDialog({ title, label, value = '', placeholder = '', multiline = false }) {
  return openModal({
    title,
    build: () => {
      const input = multiline
        ? el('textarea', { class: 'textarea', placeholder })
        : el('input', { class: 'input', placeholder });
      input.value = value;
      input.id = 'prompt-input';
      return el('div', { class: 'field' }, label ? el('label', { text: label }) : null, input);
    },
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', {
        class: 'btn btn-primary',
        onClick: () => close(document.getElementById('prompt-input').value),
      }, '保存'),
    ],
  });
}

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = el('a', { href: url, download: filename });
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}
