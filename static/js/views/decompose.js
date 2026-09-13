/* 子タスクの自動提案ダイアログ。タスク詳細とクイック追加の両方から使う。 */
import { api } from '../api.js';
import { store } from '../store.js';
import { el, fill, openModal, toast } from '../util.js';
import { categorySelect } from './pickers.js';

/**
 * 大きなタスクを子タスクに割る提案を出し、選んだものを登録する。
 * @param {object} task 少なくとも id / title / project_id / start_date / due_date
 * @returns {Promise<number>} 追加した件数
 */
export async function openSubtaskSuggestions(task, { onChange } = {}) {
  const state = { items: [], template: '', engine: '', warning: null, loading: true };
  const listHost = el('div', {});
  const meta = el('div', { class: 'hint' });
  let closeModal = null;

  const load = async (forceRule = false) => {
    state.loading = true;
    draw();
    try {
      const data = await api.post('/api/nl/decompose', {
        title: task.title,
        description: task.description || '',
        project_id: task.project_id,
        start_date: task.start_date || null,
        due_date: task.due_date || null,
        category: task.category || '',
        force_rule: forceRule,
      });
      state.items = data.items.map((item) => ({ ...item, checked: true }));
      state.template = data.template;
      state.engine = data.engine;
      state.matched = data.matched;
      state.warning = data.warning;
    } catch (error) {
      state.warning = error.message;
      state.items = [];
    }
    state.loading = false;
    draw();
  };

  const draw = () => {
    fill(meta,
      state.loading ? '分解中…' : null,
      !state.loading && state.items.length
        ? el('span', {},
          state.engine === 'llm'
            ? 'Claude が内容に合わせて分解しました。'
            : `定型テンプレート「${state.template}」から作成しました。`,
          state.matched === false && state.engine !== 'llm'
            ? '（該当するテンプレートがなかったため、汎用の手順です）'
            : null)
        : null);

    fill(listHost, ...(state.loading
      ? [el('div', { class: 'empty', text: '候補を作成しています…' })]
      : state.items.length
        ? state.items.map(row)
        : [el('div', { class: 'empty' },
          el('div', { class: 'big', text: '🤔' }), '分解の候補を作れませんでした')]));
  };

  const row = (item, index) => {
    const check = el('input', { type: 'checkbox', checked: item.checked ? true : null });
    check.addEventListener('change', () => { item.checked = check.checked; });
    const title = el('input', { class: 'input', value: item.title });
    title.addEventListener('input', () => { item.title = title.value; });
    const category = categorySelect(item.category);
    category.addEventListener('change', () => { item.category = category.value; });
    const due = el('input', { class: 'input', type: 'date', value: item.due_date || '' });
    due.addEventListener('change', () => { item.due_date = due.value || null; });

    return el('div', { class: 'suggest-row' },
      el('label', { class: 'suggest-check' }, check, el('span', { class: 'suggest-no', text: String(index + 1) })),
      title,
      el('div', { class: 'suggest-side' }, category, due),
      el('button', {
        class: 'icon-btn', title: 'この候補を消す',
        onClick: () => { state.items.splice(index, 1); draw(); },
      }, '×'));
  };

  const result = await openModal({
    title: `「${task.title}」を子タスクに分解`,
    wide: true,
    build: (close) => {
      closeModal = close;
      load();
      return el('div', {},
        el('p', { class: 'page-sub',
          text: 'チェックしたものが子タスクとして追加されます。名前・区分・期限はここで直せます。' }),
        meta, listHost,
        el('div', { style: { marginTop: '10px', display: 'flex', gap: '8px', flexWrap: 'wrap' } },
          el('button', { class: 'btn btn-sm', onClick: () => load(false) }, '↻ 作り直す'),
          store.meta?.llm_available
            ? el('button', { class: 'btn btn-sm', onClick: () => load(true) }, '定型テンプレートで作る')
            : null,
          el('button', {
            class: 'btn btn-sm',
            onClick: () => {
              state.items.push({ title: '', category: '', due_date: null, checked: true });
              draw();
            },
          }, '＋ 行を足す')));
    },
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', {
        class: 'btn btn-primary',
        onClick: async (event) => {
          const chosen = state.items.filter((i) => i.checked && i.title.trim());
          if (!chosen.length) { toast('追加する子タスクを選んでください', 'error'); return; }
          button.disabled = true;
          try {
            const data = await api.post(`/api/tasks/${task.id}/subtasks`, { items: chosen });
            toast(`子タスクを ${data.created} 件追加しました`, 'ok');
            close(data.created);
          } catch (error) {
            toast(error.message, 'error');
            button.disabled = false;
          }
        },
      }, '選んだものを追加'),
    ],
  });
  if (result && onChange) onChange();
  return result || 0;
}

/** 期間が入っていない場合の注意書き（分解の日程按分は期間があると効く）。 */
export function decomposeHint(task) {
  if (task.start_date && task.due_date) return null;
  return el('div', { class: 'hint', text: '開始日と期限を入れておくと、子タスクに日程を自動で割り振ります。' });
}
