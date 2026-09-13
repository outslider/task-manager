/* Gantt chart: SVG rendering plus PowerPoint-friendly SVG / PNG export. */
import { api } from '../api.js';
import { setHeader } from '../app.js';
import { store, STATUS_COLOR, STATUS_LABEL, IMPORTANCE_LABEL, CATEGORIES, category }
  from '../store.js';
import {
  addDays, daysBetween, downloadBlob, el, fill, isWeekend, openModal, parseDate, svgEl,
  toISO, toast, today, weekday,
} from '../util.js';
import { buildTree } from './tasks.js';
import { openTaskDetail } from './taskDetail.js';
import { projectTabs } from './projectNav.js';

const SCALES = {
  day: { key: 'day', label: '日', dayWidth: 26, minorEvery: 1 },
  week: { key: 'week', label: '週', dayWidth: 9, minorEvery: 7 },
  month: { key: 'month', label: '月', dayWidth: 3.6, minorEvery: 30 },
};

const IMPORTANCE_COLOR = { 0: '#98a2b3', 1: '#3b6ef5', 2: '#e8912b', 3: '#e14c4c' };

const COLOR_MODES = {
  status: {
    label: '状態',
    color: (task) => STATUS_COLOR[task.status] || '#98a2b3',
    legend: () => Object.entries(STATUS_LABEL).map(([k, v]) => ({ color: STATUS_COLOR[k], label: v })),
  },
  category: {
    label: 'カテゴリ',
    color: (task) => category(task.category).color,
    legend: () => [...CATEGORIES.map((c) => ({ color: c.color, label: c.label })),
      { color: '#98a2b3', label: '未分類' }],
  },
  importance: {
    label: '重要度',
    color: (task) => IMPORTANCE_COLOR[task.priority] || '#98a2b3',
    legend: () => Object.entries(IMPORTANCE_LABEL).reverse()
      .map(([k, v]) => ({ color: IMPORTANCE_COLOR[k], label: v })),
  },
};

const ROW_H = 26;
const HEADER_H = 46;
const PAD = 14;
const NAME_W_DEFAULT = 250;

/* Slide presets: width/height in pixels at 96dpi, matching PowerPoint slide sizes. */
const EXPORT_PRESETS = [
  { id: 'ppt169', label: 'PowerPoint 16:9 (33.87×19.05cm)', w: 1280, h: 720 },
  { id: 'ppt169x2', label: 'PowerPoint 16:9 高解像度 (2倍)', w: 2560, h: 1440 },
  { id: 'ppt43', label: 'PowerPoint 4:3 (25.4×19.05cm)', w: 1024, h: 768 },
  { id: 'ppt43x2', label: 'PowerPoint 4:3 高解像度 (2倍)', w: 2048, h: 1536 },
  { id: 'a4', label: 'A4 横 (印刷用 150dpi)', w: 1754, h: 1240 },
  { id: 'natural', label: '原寸（切り取らずそのまま）', w: 0, h: 0 },
];

