/* Application shell: sidebar, top bar, hash router. */
import { api } from './api.js';
import { store } from './store.js';
import { clear, closeAllOverlays, el, fill, toast } from './util.js';
import { initTheme } from './theme.js';

const root = document.getElementById('app');

const NAV = [
  { id: 'daily', icon: '☀️', label: '今日の確認', hash: '#/daily' },
  { id: 'mytasks', icon: '✓', label: 'マイタスク', hash: '#/mytasks' },
  { id: 'projects', icon: '📁', label: 'プロジェクト', hash: '#/projects' },
  { id: 'issues', icon: '📌', label: '課題', hash: '#/issues' },
  { id: 'notifications', icon: '🔔', label: '通知', hash: '#/notifications', badge: true },
];

const ADMIN_NAV = [
  { id: 'users', icon: '👥', label: 'ユーザー', hash: '#/admin/users' },
  { id: 'groups', icon: '🏷️', label: 'グループ', hash: '#/admin/groups' },
  { id: 'settings', icon: '⚙️', label: 'システム設定', hash: '#/admin/settings' },
];

let shell = null;
let currentRoute = null;

/* ------------------------------------------------------------------ shell */

function buildShell() {
  const sidebar = el('aside', { class: 'sidebar', id: 'sidebar' });
  const backdrop = el('div', { class: 'sidebar-backdrop', hidden: true,
    onClick: () => toggleSidebar(false) });
  const title = el('h1', { text: '' });
  const topActions = el('div', { class: 'topbar-actions' });
  const bell = el('button', {
    class: 'icon-btn', title: '通知', onClick: () => { location.hash = '#/notifications'; },
  }, '🔔');
  const bellBadge = el('span', { class: 'badge-dot', hidden: true });
  bell.appendChild(bellBadge);

  const menuButton = el('button', {
    class: 'icon-btn', id: 'menu-btn', title: 'メニュー',
    onClick: () => toggleSidebar(),
  });
  menuButton.innerHTML =
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg>';

  const topbar = el('header', { class: 'topbar' },
    menuButton,
    title,
    el('div', { class: 'topbar-spacer' }),
    topActions, bell);

  const content = el('main', { class: 'content', id: 'content' });
  const mobileNav = el('nav', { class: 'mobile-nav' });
  const main = el('div', { class: 'main' }, topbar, content);
  const wrap = el('div', { class: 'app-shell' }, sidebar, main);

  clear(root);
  root.append(wrap, backdrop, mobileNav);
  shell = { sidebar, backdrop, title, topActions, content, bellBadge, mobileNav };
  renderSidebar();
  renderMobileNav();
  store.on(() => { renderSidebar(); renderMobileNav(); updateBell(); });
}

function toggleSidebar(force) {
  const open = force !== undefined ? force : !shell.sidebar.classList.contains('open');
  shell.sidebar.classList.toggle('open', open);
  shell.backdrop.hidden = !open;
}

function navItem(item, active) {
  const node = el('a', {
    class: `nav-item${active ? ' active' : ''}`,
    href: item.hash,
    onClick: () => toggleSidebar(false),
  }, el('span', { class: 'ico', text: item.icon }), el('span', { text: item.label }));
  if (item.badge && store.unread > 0) {
    node.appendChild(el('span', { class: 'count', text: String(store.unread) }));
  }
  if (item.dot) {
    node.insertBefore(el('span', { class: 'nav-dot', style: { background: item.dot } }),
      node.firstChild.nextSibling);
  }
  return node;
}

function renderSidebar() {
  const active = (location.hash || '#/daily');
  const projects = store.projects.filter((p) => !p.archived).slice(0, 12);
  fill(shell.sidebar, 
    el('div', { class: 'brand' },
      el('span', { class: 'brand-mark', text: '✓' }), store.ui.app_name || 'タスク管理'),
    el('div', { class: 'sidebar-section' },
      ...NAV.map((item) => navItem(item, active.startsWith(item.hash)))),
    el('div', { class: 'sidebar-section' },
      el('div', { class: 'sidebar-title', text: 'プロジェクト' }),
      ...projects.map((p) => navItem({
        icon: '', label: p.name, hash: `#/p/${p.id}/tasks`, dot: p.color,
      }, active.startsWith(`#/p/${p.id}`))),
      projects.length === 0
        ? el('div', { class: 'hint', style: { padding: '4px 10px' },
          text: 'プロジェクトがありません' })
        : null,
      el('a', { class: 'nav-item', href: '#/projects', onClick: () => toggleSidebar(false) },
        el('span', { class: 'ico', text: '＋' }), 'すべて表示')),
    store.isAdmin()
      ? el('div', { class: 'sidebar-section' },
        el('div', { class: 'sidebar-title', text: '管理' }),
        ...ADMIN_NAV.map((item) => navItem(item, active.startsWith(item.hash))))
      : null,
    el('div', { class: 'sidebar-foot' },
      el('a', { class: 'nav-item', href: '#/profile', onClick: () => toggleSidebar(false) },
        el('span', {
          class: 'avatar sm',
          style: { background: store.user?.avatar_color || '#98a2b3' },
          text: (store.user?.name || '?').slice(0, 1),
        }),
        el('span', {
          text: store.user?.name || '',
          style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
        })),
      el('button', { class: 'nav-item', onClick: doLogout },
        el('span', { class: 'ico', text: '⏻' }), 'ログアウト')),
  );
}

