/* Tab bar shared by every project-scoped screen. */
import { el, svgEl } from '../util.js';
import { store } from '../store.js';
import { icon } from '../icons.js';
import { projectTile, refreshRoute } from '../app.js';
import { projectColors } from '../theme.js';

const TABS = [
  { key: 'tasks', label: 'タスク', icon: 'list' },
  { key: 'gantt', label: 'ガント', icon: 'chart' },
  { key: 'workload', label: '負荷', icon: 'gauge' },
  { key: 'bottlenecks', label: 'ボトルネック', icon: 'block' },
  { key: 'issues', label: '課題', icon: 'pin' },
];

export function projectTabs(projectId, active) {
  const project = store.project(projectId);
  const counts = {
    bottlenecks: project?.stats?.blocked || 0,
    issues: project?.stats?.open_issues || 0,
  };
  // このプロジェクト専用の窓口があるときだけ、チケットへの入口を出す
  const openTickets = project?.stats?.open_tickets || 0;
  // プロジェクトごとに選んだタブだけ出す（サーバーが、この人に見せてよいものを返す）。
  // 社外ユーザーの負荷は、設定によらず出ない
  const allowed = new Set(project?.tabs
    || TABS.map((tab) => tab.key).filter((key) => !(store.isGuest() && key === 'workload')));
  const base = TABS.filter((tab) => allowed.has(tab.key));
  const tabs = openTickets && (!project?.tabs || allowed.has('tickets'))
    ? [...base, { key: 'tickets', label: 'チケット', icon: 'ticket',
      href: `#/tickets?project=${projectId}`, count: openTickets }]
    : base;
  const nav = el('nav', { class: 'proj-tabs' },
    ...tabs.map((tab) => el('a', {
      class: `proj-tab${tab.key === active ? ' active' : ''}`,
      href: tab.href || `#/p/${projectId}/${tab.key}`,
    },
    el('span', { class: 'ico' }, icon(tab.icon, { size: 16 })),
    el('span', { text: tab.label }),
    (tab.count ?? counts[tab.key])
      ? el('span', { class: 'count', text: String(tab.count ?? counts[tab.key]) })
      : null)));
  return project ? projectHero(project, nav) : nav;
}

/** 進捗の輪。done / total を 0〜100 の弧で見せる。 */
function progressRing(done, total) {
  const pct = total ? Math.round((done / total) * 100) : 0;
  const r = 15;
  const len = 2 * Math.PI * r;
  return el('div', { class: 'proj-ring', title: `完了 ${done} / ${total} 件` },
    svgEl('svg', { viewBox: '0 0 36 36', width: 40, height: 40, 'aria-hidden': 'true' },
      svgEl('circle', { cx: 18, cy: 18, r, class: 'track' }),
      svgEl('circle', {
        cx: 18, cy: 18, r, class: 'bar',
        'stroke-dasharray': `${(len * pct) / 100} ${len}`, transform: 'rotate(-90 18 18)',
      })),
    el('span', { class: 'pct', text: `${pct}%` }));
}

/** プロジェクトの画面の頭。プロジェクトの色の帯に、名前・説明・ひと目で分かる数字とタブを載せる。 */
function projectHero(project, nav) {
  const stats = project.stats || {};
  const chips = [
    stats.overdue ? el('span', { class: 'hero-chip warn', text: `期限超過 ${stats.overdue}` }) : null,
    stats.blocked ? el('span', { class: 'hero-chip', text: `待ち ${stats.blocked}` }) : null,
    stats.open_issues ? el('span', { class: 'hero-chip', text: `課題 ${stats.open_issues}` }) : null,
    stats.milestones ? el('span', { class: 'hero-chip', text: `◆ ${stats.milestones}` }) : null,
  ].filter(Boolean);
  // 「プロジェクトの色で染める」を外した人には、帯も白地にする（札と上端の細い線だけ色を残す）
  const tinted = store.user?.ui_project_tint !== false;
  return el('div', {
    class: `proj-head${tinted ? '' : ' neutral'}`, dataset: { pattern: project.theme || 'aurora' },
    style: projectColors(project.color),
  },
    el('div', { class: 'proj-hero' },
      projectTile(project, 'lg'),
      el('div', { class: 'proj-hero-text' },
        el('div', { class: 'proj-hero-name', text: project.name }),
        project.description
          ? el('div', { class: 'proj-hero-desc', text: project.description.split('\n')[0] })
          : null),
      el('div', { class: 'proj-hero-stats' },
        chips.length ? el('div', { class: 'hero-chips' }, ...chips) : null,
        stats.total ? progressRing(stats.done || 0, stats.total) : null,
        memberButton(project))),
    nav);
}

/** このプロジェクトで、この画面を出してよいか（使っていないタブを URL で開いたとき用）。 */
export function tabAllowed(projectId, key) {
  const project = store.project(projectId);
  return !project?.tabs || key === 'tasks' || project.tabs.includes(key);
}

/** 参加している人の顔と人数。プロジェクト管理者には、押すとメンバーの編集が開く。 */
function memberButton(project) {
  const people = project.members || [];
  const owner = project.my_role === 'owner';
  if (!people.length && !owner) return null;
  const faces = el('span', { class: 'avatar-stack' },
    ...people.slice(0, 4).map((u) => el('span', {
      class: 'avatar sm', style: { background: u.avatar_color || '#98a2b3' }, text: (u.name || '?').slice(0, 1),
    })));
  const label = el('span', { text: owner ? `メンバー ${people.length}` : `${people.length} 名` });
  if (!owner) {
    return el('span', { class: 'hero-members', title: people.map((u) => u.name).join('、') }, faces, label);
  }
  return el('button', {
    class: 'hero-members', type: 'button', title: 'メンバーの追加・外す・権限の変更',
    onClick: async () => {
      const { openMembers } = await import('./members.js');
      if (await openMembers(project.id)) refreshRoute();
    },
  }, faces, label);
}
