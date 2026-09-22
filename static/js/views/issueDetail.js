/* Issue detail drawer: status, linked tasks, history and attachments. */
import { api, url } from '../api.js';
import {
  store, ISSUE_STATUS_LABEL, SEVERITY_LABEL, STATUS_LABEL, issueCategory,
} from '../store.js';
import {
  avatar, confirmDialog, dueClass, dueDelta, el, fill, formatBytes, formatDate,
  formatDateTime, openDrawer, openModal, toast, undoToast,
} from '../util.js';
import { issueCategorySelect, userSelect } from './pickers.js';
import { openIssueForm, taskPicker } from './issueForm.js';
import { memoEditor, openTaskDetail } from './taskDetail.js';
import { attachMentions, richText } from './mention.js';

let openInstance = null;

export async function openIssueDetail(issueId, { onChange } = {}) {
  if (openInstance) openInstance.close();
  const instance = openDrawer({
    build: (drawer) => {
      drawer.appendChild(el('div', { class: 'empty', text: '読み込み中…' }));
    },
    onClose: () => { openInstance = null; if (onChange) onChange(); },
  });
  openInstance = instance;
  await renderDetail(instance, issueId, onChange);
  return instance;
}

async function renderDetail(instance, issueId, onChange) {
  let data;
  try {
    data = await api.get(`/api/issues/${issueId}`);
  } catch (error) {
    fill(instance.drawer, el('div', { class: 'empty', text: error.message }));
    return;
  }
  const { issue, tasks, comments, attachments, my_role: role } = data;
  const linkedTickets = data.tickets || [];
  const canEdit = role === 'owner' || role === 'editor';
  const canComment = canEdit || role === 'commenter';
  const info = issueCategory(issue.category);
  const reload = async () => {
    await renderDetail(instance, issueId, onChange);
    if (onChange) onChange();
  };
  const patch = async (payload) => {
    try {
      await api.patch(`/api/issues/${issue.id}`, payload);
      reload();
    } catch (error) { toast(error.message, 'error'); }
  };

  const head = el('div', { class: 'drawer-head' },
    el('div', { style: { minWidth: 0, flex: 1 } },
      el('div', { class: 'breadcrumb' }, `${issue.project_name} ・ 課題 #${issue.seq}`),
      el('h2', { text: issue.title, style: { whiteSpace: 'normal' } })),
    canEdit
      ? el('button', {
        class: 'icon-btn', title: '編集',
        onClick: async () => {
          const projectTasks = await api.projectTasks(issue.project_id);
          const saved = await openIssueForm({
            project: { id: issue.project_id }, issue,
            tasks: projectTasks.tasks, linkedTaskIds: tasks.map((t) => t.id),
            members: projectTasks.members,
          });
          if (saved) reload();
        },
      }, '✏️')
      : null,
    canEdit
      ? el('button', {
        class: 'icon-btn', title: '削除',
        onClick: async () => {
          if (!await confirmDialog(
            `課題 #${issue.seq}「${issue.title}」を削除しますか？\n`
            + '間違えたら、ゴミ箱から 30 日以内に戻せます。',
            { danger: true, okLabel: '削除する' })) return;
          const result = await api.del(`/api/issues/${issue.id}`);
          instance.close();
          undoToast(`課題「${issue.title}」を削除しました`, () =>
            api.post(`/api/trash/${result.trash_id}/restore`, {}));
        },
      }, '🗑')
      : null,
    el('button', { class: 'icon-btn', title: '閉じる', onClick: () => instance.close() }, '×'));

  const body = el('div', { class: 'drawer-body' });

  const statusSelect = el('select', {
    class: 'select', disabled: !canEdit,
    onChange: (event) => patch({ status: event.target.value }),
  }, ...Object.entries(ISSUE_STATUS_LABEL).map(([value, label]) =>
    el('option', { value, selected: issue.status === value ? true : null }, label)));

  const severitySelect = el('select', {
    class: 'select', disabled: !canEdit,
    onChange: (event) => patch({ severity: Number(event.target.value) }),
  }, ...Object.entries(SEVERITY_LABEL).reverse().map(([value, label]) =>
    el('option', { value, selected: String(issue.severity) === value ? true : null }, label)));

  const categoryInput = issueCategorySelect(issue.category);
  categoryInput.disabled = !canEdit;
  categoryInput.addEventListener('change', (e) => patch({ category: e.target.value }));

  const ownerSelect = userSelect(issue.owner_id, { people: data.members });
  ownerSelect.disabled = !canEdit;
  ownerSelect.addEventListener('change', (e) =>
    patch({ owner_id: e.target.value ? Number(e.target.value) : null }));

  const dueInput = el('input', {
    class: 'input', type: 'date', value: issue.due_date || '', disabled: !canEdit,
    onChange: (event) => patch({ due_date: event.target.value || null }),
  });

  const stillOpen = !['resolved', 'closed'].includes(issue.status);
  const daysLeft = stillOpen ? dueDelta(issue.due_date) : null;
  if (daysLeft !== null && daysLeft < 0) {
    body.append(el('div', { class: 'warn-box danger' },
      `⏰ 対応期限を ${-daysLeft} 日超過しています。`));
  } else if (daysLeft !== null && daysLeft <= 3) {
    body.append(el('div', { class: 'warn-box' },
      daysLeft === 0 ? '⏰ 対応期限は本日です。' : `⏰ 対応期限まであと ${daysLeft} 日です。`));
  }
  if (issue.open_task_count) {
    body.append(el('div', { class: 'warn-box' },
      `📌 この課題に紐づく未完了タスクが ${issue.open_task_count} 件あります。`));
  }

  body.append(el('div', { class: 'detail-grid' },
    el('div', {}, el('span', { class: 'label', text: '状態' }), statusSelect),
    el('div', {}, el('span', { class: 'label', text: '区分' }), categoryInput),
    el('div', {}, el('span', { class: 'label', text: '影響度' }), severitySelect),
    el('div', {}, el('span', { class: 'label', text: '対応者' }), ownerSelect),
    el('div', {}, el('span', { class: 'label', text: '対応期限' }), dueInput)));

  body.append(el('div', { class: 'meta-row' },
    el('span', { class: 'cat-chip', style: {
      background: `${info.color}1f`, color: info.color, borderColor: `${info.color}55`,
    } }, info.label),
    el('span', { class: 'badge', text: `起票: ${issue.raised_by_name || '—'}` }),
    el('span', { class: 'badge', text: `発生日 ${formatDate(issue.raised_on)}` }),
    issue.resolved_on
      ? el('span', { class: 'badge done', text: `解決日 ${formatDate(issue.resolved_on)}` })
      : null));

  body.append(sectionTitle('内容・背景'));
  body.append(memoEditor({
    value: issue.description, canEdit, people: data.members,
    placeholder: '内容を入力…（URL はそのまま貼るとリンクになります）',
    onSave: (value) => patch({ description: value }),
  }));

  body.append(sectionTitle('対応方針・結果'));
  body.append(memoEditor({
    value: issue.resolution, canEdit, people: data.members,
    placeholder: '誰が・いつまでに・何をするか…',
    onSave: (value) => patch({ resolution: value }),
  }));

  /* ---- linked tasks ---- */
  body.append(sectionTitle(`関連タスク (${tasks.length})`,
    canEdit
      ? el('button', {
        class: 'btn btn-sm',
        onClick: () => editLinks(issue, tasks, reload),
      }, '＋ 紐づけ')
      : null));
  if (!tasks.length) {
    body.append(el('div', { class: 'hint', text: '紐づいているタスクはありません' }));
  } else {
    body.append(...tasks.map((task) => el('div', {
      class: 'att-item', style: { cursor: 'pointer' },
      onClick: () => openTaskDetail(task.id, { onChange: reload }),
    },
    el('span', { class: `badge ${task.status}`, text: STATUS_LABEL[task.status] }),
    el('span', { class: 'name', text: task.title }),
    el('span', { class: 'size', text: `${task.progress}%` }),
    task.due_date
      ? el('span', {
        class: `size cell-due ${dueClass(task.due_date, task.status)}`,
        text: formatDate(task.due_date),
      })
      : null)));
  }


  /* ---- もとになったチケット ---- */
  if ((linkedTickets || []).length) {
    body.append(sectionTitle(`関連チケット (${linkedTickets.length})`));
    body.append(...linkedTickets.map((ticket) => el('div', {
      class: 'att-item', style: { cursor: 'pointer' },
      onClick: async () => {
        const { openTicketDetail } = await import('./ticketDetail.js');
        openTicketDetail(ticket.id, { onChange: reload });
      },
    },
    el('span', { class: 'kind-tag ticket', text: 'チケット' }),
    el('span', { class: 'issue-no', text: `#${ticket.id}` }),
    el('span', { class: 'name', text: ticket.title }),
    el('span', { class: 'size', text: `${ticket.queue_icon || '📮'} ${ticket.queue_name}` }),
    ticket.on_behalf_of ? el('span', { class: 'size', text: ticket.on_behalf_of }) : null)));
  }

  /* ---- attachments ---- */
  body.append(sectionTitle(`リンク・ファイル (${attachments.length})`,
    canEdit ? el('button', { class: 'btn btn-sm', onClick: () => addLink(issue, reload) }, '🔗 リンク') : null));
  if (canEdit) body.append(dropzone(issue, reload));
  body.append(...attachments.map((att) => attachmentRow(att, canEdit, reload)));

  /* ---- history ---- */
  body.append(sectionTitle(`経緯・コメント (${comments.filter((c) => c.kind !== 'system').length})`));
  if (!comments.length) {
    body.append(el('div', { class: 'hint', text: 'まだコメントはありません' }));
  }
  body.append(...comments.map((comment) => commentRow(comment, reload, data.members)));

  if (canComment) {
    const input = el('textarea', {
      class: 'textarea',
      placeholder: '経緯や決まったことを記録…（@ でメンバーを呼べます / Ctrl+Enter で送信）',
      style: { minHeight: '64px' },
    });
    attachMentions(input, () => data.members || []);
    const send = async () => {
      const value = input.value.trim();
      if (!value) return;
      try {
        const result = await api.post(`/api/issues/${issue.id}/comments`, { body: value });
        input.value = '';
        if (result.mentioned?.length) {
          toast(`${result.mentioned.join('、')} さんに通知しました`, 'ok');
        }
        reload();
      } catch (error) { toast(error.message, 'error'); }
    };
    input.addEventListener('keydown', (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') send();
    });
    body.append(el('div', { style: { marginTop: '12px' } }, input,
      el('div', { style: { marginTop: '6px', textAlign: 'right' } },
        el('button', { class: 'btn btn-primary btn-sm', onClick: send }, '記録する'))));
  }

  fill(instance.drawer, head, body);
}

