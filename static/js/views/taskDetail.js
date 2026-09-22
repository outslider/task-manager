/* Task detail drawer: inline editing, subtasks, links/files, comments. */
import { api, url } from '../api.js';
import {
  store, STATUS_LABEL, IMPORTANCE_LABEL, ISSUE_STATUS_LABEL, SEVERITY_LABEL, } from
  '../store.js'; import {   avatar, confirmDialog, dueClass, dueLabel, el, fill,
  formatBytes, formatDate, formatDateTime, openDrawer, openModal, skeleton, toast,
  undoToast,
} from '../util.js';
import { icon } from '../icons.js';
import { openTaskForm } from './taskForm.js';
import { categorySelect, userSelect } from './pickers.js';
import { openParentPicker, setParent } from './hierarchy.js';
import { attachMentions, richText } from './mention.js';

let openInstance = null;

export async function openTaskDetail(taskId, { onChange } = {}) {
  if (openInstance) openInstance.close();
  const instance = openDrawer({
    build: (drawer) => {
      drawer.appendChild(skeleton('text', 3));
    },
    onClose: () => { openInstance = null; if (onChange) onChange(); },
  });
  openInstance = instance;
  await renderDetail(instance, taskId, onChange);
  return instance;
}

async function renderDetail(instance, taskId, onChange) {
  let data;
  try {
    data = await api.task(taskId);
  } catch (error) {
    fill(instance.drawer, el('div', { class: 'empty', text: error.message }));
    return;
  }
  const {
    task, path, children, comments, attachments, deps, blocking,
    metrics = {}, impact = [], conflicts = [], issues = [], tickets = [],
    members = null, my_role: role,
  } = data;
  const canEdit = role === 'owner' || role === 'editor';
  const canComment = canEdit || role === 'commenter';
  const reload = async () => {
    await renderDetail(instance, taskId, onChange);
    if (onChange) onChange();
  };

  const head = el('div', { class: 'drawer-head' },
    el('div', { style: { minWidth: 0, flex: 1 } },
      el('div', { class: 'breadcrumb' },
        el('span', { text: task.project_name }),
        ...path.map((p) => [' / ', el('a', {
          href: '#', onClick: (event) => { event.preventDefault(); openTaskDetail(p.id, { onChange }); },
          text: p.title,
        })]).flat()),
      el('h2', { text: task.title, style: { whiteSpace: 'normal' } })),
    canEdit
      ? el('button', {
        class: 'icon-btn', title: '編集',
        onClick: async () => {
          const projectTasks = await api.projectTasks(task.project_id);
          const saved = await openTaskForm({
            project: { id: task.project_id }, task,
            tasks: projectTasks.tasks, deps: projectTasks.deps,
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
          const message = children.length
            ? `「${task.title}」と配下の子タスクをすべて削除します。よろしいですか？`
            : `「${task.title}」を削除します。よろしいですか？`;
          if (!await confirmDialog(`${message}\n間違えたら、ゴミ箱から 30 日以内に戻せます。`,
            { danger: true, okLabel: '削除する' })) return;
          const result = await api.del(`/api/tasks/${task.id}`);
          instance.close();
          undoToast(`「${task.title}」を削除しました`, () =>
            api.post(`/api/trash/${result.trash_id}/restore`, {}));
        },
      }, icon('trash'))
      : null,
    el('button', { class: 'icon-btn', title: '閉じる', onClick: () => instance.close() },
      icon('close')));

  const body = el('div', { class: 'drawer-body' });

  /* ---- quick controls ---- */
  const patch = async (payload) => {
    try {
      await api.patch(`/api/tasks/${task.id}`, payload);
      reload();
    } catch (error) { toast(error.message, 'error'); }
  };

  const statusSelect = el('select', {
    class: 'select', disabled: !canEdit,
    onChange: (event) => patch({ status: event.target.value }),
  }, ...Object.entries(STATUS_LABEL).map(([value, label]) =>
    el('option', { value, selected: task.status === value ? true : null }, label)));

  const progressValue = el('strong', { text: `${task.progress}%` });
  const progressInput = el('input', {
    type: 'range', class: 'range', min: 0, max: 100, step: 5, value: task.progress,
    disabled: !canEdit, style: { width: '100%' },
    onInput: (event) => { progressValue.textContent = `${event.target.value}%`; },
    onChange: (event) => patch({ progress: Number(event.target.value) }),
  });

  const assignee = userSelect(task.assignee_id, { people: members });
  assignee.disabled = !canEdit;
  assignee.addEventListener('change', (event) =>
    patch({ assignee_id: event.target.value ? Number(event.target.value) : null }));

  const dueInput = el('input', {
    class: 'input', type: 'date', value: task.due_date || '', disabled: !canEdit,
    onChange: (event) => patch({ due_date: event.target.value || null }),
  });
  const startInput = el('input', {
    class: 'input', type: 'date', value: task.start_date || '', disabled: !canEdit,
    onChange: (event) => patch({ start_date: event.target.value || null }),
  });

  const categoryInput = categorySelect(task.category || '');
  categoryInput.disabled = !canEdit;
  categoryInput.addEventListener('change', (event) =>
    patch({ category: event.target.value }));

  const hoursInput = (key, value) => {
    const node = el('input', {
      class: 'input', type: 'number', min: 0, step: 0.5, placeholder: '任意',
      value: value ?? '', disabled: !canEdit,
      onChange: (event) => patch({ [key]: event.target.value === '' ? null : Number(event.target.value) }),
    });
    return node;
  };

  body.append(warnings(task, metrics, conflicts));
  body.append(el('div', { class: 'detail-grid' },
    el('div', { class: 'detail-wide' }, el('span', { class: 'label', text: '親タスク' }),
      parentControl(task, path, canEdit, reload, onChange)),
    el('div', {}, el('span', { class: 'label', text: '状態' }), statusSelect),
    el('div', {}, el('span', { class: 'label', text: 'カテゴリ' }), categoryInput),
    el('div', {}, el('span', { class: 'label', text: '担当者' }), assignee),
    el('div', {}, el('span', { class: 'label', text: '開始日' }), startInput),
    el('div', {}, el('span', { class: 'label', text: '期限' }), dueInput),
    el('div', {}, el('span', { class: 'label', text: '見積 (h)' }),
      hoursInput('estimate_hours', task.estimate_hours)),
    el('div', {}, el('span', { class: 'label', text: '実績 (h)' }),
      hoursInput('actual_hours', Number(task.actual_hours) || null))));

  const overdueLabel = dueLabel(task.due_date, task.status);
  body.append(el('div', { style: { marginTop: '12px' } },
    el('div', { style: { display: 'flex', alignItems: 'center', gap: '10px' } },
      el('span', { class: 'label', text: '進捗', style: { margin: 0 } }), progressValue,
      el('span', { class: `badge ${task.status}`, text: STATUS_LABEL[task.status] }),
      el('span', { class: `prio prio-${task.priority}`, text: `重要度: ${IMPORTANCE_LABEL[task.priority]}` }),
      task.is_milestone ? el('span', { class: 'badge', text: '◆ マイルストーン' }) : null,
      overdueLabel
        ? el('span', { class: `badge ${dueClass(task.due_date, task.status) || 'soon'}`, text: overdueLabel })
        : null),
    progressInput));

  /* ---- description ---- */
  body.append(sectionTitle('メモ'));
  body.append(memoEditor({
    value: task.description, canEdit, people: members,
    placeholder: 'メモを入力…（URL はそのまま貼るとリンクになります）',
    onSave: (value) => patch({ description: value }),
  }));

  /* ---- subtasks ---- */
  body.append(sectionTitle(`子タスク (${children.length})`,
    canEdit
      ? el('span', { style: { display: 'flex', gap: '6px' } },
        el('button', {
          class: 'btn btn-sm',
          title: '内容から子タスクの候補を作ります',
          onClick: async () => {
            const { openSubtaskSuggestions } = await import('./decompose.js');
            const added = await openSubtaskSuggestions(task);
            if (added) reload();
          },
        }, '✨ 分解を提案'),
        el('button', {
          class: 'btn btn-sm',
          onClick: async () => {
            const projectTasks = await api.projectTasks(task.project_id);
            const saved = await openTaskForm({
              project: { id: task.project_id }, parentId: task.id,
              tasks: projectTasks.tasks, deps: projectTasks.deps,
              members: projectTasks.members,
            });
            if (saved) reload();
          },
        }, '＋ 追加'))
      : null));
  if (children.length === 0) {
    body.append(el('div', { class: 'hint', text: '子タスクはありません' }));
  } else {
    body.append(...children.map((child) => el('div', {
      class: 'att-item', style: { cursor: 'pointer' },
      onClick: () => openTaskDetail(child.id, { onChange }),
    },
    el('span', { class: `badge ${child.status}`, text: STATUS_LABEL[child.status] }),
    el('span', { class: 'name', text: child.title }),
    el('span', { class: 'size', text: `${child.progress}%` }),
    child.due_date
      ? el('span', {
        class: `size cell-due ${dueClass(child.due_date, child.status)}`,
        text: formatDate(child.due_date),
      })
      : null)));
  }

  /* ---- dependencies ---- */
  body.append(sectionTitle('先行タスク（このタスクの前に完了が必要）',
    canEdit
      ? el('button', {
        class: 'btn btn-sm',
        onClick: () => addDependency(task, reload),
      }, '＋ 追加')
      : null));
  if (deps.length === 0) {
    body.append(el('div', { class: 'hint', text: '設定なし' }));
  } else {
    body.append(...deps.map((dep) => el('div', { class: 'att-item' },
      el('span', { class: `badge ${dep.status}`, text: STATUS_LABEL[dep.status] }),
      el('span', {
        class: 'name', style: { cursor: 'pointer' },
        onClick: () => openTaskDetail(dep.id, { onChange }), text: dep.title,
      }),
      canEdit
        ? el('button', {
          class: 'icon-btn', title: '解除',
          onClick: async () => {
            await api.del(`/api/tasks/${task.id}/deps/${dep.id}`);
            reload();
          },
        }, '×')
        : null)));
  }
  /* ---- downstream impact ---- */
  body.append(sectionTitle('このタスクが遅れると影響する範囲',
    el('span', {
      class: metrics.blocks_open ? 'badge blocking' : 'badge',
      text: `${metrics.blocks_open || 0} 件が待機`,
    })));
  if (!impact.length) {
    body.append(el('div', { class: 'hint', text: '後続タスクはありません' }));
  } else {
    const direct = new Set(blocking.map((b) => b.id));
    body.append(...impact.map((item) => el('div', {
      class: 'att-item', style: { cursor: 'pointer' },
      onClick: () => openTaskDetail(item.id, { onChange }),
    },
    el('span', { class: `badge ${item.status}`, text: STATUS_LABEL[item.status] }),
    el('span', { class: 'name', text: item.title }),
    el('span', { class: 'size', text: direct.has(item.id) ? '直後' : '間接' }),
    item.due_date ? el('span', { class: 'size', text: formatDate(item.due_date) }) : null)));
  }


  /* ---- もとになったチケット ---- */
  if ((tickets || []).length) {
    body.append(sectionTitle(`関連チケット (${tickets.length})`));
    body.append(...tickets.map((ticket) => el('div', {
      class: 'att-item', style: { cursor: 'pointer' },
      onClick: async () => {
        const { openTicketDetail } = await import('./ticketDetail.js');
        openTicketDetail(ticket.id, { onChange });
      },
    },
    el('span', { class: 'kind-tag ticket', text: 'チケット' }),
    el('span', { class: 'issue-no', text: `#${ticket.id}` }),
    el('span', { class: 'name', text: ticket.title }),
    el('span', { class: 'size', text: `${ticket.queue_icon || '📮'} ${ticket.queue_name}` }),
    ticket.on_behalf_of ? el('span', { class: 'size', text: ticket.on_behalf_of }) : null)));
  }

  /* ---- linked issues ---- */
  if (issues.length) {
    body.append(sectionTitle(`関連する課題 (${issues.length})`));
    body.append(...issues.map((issue) => el('div', {
      class: 'att-item', style: { cursor: 'pointer' },
      onClick: async () => {
        const { openIssueDetail } = await import('./issueDetail.js');
        openIssueDetail(issue.id, { onChange });
      },
    },
    el('span', { class: 'issue-no', text: `#${issue.seq}` }),
    el('span', { class: 'name', text: issue.title }),
    el('span', { class: `badge ${issue.status}`, text: ISSUE_STATUS_LABEL[issue.status] }),
    el('span', { class: `size sev sev-${issue.severity}`, text: SEVERITY_LABEL[issue.severity] }))));
  }

  /* ---- attachments ---- */
  body.append(sectionTitle(`リンク・ファイル (${attachments.length})`,
    canEdit ? el('button', { class: 'btn btn-sm', onClick: () => addLink(task, reload) }, '🔗 リンク') : null));
  if (canEdit) body.append(dropzone(task, reload));
  body.append(...attachments.map((att) => attachmentRow(att, canEdit, reload)));

  /* ---- comments ---- */
  body.append(sectionTitle(`コメント・履歴 (${comments.filter((c) => c.kind !== 'system').length})`));
  const list = el('div', {});
  if (comments.length === 0) {
    list.append(el('div', { class: 'hint', text: 'まだコメントはありません' }));
  }
  for (const comment of comments) {
    list.append(commentRow(comment, reload, members));
  }
  body.append(list);

  if (canComment) {
    const input = el('textarea', {
      class: 'textarea',
      placeholder: '進捗や気づいたことをコメント…（@ でメンバーを呼べます / Ctrl+Enter で送信）',
      style: { minHeight: '64px' },
    });
    attachMentions(input, () => members || []);
    const send = async () => {
      const value = input.value.trim();
      if (!value) return;
      try {
        const result = await api.post(`/api/tasks/${task.id}/comments`, { body: value });
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
        el('button', { class: 'btn btn-primary btn-sm', onClick: send }, 'コメントする'))));
  }

  fill(instance.drawer, head, body);
}

/** Blocked / critical-path / date-conflict notices at the top of the drawer. */
function warnings(task, metrics, conflicts) {
  const box = el('div', {});
  if (metrics.is_blocked) {
    box.append(el('div', { class: 'warn-box' },
      `⏳ 先行タスク ${metrics.blocked_by_open} 件が未完了のため、まだ着手できません。`));
  }
  if (metrics.blocks_open > 0) {
    box.append(el('div', { class: 'warn-box' },
      `⛔ このタスクが終わらないと ${metrics.blocks_open} 件のタスクが進められません。`
      + (metrics.is_critical ? ' クリティカルパス上にあります。' : '')));
  } else if (metrics.is_critical && task.status !== 'done') {
    box.append(el('div', { class: 'warn-box' },
      '🔗 クリティカルパス上のタスクです。遅れるとプロジェクト全体が同じだけ遅れます。'));
  }
  for (const conflict of conflicts) {
    box.append(el('div', { class: 'warn-box danger' },
      `⚠ 「${conflict.depends_on_title}」の期限が「${conflict.task_title}」の開始日より `
      + `${conflict.overlap_days} 日あとです。日程が矛盾しています。`));
  }
  return box;
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
      ? el('span', { class: 'avatar sm', style: { background: 'var(--border-strong)' }, text: '⟳' })
      : avatar({ name: comment.user_name, avatar_color: comment.avatar_color }, 'sm'),
    el('div', { class: 'comment-body' },
      el('div', { class: 'comment-meta' },
        el('strong', { text: comment.user_name || 'システム' }),
        el('span', { text: formatDateTime(comment.created_at) }),
        comment.kind === 'checkin' ? el('span', { class: 'badge', text: '日次更新' }) : null,
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
  const label = el('a', {
    class: 'name', href: isFile ? url(`/api/attachments/${att.id}/download`) : att.url,
    target: '_blank', rel: 'noopener noreferrer', text: att.name,
  });
  return el('div', { class: 'att-item' },
    el('span', { text: isFile ? '📎' : '🔗' }),
    label,
    isFile ? el('span', { class: 'size', text: formatBytes(att.size) }) : null,
    canEdit
      ? el('button', {
        class: 'icon-btn', title: '削除',
        onClick: async () => {
          if (!await confirmDialog(`「${att.name}」を削除しますか？`,
            { danger: true, okLabel: '削除する' })) return;
          await api.del(`/api/attachments/${att.id}`);
          reload();
        },
      }, '×')
      : null);
}

function dropzone(task, reload) {
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
        await api.post(`/api/tasks/${task.id}/attachments`, form);
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

async function addLink(task, reload) {
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
    await api.post(`/api/tasks/${task.id}/attachments`, result);
    reload();
  } catch (error) { toast(error.message, 'error'); }
}

async function addDependency(task, reload) {
  const data = await api.projectTasks(task.project_id);
  const candidates = data.tasks.filter((t) => t.id !== task.id);
  const select = el('select', { class: 'select' },
    ...candidates.map((t) => el('option', { value: t.id }, t.title)));
  const chosen = await openModal({
    title: '先行タスクを追加',
    build: () => el('div', { class: 'field' },
      el('label', { text: 'このタスクの前に完了しておくタスク' }), select),
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', { class: 'btn btn-primary', onClick: () => close(select.value) }, '追加'),
    ],
  });
  if (!chosen) return;
  try {
    await api.post(`/api/tasks/${task.id}/deps`, { depends_on_id: Number(chosen) });
    reload();
  } catch (error) { toast(error.message, 'error'); }
}


/** 親タスクの表示と付け替え。ドラッグの効かない端末でもここから変えられる。 */
function parentControl(task, path, canEdit, reload, onChange) {
  const parent = path.length ? path[path.length - 1] : null;
  const label = parent
    ? el('a', {
      href: '#', class: 'parent-current',
      onClick: (event) => { event.preventDefault(); openTaskDetail(parent.id, { onChange }); },
      text: parent.title,
    })
    : el('span', { class: 'cell-mut', text: 'トップレベル' });

  if (!canEdit) return el('div', { class: 'parent-row' }, label);

  return el('div', { class: 'parent-row' }, label,
    el('button', {
      class: 'btn btn-sm',
      onClick: async () => {
        const projectTasks = await api.projectTasks(task.project_id);
        const chosen = await openParentPicker(task, projectTasks.tasks);
        if (chosen === undefined) return;
        if (await setParent(task, chosen, projectTasks.tasks)) {
          toast(chosen ? '階層を移動しました' : 'トップレベルに移動しました', 'ok');
          reload();
        }
      },
    }, '変更'),
    parent
      ? el('button', {
        class: 'btn btn-sm',
        title: '一つ上の階層に出す',
        onClick: async () => {
          const projectTasks = await api.projectTasks(task.project_id);
          const grandParent = path.length > 1 ? path[path.length - 2].id : null;
          if (await setParent(task, grandParent, projectTasks.tasks)) {
            toast('階層を移動しました', 'ok');
            reload();
          }
        },
      }, '⇤ 一つ上へ')
      : null);
}


/**
 * メモ欄。ふだんは本文を表示し（URL はリンクとして開ける）、
 * クリックすると編集に切り替わる。
 */
export function memoEditor({ value, canEdit, people, placeholder, onSave }) {
  const host = el('div', { class: 'memo' });

  const showView = () => {
    const text = value || '';
    const view = text
      ? richText(text, people, 'comment-text memo-view')
      : el('div', { class: 'comment-text memo-view is-blank',
        text: canEdit ? 'クリックしてメモを書く' : '（メモなし）' });
    if (canEdit) {
      view.classList.add('editable');
      view.title = 'クリックで編集';
      // リンクを踏んだときは編集に入らない
      view.addEventListener('click', (event) => {
        if (event.target.closest('a')) return;
        showEdit();
      });
    }
    fill(host, view);
  };

  const showEdit = () => {
    const area = el('textarea', { class: 'textarea', placeholder });
    area.value = value || '';
    const save = el('button', {
      class: 'btn btn-sm btn-primary',
      onClick: async () => {
        const next = area.value;
        if (next !== (value || '')) {
          value = next;
          await onSave(next);
        }
        showView();
      },
    }, '保存');
    const cancel = el('button', { class: 'btn btn-sm', onClick: () => showView() }, 'キャンセル');
    area.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') showView();
      if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') save.click();
    });
    fill(host, area,
      el('div', { style: { display: 'flex', gap: '6px', marginTop: '6px' } }, save, cancel,
        el('span', { class: 'hint', style: { margin: 'auto 0 auto 4px' },
          text: 'Ctrl+Enter で保存' })));
    area.focus();
    area.setSelectionRange(area.value.length, area.value.length);
  };

  showView();
  return host;
}
