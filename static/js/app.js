/* Application shell: sidebar, top bar, hash router. */
import { api } from './api.js';
import { store } from './store.js';
import { icon } from './icons.js';
import { initPresenting, togglePresenting } from './present.js';
import { clear, closeAllOverlays, el, fill, skeleton, toast } from './util.js';
import { applyProjectTheme, initTheme } from './theme.js';
import { brandLockup } from './brand.js';

const root = document.getElementById('app');

const NAV = [
  { id: 'daily', icon: 'sun', label: '今日の確認', hash: '#/daily' },
  { id: 'mytasks', icon: 'check', label: 'マイタスク', hash: '#/mytasks' },
  { id: 'todos', icon: 'note', label: 'マイ ToDo', hash: '#/todos' },
  { id: 'projects', icon: 'folder', label: 'プロジェクト', hash: '#/projects' },
  { id: 'gantt', icon: 'chart', label: '全体ガント', hash: '#/gantt' },
  { id: 'links', icon: 'link', label: 'リンク集', hash: '#/links' },
  { id: 'issues', icon: 'pin', label: '課題', hash: '#/issues' },
  { id: 'tickets', icon: 'ticket', label: 'チケット', hash: '#/tickets' },
  { id: 'notifications', icon: 'bell', label: '通知', hash: '#/notifications', badge: true },
  { id: 'trash', icon: 'trash', label: 'ゴミ箱', hash: '#/trash' },
];

const ADMIN_NAV = [
  { id: 'users', icon: 'users', label: 'ユーザー', hash: '#/admin/users' },
  { id: 'logins', icon: 'eye', label: 'ログイン履歴', hash: '#/admin/logins' },
  { id: 'groups', icon: 'tag', label: 'グループ', hash: '#/admin/groups' },
  { id: 'taxonomy', icon: 'palette', label: '状態とカテゴリ', hash: '#/admin/taxonomy' },
  { id: 'queues', icon: 'inbox', label: 'チケット窓口', hash: '#/admin/queues' },
  { id: 'settings', icon: 'gear', label: 'システム設定', hash: '#/admin/settings' },
];

let shell = null;
let currentRoute = null;
// 描画の世代。非同期の描画が遅れて完了し、新しい画面を上書きするのを防ぐ。
let renderToken = 0;

/* ------------------------------------------------------------------ shell */

function buildShell() {
  const sidebar = el('aside', { class: 'sidebar', id: 'sidebar' });
  const backdrop = el('div', { class: 'sidebar-backdrop', hidden: true,
    onClick: () => toggleSidebar(false) });
  const title = el('h1', { text: '' });
  const topActions = el('div', { class: 'topbar-actions' });
  const quickAdd = el('button', {
    class: 'btn btn-primary qa-button', title: 'クイック追加（n キー）',
    onClick: () => openQuickAddDialog(),
  }, icon('bolt', { size: 16 }), el('span', { class: 'qa-label', text: 'クイック追加' }));
  const present = el('button', {
    class: 'icon-btn present-btn', title: '会議室モード（大きく・枠なしで映す）',
    onClick: () => togglePresenting(),
  }, icon('screen'));
  const bell = el('button', {
    class: 'icon-btn', title: '通知', onClick: () => { location.hash = '#/notifications'; },
  }, icon('bell'));
  const bellBadge = el('span', { class: 'badge-dot', hidden: true });
  bell.appendChild(bellBadge);

  const menuButton = el('button', {
    class: 'icon-btn', id: 'menu-btn', title: 'メニュー',
    onClick: () => toggleSidebar(),
  });
  menuButton.appendChild(icon('menu', { size: 20 }));

  const searchInput = el('input', {
    class: 'input topbar-search', type: 'search',
    placeholder: '検索（/ キー）', 'aria-label': '横断検索',
  });

  const topbar = el('header', { class: 'topbar' },
    menuButton,
    title,
    el('div', { class: 'topbar-spacer' }),
    // 社外ユーザーはタスクを足さない（起票はチケットから）ので、クイック追加は出さない
    searchInput, topActions, store.isGuest() ? null : quickAdd, present, bell);

  const progress = el('div', { class: 'route-progress', hidden: true });
  const content = el('main', { class: 'content', id: 'content' });
  const mobileNav = el('nav', { class: 'mobile-nav' });
  const main = el('div', { class: 'main' }, topbar, progress, content);
  const wrap = el('div', { class: 'app-shell' }, sidebar, main);

  clear(root);
  root.append(wrap, backdrop, mobileNav);
  if (store.acting) root.prepend(actingBanner());
  shell = { sidebar, backdrop, title, topActions, content, bellBadge, mobileNav, progress,
    searchInput };
  import('./views/search.js').then((m) => m.attachSearch(searchInput));
  renderSidebar();
  renderMobileNav();
  store.on(() => { renderSidebar(); renderMobileNav(); updateBell(); tintForRoute(); });
}

