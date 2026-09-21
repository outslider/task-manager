/* Cross-project task list with filters. */
import { api } from '../api.js';
import { setHeader } from '../app.js';
import { store, STATUS_LABEL, CATEGORIES } from '../store.js';
import {
  addDays, avatar, clear, debounce, dueClass, dueLabel, el, fill, formatDate,
  parseDate, toISO, today,
} from '../util.js';
import { openTaskDetail } from './taskDetail.js';
import { categoryChip } from './pickers.js';

export async function render(container) {
  const state = {
    scope: 'mine', status: 'open', q: '', project_id: '', category: '',
    overdue: false, milestone: false, blocked: false,
    week: null,          // 山グラフで選んだ週（その週だけ表示）
  };

  setHeader('マイタスク');

  // 「自分の担当」で見ているあいだ、担当者の列は全部自分になるので出さない
  const listHost = el('div', { class: 'mytask-list' });
  const syncScopeClass = () => {
    listHost.classList.toggle('no-assignee', state.scope === 'mine');
  };
  const summary = el('div', { class: 'page-sub' });
  const timeline = el('div', { class: 'card', style: { marginBottom: '14px' } });

  const search = el('input', {
    class: 'input', type: 'search', placeholder: 'キーワード検索…',
    style: { maxWidth: '220px' },
    onInput: debounce((event) => { state.q = event.target.value.trim(); load(); }, 250),
  });
  const scopeSeg = el('div', { class: 'seg' },
    segButton('自分の担当', 'mine'), segButton('すべて', 'all'));
  const statusSelect = el('select', {
    class: 'select', style: { maxWidth: '150px' },
    onChange: (event) => { state.status = event.target.value; load(); },
  },
  el('option', { value: 'open' }, '未完了のみ'),
  el('option', { value: '' }, 'すべての状態'),
  ...Object.entries(STATUS_LABEL).map(([value, label]) => el('option', { value }, label)));
  const projectSelect = el('select', {
    class: 'select', style: { maxWidth: '180px' },
    onChange: (event) => { state.project_id = event.target.value; load(); },
  },
  el('option', { value: '' }, 'すべてのプロジェクト'),
  ...store.projects.filter((p) => !p.archived).map((p) => el('option', { value: p.id }, p.name)));
  const categoryFilter = el('select', {
    class: 'select', style: { maxWidth: '170px' },
    onChange: (event) => { state.category = event.target.value; load(); },
  },
  el('option', { value: '' }, 'カテゴリ: すべて'),
  ...CATEGORIES.map((c) => el('option', { value: c.value }, `${c.icon} ${c.label}`)));
  const blockedCheck = el('label', { class: 'check' },
    el('input', {
      type: 'checkbox',
      onChange: (event) => { state.blocked = event.target.checked; load(); },
    }), el('span', { text: '先行待ちのみ' }));
  const overdueCheck = el('label', { class: 'check' },
    el('input', {
      type: 'checkbox',
      onChange: (event) => { state.overdue = event.target.checked; load(); },
    }), el('span', { text: '期限超過のみ' }));
  const milestoneCheck = el('label', { class: 'check' },
    el('input', {
      type: 'checkbox',
      onChange: (event) => { state.milestone = event.target.checked; load(); },
    }), el('span', { text: 'マイルストーンのみ' }));

  function segButton(label, value) {
    return el('button', {
      class: state.scope === value ? 'active' : '',
      onClick: (event) => {
        state.scope = value;
        [...event.currentTarget.parentNode.children].forEach((b) => b.classList.remove('active'));
        event.currentTarget.classList.add('active');
        syncScopeClass();
        load();
      },
    }, label);
  }

  fill(container,
    el('div', { class: 'page-head' }, el('div', { class: 'grow' }, summary)),
    timeline,
    el('div', { class: 'card' },
      el('div', { class: 'toolbar' },
        scopeSeg, search, statusSelect, projectSelect, categoryFilter,
        overdueCheck, blockedCheck, milestoneCheck),
      el('div', { class: 'card-body tight' }, listHost)));

  async function load() {
    fill(listHost, el('div', { class: 'empty', text: '読み込み中…' }));
    const data = await api.tasks({
      scope: state.scope, status: state.status, q: state.q,
      project_id: state.project_id,
      category: state.category,
      blocked: state.blocked ? 1 : '',
      overdue: state.overdue ? 1 : '',
      milestone: state.milestone ? 1 : '',
      limit: 500,
    });
    const tasks = data.tasks;
    drawTimeline(tasks);
    const shown = state.week
      ? tasks.filter((t) => weekKeyOf(t) === state.week)
      : tasks;
    summary.textContent = state.week
      ? `${shown.length} 件（${formatDate(state.week)} の週）／全 ${tasks.length} 件`
      : `${tasks.length} 件`;
    // 件数が上限で切れていることを黙って隠さない
    if (data.truncated) {
      summary.textContent += `（該当 ${data.matched} 件のうち先頭ぶん。絞り込んでください）`;
    }
    clear(listHost);
    if (shown.length === 0) {
      listHost.append(el('div', { class: 'empty' },
        el('div', { class: 'big', text: '🔍' }), '条件に一致するタスクがありません'));
      return;
    }
    // 時期ごとにまとめて出す。「いつごろ詰まっているか」が一覧からも分かるように
    for (const bucket of bucketize(shown)) {
      listHost.append(el('div', { class: 'period-head' },
        el('span', { text: `${bucket.icon} ${bucket.label}` }),
        el('span', { class: `badge ${bucket.tone}`.trim(), text: `${bucket.items.length} 件` })));
      for (const task of bucket.items) listHost.append(row(task));
    }
  }

  const weekStart = (date) => addDays(date, -((date.getDay() + 6) % 7));

  function weekKeyOf(task) {
    const due = parseDate(task.due_date);
    return due ? toISO(weekStart(due)) : '';
  }

  /** 期限を「いつごろか」で分ける。 */
  function bucketize(tasks) {
    const now = today();
    const endOfWeek = addDays(weekStart(now), 6);
    const endOfNext = addDays(endOfWeek, 7);
    const inMonth = addDays(now, 30);
    const defs = [
      { key: 'overdue', label: '期限超過', icon: '🔥', tone: 'overdue' },
      { key: 'today', label: '今日', icon: '📌', tone: 'soon' },
      { key: 'week', label: '今週中', icon: '🗓', tone: '' },
      { key: 'next', label: '来週', icon: '🗓', tone: '' },
      { key: 'month', label: '1か月以内', icon: '📆', tone: '' },
      { key: 'later', label: 'それ以降', icon: '🕰', tone: '' },
      { key: 'none', label: '期限なし', icon: '—', tone: '' },
    ];
    const pick = (task) => {
      const due = parseDate(task.due_date);
      if (!due) return 'none';
      if (due < now) return 'overdue';
      if (due.getTime() === now.getTime()) return 'today';
      if (due <= endOfWeek) return 'week';
      if (due <= endOfNext) return 'next';
      if (due <= inMonth) return 'month';
      return 'later';
    };
    const groups = new Map(defs.map((d) => [d.key, []]));
    for (const task of tasks) groups.get(pick(task)).push(task);
    return defs.map((d) => ({ ...d, items: groups.get(d.key) }))
      .filter((d) => d.items.length);
  }

  /** 週ごとの件数を棒で並べ、いつ山が来るかを見せる。 */
  function drawTimeline(tasks) {
    const now = today();
    const first = weekStart(now);
    const weeks = Array.from({ length: 12 }, (_, i) => addDays(first, i * 7));
    const counts = weeks.map(() => ({ total: 0, overdue: 0, milestone: 0 }));
    let past = 0;
    let none = 0;
    let beyond = 0;
    for (const task of tasks) {
      const due = parseDate(task.due_date);
      if (!due) { none += 1; continue; }
      if (due < first) { past += 1; continue; }
      const index = Math.floor((due - first) / (7 * 86400000));
      if (index >= weeks.length) { beyond += 1; continue; }
      counts[index].total += 1;
      if (due < now) counts[index].overdue += 1;
      if (task.is_milestone) counts[index].milestone += 1;
    }
    const peak = Math.max(1, ...counts.map((c) => c.total));

    const bar = (week, count, index) => {
      const key = toISO(week);
      const height = Math.round((count.total / peak) * 46);
      const selected = state.week === key;
      return el('button', {
        type: 'button',
        class: `tl-week${selected ? ' active' : ''}${index === 0 ? ' now' : ''}`,
        title: `${formatDate(key)} の週: ${count.total} 件`
          + (count.milestone ? ` / ◆ ${count.milestone}` : ''),
        onClick: () => {
          state.week = selected ? null : key;
          load();
        },
      },
      el('span', { class: 'tl-count', text: count.total ? String(count.total) : '' }),
      el('span', { class: 'tl-bar-wrap' },
        el('span', {
          class: `tl-bar${count.overdue ? ' late' : ''}${count.total >= peak && peak > 2 ? ' peak' : ''}`,
          style: { height: `${Math.max(count.total ? 4 : 0, height)}px` },
        })),
      el('span', { class: 'tl-label', text: `${week.getMonth() + 1}/${week.getDate()}` }));
    };

    fill(timeline,
      el('div', { class: 'card-head' },
        el('h2', {}, 'これからの山'),
        el('span', { class: 'hint',
          text: peak > 1
            ? `いちばん多い週で ${peak} 件。棒をクリックするとその週だけ表示します`
            : '棒をクリックするとその週だけ表示します' })),
      el('div', { class: 'card-body' },
        el('div', { class: 'tl-strip' }, ...weeks.map((w, i) => bar(w, counts[i], i))),
        el('div', { class: 'tl-notes' },
          past ? el('span', { class: 'badge overdue', text: `期限切れ ${past} 件` }) : null,
          beyond ? el('span', { class: 'badge', text: `3か月より先 ${beyond} 件` }) : null,
          none ? el('span', { class: 'badge', text: `期限なし ${none} 件` }) : null,
          state.week
            ? el('button', {
              class: 'btn btn-sm', onClick: () => { state.week = null; load(); },
            }, '週の絞り込みを解除')
            : null)));
  }

  function row(task) {
    return el('div', {
      class: `task-row${task.status === 'done' ? ' is-done' : ''}`,
      onClick: () => openTaskDetail(task.id, { onChange: load }),
    },
    el('div', { class: 'task-main' },
      el('span', {
        class: 'nav-dot',
        style: { background: task.project_color, width: '8px', height: '8px' },
        title: task.project_name,
      }),
      task.is_milestone ? el('span', { class: 'milestone-mark' }, '◆') : null,
      el('span', { class: 'task-title', text: task.title, title: task.title }),
      task.blocks_direct
        ? el('span', { class: 'badge blocking', style: { flex: 'none' },
          title: `後続 ${task.blocks_direct} 件` }, `⛔ ${task.blocks_direct}`)
        : null,
      task.blocked_by_open && task.status !== 'done'
        ? el('span', { class: 'badge blocked-by', style: { flex: 'none' },
          title: `先行 ${task.blocked_by_open} 件が未完了` }, '⏳ 待ち')
        : null,
      el('span', { class: 'task-meta-icons' },
        task.comment_count ? el('span', {}, `💬${task.comment_count}`) : null,
        task.attachment_count ? el('span', {}, `📎${task.attachment_count}`) : null)),
    state.scope === 'mine'
      ? null
      : el('div', { class: 'cell-hide-sm' },
        task.assignee_id
          ? el('span', { class: 'avatar-stack' },
            avatar({ name: task.assignee_name, avatar_color: task.assignee_color }, 'sm'),
            el('span', { class: 'cell-mut', text: task.assignee_name }))
          : el('span', { class: 'cell-mut', text: '未割当' })),
    el('div', { class: 'cell-hide-sm' },
      el('span', { class: `badge ${task.status}`, text: STATUS_LABEL[task.status] })),
    el('div', { class: `cell-mut cell-due cell-hide-sm ${dueClass(task.due_date, task.status)}` },
      formatDate(task.due_date),
      dueLabel(task.due_date, task.status)
        ? el('div', { style: { fontSize: '11px' }, text: dueLabel(task.due_date, task.status) })
        : null),
    el('div', { class: 'cell-hide-sm' },
      el('div', { class: `progress${task.progress >= 100 ? ' done' : ''}` },
        el('i', { style: { width: `${task.progress}%` } })),
      el('div', { class: 'cell-mut', style: { fontSize: '11px' }, text: `${task.progress}%` })),
    el('div', { class: 'cell-hide-sm' },
      task.category ? categoryChip(task.category, { small: true })
        : el('span', { class: 'cell-mut', text: '—' })),
    el('div', { class: 'cell-mut cell-hide-sm', style: { fontSize: '11px' },
      text: task.project_name }),
    el('div', { class: 'task-sub' },
      el('span', { class: `badge ${task.status}`, text: STATUS_LABEL[task.status] }),
      task.category ? categoryChip(task.category, { small: true }) : null,
      el('span', { text: task.project_name }),
      task.due_date
        ? el('span', { class: `cell-due ${dueClass(task.due_date, task.status)}`,
          text: `📅 ${formatDate(task.due_date)}` })
        : null,
      el('span', { text: `${task.progress}%` })));
  }

  syncScopeClass();
  await load();
}
