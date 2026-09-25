/* ログイン履歴（管理者だけ）。
 *
 * 管理メニューの「ログイン履歴」と、ユーザー管理の「履歴」ボタンの両方で使う。
 * 人を決めて開いたときは、その人のぶんだけを出す。 */
import { api } from '../api.js';
import { store } from '../store.js';
import { el, fill, formatDateTime, openModal, skeleton, toast } from '../util.js';
import { option } from './pickers.js';

const PERIODS = [[7, '7日'], [30, '30日'], [90, '90日'], [365, '1年']];
const KINDS = [['all', 'すべて'], ['login', 'ログインのみ'], ['failed', '失敗のみ'], ['security', 'パスワード・2FA']];

/** 「2026/09/25 10:31」。何分前かは吹き出しで出す。 */
function stamp(value) {
  const [day, time] = String(value).split(' ');
  return `${day.replaceAll('-', '/')} ${(time || '').slice(0, 5)}`;
}

/**
 * 履歴の一覧を作る。
 * @param {object} options userId（その人だけ）、withUserFilter（人で絞る欄を出す）
 */
export function loginHistory({ userId = null, withUserFilter = !userId } = {}) {
  const state = { days: 30, kind: 'all', userId, events: [], hasMore: false };
  const summaryHost = el('div', { class: 'login-summary' });
  const tableHost = el('div', {});
  const moreHost = el('div', { class: 'login-more' });

  const seg = (items, key) => el('div', { class: 'seg' }, ...items.map(([value, label]) => el('button', {
    type: 'button', class: state[key] === value ? 'active' : '',
    onClick: (event) => {
      state[key] = value;
      [...event.currentTarget.parentNode.children]
        .forEach((b) => b.classList.toggle('active', b === event.currentTarget));
      load();
    },
  }, label)));

  const userSelect = withUserFilter ? el('select', {
    class: 'select', style: { maxWidth: '200px' },
    onChange: (event) => { state.userId = Number(event.target.value) || null; load(); },
  }, option('', '全員', true),
  ...store.users.map((u) => option(u.id, u.name))) : null;

  const query = (before = null) => {
    const params = [`days=${state.days}`, `kind=${state.kind}`];
    if (state.userId) params.push(`user_id=${state.userId}`);
    if (before) params.push(`before=${before}`);
    return `/api/admin/logins?${params.join('&')}`;
  };

  async function load() {
    fill(tableHost, skeleton('rows', 5));
    fill(moreHost);
    try {
      const data = await api.get(query());
      state.events = data.events;
      state.hasMore = data.has_more;
      drawSummary(data.summary);
      drawTable();
    } catch (error) {
      fill(tableHost, el('div', { class: 'empty', text: error.message }));
    }
  }

  async function loadMore(button) {
    button.disabled = true;
    try {
      const data = await api.get(query(state.events[state.events.length - 1].id));
      state.events = state.events.concat(data.events);
      state.hasMore = data.has_more;
      drawTable();
    } catch (error) {
      toast(error.message, 'error');
      button.disabled = false;
    }
  }

  function drawSummary(summary) {
    const card = (label, value, tone = '') => el('div', { class: `login-stat ${tone}`.trim() },
      el('div', { class: 'l', text: label }), el('div', { class: 'v', text: String(value) }));
    fill(summaryHost,
      card('ログイン', `${summary.logins} 回`),
      state.userId ? null : card('ログインした人', `${summary.people} 人`),
      card('失敗', `${summary.failed} 回`, summary.failed ? 'warn' : ''),
      card('失敗した接続元', `${summary.failed_ips} か所`, summary.failed_ips > 1 ? 'warn' : ''));
  }

  function drawTable() {
    if (!state.events.length) {
      fill(tableHost, el('div', { class: 'empty', text: 'この期間の記録はありません' }));
      fill(moreHost);
      return;
    }
    fill(tableHost, el('div', { class: 'table-wrap' }, el('table', { class: 'table login-table' },
      el('thead', {}, el('tr', {},
        el('th', { text: '日時' }),
        state.userId ? null : el('th', { text: '人' }),
        el('th', { text: 'できごと' }),
        el('th', { text: '接続元' }),
        el('th', { text: '端末' }))),
      el('tbody', {}, ...state.events.map((e) => el('tr', { class: e.event === 'failed' ? 'is-failed' : '' },
        el('td', { class: 'nowrap', title: formatDateTime(e.created_at), text: stamp(e.created_at) }),
        state.userId ? null : el('td', {},
          el('span', { class: e.user_id ? '' : 'cell-mut', text: e.user_label || '—' })),
        el('td', {},
          el('span', { class: `login-badge ${e.event}`, text: e.event_label }),
          e.reason_label ? el('span', { class: 'login-reason', text: e.reason_label }) : null),
        el('td', { class: 'mono', text: e.ip || '—' }),
        el('td', { title: e.user_agent || '', text: e.device })))))));
    fill(moreHost, state.hasMore
      ? el('button', { class: 'btn btn-sm', onClick: (event) => loadMore(event.currentTarget) },
        'さらに古い記録を見る')
      : null);
  }

  load();
  return el('div', { class: 'login-history' },
    el('div', { class: 'toolbar', style: { padding: '0 0 10px' } },
      el('span', { class: 'label', style: { margin: 0 }, text: '期間' }), seg(PERIODS, 'days'),
      el('span', { class: 'label', style: { margin: '0 0 0 6px' }, text: '種類' }), seg(KINDS, 'kind'),
      userSelect),
    summaryHost, tableHost, moreHost);
}

/** ユーザー管理の「履歴」ボタンから開く、その人だけの履歴。 */
export function openLoginHistory(user) {
  return openModal({
    title: `${user.name} のログイン履歴`,
    wide: true,
    build: () => loginHistory({ userId: user.id }),
    footer: (close) => [el('button', { class: 'btn', onClick: () => close(null) }, '閉じる')],
  });
}
