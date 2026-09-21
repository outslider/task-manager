/* チケット一覧。受付窓口に届いた依頼・問い合わせ・障害を並べる。
 *
 * プロジェクトに属さないので、絞り込みは窓口・種別・状態・担当だけ。 */
import { api } from '../api.js';
import { setHeader } from '../app.js';
import { store } from '../store.js';
import { avatar, dueClass, el, fill, formatDate } from '../util.js';
import { openTicketDetail } from './ticketDetail.js';
import { openTicketForm } from './ticketForm.js';

/** 絞り込みは画面を離れても覚えておく。受付担当は同じ窓口を続けて見るため。 */
const KEY = 'tm.tickets.filter';

function loadFilter() {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || '{}');
    return { queue_id: '', status: 'open', kind: '', scope: '', q: '',
      category_id: '', ...saved };
  } catch {
    return { queue_id: '', status: 'open', kind: '', scope: '', q: '', category_id: '' };
  }
}

function saveFilter(state) {
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* 使えなくても困らない */ }
}

export async function render(container, route) {
  const state = loadFilter();
  // プロジェクトから来たときは、その窓口を最初から選んでおく
  const fromProject = route?.projectId || null;
  const meta = store.meta?.tickets || { kinds: [], statuses: [], priorities: [] };
  const kindLabel = Object.fromEntries(meta.kinds.map((k) => [k.value, k]));
  const statusLabel = Object.fromEntries(meta.statuses.map((s) => [s.value, s]));

  setHeader('チケット', [
    el('button', {
      class: 'btn', title: 'CSV や Excel からまとめて登録します',
      onClick: async () => {
        const { openTicketImport } = await import('./importTickets.js');
        if (await openTicketImport()) { drawQueues(); load(); }
      },
    }, '⬆ 取り込み'),
    el('button', { class: 'btn btn-primary', onClick: () => create() }, '＋ チケットを起票'),
  ]);

  const summaryHost = el('div', { class: 'grid cols-4', style: { marginBottom: '10px' } });
  const paceHost = el('div', { class: 'ticket-pace' });
  const queueHost = el('div', { class: 'queue-tabs' });
  const listHost = el('div', {});
  const countHost = el('div', { class: 'hint' });
  const viewHost = el('div', {});
  state.view = 'list';

  const search = el('input', {
    class: 'input', type: 'search', placeholder: '件名・本文で絞り込む',
    style: { maxWidth: '240px' },
    value: state.q,
  });
  let timer = null;
  search.addEventListener('input', () => {
    state.q = search.value.trim();
    clearTimeout(timer);
    timer = setTimeout(load, 250);
  });

  const statusSelect = select([
    ['open', '未完了のみ'], ['all', 'すべて'],
    ...meta.statuses.map((s) => [s.value, s.label]),
  ], state.status, (value) => { state.status = value; load(); });

  const kindSelect = select([
    ['', '種別: すべて'], ...meta.kinds.map((k) => [k.value, `${k.icon} ${k.label}`]),
  ], state.kind, (value) => { state.kind = value; load(); });

  const scopeSelect = select([
    ['', 'すべての担当'], ['mine', '自分が担当'], ['raised', '自分が出した'],
    ['unassigned', '担当が未定'],
  ], state.scope, (value) => { state.scope = value; load(); });

  // 分類は窓口ごとなので、窓口を選んでいるときだけ出す
  const categoryHost = el('span', {});
  function drawCategoryFilter() {
    const queue = queues.find((q) => String(q.id) === String(state.queue_id));
    const list = queue?.categories || [];
    if (!list.length) {
      state.category_id = '';
      fill(categoryHost);
      return;
    }
    fill(categoryHost, select([
      ['', '分類: すべて'], ...list.map((c) => [c.id, c.label]),
    ], state.category_id, (value) => { state.category_id = value; load(); }));
  }

  const listCard = el('div', { class: 'card' },
    el('div', { class: 'card-head' }, queueHost),
    el('div', { class: 'toolbar' },
      search, statusSelect, kindSelect, categoryHost, scopeSelect,
      el('span', { class: 'spacer' }), countHost),
    el('div', { class: 'card-body tight' }, listHost));

  fill(container, summaryHost, paceHost, viewHost);

  async function drawView() {
    // 集計を見ているあいだは、一覧向けの数字は引っ込める（同じ数が二重に出るため）
    summaryHost.hidden = state.view === 'stats';
    paceHost.hidden = state.view === 'stats';
    if (state.view === 'stats') {
      const { ticketStats } = await import('./ticketStats.js');
      fill(viewHost, await ticketStats({
        queueId: state.queue_id,
        onBack: () => { state.view = 'list'; drawView(); },
      }));
      return;
    }
    fill(viewHost, listCard);
  }

  function select(options, value, onChange) {
    const node = el('select', { class: 'select', style: { maxWidth: '160px' } },
      ...options.map(([v, label]) =>
        el('option', { value: v, selected: String(v) === String(value) ? true : null }, label)));
    node.addEventListener('change', () => onChange(node.value));
    return node;
  }

  let queues = [];
  let pickedFromProject = false;

  async function drawQueues() {
    ({ queues } = await api.get('/api/ticket-queues'));
    if (fromProject && !pickedFromProject) {
      const match = queues.find((q) => q.project_id === fromProject);
      if (match) { state.queue_id = match.id; state.category_id = ''; }
      pickedFromProject = true;
    }
    const tab = (id, label, count, project) => el('button', {
      class: `queue-tab${String(state.queue_id) === String(id) ? ' active' : ''}`,
      title: project ? `${project} 専用の窓口` : '',
      onClick: () => { state.queue_id = id; state.category_id = ''; drawQueues(); load(); },
    }, label,
    project ? el('span', { class: 'queue-tab-project', text: project }) : null,
    count ? el('span', { class: 'badge', text: String(count) }) : null);
    fill(queueHost,
      tab('', 'すべての窓口', queues.reduce((n, q) => n + q.open_count, 0)),
      ...queues
        .filter((q) => q.is_active || String(state.queue_id) === String(q.id) || q.ticket_count)
        .map((q) => tab(q.id, `${q.icon || '📮'} ${q.name}`, q.open_count, q.project_name)));
    drawCategoryFilter();
  }

  /**
   * 一覧を読む。件数が増えたときに全部を一度に返すと重いので、
   * 少しずつ読み、続きは「さらに読み込む」で足す。
   */
  async function load({ append = false } = {}) {
    saveFilter(state);
    if (!append) {
      state.loaded = [];
      fill(listHost, el('div', { class: 'empty', text: '読み込み中…' }));
    }
    const params = new URLSearchParams();
    for (const key of ['queue_id', 'status', 'kind', 'scope', 'q', 'category_id']) {
      if (state[key]) params.set(key, state[key]);
    }
    params.set('offset', String(append ? state.loaded.length : 0));
    let data;
    try {
      data = await api.get(`/api/tickets?${params}`);
    } catch (error) {
      fill(listHost, el('div', { class: 'empty', text: error.message }));
      return;
    }
    state.loaded = append ? [...state.loaded, ...data.tickets] : data.tickets;
    drawSummary(data.summary, data.turnaround_days);
    countHost.textContent = data.matched > state.loaded.length
      ? `${data.matched} 件中 ${state.loaded.length} 件を表示`
      : `${data.matched} 件`;
    if (!state.loaded.length) {
      fill(listHost, el('div', { class: 'empty' },
        el('div', { class: 'big', text: '🎫' }),
        state.q || state.kind || state.scope || state.category_id || state.status !== 'open'
          ? '条件に合うチケットがありません'
          : '未対応のチケットはありません'));
      return;
    }
    fill(listHost, ...state.loaded.map(row));
    if (data.has_more) {
      const more = el('button', {
        class: 'btn btn-block load-more',
        onClick: async (event) => {
          event.currentTarget.disabled = true;
          event.currentTarget.textContent = '読み込み中…';
          await load({ append: true });
        },
      }, `さらに読み込む（残り ${data.matched - state.loaded.length} 件）`);
      listHost.append(more);
    }
  }

  /**
   * 残っているぶんだけでなく、片付いたぶんも出す。
   * 数字が減るだけの画面だと、やった実感が残らないため。
   */
  function drawSummary(s, turnaround) {
    const card = (label, value, tone, sub) => el('div', {
      class: `card stat${value ? '' : ' zero'}`,
    },
    el('div', { class: 'k', text: label }),
    el('div', { class: `v ${tone || ''}`.trim(), text: String(value) }),
    sub ? el('div', { class: 'stat-sub', text: sub }) : null);
    fill(summaryHost,
      card('未完了', s.open, ''),
      card('受付待ち', s.waiting, s.waiting ? 'warn' : ''),
      card('期限超過', s.overdue, s.overdue ? 'danger' : ''),
      card('今週の完了', s.done_week, s.done_week ? 'ok' : '',
        `今月 ${s.done_month} 件 / 通算 ${s.done_total} 件`));
    fill(paceHost,
      s.unassigned
        ? el('span', { class: 'badge warn-badge',
          text: `担当が決まっていないもの ${s.unassigned} 件` })
        : null,
      turnaround !== null && turnaround !== undefined
        ? el('span', { class: 'hint', text: `受けてから片付くまで 平均 ${turnaround} 日` })
        : null,
      el('button', {
        class: 'btn btn-sm', onClick: () => { state.view = 'stats'; drawView(); },
      }, '📊 集計を見る'));
  }

  function row(ticket) {
    const kind = kindLabel[ticket.kind] || { icon: '', label: ticket.kind };
    const status = statusLabel[ticket.status] || { label: ticket.status };
    return el('div', {
      class: `ticket-row${isClosed(ticket) ? ' is-closed' : ''}`,
      onClick: () => openTicketDetail(ticket.id, { onChange: load }),
    },
    el('div', { class: 'ticket-no', text: `#${ticket.id}` }),
    el('div', { class: 'ticket-main' },
      el('div', { class: 'ticket-title-line' },
        el('span', { class: 'ticket-kind', title: kind.label, text: kind.icon }),
        el('span', { class: 'ticket-title', text: ticket.title, title: ticket.title }),
        ticket.priority >= 2
          ? el('span', { class: `prio p${ticket.priority}`,
            text: ticket.priority === 3 ? '緊急' : '高' })
          : null,
        ticket.task_count
          ? el('span', { class: 'badge', title: '関連タスク',
            text: `✓ ${ticket.open_task_count}/${ticket.task_count}` })
          : null,
        ticket.issue_count
          ? el('span', { class: 'badge', title: '関連課題', text: `📌 ${ticket.issue_count}` })
          : null,
        ticket.category_label
          ? el('span', { class: 'cat-chip sm', style: {
            background: `${ticket.category_color}1f`, color: ticket.category_color,
            borderColor: `${ticket.category_color}55`,
          } }, ticket.category_label)
          : null,
        ticket.comment_count
          ? el('span', { class: 'ticket-meta-icon', text: `💬${ticket.comment_count}` })
          : null),
      el('div', { class: 'ticket-sub' },
        el('span', { style: { color: ticket.queue_color }, text: `${ticket.queue_icon || '📮'} ${ticket.queue_name}` }),
        ticket.on_behalf_of ? el('span', { text: `依頼元: ${ticket.on_behalf_of}` }) : null,
        el('span', { text: `起票: ${ticket.requester_name || '不明'}` }))),
    el('div', { class: 'cell-hide-sm' },
      el('span', { class: `badge ticket-${ticket.status}`, text: status.label })),
    el('div', { class: 'cell-hide-sm' },
      ticket.assignee_id
        ? el('span', { class: 'avatar-stack' },
          avatar({ name: ticket.assignee_name, avatar_color: ticket.assignee_color }, 'sm'),
          el('span', { class: 'cell-mut', text: ticket.assignee_name }))
        : el('span', { class: 'cell-mut', text: '未割当' })),
    el('div', { class: `cell-mut cell-hide-sm ${dueClass(ticket.due_date, closedLike(ticket))}` },
      ticket.due_date ? formatDate(ticket.due_date) : '—'));
  }

  /**
   * 手が離れた状態かどうか。状態の種類はサーバー側で決めているので、
   * ここで名前を決め打ちせず、渡された一覧で判断する。
   */
  function isClosed(ticket) {
    const open = meta.open_statuses || [];
    return !open.includes(ticket.status);
  }

  /** 期限の色づけは完了扱いのものを赤くしないため。 */
  function closedLike(ticket) {
    return isClosed(ticket) ? 'done' : ticket.status;
  }

  async function create() {
    if (await openTicketForm()) { drawQueues(); load(); }
  }

  await drawQueues();
  await drawView();
  await load();
}
