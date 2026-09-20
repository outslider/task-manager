/* CSV / Excel からのチケット一括取り込み。
 *
 * 表の読み取りと列の推測は、タスクの取り込みと同じ仕組みを使い回す。
 * 違うのは項目と、窓口が空欄だったときの行き先を選べるところ。 */
import { api } from '../api.js';
import { el, fill, openModal, toast } from '../util.js';
import { TICKET_HINTS, decodeCsv, guessField, parseTable } from './importTasks.js';
import { option } from './pickers.js';

export async function openTicketImport() {
  const [meta, queueData] = await Promise.all([
    api.get('/api/tickets/import-fields'),
    api.get('/api/ticket-queues'),
  ]);
  const open = queueData.queues.filter((q) => q.is_active);
  if (!open.length) {
    toast('受付中の窓口がありません。管理画面で窓口を追加してください', 'error');
    return null;
  }

  const state = { rows: [], header: [], mapping: [], hasHeader: true, raw: [] };

  const queueSelect = el('select', { class: 'select' },
    ...open.map((q) => option(q.id,
      `${q.icon || '📮'} ${q.name}${q.project_name ? `（${q.project_name}）` : ''}`)));
  const paste = el('textarea', {
    class: 'textarea', rows: 6, spellcheck: 'false',
    placeholder: 'Excel で範囲を選んでコピーし、ここに貼り付けます（見出し行も含めて構いません）',
  });
  const file = el('input', { class: 'input', type: 'file', accept: '.csv,.tsv,.txt,text/csv' });
  const headerCheck = el('input', { type: 'checkbox', checked: true });
  const mappingHost = el('div', {});
  const previewHost = el('div', {});
  const problemHost = el('div', {});
  const summary = el('div', { class: 'hint' });

  paste.addEventListener('input', () => load(parseTable(paste.value)));
  headerCheck.addEventListener('change', () => {
    state.hasHeader = headerCheck.checked;
    load(state.raw);
  });
  file.addEventListener('change', async () => {
    const picked = file.files?.[0];
    if (!picked) return;
    paste.value = '';
    load(parseTable(decodeCsv(await picked.arrayBuffer())));
  });

  function load(raw) {
    state.raw = raw;
    if (!raw.length) {
      state.rows = [];
      state.header = [];
      fill(mappingHost);
      fill(previewHost);
      summary.textContent = '';
      return;
    }
    const width = Math.max(...raw.map((r) => r.length));
    state.header = state.hasHeader
      ? raw[0].concat(Array(width).fill('')).slice(0, width)
      : Array.from({ length: width }, (_, i) => `列 ${i + 1}`);
    state.rows = (state.hasHeader ? raw.slice(1) : raw).map((r) =>
      r.concat(Array(width).fill('')).slice(0, width));
    state.mapping = state.header.map((h, i) =>
      (state.hasHeader ? guessField(h, meta.fields, TICKET_HINTS)
        : (i === 0 ? 'title' : '')));
    if (!state.mapping.includes('title')) state.mapping[0] = 'title';
    drawMapping();
    drawPreview();
  }

  function drawMapping() {
    fill(mappingHost,
      el('label', { text: '列の対応づけ' }),
      el('div', { class: 'import-map' },
        ...state.header.map((name, index) => {
          const select = el('select', { class: 'select' },
            el('option', { value: '' }, '取り込まない'),
            ...meta.fields.map((f) => el('option', {
              value: f.value, selected: state.mapping[index] === f.value ? true : null,
            }, f.label)));
          select.addEventListener('change', () => {
            state.mapping.forEach((value, i) => {
              if (i !== index && value && value === select.value) state.mapping[i] = '';
            });
            state.mapping[index] = select.value;
            drawMapping();
            drawPreview();
          });
          return el('div', { class: 'import-map-col' },
            el('div', { class: 'import-map-head', text: name || `列 ${index + 1}`, title: name }),
            select,
            el('div', { class: 'hint',
              text: (state.rows[0] || [])[index] ? `例: ${(state.rows[0] || [])[index]}` : '' }));
        })));
  }

  function toRows() {
    return state.rows.map((cells) => {
      const item = {};
      state.mapping.forEach((field, index) => {
        if (field) item[field] = cells[index];
      });
      return item;
    });
  }

  function drawPreview() {
    const rows = toRows();
    const shown = rows.slice(0, 8);
    const used = meta.fields.filter((f) => state.mapping.includes(f.value));
    fill(previewHost,
      el('label', { text: `取り込む内容（先頭 ${shown.length} 行）` }),
      el('div', { class: 'table-scroll' },
        el('table', { class: 'table' },
          el('thead', {}, el('tr', {}, ...used.map((f) => el('th', { text: f.label })))),
          el('tbody', {}, ...shown.map((item) => el('tr', {},
            ...used.map((f) => el('td', { text: String(item[f.value] ?? '') }))))))));
    summary.textContent = `${rows.length} 行を取り込みます`;
  }

  function showProblems(problems, count) {
    fill(problemHost);
    if (!problems.length) {
      problemHost.append(el('div', { class: 'warn-box ok',
        text: `${count} 件をこのまま登録できます` }));
      return;
    }
    problemHost.append(el('div', { class: 'warn-box' },
      el('strong', { text: `${count} 件を登録できます。${problems.length} 行に注意があります` }),
      el('ul', {}, ...problems.slice(0, 8).map((p) =>
        el('li', { text: `${p.line} 行目: ${p.message}` })))));
  }

  return openModal({
    title: 'チケットを取り込む',
    wide: true,
    build: () => el('div', {},
      el('p', { class: 'page-sub',
        text: 'Excel からコピーして貼り付けるか、CSV ファイルを選んでください。'
          + '「窓口」列がない行は、下で選んだ窓口に入ります。' }),
      el('div', { class: 'field' },
        el('label', { text: '窓口が書かれていないときの行き先 *' }), queueSelect),
      el('div', { class: 'field' }, el('label', { text: 'Excel から貼り付け' }), paste),
      el('div', { class: 'row' },
        el('div', { class: 'field' }, el('label', { text: 'または CSV ファイル' }), file,
          el('div', { class: 'hint', text: 'UTF-8 でも Shift_JIS でも読み込めます' })),
        el('div', { class: 'field' },
          el('label', { text: '1行目' }),
          el('label', { class: 'check' }, headerCheck,
            el('span', { text: '1行目は見出しとして扱う' })))),
      mappingHost, previewHost, problemHost, summary,
      el('div', { class: 'hint', style: { marginTop: '8px' },
        text: '使える列: ' + meta.fields.map((f) => f.label).join('、') })),
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', {
        class: 'btn',
        onClick: async (event) => {
          if (!state.rows.length) { toast('取り込む行がありません', 'error'); return; }
          const button = event.currentTarget;
          button.disabled = true;
          try {
            const result = await api.post('/api/tickets/import', {
              rows: toRows(), queue_id: Number(queueSelect.value), dry_run: true,
            });
            showProblems(result.problems, result.would_create);
          } catch (error) { toast(error.message, 'error'); }
          button.disabled = false;
        },
      }, '確認する'),
      el('button', {
        class: 'btn btn-primary',
        onClick: async (event) => {
          if (!state.rows.length) { toast('取り込む行がありません', 'error'); return; }
          const button = event.currentTarget;
          button.disabled = true;
          try {
            const result = await api.post('/api/tickets/import', {
              rows: toRows(), queue_id: Number(queueSelect.value),
            });
            toast(`${result.created} 件を取り込みました`, 'ok');
            if (result.problems.length) {
              toast(`${result.problems.length} 行に注意があります（担当者など）`, 'error');
            }
            close(true);
          } catch (error) {
            toast(error.message, 'error');
            button.disabled = false;
          }
        },
      }, '取り込む'),
    ],
  });
}