/** 代理表示中の帯。いつ見ても「誰として見ているか」「見るだけ」が分かるように、常に上に出す。 */
function actingBanner() {
  document.documentElement.setAttribute('data-acting', '');
  const left = el('span', { class: 'acting-left' });
  const until = new Date(store.acting.until.replace(' ', 'T'));
  const tick = () => {
    const minutes = Math.max(0, Math.ceil((until - Date.now()) / 60000));
    left.textContent = `あと ${minutes} 分`;
    if (minutes <= 0) location.reload();
  };
  tick();
  setInterval(tick, 20000);
  const preview = store.acting.preview;
  return el('div', { class: 'acting-banner', role: 'status' },
    icon('eye', { size: 16 }),
    preview
      ? el('span', {},
        el('strong', { text: 'プレビュー：' }),
        el('span', { text: `「${preview.project}」を${preview.organization ? `${preview.organization} の` : ''}社外ユーザーとして表示中（見るだけ）` }))
      : el('span', {},
        el('strong', { text: `${store.user.name} さん` }),
        el('span', { text: `として表示中（見るだけ・${store.acting.by} さんの代理表示）` })),
    left,
    el('button', {
      class: 'btn btn-sm acting-stop',
      onClick: async () => {
        await api.post('/api/auth/act/stop');
        location.hash = preview ? `#/p/${preview.project_id}/tasks` : '#/admin/users';
        location.reload();
      },
    }, '自分の表示に戻る'));
}

/** プロジェクトの画面ではその色で染め、ほかの画面では本人の色に戻す。 */
function tintForRoute() {
  const id = currentRoute?.projectId;
  document.documentElement.toggleAttribute('data-in-project', Boolean(id));
  applyProjectTheme(id ? store.project(id) : null, { tint: store.user?.ui_project_tint !== false });
}

/** プロジェクトの頭文字か絵文字を、プロジェクトの色の角丸に載せた小さな札。 */
export function projectTile(project, size = 'sm') {
  const mark = project.icon || Array.from((project.name || '?').trim())[0] || '?';
  return el('span', {
    class: `proj-tile ${size}${project.icon ? ' emoji' : ''}`,
    style: { '--tile': project.color || '#4f6bff' },
    'aria-hidden': 'true',
    text: mark,
  });
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
  }, el('span', { class: 'ico' }, item.icon ? icon(item.icon) : null),
  el('span', { text: item.label }));
  if (item.badge && store.unread > 0) {
    node.appendChild(el('span', { class: 'count', text: String(store.unread) }));
  }
  if (item.dot) {
    node.insertBefore(el('span', { class: 'nav-dot', style: { background: item.dot } }),
      node.firstChild.nextSibling);
  }
  if (item.project) {
    node.classList.add('nav-project');
    node.style.setProperty('--tile', item.project.color || '#4f6bff');
    node.replaceChild(projectTile(item.project), node.firstChild);
  }
  return node;
}

/**
 * 左メニューの並び。本人が決めた順があればそれに従い、
 * あとから増えた項目は末尾に回す（設定を消さずに新機能を足せるように）。
 */
// 社外ユーザーには出さないメニュー（サーバー側でも閉じている）
const GUEST_HIDDEN = new Set(['trash']);
const ACTING_HIDDEN = new Set(['todos', 'trash']);
// 代理表示中に開かない画面（本人だけのもの・設定を変える画面）
const ACTING_CLOSED = new Set(['todos', 'profile', 'trash', 'admin']);

