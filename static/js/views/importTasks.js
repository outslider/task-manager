/* 表計算ソフトからのタスク一括取り込み。
 *
 * Excel からそのまま貼り付ける（タブ区切り）か、CSV ファイルを選ぶ。
 * 列の対応づけは推測したうえで、画面で直せるようにしている。 */
import { api } from '../api.js';
import { el, fill, openModal, toast } from '../util.js';

/** 区切り文字を推測して 1 行ずつの配列にする。引用符の中の改行やカンマも扱う。 */
export function parseTable(text) {
  const body = text.replace(/^﻿/, '').replace(/\r\n?/g, '\n').replace(/\n+$/, '');
  if (!body.trim()) return [];
  const head = body.split('\n')[0];
  const delimiter = (head.match(/\t/g) || []).length >= (head.match(/,/g) || []).length ? '\t' : ',';
  const rows = [];
  let row = [];
  let field = '';
  let quoted = false;
  for (let i = 0; i < body.length; i += 1) {
    const ch = body[i];
    if (quoted) {
      if (ch === '"') {
        if (body[i + 1] === '"') { field += '"'; i += 1; } else { quoted = false; }
      } else { field += ch; }
      continue;
    }
    if (ch === '"' && field === '') { quoted = true; continue; }
    if (ch === delimiter) { row.push(field); field = ''; continue; }
    if (ch === '\n') { row.push(field); rows.push(row); row = []; field = ''; continue; }
    field += ch;
  }
  row.push(field);
  rows.push(row);
  return rows.filter((r) => r.some((cell) => String(cell).trim() !== ''));
}

/** Excel の CSV は Shift_JIS のことが多いので、文字化けしない方を選ぶ。 */
export function decodeCsv(buffer) {
  const bytes = new Uint8Array(buffer);
  if (bytes[0] === 0xEF && bytes[1] === 0xBB && bytes[2] === 0xBF) {
    return new TextDecoder('utf-8').decode(buffer);
  }
  const utf8 = new TextDecoder('utf-8', { fatal: false }).decode(buffer);
  if (!utf8.includes('�')) return utf8;
  try {
    return new TextDecoder('shift_jis').decode(buffer);
  } catch {
    return utf8;
  }
}

/** 見出しの文字から、どの項目かを推測する。 */
export function guessField(header, fields) {
  const text = String(header || '').trim().toLowerCase().replace(/[（(].*?[)）]/g, '');
  if (!text) return '';
  const hints = {
    title: ['タスク', 'たすく', '件名', '作業', '項目', '内容', 'title', 'name', 'task'],
    level: ['階層', 'レベル', 'level', 'indent'],
    parent: ['親', 'parent'],
    assignee: ['担当', '責任', 'assignee', 'owner', '担当者'],
    start_date: ['開始', '着手', 'start', 'from'],
    due_date: ['期限', '締切', '終了', '完了予定', 'due', 'end', 'deadline'],
    category: ['カテゴリ', '分類', '種別', 'category', 'type'],
    priority: ['重要', '優先', 'priority', 'importance'],
    status: ['状態', 'ステータス', '進捗状況', 'status'],
    progress: ['進捗', '達成', 'progress', '%'],
    estimate_hours: ['見積', '工数', '予定工数', 'estimate', 'hours'],
    description: ['メモ', '備考', '説明', '詳細', 'note', 'memo', 'description'],
    is_milestone: ['マイルストーン', '節目', 'milestone'],
  };
  for (const field of fields) {
    for (const hint of hints[field.value] || []) {
      if (text.includes(hint)) return field.value;
    }
  }
  return '';
}

