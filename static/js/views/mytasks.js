/* Cross-project task list with filters. */
import { api } from '../api.js';
import { setHeader } from '../app.js';
import { store, STATUS_LABEL, CATEGORIES } from '../store.js';
import {
  avatar, clear, debounce, dueClass, dueLabel, el, fill, formatDate,
} from '../util.js';
import { openTaskDetail } from './taskDetail.js';
import { categoryChip } from './pickers.js';

export async function render(container) {
  const state = {
    scope: 'mine', status: 'open', q: '', project_id: '', category: '',
    overdue: false, milestone: false, blocked: false,
  };

  setHeader('マイタスク');

  const listHost = el('div', {});
  const summary = el('div', { class: 'page-sub' });

  const search = el('input', {
    class: 'input', type: 'search', placeholder: 'キーワード検索…',
    style: { maxWidth: '220px' },
    onInput: debounce((event) => { state.q = event.target.value.trim(); load(); }, 250),
  });
  const scopeSeg = el('div', { class: 'seg' },
    segButton('自分の担当', 'mine'), segButton('すべて', 'all'));
  const statusSelect = el('select', {
    class: 'select', style: { maxWidth: '150px' },
    onChange: (event) => { state.status = event.target.value; load(); },
  },
  el('option', { value: 'open' }, '未完了のみ'),
  el('option', { value: '' }, 'すべての状態'),
  ...Object.entries(STATUS_LABEL).map(([value, label]) => el('option', { value }, label)));
  const projectSelect = el('select', {
    class: 'select', style: { maxWidth: '180px' },
    onChange: (event) => { state.project_id = event.target.value; load(); },
  },
  el('option', { value: '' }, 'すべてのプロジェクト'),
  ...store.projects.filter((p) => !p.archived).map((p) => el('option', { value: p.id }, p.name)));
  const categoryFilter = el('select', {
    class: 'select', style: { maxWidth: '170px' },
    onChange: (event) => { state.category = event.target.value; load(); },
  },
  el('option', { value: '' }, 'カテゴリ: すべて'),
  ...CATEGORIES.map((c) => el('option', { value: c.value }, `${c.icon} ${c.label}`)));
  const blockedCheck = el('label', { class: 'check' },
    el('input', {
      type: 'checkbox',
      onChange: (event) => { state.blocked = event.target.checked; load(); },
    }), el('span', { text: '先行待ちのみ' }));
  const overdueCheck = el('label', { class: 'check' },
    el('input', {
      type: 'checkbox',
      onChange: (event) => { state.overdue = event.target.checked; load(); },
    }), el('span', { text: '期限超過のみ' }));
  const milestoneCheck = el('label', { class: 'check' },
    el('input', {
      type: 'checkbox',
      onChange: (event) => { state.milestone = event.target.checked; load(); },
    }), el('span', { text: 'マイルストーンのみ' }));

  function segButton(label, value) {
    return el('button', {
      class: state.scope === value ? 'active' : '',
      onClick: (event) => {
        state.scope = value;
        [...event.currentTarget.parentNode.children].forEach((b) => b.classList.remove('active'));
        event.currentTarget.classList.add('active');
        load();
      },
    }, label);
  }

  fill(container, 
    el('div', { class: 'page-head' }, el('div', { class: 'grow' }, summary)),
    el('div', { class: 'card' },
      el('div', { class: 'toolbar' },
        scopeSeg, search, statusSelect, projectSelect, categoryFilter,
        overdueCheck, blockedCheck, milestoneCheck),
      el('div', { class: 'card-body tight' }, listHost)));

  async function load() {
    fill(listHost, el('div', { class: 'empty', text: '読み込み中…' }));
    const data = await api.tasks({
      scope: state.scope, status: state.status, q: state.q,
      project_id: state.project_id,
      category: state.category,
      blocked: state.blocked ? 1 : '',
      overdue: state.overdue ? 1 : '',
      milestone: state.milestone ? 1 : '',
      limit: 500,
    });
    const tasks = data.tasks;
    summary.textContent = `${tasks.length} 件`;
    clear(listHost);
    if (tasks.length === 0) {
      listHost.append(el('div', { class: 'empty' },
        el('div', { class: 'big', text: '🔍' }), '条件に一致するタスクがありません'));
      return;
    }
    for (const task of tasks) {
      listHost.append(row(task));
    }
  }

  function row(task) {
    return el('div', {
      class: `task-row${task.status === 'done' ? ' is-done' : ''}`,
      onClick: () => openTaskDetail(task.id, { onChange: load }),
    },
    el('div', { class: 'task-main' },
      el('span', {
        class: 'nav-dot',
        style: { background: task.project_color, width: '8px', height: '8px' },
        title: task.project_name,
      }),
      task.is_milestone ? el('span', { class: 'milestone-mark' }, '◆') : null,
      el('span', { class: 'task-title', text: task.title, title: task.title }),
      task.blocks_direct
        ? el('span', { class: 'badge blocking', style: { flex: 'none' },
          title: `後続 ${task.blocks_direct} 件` }, `⛔ ${task.blocks_direct}`)
        : null,
      task.blocked_by_open && task.status !== 'done'
        ? el('span', { class: 'badge blocked-by', style: { flex: 'none' },
          title: `先行 ${task.blocked_by_open} 件が未完了` }, '⏳ 待ち')
        : null,
      el('span', { class: 'task-meta-icons' },
        task.comment_count ? el('span', {}, `💬${task.comment_count}`) : null,
        task.attachment_count ? el('span', {}, `📎${task.attachment_count}`) : null)),
    el('div', { class: 'cell-hide-sm' },
      task.assignee_id
        ? el('span', { class: 'avatar-stack' },
          avatar({ name: task.assignee_name, avatar_color: task.assignee_color }, 'sm'),
          el('span', { class: 'cell-mut', text: task.assignee_name }))
        : el('span', { class: 'cell-mut', text: '未割当' })),
    el('div', { class: 'cell-hide-sm' },
      el('span', { class: `badge ${task.status}`, text: STATUS_LABEL[task.status] })),
    el('div', { class: `cell-mut cell-due cell-hide-sm ${dueClass(task.due_date, task.status)}` },
      formatDate(task.due_date),
      dueLabel(task.due_date, task.status)
        ? el('div', { style: { fontSize: '11px' }, text: dueLabel(task.due_date, task.status) })
        : null),
    el('div', { class: 'cell-hide-sm' },
      el('div', { class: `progress${task.progress >= 100 ? ' done' : ''}` },
        el('i', { style: { width: `${task.progress}%` } })),
      el('div', { class: 'cell-mut', style: { fontSize: '11px' }, text: `${task.progress}%` })),
    el('div', { class: 'cell-hide-sm' },
      task.category ? categoryChip(task.category, { small: true })
        : el('span', { class: 'cell-mut', text: '—' })),
    el('div', { class: 'cell-mut cell-hide-sm', style: { fontSize: '11px' },
      text: task.project_name }),
    el('div', { class: 'task-sub' },
      el('span', { class: `badge ${task.status}`, text: STATUS_LABEL[task.status] }),
      task.category ? categoryChip(task.category, { small: true }) : null,
      el('span', { text: task.project_name }),
      task.due_date
        ? el('span', { class: `cell-due ${dueClass(task.due_date, task.status)}`,
          text: `📅 ${formatDate(task.due_date)}` })
        : null,
      el('span', { text: `${task.progress}%` })));
  }

  await load();
}
