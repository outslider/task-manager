/* チケット詳細ドロワー。受付の状態、タスク・課題への受け渡し、やりとりの記録。
 *
 * チケットは社内の誰でも読み書きできる（プロジェクトの権限に乗らない）。
 * 消せるのは出した本人と管理者だけ。 */
import { api, url } from '../api.js';
import { STATUS_LABEL, store } from '../store.js';
import {
  avatar, confirmDialog, dueClass, dueDelta, el, fill, formatBytes, formatDate,
  formatDateTime, openDrawer, openModal, toast,
} from '../util.js';
import { categorySelect, chipPicker, option, userSelect } from './pickers.js';
import { memoEditor, openTaskDetail } from './taskDetail.js';
import { openIssueDetail } from './issueDetail.js';
import { openTicketForm } from './ticketForm.js';
import { attachMentions, richText } from './mention.js';

let openInstance = null;

function ticketMeta() {
  return store.meta?.tickets || { kinds: [], statuses: [], priorities: [] };
}

export async function openTicketDetail(ticketId, { onChange } = {}) {
  if (openInstance) openInstance.close();
  const instance = openDrawer({
    build: (drawer) => {
      drawer.appendChild(el('div', { class: 'empty', text: '読み込み中…' }));
    },
    onClose: () => { openInstance = null; if (onChange) onChange(); },
  });
  openInstance = instance;
  await renderDetail(instance, ticketId, onChange);
  return instance;
}

