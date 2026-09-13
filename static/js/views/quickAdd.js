/* 自然言語でのクイック追加。一文を解析して、確認してから登録する。 */
import { api } from '../api.js';
import { store, CATEGORIES, IMPORTANCE_LABEL, category as categoryInfo } from '../store.js';
import { el, fill, formatDate, openModal, toast } from '../util.js';
import { categorySelect, option, userSelect } from './pickers.js';
import { openSubtaskSuggestions } from './decompose.js';

const EXAMPLES = [
  '来週金曜までに鈴木さんが移行手順書を作成',
  '至急 サーバー障害の報告書をまとめる',
  '10/1から10/20まで結合テスト',
];

const FIELD_LABEL = {
  due_date: '期限', start_date: '開始日', assignee_id: '担当者', priority: '重要度',
  category: 'カテゴリ', project_id: 'プロジェクト', is_milestone: 'マイルストーン',
};

export async function openQuickAdd({ projectId = null, onCreated } = {}) {
  const state = { draft: null, warning: null, busy: false };
  const input = el('textarea', {
    class: 'textarea qa-input',
    placeholder: `やることを一文で。例）${EXAMPLES[0]}`,
    rows: 2,
  });
  const hint = el('div', { class: 'qa-examples' },
    el('span', { class: 'hint', text: '例:' }),
    ...EXAMPLES.map((example) => el('button', {
      class: 'qa-chip', type: 'button',
      onClick: () => { input.value = example; parse(); },
    }, example)));
  const status = el('div', {});
  const preview = el('div', { hidden: true });
  const fields = {};
  const decomposeCheck = el('input', { type: 'checkbox' });

  async function parse() {
    const text = input.value.trim();
    if (!text) { toast('内容を入力してください', 'error'); return; }
    state.busy = true;
    fill(status, el('div', { class: 'hint', text: '解析しています…' }));
    try {
      const data = await api.post('/api/nl/parse', { text, project_id: projectId });
      state.draft = data.draft;
      state.warning = data.warning;
      drawPreview();
    } catch (error) {
      fill(status, el('div', { class: 'warn-box danger', text: error.message }));
      preview.hidden = true;
    }
    state.busy = false;
  }

  function drawPreview() {
    const draft = state.draft;
    const editable = store.projects.filter((p) => !p.archived && store.canEdit(p));

    fields.project = el('select', { class: 'select' },
      ...editable.map((p) => option(p.id, p.name, draft.project_id === p.id)));
    fields.title = el('input', { class: 'input', value: draft.title });
    fields.category = categorySelect(draft.category);
    fields.assignee = userSelect(draft.assignee_id);
    fields.priority = el('select', { class: 'select' },
      ...Object.entries(IMPORTANCE_LABEL).reverse().map(([value, label]) =>
        option(value, label, String(draft.priority) === value)));
    fields.start = el('input', { class: 'input', type: 'date', value: draft.start_date || '' });
    fields.due = el('input', { class: 'input', type: 'date', value: draft.due_date || '' });
    fields.milestone = el('input', { type: 'checkbox', checked: draft.is_milestone ? true : null });
    fields.description = el('textarea', { class: 'textarea', rows: 2 });
    fields.description.value = draft.description || '';

    fill(status,
      state.warning ? el('div', { class: 'warn-box', text: state.warning }) : null,
      el('div', { class: 'qa-badges' },
        el('span', {
          class: 'badge',
          title: draft.engine === 'llm' ? 'Claude が解析しました' : 'キーワードと日付表現から解析しました',
        }, draft.engine === 'llm' ? '🤖 Claude 解析' : '⚡ 簡易解析'),
        ...(draft.matched || []).map((m) => el('span', { class: 'badge doing' },
          `${FIELD_LABEL[m.field] || m.field}: ${labelFor(draft, m)}`))));

    fill(preview,
      el('div', { class: 'row' },
        el('div', { class: 'field' }, el('label', { text: 'プロジェクト' }), fields.project),
        el('div', { class: 'field' }, el('label', { text: '担当者' }), fields.assignee)),
      el('div', { class: 'field' }, el('label', { text: 'タスク名' }), fields.title),
      el('div', { class: 'row' },
        el('div', { class: 'field' }, el('label', { text: 'カテゴリ' }), fields.category),
        el('div', { class: 'field' }, el('label', { text: '重要度' }), fields.priority)),
      el('div', { class: 'row' },
        el('div', { class: 'field' }, el('label', { text: '開始日' }), fields.start),
        el('div', { class: 'field' }, el('label', { text: '期限' }), fields.due)),
      el('div', { class: 'field' }, el('label', { text: 'メモ' }), fields.description),
      el('div', { class: 'field' },
        el('label', { class: 'check' }, fields.milestone,
          el('span', { text: 'マイルストーンにする' }))),
      el('div', { class: 'field' },
        el('label', { class: 'check' }, decomposeCheck,
          el('span', { text: '登録したあとに子タスクの分解案を出す' }))));
    preview.hidden = false;
  }

  function labelFor(draft, matched) {
    if (matched.field === 'assignee_id') return store.userName(draft.assignee_id);
    if (matched.field === 'category') return categoryInfo(draft.category).label;
    if (matched.field === 'priority') return IMPORTANCE_LABEL[draft.priority];
    if (matched.field === 'project_id') return store.project(draft.project_id)?.name || '';
    if (matched.field === 'due_date') return formatDate(draft.due_date);
    if (matched.field === 'start_date') return formatDate(draft.start_date);
    return matched.text;
  }

  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      if (state.draft) submitFromFooter();
      else parse();
    }
  });

  let footerSubmit = null;
  const submitFromFooter = () => footerSubmit && footerSubmit.click();

  const created = await openModal({
    title: '⚡ クイック追加',
    wide: true,
    build: () => el('div', {},
      el('p', { class: 'page-sub',
        text: '日付・担当者・重要度などを文章から読み取ります。内容を確認してから登録してください。' }),
      input, hint, status, preview),
    footer: (close) => {
      const parseButton = el('button', { class: 'btn', onClick: parse }, '解析');
      footerSubmit = el('button', {
        class: 'btn btn-primary',
        onClick: async (event) => {
          if (!state.draft) { parse(); return; }
          const payload = {
            project_id: Number(fields.project.value),
            title: fields.title.value.trim(),
            description: fields.description.value,
            category: fields.category.value,
            assignee_id: fields.assignee.value ? Number(fields.assignee.value) : null,
            priority: Number(fields.priority.value),
            start_date: fields.start.value || null,
            due_date: fields.due.value || null,
            is_milestone: fields.milestone.checked,
          };
          if (!payload.title) { toast('タスク名を入力してください', 'error'); return; }
          if (!payload.project_id) { toast('プロジェクトを選んでください', 'error'); return; }
          button.disabled = true;
          try {
            const data = await api.post('/api/tasks', payload);
            toast('タスクを追加しました', 'ok');
            close({ task: data.task, decompose: decomposeCheck.checked });
          } catch (error) {
            toast(error.message, 'error');
            button.disabled = false;
          }
        },
      }, '登録');
      return [el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
        parseButton, footerSubmit];
    },
  });

  if (!created) return null;
  if (created.decompose) {
    await openSubtaskSuggestions(created.task);
  }
  if (onCreated) onCreated(created.task);
  return created.task;
}
