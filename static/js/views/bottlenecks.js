/* Bottleneck analysis: what is holding the project up, and why. */
import { api } from '../api.js';
import { setHeader } from '../app.js';
import { STATUS_LABEL, category } from '../store.js';
import { el, fill, formatDate, dueClass } from '../util.js';
import { openTaskDetail } from './taskDetail.js';
import { categoryChip } from './pickers.js';
import { projectTabs } from './projectNav.js';

export async function render(container, route) {
  const projectId = route.projectId;
  const [project, data] = await Promise.all([
    api.project(projectId),
    api.get(`/api/projects/${projectId}/bottlenecks`),
  ]);

  setHeader(`${project.project.name} — ボトルネック`);

  const host = el('div', {});
  fill(container, host);

  async function reload() {
    const fresh = await api.get(`/api/projects/${projectId}/bottlenecks`);
    Object.assign(data, fresh);
    draw();
  }

  function draw() {
    const { bottlenecks, conflicts, critical_path: chain } = data;
    const blocking = bottlenecks.filter((b) => b.blocks_open > 0);
    const waiting = bottlenecks.filter((b) => b.is_blocked);

    fill(host,
      projectTabs(projectId, 'bottlenecks'),
      el('div', { class: 'page-head' },
        el('div', { class: 'grow' },
          el('div', { class: 'page-sub',
            text: '依存関係をたどって、止まると影響が大きいタスクを洗い出しています。' }))),

      el('div', { class: 'grid cols-4', style: { marginBottom: '14px' } },
        stat('他をブロック中', blocking.length, blocking.length ? 'danger' : ''),
        stat('先行待ちで着手不可', waiting.length, waiting.length ? 'warn' : ''),
        stat('クリティカルパス', `${chain.length} 件`, ''),
        stat('日程の矛盾', conflicts.length, conflicts.length ? 'danger' : '')),

      conflicts.length ? conflictCard(conflicts) : null,
      chain.length ? chainCard(chain) : null,

      el('div', { class: 'card' },
        el('div', { class: 'card-head' },
          el('h2', {}, '⛔ ボトルネック（影響の大きい順）'),
          el('span', { class: 'badge', text: `${bottlenecks.length} 件` })),
        el('div', { class: 'card-body tight' },
          bottlenecks.length
            ? el('div', {}, ...bottlenecks.map(bottleneckRow))
            : el('div', { class: 'empty' },
              el('div', { class: 'big', text: '🎉' }),
              '止まっているタスクはありません'))));
  }

  function stat(label, value, tone) {
    return el('div', { class: 'card stat' },
      el('div', { class: 'k', text: label }),
      el('div', { class: `v ${tone}`.trim(), text: String(value) }));
  }

  function conflictCard(conflicts) {
    return el('div', { class: 'card', style: { marginBottom: '14px' } },
      el('div', { class: 'card-head' }, el('h2', {}, '⚠ 依存関係と日程の矛盾')),
      el('div', { class: 'card-body' },
        el('div', { class: 'page-sub',
          text: '先行タスクの期限が、後続タスクの開始日より後になっています。日程かカテゴリ順序の見直しが必要です。' }),
        ...conflicts.map((c) => el('div', { class: 'att-item' },
          el('span', { text: '⚠' }),
          el('span', { class: 'name' },
            el('a', {
              href: '#',
              onClick: (event) => {
                event.preventDefault();
                openTaskDetail(c.depends_on_id, { onChange: reload });
              },
              text: c.depends_on_title,
            }),
            ' の完了予定が ',
            el('a', {
              href: '#',
              onClick: (event) => {
                event.preventDefault();
                openTaskDetail(c.task_id, { onChange: reload });
              },
              text: c.task_title,
            }),
            ' の開始より後です'),
          el('span', { class: 'badge overdue', text: `${c.overlap_days} 日超過` })))));
  }

  function chainCard(chain) {
    return el('div', { class: 'card', style: { marginBottom: '14px' } },
      el('div', { class: 'card-head' }, el('h2', {}, '🔗 クリティカルパス')),
      el('div', { class: 'card-body' },
        el('div', { class: 'page-sub',
          text: 'この連なりのどれか1つでも遅れると、プロジェクト全体の完了が同じだけ遅れます。' }),
        el('div', { class: 'chain', style: { marginTop: '10px' } },
          ...chain.flatMap((node, index) => [
            index ? el('span', { class: 'chain-arrow', text: '→' }) : null,
            el('span', {
              class: 'chain-node', text: node.title,
              onClick: () => openTaskDetail(node.id, { onChange: reload }),
            }),
          ]))));
  }

  function bottleneckRow(item, index) {
    return el('div', {
      class: 'bn-item',
      onClick: () => openTaskDetail(item.id, { onChange: reload }),
    },
    el('div', { class: 'bn-rank', text: String(index + 1) }),
    el('div', { style: { minWidth: 0 } },
      el('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' } },
        el('strong', { text: item.title }),
        el('span', { class: `badge ${item.status}`, text: STATUS_LABEL[item.status] }),
        item.category ? categoryChip(item.category, { small: true }) : null,
        item.due_date
          ? el('span', {
            class: `badge ${dueClass(item.due_date, item.status) || ''}`.trim(),
            text: `期限 ${formatDate(item.due_date)}`,
          })
          : null),
      el('div', { class: 'bn-reasons' },
        ...item.reasons.map((reason) => el('span', { class: 'badge', text: reason }))),
      el('div', { class: 'hint' },
        item.assignee_name ? `担当: ${item.assignee_name}` : '担当: 未割当',
        ` ・ 進捗 ${item.progress}%`,
        item.slack_days > 0 ? ` ・ 余裕 ${item.slack_days} 日` : '')),
    el('div', { class: 'bn-impact' },
      el('div', { class: 'n', text: String(item.blocks_open) }),
      el('div', { class: 'k', text: '件が待機' })));
  }

  draw();
}
