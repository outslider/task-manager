/* Gantt chart: SVG rendering plus PowerPoint-friendly SVG / PNG export. */
import { api } from '../api.js';
import { setHeader } from '../app.js';
import {
  store, STATUS_COLOR, STATUS_LABEL, IMPORTANCE_LABEL, CATEGORIES, category, markerChar,
} from '../store.js';
import {
  addDays, daysBetween, downloadBlob, el, fill, formatSpan, isWeekend, openModal, parseDate, svgEl,
  toISO, toast, today, weekday,
} from '../util.js';
import { iconLabel } from '../icons.js';
import { buildTree, loadCollapsed, saveCollapsed } from './tasks.js';
import { openTaskDetail } from './taskDetail.js';
import { projectTabs } from './projectNav.js';
import { occurrenceTitle, openMeetingForm, openOccurrenceDialog } from './meetings.js';

const SCALES = {
  day: { key: 'day', label: '日', dayWidth: 26, minorEvery: 1 },
  week: { key: 'week', label: '週', dayWidth: 9, minorEvery: 7 },
  month: { key: 'month', label: '月', dayWidth: 3.6, minorEvery: 30 },
  quarter: { key: 'quarter', label: '四半期', dayWidth: 1.3, minorEvery: 90 },
};

const IMPORTANCE_COLOR = { 0: '#98a2b3', 1: '#3b6ef5', 2: '#e8912b', 3: '#e14c4c' };

/** ガントに置く記号。中心 (cx, cy) と大きさ r から輪郭を作る。 */
const MARKER_SHAPES = {
  '': (cx, cy, r) => `M ${cx} ${cy - r} L ${cx + r} ${cy} L ${cx} ${cy + r} L ${cx - r} ${cy} Z`,
  circle: (cx, cy, r) => {
    const k = r * 0.5523;                       // 円をベジェで近似する係数
    return `M ${cx} ${cy - r} C ${cx + k} ${cy - r} ${cx + r} ${cy - k} ${cx + r} ${cy} `
      + `C ${cx + r} ${cy + k} ${cx + k} ${cy + r} ${cx} ${cy + r} `
      + `C ${cx - k} ${cy + r} ${cx - r} ${cy + k} ${cx - r} ${cy} `
      + `C ${cx - r} ${cy - k} ${cx - k} ${cy - r} ${cx} ${cy - r} Z`;
  },
  square: (cx, cy, r) => {
    const h = r * 0.85;
    return `M ${cx - h} ${cy - h} H ${cx + h} V ${cy + h} H ${cx - h} Z`;
  },
  triangle: (cx, cy, r) => `M ${cx} ${cy - r} L ${cx + r} ${cy + r * 0.8} L ${cx - r} ${cy + r * 0.8} Z`,
  down: (cx, cy, r) => `M ${cx} ${cy + r} L ${cx + r} ${cy - r * 0.8} L ${cx - r} ${cy - r * 0.8} Z`,
  star: (cx, cy, r) => {
    const points = [];
    for (let i = 0; i < 10; i += 1) {
      const radius = i % 2 ? r * 0.45 : r;
      const angle = (Math.PI / 5) * i - Math.PI / 2;
      points.push(`${(cx + radius * Math.cos(angle)).toFixed(2)} `
        + `${(cy + radius * Math.sin(angle)).toFixed(2)}`);
    }
    return `M ${points.join(' L ')} Z`;
  },
};

/** 記号の輪郭を返す。未知の値なら既定のひし形。 */
export function markerPath(kind, cx, cy, r) {
  return (MARKER_SHAPES[kind] || MARKER_SHAPES[''])(cx, cy, r);
}

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

// 1 枚に描ける行数の上限。これを超えると描画も読み取りも重くなるので、
// 黙って遅くするのではなく「絞ってください」と伝える。
const MAX_ROWS = 600;

const ROW_H = 26;
const ROADMAP_ROW_H = 40;      // ロードマップは帯を太くして、名前をバーの中に書く
const MILESTONE_LANE_H = 44;   // 節目を並べる、チャート上部の専用レーン
const HEADER_H = 46;
const PAD = 14;
const NAME_W_DEFAULT = 250;
// 定例会議の点の色。状態の色（灰・青・緑・赤）と紛れない紫にする
const MEETING_COLOR = '#7c5cdb';
const MEETING_FOLD_KEY = 'tm.gantt.meetingsFolded';

/* Slide presets: width/height in pixels at 96dpi, matching PowerPoint slide sizes. */
const EXPORT_PRESETS = [
  { id: 'ppt169', label: 'PowerPoint 16:9 (33.87×19.05cm)', w: 1280, h: 720 },
  { id: 'ppt169x2', label: 'PowerPoint 16:9 高解像度 (2倍)', w: 2560, h: 1440 },
  { id: 'ppt43', label: 'PowerPoint 4:3 (25.4×19.05cm)', w: 1024, h: 768 },
  { id: 'ppt43x2', label: 'PowerPoint 4:3 高解像度 (2倍)', w: 2048, h: 1536 },
  { id: 'a4', label: 'A4 横 (印刷用 150dpi)', w: 1754, h: 1240 },
  { id: 'natural', label: '原寸（切り取らずそのまま）', w: 0, h: 0 },
];

/** バーの横に何を出すか。既定は今までと同じ（担当者と進捗）。 */
function loadLabels() {
  const fallback = { date: false, assignee: true, progress: true };
  try {
    return { ...fallback, ...JSON.parse(localStorage.getItem('tm.gantt.labels') || '{}') };
  } catch { return fallback; }
}

function saveLabels(labels) {
  try { localStorage.setItem('tm.gantt.labels', JSON.stringify(labels)); } catch { /* private */ }
}

const GROUPINGS = [
  { key: 'none', label: 'なし', hint: '階層のまま並べます' },
  { key: 'project', label: 'プロジェクト', hint: 'プロジェクトごとに区切ります',
    overviewOnly: true },
  { key: 'phase', label: 'フェーズ', hint: 'トップレベルのタスクごとに区切ります' },
  { key: 'assignee', label: '担当者', hint: '担当者ごとに区切り行を入れます' },
  { key: 'category', label: 'カテゴリ', hint: 'カテゴリごとに区切り行を入れます' },
];

/** 担当者の名前。全体ガントは行数が多いので、名前は id から引く。 */
function assigneeName(task) {
  return task.assignee_id ? (store.usersById.get(Number(task.assignee_id))?.name || '') : '';
}

/* 全体ガントで、はじめに出すプロジェクトの数。
   参加案件が増えても 1 枚に全部並べても読めないので、既定では動いているものに絞る。 */
const OVERVIEW_DEFAULT_MAX = 8;
const PICK_KEY = 'tm.gantt.projects';

/** 前回の選択。無ければ null（＝既定の絞り込みを使う）。 */
function loadPicked() {
  try {
    const raw = localStorage.getItem(PICK_KEY);
    if (!raw) return null;
    if (raw === 'all') return 'all';
    const ids = raw.split(',').map(Number).filter(Boolean);
    return ids.length ? new Set(ids) : null;
  } catch { return null; }
}

function savePicked(picked) {
  try {
    if (picked === 'all') localStorage.setItem(PICK_KEY, 'all');
    else if (picked) localStorage.setItem(PICK_KEY, [...picked].join(','));
    else localStorage.removeItem(PICK_KEY);
  } catch { /* プライベートウィンドウでは覚えなくてよい */ }
}

/**
 * 初めて開いたときに出すプロジェクト。
 * 未完了の仕事が残っているものを、遅れの多い順に。多すぎるときは頭だけ。
 */
function defaultPicked(projects) {
  const live = projects.filter((p) => !p.archived);
  if (live.length <= OVERVIEW_DEFAULT_MAX) return null;   // 少なければ全部でよい
  const moving = live.filter((p) => (p.stats?.total || 0) > (p.stats?.done || 0));
  const ordered = (moving.length ? moving : live).sort((a, b) =>
    (b.stats?.overdue || 0) - (a.stats?.overdue || 0)
    || ((b.stats?.total || 0) - (b.stats?.done || 0))
       - ((a.stats?.total || 0) - (a.stats?.done || 0)));
  return new Set(ordered.slice(0, OVERVIEW_DEFAULT_MAX).map((p) => p.id));
}

