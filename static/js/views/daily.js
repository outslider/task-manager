/* Daily check-in: review everything due and update it in a couple of clicks. */
import { api } from '../api.js';
import { setHeader } from '../app.js';
import { store, STATUS_LABEL, ISSUE_STATUS_LABEL, SEVERITY_LABEL } from '../store.js';
import { clear, dueClass, el, fill, formatDate, formatDateTime, toast } from '../util.js';
import { openTaskDetail } from './taskDetail.js';
import { openIssueDetail } from './issueDetail.js';

const BUCKETS = [
  { key: 'overdue', label: '期限超過', tone: 'overdue', icon: '🔥' },
  { key: 'today', label: '本日期限', tone: 'soon', icon: '📌' },
  { key: 'soon', label: 'まもなく期限', tone: 'soon', icon: '⏳' },
  { key: 'no_due', label: '期限未設定', tone: '', icon: '❓' },
  { key: 'later', label: '先の予定', tone: '', icon: '🗓' },
];

export async function render(container) {
  const data = await api.daily();
  const pending = new Map();   // task_id -> { progress, status, note }

  setHeader('今日の確認', [
    el('a', { class: 'btn', href: '#/mytasks' }, 'マイタスク一覧'),
  ]);

  const counts = {
    overdue: data.buckets.overdue.length,
    today: data.buckets.today.length,
    soon: data.buckets.soon.length,
    open: Object.values(data.buckets).reduce((sum, list) => sum + list.length, 0)
      - data.buckets.later.length,
  };

  const saveBar = el('div', { class: 'sticky-save', hidden: true });
  const noteInput = el('input', {
    class: 'input', placeholder: '今日のひとこと（任意・チェックイン記録に残ります）',
  });
  noteInput.value = data.checkin?.note || '';

  const listHost = el('div', {});

  fill(container, 
    el('div', { class: 'page-head' },
      el('div', { class: 'grow' },
        el('h1', { text: `${greeting()}、${store.user.name} さん` }),
        el('div', { class: 'page-sub' },
          counts.open > 0
            ? `${formatDate(data.date, true)} — 対応が必要なタスク ${counts.open} 件`
            : `${formatDate(data.date, true)} — 期限が迫っているタスクはありません`,
          data.streak > 0
            ? el('span', { class: 'badge', style: { marginLeft: '8px' },
              text: `🔥 ${data.streak}日連続チェックイン` })
            : null))),
    el('div', { class: 'grid cols-4', style: { marginBottom: '14px' } },
      statCard('期限超過', counts.overdue, counts.overdue ? 'danger' : ''),
      statCard('本日期限', counts.today, counts.today ? 'warn' : ''),
      statCard('まもなく期限', counts.soon, ''),
      statCard('今週の完了', data.recently_done.length, 'ok')),
    listHost, saveBar);

  function statCard(label, value, tone) {
    return el('div', { class: 'card stat' },
      el('div', { class: 'k', text: label }),
      el('div', { class: `v ${tone}`.trim(), text: String(value) }));
  }

  function draw() {
    clear(listHost);
    let rendered = 0;
    for (const bucket of BUCKETS) {
      const items = data.buckets[bucket.key] || [];
      if (!items.length) continue;
      rendered += items.length;
      listHost.append(el('div', { class: 'card daily-bucket' },
        el('div', { class: 'card-head' },
          el('h2', {}, `${bucket.icon} ${bucket.label}`),
          el('span', { class: `badge ${bucket.tone}`, text: `${items.length} 件` })),
        el('div', { class: 'card-body tight' }, ...items.map(taskItem))));
    }
    if (data.stale.length) {
      listHost.append(el('div', { class: 'card daily-bucket' },
        el('div', { class: 'card-head' },
          el('h2', {}, '💤 1週間以上動きのないタスク'),
          el('span', { class: 'badge', text: `${data.stale.length} 件` })),
        el('div', { class: 'card-body tight' }, ...data.stale.map(taskItem))));
    }
    if ((data.issues || []).length) {
      listHost.append(el('div', { class: 'card daily-bucket' },
        el('div', { class: 'card-head' },
          el('h2', {}, '📌 自分が対応者の課題'),
          el('span', { class: 'badge', text: `${data.issues.length} 件` })),
        el('div', { class: 'card-body tight' },
          ...data.issues.map((issue) => el('div', {
            class: 'daily-item', style: { cursor: 'pointer' },
            onClick: () => openIssueDetail(issue.id, { onChange: reload }),
          },
          el('div', {},
            el('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' } },
              el('span', { class: 'issue-no', text: `#${issue.seq}` }),
              el('a', { href: '#', style: { fontWeight: 550 }, text: issue.title,
                onClick: (event) => event.preventDefault() }),
              issue.due_date
                ? el('span', {
                  class: `badge ${dueClass(issue.due_date, issue.status) || ''}`.trim(),
                  text: `期限 ${formatDate(issue.due_date)}`,
                })
                : null),
            el('div', { class: 'page-sub', text: issue.project_name })),
          el('div', { class: 'daily-controls' },
            el('span', { class: `sev sev-${issue.severity}`,
              text: `影響度 ${SEVERITY_LABEL[issue.severity]}` }),
            el('span', { class: `badge ${issue.status}`,
              text: ISSUE_STATUS_LABEL[issue.status] })))))));
    }
    if ((data.todos || []).length) {
      listHost.append(el('div', { class: 'card daily-bucket' },
        el('div', { class: 'card-head' },
          el('h2', {}, '📝 マイ ToDo'),
          el('a', { class: 'btn btn-sm', href: '#/todos' }, '一覧を開く')),
        el('div', { class: 'card-body tight' },
          ...data.todos.map((todo) => el('div', { class: 'daily-item' },
            el('label', { class: 'check' },
              el('input', {
                type: 'checkbox',
                onChange: async (event) => {
                  event.target.disabled = true;
                  try {
                    await api.patch(`/api/todos/${todo.id}`, { is_done: true });
                    reload();
                  } catch (error) {
                    toast(error.message, 'error');
                    event.target.disabled = false;
                  }
                },
              }),
              el('span', { text: todo.title })),
            todo.due_date
              ? el('span', {
                class: `badge ${dueClass(todo.due_date, 'todo') || ''}`.trim(),
                text: `期限 ${formatDate(todo.due_date)}`,
              })
              : null)))));
    }
    if (data.recently_done.length) {
      listHost.append(el('div', { class: 'card daily-bucket' },
        el('div', { class: 'card-head' }, el('h2', {}, '✅ 直近7日で完了したタスク')),
        el('div', { class: 'card-body tight' },
          ...data.recently_done.map((task) => el('div', { class: 'daily-item' },
            el('div', {},
              el('div', {}, el('a', {
                href: '#', onClick: (event) => {
                  event.preventDefault();
                  openTaskDetail(task.id, { onChange: reload });
                }, text: task.title,
              })),
              el('div', { class: 'page-sub', text: task.project_name })),
            el('span', { class: 'badge done', text: '完了' }))))));
    }
    if (rendered === 0 && !data.stale.length) {
      listHost.append(el('div', { class: 'card' },
        el('div', { class: 'empty' },
          el('div', { class: 'big', text: '🎉' }),
          '対応が必要なタスクはありません。お疲れさまです！')));
    }
  }

  function taskItem(task) {
    const change = pending.get(task.id) || {};
    const row = el('div', { class: `daily-item${pending.has(task.id) ? ' changed' : ''}` });

    const progressLabel = el('span', {
      class: 'cell-mut',
      text: `${change.progress ?? task.progress}%`,
      style: { minWidth: '38px', textAlign: 'right' },
    });

    const markChanged = () => {
      row.classList.add('changed');
      updateSaveBar();
    };

    const quick = [0, 25, 50, 75, 100].map((value) => el('button', {
      class: `qbtn${(change.progress ?? task.progress) === value ? ' active' : ''}`,
      onClick: () => {
        const entry = pending.get(task.id) || {};
        entry.progress = value;
        if (value === 100) entry.status = 'done';
        else if ((entry.status || task.status) === 'done') entry.status = 'doing';
        pending.set(task.id, entry);
        progressLabel.textContent = `${value}%`;
        [...quick].forEach((b, i) => b.classList.toggle('active', [0, 25, 50, 75, 100][i] === value));
        statusSelect.value = entry.status || task.status;
        markChanged();
      },
    }, `${value}%`));

    const statusSelect = el('select', {
      class: 'select', style: { maxWidth: '120px' },
      onChange: (event) => {
        const entry = pending.get(task.id) || {};
        entry.status = event.target.value;
        pending.set(task.id, entry);
        markChanged();
      },
    }, ...Object.entries(STATUS_LABEL).map(([value, label]) =>
      el('option', { value, selected: (change.status || task.status) === value ? true : null }, label)));

    const hoursBox = el('input', {
      class: 'input qhours', type: 'number', min: 0, step: 0.5, placeholder: '実績h',
      title: '今日かけた時間（任意）。保存すると実績工数に足されます',
      onInput: (event) => {
        const entry = pending.get(task.id) || {};
        const value = event.target.value;
        if (value === '') delete entry.hours; else entry.hours = Number(value);
        if (Object.keys(entry).length) pending.set(task.id, entry); else pending.delete(task.id);
        markChanged();
      },
    });

    const noteBox = el('textarea', {
      class: 'textarea', hidden: true, placeholder: 'ひとことメモ（タスクのコメントとして残ります）',
      style: { minHeight: '54px', marginTop: '6px' },
      onInput: (event) => {
        const entry = pending.get(task.id) || {};
        entry.note = event.target.value;
        if (!entry.note) delete entry.note;
        if (Object.keys(entry).length) pending.set(task.id, entry); else pending.delete(task.id);
        markChanged();
      },
    });

    row.append(
      el('div', { style: { minWidth: 0 } },
        el('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' } },
          el('a', {
            href: '#', style: { fontWeight: 550 },
            onClick: (event) => {
              event.preventDefault();
              openTaskDetail(task.id, { onChange: reload });
            },
            text: task.title,
          }),
          task.due_date
            ? el('span', {
              class: `badge ${dueClass(task.due_date, task.status) || ''}`.trim(),
              text: `期限 ${formatDate(task.due_date)}`,
            })
            : null),
        el('div', { class: 'page-sub' },
          task.project_name,
          task.updated_at ? ` · 最終更新 ${formatDateTime(task.updated_at)}` : ''),
        noteBox),
      el('div', { class: 'daily-controls' },
        ...quick, progressLabel, statusSelect, hoursBox,
        el('button', {
          class: 'qbtn', title: 'メモを書く',
          onClick: (event) => {
            noteBox.hidden = !noteBox.hidden;
            event.currentTarget.classList.toggle('active', !noteBox.hidden);
            if (!noteBox.hidden) noteBox.focus();
          },
        }, '💬')));
    return row;
  }

  function updateSaveBar() {
    const count = pending.size;
    saveBar.hidden = count === 0;
    fill(saveBar, 
      el('strong', { text: `${count} 件のタスクを更新` }),
      noteInput,
      el('button', {
        class: 'btn', onClick: () => { pending.clear(); draw(); updateSaveBar(); },
      }, '取消'),
      el('button', { class: 'btn btn-primary', onClick: submit }, 'まとめて更新'));
  }

  async function submit(event) {
    const button = event.currentTarget;
    button.disabled = true;
    const updates = [...pending.entries()].map(([taskId, change]) => ({
      task_id: taskId, ...change,
    }));
    try {
      const result = await api.post('/api/daily/update', {
        updates, note: noteInput.value.trim(),
      });
      toast(`${result.updated.length} 件を更新しました`, 'ok');
      pending.clear();
      await reload();
    } catch (error) {
      toast(error.message, 'error');
      button.disabled = false;
    }
  }

  async function reload() {
    const fresh = await api.daily();
    Object.assign(data, fresh);
    draw();
    updateSaveBar();
    store.refreshUnread().catch(() => {});
  }

  draw();
  updateSaveBar();
}

function greeting() {
  const hour = new Date().getHours();
  if (hour < 5) return 'こんばんは';
  if (hour < 11) return 'おはようございます';
  if (hour < 18) return 'こんにちは';
  return 'おつかれさまです';
}
