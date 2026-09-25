/* Daily check-in: review everything due and update it in a couple of clicks. */
import { api } from '../api.js';
import { setHeader } from '../app.js';
import {
  store, category, STATUS_LABEL, ISSUE_STATUS_LABEL, SEVERITY_LABEL, taskProgress,
} from '../store.js';
import { clear, dueClass, el, fill, formatDate, formatDateTime, toast } from '../util.js';
import { openTaskDetail } from './taskDetail.js';
import { openIssueDetail } from './issueDetail.js';
import { openTicketDetail } from './ticketDetail.js';

const BUCKETS = [
  { key: 'overdue', label: '期限超過', tone: 'overdue', icon: '🔥' },
  { key: 'today', label: '本日期限', tone: 'soon', icon: '📌' },
  { key: 'soon', label: 'まもなく期限', tone: 'soon', icon: '⏳' },
  // 今日の判断には要らないので、既定ではたたんでおく
  { key: 'no_due', label: '期限未設定', tone: '', icon: '❓', folded: true },
  { key: 'later', label: '先の予定', tone: '', icon: '🗓', folded: true },
];
/** 停滞と完了ぶんは「今日やること」ではないので、件数だけ見せて既定ではたたむ。 */
const STALE_KEY = 'stale';
const DONE_KEY = 'done';
const FOLD_KEY = 'tm.daily.folded';

function loadFolded() {
  try {
    const saved = localStorage.getItem(FOLD_KEY);
    if (saved !== null) return new Set(JSON.parse(saved));
  } catch { /* private mode */ }
  return new Set([...BUCKETS.filter((b) => b.folded).map((b) => b.key),
    STALE_KEY, DONE_KEY]);
}

function saveFolded(set) {
  try { localStorage.setItem(FOLD_KEY, JSON.stringify([...set])); } catch { /* ignore */ }
}

