/* Project task list: hierarchical tree with inline status, drag & drop ordering.
 * 階層の組み替えは、行のドラッグ（PC）と行メニュー（スマホを含む）の両方からできる。 */
import { api } from '../api.js';
import { setHeader } from '../app.js';
import { store, STATUS_LABEL, IMPORTANCE_LABEL, CATEGORIES, category } from '../store.js';
import {
  avatar, clear, confirmDialog, debounce, dueClass, dueLabel, el, fill, formatDate, toast,
} from '../util.js';
import { openTaskForm } from './taskForm.js';
import { categoryChip } from './pickers.js';
import { openTaskDetail } from './taskDetail.js';
import { projectTabs } from './projectNav.js';
import { indentTarget, openParentPicker, outdentTarget, setParent } from './hierarchy.js';

const collapsedKey = (projectId) => `tm.collapsed.${projectId}`;

function loadCollapsed(projectId) {
  try {
    return new Set(JSON.parse(localStorage.getItem(collapsedKey(projectId)) || '[]'));
  } catch { return new Set(); }
}

function saveCollapsed(projectId, set) {
  try {
    localStorage.setItem(collapsedKey(projectId), JSON.stringify([...set]));
  } catch { /* private mode */ }
}

/** Build parent → children lists sorted by sort_order. */
export function buildTree(tasks) {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  const children = new Map();
  for (const task of tasks) {
    const key = byId.has(task.parent_id) ? task.parent_id : null;
    if (!children.has(key)) children.set(key, []);
    children.get(key).push(task);
  }
  for (const list of children.values()) {
    list.sort((a, b) => (a.sort_order - b.sort_order) || (a.id - b.id));
  }
  return { byId, children };
}