async function renderDetail(instance, ticketId, onChange) {
  let data;
  try {
    data = await api.get(`/api/tickets/${ticketId}`);
  } catch (error) {
    fill(instance.drawer, el('div', { class: 'empty', text: error.message }));
    return;
  }
  const { ticket, tasks, issues, comments, attachments } = data;
  const meta = ticketMeta();
  const kind = meta.kinds.find((k) => k.value === ticket.kind) || { icon: '', label: ticket.kind };
  const people = store.users || [];

  const reload = async () => {
    await renderDetail(instance, ticketId, onChange);
    if (onChange) onChange();
  };
  const patch = async (payload) => {
    try {
      await api.patch(`/api/tickets/${ticket.id}`, payload);
      reload();
    } catch (error) { toast(error.message, 'error'); }
  };

  const head = el('div', { class: 'drawer-head' },
    el('div', { style: { minWidth: 0, flex: 1 } },
      el('div', { class: 'breadcrumb' },
        `${ticket.queue_icon || '📮'} ${ticket.queue_name}`
        + (ticket.queue_project_name ? ` （${ticket.queue_project_name}）` : '')
        + ` ・ チケット #${ticket.id}`),
      el('h2', { text: ticket.title, style: { whiteSpace: 'normal' } })),
    el('button', {
      class: 'icon-btn', title: '編集',
      onClick: async () => { if (await openTicketForm({ ticket })) reload(); },
    }, '✏️'),
    data.can_delete
      ? el('button', {
        class: 'icon-btn', title: '削除',
        onClick: async () => {
          if (!await confirmDialog(
            `チケット #${ticket.id}「${ticket.title}」を削除しますか？\n`
            + 'やりとりの記録も消えます。紐づけたタスクや課題は残ります。',
            { danger: true, okLabel: '削除する' })) return;
          await api.del(`/api/tickets/${ticket.id}`);
          toast('削除しました', 'ok');
          instance.close();
        },
      }, '🗑')
      : null,
    el('button', { class: 'icon-btn', title: '閉じる', onClick: () => instance.close() }, '×'));

  const body = el('div', { class: 'drawer-body' });

  const statusSelect = el('select', {
    class: 'select',
    onChange: (event) => patch({ status: event.target.value }),
  }, ...meta.statuses.map((s) => option(s.value, s.label, ticket.status === s.value)));

  const kindSelect = el('select', {
    class: 'select',
    onChange: (event) => patch({ kind: event.target.value }),
  }, ...meta.kinds.map((k) => option(k.value, `${k.icon} ${k.label}`, ticket.kind === k.value)));

  // 分類はその窓口に登録されているときだけ出す
  const queueCats = (data.queue_categories || []);
  const categorySelect = queueCats.length
    ? el('select', {
      class: 'select',
      onChange: (event) => patch({
        category_id: event.target.value ? Number(event.target.value) : null,
      }),
    }, option('', '分類なし', !ticket.category_id),
    ...queueCats.map((c) => option(c.id, c.label,
      String(ticket.category_id || '') === String(c.id))))
    : null;

  const prioritySelect = el('select', {
    class: 'select',
    onChange: (event) => patch({ priority: Number(event.target.value) }),
  }, ...meta.priorities.map((p) =>
    option(p.value, p.label, String(ticket.priority) === String(p.value))));

  const assigneeSelect = userSelect(ticket.assignee_id, { people });
  assigneeSelect.addEventListener('change', (event) =>
    patch({ assignee_id: event.target.value ? Number(event.target.value) : null }));

  const dueInput = el('input', {
    class: 'input', type: 'date', value: ticket.due_date || '',
    onChange: (event) => patch({ due_date: event.target.value || null }),
  });

  // 対応時間は任意。入れておくと、あとで集計に出る。
  const spentInput = el('input', {
    class: 'input', type: 'number', step: '0.5', min: '0', placeholder: '—',
    value: ticket.spent_hours ?? '',
    onChange: (event) => patch({
      spent_hours: event.target.value === '' ? null : Number(event.target.value),
    }),
  });

  const stillOpen = (meta.open_statuses || []).includes(ticket.status);
  const daysLeft = stillOpen ? dueDelta(ticket.due_date) : null;
  if (daysLeft !== null && daysLeft < 0) {
    body.append(el('div', { class: 'warn-box danger' },
      `⏰ 期限を ${-daysLeft} 日超過しています。`));
  } else if (daysLeft !== null && daysLeft <= 3) {
    body.append(el('div', { class: 'warn-box' },
      daysLeft === 0 ? '⏰ 期限は本日です。' : `⏰ 期限まであと ${daysLeft} 日です。`));
  }
  if (stillOpen && !ticket.assignee_id) {
    body.append(el('div', { class: 'warn-box', text: '👤 担当がまだ決まっていません。' }));
  }
  if (ticket.open_task_count) {
    body.append(el('div', { class: 'warn-box',
      text: `✓ 対応中のタスクが ${ticket.open_task_count} 件あります。` }));
  }

  body.append(el('div', { class: 'detail-grid' },
    el('div', {}, el('span', { class: 'label', text: '状態' }), statusSelect),
    el('div', {}, el('span', { class: 'label', text: '種別' }), kindSelect),
    el('div', {}, el('span', { class: 'label', text: '優先度' }), prioritySelect),
    categorySelect
      ? el('div', {}, el('span', { class: 'label', text: '分類' }), categorySelect)
      : null,
    el('div', {}, el('span', { class: 'label', text: '担当' }), assigneeSelect),
    el('div', {}, el('span', { class: 'label', text: '期限' }), dueInput),
    el('div', {}, el('span', { class: 'label', text: '対応時間 (h)' }), spentInput)));

  body.append(el('div', { class: 'meta-row' },
    el('span', { class: 'badge', text: `${kind.icon} ${kind.label}` }),
    ticket.category_label
      ? el('span', { class: 'cat-chip', style: {
        background: `${ticket.category_color}1f`, color: ticket.category_color,
        borderColor: `${ticket.category_color}55`,
      } }, ticket.category_label)
      : null,
    el('span', { class: 'badge', text: `起票: ${ticket.requester_name || '—'}` }),
    ticket.on_behalf_of
      ? el('span', { class: 'badge', text: `依頼元: ${ticket.on_behalf_of}` })
      : null,
    el('span', { class: 'badge', text: `受付 ${formatDateTime(ticket.created_at)}` }),
    ticket.occurred_at
      ? el('span', { class: 'badge blocked', text: `発生 ${formatDateTime(ticket.occurred_at)}` })
      : null,
    ticket.resolved_at
      ? el('span', { class: 'badge done', text: `対応完了 ${formatDateTime(ticket.resolved_at)}` })
      : null));

  body.append(sectionTitle('内容'));
  body.append(memoEditor({
    value: ticket.body, canEdit: true, people,
    placeholder: '困っていること、してほしいこと…',
    onSave: (value) => patch({ body: value }),
  }));

  body.append(sectionTitle('対応結果'));
  body.append(memoEditor({
    value: ticket.resolution, canEdit: true, people,
    placeholder: '何をして、どう返したか…',
    onSave: (value) => patch({ resolution: value }),
  }));

  /* ---- タスクへ渡す ---- */
  body.append(sectionTitle(`関連タスク (${tasks.length})`,
    el('span', { style: { display: 'flex', gap: '6px' } },
      el('button', { class: 'btn btn-sm btn-primary',
        onClick: () => makeTask(ticket, reload) }, '＋ タスクにする'),
      el('button', { class: 'btn btn-sm',
        onClick: () => editTaskLinks(ticket, tasks, reload) }, '既存に紐づけ'))));
  if (!tasks.length) {
    body.append(el('div', { class: 'hint',
      text: '作業が要るものは「タスクにする」でプロジェクトへ渡せます' }));
  } else {
    body.append(...tasks.map((task) => el('div', {
      class: 'att-item', style: { cursor: 'pointer' },
      onClick: () => openTaskDetail(task.id, { onChange: reload }),
    },
    el('span', { class: `badge ${task.status}`, text: STATUS_LABEL[task.status] }),
    el('span', { class: 'name', text: task.title }),
    el('span', { class: 'size', text: task.project_name }),
    task.due_date
      ? el('span', { class: `size cell-due ${dueClass(task.due_date, task.status)}`,
        text: formatDate(task.due_date) })
      : null)));
  }

  /* ---- 課題へ渡す ---- */
  body.append(sectionTitle(`関連課題 (${issues.length})`,
    el('button', { class: 'btn btn-sm',
      onClick: () => makeIssue(ticket, reload) }, '＋ 課題にする')));
  if (issues.length) {
    body.append(...issues.map((issue) => el('div', {
      class: 'att-item', style: { cursor: 'pointer' },
      onClick: () => openIssueDetail(issue.id, { onChange: reload }),
    },
    el('span', { text: '📌' }),
    el('span', { class: 'name', text: `#${issue.seq} ${issue.title}` }),
    el('span', { class: 'size', text: issue.project_name }))));
  }

  /* ---- 添付 ---- */
  body.append(sectionTitle(`リンク・ファイル (${attachments.length})`,
    el('button', { class: 'btn btn-sm', onClick: () => addLink(ticket, reload) }, '🔗 リンク')));
  body.append(dropzone(ticket, reload));
  body.append(...attachments.map((att) => attachmentRow(att, reload)));

  /* ---- やりとり ---- */
  const said = comments.filter((c) => c.kind !== 'system').length;
  body.append(sectionTitle(`やりとり・経緯 (${said})`));
  if (!comments.length) {
    body.append(el('div', { class: 'hint', text: 'まだやりとりはありません' }));
  }
  body.append(...comments.map((comment) => commentRow(comment, reload, people)));

  const input = el('textarea', {
    class: 'textarea',
    placeholder: '対応の経過や回答を記録…（@ でメンバーを呼べます / Ctrl+Enter で送信）',
    style: { minHeight: '64px' },
  });
  attachMentions(input, () => people);
  const send = async () => {
    const value = input.value.trim();
    if (!value) return;
    try {
      const result = await api.post(`/api/tickets/${ticket.id}/comments`, { body: value });
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

  fill(instance.drawer, head, body);
}

function sectionTitle(text, action) {
  return el('div', { class: 'section-title' },
    el('span', { text }), el('span', { class: 'line' }), action || null);
}

/** 書き込めるプロジェクトだけを選ばせる。権限のないところへは渡せない。 */
function writableProjects() {
  return (store.projects || []).filter((p) => !p.archived && store.canEdit(p));
}

/** 窓口にプロジェクトが紐づいていれば、そこを最初から選んでおく。 */
function projectSelect(ticket, projects) {
  return el('select', { class: 'select' },
    ...projects.map((p) => option(
      p.id, p.name, String(ticket.queue_project_id || '') === String(p.id))));
}

async function makeTask(ticket, reload) {
  const projects = writableProjects();
  if (!projects.length) {
    toast('タスクを追加できるプロジェクトがありません', 'error');
    return;
  }
  const project = projectSelect(ticket, projects);
  const title = el('input', { class: 'input' });
  title.value = ticket.title;
  const category = categorySelect('');
  const assignee = userSelect(ticket.assignee_id, { people: store.users });
  const due = el('input', { class: 'input', type: 'date', value: ticket.due_date || '' });

  const made = await openModal({
    title: 'このチケットをタスクにする',
    build: () => el('div', {},
      el('p', { class: 'page-sub',
        text: 'チケットの内容をタスクの説明に写します。作ったあとも両方から行き来できます。' }),
      el('div', { class: 'field' }, el('label', { text: '登録先プロジェクト *' }), project),
      el('div', { class: 'field' }, el('label', { text: 'タスク名 *' }), title),
      el('div', { class: 'row' },
        el('div', { class: 'field' }, el('label', { text: 'カテゴリ' }), category),
        el('div', { class: 'field' }, el('label', { text: '担当' }), assignee),
        el('div', { class: 'field' }, el('label', { text: '期限' }), due))),
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', {
        class: 'btn btn-primary',
        onClick: async (event) => {
          if (!title.value.trim()) { toast('タスク名を入れてください', 'error'); return; }
          const button = event.currentTarget;
          button.disabled = true;
          try {
            await api.post(`/api/tickets/${ticket.id}/task`, {
              project_id: Number(project.value),
              title: title.value.trim(),
              category: category.value,
              assignee_id: assignee.value ? Number(assignee.value) : null,
              due_date: due.value || null,
            });
            toast('タスクを作りました', 'ok');
            close(true);
          } catch (error) {
            toast(error.message, 'error');
            button.disabled = false;
          }
        },
      }, '作成する'),
    ],
  });
  if (made) reload();
}

