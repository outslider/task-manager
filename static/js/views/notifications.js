/* Notification inbox. */
import { api } from '../api.js';
import { setHeader } from '../app.js';
import { store } from '../store.js';
import { linkify } from './mention.js';
import { clear, el, fill, formatDateTime, skeleton, toast } from '../util.js';
import { openTaskDetail } from './taskDetail.js';

const TYPE_ICON = {
  overdue: '🔥', due_soon: '⏳', assigned: '👤', comment: '💬', digest: '📋', mentioned: '📣',
};

export async function render(container) {
  const state = { unreadOnly: false };
  const listHost = el('div', {});

  setHeader('通知', [
    el('button', {
      class: 'btn',
      onClick: async () => {
        await api.post('/api/notifications/read', { all: true });
        await store.refreshUnread();
        toast('すべて既読にしました', 'ok');
        load();
      },
    }, 'すべて既読'),
  ]);

  fill(container, 
    el('div', { class: 'card' },
      el('div', { class: 'toolbar' },
        el('label', { class: 'check' },
          el('input', {
            type: 'checkbox',
            onChange: (event) => { state.unreadOnly = event.target.checked; load(); },
          }), el('span', { text: '未読のみ' }))),
      el('div', { class: 'card-body tight' }, listHost)));

  async function load() {
    fill(listHost, skeleton('rows', 6));
    const data = await api.notifications({ unread: state.unreadOnly ? 1 : '', limit: 200 });
    clear(listHost);
    if (data.notifications.length === 0) {
      listHost.append(el('div', { class: 'empty' },
        el('div', { class: 'big', text: '🔔' }), '通知はありません'));
      return;
    }
    for (const item of data.notifications) {
      listHost.append(row(item));
    }
  }

  function row(item) {
    const node = el('div', {
      class: `notif-item ${item.is_read ? 'read' : 'unread'}`
        + (item.task_id || item.ticket_id || item.issue_id ? ' openable' : ''),
      onClick: async () => {
        if (!item.is_read) {
          await api.post('/api/notifications/read', { ids: [item.id] });
          item.is_read = 1;
          node.classList.remove('unread');
          node.classList.add('read');
          store.refreshUnread().catch(() => {});
        }
        // どこから来た知らせかで、開く先を変える
        if (item.task_id) {
          openTaskDetail(item.task_id, { onChange: load });
        } else if (item.ticket_id) {
          const { openTicketDetail } = await import('./ticketDetail.js');
          openTicketDetail(item.ticket_id, { onChange: load });
        } else if (item.issue_id) {
          const { openIssueDetail } = await import('./issueDetail.js');
          openIssueDetail(item.issue_id, { onChange: load });
        }
      },
    },
    el('span', { class: 'dot' }),
    el('span', { text: TYPE_ICON[item.type] || '•' }),
    el('div', { class: 'notif-body' },
      el('div', { class: 'notif-title', text: item.title }),
      ...summarize(item.body),
      el('div', { class: 'hint', text: formatDateTime(item.created_at) })),
    el('button', {
      class: 'icon-btn', title: '削除',
      onClick: async (event) => {
        event.stopPropagation();
        await api.del(`/api/notifications/${item.id}`);
        node.remove();
        store.refreshUnread().catch(() => {});
      },
    }, '×'));
    return node;
  }

  await load();
}

/* 本文はメール用に「プロジェクト: …／期限: …」と改行で並べてある。
 * 画面ではそのまま出すと 1 件が何行にもなるので、見出しの部分はバッジにたたむ。 */
const FIELD_LINE = /^(プロジェクト|期限|状態|担当者|影響度|対応者)\s*[:：]\s*(.+)$/;

function summarize(body) {
  const text = String(body || '').trim();
  if (!text) return [];
  const badges = [];
  const rest = [];
  for (const line of text.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    // 画面では行をクリックすれば開けるので、メール用の URL 行は出さない
    if (/^https?:\/\/\S+$/.test(trimmed)) continue;
    const field = trimmed.match(FIELD_LINE);
    if (field) {
      badges.push([field[1], tidyDate(field[2])]);
      continue;
    }
    rest.push(trimmed);
  }
  const out = [];
  if (badges.length) {
    out.push(el('div', { class: 'notif-meta' },
      ...badges.map(([key, value]) => el('span', { class: 'badge', text: `${key} ${value}` }))));
  }
  if (rest.length) {
    const body2 = linkify(rest.join(' / '), el('div', { class: 'notif-text' }));
    body2.title = text;
    out.push(body2);
  }
  return out;
}

/** 2026-09-21 のような日付は、他の画面に合わせて 9/21 にする。 */
function tidyDate(value) {
  const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return match ? `${Number(match[2])}/${Number(match[3])}` : value;
}
