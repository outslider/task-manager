/* マイ ToDo: プロジェクトに属さない、自分だけの覚え書き。
 *
 * 本人以外（管理者も含めて）には見えない。思いつきをすぐ書き留められるよう、
 * 入力は「やること」1 行だけで済むようにしてある。 */
import { api } from '../api.js';
import { setHeader } from '../app.js';
import { store } from '../store.js';
import { confirmDialog, dueClass, el, fill, formatDate, toast } from '../util.js';

export async function render(container) {
  setHeader('マイ ToDo');
  const state = { includeDone: false, todos: [] };

  const input = el('input', {
    class: 'input', placeholder: 'やることを入力して Enter（例: 経費精算を出す）',
    maxlength: 300,
  });
  const dueInput = el('input', { class: 'input', type: 'date', style: { maxWidth: '160px' } });
  const addButton = el('button', { class: 'btn btn-primary', onClick: () => add() }, '追加');
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); add(); }
  });

  const doneToggle = el('input', {
    type: 'checkbox',
    onChange: (event) => { state.includeDone = event.target.checked; load(); },
  });

  const list = el('div', { class: 'todo-list' });
  const summary = el('div', { class: 'hint' });

  fill(container,
    el('div', { class: 'card' },
      el('div', { class: 'card-head' },
        el('h2', {}, 'マイ ToDo'),
        el('label', { class: 'check' }, doneToggle, el('span', { text: '完了も表示' }))),
      el('div', { class: 'card-body' },
        el('p', { class: 'page-sub',
          text: 'プロジェクトに紐づかない、ちょっとした用事の置き場です。'
            + 'ここに書いたものは自分だけに見えます（管理者にも見えません）。' }),
        el('div', { class: 'todo-add' }, input, dueInput, addButton),
        list,
        summary)));

  async function load() {
    try {
      const data = await api.todos(state.includeDone ? { include_done: 1 } : null);
      state.todos = data.todos;
      draw(data.open_count);
    } catch (error) { toast(error.message, 'error'); }
  }

  async function add() {
    const title = input.value.trim();
    if (!title) { input.focus(); return; }
    addButton.disabled = true;
    try {
      await api.post('/api/todos', { title, due_date: dueInput.value || null });
      input.value = '';
      dueInput.value = '';
      await load();
      input.focus();
    } catch (error) { toast(error.message, 'error'); }
    addButton.disabled = false;
  }

  async function patch(todo, payload) {
    try {
      await api.patch(`/api/todos/${todo.id}`, payload);
      await load();
    } catch (error) { toast(error.message, 'error'); }
  }

  function draw(openCount) {
    if (!state.todos.length) {
      fill(list, el('div', { class: 'empty' },
        el('div', { style: { fontSize: '28px' } }, '📝'),
        el('div', { text: state.includeDone ? 'ToDo はまだありません' : '未完了の ToDo はありません' })));
      summary.textContent = '';
      return;
    }
    fill(list, ...state.todos.map((todo) => row(todo)));
    summary.textContent = `未完了 ${openCount} 件`;
  }

  function row(todo) {
    const check = el('input', {
      type: 'checkbox', checked: todo.is_done ? true : null,
      onChange: (event) => patch(todo, { is_done: event.target.checked }),
    });
    const title = el('span', {
      class: 'todo-title', text: todo.title, title: 'クリックで編集', tabindex: '0',
      onClick: () => startEdit(),
    });
    const due = el('span', {
      class: `todo-due ${todo.is_done ? '' : dueClass(todo.due_date, 'todo')}`,
      text: todo.due_date ? formatDate(todo.due_date) : '',
    });

    const node = el('div', { class: `todo-row${todo.is_done ? ' is-done' : ''}` },
      check, title, due,
      el('div', { class: 'todo-actions' },
        el('button', {
          class: 'icon-btn', title: '期限を設定',
          onClick: () => editDue(),
        }, '📅'),
        el('button', {
          class: 'icon-btn', title: 'プロジェクトのタスクにする',
          onClick: () => promote(todo),
        }, '📁'),
        el('button', {
          class: 'icon-btn', title: '削除',
          onClick: async () => {
            if (!await confirmDialog(`「${todo.title}」を削除しますか？`,
              { danger: true, okLabel: '削除する' })) return;
            await api.del(`/api/todos/${todo.id}`);
            load();
          },
        }, '🗑')));

    function startEdit() {
      const field = el('input', { class: 'input', value: todo.title, maxlength: 300 });
      const finish = async (save) => {
        if (save && field.value.trim() && field.value.trim() !== todo.title) {
          await patch(todo, { title: field.value.trim() });
        } else {
          draw(state.todos.filter((t) => !t.is_done).length);
        }
      };
      field.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') finish(true);
        if (event.key === 'Escape') finish(false);
      });
      field.addEventListener('blur', () => finish(true));
      node.replaceChild(field, title);
      field.focus();
      field.select();
    }

    function editDue() {
      const field = el('input', {
        class: 'input', type: 'date', value: todo.due_date || '',
        style: { maxWidth: '160px' },
        onChange: () => patch(todo, { due_date: field.value || null }),
      });
      node.replaceChild(field, due);
      field.focus();
      if (field.showPicker) { try { field.showPicker(); } catch { /* 未対応でも問題ない */ } }
    }

    return node;
  }

  async function promote(todo) {
    const editable = store.projects.filter((p) => !p.archived && store.canEdit(p));
    if (!editable.length) {
      toast('タスクを追加できるプロジェクトがありません', 'error');
      return;
    }
    const select = el('select', { class: 'select' },
      ...editable.map((p) => el('option', { value: p.id }, p.name)));
    const { openModal } = await import('../util.js');
    const chosen = await openModal({
      title: 'プロジェクトのタスクにする',
      build: () => el('div', {},
        el('p', { class: 'page-sub',
          text: `「${todo.title}」をタスクとして登録し、この ToDo は削除します。` }),
        el('div', { class: 'field' }, el('label', { text: '登録先' }), select)),
      footer: (close) => [
        el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
        el('button', {
          class: 'btn btn-primary', onClick: () => close(Number(select.value)),
        }, 'タスクにする'),
      ],
    });
    if (!chosen) return;
    try {
      const result = await api.post(`/api/todos/${todo.id}/promote`, { project_id: chosen });
      toast(`${store.project(chosen)?.name} に登録しました`, 'ok');
      await load();
      const { openTaskDetail } = await import('./taskDetail.js');
      openTaskDetail(result.task.id);
    } catch (error) { toast(error.message, 'error'); }
  }

  await load();
  input.focus();
}
