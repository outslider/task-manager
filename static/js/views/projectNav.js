/* Tab bar shared by every project-scoped screen. */
import { el } from '../util.js';
import { store } from '../store.js';
import { icon } from '../icons.js';

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
  return el('div', { class: 'proj-tabs' },
    ...tabs.map((tab) => el('a', {
      class: `proj-tab${tab.key === active ? ' active' : ''}`,
      href: tab.href || `#/p/${projectId}/${tab.key}`,
    },
    el('span', { class: 'ico' }, icon(tab.icon, { size: 16 })),
    el('span', { text: tab.label }),
    (tab.count ?? counts[tab.key])
      ? el('span', { class: 'count', text: String(tab.count ?? counts[tab.key]) })
      : null)));
}

/** このプロジェクトで、この画面を出してよいか（使っていないタブを URL で開いたとき用）。 */
export function tabAllowed(projectId, key) {
  const project = store.project(projectId);
  return !project?.tabs || key === 'tasks' || project.tabs.includes(key);
}