export async function render(container) {
  const data = await api.daily();
  const pending = new Map();   // task_id -> { progress, status, note }
  const folded = loadFolded();

  setHeader('今日の確認');

  // 課題・チケットなどの一覧は頭の数件だけが届く。数字カードや見出しで
  // 並べた件数を出すと、多い人ほど少なく見えるので、本当の件数を使う。
  const total = (key, list) => data.totals?.[key] ?? (list || []).length;
  const counts = {
    overdue: data.buckets.overdue.length,
    today: data.buckets.today.length,
    soon: data.buckets.soon.length,
    open: Object.values(data.buckets).reduce((sum, list) => sum + list.length, 0)
      - data.buckets.later.length,
    tickets: total('tickets', data.tickets),
    issues: total('issues', data.issues),
    done: total('recently_done', data.recently_done),
    stale: total('stale', data.stale),
    todos: total('todos', data.todos),
  };
  /** 並べきれなかったぶんを知らせる一行。全部並んでいれば何も出さない。 */
  const moreLine = (shown, all, href) => (all > shown
    ? el('div', { class: 'daily-more' },
      el('span', { text: `ほか ${all - shown} 件あります` }),
      href ? el('a', { href, text: '一覧で見る' }) : null)
    : null);

  const saveBar = el('div', { class: 'sticky-save', hidden: true });
  const noteInput = el('input', {
    class: 'input', placeholder: '今日のひとこと（任意・チェックイン記録に残ります）',
  });
  noteInput.value = data.checkin?.note || '';

  const listHost = el('div', {});

  fill(container, 
    el('div', { class: 'home-hero', dataset: { pattern: 'aurora' } },
      el('div', { class: 'grow' },
        el('h1', { text: `${greeting()}、${store.user.name} さん` }),
        el('div', { class: 'home-hero-sub' },
          headline(),
          data.streak > 0
            ? el('span', { class: 'hero-chip', style: { marginLeft: '8px' },
              text: `🔥 ${data.streak}日連続チェックイン` })
            : null))),
    el('div', { class: 'grid daily-stats', style: { marginBottom: '14px' } },
      statCard('期限超過', counts.overdue, counts.overdue ? 'danger' : '', 'overdue'),
      statCard('本日期限', counts.today, counts.today ? 'warn' : '', 'today'),
      statCard('担当の課題', counts.issues, '', 'issues'),
      // 社外ユーザーはチケットをまだ使えないので、カードごと出さない
      store.isGuest() ? null : statCard('担当のチケット', counts.tickets, '', 'tickets'),
      statCard('まもなく期限', counts.soon, '', 'soon'),
      statCard('直近7日の完了', counts.done, 'ok', 'done')),
    listHost, saveBar);

  /**
   * 自分が担当しているチケット。
   * 受けたまま誰も見ていないものは、件数だけ添えて一覧へ送る。
   */
  function ticketCard() {
    const meta = store.meta?.tickets || { kinds: [], statuses: [] };
    const kindOf = (value) => meta.kinds.find((k) => k.value === value) || { icon: '' };
    const statusOf = (value) =>
      meta.statuses.find((x) => x.value === value) || { label: value };
    const list = data.tickets || [];
    return el('div', { class: 'card daily-bucket kind-ticket' },
      el('div', { class: 'card-head' },
        el('h2', {}, '🎫 自分が担当のチケット'),
        counts.tickets ? el('span', { class: 'badge', text: `${counts.tickets} 件` }) : null,
        data.unclaimed_tickets
          ? el('span', { class: 'badge warn-badge',
            text: `未割当 ${data.unclaimed_tickets} 件` })
          : null,
        el('a', { class: 'btn btn-sm', href: '#/tickets' }, '一覧を開く')),
      el('div', { class: 'card-body tight' },
        list.length
          ? el('div', {}, ...list.map(ticketItem),
            moreLine(list.length, counts.tickets, '#/tickets?scope=mine'))
          : el('div', { class: 'hint', style: { padding: '12px 15px' },
            text: '自分が担当のチケットはありません。'
              + '誰も受けていないものが残っています。' })));

    function ticketItem(ticket) {
      const overdue = ticket.due_date && ticket.status !== 'done'
        && ticket.due_date < data.date;
      return el('div', {
        class: 'daily-item', style: { cursor: 'pointer' },
        onClick: () => openTicketDetail(ticket.id, { onChange: reload }),
      },
      el('div', {},
        el('div', { style: { display: 'flex', gap: '8px', alignItems: 'center',
          flexWrap: 'wrap' } },
        el('span', { class: 'kind-tag ticket', text: 'チケット' }),
        el('span', { class: 'issue-no', text: `#${ticket.id}` }),
        el('span', { text: kindOf(ticket.kind).icon }),
        el('a', { href: '#', style: { fontWeight: 550 }, text: ticket.title,
          onClick: (event) => event.preventDefault() }),
        ticket.priority >= 2
          ? el('span', { class: `prio p${ticket.priority}`,
            text: ticket.priority === 3 ? '緊急' : '高' })
          : null,
        ticket.due_date
          ? el('span', {
            class: `badge ${overdue ? 'blocked' : ''}`.trim(),
            text: `期限 ${formatDate(ticket.due_date)}`,
          })
          : null),
        el('div', { class: 'page-sub' },
          `${ticket.queue_icon || '📮'} ${ticket.queue_name}`
          + (ticket.on_behalf_of ? ` ・ 依頼元 ${ticket.on_behalf_of}` : ''))),
      el('div', { class: 'daily-controls' },
        ticket.category_label
          ? el('span', { class: 'cat-tag sm', title: ticket.category_label },
            el('i', { class: 'cat-dot', style: { background: ticket.category_color } }),
            el('span', { text: ticket.category_label }))
          : null,
        el('span', { class: `badge ticket-${ticket.status}`,
          text: statusOf(ticket.status).label })));
    }
  }

  /** 見出しの一行。タスクとチケットの両方を抱えている日もあるので、両方数える。 */
  function headline() {
    const day = formatDate(data.date, true);
    const parts = [];
    if (counts.open) parts.push(`対応が必要なタスク ${counts.open} 件`);
    if (counts.tickets) parts.push(`担当チケット ${counts.tickets} 件`);
    return parts.length
      ? `${day} — ${parts.join(' / ')}`
      : `${day} — 期限が迫っているものはありません`;
  }

  /**
   * 上の集計。押すとその節へ飛ぶ。
   * スクロールしないと全体が分からない、という状態を避けるため、
   * 今日見るものの件数はここだけで全部そろうようにしてある。
   */
  function statCard(label, value, tone, anchor) {
    // 0 のカードまで同じ濃さだと、目を向けるべきところが分からなくなる
    const node = el('div', {
      class: `card stat${value ? '' : ' zero'}${value && anchor ? ' jump' : ''}`,
      title: value && anchor ? 'ここを押すと該当の欄へ移動します' : '',
      onClick: value && anchor ? () => {
        const target = listHost.querySelector(`[data-sec="${anchor}"]`);
        if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } : null,
    },
    el('div', { class: 'k', text: label }),
    el('div', { class: `v ${value ? tone : ''}`.trim(), text: String(value) }));
    return node;
  }

  /** 節に目印を付けて、上の集計から飛べるようにする。 */
  function section(id, node) {
    if (node) node.dataset.sec = id;
    return node;
  }

  function draw() {
    clear(listHost);
    const bucket = (key) => {
      const items = data.buckets[key] || [];
      if (!items.length) return null;
      return section(key, bucketCard(BUCKETS.find((b) => b.key === key), items));
    };

    // いま見るもの。期限切れ・今日・課題・チケットを先に置く。
    const urgent = [
      bucket('overdue'),
      bucket('today'),
      (data.issues || []).length ? section('issues', issueCard()) : null,
      (data.tickets || []).length || data.unclaimed_tickets
        ? section('tickets', ticketCard())
        : null,
    ].filter(Boolean);
    listHost.append(...urgent);

    // そのあとで見るもの
    const later = [
      bucket('soon'),
      (data.todos || []).length ? section('todos', todoCard()) : null,
      bucket('later'),
      bucket('no_due'),
      data.stale.length ? section('stale', staleCard()) : null,
    ].filter(Boolean);
    if (later.length) {
      if (urgent.length) {
        listHost.append(el('div', { class: 'daily-divider' },
          el('span', { text: 'そのあとで見るもの' })));
      }
      listHost.append(...later);
    }
    if (data.recently_done.length) {
      listHost.append(section('done', doneCard()));
    }
    if (!urgent.length && !later.length) {
      listHost.append(el('div', { class: 'card' },
        el('div', { class: 'empty' },
          el('div', { class: 'big', text: '🎉' }),
          '対応が必要なものはありません。お疲れさまです！')));
    }
  }

  /** 片付いたぶん。振り返り用なので、既定ではたたんでおく。 */
  function doneCard() {
    return foldableCard(DONE_KEY, '✅ 直近7日で完了したタスク',
      counts.done, 'kind-done',
      () => data.recently_done.map((task) => el('div', { class: 'daily-item' },
        el('div', {},
          el('div', {}, el('a', {
            href: '#',
            onClick: (event) => {
              event.preventDefault();
              openTaskDetail(task.id, { onChange: reload });
            },
            text: task.title,
          })),
          el('div', { class: 'page-sub', text: task.project_name })),
        el('span', { class: 'badge done', text: '完了' }))));
  }

  /** 見出しを押すと開け閉めできるまとまり。開いたかどうかは次回も引き継ぐ。 */
  function foldableCard(key, title, count, tone, buildItems) {
    const closed = folded.has(key);
    const head = el('div', { class: 'card-head foldable' },
      el('h2', {},
        el('span', { class: 'fold-mark', text: closed ? '▶' : '▼' }),
        ` ${title}`),
      el('span', { class: 'badge', text: `${count} 件` }));
    head.addEventListener('click', () => {
      if (folded.has(key)) folded.delete(key); else folded.add(key);
      saveFolded(folded);
      draw();
    });
    return el('div', { class: `card daily-bucket ${tone}${closed ? ' folded' : ''}` },
      head,
      el('div', { class: 'card-body tight', hidden: closed },
        ...(closed ? [] : buildItems())));
  }

  /** 止まっているタスク。開始日がまだのものはサーバー側で除いてある。 */
  function staleCard() {
    return foldableCard(STALE_KEY, '💤 1週間以上動きのないタスク',
      counts.stale, 'kind-task', () => [...data.stale.map(taskItem),
        moreLine(data.stale.length, counts.stale, '#/mytasks')]);
  }

  function issueCard() {
    return el('div', { class: 'card daily-bucket kind-issue' },
        el('div', { class: 'card-head' },
          el('h2', {}, '📌 自分が対応者の課題'),
          el('span', { class: 'badge', text: `${counts.issues} 件` })),
        el('div', { class: 'card-body tight' },
          ...data.issues.map((issue) => el('div', {
            class: 'daily-item', style: { cursor: 'pointer' },
            onClick: () => openIssueDetail(issue.id, { onChange: reload }),
          },
          el('div', {},
            el('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' } },
              el('span', { class: 'kind-tag issue', text: '課題' }),
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
              text: ISSUE_STATUS_LABEL[issue.status] })))),
          moreLine(data.issues.length, counts.issues, '#/issues')));
  }

  function todoCard() {
    return el('div', { class: 'card daily-bucket kind-todo' },
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
            todo.recurrence_id
              ? el('span', { class: 'todo-repeat', title: '繰り返しから出た ToDo', text: '🔁' })
              : null,
            todo.due_date
              ? el('span', {
                class: `badge ${dueClass(todo.due_date, 'todo') || ''}`.trim(),
                text: `期限 ${formatDate(todo.due_date)}`,
              })
              : null)),
          moreLine(data.todos.length, counts.todos, '#/todos')));
  }

  /** 区分ごとのまとまり。見出しを押すと開け閉めできる。 */
  function bucketCard(bucket, items) {
    const closed = folded.has(bucket.key);
    const body = el('div', { class: 'card-body tight', hidden: closed },
      ...(closed ? [] : items.map(taskItem)));
    const head = el('div', { class: 'card-head foldable' },
      el('h2', {},
        el('span', { class: 'fold-mark', text: closed ? '▶' : '▼' }),
        ` ${bucket.icon} ${bucket.label}`),
      el('span', { class: `badge ${bucket.tone}`.trim(), text: `${items.length} 件` }));
    head.addEventListener('click', () => {
      if (folded.has(bucket.key)) folded.delete(bucket.key);
      else folded.add(bucket.key);
      saveFolded(folded);
      draw();
    });
    return el('div', {
      class: `card daily-bucket kind-task${closed ? ' folded' : ''}`,
    }, head, body);
  }

  function taskItem(task) {
    const change = pending.get(task.id) || {};
    const row = el('div', { class: `daily-item${pending.has(task.id) ? ' changed' : ''}` });

    const markChanged = () => {
      row.classList.add('changed');
      updateSaveBar();
    };

    // 子タスクのあるタスクは、進捗を子から集計するので押させない（状態は変えられる）
    const quick = task.child_count ? [el('span', {
      class: 'qrollup', title: '子タスクから自動で集計しています',
      text: `子タスクから集計 ${taskProgress(task)}%`,
    })] : [0, 25, 50, 75, 100].map((value) => el('button', {
      class: `qbtn${(change.progress ?? task.progress) === value ? ' active' : ''}`,
      onClick: () => {
        const entry = pending.get(task.id) || {};
        entry.progress = value;
        if (value === 100) entry.status = 'done';
        else if ((entry.status || task.status) === 'done') entry.status = 'doing';
        pending.set(task.id, entry);
        quick.forEach((b, i) => b.classList.toggle('active', [0, 25, 50, 75, 100][i] === value));
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
      class: 'input qhours', type: 'number', min: 0, step: 0.5, placeholder: '実績 h',
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
      class: 'textarea', placeholder: 'ひとことメモ（タスクのコメントとして残ります）',
      style: { minHeight: '54px' },
      onInput: (event) => {
        const entry = pending.get(task.id) || {};
        entry.note = event.target.value;
        if (!entry.note) delete entry.note;
        if (Object.keys(entry).length) pending.set(task.id, entry); else pending.delete(task.id);
        markChanged();
      },
    });

    // メモと実績時間は毎日使うものではないので、💬 を押したときだけ出す
    const extra = el('div', { class: 'daily-extra', hidden: true },
      noteBox,
      el('label', { class: 'daily-hours' },
        el('span', { class: 'hint', text: '今日かけた時間' }), hoursBox));

    row.append(
      el('div', { style: { minWidth: 0 } },
        el('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' } },
          task.category
            ? el('span', {
              class: 'daily-cat', title: category(task.category).label,
              style: { color: category(task.category).color },
              text: category(task.category).icon,
            })
            : null,
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
        extra),
      el('div', { class: 'daily-controls' },
        ...quick, statusSelect,
        el('button', {
          class: 'qbtn', title: 'メモと実績時間を書く',
          onClick: (event) => {
            extra.hidden = !extra.hidden;
            event.currentTarget.classList.toggle('active', !extra.hidden);
            if (!extra.hidden) noteBox.focus();
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