const MOBILE_NAV = ['daily', 'mytasks', 'projects', 'issues'];

function renderMobileNav() {
  const active = location.hash || '#/daily';
  const items = NAV.filter((item) => MOBILE_NAV.includes(item.id));
  fill(shell.mobileNav, ...items.map((item) => {
    const button = el('button', {
      class: active.startsWith(item.hash) ? 'active' : '',
      onClick: () => { location.hash = item.hash; },
    }, el('span', { class: 'ico', text: item.icon }), el('span', { text: item.label }));
    if (item.badge && store.unread > 0) {
      button.querySelector('.ico').textContent = '🔔';
      button.appendChild(el('span', {
        class: 'badge-dot', style: { position: 'static', marginTop: '-2px' },
        text: String(store.unread),
      }));
    }
    return button;
  }));
}

function updateBell() {
  if (!shell) return;
  shell.bellBadge.hidden = store.unread === 0;
  shell.bellBadge.textContent = store.unread > 99 ? '99+' : String(store.unread);
}

/** Views call this to set the page title and the top-right action buttons. */
export function setHeader(title, actions = []) {
  shell.title.textContent = title;
  fill(shell.topActions, ...actions.filter(Boolean));
  document.title = `${title} — ${store.ui.app_name || 'タスク管理'}`;
}

export function contentEl() {
  return shell.content;
}

async function doLogout() {
  try { await api.logout(); } catch { /* ignore */ }
  location.hash = '';
  location.reload();
}

/* ----------------------------------------------------------------- router */

