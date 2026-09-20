/* チケットの集計。日次・週次で「受けた数」と「片付いた数」を並べる。
 *
 * 溜まっているかどうかは、この 2 本の差を見れば分かる。
 * 数字はそのまま CSV に落とせるようにしておく。 */
import { api } from '../api.js';
import { avatar, downloadBlob, el, fill, toISO } from '../util.js';

const UNITS = [
  { value: 'day', label: '日次', spans: [7, 14, 30, 60] },
  { value: 'week', label: '週次', spans: [4, 8, 12, 26] },
];

export async function ticketStats({ queueId = '', onBack } = {}) {
  const state = { unit: 'day', span: 14, queue_id: queueId, end: '', data: null };
  const host = el('div', {});
  const chartHost = el('div', {});
  const queueHost = el('span', {});
  const rangeHost = el('div', { class: 'tk-range' });
  const breakdownHost = el('div', { class: 'grid cols-2', style: { marginTop: '14px' } });
  // 表が 3 つ以上になっても、2 列で素直に折り返す
  const totalHost = el('div', { class: 'grid cols-4', style: { marginBottom: '14px' } });

  /** 窓口を切り替える。「すべての窓口」に戻せるようにしておく。 */
  async function drawQueuePicker() {
    let queues = [];
    try {
      ({ queues } = await api.get('/api/ticket-queues'));
    } catch { /* 取れなくても集計自体は出せる */ }
    const picker = el('select', { class: 'select', style: { maxWidth: '200px' } },
      el('option', { value: '', selected: state.queue_id ? null : true }, 'すべての窓口'),
      ...queues.map((q) => el('option', {
        value: q.id, selected: String(state.queue_id) === String(q.id) ? true : null,
      }, `${q.icon || '📮'} ${q.name}`)));
    picker.addEventListener('change', () => {
      state.queue_id = picker.value;
      load();
    });
    fill(queueHost, picker);
  }

  const unitSeg = el('div', { class: 'seg' },
    ...UNITS.map((u) => el('button', {
      type: 'button', class: state.unit === u.value ? 'active' : '',
      onClick: () => {
        if (state.unit === u.value) return;
        state.unit = u.value;
        state.span = u.value === 'day' ? 14 : 12;
        state.end = '';
        draw();
        load();
      },
    }, u.label)));

  const spanHost = el('div', { class: 'seg' });

  function drawSpans() {
    const unit = UNITS.find((u) => u.value === state.unit);
    fill(spanHost, ...unit.spans.map((n) => el('button', {
      type: 'button', class: state.span === n ? 'active' : '',
      onClick: () => { state.span = n; state.end = ''; drawSpans(); load(); },
    }, state.unit === 'day' ? `${n}日` : `${n}週`)));
  }

  function draw() {
    [...unitSeg.children].forEach((button, index) => {
      button.className = state.unit === UNITS[index].value ? 'active' : '';
    });
    drawSpans();
  }

  /** 期間を 1 画面ぶんずらす。半年より前へは行けない。 */
  function shift(direction) {
    const d = state.data;
    if (!d) return;
    const days = (state.unit === 'week' ? 7 : 1) * state.span * direction;
    const at = new Date(`${d.end}T00:00:00`);
    at.setDate(at.getDate() + days);
    state.end = toISO(at);
    load();
  }

  function drawRange() {
    const d = state.data;
    if (!d) { fill(rangeHost); return; }
    fill(rangeHost,
      el('button', {
        class: 'btn btn-sm', title: '前の期間', disabled: d.can_go_back ? null : true,
        onClick: () => shift(-1),
      }, '←'),
      el('span', { class: 'tk-range-label', text: `${d.from} 〜 ${d.to}` }),
      el('button', {
        class: 'btn btn-sm', title: '次の期間', disabled: d.can_go_forward ? null : true,
        onClick: () => shift(1),
      }, '→'),
      d.can_go_forward
        ? el('button', {
          class: 'btn btn-sm', onClick: () => { state.end = ''; load(); },
        }, '今に戻る')
        : null,
      d.can_go_back
        ? null
        : el('span', { class: 'hint', text: 'さかのぼれるのはここまで（半年）' }));
  }

  async function load() {
    fill(chartHost, el('div', { class: 'empty', text: '集計中…' }));
    const params = new URLSearchParams({ unit: state.unit, span: String(state.span) });
    if (state.queue_id) params.set('queue_id', state.queue_id);
    if (state.end) params.set('end', state.end);
    try {
      state.data = await api.get(`/api/tickets/stats?${params}`);
    } catch (error) {
      fill(chartHost, el('div', { class: 'empty', text: error.message }));
      return;
    }
    drawTotals();
    drawChart();
    drawBreakdown();
    drawRange();
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
      t.spent_hours
        ? card('かかった時間', `${t.spent_hours}h`, '',
          `${t.spent_rows} 件に入力あり / 平均 ${t.turnaround_days ?? '—'} 日で対応`)
        : card('いま未完了', t.open_now, '',
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

  /**
   * 内訳の表。extra を渡すと 1 列足せる（担当者の平均日数など）。
   */
  function table(title, rows, nameOf, extra) {
    // 対応時間はどこかに入っているときだけ列を出す（全部空なら邪魔なだけ）
    const anyHours = rows.some((row) => row.spent_hours);
    return el('div', { class: 'card' },
      el('div', { class: 'card-head' }, el('h2', {}, title)),
      el('div', { class: 'card-body tight' },
        rows.length
          ? el('div', { class: 'table-wrap' }, el('table', { class: 'table' },
            el('thead', {}, el('tr', {},
              el('th', { text: '' }), el('th', { text: '受けた' }),
              el('th', { text: '片付いた' }),
              anyHours ? el('th', { text: '時間' }) : null,
              extra ? el('th', { text: extra.label }) : null)),
            el('tbody', {}, ...rows.map((row) => el('tr', {},
              el('td', {}, nameOf(row)),
              el('td', { text: String(row.created) }),
              el('td', { text: String(row.resolved) }),
              anyHours
                ? el('td', { text: row.spent_hours ? `${row.spent_hours}h` : '—' })
                : null,
              extra ? el('td', {}, extra.cell(row)) : null)))))
          : el('div', { class: 'empty', text: 'この期間の動きはありません' })));
  }

  function drawBreakdown() {
    const data = state.data;
    fill(breakdownHost,
      table('担当者ごと', data.by_assignee, (row) => el('span', { class: 'avatar-stack' },
        avatar({ name: row.name, avatar_color: row.avatar_color }, 'sm'),
        el('span', { text: row.name })), {
        label: '平均日数',
        cell: (row) => el('span', {
          class: row.turnaround_days === null ? 'hint' : '',
          text: row.turnaround_days === null ? '—' : `${row.turnaround_days} 日`,
        }),
      }),
      data.has_categories
        ? table('分類ごと', data.by_category, (row) => el('span', {},
          el('span', { class: 'dot', style: { background: row.color } }),
          el('span', { text: row.label })))
        : null,
      table('窓口ごと', data.by_queue, (row) => el('span', {},
        el('span', { class: 'dot', style: { background: row.color } }),
        el('span', { text: row.name }))),
      table('種別ごと', data.by_kind, (row) =>
        el('span', { text: `${row.icon} ${row.label}` })));
  }

  function exportCsv() {
    if (!state.data) return;
    const d = state.data;
    const unit = state.unit === 'day' ? '日' : '週';
    const header = ['区切り', '名前', '受けた', '片付いた', '時間', '平均日数'];
    const rows = [
      ...d.buckets.map((b) => [`期間（${unit}）`, b.key, b.created, b.resolved, '', '']),
      ...d.by_assignee.map((p) => ['担当者', p.name, p.created, p.resolved,
        p.spent_hours ?? '', p.turnaround_days ?? '']),
      ...(d.has_categories
        ? d.by_category.map((c) =>
          ['分類', c.label, c.created, c.resolved, c.spent_hours ?? '', ''])
        : []),
      ...d.by_queue.map((q) =>
        ['窓口', q.name, q.created, q.resolved, q.spent_hours ?? '', '']),
      ...d.by_kind.map((k) =>
        ['種別', k.label, k.created, k.resolved, k.spent_hours ?? '', '']),
    ];
    const escape = (value) => `"${String(value ?? '').replace(/"/g, '""')}"`;
    const csv = [header, ...rows].map((r) => r.map(escape).join(',')).join('\r\n');
    downloadBlob(new Blob([`﻿${csv}`], { type: 'text/csv;charset=utf-8' }),
      `チケット集計_${state.unit}_${d.from}_${d.to}.csv`);
  }

  fill(host,
    totalHost,
    el('div', { class: 'card' },
      el('div', { class: 'toolbar' },
        onBack
          ? el('button', { class: 'btn btn-sm', onClick: onBack }, '← 一覧に戻る')
          : null,
        queueHost,
        el('span', { class: 'label', text: '単位' }), unitSeg,
        el('span', { class: 'label', text: '期間' }), spanHost,
        el('span', { class: 'spacer' }),
        el('button', { class: 'btn btn-sm', onClick: exportCsv }, '⬇ CSV')),
      el('div', { class: 'card-body' }, rangeHost, chartHost)),
    breakdownHost);

  draw();
  await drawQueuePicker();
  await load();
  return host;
}
