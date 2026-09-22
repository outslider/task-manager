/* ゴミ箱。消したものを一定期間だけ置いておき、元に戻せるようにする。
 *
 * 出てくるのは「自分が消したもの」と「自分が編集できるプロジェクトのもの」だけ。
 * 戻すと元の番号のまま返ってくるので、依存線や課題との紐づけもそのまま復活する。 */
import { api } from '../api.js';
import { setHeader } from '../app.js';
import { confirmDialog, el, fill, formatDate, formatDateTime, toast } from '../util.js';

const ICONS = { task: '✓', issue: '📌', ticket: '🎫' };

export async function render(container) {
  setHeader('ゴミ箱');
  const state = { kind: '', items: [], keepDays: 30, kinds: [] };

  const filterHost = el('div', { class: 'seg' });
  const listHost = el('div', {});
  const summary = el('div', { class: 'hint' });

  fill(container,
    el('div', { class: 'card' },
      el('div', { class: 'card-head' },
        el('h2', {}, '🗑 ゴミ箱'),
        filterHost),
      el('div', { class: 'card-body' },
        el('p', { class: 'page-sub', id: 'trash-lead' }),
        listHost,
        summary)));

  function drawFilter() {
    fill(filterHost,
      el('button', {
        type: 'button', class: state.kind === '' ? 'active' : '',
        onClick: () => { state.kind = ''; load(); },
      }, 'すべて'),
      ...state.kinds.map((k) => el('button', {
        type: 'button', class: state.kind === k.value ? 'active' : '',
        onClick: () => { state.kind = k.value; load(); },
      }, `${ICONS[k.value] || ''} ${k.label}`)));
  }

  async function load() {
    fill(listHost, el('div', { class: 'empty', text: '読み込み中…' }));
    try {
      const data = await api.get(`/api/trash${state.kind ? `?kind=${state.kind}` : ''}`);
      state.items = data.items;
      state.keepDays = data.keep_days;
      state.kinds = data.kinds;
    } catch (error) {
      fill(listHost, el('div', { class: 'empty', text: error.message }));
      return;
    }
    const lead = container.querySelector('#trash-lead');
    if (lead) {
      lead.textContent = `消したものは ${state.keepDays} 日だけここに残ります。`
        + 'それを過ぎると自動で消え、戻せなくなります。'
        + '出てくるのは自分が消したものと、自分が編集できるプロジェクトのものだけです。';
    }
    drawFilter();
    draw();
  }

  function draw() {
    if (!state.items.length) {
      fill(listHost, el('div', { class: 'empty' },
        el('div', { style: { fontSize: '28px' } }, '🗑'),
        el('div', { text: 'ゴミ箱は空です' })));
      summary.textContent = '';
      return;
    }
    fill(listHost, ...state.items.map(row));
    summary.textContent = `${state.items.length} 件`;
  }

  /** 残り日数。今日が最終日なら 0。 */
  function daysLeft(purgeAfter) {
    const at = new Date(`${purgeAfter}T00:00:00`);
    const now = new Date();
    return Math.round((at - new Date(now.getFullYear(), now.getMonth(), now.getDate()))
      / 86400000);
  }

  function row(item) {
    const left = daysLeft(item.purge_after);
    return el('div', { class: 'rec-row' },
      el('div', { style: { minWidth: 0 } },
        el('div', { style: { display: 'flex', gap: '8px', alignItems: 'center',
          flexWrap: 'wrap' } },
        el('span', { class: 'badge', text: `${ICONS[item.kind] || ''} ${item.label}` }),
        el('strong', { text: item.title }),
        item.project_name ? el('span', { class: 'hint', text: item.project_name }) : null),
        el('div', { class: 'hint' },
          item.summary ? `${item.summary} ・ ` : '',
          `${item.deleted_by_name || '不明'} が ${formatDateTime(item.deleted_at)} に削除`,
          ' ・ ',
          el('span', { class: left <= 3 ? 'overdue' : '',
            text: left <= 0 ? 'まもなく消えます'
              : `あと ${left} 日（${formatDate(item.purge_after)} まで）` }))),
      el('div', { style: { display: 'flex', gap: '6px' } },
        el('button', {
          class: 'btn btn-sm btn-primary',
          onClick: async (event) => {
            const button = event.currentTarget;
            button.disabled = true;
            try {
              const result = await api.post(`/api/trash/${item.id}/restore`, {});
              toast(result.skipped
                ? `元に戻しました（${result.skipped} 件は相手が無く戻せませんでした）`
                : '元に戻しました', 'ok');
              load();
            } catch (error) {
              toast(error.message, 'error');
              button.disabled = false;
            }
          },
        }, '↩︎ 元に戻す'),
        el('button', {
          class: 'btn btn-sm', title: '待たずに完全に消す',
          onClick: async () => {
            if (!await confirmDialog(
              `「${item.title}」を完全に削除しますか？\nここから先は戻せません。`,
              { danger: true, okLabel: '完全に削除' })) return;
            try {
              await api.del(`/api/trash/${item.id}`);
              toast('完全に削除しました', 'ok');
              load();
            } catch (error) { toast(error.message, 'error'); }
          },
        }, '完全に削除')));
  }

  await load();
}