async function makeIssue(ticket, reload) {
  const projects = writableProjects();
  if (!projects.length) {
    toast('課題を追加できるプロジェクトがありません', 'error');
    return;
  }
  const project = projectSelect(ticket, projects);
  const title = el('input', { class: 'input' });
  title.value = ticket.title;

  const made = await openModal({
    title: 'このチケットを課題にする',
    build: () => el('div', {},
      el('p', { class: 'page-sub',
        text: '作業ではなく、決めないと進まない論点だったときに使います。' }),
      el('div', { class: 'field' }, el('label', { text: '登録先プロジェクト *' }), project),
      el('div', { class: 'field' }, el('label', { text: '課題 *' }), title)),
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', {
        class: 'btn btn-primary',
        onClick: async (event) => {
          if (!title.value.trim()) { toast('課題名を入れてください', 'error'); return; }
          const button = event.currentTarget;
          button.disabled = true;
          try {
            await api.post(`/api/tickets/${ticket.id}/issue`, {
              project_id: Number(project.value), title: title.value.trim(),
            });
            toast('課題に登録しました', 'ok');
            close(true);
          } catch (error) {
            toast(error.message, 'error');
            button.disabled = false;
          }
        },
      }, '登録する'),
    ],
  });
  if (made) reload();
}

async function editTaskLinks(ticket, linked, reload) {
  const { tasks } = await api.get('/api/tasks?status=all&limit=500');
  const picker = chipPicker(tasks, linked.map((t) => t.id), {
    placeholder: 'このチケットに関係するタスクを選ぶ…',
    emptyText: '紐づけたタスクはありません',
    exhausted: '選べるタスクがありません',
    labelOf: (task) => `${task.title}（${task.project_name}）`,
  });
  const saved = await openModal({
    title: '既存のタスクに紐づける',
    build: () => el('div', { class: 'field' },
      el('label', { text: 'このチケットに関係するタスク' }), picker.node),
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', {
        class: 'btn btn-primary',
        onClick: async () => {
          try {
            await api.put(`/api/tickets/${ticket.id}/tasks`, { task_ids: picker.ids() });
            close(true);
          } catch (error) { toast(error.message, 'error'); }
        },
      }, '保存'),
    ],
  });
  if (saved) reload();
}