function sectionTitle(text, action) {
  return el('div', { class: 'section-title' },
    el('span', { text }), el('span', { class: 'line' }), action || null);
}

function commentRow(comment, reload, members) {
  const own = comment.user_id === store.user?.id;
  const canDelete = comment.kind !== 'system' && (own || store.isAdmin());
  return el('div', { class: `comment ${comment.kind}` },
    comment.kind === 'system'
      ? el('span', { class: 'avatar sm', style: { background: 'var(--border-strong)' } }, '⟳')
      : avatar({ name: comment.user_name, avatar_color: comment.avatar_color }, 'sm'),
    el('div', { class: 'comment-body' },
      el('div', { class: 'comment-meta' },
        el('strong', { text: comment.user_name || 'システム' }),
        el('span', { text: formatDateTime(comment.created_at) }),
        canDelete
          ? el('button', {
            class: 'btn btn-ghost btn-sm',
            onClick: async () => {
              await api.del(`/api/comments/${comment.id}`);
              reload();
            },
          }, '削除')
          : null),
      richText(comment.body, members)));
}

function attachmentRow(att, canEdit, reload) {
  const isFile = att.kind === 'file';
  return el('div', { class: 'att-item' },
    el('span', { text: isFile ? '📎' : '🔗' }),
    el('a', {
      class: 'name', href: isFile ? url(`/api/attachments/${att.id}/download`) : att.url,
      target: '_blank', rel: 'noopener noreferrer', text: att.name,
    }),
    isFile ? el('span', { class: 'size', text: formatBytes(att.size) }) : null,
    canEdit
      ? el('button', {
        class: 'icon-btn', title: '削除',
        onClick: async () => {
          if (!await confirmDialog(`「${att.name}」を削除しますか？`,
            { danger: true, okLabel: '削除' })) return;
          await api.del(`/api/attachments/${att.id}`);
          reload();
        },
      }, '×')
      : null);
}

