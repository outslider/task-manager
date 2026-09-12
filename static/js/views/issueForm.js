/* Create / edit dialog for an issue. */
import { api } from '../api.js';
import { ISSUE_STATUS_LABEL, SEVERITY_LABEL } from '../store.js';
import { el, openModal, toast, toISO, today } from '../util.js';
import { chipPicker, issueCategorySelect, option, userSelect } from './pickers.js';

export function taskPicker(tasks, selectedIds = []) {
  return chipPicker(tasks, selectedIds, {
    placeholder: 'この課題に関係するタスクを選ぶ…',
    emptyText: '関連づけたタスクはありません',
    exhausted: '追加できるタスクがありません',
  });
}

/**
 * @param {object} options project, issue (edit), tasks, linkedTaskIds
 * @returns {Promise<object|null>} the saved issue
 */
export function openIssueForm({ project, issue = null, tasks = [], linkedTaskIds = [] }) {
  const editing = Boolean(issue);
  const f = {};
  const picker = taskPicker(tasks, linkedTaskIds);

  return openModal({
    title: editing ? `課題 #${issue.seq} を編集` : '課題を起票',
    wide: true,
    build: () => {
      f.title = el('input', { class: 'input', placeholder: '例）本番DBの接続情報が未共有' });
      f.title.value = issue?.title || '';
      f.description = el('textarea', {
        class: 'textarea', placeholder: '何が起きていて、なぜ困っているのか',
      });
      f.description.value = issue?.description || '';
      f.resolution = el('textarea', {
        class: 'textarea', placeholder: '誰が・いつまでに・何をするか / 決まったこと',
      });
      f.resolution.value = issue?.resolution || '';
      f.category = issueCategorySelect(issue?.category || 'other');
      f.status = el('select', { class: 'select' },
        ...Object.entries(ISSUE_STATUS_LABEL).map(([value, label]) =>
          option(value, label, (issue?.status || 'open') === value)));
      f.severity = el('select', { class: 'select' },
        ...Object.entries(SEVERITY_LABEL).reverse().map(([value, label]) =>
          option(value, label, String(issue?.severity ?? 1) === value)));
      f.owner = userSelect(issue?.owner_id, { emptyLabel: '未割当' });
      f.raised = el('input', { class: 'input', type: 'date' });
      f.raised.value = issue?.raised_on || toISO(today());
      f.due = el('input', { class: 'input', type: 'date' });
      f.due.value = issue?.due_date || '';
      f.resolved = el('input', { class: 'input', type: 'date' });
      f.resolved.value = issue?.resolved_on || '';

      return el('div', {},
        el('div', { class: 'field' }, el('label', { text: '課題 *' }), f.title),
        el('div', { class: 'row' },
          el('div', { class: 'field' }, el('label', { text: '区分' }), f.category),
          el('div', { class: 'field' }, el('label', { text: '影響度' }), f.severity),
          el('div', { class: 'field' }, el('label', { text: '状態' }), f.status)),
        el('div', { class: 'row' },
          el('div', { class: 'field' }, el('label', { text: '対応者' }), f.owner),
          el('div', { class: 'field' }, el('label', { text: '発生日 *' }), f.raised),
          el('div', { class: 'field' }, el('label', { text: '対応期限' }), f.due)),
        el('div', { class: 'field' },
          el('label', { text: '内容・背景' }), f.description),
        el('div', { class: 'field' },
          el('label', { text: '対応方針・結果' }), f.resolution),
        el('div', { class: 'field' },
          el('label', { text: '関連タスク' }), picker.node,
          el('div', { class: 'hint',
            text: 'この課題が解消しないと進まないタスク、または課題対応のために作ったタスクを紐づけます。' })),
        editing
          ? el('div', { class: 'field' }, el('label', { text: '解決日' }), f.resolved,
            el('div', { class: 'hint', text: '「解決済」「クローズ」にすると自動で入ります。' }))
          : null);
    },
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', {
        class: 'btn btn-primary',
        onClick: async (event) => {
          const button = event.currentTarget;
          const payload = {
            title: f.title.value.trim(),
            description: f.description.value,
            resolution: f.resolution.value,
            category: f.category.value,
            status: f.status.value,
            severity: Number(f.severity.value),
            owner_id: f.owner.value ? Number(f.owner.value) : null,
            raised_on: f.raised.value || null,
            due_date: f.due.value || null,
            task_ids: picker.ids(),
          };
          if (editing) payload.resolved_on = f.resolved.value || null;
          if (!payload.title) { toast('課題を入力してください', 'error'); return; }
          if (!payload.raised_on) { toast('発生日を入力してください', 'error'); return; }
          button.disabled = true;
          try {
            const result = editing
              ? await api.patch(`/api/issues/${issue.id}`, payload)
              : await api.post('/api/issues', { ...payload, project_id: project.id });
            toast(editing ? '更新しました' : '課題を起票しました', 'ok');
            close(result.issue);
          } catch (error) {
            toast(error.message, 'error');
            button.disabled = false;
          }
        },
      }, editing ? '保存' : '起票'),
    ],
  });
}