export function orderedNav() {
  const wanted = store.user?.nav_order || [];
  let nav = store.isGuest() ? NAV.filter((item) => !GUEST_HIDDEN.has(item.id)) : NAV;
  // 代理表示中は、本人だけのもの（マイ ToDo）を出さない（サーバーでも閉じている）
  if (store.acting) nav = nav.filter((item) => !ACTING_HIDDEN.has(item.id));
  if (!wanted.length) return nav;
  const byId = new Map(nav.map((item) => [item.id, item]));
  const picked = [];
  for (const id of wanted) {
    if (byId.has(id)) {
      picked.push(byId.get(id));
      byId.delete(id);
    }
  }
  return [...picked, ...byId.values()];
}

/** 並び替え画面のための一覧（id・記号・名前だけ）。 */
export function navChoices() {
  return orderedNav().map(({ id, icon, label }) => ({ id, icon, label }));
}

export function defaultNavOrder() {
  return NAV.map((item) => item.id);
}

// 左メニューに並べるプロジェクトの数。これを超えた分は「ほか N 件」にまとめる
const SIDEBAR_PROJECTS = 12;

function renderSidebar() {
  const active = (location.hash || '#/daily');
  const live = store.projects.filter((p) => !p.archived);
  const projects = live.slice(0, SIDEBAR_PROJECTS);
  const more = live.length - projects.length;
  fill(shell.sidebar, 
    brandLockup('md'),
    el('div', { class: 'sidebar-section' },
      ...orderedNav().map((item) => navItem(item, active.startsWith(item.hash)))),
    el('div', { class: 'sidebar-section' },
      el('div', { class: 'sidebar-title', text: 'プロジェクト' }),
      ...projects.map((p) => navItem({
        // プロジェクトを切り替えても、いま見ていた画面のまま移りたい
        icon: '', label: p.name, hash: `#/p/${p.id}/${lastProjectTab()}`, project: p,
      }, active.startsWith(`#/p/${p.id}`))),
      projects.length === 0
        ? el('div', { class: 'hint', style: { padding: '4px 10px' },
          text: 'プロジェクトがありません' })
        : null,
      // 並びきらないときだけ出す。いつも出ていると、隠れているものがあるように見えるため
      more > 0
        ? el('a', {
          class: 'nav-item', href: '#/projects', title: 'プロジェクト一覧ですべて見る',
          onClick: () => toggleSidebar(false),
        }, el('span', { class: 'ico' }, icon('list')), `ほか ${more} 件`)
        : null),
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
        el('span', { class: 'ico' }, icon('power')), 'ログアウト')),
  );
}

/** スマホ下部に出す数。並び替えた先頭から取る。 */
const MOBILE_NAV_COUNT = 4;