export async function render(container, route) {
  const projectId = route.projectId || null;
  const overview = !projectId;               // プロジェクトを横断して見るモード
  const allProjects = overview ? store.projects.filter((p) => !p.archived) : [];
  let picked = overview ? (loadPicked() ?? defaultPicked(allProjects)) : null;
  // 空の集合は「全部」と区別できず、問い合わせ側では全件扱いになってしまう。
  // 1 件も選ばれていないときは、絞り込み無しとして扱う。
  const pickedIds = () => {
    if (!picked || picked === 'all' || !picked.size) return null;
    return [...picked];
  };
  const fetchData = async () => {
    if (!overview) return api.projectTasks(projectId);
    const ids = pickedIds();
    return api.get(`/api/gantt${ids ? `?project_ids=${ids.join(',')}` : ''}`);
  };
  let data = await fetchData();
  const project = overview
    ? { id: null, name: '全プロジェクト' }
    : data.project;
  /** 日付 (ISO) → 祝日名。ガントの網掛けに使う。 */
  const holidayMap = new Map();
  let holidayRange = null;

  const compact = window.innerWidth < 760;
  // 俯瞰では、どれか 1 つでも編集できれば日程を動かせる（保存時に権限は再判定される）
  const canEdit = overview
    ? (data.projects || []).some((p) => ['owner', 'editor'].includes(p.my_role))
    : store.canEdit(project);
  const collapsed = loadCollapsed(projectId || 'all');   // タスク一覧と共有する
  const collapsedGroups = new Set();
  const state = {
    mode: localStorage.getItem('tm.gantt.mode') || 'gantt',   // gantt | roadmap
    group: overview
      ? (localStorage.getItem('tm.gantt.groupAll') || 'project')
      : (localStorage.getItem('tm.gantt.group') || 'phase'),
    showMarks: localStorage.getItem('tm.gantt.marks') !== '0',
    labels: loadLabels(),
    scale: localStorage.getItem('tm.gantt.scale') || (compact ? 'week' : 'day'),
    colorBy: localStorage.getItem('tm.gantt.colorBy') || 'status',
    showDone: true,
    onlyMine: false,
    nameWidth: compact ? 140 : NAME_W_DEFAULT,
    fromISO: null,
    toISO: null,
  };

  const syncHeader = () => setHeader(
    `${project.name}${overview ? '' : ' —'} ${state.mode === 'roadmap' ? 'ロードマップ' : 'ガント'}`,
    [
      canEdit && !overview
        ? el('button', { class: 'btn', onClick: () => addTask() }, ...iconLabel('plus', 'タスク'))
        : null,
      canEdit && !overview
        ? el('button', {
          class: 'btn', title: '週次定例などを 1 行にまとめて、開催日を点で並べます',
          onClick: () => editMeeting(null),
        }, ...iconLabel('calendar', '定例を追加'))
        : null,
      el('button', { class: 'btn btn-primary', onClick: () => exportDialog() }, ...iconLabel('download', 'エクスポート')),
    ]);
  syncHeader();

  /** ガント画面からタスクを足す。期間を渡すとその日程で開く。 */
  async function addTask(dates = null) {
    const { openTaskForm } = await import('./taskForm.js');
    const saved = await openTaskForm({
      project, tasks: data.tasks, deps: data.deps, members: data.members,
      preset: dates,
    });
    if (!saved) return;
    await refresh();
    toast(`${saved.title} を追加しました`, 'ok');
  }

  /* ---- 定例会議 ----
   * 開催日は見ている期間のぶんだけサーバーで数えてもらう（祝日と例外を反映するため）。
   * 祝日と同じく、期間の前後に余裕を持たせて取り、足りなくなったら取り直す。 */
  let meetingCache = { list: [], from: null, to: null, key: null };
  try {
    if (localStorage.getItem(MEETING_FOLD_KEY) === '1') collapsedGroups.add('meetings');
  } catch { /* 覚えられなくても開いた状態で出すだけ */ }

  const meetingProjectIds = () => (overview
    ? (data.projects || []).map((p) => p.id)
    : [projectId]);

  async function ensureMeetings(range) {
    const ids = meetingProjectIds();
    const key = ids.join(',');
    if (!ids.length) {
      const had = meetingCache.list.length > 0;
      meetingCache = { list: [], from: null, to: null, key };
      return had;
    }
    const from = toISO(addDays(range.from, -40));
    const to = toISO(addDays(range.to, 40));
    if (meetingCache.key === key && meetingCache.from && meetingCache.from <= from
      && meetingCache.to >= to) return false;
    try {
      const result = await api.get(`/api/meetings?project_ids=${key}&from=${from}&to=${to}`);
      meetingCache = { list: result.meetings || [], from, to, key };
      return true;
    } catch {
      return false;                 // 定例が取れなくても本体は描画する
    }
  }

  /** 定例を変えたら、数え直してもらう。 */
  async function reloadMeetings() {
    meetingCache = { ...meetingCache, key: null };
    draw();
  }

  async function editMeeting(meeting) {
    const saved = await openMeetingForm({ project, meeting, tasks: data.tasks });
    if (saved) await reloadMeetings();
  }

  async function editOccurrence(meeting, occurrence) {
    if (!meeting.can_edit) return;
    if (await openOccurrenceDialog(meeting, occurrence)) await reloadMeetings();
  }

  /* 置き場所（親タスク）を決めた定例は、そのタスクの子として並べる。
   * ただし親タスクが行として出ない表示（担当者別の区切り、ロードマップで
   * フェーズより下のタスクなど）では置き場所が無いので、先頭の「定例」に回す。 */
  let meetingsUnder = new Map();

  /** いまの表示で、行として出うるタスク（たたまれて隠れているものも含む）。 */
  function nestTargets(tasks) {
    if (['assignee', 'category'].includes(state.group) && state.mode !== 'roadmap') {
      return new Set();
    }
    if (state.mode === 'roadmap') {
      // 行になるのはフェーズ（トップレベルのタスク）だけ
      const ids = new Set(tasks.map((t) => t.id));
      return new Set(tasks.filter((t) => !ids.has(t.parent_id) && !t.is_milestone)
        .map((t) => t.id));
    }
    return new Set(tasks.map((t) => t.id));
  }

  /** 定例を「タスクの下に置くもの」と「先頭にまとめるもの」に分ける。 */
  function placeMeetings(tasks) {
    const targets = nestTargets(tasks);
    meetingsUnder = new Map();
    const top = [];
    for (const meeting of meetingCache.list) {
      if (meeting.parent_id && targets.has(meeting.parent_id)) {
        if (!meetingsUnder.has(meeting.parent_id)) meetingsUnder.set(meeting.parent_id, []);
        meetingsUnder.get(meeting.parent_id).push(meeting);
      } else {
        top.push(meeting);
      }
    }
    return top;
  }

  /** タスクの直下に並べる定例の行。 */
  function meetingRowsUnder(taskId, depth) {
    return (meetingsUnder.get(taskId) || []).map((meeting) => ({
      meeting, depth, inGroup: true, color: MEETING_COLOR, projectName: '',
    }));
  }

  /** ガントの先頭に置く「定例」のかたまり。1 つの会議が 1 行。 */
  function meetingRows(list) {
    if (!list.length) return [];
    const projects = new Map((data.projects || []).map((p) => [p.id, p]));
    const folded = collapsedGroups.has('meetings');
    const rows = [{
      group: true, groupId: 'meetings', label: '定例', color: MEETING_COLOR,
      count: list.length, collapsed: folded,
    }];
    if (folded) return rows;
    for (const meeting of list) {
      const owner = projects.get(meeting.project_id);
      rows.push({
        meeting, inGroup: true, depth: 1,
        // 全体ガントでは、どの案件の会議かを点の色で分ける
        color: overview ? (owner?.color || MEETING_COLOR) : MEETING_COLOR,
        projectName: overview ? (owner?.name || '') : '',
      });
    }
    return rows;
  }

  const scroll = el('div', { class: 'gantt-scroll' });

  const modeSeg = el('div', { class: 'seg' },
    ...[
      { key: 'gantt', label: 'ガント', hint: '1タスク1行の詳細表示' },
      { key: 'roadmap', label: 'ロードマップ', hint: 'フェーズ単位でまとめ、節目を上に並べます' },
    ].map((mode) => el('button', {
      class: state.mode === mode.key ? 'active' : '',
      title: mode.hint,
      onClick: (event) => {
        state.mode = mode.key;
        localStorage.setItem('tm.gantt.mode', mode.key);
        [...event.currentTarget.parentNode.children].forEach((b) => b.classList.remove('active'));
        event.currentTarget.classList.add('active');
        applyModeDefaults();
        syncHeader();
        draw();
      },
    }, mode.label)));

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

  const groupSelect = el('select', {
    class: 'select', style: { maxWidth: '120px' },
    onChange: (event) => {
      state.group = event.target.value;
      localStorage.setItem(overview ? 'tm.gantt.groupAll' : 'tm.gantt.group', state.group);
      const meetingsFolded = collapsedGroups.has('meetings');
      collapsedGroups.clear();
      if (meetingsFolded) collapsedGroups.add('meetings');
      draw();
    },
  }, ...GROUPINGS.filter((g) => overview || !g.overviewOnly).map((g) => el('option', {
    value: g.key, title: g.hint, selected: state.group === g.key ? true : null,
  }, g.label)));

  /** バーの横に出す項目のオン/オフ。 */
  const labelToggle = (key, text) => el('label', { class: 'check' },
    el('input', {
      type: 'checkbox', checked: state.labels[key] ? true : null,
      onChange: (event) => {
        state.labels = { ...state.labels, [key]: event.target.checked };
        saveLabels(state.labels);
        draw();
      },
    }), el('span', { text }));

  const fromInput = el('input', {
    class: 'input', type: 'date', style: { maxWidth: '150px' },
    onChange: (event) => { state.fromISO = event.target.value || null; draw(); },
  });
  const toInput = el('input', {
    class: 'input', type: 'date', style: { maxWidth: '150px' },
    onChange: (event) => { state.toISO = event.target.value || null; draw(); },
  });

  /** どのプロジェクトを並べるかを選ぶ。全体ガントだけに出す。 */
  const pickerButton = overview ? el('button', { class: 'btn btn-sm' }) : null;
  const syncPickerLabel = () => {
    if (!pickerButton) return;
    const shown = pickedIds() ? pickedIds().length : allProjects.length;
    fill(pickerButton, ...iconLabel('folder',
      `プロジェクト ${shown}/${allProjects.length}`));
    pickerButton.title = pickedIds()
      ? '表示するプロジェクトを選びます（いまは絞り込み中）'
      : '表示するプロジェクトを選びます';
  };
  if (pickerButton) {
    pickerButton.addEventListener('click', () => openProjectPicker());
    syncPickerLabel();
  }

  async function openProjectPicker() {
    const { openModal } = await import('../util.js');
    const current = pickedIds() ? new Set(pickedIds()) : new Set(allProjects.map((p) => p.id));
    const boxes = new Map();
    const chosen = await openModal({
      title: '並べるプロジェクト',
      wide: true,
      build: () => el('div', {},
        el('p', { class: 'page-sub',
          text: '1 枚に並べるプロジェクトを選べます（次回も覚えています）。'
            + `いまは ${allProjects.length} 件のうち `
            + `${pickedIds() ? pickedIds().length : allProjects.length} 件を並べています。`
            + (allProjects.length > OVERVIEW_DEFAULT_MAX
              ? '数が多いので、初めは動いているものだけに絞っています。' : '') }),
        el('div', { class: 'toolbar', style: { marginBottom: '8px' } },
          el('button', {
            class: 'btn btn-sm',
            onClick: () => boxes.forEach((b) => { b.checked = true; }),
          }, 'すべて選ぶ'),
          el('button', {
            class: 'btn btn-sm',
            onClick: () => boxes.forEach((b) => { b.checked = false; }),
          }, 'すべて外す')),
        el('div', { class: 'pick-grid' }, ...allProjects.map((p) => {
          const box = el('input', { type: 'checkbox', checked: current.has(p.id) ? true : null });
          boxes.set(p.id, box);
          const open = (p.stats?.total || 0) - (p.stats?.done || 0);
          return el('label', { class: 'check pick-row' }, box,
            el('span', { class: 'nav-dot', style: { background: p.color } }),
            el('span', { class: 'pick-name', text: p.name }),
            el('span', { class: 'hint', text: open ? `未完了 ${open}` : '完了' }));
        }))),
      footer: (close) => [
        el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
        el('button', {
          class: 'btn btn-primary',
          onClick: () => {
            const ids = [...boxes].filter(([, b]) => b.checked).map(([id]) => id);
            if (!ids.length) {
              toast('1 つ以上選んでください', 'error');
              return;
            }
            close(ids);
          },
        }, 'この並びで見る'),
      ],
    });
    if (!chosen) return;
    picked = chosen.length >= allProjects.length ? 'all' : new Set(chosen);
    savePicked(picked);
    syncPickerLabel();
    await refresh();
  }

  const pickNotice = el('div', { class: 'warn-box soft', hidden: true });
  const syncPickNotice = () => {
    const ids = pickedIds();
    pickNotice.hidden = !ids || !overview;
    if (pickNotice.hidden) return;
    fill(pickNotice,
      el('span', { text: `${allProjects.length} 件のうち ${ids.length} 件だけを並べています。` }),
      el('button', {
        class: 'btn btn-sm', style: { marginLeft: '10px' },
        onClick: async () => {
          picked = 'all';
          savePicked(picked);
          syncPickerLabel();
          await refresh();
        },
      }, 'すべて並べる'));
  };

  const toolbar = el('div', { class: 'toolbar' },
    pickerButton,
    el('span', { class: 'label', style: { margin: 0 }, text: '表示' }), modeSeg,
    el('span', { class: 'label', style: { margin: '0 0 0 6px' }, text: '表示単位' }), scaleSeg,
    el('span', { class: 'label', style: { margin: '0 0 0 6px' }, text: '色分け' }), colorSelect,
    el('span', { class: 'label', style: { margin: '0 0 0 6px' }, text: '区切り' }), groupSelect,
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
    el('label', { class: 'check', title: '折りたたんだ親の行に、各回を記号で並べます' },
      el('input', {
        type: 'checkbox', checked: state.showMarks ? true : null,
        onChange: (event) => {
          state.showMarks = event.target.checked;
          localStorage.setItem('tm.gantt.marks', state.showMarks ? '1' : '0');
          draw();
        },
      }), el('span', { text: 'たたんだ行に各回' })),
    el('span', { class: 'label', style: { margin: '0 0 0 6px' }, text: 'バーの横' }),
    labelToggle('date', '日付'),
    labelToggle('assignee', '担当者'),
    labelToggle('progress', '進捗'),
    el('div', { class: 'spacer' }),
    el('span', { class: 'hint', style: { marginRight: '4px' }, id: 'gantt-hint' }),
    el('button', {
      class: 'btn btn-sm', title: 'すべて展開',
      onClick: () => {
        collapsed.clear();
        collapsedGroups.clear();
        saveCollapsed(projectId, collapsed);
        saveMeetingFold();
        draw();
      },
    }, '⤢ 展開'),
    el('button', {
      class: 'btn btn-sm', title: '子タスクをすべて折りたたむ',
      onClick: () => {
        for (const task of data.tasks) {
          if (data.tasks.some((child) => child.parent_id === task.id)) collapsed.add(task.id);
        }
        for (const meeting of meetingCache.list) {
          if (meeting.parent_id) collapsed.add(meeting.parent_id);
        }
        for (const row of visibleRows()) if (row.group) collapsedGroups.add(row.groupId);
        saveCollapsed(projectId, collapsed);
        saveMeetingFold();
        draw();
      },
    }, '⤡ 折りたたみ'),
    el('button', {
      class: 'btn btn-sm',
      onClick: () => { state.fromISO = null; state.toISO = null; syncRangeInputs(); draw(); },
    }, '期間リセット'));

  const legend = el('div', { class: 'gantt-legend' });
  const rowNotice = el('div', {});

  /** 行数が上限を超えたことを伝える。 */
  function drawRowNotice(total) {
    if (total <= MAX_ROWS) { fill(rowNotice); return; }
    fill(rowNotice, el('div', { class: 'filter-notice' },
      el('span', { text: `表示は ${MAX_ROWS} 行までです（該当 ${total} 行）。`
        + 'プロジェクトや親タスクをたたむか、絞り込んでください。' })));
  }

  function drawLegend() {
    const hint = document.getElementById('gantt-hint');
    if (hint) {
      hint.textContent = state.mode === 'roadmap'
        ? 'フェーズ（トップレベルのタスク）と節目だけを並べています'
        : (canEdit
          ? 'バーをドラッグで移動、端をドラッグで期間変更。▼ で折りたたみ'
          : '▼ をクリックすると折りたためます');
    }
    fill(legend,
      ...COLOR_MODES[state.colorBy].legend().map((entry) => el('span', { class: 'legend-item' },
        el('span', { class: 'legend-swatch', style: { background: entry.color } }),
        el('span', { text: entry.label }))),
      el('span', { class: 'legend-item' }, el('span', { text: '◆ マイルストーン' })),
      meetingCache.list.length
        ? el('span', { class: 'legend-item', title: '点を押すと、その回を中止・日にち変更できます' },
          el('span', { style: { color: MEETING_COLOR }, text: '●' }),
          el('span', { text: '定例（○ 中止・◌ 日にち変更）' }))
        : null,
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
      overview ? overviewHead() : projectTabs(projectId, 'gantt'),
      toolbar, pickNotice, rowNotice, scroll, legend));

  /** 俯瞰のときの見出し。どのプロジェクトが対象かを示す。 */
  function overviewHead() {
    const names = (data.projects || []).map((p) => p.name);
    return el('div', { class: 'proj-tabs' },
      el('span', { class: 'hint', style: { padding: '8px 12px' },
        text: names.length
          ? `${names.length} プロジェクトを並べています: ${names.join(' / ')}`
          : '表示できるプロジェクトがありません' }));
  }

  function filteredTasks() {
    return data.tasks.filter((task) => {
      if (!state.showDone && task.status === 'done') return false;
      if (state.onlyMine && task.assignee_id !== store.user.id) return false;
      return true;
    });
  }

  function visibleRows() {
    const meetingsPart = meetingRows(placeMeetings(filteredTasks()));
    const tasksPart = taskRows();
    if (meetingsPart.length && tasksPart.length) {
      tasksPart[0] = { ...tasksPart[0], separator: true };
    }
    return [...meetingsPart, ...tasksPart];
  }

  function taskRows() {
    const tasks = filteredTasks();
    const { children } = buildTree(tasks);
    if (state.mode === 'roadmap') {
      // 複数プロジェクトを 1 枚にすると、節目を上に 1 本にまとめても
      // どれがどの案件のものか分からない。案件ごとに区切って、
      // その見出しの行に自分の節目を並べる。
      if (overview) return roadmapByProject(tasks);
      // フェーズ＝トップレベルのタスク。節目は上のレーンにまとめるので行にはしない
      return (children.get(null) || [])
        .filter((task) => !task.is_milestone)
        .flatMap((task) => [{
          task, depth: 0, hasChildren: (children.get(task.id) || []).length > 0,
        }, ...meetingRowsUnder(task.id, 1)]);
    }
    if (['assignee', 'category', 'project'].includes(state.group)) {
      return groupedRows(tasks);
    }

    // 階層のまま。折りたたんだ親の下は出さない
    const rows = [];
    const walk = (parentId, depth) => {
      for (const task of children.get(parentId) || []) {
        const kids = children.get(task.id) || [];
        const folded = collapsed.has(task.id);
        const under = meetingsUnder.get(task.id) || [];
        rows.push({
          task, depth, hasChildren: kids.length > 0, hasMeetings: under.length > 0,
          collapsed: folded,
          // たたんだ親の行に、隠れている各回を記号で並べる（週次の打ち合わせなど）
          marks: folded && state.showMarks ? occurrenceDates(task.id) : null,
          // フェーズ区切り: トップレベルの行の上に太い線を引く
          separator: state.group === 'phase' && depth === 0 && rows.length > 0,
          lead: state.group === 'phase' && depth === 0,
        });
        if (!folded) {
          rows.push(...meetingRowsUnder(task.id, depth + 1));
          walk(task.id, depth + 1);
        }
      }
    };
    walk(null, 0);
    return rows;
  }

  /** 担当者・カテゴリ・プロジェクトごとに区切り行を挟んで並べる。 */
  /**
   * 全体ガントのロードマップ。案件ごとに見出しを置き、その行に節目を並べ、
   * 下にフェーズを続ける。節目はその案件のものだけ（階層のどこにあっても拾う）。
   */
  function roadmapByProject(tasks) {
    const projects = new Map((data.projects || []).map((p) => [p.id, p]));
    const buckets = new Map();
    for (const task of tasks) {
      if (!buckets.has(task.project_id)) buckets.set(task.project_id, []);
      buckets.get(task.project_id).push(task);
    }
    const order = [...buckets.keys()].sort((a, b) =>
      String(projects.get(a)?.name || '').localeCompare(String(projects.get(b)?.name || ''), 'ja'));
    const rows = [];
    for (const pid of order) {
      const items = buckets.get(pid);
      const { children } = buildTree(items);
      const phases = (children.get(null) || [])
        .filter((task) => !task.is_milestone)
        .sort((a, b) => (a.sort_order - b.sort_order) || (a.id - b.id));
      const marks = items.filter((task) => task.is_milestone && task.due_date);
      const groupId = `roadmap:${pid}`;
      const folded = collapsedGroups.has(groupId);
      rows.push({
        group: true, groupId, label: projects.get(pid)?.name || '（不明なプロジェクト）',
        color: projects.get(pid)?.color || '#98a2b3',
        count: phases.length, collapsed: folded, separator: rows.length > 0,
        milestones: marks,
      });
      if (folded) continue;
      for (const task of phases) {
        rows.push({
          task, depth: 0, hasChildren: (children.get(task.id) || []).length > 0, inGroup: true,
        }, ...meetingRowsUnder(task.id, 1));
      }
    }
    return rows;
  }

  function groupedRows(tasks) {
    const mode = state.group;
    const keyOf = (task) => {
      if (mode === 'assignee') return task.assignee_id ? String(task.assignee_id) : '';
      if (mode === 'project') return String(task.project_id);
      return task.category || '';
    };
    const buckets = new Map();
    for (const task of tasks) {
      const key = keyOf(task);
      if (!buckets.has(key)) buckets.set(key, []);
      buckets.get(key).push(task);
    }
    const projects = new Map((data.projects || []).map((p) => [String(p.id), p]));
    const label = (key) => {
      if (mode === 'assignee') return key ? (store.userName(Number(key)) || '不明') : '未割当';
      if (mode === 'project') return projects.get(key)?.name || '（不明なプロジェクト）';
      return key ? category(key).label : '未分類';
    };
    const tint = (key) => {
      if (mode === 'assignee') return store.usersById.get(Number(key))?.avatar_color || '#98a2b3';
      if (mode === 'project') return projects.get(key)?.color || '#98a2b3';
      return key ? category(key).color : '#98a2b3';
    };
    // 中身の多い順。未割当・未分類は最後に回す
    const keys = [...buckets.keys()].sort((a, b) => {
      if (!a !== !b) return a ? -1 : 1;
      return buckets.get(b).length - buckets.get(a).length;
    });
    const rows = [];
    for (const key of keys) {
      const items = buckets.get(key)
        .sort((a, b) => (a.sort_order - b.sort_order) || (a.id - b.id));
      const groupId = `${mode}:${key}`;
      const folded = collapsedGroups.has(groupId);
      rows.push({
        group: true, groupId, label: label(key), color: tint(key),
        count: items.length, collapsed: folded, separator: rows.length > 0,
      });
      if (folded) continue;
      if (mode === 'project') {
        // プロジェクト単位なら、その中の親子関係はそのまま見せる
        rows.push(...hierarchyRows(items, 1));
      } else {
        for (const task of items) {
          rows.push({ task, depth: 0, hasChildren: false, inGroup: true });
        }
      }
    }
    return rows;
  }

  /** タスクの集合を、親子の順に並べた行にする。 */
  function hierarchyRows(tasks, baseDepth) {
    const { children } = buildTree(tasks);
    const out = [];
    const walk = (parentId, depth) => {
      for (const task of children.get(parentId) || []) {
        const kids = children.get(task.id) || [];
        const folded = collapsed.has(task.id);
        const under = meetingsUnder.get(task.id) || [];
        out.push({
          task, depth, hasChildren: kids.length > 0, hasMeetings: under.length > 0,
          collapsed: folded, inGroup: true,
          marks: folded && state.showMarks ? occurrenceDates(task.id) : null,
        });
        if (!folded) {
          out.push(...meetingRowsUnder(task.id, depth + 1));
          walk(task.id, depth + 1);
        }
      }
    };
    walk(null, baseDepth - baseDepth);
    return out.map((row) => ({ ...row, depth: row.depth + baseDepth }));
  }

  function toggleRow(row) {
    if (row.group) {
      if (collapsedGroups.has(row.groupId)) collapsedGroups.delete(row.groupId);
      else collapsedGroups.add(row.groupId);
      if (row.groupId === 'meetings') saveMeetingFold();
    } else {
      if (collapsed.has(row.task.id)) collapsed.delete(row.task.id);
      else collapsed.add(row.task.id);
      saveCollapsed(projectId, collapsed);
    }
    draw();
  }

  function saveMeetingFold() {
    try {
      localStorage.setItem(MEETING_FOLD_KEY, collapsedGroups.has('meetings') ? '1' : '0');
    } catch { /* private */ }
  }

  /** そのタスクの配下にある「回」の期限。たたんだ行に並べる印に使う。 */
  function occurrenceDates(taskId) {
    const byParent = new Map();
    for (const task of data.tasks) {
      if (!byParent.has(task.parent_id)) byParent.set(task.parent_id, []);
      byParent.get(task.parent_id).push(task);
    }
    const out = [];
    const walk = (id) => {
      for (const child of byParent.get(id) || []) {
        const kids = byParent.get(child.id) || [];
        if (kids.length) walk(child.id);
        else if (child.due_date) {
          out.push({ id: child.id, title: child.title, due: child.due_date,
            done: child.status === 'done' });
        }
      }
    };
    walk(taskId);
    // 数が多すぎると潰れるので、表示は 60 件までに抑える
    return out.sort((a, b) => a.due.localeCompare(b.due)).slice(0, 60);
  }

  /** ロードマップの上部レーンに並べる節目。階層のどこにあっても拾う。
   *  全体ガントでは案件ごとの見出し行に並べるので、上のレーンは使わない。 */
  function milestoneRows() {
    if (state.mode !== 'roadmap' || overview) return [];
    return filteredTasks().filter((task) => task.is_milestone && task.due_date);
  }

  /** 期間の計算には、どこに並べる節目でも含める。 */
  function allMilestones() {
    if (state.mode !== 'roadmap') return [];
    return filteredTasks().filter((task) => task.is_milestone && task.due_date);
  }

  /** ロードマップは全体像を一目で見るための図なので、画面幅いっぱいに広げる。 */
  function roadmapScale(range) {
    const totalDays = Math.max(1, daysBetween(range.from, range.to) + 1);
    const available = (scroll.clientWidth || 900) - state.nameWidth - PAD * 2 - 4;
    return {
      ...SCALES.quarter,
      dayWidth: Math.min(12, Math.max(SCALES.quarter.dayWidth, available / totalDays)),
    };
  }

  /** モードを切り替えたときに、見やすい表示単位へ寄せる。 */
  function applyModeDefaults() {
    const wanted = state.mode === 'roadmap' ? 'quarter' : (compact ? 'week' : 'day');
    if (state.scale === wanted) return;
    state.scale = wanted;
    localStorage.setItem('tm.gantt.scale', wanted);
    [...scaleSeg.children].forEach((button) =>
      button.classList.toggle('active', button.textContent === SCALES[wanted].label));
  }

  function dateRange(rows, milestones = []) {
    let min = null;
    let max = null;
    const entries = [...rows, ...milestones.map((task) => ({ task }))]
      .filter((row) => row.task);          // 区切り行にはタスクが無い
    for (const { task } of entries) {
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

  /** 表示中の期間の祝日をまとめて取り、足りなければ引き直す。 */
  async function ensureHolidays(range) {
    const from = toISO(addDays(range.from, -40));
    const to = toISO(addDays(range.to, 40));
    if (holidayRange && holidayRange.from <= from && holidayRange.to >= to) return false;
    try {
      const data = await api.holidays({ from, to });
      holidayMap.clear();
      for (const item of data.holidays || []) holidayMap.set(item.day, item.name);
      holidayRange = { from, to };
      return true;
    } catch {
      return false;                 // 祝日が取れなくても本体は描画する
    }
  }

  // 俯瞰は件数が多くなりがちなので、初めて開いたときは親をたたんでおく。
  // 全部広げると数千行になり、描画も読み取りも重くなるため。
  if (overview && !collapsed.size) {
    const hasChild = new Set(data.tasks.map((t) => t.parent_id).filter(Boolean));
    for (const task of data.tasks) {
      if (hasChild.has(task.id)) collapsed.add(task.id);
    }
    saveCollapsed('all', collapsed);
  }

  let scrolledToToday = false;

  /** サーバーから読み直して引き直す。横位置は draw() が持ち越す。 */
  async function refresh() {
    try {
      data = await fetchData();
    } catch (error) {
      toast(error.message, 'error');
      return;
    }
    draw();
  }

  /** 詳細ドロワーを開く。中で変更されたら、そのつどチャートを引き直す。 */
  function openTask(taskId) {
    openTaskDetail(taskId, { onChange: refresh });
  }

  function draw() {
    syncPickNotice();
    const all = visibleRows();
    const rows = all.length > MAX_ROWS ? all.slice(0, MAX_ROWS) : all;
    drawRowNotice(all.length);
    const milestones = milestoneRows();
    // 期間は、案件ごとの行に並べる節目も含めて決める
    const range = dateRange(rows, allMilestones());
    ensureHolidays(range).then((changed) => { if (changed) draw(); });
    ensureMeetings(range).then((changed) => { if (changed) draw(); });
    const roadmap = state.mode === 'roadmap';
    syncRangeInputs(range);
    const svg = buildGanttSvg({
      rows, range, scale: roadmap ? roadmapScale(range) : SCALES[state.scale], deps: data.deps,
      conflicts: data.conflicts, colorBy: state.colorBy,
      title: project.name, nameWidth: state.nameWidth, interactive: true,
      // ロードマップは見せるための図なので、ドラッグ編集はガント表示だけにする
      editable: canEdit && !roadmap, onEdit: applyEdit, scroller: scroll,
      roadmap, milestones, holidays: holidayMap, labels: state.labels,
      onCreate: canEdit && !overview ? addTask : null, onOpenTask: openTask,
      onToggleRow: toggleRow,
      // 定例の名前を押すと編集、点を押すとその回の中止・日にち変更
      onMeeting: overview ? null : (meeting) => { if (meeting.can_edit) editMeeting(meeting); },
      onOccurrence: editOccurrence,
    });
    drawLegend();
    // 描き直すと横位置が先頭に戻ってしまうので、見ていた位置を持ち越す。
    const keepLeft = scroll.scrollLeft;
    fill(scroll, svg);
    const mark = svg.querySelector('.gantt-today');
    if (!scrolledToToday && mark && scroll.clientWidth > 0) {
      // 最初に開いたときは今日が見える位置から。過去も少し見せたいので左寄りに置く。
      scroll.scrollLeft = Math.max(0,
        Number(mark.getAttribute('x1')) - Math.max(240, scroll.clientWidth * 0.35));
      scrolledToToday = true;
    } else {
      scroll.scrollLeft = keepLeft;
    }
    const names = svg.querySelector('.gantt-names');
    if (names) {
      const stick = () => names.setAttribute('transform', `translate(${scroll.scrollLeft},0)`);
      scroll.onscroll = stick;
      stick();
    }
  }

  /** バーをドラッグして確定した日程を保存する。 */
  async function applyEdit(task, patch) {
    try {
      await api.patch(`/api/tasks/${task.id}`, patch);
      await refresh();
      toast(`${task.title}: ${formatSpan(patch.start_date, patch.due_date, { sep: ' 〜 ' })}`, 'ok');
    } catch (error) {
      toast(error.message, 'error');
      await refresh();                    // 画面を実際の値に戻す
    }
  }

  async function exportDialog() {
    const rows = visibleRows();
    const milestones = milestoneRows();
    const range = dateRange(rows, allMilestones());
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
          text: state.mode === 'roadmap'
            ? `ロードマップ表示: フェーズ ${rows.length} 件 / 節目 ${milestones.length} 件`
              + ` / 期間 ${formatSpan(toISO(range.from), toISO(range.to), { sep: ' 〜 ' })}`
            : `対象タスク ${rows.filter((r) => r.task).length} 件`
              + ` / 期間 ${formatSpan(toISO(range.from), toISO(range.to), { sep: ' 〜 ' })}` })),
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
    const roadmap = state.mode === 'roadmap';
    const scale = (fitWidth && target.w)
      ? fitScale(range, target.w, NAME_W_DEFAULT, roadmap)
      : SCALES[state.scale];
    const svg = buildGanttSvg({
      rows, range, scale, deps: data.deps,
      conflicts: data.conflicts, colorBy: state.colorBy,
      title: withTitle ? project.name : null,
      subtitle: withTitle
        ? `${formatSpan(toISO(range.from), toISO(range.to), { sep: ' 〜 ' })}　作成日: ${toISO(today())}`
        : null,
      nameWidth: NAME_W_DEFAULT, forExport: true,
      roadmap, milestones: milestoneRows(), holidays: holidayMap, labels: state.labels,
    });
    const stamp = toISO(today());
    const kind = roadmap ? 'ロードマップ' : 'ガント';
    const base = `${project.name}_${kind}_${stamp}`.replace(/[\\/:*?"<>|]/g, '_');
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
  roadmap = false, milestones = [], holidays = null, onCreate = null, onOpenTask = null,
  labels = null, onToggleRow = null, onMeeting = null, onOccurrence = null,
}) {
  const colorOf = (COLOR_MODES[colorBy] || COLOR_MODES.status).color;
  // 呼び出し側が渡してくれば、閉じたときにチャートを引き直せる
  const openTask = onOpenTask || ((id) => openTaskDetail(id));
  const show = { date: false, assignee: true, progress: true, ...(labels || {}) };
  const conflictEdges = new Set(
    conflicts.map((c) => `${c.depends_on_id}->${c.task_id}`));
  const totalDays = Math.max(1, daysBetween(range.from, range.to) + 1);
  const dayWidth = scale.dayWidth;
  const chartW = Math.round(totalDays * dayWidth);
  const titleH = title ? 42 : 0;
  const rowH = roadmap ? ROADMAP_ROW_H : ROW_H;
  const laneH = roadmap && milestones.length ? MILESTONE_LANE_H : 0;
  // 一番下に「ドラッグして追加」の空き行を1つ置く
  const ghostRows = interactive && editable && onCreate && !roadmap ? 1 : 0;
  const totalRows = rows.length + ghostRows;
  // 「タスクがありません」の一文を入れるぶんの余白
  const noticeH = rows.length === 0 ? 28 : 0;
  const width = nameWidth + chartW + PAD * 2;
  const height = titleH + HEADER_H + laneH + totalRows * rowH + noticeH + PAD * 2 + 6;

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
  const originY = titleH + PAD + HEADER_H + laneH;
  const x = (date) => originX + daysBetween(range.from, date) * dayWidth;

  /* ---- background bands and weekend shading ---- */
  const bands = svgEl('g');
  rows.forEach((row, index) => {
    const y = originY + index * rowH;
    if (row.group) {
      // 区切り行は帯で塗り、担当者やカテゴリの色を左端に置く
      bands.appendChild(svgEl('rect', {
        x: PAD, y, width: nameWidth + chartW, height: rowH,
        fill: forExport ? '#eef1f6' : 'var(--surface-3)',
      }));
      bands.appendChild(svgEl('rect', {
        x: PAD, y, width: 4, height: rowH, fill: row.color || colors.gridStrong,
      }));
    } else if (row.lead) {
      bands.appendChild(svgEl('rect', {
        x: PAD, y, width: nameWidth + chartW, height: rowH,
        fill: forExport ? '#f4f6f9' : 'var(--surface-2)',
      }));
    } else if (index % 2 === 1) {
      bands.appendChild(svgEl('rect', {
        x: PAD, y, width: nameWidth + chartW, height: rowH, fill: colors.band,
      }));
    }
  });
  svg.appendChild(bands);

  const gridG = svgEl('g');
  const bodyH = totalRows * rowH;
  const holidayName = (date) => (holidays ? holidays.get(toISO(date)) : null) || null;
  if (scale.key === 'day') {
    for (let i = 0; i < totalDays; i += 1) {
      const date = addDays(range.from, i);
      const name = holidayName(date);
      if (isWeekend(date) || name) {
        const cell = svgEl('rect', {
          x: originX + i * dayWidth, y: originY, width: dayWidth, height: bodyH,
          fill: colors.weekend, opacity: forExport ? 1 : 0.7,
        });
        if (name) cell.appendChild(svgEl('title', { text: name }));
        gridG.appendChild(cell);
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
  if (scale.key === 'quarter') {
    // ロードマップ向け: 上段に年、下段に四半期
    let cursor = new Date(range.from.getFullYear(), Math.floor(range.from.getMonth() / 3) * 3, 1);
    while (cursor <= range.to) {
      const next = new Date(cursor.getFullYear(), cursor.getMonth() + 3, 1);
      const startX = Math.max(originX, x(cursor));
      const endX = Math.min(originX + chartW, x(next));
      const quarter = Math.floor(cursor.getMonth() / 3) + 1;
      const yearStart = quarter === 1 || startX === originX;
      if (endX - startX > 22) {
        headerG.appendChild(svgEl('text', {
          x: (startX + endX) / 2, y: headerY + 33, 'font-size': 12, 'font-weight': 700,
          'text-anchor': 'middle', fill: colors.text, text: `Q${quarter}`,
        }));
      }
      if (yearStart && endX - startX > 16) {
        headerG.appendChild(svgEl('text', {
          x: startX + 5, y: headerY + 15, 'font-size': 12, 'font-weight': 700,
          fill: colors.text, text: `${cursor.getFullYear()}年`,
        }));
      }
      headerG.appendChild(svgEl('line', {
        x1: startX, y1: headerY, x2: startX, y2: originY + bodyH,
        stroke: colors.gridStrong, 'stroke-width': quarter === 1 ? 1.6 : 1,
      }));
      cursor = next;
    }
  } else {
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
  }

  if (scale.key === 'day') {
    for (let i = 0; i < totalDays; i += 1) {
      const date = addDays(range.from, i);
      const cx = originX + i * dayWidth;
      const off = isWeekend(date) || Boolean(holidayName(date));
      const dayText = svgEl('text', {
        x: cx + dayWidth / 2, y: headerY + 32, 'font-size': 10, 'text-anchor': 'middle',
        fill: off ? '#e14c4c' : colors.muted, text: String(date.getDate()),
      });
      if (holidayName(date)) dayText.appendChild(svgEl('title', { text: holidayName(date) }));
      headerG.appendChild(dayText);
      headerG.appendChild(svgEl('text', {
        x: cx + dayWidth / 2, y: headerY + 43, 'font-size': 8.5, 'text-anchor': 'middle',
        fill: off ? '#e14c4c' : colors.muted, opacity: 0.8,
        text: holidayName(date) ? '祝' : weekday(date),
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
  } else if (scale.key === 'month') {
    for (let i = 0; i < totalDays; i += 1) {
      const date = addDays(range.from, i);
      if (date.getDate() !== 1) continue;
      gridG.appendChild(svgEl('line', {
        x1: originX + i * dayWidth, y1: originY, x2: originX + i * dayWidth, y2: originY + bodyH,
        stroke: colors.grid, 'stroke-width': 1,
      }));
    }
  } else {
    // 四半期: 月の区切りは薄く、四半期の区切りはヘッダー側で濃く引く
    for (let i = 0; i < totalDays; i += 1) {
      const date = addDays(range.from, i);
      if (date.getDate() !== 1 || date.getMonth() % 3 === 0) continue;
      gridG.appendChild(svgEl('line', {
        x1: originX + i * dayWidth, y1: originY, x2: originX + i * dayWidth, y2: originY + bodyH,
        stroke: colors.grid, 'stroke-width': 1, opacity: 0.6,
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

  // 横スクロールすると名前列は右へずれる。左の余白ぶんも塗っておかないと、
  // その隙間から後ろのバーが覗いてしまう。
  namesG.appendChild(svgEl('rect', {
    x: 0, y: titleH + PAD, width: PAD + nameWidth + 1,
    height: HEADER_H + laneH + totalRows * rowH, fill: colors.bg,
  }));
  namesG.appendChild(svgEl('rect', {
    x: 0, y: titleH + PAD, width: PAD + nameWidth + 1, height: HEADER_H,
    fill: forExport ? '#f7f8fa' : 'var(--surface-2)',
  }));
  namesG.appendChild(svgEl('text', {
    x: PAD + 8, y: titleH + PAD + 28, 'font-size': 12, 'font-weight': 600, fill: colors.muted,
    text: roadmap ? 'フェーズ' : 'タスク',
  }));
  if (laneH) {
    namesG.appendChild(svgEl('text', {
      x: PAD + 8, y: titleH + PAD + HEADER_H + laneH / 2 + 4, 'font-size': 11,
      'font-weight': 600, fill: colors.muted, text: '◆ マイルストーン',
    }));
  }
  rows.forEach((row, index) => {
    const y = originY + index * rowH;
    if (row.group) {
      namesG.appendChild(svgEl('rect', {
        x: PAD, y, width: nameWidth, height: rowH,
        fill: forExport ? '#eef1f6' : 'var(--surface-3)',
      }));
      namesG.appendChild(svgEl('rect', {
        x: PAD, y, width: 4, height: rowH, fill: row.color || colors.gridStrong,
      }));
    } else if (row.lead) {
      namesG.appendChild(svgEl('rect', {
        x: PAD, y, width: nameWidth, height: rowH,
        fill: forExport ? '#f4f6f9' : 'var(--surface-2)',
      }));
    } else if (index % 2 === 1) {
      namesG.appendChild(svgEl('rect', {
        x: PAD, y, width: nameWidth, height: rowH, fill: colors.band,
      }));
    }
    namesG.appendChild(svgEl('line', {
      x1: PAD, y1: originY + (index + 1) * rowH, x2: PAD + nameWidth,
      y2: originY + (index + 1) * rowH, stroke: colors.grid, 'stroke-width': 1,
    }));
  });

  /**
   * 節目の菱形を 1 行のなかに並べる。案件ごとのロードマップで使う。
   * 上部の一本レーンと違い、行の高さに収めるため札は 1 段だけ。
   */
  function milestoneMarks(items, rowTop, height) {
    const g = svgEl('g');
    const sorted = [...items]
      .filter((m) => parseDate(m.due_date))
      .sort((a, b) => String(a.due_date).localeCompare(String(b.due_date)));
    const cy = rowTop + Math.min(15, height / 2 - 4);
    let lastX = -Infinity;
    for (const task of sorted) {
      const due = parseDate(task.due_date);
      const cx = x(due) + dayWidth / 2;
      const done = task.status === 'done';
      const pin = svgEl('path', {
        d: markerPath(task.marker, cx, cy, 6),
        fill: done ? STATUS_COLOR.done : '#e8912b',
        stroke: forExport ? '#ffffff' : 'none', 'stroke-width': 1,
      });
      pin.appendChild(svgEl('title', {
        text: `${task.title} — ${toISO(due)}${done ? '（完了）' : ''}`,
      }));
      const node = svgEl('g', {}, pin);
      // 隣と近すぎるときは札を出さない。菱形は残るので、詳しくは吹き出しで見る
      if (cx - lastX >= 74) {
        node.appendChild(svgEl('text', {
          x: cx, y: rowTop + height - 7, 'font-size': 9.5, 'text-anchor': 'middle',
          fill: done ? colors.muted : colors.text, 'font-weight': done ? 400 : 600,
          text: truncate(task.title, 12),
        }));
        lastX = cx;
      }
      if (interactive) {
        node.style.cursor = 'pointer';
        node.addEventListener('click', () => openTask(task.id));
      }
      g.appendChild(node);
    }
    return g;
  }

  /**
   * 定例会議の 1 行。開催日に丸を打ち、最初と最後の回を細い線で結ぶ。
   * 済んだ回は薄く、中止は白抜きに斜線、日にちを変えた回は外側に輪を付ける。
   */
  function drawMeetingRow(row, y) {
    const { meeting, color } = row;
    // 全体ガントでは、どの案件の会議かを名前の前の色で示す
    const indent = PAD + 8 + (row.depth || 0) * 12 + (row.projectName ? 12 : 0);
    if (row.projectName) {
      namesG.appendChild(svgEl('circle', {
        cx: indent - 8, cy: y + rowH / 2, r: 3.5, fill: color,
      }));
    }
    const room = nameWidth - (indent - PAD) - 12;
    const summary = meeting.summary || '';
    // 説明（毎週 火 10:00）は、名前を詰めすぎない範囲で右に添える
    const summaryChars = room > 150 ? Math.min(summary.length, Math.floor((room - 60) / 10)) : 0;
    const nameChars = Math.max(4, Math.floor((room - summaryChars * 10 - 8) / 12));
    const nameNode = svgEl('text', {
      x: indent, y: y + rowH / 2 + 4, 'font-size': 11.5, fill: colors.text,
      text: truncate(meeting.title, nameChars),
    });
    nameNode.appendChild(svgEl('title', {
      text: `${row.projectName ? `${row.projectName} / ` : ''}${meeting.title}\n${summary}`
        + (interactive && meeting.can_edit && onMeeting ? '\n（押すと決まりを編集）' : ''),
    }));
    namesG.appendChild(nameNode);
    if (summaryChars) {
      namesG.appendChild(svgEl('text', {
        x: PAD + nameWidth - 8, y: y + rowH / 2 + 4, 'font-size': 10, 'text-anchor': 'end',
        fill: colors.muted, text: truncate(summary, summaryChars),
      }));
    }
    if (interactive && onMeeting && meeting.can_edit) {
      nameNode.style.cursor = 'pointer';
      nameNode.addEventListener('click', () => onMeeting(meeting));
    }

    rowsG.appendChild(svgEl('line', {
      x1: originX, y1: y + rowH, x2: PAD + nameWidth + chartW, y2: y + rowH,
      stroke: colors.grid, 'stroke-width': 1,
    }));

    const cy = y + rowH / 2;
    const r = Math.max(3, Math.min(5.5, dayWidth * 0.42));
    const shown = (meeting.occurrences || []).filter((o) => {
      const date = parseDate(o.date);
      return date && date >= range.from && date <= range.to;
    });
    if (!shown.length) return;
    const g = svgEl('g', { class: 'gantt-meeting' });
    const live = shown.filter((o) => o.status !== 'cancelled');
    if (live.length > 1) {
      g.appendChild(svgEl('line', {
        x1: x(parseDate(live[0].date)) + dayWidth / 2, y1: cy,
        x2: x(parseDate(live[live.length - 1].date)) + dayWidth / 2, y2: cy,
        stroke: color, 'stroke-width': 1.5, opacity: 0.28,
      }));
    }
    const todayDate = today();
    for (const occurrence of shown) {
      const date = parseDate(occurrence.date);
      const cx = x(date) + dayWidth / 2;
      const past = date < todayDate;
      const node = svgEl('g', { opacity: past ? 0.45 : 1 });
      if (occurrence.status === 'cancelled') {
        node.appendChild(svgEl('circle', {
          cx, cy, r, fill: forExport ? '#ffffff' : 'var(--surface)',
          stroke: colors.muted, 'stroke-width': 1.3,
        }));
        node.appendChild(svgEl('line', {
          x1: cx - r * 0.7, y1: cy + r * 0.7, x2: cx + r * 0.7, y2: cy - r * 0.7,
          stroke: colors.muted, 'stroke-width': 1.3,
        }));
      } else {
        node.appendChild(svgEl('circle', {
          cx, cy, r, fill: color,
          stroke: forExport ? '#ffffff' : 'var(--surface)', 'stroke-width': 1,
        }));
        if (occurrence.status === 'moved') {
          node.appendChild(svgEl('circle', {
            cx, cy, r: r + 2.6, fill: 'none', stroke: color, 'stroke-width': 1.2,
            'stroke-dasharray': '2 1.6',
          }));
        }
      }
      // 小さな丸だけだと押しにくいので、当たり判定を広げておく
      const hit = svgEl('rect', {
        x: cx - Math.max(r + 3, dayWidth / 2), y: y + 2,
        width: Math.max((r + 3) * 2, dayWidth), height: rowH - 4, fill: 'transparent',
      });
      node.appendChild(hit);
      node.appendChild(svgEl('title', {
        text: occurrenceTitle(meeting, occurrence, row.projectName)
          + (interactive && meeting.can_edit && onOccurrence ? '\n（押すと中止・日にち変更）' : ''),
      }));
      if (interactive && onOccurrence && meeting.can_edit) {
        node.style.cursor = 'pointer';
        node.addEventListener('click', () => onOccurrence(meeting, occurrence));
      }
      g.appendChild(node);
    }
    rowsG.appendChild(g);
  }

  rows.forEach((row, index) => {
    const y = originY + index * rowH;

    // 区切りの太線（フェーズの頭、グループの頭）
    if (row.separator) {
      rowsG.appendChild(svgEl('line', {
        x1: PAD, y1: y, x2: PAD + nameWidth + chartW, y2: y,
        stroke: colors.gridStrong, 'stroke-width': 1.6,
      }));
    }

    if (row.group) {
      const twisty = svgEl('text', {
        x: PAD + 12, y: y + rowH / 2 + 4, 'font-size': 9, fill: colors.muted,
        text: row.collapsed ? '▶' : '▼',
      });
      const heading = svgEl('text', {
        x: PAD + 26, y: y + rowH / 2 + 4, 'font-size': 11.5, 'font-weight': 700,
        fill: colors.text, text: truncate(row.label, Math.floor((nameWidth - 60) / 12)),
      });
      heading.appendChild(svgEl('title', { text: row.label }));
      const count = svgEl('text', {
        x: PAD + nameWidth - 8, y: y + rowH / 2 + 4, 'font-size': 10,
        'text-anchor': 'end', fill: colors.muted,
        text: row.milestones?.length
          ? `${row.count}件 ◆${row.milestones.length}` : `${row.count}件`,
      });
      const hit = svgEl('rect', {
        x: PAD, y, width: nameWidth, height: rowH, fill: 'transparent',
      });
      namesG.appendChild(svgEl('g', {}, hit, twisty, heading, count));
      if (interactive && onToggleRow) {
        hit.style.cursor = 'pointer';
        hit.appendChild(svgEl('title', { text: '開く / 閉じる' }));
        hit.addEventListener('click', () => onToggleRow(row));
      }
      rowsG.appendChild(svgEl('line', {
        x1: originX, y1: y + rowH, x2: PAD + nameWidth + chartW, y2: y + rowH,
        stroke: colors.grid, 'stroke-width': 1,
      }));
      // 案件ごとのロードマップでは、その案件の節目をこの見出しの行に並べる
      if (row.milestones?.length && !row.collapsed) {
        rowsG.appendChild(milestoneMarks(row.milestones, y, rowH));
      }
      return;
    }

    if (row.meeting) {
      drawMeetingRow(row, y);
      return;
    }

    const { task, depth, hasChildren } = row;
    // 子タスクが無くても、下に定例を置いていればたためる
    const foldable = hasChildren || row.hasMeetings;
    const indent = PAD + 8 + depth * 12 + (foldable ? 12 : 0);
    const label = truncate(task.title, Math.max(4, Math.floor((nameWidth - (indent - PAD) - 34) / 12)));

    // 子を持つ行には開閉の三角を出す
    if (foldable && !roadmap) {
      const twisty = svgEl('text', {
        x: PAD + 8 + depth * 12, y: y + rowH / 2 + 4, 'font-size': 9,
        fill: colors.muted, text: row.collapsed ? '▶' : '▼',
      });
      if (interactive && onToggleRow) {
        twisty.style.cursor = 'pointer';
        twisty.appendChild(svgEl('title', { text: '子タスクを開く / 閉じる' }));
        twisty.addEventListener('click', (event) => {
          event.stopPropagation();
          onToggleRow(row);
        });
      }
      namesG.appendChild(twisty);
    }

    const nameNode = svgEl('text', {
      x: indent, y: y + rowH / 2 + 4, 'font-size': 11.5,
      'font-weight': hasChildren || row.lead ? 650 : 400,
      fill: task.status === 'done' ? colors.muted : colors.text,
      text: (task.is_milestone ? `${markerChar(task)} ` : '')
        + (task.blocks_open && task.status !== 'done' ? '⛔ ' : '') + label
        + (row.collapsed && task.child_count ? ` (${task.child_count})` : ''),
    });
    if (interactive) {
      nameNode.style.cursor = 'pointer';
      nameNode.appendChild(svgEl('title', { text: task.title }));
      nameNode.addEventListener('click', () => openTask(task.id));
    }
    namesG.appendChild(nameNode);

    rowsG.appendChild(svgEl('line', {
      x1: originX, y1: y + rowH, x2: PAD + nameWidth + chartW, y2: y + rowH,
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
      const cy = y + rowH / 2;
      const size = 7;
      const node = svgEl('path', {
        d: markerPath(task.marker, cx, cy, size),
        fill: task.status === 'done' ? STATUS_COLOR.done : (colorBy === 'status' ? '#e8912b' : color),
        stroke: critical ? '#e14c4c' : (forExport ? '#ffffff' : 'none'),
        'stroke-width': critical ? 2 : 1,
      });
      node.appendChild(svgEl('title', { text: `${task.title} — ${dueISO}` }));
      const label = svgEl('text', {
        x: cx + size + 5, y: cy + 4, 'font-size': 10.5, fill: colors.muted,
        text: truncate(task.title, 18) + (show.date && dueISO ? ` (${shortDate(dueISO)})` : ''),
      });
      const group = svgEl('g', {}, node, label);
      rowsG.appendChild(group);
      if (interactive) {
        group.style.cursor = 'pointer';
        group.addEventListener('click', () => openTask(task.id));
      }
      if (canDrag(task)) {
        attachDrag({
          group, task, dayWidth, range, scroller, onEdit, interactive,
          // マイルストーンは期限だけを持つので、移動のみ
          setGeometry: (offsetX) => {
            const px = cx + offsetX;
            node.setAttribute('d', markerPath(task.marker, px, cy, size));
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
    const progressValue = hasChildren ? (task.rollup_progress ?? task.progress) : task.progress;

    // たたんだ親の行を「├◇──◇──◇──┤」の形で描く
    if (row.marks && row.marks.length) {
      const cy = y + rowH / 2;
      const x1 = x(barStartOf(start, due));
      const x2 = x(barEndOf(start, due)) + dayWidth;
      const line = svgEl('g', { opacity: faded ? 0.45 : 1 });
      line.appendChild(svgEl('line', {
        x1, y1: cy, x2, y2: cy, stroke: color, 'stroke-width': 2, opacity: 0.55,
      }));
      for (const cap of [x1, x2]) {
        line.appendChild(svgEl('line', {
          x1: cap, y1: cy - 6, x2: cap, y2: cy + 6, stroke: color, 'stroke-width': 2,
        }));
      }
      for (const mark of row.marks) {
        const date = parseDate(mark.due);
        if (!date || date < range.from || date > range.to) continue;
        const mx = x(date) + dayWidth / 2;
        const node = svgEl('path', {
          d: markerPath(task.marker, mx, cy, 5.5),
          fill: mark.done ? STATUS_COLOR.done : color,
          stroke: forExport ? '#ffffff' : 'var(--surface)', 'stroke-width': 1,
        });
        node.appendChild(svgEl('title', {
          text: `${mark.title} — ${mark.due}${mark.done ? '（完了）' : ''}`,
        }));
        if (interactive) {
          node.style.cursor = 'pointer';
          node.addEventListener('click', (event) => {
            event.stopPropagation();
            openTask(mark.id);
          });
        }
        line.appendChild(node);
      }
      line.appendChild(svgEl('title', {
        text: `${task.title}\n${row.marks.length} 回（${row.marks.filter((m) => m.done).length} 回完了）`,
      }));
      rowsG.appendChild(line);
      if (show.progress) {
        rowsG.appendChild(svgEl('text', {
          x: x2 + 8, y: cy + 4, 'font-size': 10, fill: colors.muted,
          text: `${row.marks.filter((m) => m.done).length}/${row.marks.length}`,
        }));
      }
      barGeom.set(task.id, { x1, x2, y: cy });
      return;
    }

    const barH = roadmap ? 22 : (hasChildren ? 9 : 14);
    const by = y + (rowH - barH) / 2;
    const progress = progressValue;

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
      text: `${task.title}\n${formatSpan(startISO, dueISO, { sep: ' 〜 ' })}  進捗 ${progress}%`
        + (assigneeName(task) ? `\n担当: ${assigneeName(task)}` : '')
        + (task.blocks_open ? `\n⛔ 後続 ${task.blocks_open} 件が待機` : '')
        + (critical ? '\n🔗 クリティカルパス上' : ''),
    }));
    if (interactive) {
      group.style.cursor = 'pointer';
      group.addEventListener('click', () => openTask(task.id));
    }
    rowsG.appendChild(group);

    if (roadmap) {
      // バーの中に名前を入れる。狭いときは外に出し、右端では左に逃がす
      const inside = bw > 70;
      const rightRoom = originX + chartW - (bx + bw) - 8;
      const outsideRight = !inside && rightRoom > 60;
      const anchor = inside || outsideRight ? 'start' : 'end';
      const labelX = inside ? bx + 8 : (outsideRight ? bx + bw + 6 : bx - 6);
      const room = inside ? bw - 16 : (outsideRight ? rightRoom : bx - originX - 8);
      const textColor = inside
        ? (progress >= 55 ? '#ffffff' : colors.text)
        : colors.text;
      const barLabel = svgEl('text', {
        x: labelX, y: by + barH / 2 + 4, 'text-anchor': anchor,
        'font-size': 11.5, 'font-weight': 600, fill: textColor,
        text: truncate(task.title, Math.max(3, Math.floor(room / 12))),
      });
      barLabel.appendChild(svgEl('title', { text: task.title }));
      group.appendChild(barLabel);
      if (show.progress && progress > 0 && bw > 110) {
        group.appendChild(svgEl('text', {
          x: bx + bw - 8, y: by + barH / 2 + 4, 'font-size': 10.5, 'text-anchor': 'end',
          fill: inside && progress >= 95 ? '#ffffff' : colors.muted, text: `${progress}%`,
        }));
      }
      barGeom.set(task.id, { x1: bx, x2: bx + bw, y: by + barH / 2 });
      return;
    }

    const labelParts = [];
    if (show.date && (startISO || dueISO)) {
      labelParts.push(formatSpan(startISO, dueISO, { format: shortDate }));
    }
    if (show.progress && progress > 0 && progress < 100) labelParts.push(`${progress}%`);
    if (show.assignee && assigneeName(task)) labelParts.push(assigneeName(task));
    let sideLabel = null;
    if (labelParts.length && bx + bw + 6 < originX + chartW) {
      sideLabel = svgEl('text', {
        x: bx + bw + 6, y: y + rowH / 2 + 4, 'font-size': 10, fill: colors.muted,
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

  /* ---- 一番下の「ドラッグして追加」行 ---- */
  if (ghostRows) {
    const y = originY + rows.length * rowH;
    const laneRect = svgEl('rect', {
      x: originX, y, width: chartW, height: rowH,
      fill: 'transparent', style: 'cursor: crosshair',
    });
    laneRect.appendChild(svgEl('title', {
      text: 'ドラッグすると、その期間で新しいタスクを追加できます',
    }));
    namesG.appendChild(svgEl('text', {
      x: PAD + 8, y: y + rowH / 2 + 4, 'font-size': 11.5, fill: colors.muted,
      text: '＋ 新しいタスク',
    }));
    namesG.appendChild(svgEl('rect', {
      x: PAD, y, width: nameWidth, height: rowH,
      fill: 'transparent', style: 'cursor: pointer',
    })).addEventListener('click', () => onCreate({}));

    const preview = svgEl('rect', {
      y: y + (rowH - 14) / 2, height: 14, rx: 4,
      fill: 'var(--accent)', opacity: 0.35, stroke: 'var(--accent)', 'stroke-width': 1,
      visibility: 'hidden',
    });
    const hint = svgEl('text', {
      y: y + rowH / 2 + 4, 'font-size': 10.5, fill: colors.text, visibility: 'hidden',
    });
    const group = svgEl('g', {}, laneRect, preview, hint);
    rowsG.appendChild(group);

    const dayAt = (px) => Math.max(0, Math.min(totalDays - 1,
      Math.floor((px - originX) / dayWidth)));
    let anchor = null;

    const paint = (from, to) => {
      const [a, b] = from <= to ? [from, to] : [to, from];
      preview.setAttribute('x', originX + a * dayWidth + 1);
      preview.setAttribute('width', Math.max(2, (b - a + 1) * dayWidth - 2));
      preview.setAttribute('visibility', 'visible');
      hint.setAttribute('x', originX + a * dayWidth + 4);
      hint.setAttribute('visibility', 'visible');
      hint.textContent = formatSpan(toISO(addDays(range.from, a)), toISO(addDays(range.from, b)),
        { sep: ' 〜 ' });
    };
    const clear = () => {
      preview.setAttribute('visibility', 'hidden');
      hint.setAttribute('visibility', 'hidden');
    };

    laneRect.addEventListener('pointerdown', (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      anchor = dayAt(svgPoint(laneRect, event).x);
      paint(anchor, anchor);
      laneRect.setPointerCapture(event.pointerId);
    });
    laneRect.addEventListener('pointermove', (event) => {
      if (anchor === null) return;
      paint(anchor, dayAt(svgPoint(laneRect, event).x));
    });
    const finish = (event) => {
      if (anchor === null) return;
      const end = dayAt(svgPoint(laneRect, event).x);
      const [a, b] = anchor <= end ? [anchor, end] : [end, anchor];
      anchor = null;
      clear();
      onCreate({
        start_date: toISO(addDays(range.from, a)),
        due_date: toISO(addDays(range.from, b)),
      });
    };
    laneRect.addEventListener('pointerup', finish);
    laneRect.addEventListener('pointercancel', () => { anchor = null; clear(); });
  }

  /* ---- マイルストーンのレーン（ロードマップ表示） ---- */
  if (laneH) {
    const laneY = titleH + PAD + HEADER_H;
    const laneG = svgEl('g');
    laneG.appendChild(svgEl('rect', {
      x: PAD, y: laneY, width: nameWidth + chartW, height: laneH,
      fill: forExport ? '#fbfcfd' : 'var(--surface-2)',
    }));
    laneG.appendChild(svgEl('line', {
      x1: PAD, y1: laneY + laneH, x2: PAD + nameWidth + chartW, y2: laneY + laneH,
      stroke: colors.gridStrong, 'stroke-width': 1,
    }));
    const sorted = [...milestones]
      .filter((m) => parseDate(m.due_date || m.rollup_due))
      .sort((a, b) => String(a.due_date).localeCompare(String(b.due_date)));
    let lastX = -Infinity;
    let level = 0;
    for (const task of sorted) {
      const due = parseDate(task.due_date || task.rollup_due);
      const cx = x(due) + dayWidth / 2;
      // 近すぎるラベルは上下に振り分けて重ならないようにする
      level = cx - lastX < 90 ? 1 - level : 0;
      lastX = cx;
      const cy = laneY + 15;
      const size = 6.5;
      const done = task.status === 'done';
      const pin = svgEl('path', {
        d: markerPath(task.marker, cx, cy, size),
        fill: done ? STATUS_COLOR.done : '#e8912b',
        stroke: forExport ? '#ffffff' : 'none', 'stroke-width': 1,
      });
      pin.appendChild(svgEl('title', {
        text: `${task.title} — ${toISO(due)}${done ? '（完了）' : ''}`,
      }));
      const group = svgEl('g', {}, pin,
        svgEl('line', {
          x1: cx, y1: cy + size, x2: cx, y2: originY + bodyH,
          stroke: done ? STATUS_COLOR.done : '#e8912b',
          'stroke-width': 1, 'stroke-dasharray': '3 3', opacity: 0.45,
        }),
        svgEl('text', {
          x: cx, y: cy + (level ? 30 : 19), 'font-size': 10, 'text-anchor': 'middle',
          fill: done ? colors.muted : colors.text,
          'font-weight': done ? 400 : 600,
          text: truncate(task.title, 14),
        }),
        svgEl('text', {
          x: cx, y: cy + (level ? 39 : 28), 'font-size': 8.5, 'text-anchor': 'middle',
          fill: colors.muted, text: `${due.getMonth() + 1}/${due.getDate()}`,
        }));
      if (interactive) {
        group.style.cursor = 'pointer';
        group.addEventListener('click', () => openTask(task.id));
      }
      laneG.appendChild(group);
    }
    svg.appendChild(laneG);
  }

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
  for (const dep of roadmap ? [] : deps) {
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
      class: 'gantt-today', x1: tx, y1: titleH + PAD, x2: tx, y2: originY + bodyH,
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
      x: width / 2, y: originY + totalRows * rowH + 20, 'text-anchor': 'middle',
      'font-size': 13,
      fill: colors.muted,
      text: ghostRows
        ? '表示できるタスクがありません（上の行をドラッグすると追加できます）'
        : '表示できるタスクがありません',
    }));
  }
  return svg;
}

const barStartOf = (start, due) => start || due;
const barEndOf = (start, due) => due || start;

/** バーの横に添える「9/14」形式の日付。 */
function shortDate(iso) {
  if (!iso) return '—';
  const [, month, day] = String(iso).slice(0, 10).split('-');
  return `${Number(month)}/${Number(day)}`;
}

/** Pick a day width so the chart fills the target slide width exactly. */
function fitScale(range, targetWidth, nameWidth, roadmap = false) {
  const totalDays = Math.max(1, daysBetween(range.from, range.to) + 1);
  const available = targetWidth - nameWidth - PAD * 2;
  const dayWidth = Math.min(40, Math.max(1.2, available / totalDays));
  // ロードマップは目盛りが細かいと読みにくいので、四半期のまま幅だけ広げる
  const key = roadmap ? 'quarter'
    : dayWidth >= 14 ? 'day' : dayWidth >= 5 ? 'week' : 'month';
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
      ? formatSpan(preview.start_date, preview.due_date, { sep: ' 〜 ' })
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
