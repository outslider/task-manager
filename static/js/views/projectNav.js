/* Tab bar shared by every project-scoped screen. */
import { el } from '../util.js';
import { store } from '../store.js';

const TABS = [
  { key: 'tasks', label: 'タスク', icon: '☰' },
  { key: 'gantt', label: 'ガント', icon: '📊' },
  { key: 'workload', label: '負荷', icon: '📈' },
  { key: 'bottlenecks', label: 'ボトルネック', icon: '⛔' },
  { key: 'issues', label: '課題', icon: '📌' },
];

export function projectTabs(projectId, active) {
  const project = store.project(projectId);
  const counts = {
    bottlenecks: project?.stats?.blocked || 0,
    issues: project?.stats?.open_issues || 0,
  };
  // このプロジェクト専用の窓口があるときだけ、チケットへの入口を出す
  const openTickets = project?.stats?.open_tickets || 0;
  const tabs = openTickets
    ? [...TABS, { key: 'tickets', label: 'チケット', icon: '🎫',
      href: `#/tickets?project=${projectId}`, count: openTickets }]
    : TABS;
  return el('div', { class: 'proj-tabs' },
    ...tabs.map((tab) => el('a', {
      class: `proj-tab${tab.key === active ? ' active' : ''}`,
      href: tab.href || `#/p/${projectId}/${tab.key}`,
    },
    el('span', { class: 'ico', text: tab.icon }),
    el('span', { text: tab.label }),
    (tab.count ?? counts[tab.key])
      ? el('span', { class: 'count', text: String(tab.count ?? counts[tab.key]) })
      : null)));
}
