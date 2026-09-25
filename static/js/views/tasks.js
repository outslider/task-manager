/* Project task list: hierarchical tree with inline status, drag & drop ordering.
 * 階層の組み替えは、行のドラッグ（PC）と行メニュー（スマホを含む）の両方からできる。 */
import { api } from '../api.js';
import { setHeader } from '../app.js';
import {
  store, STATUS_LABEL, IMPORTANCE_LABEL, CATEGORIES, category, markerChar,
} from '../store.js';
import {
  avatar, clear, confirmDialog, debounce, dueClass, dueLabel, el, fill, formatDate, popupMenu, toast,
} from '../util.js';
import { iconLabel } from '../icons.js';
import { HEADING_LEVEL_LABEL, headingLevel, sectionize } from '../outline.js';
import { aiMark } from '../ai.js';
import { openTaskForm } from './taskForm.js';
import { categoryChip } from './pickers.js';
import { openTaskDetail } from './taskDetail.js';
import { projectTabs } from './projectNav.js';
import { indentTarget, openParentPicker, outdentTarget, setParent, siblingsOf }
  from './hierarchy.js';

const collapsedKey = (projectId) => `tm.collapsed.${projectId}`;

/** 折りたたみ状態はガント画面と共有する（同じプロジェクトなら同じ見え方にする）。 */
export function loadCollapsed(projectId) {
  try {
    return new Set(JSON.parse(localStorage.getItem(collapsedKey(projectId)) || '[]'));
  } catch { return new Set(); }
}

