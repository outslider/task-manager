/* 課題管理表 — issue log, per project or across every project. */
import { api } from '../api.js';
import { setHeader } from '../app.js';
import {
  store, ISSUE_CATEGORIES, ISSUE_STATUS_LABEL, SEVERITY_LABEL, issueCategory,
} from '../store.js';
import {
  avatar, debounce, downloadBlob, dueClass, dueLabel, el, fill, formatDate, toISO, today,
} from '../util.js';
import { issueCategoryChip, option } from './pickers.js';
import { projectTabs } from './projectNav.js';
import { openIssueForm } from './issueForm.js';
import { openIssueDetail } from './issueDetail.js';

export async function render(container, route) {
  const projectId = route.projectId || null;
  const project = projectId ? (await api.project(projectId)).project : null;
  const state = { status: 'open', category: '', owner_id: '', q: '', min_severity: '' };
  let data = { issues: [], summary: {} };

  const canEdit = project ? store.canEdit(project) : false;

  setHeader(project ? `${project.name} — 課題管理表` : '課題管理表',
    [
      el('button', { class: 'btn', onClick: exportCsv }, '⬇ CSV'),
      // 全体一覧にはプロジェクトが無いので、どこかで編集できるなら出す
      (canEdit || store.projects.some((p) => !p.archived && store.canEdit(p)))
        ? el('button', { class: 'btn btn-primary', onClick: () => createIssue() }, '＋ 課題を起票')
        : null,
    ]);

  const summaryHost = el('div', { class: 'grid cols-4', style: { marginBottom: '14px' } });
  const rowsHost = el('div', {});

  const search = el('input', {
    class: 'input', type: 'search', placeholder: '課題を検索…', style: { maxWidth: '220px' },
    onInput: debounce((event) => { state.q = event.target.value.trim(); load(); }, 250),
  });
  const statusFilter = el('select', {
    class: 'select', style: { maxWidth: '150px' },
    onChange: (event) => { state.status = event.target.value; load(); },
  },
  option('open', '未解決のみ', true), option('', 'すべての状態'),
  ...Object.entries(ISSUE_STATUS_LABEL).map(([value, label]) => option(value, label)));
  const categoryFilter = el('select', {
    class: 'select', style: { maxWidth: '150px' },
    onChange: (event) => { state.category = event.target.value; load(); },
  }, option('', '区分: すべて'),
  ...ISSUE_CATEGORIES.map((c) => option(c.value, c.label)));
  const ownerFilter = el('select', {
    class: 'select', style: { maxWidth: '150px' },
    onChange: (event) => { state.owner_id = event.target.value; load(); },
  }, option('', '対応者: すべて'), option('me', '自分の担当'), option('none', '未割当'),
  ...store.users.map((u) => option(u.id, u.name)));
  const severityFilter = el('select', {
    class: 'select', style: { maxWidth: '140px' },
    onChange: (event) => { state.min_severity = event.target.value; load(); },
  }, option('', '影響度: すべて'), option('2', '高・重大のみ'), option('3', '重大のみ'));

  const head = el('div', { class: 'issue-head' },
    el('div', { text: 'No.' }), el('div', { text: '課題' }), el('div', { text: '区分' }),
    el('div', { text: '影響度' }), el('div', { text: '状態' }), el('div', { text: '対応者' }),
    el('div', { text: '期限' }), el('div', { text: 'タスク' }));

  fill(container,
    projectId ? projectTabs(projectId, 'issues') : null,
    summaryHost,
    el('div', { class: 'card' },
      el('div', { class: 'toolbar' },
        search, statusFilter, categoryFilter, ownerFilter, severityFilter),
      head,
      el('div', { class: 'card-body tight' }, rowsHost)));

  async function load() {
    fill(rowsHost, el('div', { class: 'empty', text: '読み込み中…' }));
    const query = {
      status: state.status, category: state.category, owner_id: state.owner_id,
      q: state.q, min_severity: state.min_severity,
    };
    data = projectId
      ? await api.get(`/api/projects/${projectId}/issues`, query)
      : await api.get('/api/issues', query);
    draw();
  }

  function draw() {
    const s = data.summary || {};
    fill(summaryHost,
      stat('未解決', s.open ?? 0, s.open ? '' : 'ok'),
      stat('期限超過', s.overdue ?? 0, s.overdue ? 'danger' : ''),
      stat('影響度 高・重大', s.high ?? 0, s.high ? 'warn' : ''),
      stat('解決済', s.resolved ?? 0, 'ok'));

    fill(rowsHost, ...(data.issues.length
      ? data.issues.map(issueRow)
      : [el('div', { class: 'empty' },
        el('div', { class: 'big', text: '📌' }),
        state.q || state.status !== 'open' || state.category
          ? '条件に一致する課題がありません'
          : '課題は登録されていません',
        canEdit
          ? el('div', { style: { marginTop: '12px' } },
            el('button', { class: 'btn btn-primary', onClick: () => createIssue() },
              '課題を起票する'))
          : null)]));
  }

  function stat(label, value, tone) {
    return el('div', { class: 'card stat' },
      el('div', { class: 'k', text: label }),
      el('div', { class: `v ${tone}`.trim(), text: String(value) }));
  }

  function issueRow(issue) {
    const closed = ['resolved', 'closed'].includes(issue.status);
    const overdueClass = closed ? '' : dueClass(issue.due_date, issue.status);
    const overdueText = closed ? '' : dueLabel(issue.due_date, issue.status);
    return el('div', {
      class: ['issue-row', closed ? 'is-closed' : '',
        !closed && issue.severity >= 3 ? 'is-critical' : ''].filter(Boolean).join(' '),
      onClick: () => openIssueDetail(issue.id, { onChange: load }),
    },
    el('div', { class: 'issue-no', text: `#${issue.seq}` }),
    el('div', {},
      el('div', { class: 'issue-title', text: issue.title, title: issue.title }),
      !projectId
        ? el('div', { class: 'hint', text: issue.project_name })
        : null),
    el('div', { class: 'cell-hide-sm' }, issueCategoryChip(issue.category, { small: true })),
    el('div', { class: `cell-hide-sm sev sev-${issue.severity}`,
      text: SEVERITY_LABEL[issue.severity] }),
    el('div', { class: 'cell-hide-sm' },
      el('span', { class: `badge ${issue.status}`, text: ISSUE_STATUS_LABEL[issue.status] })),
    el('div', { class: 'cell-hide-sm' },
      issue.owner_id
        ? el('span', { class: 'avatar-stack' },
          avatar({ name: issue.owner_name, avatar_color: issue.owner_color }, 'sm'),
          el('span', { class: 'cell-mut', text: issue.owner_name }))
        : el('span', { class: 'cell-mut', text: '未割当' })),
    el('div', { class: `cell-mut cell-due cell-hide-sm ${overdueClass}` },
      formatDate(issue.due_date),
      overdueText ? el('div', { style: { fontSize: '11px' }, text: overdueText }) : null),
    el('div', { class: 'cell-mut cell-hide-sm', style: { fontSize: '12px' } },
      issue.task_count
        ? el('span', {
          title: `関連タスク ${issue.task_count} 件（未完了 ${issue.open_task_count} 件）`,
        }, `🔗 ${issue.open_task_count}/${issue.task_count}`)
        : '—'),
    el('div', { class: 'issue-sub' },
      el('span', { class: `badge ${issue.status}`, text: ISSUE_STATUS_LABEL[issue.status] }),
      issueCategoryChip(issue.category, { small: true }),
      el('span', { class: `sev sev-${issue.severity}`, text: `影響度 ${SEVERITY_LABEL[issue.severity]}` }),
      issue.owner_name ? el('span', { text: `👤 ${issue.owner_name}` }) : null,
      issue.due_date
        ? el('span', { class: `cell-due ${overdueClass}`, text: `📅 ${formatDate(issue.due_date)}` })
        : null,
      issue.task_count ? el('span', { text: `🔗 ${issue.open_task_count}/${issue.task_count}` }) : null));
  }

  async function createIssue() {
    // 全体一覧から起票するときは、書き込めるプロジェクトの中から選ばせる
    const candidates = store.projects.filter((p) => !p.archived && store.canEdit(p));
    const target = project || candidates[0];
    if (!target) { return; }
    const projectTasks = await api.projectTasks(target.id);
    const saved = await openIssueForm({
      project: target, tasks: projectTasks.tasks, members: projectTasks.members,
      projects: project ? null : candidates });
    if (saved) {
      await store.refreshProjects();
      load();
    }
  }

  function exportCsv() {
    const header = ['No.', '課題', '区分', '影響度', '状態', '対応者', '起票者',
      '発生日', '期限', '解決日', '内容', '対応方針', '関連タスク数', 'プロジェクト'];
    const rows = data.issues.map((i) => [
      i.seq, i.title, issueCategory(i.category).label, SEVERITY_LABEL[i.severity],
      ISSUE_STATUS_LABEL[i.status], i.owner_name || '', i.raised_by_name || '',
      i.raised_on || '', i.due_date || '', i.resolved_on || '',
      i.description || '', i.resolution || '', i.task_count, i.project_name,
    ]);
    const escape = (value) => `"${String(value ?? '').replace(/"/g, '""')}"`;
    const csv = [header, ...rows].map((r) => r.map(escape).join(',')).join('\r\n');
    // BOM 付きにして Excel で文字化けしないようにする
    downloadBlob(new Blob([`﻿${csv}`], { type: 'text/csv;charset=utf-8' }),
      `課題管理表_${project ? project.name : '全プロジェクト'}_${toISO(today())}.csv`);
  }

  await load();
}
