/* チケットの起票・編集ダイアログ。 */
import { api } from '../api.js';
import { store } from '../store.js';
import { el, fill, openModal, toast } from '../util.js';
import { option, userSelect } from './pickers.js';

function ticketMeta() {
  return store.meta?.tickets || { kinds: [], statuses: [], priorities: [] };
}

/**
 * @param {object} options ticket（編集のとき）、queues（省略時は取得する）
 * @returns {Promise<object|null>} 保存したチケット
 */
export async function openTicketForm({ ticket = null, queues = null } = {}) {
  const meta = ticketMeta();
  const list = queues || (await api.get('/api/ticket-queues')).queues;
  const open = list.filter((q) => q.is_active || q.id === ticket?.queue_id);
  if (!open.length) {
    toast(store.isGuest()
      ? '起票できる窓口がありません。社内の担当者にお問い合わせください'
      : '受付中の窓口がありません。管理画面で窓口を追加してください', 'error');
    return null;
  }

  const editing = Boolean(ticket);
  const f = {};
  // 社外ユーザーの起票は、窓口・種別・分類・件名・内容だけ（担当や期限は社内が決める）
  const guest = store.isGuest();
  const organizations = guest ? [] : await api.get('/api/organizations')
    .then((r) => r.organizations).catch(() => []);

  return openModal({
    title: editing ? `チケット #${ticket.id} を編集` : 'チケットを起票',
    wide: true,
    build: () => {
      f.queue = el('select', { class: 'select' },
        ...open.map((q) => option(
          q.id,
          `${q.icon || '📮'} ${q.name}${q.project_name ? `（${q.project_name}）` : ''}`,
          String(ticket?.queue_id || open[0].id) === String(q.id))));
      const queueOf = () => open.find((q) => String(q.id) === f.queue.value) || open[0];
      f.kind = el('select', { class: 'select' },
        ...meta.kinds.map((k) => option(k.value, `${k.icon} ${k.label}`,
          (ticket?.kind || queueOf().default_kind || 'request') === k.value)));
      // 分類は窓口ごとに違う。窓口が変わったら選択肢ごと入れ替える。
      const categoryHost = el('div', {});
      const categoryField = el('div', { class: 'field' },
        el('label', { text: '分類' }), categoryHost);
      f.category = null;
      const drawCategory = () => {
        const queue = queueOf();
        const list = queue.categories || [];
        categoryField.hidden = list.length === 0;
        if (!list.length) { f.category = null; fill(categoryHost); return; }
        const keep = String(ticket?.queue_id || '') === String(queue.id)
          ? ticket?.category_id : null;
        f.category = el('select', { class: 'select' },
          option('', '分類なし', !keep),
          ...list.map((c) => option(c.id, c.label, String(keep || '') === String(c.id))));
        fill(categoryHost, f.category);
      };
      f.title = el('input', {
        class: 'input', placeholder: '例）共有フォルダの権限を追加してほしい',
      });
      f.title.value = ticket?.title || '';
      f.body = el('textarea', {
        class: 'textarea',
        placeholder: '困っていること、してほしいこと、いつまでに必要か',
      });
      f.body.value = ticket?.body || '';
      f.onBehalf = el('input', {
        class: 'input', placeholder: '例）営業部 田中（自分の依頼なら空欄）',
      });
      f.onBehalf.value = ticket?.on_behalf_of || '';
      f.priority = el('select', { class: 'select' },
        ...meta.priorities.map((p) => option(p.value, p.label,
          String(ticket?.priority ?? 1) === String(p.value))));
      f.assignee = userSelect(ticket?.assignee_id, { emptyLabel: '未割当' });
      f.due = el('input', { class: 'input', type: 'date' });
      f.due.value = ticket?.due_date || '';
      f.occurred = el('input', { class: 'input', type: 'datetime-local' });
      f.occurred.value = (ticket?.occurred_at || '').replace(' ', 'T').slice(0, 16);
      f.spent = el('input', {
        class: 'input', type: 'number', step: '0.5', min: '0', placeholder: '例）1.5',
      });
      f.spent.value = ticket?.spent_hours ?? '';
      // どの会社のチケットか。選ぶとその会社の社外ユーザーにも見える
      f.org = el('select', { class: 'select' },
        option('', '社内だけ（社外には見せない）', !ticket?.organization_id),
        ...organizations.map((o) => option(o.id, o.name, o.id === ticket?.organization_id)));

      const occurredField = el('div', { class: 'field' },
        el('label', { text: '発生日時' }), f.occurred,
        el('div', { class: 'hint', text: '障害のときだけ' }));
      const syncKind = () => { occurredField.hidden = f.kind.value !== 'incident'; };
      f.kind.addEventListener('change', syncKind);
      f.queue.addEventListener('change', () => {
        drawCategory();
        if (!editing) {
          f.kind.value = queueOf().default_kind || 'request';
          syncKind();
        }
      });
      drawCategory();
      syncKind();

      return el('div', {},
        el('div', { class: 'row' },
          el('div', { class: 'field' }, el('label', { text: '窓口 *' }), f.queue),
          el('div', { class: 'field' }, el('label', { text: '種別' }), f.kind),
          categoryField),
        el('div', { class: 'field' }, el('label', { text: '件名 *' }), f.title),
        el('div', { class: 'field' }, el('label', { text: '内容' }), f.body),
        guest ? el('div', { class: 'hint',
          text: '起票すると、社内の担当者と、同じ会社の方に見えます。対応の状況はこのチケットで確認できます。' })
          : null,
        guest ? null : el('div', { class: 'row' },
          el('div', { class: 'field' }, el('label', { text: '依頼元' }), f.onBehalf),
          el('div', { class: 'field' }, el('label', { text: '優先度' }), f.priority),
          organizations.length
            ? el('div', { class: 'field' }, el('label', { text: '会社（社外に見せる）' }), f.org)
            : null),
        guest ? null : el('div', { class: 'row' },
          el('div', { class: 'field' }, el('label', { text: '担当' }), f.assignee),
          el('div', { class: 'field' }, el('label', { text: '期限' }), f.due),
          occurredField,
          el('div', { class: 'field' },
            el('label', { text: '対応時間 (h)' }), f.spent,
            el('div', { class: 'hint', text: '任意。あとで集計に使えます' }))));
    },
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', {
        class: 'btn btn-primary',
        onClick: async (event) => {
          const title = f.title.value.trim();
          if (!title) { toast('件名を入れてください', 'error'); return; }
          const payload = {
            queue_id: Number(f.queue.value),
            kind: f.kind.value,
            title,
            body: f.body.value,
            on_behalf_of: f.onBehalf.value.trim(),
            priority: Number(f.priority.value),
            assignee_id: f.assignee.value ? Number(f.assignee.value) : null,
            due_date: f.due.value || null,
            occurred_at: f.kind.value === 'incident' ? (f.occurred.value || null) : null,
            category_id: f.category && f.category.value ? Number(f.category.value) : null,
            spent_hours: f.spent.value === '' ? null : Number(f.spent.value),
          };
          if (!guest && organizations.length) {
            payload.organization_id = f.org.value ? Number(f.org.value) : null;
          }
          const button = event.currentTarget;
          button.disabled = true;
          try {
            const result = editing
              ? await api.patch(`/api/tickets/${ticket.id}`, payload)
              : await api.post('/api/tickets', payload);
            toast(editing ? '保存しました' : '起票しました', 'ok');
            close(result.ticket);
          } catch (error) {
            toast(error.message, 'error');
            button.disabled = false;
          }
        },
      }, editing ? '保存' : '起票する'),
    ],
  });
}
