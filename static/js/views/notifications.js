/* Notification inbox. */
import { api } from '../api.js';
import { setHeader } from '../app.js';
import { store } from '../store.js';
import { linkify } from './mention.js';
import { clear, el, fill, formatDateTime, toast } from '../util.js';
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
    fill(listHost, el('div', { class: 'empty', text: '読み込み中…' }));
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
      class: `notif-item ${item.is_read ? 'read' : 'unread'}`,
      onClick: async () => {
        if (!item.is_read) {
          await api.post('/api/notifications/read', { ids: [item.id] });
          item.is_read = 1;
          node.classList.remove('unread');
          node.classList.add('read');
          store.refreshUnread().catch(() => {});
        }
        if (item.task_id) openTaskDetail(item.task_id, { onChange: load });
      },
    },
    el('span', { class: 'dot' }),
    el('span', { text: TYPE_ICON[item.type] || '•' }),
    el('div', { class: 'notif-body' },
      el('div', { class: 'notif-title', text: item.title }),
      item.body ? linkify(item.body, el('div', { class: 'notif-text' })) : null,
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