export async function render(container, route) {
  const projectId = route.projectId;
  let data = await api.projectTasks(projectId);
  const project = data.project;

  const compact = window.innerWidth < 760;
  const canEdit = store.canEdit(project);
  const state = {
    scale: localStorage.getItem('tm.gantt.scale') || (compact ? 'week' : 'day'),
    colorBy: localStorage.getItem('tm.gantt.colorBy') || 'status',
    showDone: true,
    onlyMine: false,
    nameWidth: compact ? 140 : NAME_W_DEFAULT,
    fromISO: null,
    toISO: null,
  };

  setHeader(`${project.name} — ガント`, [
    el('button', { class: 'btn btn-primary', onClick: () => exportDialog() }, '⬇ エクスポート'),
  ]);

  const scroll = el('div', { class: 'gantt-scroll' });

  const scaleSeg = el('div', { class: 'seg' },
    ...Object.values(SCALES).map((scale) => el('button', {
      class: state.scale === scale.key ? 'active' : '',
      onClick: (event) => {
        state.scale = scale.key;
        localStorage.setItem('tm.gantt.scale', scale.key);
        [...event.currentTarget.parentNode.children].forEach((b) => b.classList.remove('active'));
        event.currentTarget.classList.add('active');
        draw();
      },
    }, scale.label)));

  const colorSelect = el('select', {
    class: 'select', style: { maxWidth: '130px' },
    onChange: (event) => {
      state.colorBy = event.target.value;
      localStorage.setItem('tm.gantt.colorBy', state.colorBy);
      draw();
    },
  }, ...Object.entries(COLOR_MODES).map(([value, mode]) =>
    el('option', { value, selected: state.colorBy === value ? true : null }, mode.label)));

  const fromInput = el('input', {
    class: 'input', type: 'date', style: { maxWidth: '150px' },
    onChange: (event) => { state.fromISO = event.target.value || null; draw(); },
  });
  const toInput = el('input', {
    class: 'input', type: 'date', style: { maxWidth: '150px' },
    onChange: (event) => { state.toISO = event.target.value || null; draw(); },
  });

  const toolbar = el('div', { class: 'toolbar' },
    el('span', { class: 'label', style: { margin: 0 }, text: '表示単位' }), scaleSeg,
    el('span', { class: 'label', style: { margin: '0 0 0 6px' }, text: '色分け' }), colorSelect,
    el('span', { class: 'label', style: { margin: '0 0 0 6px' }, text: '期間' }), fromInput, '〜', toInput,
    el('label', { class: 'check' },
      el('input', {
        type: 'checkbox', checked: true,
        onChange: (event) => { state.showDone = event.target.checked; draw(); },
      }), el('span', { text: '完了も表示' })),
    el('label', { class: 'check' },
      el('input', {
        type: 'checkbox',
        onChange: (event) => { state.onlyMine = event.target.checked; draw(); },
      }), el('span', { text: '自分の担当のみ' })),
    el('div', { class: 'spacer' }),
    canEdit
      ? el('span', { class: 'hint', style: { marginRight: '4px' } },
        'バーをドラッグで移動、端をドラッグで期間変更')
      : null,
    el('button', {
      class: 'btn btn-sm',
      onClick: () => { state.fromISO = null; state.toISO = null; syncRangeInputs(); draw(); },
    }, '期間リセット'));

  const legend = el('div', { class: 'gantt-legend' });

  function drawLegend() {
    fill(legend,
      ...COLOR_MODES[state.colorBy].legend().map((entry) => el('span', { class: 'legend-item' },
        el('span', { class: 'legend-swatch', style: { background: entry.color } }),
        el('span', { text: entry.label }))),
      el('span', { class: 'legend-item' }, el('span', { text: '◆ マイルストーン' })),
      el('span', { class: 'legend-item' },
        el('span', {
          class: 'legend-swatch',
          style: { background: 'transparent', border: '2px solid #e14c4c' },
        }),
        el('span', { text: 'クリティカルパス' })),
      el('span', { class: 'legend-item' },
        el('span', { class: 'legend-swatch', style: { background: '#e14c4c', width: '2px' } }),
        el('span', { text: '本日' })));
  }

  container.className = 'content flush';
  fill(container,
    el('div', { class: 'gantt-wrap' },
      projectTabs(projectId, 'gantt'), toolbar, scroll, legend));

  function visibleRows() {
    const tasks = data.tasks.filter((task) => {
      if (!state.showDone && task.status === 'done') return false;
      if (state.onlyMine && task.assignee_id !== store.user.id) return false;
      return true;
    });
    const { children } = buildTree(tasks);
    const rows = [];
    const walk = (parentId, depth) => {
      for (const task of children.get(parentId) || []) {
        rows.push({ task, depth, hasChildren: (children.get(task.id) || []).length > 0 });
        walk(task.id, depth + 1);
      }
    };
    walk(null, 0);
    return rows;
  }

  function dateRange(rows) {
    let min = null;
    let max = null;
    for (const { task } of rows) {
      for (const value of [task.rollup_start || task.start_date, task.rollup_due || task.due_date]) {
        const date = parseDate(value);
        if (!date) continue;
        if (!min || date < min) min = date;
        if (!max || date > max) max = date;
      }
    }
    const now = today();
    if (!min) min = addDays(now, -7);
    if (!max) max = addDays(now, 21);
    if (min > now) min = addDays(now, -3);
    if (max < now) max = addDays(now, 3);
    let from = state.fromISO ? parseDate(state.fromISO) : addDays(min, -3);
    let to = state.toISO ? parseDate(state.toISO) : addDays(max, 4);
    if (to <= from) to = addDays(from, 14);
    return { from, to };
  }

  function syncRangeInputs(range) {
    if (range) {
      fromInput.value = state.fromISO || toISO(range.from);
      toInput.value = state.toISO || toISO(range.to);
    } else {
      fromInput.value = '';
      toInput.value = '';
    }
  }

  function draw() {
    const rows = visibleRows();
    const range = dateRange(rows);
    syncRangeInputs(range);
    const svg = buildGanttSvg({
      rows, range, scale: SCALES[state.scale], deps: data.deps,
      conflicts: data.conflicts, colorBy: state.colorBy,
      title: project.name, nameWidth: state.nameWidth, interactive: true,
      editable: canEdit, onEdit: applyEdit, scroller: scroll,
    });
    drawLegend();
    fill(scroll, svg);
    const names = svg.querySelector('.gantt-names');
    if (names) {
      const stick = () => names.setAttribute('transform', `translate(${scroll.scrollLeft},0)`);
      scroll.onscroll = stick;
      stick();
    }
  }

  /** バーをドラッグして確定した日程を保存する。 */
  async function applyEdit(task, patch) {
    const scrollLeft = scroll.scrollLeft;
    try {
      await api.patch(`/api/tasks/${task.id}`, patch);
      data = await api.projectTasks(projectId);
      draw();
      scroll.scrollLeft = scrollLeft;     // 見ていた位置を保つ
      toast(`${task.title}: ${patch.start_date || '—'} 〜 ${patch.due_date}`, 'ok');
    } catch (error) {
      toast(error.message, 'error');
      draw();
      scroll.scrollLeft = scrollLeft;
    }
  }

  async function exportDialog() {
    const rows = visibleRows();
    const range = dateRange(rows);
    const preset = { value: 'ppt169' };
    const includeTitle = { value: true };
    const format = { value: 'png' };
    const fitWidth = { value: true };

    const presetSelect = el('select', { class: 'select' },
      ...EXPORT_PRESETS.map((p) => el('option', { value: p.id }, p.label)));
    presetSelect.addEventListener('change', () => { preset.value = presetSelect.value; });
    const formatSelect = el('select', { class: 'select' },
      el('option', { value: 'png' }, 'PNG 画像（貼り付けが簡単・推奨）'),
      el('option', { value: 'svg' }, 'SVG ベクター（拡大しても綺麗）'));
    formatSelect.addEventListener('change', () => { format.value = formatSelect.value; });
    const titleCheck = el('input', { type: 'checkbox', checked: true });
    titleCheck.addEventListener('change', () => { includeTitle.value = titleCheck.checked; });
    const fitCheck = el('input', { type: 'checkbox', checked: true });
    fitCheck.addEventListener('change', () => { fitWidth.value = fitCheck.checked; });

    await openModal({
      title: 'ガントチャートをエクスポート',
      build: () => el('div', {},
        el('p', { class: 'page-sub',
          text: 'PowerPoint のスライドサイズに合わせて書き出します。貼り付け後はそのままスライド全面に収まります。' }),
        el('div', { class: 'field' }, el('label', { text: '出力サイズ' }), presetSelect),
        el('div', { class: 'field' }, el('label', { text: '形式' }), formatSelect),
        el('div', { class: 'field' },
          el('label', { class: 'check' }, titleCheck,
            el('span', { text: 'タイトルと作成日を入れる' }))),
        el('div', { class: 'field' },
          el('label', { class: 'check' }, fitCheck,
            el('span', { text: 'スライド幅いっぱいに広げる（推奨）' })),
          el('div', { class: 'hint',
            text: '期間の長さに合わせて目盛りの幅を自動調整し、余白の少ない図にします。' })),
        el('div', { class: 'hint',
          text: `対象タスク ${rows.length} 件 / 期間 ${toISO(range.from)} 〜 ${toISO(range.to)}` })),
      footer: (close) => [
        el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
        el('button', {
          class: 'btn btn-primary',
          onClick: async (event) => {
            const button = event.currentTarget;
            button.disabled = true;
            try {
              await runExport(rows, range, preset.value, format.value,
                includeTitle.value, fitWidth.value);
              close(true);
            } catch (error) {
              toast(error.message || 'エクスポートに失敗しました', 'error');
              button.disabled = false;
            }
          },
        }, '書き出す'),
      ],
    });
  }

  async function runExport(rows, range, presetId, format, withTitle, fitWidth = true) {
    const target = EXPORT_PRESETS.find((p) => p.id === presetId);
    const scale = (fitWidth && target.w)
      ? fitScale(range, target.w, NAME_W_DEFAULT)
      : SCALES[state.scale];
    const svg = buildGanttSvg({
      rows, range, scale, deps: data.deps,
      conflicts: data.conflicts, colorBy: state.colorBy,
      title: withTitle ? project.name : null,
      subtitle: withTitle
        ? `${toISO(range.from)} 〜 ${toISO(range.to)}　作成日: ${toISO(today())}`
        : null,
      nameWidth: NAME_W_DEFAULT, forExport: true,
    });
    const stamp = toISO(today());
    const base = `${project.name}_ガント_${stamp}`.replace(/[\\/:*?"<>|]/g, '_');
    if (format === 'svg') {
      const text = new XMLSerializer().serializeToString(svg);
      downloadBlob(new Blob([`<?xml version="1.0" encoding="UTF-8"?>\n${text}`],
        { type: 'image/svg+xml;charset=utf-8' }), `${base}.svg`);
      toast('SVG を書き出しました', 'ok');
      return;
    }
    const natural = { w: Number(svg.getAttribute('width')), h: Number(svg.getAttribute('height')) };
    const size = target.w ? target : { w: natural.w, h: natural.h };
    const blob = await rasterize(svg, natural, size);
    downloadBlob(blob, `${base}.png`);
    toast('PNG を書き出しました', 'ok');
  }

  draw();
}

/* ------------------------------------------------------------------ render */

/**
 * Build a standalone SVG element for the chart.
 * The same function powers both the on-screen chart and the exported file, so
 * what you see is exactly what gets pasted into a slide.
 */
export function buildGanttSvg({
  rows, range, scale, deps = [], conflicts = [], colorBy = 'status',
  title = null, subtitle = null, editable = false, onEdit = null, scroller = null,
  nameWidth = NAME_W_DEFAULT, interactive = false, forExport = false,
}) {
  const colorOf = (COLOR_MODES[colorBy] || COLOR_MODES.status).color;
  const conflictEdges = new Set(
    conflicts.map((c) => `${c.depends_on_id}->${c.task_id}`));
  const totalDays = Math.max(1, daysBetween(range.from, range.to) + 1);
  const dayWidth = scale.dayWidth;
  const chartW = Math.round(totalDays * dayWidth);
  const titleH = title ? 42 : 0;
  const width = nameWidth + chartW + PAD * 2;
  const height = titleH + HEADER_H + rows.length * ROW_H + PAD * 2 + 6;

  const colors = forExport
    ? {
      bg: '#ffffff', text: '#1b2028', muted: '#667085', grid: '#e6e9ee',
      gridStrong: '#cbd1da', weekend: '#f5f6f8', band: '#fbfcfd', bar: '#c9d3e4',
    }
    : {
      bg: 'var(--surface)', text: 'var(--text)', muted: 'var(--text-muted)',
      grid: 'var(--border)', gridStrong: 'var(--border-strong)',
      weekend: 'var(--surface-3)', band: 'var(--surface-2)', bar: 'var(--border-strong)',
    };

  const svg = svgEl('svg', {
    xmlns: 'http://www.w3.org/2000/svg', width, height,
    viewBox: `0 0 ${width} ${height}`, class: 'gantt-svg',
    'font-family': 'system-ui, -apple-system, "Hiragino Sans", "Noto Sans JP", Meiryo, sans-serif',
  });

  svg.appendChild(svgEl('rect', { x: 0, y: 0, width, height, fill: colors.bg }));

  if (title) {
    svg.appendChild(svgEl('text', {
      x: PAD, y: 24, 'font-size': 17, 'font-weight': 700, fill: colors.text, text: title,
    }));
    if (subtitle) {
      svg.appendChild(svgEl('text', {
        x: PAD, y: 38, 'font-size': 11, fill: colors.muted, text: subtitle,
      }));
    }
  }

  const originX = PAD + nameWidth;
  const originY = titleH + PAD + HEADER_H;
  const x = (date) => originX + daysBetween(range.from, date) * dayWidth;

  /* ---- background bands and weekend shading ---- */
  const bands = svgEl('g');
  rows.forEach((row, index) => {
    if (index % 2 === 1) {
      bands.appendChild(svgEl('rect', {
        x: PAD, y: originY + index * ROW_H, width: nameWidth + chartW, height: ROW_H,
        fill: colors.band,
      }));
    }
  });
  svg.appendChild(bands);

  const gridG = svgEl('g');
  const bodyH = rows.length * ROW_H;
  if (scale.key === 'day') {
    for (let i = 0; i < totalDays; i += 1) {
      const date = addDays(range.from, i);
      if (isWeekend(date)) {
        gridG.appendChild(svgEl('rect', {
          x: originX + i * dayWidth, y: originY, width: dayWidth, height: bodyH,
          fill: colors.weekend, opacity: forExport ? 1 : 0.7,
        }));
      }
    }
  }

  /* ---- timeline header ---- */
  const headerG = svgEl('g');
  const headerY = titleH + PAD;
  headerG.appendChild(svgEl('rect', {
    x: PAD, y: headerY, width: nameWidth + chartW, height: HEADER_H,
    fill: forExport ? '#f7f8fa' : 'var(--surface-2)',
  }));
  let cursor = new Date(range.from.getTime());
  while (cursor <= range.to) {
    const monthStart = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
    const nextMonth = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1);
    const startX = Math.max(originX, x(monthStart));
    const endX = Math.min(originX + chartW, x(nextMonth));
    if (endX - startX > 26) {
      headerG.appendChild(svgEl('text', {
        x: startX + 5, y: headerY + 16, 'font-size': 11.5, 'font-weight': 700, fill: colors.text,
        text: `${cursor.getFullYear()}年${cursor.getMonth() + 1}月`,
      }));
    }
    headerG.appendChild(svgEl('line', {
      x1: startX, y1: headerY, x2: startX, y2: originY + bodyH,
      stroke: colors.gridStrong, 'stroke-width': 1,
    }));
    cursor = nextMonth;
  }

  if (scale.key === 'day') {
    for (let i = 0; i < totalDays; i += 1) {
      const date = addDays(range.from, i);
      const cx = originX + i * dayWidth;
      headerG.appendChild(svgEl('text', {
        x: cx + dayWidth / 2, y: headerY + 32, 'font-size': 10, 'text-anchor': 'middle',
        fill: isWeekend(date) ? '#e14c4c' : colors.muted, text: String(date.getDate()),
      }));
      headerG.appendChild(svgEl('text', {
        x: cx + dayWidth / 2, y: headerY + 43, 'font-size': 8.5, 'text-anchor': 'middle',
        fill: isWeekend(date) ? '#e14c4c' : colors.muted, opacity: 0.8, text: weekday(date),
      }));
      gridG.appendChild(svgEl('line', {
        x1: cx, y1: originY, x2: cx, y2: originY + bodyH,
        stroke: colors.grid, 'stroke-width': 1,
      }));
    }
  } else if (scale.key === 'week') {
    for (let i = 0; i < totalDays; i += 1) {
      const date = addDays(range.from, i);
      if (date.getDay() !== 1) continue;
      const cx = originX + i * dayWidth;
      headerG.appendChild(svgEl('text', {
        x: cx + 2, y: headerY + 34, 'font-size': 9.5, fill: colors.muted,
        text: `${date.getMonth() + 1}/${date.getDate()}`,
      }));
      gridG.appendChild(svgEl('line', {
        x1: cx, y1: originY, x2: cx, y2: originY + bodyH,
        stroke: colors.grid, 'stroke-width': 1,
      }));
    }
  } else {
    for (let i = 0; i < totalDays; i += 1) {
      const date = addDays(range.from, i);
      if (date.getDate() !== 1) continue;
      gridG.appendChild(svgEl('line', {
        x1: originX + i * dayWidth, y1: originY, x2: originX + i * dayWidth, y2: originY + bodyH,
        stroke: colors.grid, 'stroke-width': 1,
      }));
    }
  }
  svg.appendChild(gridG);
  svg.appendChild(headerG);

  /* ---- rows ---- */
  /** 子タスクを持つ行は子から集計した期間なので、直接は動かさない。 */
  const canDrag = (task) => Boolean(
    interactive && editable && onEdit && !task.child_count);

  const rowsG = svgEl('g');
  const namesG = svgEl('g', { class: 'gantt-names' });
  const barGeom = new Map();

  namesG.appendChild(svgEl('rect', {
    x: PAD - 1, y: titleH + PAD, width: nameWidth + 1, height: HEADER_H + rows.length * ROW_H,
    fill: colors.bg,
  }));
  namesG.appendChild(svgEl('rect', {
    x: PAD - 1, y: titleH + PAD, width: nameWidth + 1, height: HEADER_H,
    fill: forExport ? '#f7f8fa' : 'var(--surface-2)',
  }));
  namesG.appendChild(svgEl('text', {
    x: PAD + 8, y: titleH + PAD + 28, 'font-size': 12, 'font-weight': 600, fill: colors.muted,
    text: 'タスク',
  }));
  rows.forEach((row, index) => {
    if (index % 2 === 1) {
      namesG.appendChild(svgEl('rect', {
        x: PAD, y: originY + index * ROW_H, width: nameWidth, height: ROW_H, fill: colors.band,
      }));
    }
    namesG.appendChild(svgEl('line', {
      x1: PAD, y1: originY + (index + 1) * ROW_H, x2: PAD + nameWidth,
      y2: originY + (index + 1) * ROW_H, stroke: colors.grid, 'stroke-width': 1,
    }));
  });

  rows.forEach((row, index) => {
    const { task, depth, hasChildren } = row;
    const y = originY + index * ROW_H;
    const indent = PAD + 8 + depth * 12;
    const label = truncate(task.title, Math.max(4, Math.floor((nameWidth - (indent - PAD) - 34) / 12)));

    const nameNode = svgEl('text', {
      x: indent, y: y + ROW_H / 2 + 4, 'font-size': 11.5,
      'font-weight': hasChildren ? 650 : 400,
      fill: task.status === 'done' ? colors.muted : colors.text,
      text: (task.is_milestone ? '◆ ' : '')
        + (task.blocks_open && task.status !== 'done' ? '⛔ ' : '') + label,
    });
    if (interactive) {
      nameNode.style.cursor = 'pointer';
      nameNode.appendChild(svgEl('title', { text: task.title }));
      nameNode.addEventListener('click', () => openTaskDetail(task.id));
    }
    namesG.appendChild(nameNode);

    rowsG.appendChild(svgEl('line', {
      x1: originX, y1: y + ROW_H, x2: PAD + nameWidth + chartW, y2: y + ROW_H,
      stroke: colors.grid, 'stroke-width': 1,
    }));

    const startISO = task.rollup_start || task.start_date;
    const dueISO = task.rollup_due || task.due_date;
    const start = parseDate(startISO);
    const due = parseDate(dueISO);
    const color = colorOf(task);
    const critical = Boolean(task.is_critical) && task.status !== 'done';
    const faded = task.status === 'done' && colorBy !== 'status';

    if (task.is_milestone && due) {
      const cx = x(due) + dayWidth / 2;
      const cy = y + ROW_H / 2;
      const size = 7;
      const node = svgEl('path', {
        d: `M ${cx} ${cy - size} L ${cx + size} ${cy} L ${cx} ${cy + size} L ${cx - size} ${cy} Z`,
        fill: task.status === 'done' ? STATUS_COLOR.done : (colorBy === 'status' ? '#e8912b' : color),
        stroke: critical ? '#e14c4c' : (forExport ? '#ffffff' : 'none'),
        'stroke-width': critical ? 2 : 1,
      });
      node.appendChild(svgEl('title', { text: `${task.title} — ${dueISO}` }));
      const label = svgEl('text', {
        x: cx + size + 5, y: cy + 4, 'font-size': 10.5, fill: colors.muted,
        text: truncate(task.title, 18),
      });
      const group = svgEl('g', {}, node, label);
      rowsG.appendChild(group);
      if (interactive) {
        group.style.cursor = 'pointer';
        group.addEventListener('click', () => openTaskDetail(task.id));
      }
      if (canDrag(task)) {
        attachDrag({
          group, task, dayWidth, range, scroller, onEdit, interactive,
          // マイルストーンは期限だけを持つので、移動のみ
          setGeometry: (offsetX) => {
            const px = cx + offsetX;
            node.setAttribute('d',
              `M ${px} ${cy - size} L ${px + size} ${cy} L ${px} ${cy + size} `
              + `L ${px - size} ${cy} Z`);
            label.setAttribute('x', px + size + 5);
          },
          modeAt: () => 'move',
          dates: { start: null, due },
        });
      }
      barGeom.set(task.id, { x1: cx - size, x2: cx + size, y: cy });
      return;
    }

    if (!start && !due) return;
    const barStart = start || due;
    const barEnd = due || start;
    const bx = x(barStart);
    const bw = Math.max(dayWidth * 0.8, (daysBetween(barStart, barEnd) + 1) * dayWidth - 2);
    const barH = hasChildren ? 9 : 14;
    const by = y + (ROW_H - barH) / 2;
    const progress = hasChildren ? (task.rollup_progress ?? task.progress) : task.progress;

    const group = svgEl('g', { opacity: faded ? 0.45 : 1 });
    const radius = hasChildren ? 2 : 4;
    const bgRect = svgEl('rect', {
      x: bx, y: by, width: bw, height: barH, rx: radius,
      fill: color, opacity: hasChildren ? 0.35 : 0.28,
    });
    const fillRect = progress > 0 ? svgEl('rect', {
      x: bx, y: by, width: Math.max(2, (bw * progress) / 100), height: barH,
      rx: radius, fill: color,
    }) : null;
    const outlineRect = svgEl('rect', {
      x: bx, y: by, width: bw, height: barH, rx: radius,
      fill: 'none', stroke: critical ? '#e14c4c' : color,
      'stroke-width': critical ? 2 : 1, opacity: critical ? 1 : 0.85,
    });
    group.appendChild(bgRect);
    if (fillRect) group.appendChild(fillRect);
    group.appendChild(outlineRect);
    group.appendChild(svgEl('title', {
      text: `${task.title}\n${startISO || '?'} 〜 ${dueISO || '?'}  進捗 ${progress}%`
        + (task.assignee_name ? `\n担当: ${task.assignee_name}` : '')
        + (task.blocks_open ? `\n⛔ 後続 ${task.blocks_open} 件が待機` : '')
        + (critical ? '\n🔗 クリティカルパス上' : ''),
    }));
    if (interactive) {
      group.style.cursor = 'pointer';
      group.addEventListener('click', () => openTaskDetail(task.id));
    }
    rowsG.appendChild(group);

    const labelParts = [];
    if (progress > 0 && progress < 100) labelParts.push(`${progress}%`);
    if (task.assignee_name) labelParts.push(task.assignee_name);
    let sideLabel = null;
    if (labelParts.length && bx + bw + 6 < originX + chartW) {
      sideLabel = svgEl('text', {
        x: bx + bw + 6, y: y + ROW_H / 2 + 4, 'font-size': 10, fill: colors.muted,
        text: labelParts.join(' · '),
      });
      group.appendChild(sideLabel);
    }

    if (canDrag(task)) {
      attachDrag({
        group, task, dayWidth, range, scroller, onEdit, interactive,
        setGeometry: (offsetX, widthDelta) => {
          const nx = bx + offsetX;
          const nw = Math.max(dayWidth * 0.5, bw + widthDelta);
          for (const rect of [bgRect, outlineRect]) {
            rect.setAttribute('x', nx);
            rect.setAttribute('width', nw);
          }
          if (fillRect) {
            fillRect.setAttribute('x', nx);
            fillRect.setAttribute('width', Math.max(2, (nw * progress) / 100));
          }
          if (sideLabel) sideLabel.setAttribute('x', nx + nw + 6);
        },
        // バーの端 7px だけがリサイズ。バーの外（横のラベルなど）は移動として扱う
        modeAt: (px) => {
          if (px < bx || px > bx + bw) return 'move';
          const edge = Math.min(7, bw / 3);
          if (px - bx <= edge) return 'resize-start';
          if (bx + bw - px <= edge) return 'resize-end';
          return 'move';
        },
        dates: { start, due },
        bounds: () => ({ bx, bw }),
      });
    }
    barGeom.set(task.id, { x1: bx, x2: bx + bw, y: by + barH / 2 });
  });

  /* ---- dependency arrows ---- */
  const depG = svgEl('g');
  const suffix = Math.random().toString(36).slice(2, 8);
  const arrowId = `arrow-${suffix}`;
  const arrowBadId = `arrow-bad-${suffix}`;
  svg.appendChild(svgEl('defs', {},
    svgEl('marker', {
      id: arrowId, markerWidth: 7, markerHeight: 7, refX: 6, refY: 3.5, orient: 'auto',
    }, svgEl('path', { d: 'M0,0 L7,3.5 L0,7 Z', fill: '#98a2b3' })),
    svgEl('marker', {
      id: arrowBadId, markerWidth: 7, markerHeight: 7, refX: 6, refY: 3.5, orient: 'auto',
    }, svgEl('path', { d: 'M0,0 L7,3.5 L0,7 Z', fill: '#e14c4c' }))));
  for (const dep of deps) {
    const from = barGeom.get(dep.depends_on_id);
    const to = barGeom.get(dep.task_id);
    if (!from || !to) continue;
    const bad = conflictEdges.has(`${dep.depends_on_id}->${dep.task_id}`);
    // A successor that starts before its predecessor ends needs a detour, otherwise
    // the arrow would run straight back through the bars.
    const backwards = to.x1 < from.x2 + 10;
    const path = backwards
      ? `M ${from.x2} ${from.y} h 8 V ${(from.y + to.y) / 2} H ${to.x1 - 10} `
        + `V ${to.y} H ${to.x1 - 2}`
      : `M ${from.x2} ${from.y} H ${Math.max(from.x2 + 8, to.x1 - 8)} V ${to.y} `
        + `H ${to.x1 - 2}`;
    const line = svgEl('path', {
      d: path,
      fill: 'none', stroke: bad ? '#e14c4c' : '#98a2b3',
      'stroke-width': bad ? 1.8 : 1.2, 'stroke-dasharray': bad ? null : '3 2',
      'marker-end': `url(#${bad ? arrowBadId : arrowId})`, opacity: bad ? 1 : 0.85,
    });
    if (bad) {
      line.appendChild(svgEl('title', { text: '日程が矛盾しています（先行タスクの期限が後続の開始より後）' }));
    }
    depG.appendChild(line);
  }
  svg.appendChild(depG);
  svg.appendChild(rowsG);

  /* ---- today marker ---- */
  const now = today();
  if (now >= range.from && now <= range.to) {
    const tx = x(now) + dayWidth / 2;
    svg.appendChild(svgEl('line', {
      x1: tx, y1: titleH + PAD, x2: tx, y2: originY + bodyH,
      stroke: '#e14c4c', 'stroke-width': 1.6, opacity: 0.85,
    }));
    svg.appendChild(svgEl('text', {
      x: tx, y: titleH + PAD - 4, 'font-size': 10, 'text-anchor': 'middle',
      fill: '#e14c4c', 'font-weight': 700, text: '今日',
    }));
  }

  /* ---- frame ---- */
  svg.appendChild(svgEl('rect', {
    x: PAD, y: titleH + PAD, width: nameWidth + chartW, height: HEADER_H + bodyH,
    fill: 'none', stroke: colors.gridStrong, 'stroke-width': 1,
  }));
  svg.appendChild(svgEl('line', {
    x1: originX, y1: titleH + PAD, x2: originX, y2: originY + bodyH,
    stroke: colors.gridStrong, 'stroke-width': 1,
  }));

  namesG.appendChild(svgEl('line', {
    x1: originX, y1: titleH + PAD, x2: originX, y2: originY + bodyH,
    stroke: colors.gridStrong, 'stroke-width': 1,
  }));
  svg.appendChild(namesG);

  if (rows.length === 0) {
    svg.appendChild(svgEl('text', {
      x: width / 2, y: originY + 30, 'text-anchor': 'middle', 'font-size': 13,
      fill: colors.muted, text: '表示できるタスクがありません',
    }));
  }
  return svg;
}

/** Pick a day width so the chart fills the target slide width exactly. */
function fitScale(range, targetWidth, nameWidth) {
  const totalDays = Math.max(1, daysBetween(range.from, range.to) + 1);
  const available = targetWidth - nameWidth - PAD * 2;
  const dayWidth = Math.min(40, Math.max(1.2, available / totalDays));
  const key = dayWidth >= 14 ? 'day' : dayWidth >= 5 ? 'week' : 'month';
  return { key, label: SCALES[key].label, dayWidth, minorEvery: SCALES[key].minorEvery };
}

/* ---------------------------------------------------------------- ドラッグ */

const DRAG_THRESHOLD = 4;      // これ未満の移動はクリック扱い
const AUTO_SCROLL_EDGE = 56;   // 端に近づいたら自動でスクロールする距離

/** 画面座標を SVG の座標系に変換する。 */
function svgPoint(node, event) {
  const svg = node.ownerSVGElement || node;
  const point = svg.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  return point.matrixTransform(svg.getScreenCTM().inverse());
}

function dragHint() {
  let node = document.querySelector('.gantt-drag-hint');
  if (!node) {
    node = el('div', { class: 'gantt-drag-hint' });
    document.body.appendChild(node);
  }
  return node;
}

/** ドラッグ結果の日付を求める。動かせない形になる場合は null。 */
function shiftedDates({ start, due }, mode, days) {
  const from = start || due;
  const to = due || start;
  if (mode === 'move') {
    return {
      start_date: start ? toISO(addDays(from, days)) : null,
      due_date: toISO(addDays(to, days)),
    };
  }
  if (mode === 'resize-start') {
    const next = addDays(from, days);
    if (next > to) return null;
    return { start_date: toISO(next), due_date: toISO(to) };
  }
  const next = addDays(to, days);
  if (next < from) return null;
  return { start_date: start ? toISO(from) : null, due_date: toISO(next) };
}

/**
 * バー／マイルストーンをドラッグで移動・期間変更できるようにする。
 * setGeometry(offsetX, widthDelta) で見た目だけを先に動かし、
 * 離した時点で onEdit に確定した日付を渡す。
 */
function attachDrag({ group, task, dayWidth, range, scroller, onEdit, setGeometry,
                      modeAt, dates }) {
  let drag = null;
  group.style.touchAction = 'none';
  group.style.cursor = 'grab';        // 動かせることが分かるようにしておく
  const span = daysBetween(dates.start || dates.due, dates.due || dates.start) + 1;

  const clampDays = (mode, days) => {
    if (mode === 'resize-start') return Math.min(days, span - 1);
    if (mode === 'resize-end') return Math.max(days, -(span - 1));
    return days;
  };

  const onMove = (event) => {
    const point = svgPoint(group, event);
    if (!drag) {
      group.style.cursor = modeAt(point.x) === 'move' ? 'grab' : 'ew-resize';
      return;
    }
    const dx = point.x - drag.startX;
    if (!drag.moved && Math.abs(dx) < DRAG_THRESHOLD) return;
    drag.moved = true;
    const days = clampDays(drag.mode, Math.round(dx / dayWidth));
    drag.days = days;
    const shift = days * dayWidth;
    if (drag.mode === 'move') setGeometry(shift, 0);
    else if (drag.mode === 'resize-start') setGeometry(shift, -shift);
    else setGeometry(0, shift);

    const preview = shiftedDates(dates, drag.mode, days);
    const hint = dragHint();
    hint.textContent = preview
      ? `${preview.start_date || '—'} 〜 ${preview.due_date}`
      : '動かせません';
    hint.style.left = `${event.clientX + 14}px`;
    hint.style.top = `${event.clientY - 34}px`;
    hint.hidden = false;
    autoScroll(event);
  };

  const autoScroll = (event) => {
    if (!scroller) return;
    const box = scroller.getBoundingClientRect();
    if (event.clientX > box.right - AUTO_SCROLL_EDGE) scroller.scrollLeft += 12;
    else if (event.clientX < box.left + AUTO_SCROLL_EDGE) scroller.scrollLeft -= 12;
  };

  const onUp = (event) => {
    if (!drag) return;
    const finished = drag;
    drag = null;
    group.releasePointerCapture?.(event.pointerId);
    group.style.cursor = 'grab';
    dragHint().hidden = true;
    if (!finished.moved || finished.days === 0) {
      setGeometry(0, 0);
      return;                      // 動いていなければクリックとして扱う
    }
    // ドラッグ直後のクリックで詳細が開かないように 1 回だけ握りつぶす
    group.addEventListener('click', (e) => { e.stopPropagation(); e.preventDefault(); },
      { capture: true, once: true });
    const patch = shiftedDates(dates, finished.mode, finished.days);
    if (!patch) { setGeometry(0, 0); return; }
    onEdit(task, patch);
  };

  group.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    const point = svgPoint(group, event);
    drag = { startX: point.x, mode: modeAt(point.x), moved: false, days: 0 };
    group.setPointerCapture?.(event.pointerId);
    group.style.cursor = drag.mode === 'move' ? 'grabbing' : 'ew-resize';
    event.stopPropagation();
  });
  group.addEventListener('pointermove', onMove);
  group.addEventListener('pointerup', onUp);
  group.addEventListener('pointercancel', onUp);
  group.addEventListener('pointerleave', () => {
    if (!drag) group.style.cursor = 'grab';
  });
}

function truncate(text, max) {
  const value = String(text || '');
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

/** Draw the SVG into a canvas of the requested size, centred and scaled to fit. */
function rasterize(svg, natural, size) {
  return new Promise((resolve, reject) => {
    const clone = svg.cloneNode(true);
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    const source = new XMLSerializer().serializeToString(clone);
    const url = URL.createObjectURL(new Blob([source], { type: 'image/svg+xml;charset=utf-8' }));
    const image = new Image();
    image.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width = size.w;
      canvas.height = size.h;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, size.w, size.h);
      const scale = Math.min(size.w / natural.w, size.h / natural.h);
      const drawW = natural.w * scale;
      const drawH = natural.h * scale;
      ctx.drawImage(image, (size.w - drawW) / 2, (size.h - drawH) / 2, drawW, drawH);
      URL.revokeObjectURL(url);
      canvas.toBlob((blob) => {
        if (blob) resolve(blob); else reject(new Error('画像の生成に失敗しました'));
      }, 'image/png');
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error('画像の生成に失敗しました'));
    };
    image.src = url;
  });
}
