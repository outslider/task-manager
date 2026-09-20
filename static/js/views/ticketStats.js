/* チケットの集計。日次・週次で「受けた数」と「片付いた数」を並べる。
 *
 * 溜まっているかどうかは、この 2 本の差を見れば分かる。
 * 数字はそのまま CSV に落とせるようにしておく。 */
import { api } from '../api.js';
import { downloadBlob, el, fill, toISO, today } from '../util.js';

const UNITS = [
  { value: 'day', label: '日次', spans: [7, 14, 30, 60] },
  { value: 'week', label: '週次', spans: [4, 8, 12, 26] },
];

export async function ticketStats({ queueId = '', onBack } = {}) {
  const state = { unit: 'day', span: 14, queue_id: queueId, data: null };
  const host = el('div', {});
  const chartHost = el('div', {});
  const breakdownHost = el('div', { class: 'grid cols-2', style: { marginTop: '14px' } });
  const totalHost = el('div', { class: 'grid cols-4', style: { marginBottom: '14px' } });

  const unitSeg = el('div', { class: 'seg' },
    ...UNITS.map((u) => el('button', {
      type: 'button', class: state.unit === u.value ? 'active' : '',
      onClick: () => {
        if (state.unit === u.value) return;
        state.unit = u.value;
        state.span = u.value === 'day' ? 14 : 12;
        draw();
        load();
      },
    }, u.label)));

  const spanHost = el('div', { class: 'seg' });

  function drawSpans() {
    const unit = UNITS.find((u) => u.value === state.unit);
    fill(spanHost, ...unit.spans.map((n) => el('button', {
      type: 'button', class: state.span === n ? 'active' : '',
      onClick: () => { state.span = n; drawSpans(); load(); },
    }, state.unit === 'day' ? `${n}日` : `${n}週`)));
  }

  function draw() {
    [...unitSeg.children].forEach((button, index) => {
      button.className = state.unit === UNITS[index].value ? 'active' : '';
    });
    drawSpans();
  }

  async function load() {
    fill(chartHost, el('div', { class: 'empty', text: '集計中…' }));
    const params = new URLSearchParams({ unit: state.unit, span: String(state.span) });
    if (state.queue_id) params.set('queue_id', state.queue_id);
    try {
      state.data = await api.get(`/api/tickets/stats?${params}`);
    } catch (error) {
      fill(chartHost, el('div', { class: 'empty', text: error.message }));
      return;
    }
    drawTotals();
    drawChart();
    drawBreakdown();
  }

  function drawTotals() {
    const t = state.data.totals;
    const card = (label, value, tone, sub) => el('div', { class: 'card stat' },
      el('div', { class: 'k', text: label }),
      el('div', { class: `v ${tone || ''}`.trim(), text: String(value) }),
      sub ? el('div', { class: 'stat-sub', text: sub }) : null);
    const diff = t.created - t.resolved;
    fill(totalHost,
      card('受けた', t.created, ''),
      card('片付いた', t.resolved, t.resolved ? 'ok' : ''),
      card('差し引き', diff > 0 ? `+${diff}` : String(diff), diff > 0 ? 'warn' : 'ok',
        diff > 0 ? 'そのぶん溜まっています' : '受けたぶんは追いつけています'),
      card('いま未完了', t.open_now, '',
        t.turnaround_days === null ? '' : `平均 ${t.turnaround_days} 日で対応`));
  }

  function drawChart() {
    const buckets = state.data.buckets;
    const peak = Math.max(1, ...buckets.map((b) => Math.max(b.created, b.resolved)));
    fill(chartHost,
      el('div', { class: 'tk-legend' },
        el('span', {}, el('i', { class: 'tk-key created' }), '受けた'),
        el('span', {}, el('i', { class: 'tk-key resolved' }), '片付いた')),
      el('div', { class: 'tk-chart' }, ...buckets.map((b) => el('div', { class: 'tk-col' },
        el('div', { class: 'tk-bars', title: `受けた ${b.created} 件 / 片付いた ${b.resolved} 件` },
          el('div', { class: 'tk-bar created', style: { height: `${(b.created / peak) * 100}%` } },
            b.created ? el('span', { class: 'tk-num', text: String(b.created) }) : null),
          el('div', { class: 'tk-bar resolved', style: { height: `${(b.resolved / peak) * 100}%` } },
            b.resolved ? el('span', { class: 'tk-num', text: String(b.resolved) }) : null)),
        el('div', { class: 'tk-label', text: b.label })))));
  }

  function drawBreakdown() {
    const table = (title, rows, nameOf) => el('div', { class: 'card' },
      el('div', { class: 'card-head' }, el('h2', {}, title)),
      el('div', { class: 'card-body tight' },
        rows.length
          ? el('div', { class: 'table-wrap' }, el('table', { class: 'table' },
            el('thead', {}, el('tr', {},
              el('th', { text: '' }), el('th', { text: '受けた' }),
              el('th', { text: '片付いた' }))),
            el('tbody', {}, ...rows.map((row) => el('tr', {},
              el('td', {}, nameOf(row)),
              el('td', { text: String(row.created) }),
              el('td', { text: String(row.resolved) }))))))
          : el('div', { class: 'empty', text: 'この期間の動きはありません' })));

    fill(breakdownHost,
      table('窓口ごと', state.data.by_queue, (row) => el('span', {},
        el('span', { class: 'dot', style: { background: row.color } }),
        el('span', { text: row.name }))),
      table('種別ごと', state.data.by_kind, (row) =>
        el('span', { text: `${row.icon} ${row.label}` })));
  }

  function exportCsv() {
    if (!state.data) return;
    const unit = state.unit === 'day' ? '日' : '週';
    const header = [`期間（${unit}）`, '受けた', '片付いた'];
    const rows = state.data.buckets.map((b) => [b.key, b.created, b.resolved]);
    const escape = (value) => `"${String(value ?? '').replace(/"/g, '""')}"`;
    const csv = [header, ...rows].map((r) => r.map(escape).join(',')).join('\r\n');
    downloadBlob(new Blob([`﻿${csv}`], { type: 'text/csv;charset=utf-8' }),
      `チケット集計_${state.unit}_${toISO(today())}.csv`);
  }

  fill(host,
    totalHost,
    el('div', { class: 'card' },
      el('div', { class: 'toolbar' },
        onBack
          ? el('button', { class: 'btn btn-sm', onClick: onBack }, '← 一覧に戻る')
          : null,
        el('span', { class: 'label', text: '単位' }), unitSeg,
        el('span', { class: 'label', text: '期間' }), spanHost,
        el('span', { class: 'spacer' }),
        el('button', { class: 'btn btn-sm', onClick: exportCsv }, '⬇ CSV')),
      el('div', { class: 'card-body' }, chartHost)),
    breakdownHost);

  draw();
  await load();
  return host;
}