function dropzone(issue, reload) {
  const input = el('input', { type: 'file', multiple: true, hidden: true });
  const zone = el('div', { class: 'dropzone' },
    `クリックまたはドラッグ＆ドロップでファイルを添付（1ファイル ${store.meta?.max_upload_mb || 25}MB まで）`);
  const upload = async (files) => {
    if (!files.length) return;
    zone.textContent = 'アップロード中…';
    try {
      for (const file of files) {
        const form = new FormData();
        form.append('file', file, file.name);
        await api.post(`/api/issues/${issue.id}/attachments`, form);
      }
      toast('添付しました', 'ok');
      reload();
    } catch (error) {
      toast(error.message, 'error');
      zone.textContent = '添付に失敗しました。もう一度お試しください。';
    }
  };
  zone.addEventListener('click', () => input.click());
  input.addEventListener('change', () => upload([...input.files]));
  zone.addEventListener('dragover', (event) => { event.preventDefault(); zone.classList.add('over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('over'));
  zone.addEventListener('drop', (event) => {
    event.preventDefault();
    zone.classList.remove('over');
    upload([...event.dataTransfer.files]);
  });
  return el('div', {}, zone, input);
}

async function addLink(issue, reload) {
  const url = el('input', { class: 'input', placeholder: 'https://…' });
  const name = el('input', { class: 'input', placeholder: '表示名（省略可）' });
  const result = await openModal({
    title: 'リンクを追加',
    build: () => el('div', {},
      el('div', { class: 'field' }, el('label', { text: 'URL *' }), url),
      el('div', { class: 'field' }, el('label', { text: '表示名' }), name)),
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', {
        class: 'btn btn-primary',
        onClick: () => close({ url: url.value.trim(), name: name.value.trim() }),
      }, '追加'),
    ],
  });
  if (!result?.url) return;
  try {
    await api.post(`/api/issues/${issue.id}/attachments`, result);
    reload();
  } catch (error) { toast(error.message, 'error'); }
}

async function editLinks(issue, linked, reload) {
  const data = await api.projectTasks(issue.project_id);
  const picker = taskPicker(data.tasks, linked.map((t) => t.id));
  const saved = await openModal({
    title: '関連タスクを選ぶ',
    build: () => el('div', { class: 'field' },
      el('label', { text: 'この課題に関係するタスク' }), picker.node),
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', {
        class: 'btn btn-primary',
        onClick: async () => {
          try {
            await api.put(`/api/issues/${issue.id}/tasks`, { task_ids: picker.ids() });
            close(true);
          } catch (error) { toast(error.message, 'error'); }
        },
      }, '保存'),
    ],
  });
  if (saved) reload();
}