const ROUTES = [
  [/^#?\/?$/, () => ({ view: 'daily' })],
  [/^#\/daily$/, () => ({ view: 'daily' })],
  [/^#\/mytasks$/, () => ({ view: 'mytasks' })],
  [/^#\/projects$/, () => ({ view: 'projects' })],
  [/^#\/p\/(\d+)\/tasks$/, (m) => ({ view: 'tasks', projectId: Number(m[1]) })],
  [/^#\/p\/(\d+)\/gantt$/, (m) => ({ view: 'gantt', projectId: Number(m[1]) })],
  [/^#\/p\/(\d+)\/bottlenecks$/, (m) => ({ view: 'bottlenecks', projectId: Number(m[1]) })],
  [/^#\/p\/(\d+)\/issues$/, (m) => ({ view: 'issues', projectId: Number(m[1]) })],
  [/^#\/issues$/, () => ({ view: 'issues' })],
  [/^#\/issue\/(\d+)$/, (m) => ({ view: 'issue', issueId: Number(m[1]) })],
  [/^#\/p\/(\d+)$/, (m) => ({ view: 'tasks', projectId: Number(m[1]) })],
  [/^#\/task\/(\d+)$/, (m) => ({ view: 'task', taskId: Number(m[1]) })],
  [/^#\/notifications$/, () => ({ view: 'notifications' })],
  [/^#\/profile$/, () => ({ view: 'profile' })],
  [/^#\/admin\/users$/, () => ({ view: 'admin', tab: 'users' })],
  [/^#\/admin\/groups$/, () => ({ view: 'admin', tab: 'groups' })],
  [/^#\/admin\/settings$/, () => ({ view: 'admin', tab: 'settings' })],
];

function parseRoute() {
  const hash = location.hash || '#/daily';
  for (const [pattern, build] of ROUTES) {
    const match = hash.match(pattern);
    if (match) return build(match);
  }
  return { view: 'notfound' };
}

const LOADERS = {
  daily: () => import('./views/daily.js'),
  mytasks: () => import('./views/mytasks.js'),
  projects: () => import('./views/projects.js'),
  tasks: () => import('./views/tasks.js'),
  gantt: () => import('./views/gantt.js'),
  bottlenecks: () => import('./views/bottlenecks.js'),
  issues: () => import('./views/issues.js'),
  notifications: () => import('./views/notifications.js'),
  profile: () => import('./views/profile.js'),
  admin: () => import('./views/admin.js'),
};

async function renderRoute() {
  const route = parseRoute();
  currentRoute = route;
  closeAllOverlays();
  renderSidebar();
  renderMobileNav();
  shell.content.className = 'content';

  if (route.view === 'task') {
    const { openTaskDetail } = await import('./views/taskDetail.js');
    const detail = await api.task(route.taskId).catch(() => null);
    if (!detail) {
      toast('タスクが見つかりません', 'error');
      location.hash = '#/daily';
      return;
    }
    location.hash = `#/p/${detail.task.project_id}/tasks`;
    setTimeout(() => openTaskDetail(route.taskId), 60);
    return;
  }

  if (route.view === 'issue') {
    const { openIssueDetail } = await import('./views/issueDetail.js');
    const detail = await api.get(`/api/issues/${route.issueId}`).catch(() => null);
    if (!detail) {
      toast('課題が見つかりません', 'error');
      location.hash = '#/issues';
      return;
    }
    location.hash = `#/p/${detail.issue.project_id}/issues`;
    setTimeout(() => openIssueDetail(route.issueId), 60);
    return;
  }

  const loader = LOADERS[route.view];
  if (!loader) {
    setHeader('ページが見つかりません');
    fill(shell.content, el('div', { class: 'card' },
      el('div', { class: 'empty' }, el('div', { class: 'big', text: '🧭' }),
        'このページは存在しません')));
    return;
  }
  fill(shell.content, el('div', { class: 'empty', text: '読み込み中…' }));
  try {
    const module = await loader();
    if (currentRoute !== route) return;
    await module.render(shell.content, route);
  } catch (error) {
    console.error(error);
    fill(shell.content, el('div', { class: 'card' },
      el('div', { class: 'empty' }, el('div', { class: 'big', text: '⚠️' }),
        error.message || '読み込みに失敗しました')));
  }
}

/* ------------------------------------------------------------------- boot */

function applyStoredTheme() {
  const value = localStorage.getItem('tm.theme') || 'auto';
  if (value === 'auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', value);
}

async function boot() {
  applyStoredTheme();
  try {
    await store.loadSession();
  } catch {
    /* offline or server error — fall through to the login screen */
  }
  initTheme({ theme: store.user?.ui_theme, accent: store.accent() });
  if (!store.user) {
    const { renderLogin } = await import('./views/login.js');
    renderLogin(root, boot);
    return;
  }
  buildShell();
  await store.loadBase();
  updateBell();
  window.addEventListener('hashchange', renderRoute);
  await renderRoute();
  primeNotifiedCursor();
  setInterval(pollNotifications, 120000);
}

/* --------------------------------------------------- desktop notifications */

const NOTIFIED_KEY = 'tm.lastNotifiedId';

function lastNotifiedId() {
  return Number(localStorage.getItem(NOTIFIED_KEY) || 0);
}

function setLastNotifiedId(value) {
  try { localStorage.setItem(NOTIFIED_KEY, String(value)); } catch { /* private mode */ }
}

/** Remember what already exists so we only alert on genuinely new items. */
async function primeNotifiedCursor() {
  try {
    const data = await api.notifications({ limit: 1 });
    const newest = data.notifications[0]?.id || 0;
    if (newest > lastNotifiedId()) setLastNotifiedId(newest);
  } catch { /* ignore */ }
}

async function pollNotifications() {
  try {
    const data = await api.notifications({ unread: 1, limit: 20 });
    store.unread = data.unread || 0;
    store.emit();
    updateBell();
    const cursor = lastNotifiedId();
    const fresh = data.notifications.filter((n) => n.id > cursor);
    if (!fresh.length) return;
    setLastNotifiedId(Math.max(...fresh.map((n) => n.id)));
    if (window.Notification && Notification.permission === 'granted') {
      for (const item of fresh.slice(0, 3)) {
        const alert = new Notification(item.title, {
          body: (item.body || '').split('\n').slice(0, 3).join('\n'),
          tag: `tm-${item.id}`,
        });
        alert.onclick = () => {
          window.focus();
          if (item.task_id) location.hash = `#/task/${item.task_id}`;
          alert.close();
        };
      }
    } else if (fresh.length) {
      toast(fresh.length === 1 ? fresh[0].title : `新しい通知が ${fresh.length} 件あります`);
    }
  } catch { /* offline */ }
}

boot();