/** Depth-first list of {task, depth, hasChildren} honouring the collapsed set. */
export function flattenTree(tasks, collapsed = new Set(), filter = null) {
  const { children } = buildTree(tasks);
  const keep = filter ? new Set(matchingWithAncestors(tasks, filter)) : null;
  const out = [];
  const walk = (parentId, depth) => {
    for (const task of children.get(parentId) || []) {
      if (keep && !keep.has(task.id)) continue;
      const kids = (children.get(task.id) || []).filter((k) => !keep || keep.has(k.id));
      out.push({ task, depth, hasChildren: kids.length > 0 });
      if (kids.length && !collapsed.has(task.id)) walk(task.id, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

/** Ids of tasks matching the filter plus every ancestor, so the tree stays navigable. */
function matchingWithAncestors(tasks, filter) {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  const keep = new Set();
  for (const task of tasks) {
    if (!filter(task)) continue;
    let node = task;
    let guard = 0;
    while (node && guard < 20) {
      if (keep.has(node.id)) break;
      keep.add(node.id);
      node = byId.get(node.parent_id);
      guard += 1;
    }
  }
  return keep;
}

export async function render(container, route) {
  const projectId = route.projectId;
  let data = await api.projectTasks(projectId);
  const project = data.project;
  const collapsed = loadCollapsed(projectId);
  const state = {
    query: '', assignee: '', status: 'open', category: '', attention: false,
    selectedId: null,
    picked: new Set(),      // 一括編集で選んでいるタスク
  };

  const canEdit = store.canEdit(project);

  setHeader(project.name, [
    canEdit
      ? el('button', {
        class: 'btn', title: 'Excel や CSV からまとめて登録します',
        onClick: async () => {
          const { openImportDialog } = await import('./importTasks.js');
          if (await openImportDialog(project)) reload();
        },
      }, '⬆ 取り込み')
      : null,
    canEdit
      ? el('button', { class: 'btn btn-primary', onClick: () => addTask(null) }, '＋ タスク')
      : null,
  ]);

  const rowsHost = el('div', { class: 'task-rows' });
  const summary = el('div', { class: 'page-sub' });

  const searchInput = el('input', {
    class: 'input', type: 'search', placeholder: 'タスクを検索…',
    style: { maxWidth: '220px' },
    onInput: debounce((event) => { state.query = event.target.value.trim(); draw(); }, 200),
  });

  const assigneeFilter = el('select', {
    class: 'select', style: { maxWidth: '160px' },
    onChange: (event) => { state.assignee = event.target.value; draw(); },
  },
  el('option', { value: '' }, '担当者: すべて'),
  el('option', { value: 'me' }, '自分の担当'),
  el('option', { value: 'none' }, '未割当'),
  ...store.users.map((u) => el('option', { value: String(u.id) }, u.name)));

  const statusFilter = el('select', {
    class: 'select', style: { maxWidth: '150px' },
    onChange: (event) => { state.status = event.target.value; draw(); },
  },
  el('option', { value: 'open' }, '未完了のみ'),
  el('option', { value: 'all' }, 'すべての状態'),
  ...Object.entries(STATUS_LABEL).map(([value, label]) => el('option', { value }, label)));

  const categoryFilter = el('select', {
    class: 'select', style: { maxWidth: '170px' },
    onChange: (event) => { state.category = event.target.value; draw(); },
  },
  el('option', { value: '' }, 'カテゴリ: すべて'),
  el('option', { value: 'none' }, '未分類'),
  ...CATEGORIES.map((c) => el('option', { value: c.value }, `${c.icon} ${c.label}`)));

  const attentionToggle = el('label', { class: 'check' },
    el('input', {
      type: 'checkbox',
      onChange: (event) => { state.attention = event.target.checked; draw(); },
    }), el('span', { text: '要注意のみ' }));

  const toolbar = el('div', { class: 'toolbar' },
    searchInput, assigneeFilter, statusFilter, categoryFilter, attentionToggle,
    el('div', { class: 'spacer' }),
    canEdit
      ? el('button', {
        class: 'btn btn-sm', title: '定例タスクの設定',
        onClick: async () => {
          const { openRecurrenceManager } = await import('./recurrence.js');
          if (await openRecurrenceManager(project)) reload();
        },
      }, '🔁 定例')
      : null,
    el('button', {
      class: 'btn btn-sm', title: 'すべて展開',
      onClick: () => { collapsed.clear(); saveCollapsed(projectId, collapsed); draw(); },
    }, '⤢ 展開'),
    el('button', {
      class: 'btn btn-sm', title: 'すべて折りたたむ',
      onClick: () => {
        data.tasks.forEach((t) => { if (data.tasks.some((c) => c.parent_id === t.id)) collapsed.add(t.id); });
        saveCollapsed(projectId, collapsed);
        draw();
      },
    }, '⤡ 折りたたみ'));

  const bulkBar = el('div', { class: 'bulk-bar', hidden: true });

  const head = el('div', { class: 'tree-head' },
    el('div', { text: 'タスク' }), el('div', { text: 'カテゴリ' }), el('div', { text: '担当' }),
    el('div', { text: '状態' }), el('div', { text: '期限' }), el('div', { text: '進捗' }),
    el('div', {}));

  const alerts = el('div', {});

  fill(container,
    projectTabs(projectId, 'tasks'),
    el('div', { class: 'page-head' },
      el('div', { class: 'grow' },
        el('div', { class: 'page-sub' }, project.description || '　'), summary)),
    alerts,
    el('div', { class: 'card' }, toolbar, bulkBar, head,
      el('div', { class: 'card-body tight' }, rowsHost)));

  const reload = async () => {
    data = await api.projectTasks(projectId);
    draw();
  };

  function currentFilter() {
    const query = state.query.toLowerCase();
    return (task) => {
      if (query && !(`${task.title} ${task.description || ''}`.toLowerCase().includes(query))) {
        return false;
      }
      if (state.assignee === 'me' && task.assignee_id !== store.user.id) return false;
      else if (state.assignee === 'none' && task.assignee_id) return false;
      else if (state.assignee && !['me', 'none'].includes(state.assignee)
        && task.assignee_id !== Number(state.assignee)) return false;
      if (state.status === 'open' && task.status === 'done') return false;
      if (!['open', 'all'].includes(state.status) && task.status !== state.status) return false;
      if (state.category === 'none' && task.category) return false;
      else if (state.category && state.category !== 'none' && task.category !== state.category) {
        return false;
      }
      if (state.attention && !needsAttention(task)) return false;
      return true;
    };
  }

  /** Blocking others, blocked, on the critical path, or already late. */
  function needsAttention(task) {
    if (task.status === 'done') return false;
    return Boolean(task.blocks_open || task.is_blocked || task.is_critical
      || task.status === 'blocked' || dueClass(task.due_date, task.status) === 'overdue');
  }

  function draw() {
    const filter = currentFilter();
    const active = state.query || state.assignee || state.category || state.attention
      || state.status !== 'all';
    const rows = flattenTree(data.tasks, collapsed, active ? filter : null);
    clear(rowsHost);
    if (rows.length === 0) {
      rowsHost.append(el('div', { class: 'empty' },
        el('div', { class: 'big', text: '📋' }),
        data.tasks.length === 0 ? 'まだタスクがありません' : '条件に一致するタスクがありません',
        canEdit && data.tasks.length === 0
          ? el('div', { style: { marginTop: '12px' } },
            el('button', { class: 'btn btn-primary', onClick: () => addTask(null) }, '最初のタスクを追加'))
          : null));
    } else {
      rows.forEach((row) => rowsHost.append(taskRow(row)));
    }
    drawAlerts();
    drawBulkBar();
    const done = data.tasks.filter((t) => t.status === 'done').length;
    const overdue = data.tasks.filter((t) => dueClass(t.due_date, t.status) === 'overdue').length;
    fill(summary, 
      `全 ${data.tasks.length} 件 / 完了 ${done} 件`,
      overdue ? el('span', { class: 'badge overdue', style: { marginLeft: '8px' }, text: `期限超過 ${overdue}` }) : null,
      el('span', { style: { marginLeft: '8px' }, text: `表示 ${rows.length} 件` }));
  }

  function drawAlerts() {
    const conflicts = data.conflicts || [];
    if (!conflicts.length) { fill(alerts); return; }
    fill(alerts, el('div', { class: 'warn-box danger' },
      el('strong', { text: `⚠ 依存関係と日程が矛盾しています（${conflicts.length} 件）` }),
      el('ul', {}, ...conflicts.slice(0, 5).map((c) => el('li', {},
        `「${c.depends_on_title}」の期限が「${c.task_title}」の開始日より `
        + `${c.overlap_days} 日あとになっています`))),
      conflicts.length > 5
        ? el('div', { class: 'hint', text: `ほか ${conflicts.length - 5} 件` })
        : null));
  }

  function taskRow({ task, depth, hasChildren }) {
    const isDone = task.status === 'done';
    const twisty = el('button', {
      class: `twisty${hasChildren ? '' : ' leaf'}${collapsed.has(task.id) ? '' : ' open'}`,
      title: collapsed.has(task.id) ? '展開' : '折りたたむ',
      onClick: (event) => {
        event.stopPropagation();
        if (collapsed.has(task.id)) collapsed.delete(task.id); else collapsed.add(task.id);
        saveCollapsed(projectId, collapsed);
        draw();
      },
    }, '▶');

    const progress = task.child_count
      ? (task.rollup_progress ?? task.progress) : task.progress;

    const row = el('div', {
      class: [
        'task-row', isDone ? 'is-done' : '', hasChildren ? 'is-parent' : '',
        task.is_critical && !isDone ? 'is-critical' : '',
        task.is_blocked ? 'is-blocked' : '',
        state.selectedId === task.id ? 'selected' : '',
      ].filter(Boolean).join(' '),
      draggable: canEdit ? 'true' : null,
      dataset: { id: String(task.id) },
      onClick: () => {
        state.selectedId = task.id;
        openTaskDetail(task.id, { onChange: reload });
      },
    },
    el('div', { class: 'task-main', style: { paddingLeft: `${depth * 16}px` } },
      canEdit ? el('input', {
        type: 'checkbox', class: 'task-pick', title: 'まとめて編集する対象に選ぶ',
        checked: state.picked.has(task.id) ? true : null,
        onClick: (event) => event.stopPropagation(),
        onChange: (event) => {
          if (event.target.checked) state.picked.add(task.id);
          else state.picked.delete(task.id);
          drawBulkBar();
        },
      }) : null,
      canEdit ? el('span', {
        class: 'drag-handle',
        title: 'ドラッグで並べ替え。行の中央に重ねるとその子タスクになります',
      }, '⠿') : null,
      twisty,
      importanceMark(task.priority),
      task.is_milestone ? el('span', { class: 'milestone-mark', title: 'マイルストーン' }, '◆') : null,
      el('span', { class: 'task-title', text: task.title, title: task.title }),
      ...dependencyBadges(task),
      el('span', { class: 'task-meta-icons' },
        task.comment_count ? el('span', { title: 'コメント' }, `💬${task.comment_count}`) : null,
        task.attachment_count ? el('span', { title: '添付' }, `📎${task.attachment_count}`) : null,
        task.child_count ? el('span', { title: '子タスク' }, `${task.leaf_done}/${task.leaf_total}`) : null)),
    el('div', { class: 'cell-hide-sm' },
      task.category ? categoryChip(task.category, { small: true }) : el('span', { class: 'cell-mut', text: '—' })),
    el('div', { class: 'cell-hide-sm' },
      task.assignee_id
        ? el('span', { class: 'avatar-stack' }, avatar({
          name: task.assignee_name, avatar_color: task.assignee_color,
        }, 'sm'), el('span', { class: 'cell-mut', text: task.assignee_name }))
        : el('span', { class: 'cell-mut', text: '未割当' })),
    el('div', { class: 'cell-hide-sm' },
      el('span', { class: `badge ${task.status}`, text: STATUS_LABEL[task.status] })),
    el('div', { class: `cell-mut cell-due cell-hide-sm ${dueClass(task.due_date, task.status)}` },
      formatDate(task.due_date),
      dueLabel(task.due_date, task.status)
        ? el('div', { style: { fontSize: '11px' }, text: dueLabel(task.due_date, task.status) })
        : null),
    el('div', { class: 'cell-hide-sm' },
      el('div', { class: `progress${progress >= 100 ? ' done' : ''}` },
        el('i', { style: { width: `${progress}%` } })),
      el('div', { class: 'cell-mut', style: { fontSize: '11px' }, text: `${progress}%` })),
    el('div', {},
      canEdit
        ? el('button', {
          class: 'icon-btn', title: 'メニュー',
          onClick: (event) => { event.stopPropagation(); rowMenu(event.currentTarget, task); },
        }, '⋯')
        : null),
    el('div', { class: 'task-sub' },
      el('span', { class: `badge ${task.status}`, text: STATUS_LABEL[task.status] }),
      task.category ? categoryChip(task.category, { small: true }) : null,
      ...dependencyBadges(task),
      task.assignee_id ? el('span', { text: `👤 ${task.assignee_name}` }) : null,
      task.due_date
        ? el('span', { class: `cell-due ${dueClass(task.due_date, task.status)}`,
          text: `📅 ${formatDate(task.due_date)}` })
        : null,
      el('span', { text: `${progress}%` })));

    if (canEdit) attachDragHandlers(row, task);
    return row;
  }

  /** 高・最重要だけ目印を出す。中／低は無印にしてノイズを減らす。 */
  function importanceMark(priority) {
    if (priority < 2) return null;
    const urgent = priority >= 3;
    return el('span', {
      class: `prio prio-${priority}`,
      title: `重要度: ${IMPORTANCE_LABEL[priority]}`,
      style: { flex: 'none' },
    }, urgent ? '!!' : '!');
  }

  function dependencyBadges(task) {
    const out = [];
    if (task.blocks_open > 0) {
      out.push(el('span', {
        class: 'badge blocking', style: { flex: 'none' },
        title: `このタスクが終わらないと ${task.blocks_open} 件が進められません`,
      }, `⛔ ${task.blocks_open}`));
    }
    if (task.is_blocked) {
      out.push(el('span', {
        class: 'badge blocked-by', style: { flex: 'none' },
        title: `先行タスク ${task.blocked_by_open} 件が未完了です`,
      }, `⏳ 待ち`));
    }
    if (task.is_critical && task.status !== 'done') {
      out.push(el('span', {
        class: 'badge critical', style: { flex: 'none' },
        title: 'クリティカルパス上のタスクです。遅れると全体が遅れます',
      }, 'CP'));
    }
    return out;
  }

  /* ---- drag & drop ---- */
  let dragId = null;

  function attachDragHandlers(row, task) {
    row.addEventListener('dragstart', (event) => {
      dragId = task.id;
      row.classList.add('dragging');
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', String(task.id));
    });
    row.addEventListener('dragend', () => {
      dragId = null;
      row.classList.remove('dragging');
      rowsHost.querySelectorAll('.task-row').forEach((node) =>
        node.classList.remove('drop-before', 'drop-after', 'drop-into'));
    });
    row.addEventListener('dragover', (event) => {
      if (dragId === null || dragId === task.id) return;
      event.preventDefault();
      const rect = row.getBoundingClientRect();
      const offset = (event.clientY - rect.top) / rect.height;
      row.classList.remove('drop-before', 'drop-after', 'drop-into');
      row.classList.add(offset < 0.28 ? 'drop-before' : offset > 0.72 ? 'drop-after' : 'drop-into');
    });
    row.addEventListener('dragleave', () => {
      row.classList.remove('drop-before', 'drop-after', 'drop-into');
    });
    row.addEventListener('drop', async (event) => {
      event.preventDefault();
      if (dragId === null || dragId === task.id) return;
      const mode = row.classList.contains('drop-into') ? 'into'
        : row.classList.contains('drop-before') ? 'before' : 'after';
      row.classList.remove('drop-before', 'drop-after', 'drop-into');
      await moveTask(dragId, task.id, mode);
    });
  }

  async function moveTask(sourceId, targetId, mode) {
    const source = data.tasks.find((t) => t.id === sourceId);
    const target = data.tasks.find((t) => t.id === targetId);
    if (!source || !target) return;
    if (isAncestor(sourceId, targetId)) {
      toast('子タスクの中には移動できません', 'error');
      return;
    }
    const newParent = mode === 'into' ? target.id : target.parent_id;
    const { children } = buildTree(data.tasks);
    const siblings = (children.get(newParent) || []).filter((t) => t.id !== sourceId);
    let index = siblings.length;
    if (mode !== 'into') {
      const at = siblings.findIndex((t) => t.id === target.id);
      index = mode === 'before' ? Math.max(at, 0) : at + 1;
    }
    siblings.splice(index, 0, source);
    const items = siblings.map((task, i) => ({
      id: task.id, parent_id: newParent, sort_order: (i + 1) * 10,
    }));
    try {
      await api.post('/api/tasks/reorder', { project_id: projectId, items });
      if (mode === 'into') collapsed.delete(target.id);
      await reload();
    } catch (error) { toast(error.message, 'error'); }
  }

  function isAncestor(ancestorId, nodeId) {
    const byId = new Map(data.tasks.map((t) => [t.id, t]));
    let node = byId.get(nodeId);
    let guard = 0;
    while (node && guard < 25) {
      if (node.id === ancestorId) return true;
      node = byId.get(node.parent_id);
      guard += 1;
    }
    return false;
  }

  /* ---- row menu ---- */
  function rowMenu(anchor, task) {
    const menu = el('div', {
      class: 'card',
      style: {
        position: 'absolute', zIndex: '120', minWidth: '190px', padding: '5px',
        boxShadow: 'var(--shadow-lg)',
      },
    },
    menuItem('👁 詳細を開く', () => openTaskDetail(task.id, { onChange: reload })),
    menuItem('＋ 子タスクを追加', () => addTask(task.id)),
    ...hierarchyMenuItems(task, menuItem),
    menuItem('✏️ 編集', async () => {
      const saved = await openTaskForm({
        project, task, tasks: data.tasks, deps: data.deps, members: data.members });
      if (saved) reload();
    }),
    menuItem('🔁 定例にする', async () => {
      const { openRecurrenceForm } = await import('./recurrence.js');
      if (await openRecurrenceForm(project, null, task)) {
        toast('定例タスクとして登録しました', 'ok');
      }
    }),
    menuItem(task.status === 'done' ? '↩︎ 未完了に戻す' : '✓ 完了にする', async () => {
      await api.patch(`/api/tasks/${task.id}`, { status: task.status === 'done' ? 'doing' : 'done' });
      reload();
    }),
    menuItem('🗑 削除', async () => {
      const { confirmDialog } = await import('../util.js');
      if (!await confirmDialog(`「${task.title}」を削除しますか？（子タスクも削除されます）`,
        { danger: true, okLabel: '削除する' })) return;
      await api.del(`/api/tasks/${task.id}`);
      reload();
    }, true));

    const rect = anchor.getBoundingClientRect();
    menu.style.top = `${window.scrollY + rect.bottom + 4}px`;
    menu.style.left = `${Math.max(8, window.scrollX + rect.right - 190)}px`;
    document.body.appendChild(menu);
    const dismiss = (event) => {
      if (menu.contains(event.target)) return;
      menu.remove();
      document.removeEventListener('mousedown', dismiss);
    };
    setTimeout(() => document.addEventListener('mousedown', dismiss), 0);

    function menuItem(label, action, danger = false) {
      return el('button', {
        class: 'nav-item',
        style: danger ? { color: 'var(--danger)' } : null,
        onClick: () => { menu.remove(); action(); },
      }, label);
    }
  }

  /* ---- まとめて編集 ---- */

  function drawBulkBar() {
    // 画面から消えたタスクの選択は残さない
    const visible = new Set(data.tasks.map((t) => t.id));
    for (const id of [...state.picked]) if (!visible.has(id)) state.picked.delete(id);

    const count = state.picked.size;
    bulkBar.hidden = count === 0;
    if (!count) { fill(bulkBar); return; }

    const picked = () => [...state.picked];
    const run = async (payload, label) => {
      try {
        const result = await api.post('/api/tasks/bulk', { ids: picked(), ...payload });
        toast(`${result.updated ?? result.deleted} 件を${label}`, 'ok');
        state.picked.clear();
        await reload();
      } catch (error) { toast(error.message, 'error'); }
    };

    const assignee = el('select', { class: 'select' },
      el('option', { value: '' }, '担当者を変更…'),
      el('option', { value: 'none' }, '未割当にする'),
      ...(data.members || store.users).map((u) => el('option', { value: String(u.id) }, u.name)));
    assignee.addEventListener('change', () => {
      if (!assignee.value) return;
      const value = assignee.value === 'none' ? null : Number(assignee.value);
      assignee.value = '';
      run({ assignee_id: value }, '更新しました');
    });

    const status = el('select', { class: 'select' },
      el('option', { value: '' }, '状態を変更…'),
      ...Object.entries(STATUS_LABEL).map(([value, label]) => el('option', { value }, label)));
    status.addEventListener('change', () => {
      if (!status.value) return;
      const value = status.value;
      status.value = '';
      run({ status: value }, '更新しました');
    });

    const category = el('select', { class: 'select' },
      el('option', { value: '' }, 'カテゴリを変更…'),
      el('option', { value: 'none' }, '未分類にする'),
      ...CATEGORIES.map((c) => el('option', { value: c.value }, `${c.icon} ${c.label}`)));
    category.addEventListener('change', () => {
      if (!category.value) return;
      const value = category.value === 'none' ? '' : category.value;
      category.value = '';
      run({ category: value }, '更新しました');
    });

    const dueInput = el('input', { class: 'input', type: 'date', style: { maxWidth: '150px' } });
    dueInput.addEventListener('change', () => {
      if (!dueInput.value) return;
      run({ due_date: dueInput.value }, '期限をそろえました');
    });

    fill(bulkBar,
      el('span', { class: 'bulk-count', text: `${count} 件を選択中` }),
      assignee, status, category,
      el('span', { class: 'label', style: { margin: 0 }, text: '期限' }), dueInput,
      el('button', {
        class: 'btn btn-sm', title: '選んだタスクの開始日と期限をまとめてずらします',
        onClick: () => shiftDialog(run),
      }, '📆 日程をずらす'),
      el('div', { class: 'spacer' }),
      el('button', {
        class: 'btn btn-sm btn-danger',
        onClick: async () => {
          const { confirmDialog } = await import('../util.js');
          if (!await confirmDialog(
            `選択した ${count} 件を削除します。\n子タスクもまとめて削除され、元に戻せません。`,
            { danger: true, okLabel: '削除する' })) return;
          run({ action: 'delete' }, '削除しました');
        },
      }, '🗑 削除'),
      el('button', {
        class: 'btn btn-sm',
        onClick: () => { state.picked.clear(); draw(); },
      }, '選択を解除'));
  }

  async function shiftDialog(run) {
    const { openModal } = await import('../util.js');
    const days = el('input', { class: 'input', type: 'number', value: '7', step: '1' });
    const chosen = await openModal({
      title: '日程をまとめてずらす',
      build: () => el('div', {},
        el('p', { class: 'page-sub',
          text: '選んだタスクの開始日と期限を、同じ日数だけ前後に動かします。'
            + 'マイナスを入れると前倒しです。日付が入っていないタスクはそのままです。' }),
        el('div', { class: 'field' }, el('label', { text: 'ずらす日数' }), days)),
      footer: (close) => [
        el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
        el('button', {
          class: 'btn btn-primary', onClick: () => close(Number(days.value)),
        }, 'ずらす'),
      ],
    });
    if (chosen) run({ action: 'shift', days: chosen }, 'ずらしました');
  }

  /** 階層を組み替えるメニュー項目。タッチ端末ではここが唯一の手段になる。 */
  function hierarchyMenuItems(task, menuItem) {
    const indentTo = indentTarget(data.tasks, task);
    const outdentTo = outdentTarget(data.tasks, task);
    const items = [];
    if (indentTo) {
      items.push(menuItem(`⇥ 「${ellipsis(indentTo.title)}」の子にする`,
        () => reparent(task, indentTo.id)));
    }
    if (outdentTo) {
      const grandParent = data.tasks.find((t) => t.id === outdentTo.parent_id);
      items.push(menuItem(
        grandParent ? `⇤ 「${ellipsis(grandParent.title)}」の子にする` : '⇤ トップレベルに出す',
        () => reparent(task, outdentTo.parent_id ?? null)));
    }
    items.push(menuItem('⤴ 親タスクを変更…', async () => {
      const chosen = await openParentPicker(task, data.tasks);
      if (chosen === undefined) return;
      await reparent(task, chosen);
    }));
    return items;
  }

  async function reparent(task, parentId) {
    if (!await setParent(task, parentId, data.tasks)) return;
    if (parentId) collapsed.delete(parentId);
    saveCollapsed(projectId, collapsed);
    toast(parentId ? '階層を移動しました' : 'トップレベルに移動しました', 'ok');
    await reload();
  }

  function ellipsis(text, limit = 16) {
    return text.length > limit ? `${text.slice(0, limit)}…` : text;
  }

  async function addTask(parentId) {
    const saved = await openTaskForm({
      project, parentId, tasks: data.tasks, deps: data.deps, members: data.members });
    if (saved) {
      if (parentId) collapsed.delete(parentId);
      reload();
    }
  }

  draw();
}
