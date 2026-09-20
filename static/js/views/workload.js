/* 担当者ごとの負荷（週別）と、見積・実績の突き合わせ。 */
import { api } from '../api.js';
import { setHeader } from '../app.js';
import { store } from '../store.js';
import { avatar, el, fill, toast } from '../util.js';
import { option } from './pickers.js';
import { projectTabs } from './projectNav.js';
import { openTaskDetail } from './taskDetail.js';

export async function render(container, route) {
  const projectId = route.projectId || null;
  const state = { weeks: 8, projectId };
  const host = el('div', {});
  let data = null;

  const project = projectId ? store.project(projectId) : null;
  setHeader(project ? `${project.name} — 負荷` : 'メンバーの負荷');

  fill(container, projectId ? projectTabs(projectId, 'workload') : null, host);

  async function load() {
    fill(host, el('div', { class: 'empty', text: '集計しています…' }));
    try {
      data = await api.get('/api/workload', {
        project_id: state.projectId || '', weeks: state.weeks,
      });
      draw();
    } catch (error) {
      fill(host, el('div', { class: 'card' }, el('div', { class: 'empty', text: error.message })));
    }
  }

  function draw() {
    const { weeks, rows, unscheduled, effort, capacity_per_week: capacity } = data;
    const showHours = data.has_estimates;

    fill(host,
      el('div', { class: 'page-head' },
        el('div', { class: 'grow' },
          el('div', { class: 'page-sub' },
            showHours
              ? `見積工数を週ごとに割り振って、担当者の負荷を出しています（1人あたり ${capacity}h/週）。`
              : '見積工数が入っていないため、担当タスクの件数で表示しています。')),
        controls()),

      el('div', { class: 'grid cols-4', style: { marginBottom: '14px' } },
        stat('見積の合計', effort.estimated ? `${effort.estimated}h` : '—', ''),
        stat('実績の合計', effort.actual ? `${effort.actual}h` : '—', ''),
        stat('完了分の見積 / 実績',
          effort.done_estimated ? `${effort.done_estimated} / ${effort.done_actual}h` : '—', ''),
        stat('見積精度', effort.accuracy
          ? `${Math.round(effort.accuracy * 100)}%`
          : '—', effort.accuracy && effort.accuracy > 1.2 ? 'danger'
          : effort.accuracy && effort.accuracy > 1.05 ? 'warn' : 'ok')),

      el('div', { class: 'card' },
        el('div', { class: 'card-head' },
          el('h2', {}, '週別の負荷'),
          data.missing_estimate
            ? el('span', { class: 'badge', title: '見積工数が入っていないタスク' },
              `見積未入力 ${data.missing_estimate} 件`)
            : null),
        el('div', { class: 'card-body tight' },
          rows.length
            ? el('div', { class: 'wl-wrap' }, table(weeks, rows, capacity, showHours))
            : el('div', { class: 'empty' },
              el('div', { class: 'big', text: '🗓' }), '期間の入ったタスクがありません'))),

      unscheduled.length
        ? el('div', { class: 'card', style: { marginTop: '14px' } },
          el('div', { class: 'card-head' },
            el('h2', {}, '未計画のタスク'),
            el('span', { class: 'badge', text: `${unscheduled.length} 名` })),
          el('div', { class: 'card-body' },
            el('div', { class: 'page-sub',
              text: '開始日も期限も入っていないため、負荷に反映できていません。' }),
            el('div', { class: 'chip-row', style: { marginTop: '8px' } },
              ...unscheduled.map((entry) => el('span', { class: 'chip' },
                el('span', { text: `${entry.name} ${entry.count}件` }))))))
        : null);
  }

  function controls() {
    const projectSelect = el('select', {
      class: 'select', style: { maxWidth: '200px' },
      onChange: (event) => {
        const value = event.target.value;
        location.hash = value ? `#/p/${value}/workload` : '#/workload';
      },
    },
    option('', 'すべてのプロジェクト', !state.projectId),
    ...(data.projects || []).map((p) => option(p.id, p.name, state.projectId === p.id)));

    const weekSelect = el('select', {
      class: 'select', style: { maxWidth: '130px' },
      onChange: (event) => { state.weeks = Number(event.target.value); load(); },
    }, ...[4, 8, 12, 16].map((n) => option(n, `${n} 週間`, state.weeks === n)));

    return el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap' } },
      projectSelect, weekSelect);
  }

  function stat(label, value, tone) {
    return el('div', { class: 'card stat' },
      el('div', { class: 'k', text: label }),
      el('div', { class: `v ${tone}`.trim(), text: String(value) }));
  }

  function table(weeks, rows, capacity, showHours) {
    return el('table', { class: 'wl-table' },
      el('thead', {}, el('tr', {},
        el('th', { class: 'wl-name', text: '担当者' }),
        ...weeks.map((week) => el('th', {
          class: `wl-week${week.is_current ? ' current' : ''}`,
          // 祝日のある週は使える時間が減るので、その旨を出す
          title: (week.holidays || []).length
            ? `祝日・休業日 ${week.holidays.length} 日（この週に使えるのは ${week.capacity}h）`
            : null,
        }, el('span', { text: week.label }),
        (week.holidays || []).length
          ? el('span', { class: 'wl-holiday', text: `休${week.holidays.length}` })
          : null,
        week.is_current ? el('span', { class: 'wl-now', text: '今週' }) : null)),
        el('th', { class: 'wl-total', text: '合計' }))),
      el('tbody', {}, ...rows.map((row) => el('tr', {},
        el('td', { class: 'wl-name' },
          el('span', { class: 'avatar-stack' },
            avatar({ name: row.user_id ? row.name : '', avatar_color: row.avatar_color }, 'sm'),
            el('span', { text: row.name }))),
        ...row.cells.map((cell, i) =>
          cellNode(cell, weeks[i]?.capacity ?? capacity, showHours)),
        el('td', { class: 'wl-total' },
          showHours ? `${row.total_hours}h` : `${row.total_count}件`)))));
  }

  function cellNode(cell, capacity, showHours) {
    const ratio = showHours ? (cell.ratio ?? 0) : Math.min(1.4, cell.count / 5);
    const level = ratio >= 1 ? 'over' : ratio >= 0.8 ? 'high' : ratio > 0 ? 'ok' : 'idle';
    const label = showHours
      ? (cell.hours ? `${cell.hours}h` : '')
      : (cell.count ? `${cell.count}件` : '');
    const node = el('td', { class: `wl-cell ${level}` },
      el('span', { class: 'wl-value', text: label }),
      cell.count
        ? el('span', {
          class: 'wl-bar',
          style: { width: `${Math.min(100, Math.round(ratio * 100))}%` },
        })
        : null);
    if (cell.tasks.length) {
      node.title = cell.tasks.map((t) => `・${t.title}`).join('\n');
      node.style.cursor = 'pointer';
      node.addEventListener('click', () => {
        if (cell.tasks.length === 1) openTaskDetail(cell.tasks[0].id, { onChange: load });
        else toast(`${cell.count} 件: ${cell.tasks.map((t) => t.title).join('、')}`);
      });
    }
    return node;
  }

  await load();
}