function commentRow(comment, reload, people) {
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
      richText(comment.body, people)));
}

function attachmentRow(att, reload) {
  const isFile = att.kind === 'file';
  return el('div', { class: 'att-item' },
    el('span', { text: isFile ? '📎' : '🔗' }),
    el('a', {
      class: 'name', href: isFile ? url(`/api/attachments/${att.id}/download`) : att.url,
      target: '_blank', rel: 'noopener noreferrer', text: att.name,
    }),
    isFile ? el('span', { class: 'size', text: formatBytes(att.size) }) : null,
    el('button', {
      class: 'icon-btn', title: '削除',
      onClick: async () => {
        if (!await confirmDialog(`「${att.name}」を削除しますか？`,
          { danger: true, okLabel: '削除' })) return;
        await api.del(`/api/attachments/${att.id}`);
        reload();
      },
    }, '×'));
}

function dropzone(ticket, reload) {
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
        await api.post(`/api/tickets/${ticket.id}/attachments`, form);
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
  zone.addEventListener('dragover', (event) => {
    event.preventDefault();
    zone.classList.add('over');
  });
  zone.addEventListener('dragleave', () => zone.classList.remove('over'));
  zone.addEventListener('drop', (event) => {
    event.preventDefault();
    zone.classList.remove('over');
    upload([...event.dataTransfer.files]);
  });
  return el('div', {}, zone, input);
}

async function addLink(ticket, reload) {
  const link = el('input', { class: 'input', placeholder: 'https://…' });
  const name = el('input', { class: 'input', placeholder: '表示名（省略可）' });
  const result = await openModal({
    title: 'リンクを追加',
    build: () => el('div', {},
      el('div', { class: 'field' }, el('label', { text: 'URL *' }), link),
      el('div', { class: 'field' }, el('label', { text: '表示名' }), name)),
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', {
        class: 'btn btn-primary',
        onClick: () => close({ url: link.value.trim(), name: name.value.trim() }),
      }, '追加'),
    ],
  });
  if (!result?.url) return;
  try {
    await api.post(`/api/tickets/${ticket.id}/attachments`, result);
    reload();
  } catch (error) { toast(error.message, 'error'); }
}