export async function openImportDialog(project) {
  const meta = await api.get('/api/import/fields');
  const state = { rows: [], header: [], mapping: [], hasHeader: true };

  const paste = el('textarea', {
    class: 'textarea', rows: 6, spellcheck: 'false',
    placeholder: 'Excel で範囲を選んでコピーし、ここに貼り付けます（見出し行も含めて構いません）',
  });
  const file = el('input', { class: 'input', type: 'file', accept: '.csv,.tsv,.txt,text/csv' });
  const headerCheck = el('input', { type: 'checkbox', checked: true });
  const mappingHost = el('div', {});
  const previewHost = el('div', {});
  const summary = el('div', { class: 'hint' });

  paste.addEventListener('input', () => load(parseTable(paste.value)));
  headerCheck.addEventListener('change', () => {
    state.hasHeader = headerCheck.checked;
    load(state.raw || []);
  });
  file.addEventListener('change', async () => {
    const picked = file.files?.[0];
    if (!picked) return;
    const text = decodeCsv(await picked.arrayBuffer());
    paste.value = '';
    load(parseTable(text));
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
      (state.hasHeader ? guessField(h, meta.fields) : (i === 0 ? 'title' : '')));
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
            // 同じ項目を二重に割り当てない
            state.mapping.forEach((value, i) => {
              if (i !== index && value && value === select.value) state.mapping[i] = '';
            });
            state.mapping[index] = select.value;
            drawMapping();
            drawPreview();
          });
          return el('div', { class: 'import-map-col' },
            el('div', { class: 'import-map-head', text: name || `列 ${index + 1}`,
              title: name }),
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

  const problemHost = el('div', {});

  const saved = await openModal({
    title: `${project.name} にタスクを取り込む`,
    wide: true,
    build: () => el('div', {},
      el('p', { class: 'page-sub',
        text: 'Excel からコピーして貼り付けるか、CSV ファイルを選んでください。'
          + 'タスク名の先頭を字下げする（または「階層」列を作る）と、そのまま子タスクになります。' }),
      el('div', { class: 'field' },
        el('label', { text: 'Excel から貼り付け' }), paste),
      el('div', { class: 'row' },
        el('div', { class: 'field' }, el('label', { text: 'または CSV ファイル' }), file,
          el('div', { class: 'hint', text: 'UTF-8 でも Shift_JIS でも読み込めます' })),
        el('div', { class: 'field' },
          el('label', { text: '1行目' }),
          el('label', { class: 'check' }, headerCheck,
            el('span', { text: '1行目は見出しとして扱う' })))),
      mappingHost, previewHost, problemHost, summary),
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', {
        class: 'btn',
        onClick: async (event) => {
          const button = event.currentTarget;
          if (!state.rows.length) { toast('取り込む行がありません', 'error'); return; }
          button.disabled = true;
          try {
            const result = await api.post(
              `/api/projects/${project.id}/tasks/import`,
              { rows: toRows(), dry_run: true });
            showProblems(result.problems, result.would_create);
          } catch (error) { toast(error.message, 'error'); }
          button.disabled = false;
        },
      }, '確認する'),
      el('button', {
        class: 'btn btn-primary',
        onClick: async (event) => {
          const button = event.currentTarget;
          if (!state.rows.length) { toast('取り込む行がありません', 'error'); return; }
          button.disabled = true;
          try {
            const result = await api.post(
              `/api/projects/${project.id}/tasks/import`, { rows: toRows() });
            toast(`${result.created} 件のタスクを取り込みました`, 'ok');
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

  function showProblems(problems, count) {
    if (!problems.length) {
      fill(problemHost, el('div', { class: 'warn-box ok',
        text: `${count} 行すべて問題なく取り込めます。` }));
      return;
    }
    fill(problemHost, el('div', { class: 'warn-box' },
      el('div', { text: `${problems.length} 行に確認したい点があります（取り込みは続行できます）` }),
      el('ul', { style: { margin: '6px 0 0', paddingLeft: '18px' } },
        ...problems.slice(0, 10).map((p) =>
          el('li', { text: `${p.line} 行目: ${p.message}` })),
        problems.length > 10
          ? el('li', { text: `… 他 ${problems.length - 10} 行` })
          : null)));
  }

  return saved;
}