export function saveCollapsed(projectId, set) {
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

// 見出しで区切る計算はガントと共通なので outline.js に置いてある
export { sectionize } from '../outline.js';

/** Depth-first list of {task, depth, hasChildren} honouring the collapsed set. */
export function flattenTree(tasks, collapsed = new Set(), filter = null) {
  const { children } = buildTree(tasks);
  const keep = filter ? new Set(matchingWithAncestors(tasks, filter)) : null;
  const out = [];
  // outline は、その行を囲む見出しの数（親の分も足し込む）。字下げに使う
  const walk = (parentId, depth, base) => {
    for (const entry of sectionize(children.get(parentId) || [], collapsed, keep)) {
      const { task } = entry;
      const outline = base + entry.outline;
      if (entry.heading) {
        out.push({ task, depth, outline, heading: true, level: entry.level,
          count: entry.count, collapsed: collapsed.has(task.id) });
        continue;
      }
      const kids = sectionize(children.get(task.id) || [], new Set(), keep);
      // hasChildren はたためるか（見出しだけでも）。isParent は本当に子タスクを持つか
      out.push({ task, depth, outline, hasChildren: kids.length > 0,
        isParent: kids.some((k) => !k.heading) });
      if (kids.length && !collapsed.has(task.id)) walk(task.id, depth + 1, outline);
    }
  };
  walk(null, 0, 0);
  return out;
}

/** Ids of tasks matching the filter plus every ancestor, so the tree stays navigable. */
function matchingWithAncestors(tasks, filter) {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  const keep = new Set();
  for (const task of tasks) {
    // 見出しは状態を持たないので「未完了」などに当たってしまう。中身で判断する
    if (task.is_heading || !filter(task)) continue;
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
      }, ...iconLabel('upload', '取り込み'))
      : null,
    canEdit
      ? el('button', {
        class: 'btn', title: '会議メモを貼り付けて、やることをまとめて登録します',
        onClick: async () => {
          const { openMemoDialog } = await import('./memoTasks.js');
          if (await openMemoDialog(project)) reload();
        },
      }, ...iconLabel('note', 'メモから'), aiMark())
      : null,
    canEdit
      ? el('button', {
        class: 'btn', title: '取っておいた一式から、まとめて起こします',
        onClick: async () => {
          const { openTemplates } = await import('./templates.js');
          await openTemplates({ project, onApplied: reload });
        },
      }, ...iconLabel('blocks', '雛形'))
      : null,
    canEdit
      ? el('button', {
        class: 'btn', title: '区切りの見出しを足します（作業ではないので件数には入りません）',
        onClick: () => addHeading(null),
      }, ...iconLabel('list', '見出し'))
      : null,
    canEdit
      ? el('button', { class: 'btn btn-primary', onClick: () => addTask(null) }, ...iconLabel('plus', 'タスク'))
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
      }, ...iconLabel('repeat', '定例'))
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
  const filterNotice = el('div', {});

  fill(container,
    projectTabs(projectId, 'tasks'),
    el('div', { class: 'page-head' },
      el('div', { class: 'grow' }, summary)),
    alerts, filterNotice,
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
        !data.tasks.some((t) => !t.is_heading) && !state.query
          ? 'まだタスクがありません' : '条件に一致するタスクがありません',
        canEdit && data.tasks.length === 0
          ? el('div', { style: { marginTop: '12px' } },
            el('button', { class: 'btn btn-primary', onClick: () => addTask(null) }, '最初のタスクを追加'))
          : null));
    } else {
      rows.forEach((row) => rowsHost.append(row.heading ? headingRow(row) : taskRow(row)));
    }
    drawAlerts();
    drawBulkBar();
    // 見出しは作業ではないので、件数には入れない
    const work = data.tasks.filter((t) => !t.is_heading);
    const shownWork = rows.filter((r) => !r.heading).length;
    const done = work.filter((t) => t.status === 'done').length;
    const overdue = work.filter((t) => dueClass(t.due_date, t.status) === 'overdue').length;
    const hidden = work.length - shownWork;
    fill(summary,
      `全 ${work.length} 件 / 完了 ${done} 件`,
      overdue ? el('span', { class: 'badge overdue', style: { marginLeft: '8px' }, text: `期限超過 ${overdue}` }) : null,
      el('span', { style: { marginLeft: '8px' }, text: `表示 ${shownWork} 件` }));
    drawFilterNotice(hidden);
  }

  /** 絞り込みで隠れている件数を知らせる。「作ったのに出てこない」を防ぐため。 */
  function drawFilterNotice(hidden) {
    const active = [];
    if (state.status === 'open') active.push('未完了のみ');
    else if (state.status !== 'all') active.push(STATUS_LABEL[state.status]);
    if (state.query) active.push(`「${state.query}」で検索`);
    if (state.assignee === 'me') active.push('自分の担当のみ');
    else if (state.assignee === 'none') active.push('未割当のみ');
    else if (state.assignee) active.push(`担当 ${store.userName(Number(state.assignee))}`);
    if (state.category === 'none') active.push('未分類のみ');
    else if (state.category) active.push(category(state.category).label);
    if (state.attention) active.push('要注意のみ');

    if (!hidden || !active.length) { fill(filterNotice); return; }
    fill(filterNotice, el('div', { class: 'filter-notice' },
      el('span', { text: `${active.join(' / ')} で絞り込み中 — ${hidden} 件を隠しています` }),
      el('button', {
        class: 'btn btn-sm',
        onClick: () => {
          state.status = 'all';
          state.query = '';
          state.assignee = '';
          state.category = '';
          state.attention = false;
          searchInput.value = '';
          statusFilter.value = 'all';
          assigneeFilter.value = '';
          categoryFilter.value = '';
          attentionToggle.querySelector('input').checked = false;
          draw();
        },
      }, 'すべて表示')));
  }

  /**
   * 矛盾の一覧は長くなって一覧の邪魔になるので、既定では 1 行に畳んでおく。
   * 開いたかどうかは画面を切り替えても覚えておく。
   */
  function drawAlerts() {
    const conflicts = data.conflicts || [];
    if (!conflicts.length) { fill(alerts); return; }
    const open = localStorage.getItem('tm.tasks.conflictsOpen') === '1';
    const head = el('button', {
      class: 'warn-fold', type: 'button',
      onClick: () => {
        localStorage.setItem('tm.tasks.conflictsOpen', open ? '0' : '1');
        drawAlerts();
      },
    },
    el('span', { class: 'fold-mark', text: open ? '▼' : '▶' }),
    el('strong', { text: `⚠ 依存関係と日程が矛盾しています（${conflicts.length} 件）` }),
    el('span', { class: 'hint', text: open ? '' : 'クリックで内訳を表示' }));
    fill(alerts, el('div', { class: 'warn-box danger' }, head,
      open
        ? el('div', {},
          el('ul', {}, ...conflicts.slice(0, 5).map((c) => el('li', {},
            `「${c.depends_on_title}」の期限が「${c.task_title}」の開始日より `
            + `${c.overlap_days} 日あとになっています`))),
          conflicts.length > 5
            ? el('div', { class: 'hint', text: `ほか ${conflicts.length - 5} 件` })
            : null)
        : null));
  }

  function taskRow({ task, depth, hasChildren, isParent = hasChildren, outline = 0 }) {
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
        'task-row', isDone ? 'is-done' : '', isParent ? 'is-parent' : '',
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
    el('div', { class: 'task-main', style: { paddingLeft: `${depth * 16 + outline * 14}px` } },
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
      task.is_milestone
        ? el('span', { class: 'milestone-mark', title: 'マイルストーン' }, markerChar(task))
        : null,
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
    // 依存のバッジはタイトル行に出ているので、ここでは繰り返さない
    el('div', { class: 'task-sub' },
      el('span', { class: `badge ${task.status}`, text: STATUS_LABEL[task.status] }),
      task.category ? categoryChip(task.category, { small: true }) : null,
      task.assignee_id ? el('span', { text: `👤 ${task.assignee_name}` }) : null,
      task.due_date
        ? el('span', { class: `cell-due ${dueClass(task.due_date, task.status)}`,
          text: `📅 ${formatDate(task.due_date)}` })
        : null,
      el('span', { text: `${progress}%` })));

    if (canEdit) attachDragHandlers(row, task);
    return row;
  }

  /**
   * 見出しの行。区切りの帯として描き、押すと次の見出しまでをたたむ。
   * 状態・担当・期限・進捗の欄は持たないので、帯を横いっぱいに伸ばす。
   */
  function headingRow({ task, depth, count, level = 1, outline = 0, collapsed: folded }) {
    const row = el('div', {
      class: `task-row is-heading level-${level}`,
      draggable: canEdit ? 'true' : null,
      dataset: { id: String(task.id) },
      title: folded ? 'クリックで開く' : 'クリックでたたむ',
      onClick: () => {
        if (collapsed.has(task.id)) collapsed.delete(task.id); else collapsed.add(task.id);
        saveCollapsed(projectId, collapsed);
        draw();
      },
    },
    el('div', { class: 'task-main heading-main',
      style: { paddingLeft: `${depth * 16 + outline * 14}px` } },
      canEdit ? el('span', {
        class: 'drag-handle', title: 'ドラッグで動かせます',
        onClick: (event) => event.stopPropagation(),
      }, '⠿') : null,
      el('span', { class: `twisty${folded ? '' : ' open'}` }, '▶'),
      el('span', { class: 'heading-title', text: task.title, title: task.title }),
      el('span', { class: 'heading-count', text: `${count} 件` })),
    el('div', {},
      canEdit
        ? el('button', {
          class: 'icon-btn', title: 'メニュー',
          onClick: (event) => { event.stopPropagation(); headingMenu(event.currentTarget, task); },
        }, '⋯')
        : null));
    if (canEdit) attachDragHandlers(row, task);
    return row;
  }

  function headingMenu(anchor, task) {
    const current = headingLevel(task);
    popupMenu(anchor, (item) => [
      item('✏️ 名前を変える', () => renameHeading(task)),
      ...[1, 2, 3].filter((level) => level !== current).map((level) => item(
        `${level < current ? '⇤' : '⇥'} ${HEADING_LEVEL_LABEL[level]}にする`,
        () => setHeadingLevel(task, level))),
      ...hierarchyMenuItems(task, item).filter((node) => !/子タスク|親タスク/.test(node.textContent)),
      item('＋ この下にタスクを追加', () => addTaskAfter(task)),
      item('🗑 見出しを削除', async () => {
        const { undoToast } = await import('../util.js');
        // 見出しを消しても、その下のタスクはそのまま残る（区切りが 1 本消えるだけ）
        const result = await api.del(`/api/tasks/${task.id}`);
        reload();
        undoToast(`見出し「${task.title}」を削除しました`, async () => {
          await api.post(`/api/trash/${result.trash_id}/restore`, {});
          reload();
        });
      }, true),
    ]);
  }

  async function setHeadingLevel(task, level) {
    try {
      await api.patch(`/api/tasks/${task.id}`, { heading_level: level });
      reload();
    } catch (error) { toast(error.message, 'error'); }
  }

  async function renameHeading(task) {
    const { promptDialog } = await import('../util.js');
    const title = await promptDialog({ title: '見出しの名前', label: '名前', value: task.title });
    if (!title || !title.trim() || title.trim() === task.title) return;
    try {
      await api.patch(`/api/tasks/${task.id}`, { title: title.trim() });
      reload();
    } catch (error) { toast(error.message, 'error'); }
  }

  /**
   * 見出しを足す。after を渡すと、そのすぐ下（同じ階層）に入れる。
   * 渡さなければ、いちばん下に足す。
   */
  async function addHeading(after) {
    const chosen = await headingDialog(after ? governingLevel(after) : 1);
    if (!chosen) return;
    const title = chosen.title;
    try {
      const created = await api.post('/api/tasks', {
        project_id: projectId, title, is_heading: true, heading_level: chosen.level,
        parent_id: after?.parent_id ?? null,
      });
      if (after) await placeAfter(created.task, after);
      await reload();
      toast(`見出し「${title}」を足しました`, 'ok');
    } catch (error) { toast(error.message, 'error'); }
  }

  /** その位置を囲んでいる見出しの段。同じ段で足すのがいちばん多いので、既定にする。 */
  function governingLevel(task) {
    const siblings = siblingsOf(data.tasks, task.parent_id ?? null);
    for (let i = siblings.findIndex((t) => t.id === task.id); i >= 0; i -= 1) {
      if (siblings[i].is_heading) return headingLevel(siblings[i]);
    }
    return 1;
  }

  /** 見出しの名前と段を聞く。 */
  async function headingDialog(level) {
    const { openModal } = await import('../util.js');
    let picked = level;
    const name = el('input', { class: 'input', maxlength: 300,
      placeholder: '例）準備、本番、振り返り' });
    const seg = el('div', { class: 'seg' }, ...[1, 2, 3].map((value) => el('button', {
      type: 'button', class: value === picked ? 'active' : '',
      onClick: (event) => {
        picked = value;
        [...seg.children].forEach((b) => b.classList.toggle('active', b === event.currentTarget));
      },
    }, HEADING_LEVEL_LABEL[value])));
    const submit = (close) => {
      const title = name.value.trim();
      if (!title) { toast('見出しの名前を入れてください', 'error'); return; }
      close({ title, level: picked });
    };
    return openModal({
      title: '見出しを追加',
      build: (close) => {
        name.addEventListener('keydown', (event) => {
          if (event.key === 'Enter' && !event.isComposing) submit(close);
        });
        return el('div', {},
          el('div', { class: 'field' }, el('label', { text: '見出しの名前' }), name),
          el('div', { class: 'field' }, el('label', { text: '段' }), seg,
            el('div', { class: 'hint',
              text: '大見出しの区切りは次の大見出しまで。中・小見出しはその中をさらに区切ります。' })));
      },
      footer: (close) => [
        el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
        el('button', { class: 'btn btn-primary', onClick: () => submit(close) }, '追加'),
      ],
    });
  }

  /** 作ったばかりの行を、target のすぐ下へ動かす。 */
  async function placeAfter(item, target) {
    const parentId = target.parent_id ?? null;
    const siblings = siblingsOf(data.tasks, parentId).filter((t) => t.id !== item.id);
    const at = siblings.findIndex((t) => t.id === target.id);
    siblings.splice(at + 1, 0, item);
    await api.post('/api/tasks/reorder', {
      project_id: projectId,
      items: siblings.map((t, index) => ({
        id: t.id, parent_id: parentId, sort_order: (index + 1) * 10,
      })),
    });
  }

  /** 見出しのすぐ下にタスクを足す（その区切りの先頭に入る）。 */
  async function addTaskAfter(heading) {
    const saved = await openTaskForm({
      project, parentId: heading.parent_id ?? null, tasks: data.tasks, deps: data.deps,
      members: data.members });
    if (!saved) return;
    try {
      await placeAfter(saved, heading);
    } catch (error) { toast(error.message, 'error'); }
    collapsed.delete(heading.id);
    reload();
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
      hideDropHint();
      row.classList.remove('dragging');
      rowsHost.querySelectorAll('.task-row').forEach((node) =>
        node.classList.remove('drop-before', 'drop-after', 'drop-into'));
    });
    row.addEventListener('dragover', (event) => {
      if (dragId === null || dragId === task.id) return;
      event.preventDefault();
      const rect = row.getBoundingClientRect();
      const offset = (event.clientY - rect.top) / rect.height;
      // 上下に落とせば並べ替え、真ん中だけが「子にする」。
      // 並べ替えのほうが使う頻度が高いので、子にする帯は狭くしてある
      let mode = offset < 0.35 ? 'before' : offset > 0.65 ? 'after' : 'into';
      // 見出しの下（子）には入れられない。上下の並べ替えだけにする
      if (task.is_heading && mode === 'into') mode = offset < 0.5 ? 'before' : 'after';
      row.classList.remove('drop-before', 'drop-after', 'drop-into');
      row.classList.add('drop-' + mode);
      showDropHint(event, mode, task);
    });
    row.addEventListener('dragleave', () => {
      row.classList.remove('drop-before', 'drop-after', 'drop-into');
    });
    row.addEventListener('drop', async (event) => {
      event.preventDefault();
      hideDropHint();
      if (dragId === null || dragId === task.id) return;
      const mode = row.classList.contains('drop-into') ? 'into'
        : row.classList.contains('drop-before') ? 'before' : 'after';
      row.classList.remove('drop-before', 'drop-after', 'drop-into');
      await moveTask(dragId, task.id, mode);
    });
  }

  /** ドラッグ中、いま落とすとどうなるかをカーソルの横に出す。 */
  let hintNode = null;
  function showDropHint(event, mode, target) {
    if (!hintNode) {
      hintNode = el('div', { class: 'drop-hint' });
      document.body.appendChild(hintNode);
    }
    const label = mode === 'into'
      ? `「${target.title}」の子にする`
      : (mode === 'before' ? 'この上に移動' : 'この下に移動');
    hintNode.textContent = label;
    hintNode.classList.toggle('into', mode === 'into');
    hintNode.style.left = `${event.clientX + 14}px`;
    hintNode.style.top = `${event.clientY + 16}px`;
    hintNode.hidden = false;
  }

  function hideDropHint() {
    if (hintNode) hintNode.hidden = true;
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
    popupMenu(anchor, (menuItem) => [
    menuItem('👁 詳細を開く', () => openTaskDetail(task.id, { onChange: reload })),
    menuItem('＋ 子タスクを追加', () => addTask(task.id)),
    menuItem('🔖 この下に見出しを追加', () => addHeading(task)),
    ...hierarchyMenuItems(task, menuItem),
    menuItem('✏️ 編集', async () => {
      const saved = await openTaskForm({
        project, task, tasks: data.tasks, deps: data.deps, members: data.members });
      if (saved) reload();
    }),
    menuItem('📄 複製', async () => {
      const result = await api.post(`/api/tasks/${task.id}/duplicate`, {});
      toast(result.created > 1
        ? `${result.created} 件を複製しました` : '複製しました', 'ok');
      reload();
    }),
    menuItem('🧩 雛形として保存', async () => {
      const { openSaveTemplate } = await import('./templates.js');
      await openSaveTemplate({ task });
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
      const { confirmDialog, undoToast } = await import('../util.js');
      if (!await confirmDialog(
        `「${task.title}」を削除しますか？（子タスクも削除されます）\n`
        + '間違えたら、ゴミ箱から 30 日以内に戻せます。',
        { danger: true, okLabel: '削除する' })) return;
      const result = await api.del(`/api/tasks/${task.id}`);
      reload();
      undoToast(`「${task.title}」を削除しました`, async () => {
        await api.post(`/api/trash/${result.trash_id}/restore`, {});
        reload();
      });
    }, true)]);
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
        const done = result.updated ?? result.deleted;
        state.picked.clear();
        await reload();
        // まとめて消したときは、選んだ数だけゴミ箱の行ができる。まとめて戻せるようにする。
        if (result.trash_ids?.length) {
          const { undoToast } = await import('../util.js');
          undoToast(`${done} 件を${label}`, async () => {
            for (const id of result.trash_ids) await api.post(`/api/trash/${id}/restore`, {});
            await reload();
          });
        } else {
          toast(`${done} 件を${label}`, 'ok');
        }
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
      }, ...iconLabel('calendar', '日程をずらす')),
      el('div', { class: 'spacer' }),
      el('button', {
        class: 'btn btn-sm btn-danger',
        onClick: async () => {
          const { confirmDialog } = await import('../util.js');
          if (!await confirmDialog(
            `選択した ${count} 件を削除します。\n子タスクも一緒に消えます。\n`
            + '間違えたら、ゴミ箱から 30 日以内に戻せます。',
            { danger: true, okLabel: '削除する' })) return;
          run({ action: 'delete' }, '削除しました');
        },
      }, ...iconLabel('trash', '削除')),
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
    // 上下の入れ替え。ドラッグの効かない端末ではここが頼りになる
    const siblings = siblingsOf(data.tasks, task.parent_id ?? null);
    const at = siblings.findIndex((t) => t.id === task.id);
    if (at > 0) {
      items.push(menuItem('↑ 上へ移動', () => reorderWithin(task, -1)));
    }
    if (at >= 0 && at < siblings.length - 1) {
      items.push(menuItem('↓ 下へ移動', () => reorderWithin(task, 1)));
    }
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

  /** 同じ階層の中で、ひとつ上（-1）またはひとつ下（+1）へ動かす。 */
  async function reorderWithin(task, direction) {
    const siblings = siblingsOf(data.tasks, task.parent_id ?? null);
    const at = siblings.findIndex((t) => t.id === task.id);
    const to = at + direction;
    if (at < 0 || to < 0 || to >= siblings.length) return;
    const ordered = [...siblings];
    ordered.splice(to, 0, ordered.splice(at, 1)[0]);
    try {
      await api.post('/api/tasks/reorder', {
        project_id: projectId,
        items: ordered.map((item, index) => ({
          id: item.id, parent_id: task.parent_id ?? null, sort_order: (index + 1) * 10,
        })),
      });
      await reload();
    } catch (error) { toast(error.message, 'error'); }
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
