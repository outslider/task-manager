/* Create / edit dialog for a single task. */
import { api } from '../api.js';
import { store, IMPORTANCE_LABEL, STATUS_LABEL } from '../store.js';
import { el, openModal, toast } from '../util.js';
import { categorySelect, chipPicker, option, userSelect } from './pickers.js';

export { categoryChip, categorySelect, userSelect } from './pickers.js';

/** Predecessor picker: the tasks that must finish before this one can start. */
export function dependencyPicker(candidates, selectedIds = []) {
  return chipPicker(candidates, selectedIds, {
    placeholder: '先行タスクを選ぶ…',
    emptyText: '設定なし（他のタスクの完了を待たずに着手できます）',
    exhausted: '追加できるタスクがありません',
  });
}

/**
 * @param {object} options project, task (edit), parentId, tasks, deps
 * @returns {Promise<object|null>} the saved task
 */
export function openTaskForm({
  project, task = null, parentId = null, tasks = [], deps = [], members = null,
  preset = null,
}) {
  const editing = Boolean(task);
  const fields = {};

  // 見出しは区切りの線なので、親にも先行タスクにもできない
  const parentOptions = tasks
    .filter((t) => !t.is_heading)
    .filter((t) => !task || (t.id !== task.id && !isDescendant(tasks, t.id, task.id)))
    .map((t) => {
      // 同じ名前のタスクが他にもあるときは、どこにあるものか添えて見分けられるようにする
      const twin = tasks.some((o) => o.id !== t.id && o.title === t.title);
      const path = twin ? parentPath(tasks, t.id) : '';
      return option(t.id,
        `${'　'.repeat(depthOf(tasks, t.id))}${t.title}${path ? `（${path}）` : ''}`,
        Number(task ? task.parent_id : parentId) === t.id);
    });

  const depCandidates = tasks.filter((t) => !t.is_heading && (!task || t.id !== task.id));
  const currentDeps = task
    ? deps.filter((d) => d.task_id === task.id).map((d) => d.depends_on_id)
    : [];
  const depPicker = dependencyPicker(depCandidates, currentDeps);

  return openModal({
    title: editing ? 'タスクを編集' : '新しいタスク',
    build: () => {
      fields.title = el('input', { class: 'input', placeholder: 'タスク名', required: true });
      fields.title.value = task?.title || '';
      fields.description = el('textarea', {
        class: 'textarea', placeholder: 'メモ・詳細（改行可）',
      });
      fields.description.value = task?.description || '';
      fields.category = categorySelect(task?.category || '');
      fields.assignee = userSelect(task?.assignee_id, { people: members });
      fields.status = el('select', { class: 'select' },
        ...Object.entries(STATUS_LABEL).map(([value, label]) =>
          option(value, label, (task?.status || 'todo') === value)));
      fields.priority = el('select', { class: 'select' },
        ...Object.entries(IMPORTANCE_LABEL).reverse().map(([value, label]) =>
          option(value, label, String(task?.priority ?? 1) === value)));
      // ガント上でドラッグして開いた場合は、その期間を初期値にする
      fields.start = el('input', { class: 'input', type: 'date' });
      fields.start.value = task?.start_date || preset?.start_date || '';
      fields.due = el('input', { class: 'input', type: 'date' });
      fields.due.value = task?.due_date || preset?.due_date || '';
      fields.estimate = el('input', {
        class: 'input', type: 'number', min: 0, step: 0.5, placeholder: '任意',
      });
      fields.estimate.value = task?.estimate_hours ?? '';
      fields.progress = el('input', {
        class: 'input', type: 'number', min: 0, max: 100, step: 5,
      });
      fields.progress.value = String(task?.progress ?? 0);
      fields.milestone = el('input', { type: 'checkbox' });
      fields.milestone.checked = Boolean(task?.is_milestone);
      // ガント上の記号。マイルストーンと、まとめ行に並ぶ各回の印に使う
      fields.marker = el('select', { class: 'select', style: { maxWidth: '180px' } },
        ...(store.meta?.markers || [{ value: '', label: '◆ ひし形（既定）' }]).map((m) =>
          option(m.value, m.label, (task?.marker || '') === m.value)));
      fields.parent = el('select', { class: 'select' },
        option('', '（トップレベル）', !(task ? task.parent_id : parentId)),
        ...parentOptions);

      return el('div', {},
        el('div', { class: 'field' }, el('label', { text: 'タスク名 *' }), fields.title),
        el('div', { class: 'row' },
          el('div', { class: 'field' }, el('label', { text: 'カテゴリ' }), fields.category),
          el('div', { class: 'field' }, el('label', { text: '担当者' }), fields.assignee)),
        el('div', { class: 'row' },
          el('div', { class: 'field' }, el('label', { text: '開始日' }), fields.start),
          el('div', { class: 'field' }, el('label', { text: '期限' }), fields.due),
          el('div', { class: 'field' }, el('label', { text: '見積 (h)' }), fields.estimate)),
        el('div', { class: 'row' },
          el('div', { class: 'field' }, el('label', { text: '状態' }), fields.status),
          el('div', { class: 'field' }, el('label', { text: '重要度' }), fields.priority),
          el('div', { class: 'field' }, el('label', { text: '進捗 %' }), fields.progress)),
        el('div', { class: 'field' },
          el('label', { text: '先行タスク（これが終わるまで着手できない）' }),
          depPicker.node),
        el('div', { class: 'field' }, el('label', { text: '親タスク' }), fields.parent),
        el('div', { class: 'row' },
          el('div', { class: 'field' },
            el('label', { class: 'check' }, fields.milestone,
              el('span', { text: 'マイルストーンとして表示する' }))),
          el('div', { class: 'field' },
            el('label', { text: 'ガントの記号' }), fields.marker,
            el('div', { class: 'hint',
              text: 'マイルストーンの印と、折りたたんだ親の行に並ぶ各回の印に使います。' }))),
        el('div', { class: 'field' }, el('label', { text: 'メモ' }), fields.description));
    },
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', {
        class: 'btn btn-primary',
        onClick: async (event) => {
          const button = event.currentTarget;
          const payload = {
            title: fields.title.value.trim(),
            description: fields.description.value,
            category: fields.category.value,
            assignee_id: fields.assignee.value ? Number(fields.assignee.value) : null,
            status: fields.status.value,
            priority: Number(fields.priority.value),
            start_date: fields.start.value || null,
            due_date: fields.due.value || null,
            progress: Number(fields.progress.value || 0),
            is_milestone: fields.milestone.checked,
            marker: fields.marker.value,
            estimate_hours: fields.estimate.value === '' ? null : Number(fields.estimate.value),
            parent_id: fields.parent.value ? Number(fields.parent.value) : null,
            depends_on: depPicker.ids(),
          };
          if (!payload.title) { toast('タスク名を入力してください', 'error'); return; }
          button.disabled = true;
          try {
            const result = editing
              ? await api.patch(`/api/tasks/${task.id}`, payload)
              : await api.post('/api/tasks', { ...payload, project_id: project.id });
            toast(editing ? '更新しました' : 'タスクを追加しました', 'ok');
            close(result.task);
          } catch (error) {
            toast(error.message, 'error');
            button.disabled = false;
          }
        },
      }, editing ? '保存' : '追加'),
    ],
  });
}

/** 上位をたどった道のり。トップレベルなら「トップレベル」。 */
function parentPath(tasks, id) {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  const parts = [];
  let node = byId.get(byId.get(id)?.parent_id);
  let guard = 0;
  while (node && guard < 12) {
    parts.unshift(node.title);
    node = byId.get(node.parent_id);
    guard += 1;
  }
  return parts.length ? parts.join(' > ') : 'トップレベル';
}

function depthOf(tasks, id) {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  let depth = 0;
  let node = byId.get(id);
  while (node?.parent_id && depth < 12) { node = byId.get(node.parent_id); depth += 1; }
  return depth;
}

function isDescendant(tasks, candidateId, ancestorId) {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  let node = byId.get(candidateId);
  let guard = 0;
  while (node?.parent_id && guard < 20) {
    if (node.parent_id === ancestorId) return true;
    node = byId.get(node.parent_id);
    guard += 1;
  }
  return false;
}