function renderMobileNav() {
  const active = location.hash || '#/daily';
  const items = orderedNav().slice(0, MOBILE_NAV_COUNT);
  fill(shell.mobileNav, ...items.map((item) => {
    const button = el('button', {
      class: active.startsWith(item.hash) ? 'active' : '',
      onClick: () => { location.hash = item.hash; },
    }, el('span', { class: 'ico' }, item.icon ? icon(item.icon) : null),
    el('span', { text: item.label }));
    if (item.badge && store.unread > 0) {
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

const PROJECT_TABS = ['tasks', 'gantt', 'workload', 'bottlenecks', 'issues'];
const LAST_TAB_KEY = 'tm.lastProjectTab';

/** 直前に開いていたプロジェクト内の画面。既定はタスク一覧。 */
export function lastProjectTab() {
  try {
    const saved = localStorage.getItem(LAST_TAB_KEY);
    return PROJECT_TABS.includes(saved) ? saved : 'tasks';
  } catch { return 'tasks'; }
}

function rememberProjectTab(view) {
  if (!PROJECT_TABS.includes(view)) return;
  try { localStorage.setItem(LAST_TAB_KEY, view); } catch { /* private mode */ }
}

/** いま表示している画面を描き直す。重なったドロワーから中身を変えたときに使う。 */
export function refreshRoute() {
  return renderRoute();
}

export function contentEl() {
  return shell.content;
}

/** どの画面からでも開けるクイック追加。現在のプロジェクトを初期値にする。 */
export async function openQuickAddDialog() {
  const { openQuickAdd } = await import('./views/quickAdd.js');
  const match = (location.hash || '').match(/^#\/p\/(\d+)\//);
  const openedAt = location.hash;
  await openQuickAdd({
    projectId: match ? Number(match[1]) : null,
    // 登録中に別の画面へ移動していたら、その画面を上書きしない
    onCreated: () => { if (location.hash === openedAt) renderRoute(); },
  });
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
  [/^#\/todos$/, () => ({ view: 'todos' })],
  [/^#\/projects$/, () => ({ view: 'projects' })],
  [/^#\/p\/(\d+)\/tasks$/, (m) => ({ view: 'tasks', projectId: Number(m[1]) })],
  [/^#\/p\/(\d+)\/gantt$/, (m) => ({ view: 'gantt', projectId: Number(m[1]) })],
  [/^#\/gantt$/, () => ({ view: 'gantt', projectId: null })],
  [/^#\/p\/(\d+)\/bottlenecks$/, (m) => ({ view: 'bottlenecks', projectId: Number(m[1]) })],
  [/^#\/p\/(\d+)\/issues$/, (m) => ({ view: 'issues', projectId: Number(m[1]) })],
  [/^#\/p\/(\d+)\/workload$/, (m) => ({ view: 'workload', projectId: Number(m[1]) })],
  [/^#\/workload$/, () => ({ view: 'workload' })],
  [/^#\/issues$/, () => ({ view: 'issues' })],
  [/^#\/issue\/(\d+)$/, (m) => ({ view: 'issue', issueId: Number(m[1]) })],
  [/^#\/p\/(\d+)$/, (m) => ({ view: 'tasks', projectId: Number(m[1]) })],
  [/^#\/task\/(\d+)$/, (m) => ({ view: 'task', taskId: Number(m[1]) })],
  [/^#\/links$/, () => ({ view: 'links' })],
  [/^#\/tickets(?:\?(.*))?$/, (m) => ({
    view: 'tickets',
    projectId: Number(new URLSearchParams(m[1] || '').get('project')) || null,
  })],
  [/^#\/ticket\/(\d+)$/, (m) => ({ view: 'ticket', ticketId: Number(m[1]) })],
  [/^#\/notifications$/, () => ({ view: 'notifications' })],
  [/^#\/trash$/, () => ({ view: 'trash' })],
  [/^#\/profile$/, () => ({ view: 'profile' })],
  [/^#\/admin\/users$/, () => ({ view: 'admin', tab: 'users' })],
  [/^#\/admin\/logins$/, () => ({ view: 'admin', tab: 'logins' })],
  [/^#\/admin\/groups$/, () => ({ view: 'admin', tab: 'groups' })],
  [/^#\/admin\/taxonomy$/, () => ({ view: 'admin', tab: 'taxonomy' })],
  [/^#\/admin\/queues$/, () => ({ view: 'admin', tab: 'queues' })],
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
  todos: () => import('./views/todos.js'),
  projects: () => import('./views/projects.js'),
  tasks: () => import('./views/tasks.js'),
  gantt: () => import('./views/gantt.js'),
  bottlenecks: () => import('./views/bottlenecks.js'),
  issues: () => import('./views/issues.js'),
  workload: () => import('./views/workload.js'),
  links: () => import('./views/links.js'),
  tickets: () => import('./views/tickets.js'),
  notifications: () => import('./views/notifications.js'),
  trash: () => import('./views/trash.js'),
  profile: () => import('./views/profile.js'),
  admin: () => import('./views/admin.js'),
};

async function renderRoute() {
  const route = parseRoute();
  const token = ++renderToken;
  currentRoute = route;
  if (route.projectId) rememberProjectTab(route.view);
  closeAllOverlays();
  renderSidebar();
  renderMobileNav();
  tintForRoute();
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

  if (route.view === 'ticket') {
    const { openTicketDetail } = await import('./views/ticketDetail.js');
    location.hash = '#/tickets';
    setTimeout(() => openTicketDetail(route.ticketId), 60);
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

  if (store.acting && ACTING_CLOSED.has(route.view)) {
    setHeader('代理表示中');
    fill(shell.content, el('div', { class: 'card' },
      el('div', { class: 'empty' }, el('div', { class: 'big', text: '🔒' }),
        '代理表示中は、この画面を開けません（本人だけの画面・設定を変える画面のため）')));
    return;
  }

  // プロジェクトで使っていないタブ（社外ユーザーに見せていないタブを含む）を URL で
  // 開いたときは、タスク一覧へ回す。前に開いていたタブを覚えている場合もここに来る
  if (route.projectId && route.view !== 'tasks') {
    const { tabAllowed } = await import('./views/projectNav.js');
    if (!tabAllowed(route.projectId, route.view)) {
      location.replace(`#/p/${route.projectId}/tasks`);
      return;
    }
  }

  const loader = LOADERS[route.view];
  if (!loader) {
    setHeader('ページが見つかりません');
    fill(shell.content, el('div', { class: 'card' },
      el('div', { class: 'empty' }, el('div', { class: 'big', text: '🧭' }),
        'このページは存在しません')));
    return;
  }

  // ビューはいったん DOM から切り離した箱に描く。描いている間に別の画面へ
  // 移動していたら、その箱ごと捨てる。古い描画が新しい画面を上書きしない。
  const view = el('div', { class: 'content' });
  setLoading(true);
  // 完成した画面と差し替わるまでのあいだ、中身の形をした箱を出しておく。
  // ビュー自身は切り離した箱に描くので、ここで出さないと真っ白なまま待たせてしまう。
  fill(shell.content, routeSkeleton(route.view));
  try {
    const module = await loader();
    if (token !== renderToken) return;
    await module.render(view, route);
  } catch (error) {
    if (token !== renderToken) return;
    console.error(error);
    fill(view, el('div', { class: 'card' },
      el('div', { class: 'empty' }, el('div', { class: 'big', text: '⚠️' }),
        error.message || '読み込みに失敗しました')));
  } finally {
    if (token === renderToken) setLoading(false);
  }
  if (token !== renderToken) return;
  shell.content.className = view.className;
  fill(shell.content, ...[...view.childNodes]);
}

// 画面ごとの、待っているあいだの見た目。中身の形に近いものを選ぶ。
const SKELETON_SHAPE = {
  projects: ['cards', 3], daily: ['cards', 3], notifications: ['rows', 7],
  admin: ['rows', 5], profile: ['cards', 2], trash: ['rows', 3],
};

function routeSkeleton(view) {
  const [kind, count] = SKELETON_SHAPE[view] || ['rows', 6];
  return el('div', { class: 'card' }, el('div', { class: 'card-body' },
    skeleton(kind, count)));
}

/** 画面の切り替え中であることを細いバーで示す。 */
function setLoading(on) {
  if (!shell?.progress) return;
  shell.progress.hidden = !on;
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
  const reset = location.hash.match(/^#\/reset\/([A-Za-z0-9_-]+)$/);
  if (!store.user && reset) {
    const { renderReset } = await import('./views/login.js');
    renderReset(root, reset[1], boot);
    return;
  }
  if (!store.user) {
    const { renderLogin } = await import('./views/login.js');
    renderLogin(root, boot);
    return;
  }
  if (store.user.mfa_setup_required) {
    // 多要素認証が必須なのに未設定。済ませるまで他の画面には進めない（サーバーでも止めている）
    const { renderForcedSetup } = await import('./views/security.js');
    renderForcedSetup(root, () => location.reload());
    return;
  }
  if (reset) location.hash = '#/';
  buildShell();
  await store.loadBase();
  updateBell();
  initPresenting();
  window.addEventListener('hashchange', renderRoute);
  document.addEventListener('keydown', (event) => {
    if (!['n', '/'].includes(event.key) || event.metaKey || event.ctrlKey || event.altKey) return;
    const active = document.activeElement;
    if (active && active.matches('input, textarea, select, [contenteditable]')) return;
    if (document.querySelector('.overlay')) return;
    event.preventDefault();
    if (event.key === '/') shell.searchInput?.focus();
    else if (!store.isGuest()) openQuickAddDialog();
  });
  await renderRoute();
  if (store.acting) return;   // 代理表示中は、相手の通知を管理者のデスクトップに出さない
  const recoveryLeft = sessionStorage.getItem('tm.recoveryWarn');
  if (recoveryLeft !== null) {
    sessionStorage.removeItem('tm.recoveryWarn');
    toast(`予備コードでログインしました。残りは ${recoveryLeft} 個です。プロフィール設定で作り直せます`, 'error');
  }
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
