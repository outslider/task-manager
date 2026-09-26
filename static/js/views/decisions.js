/* 意思決定ログ（プロジェクトの「決定」タブ）。一覧と、時系列の表示。 */
import { api } from '../api.js';
import { setHeader } from '../app.js';
import { store } from '../store.js';
import { avatar, el, fill, formatDate } from '../util.js';
import { iconLabel } from '../icons.js';
import { projectTabs } from './projectNav.js';
import { openDecisionDetail, statusBadge } from './decisionDetail.js';
import { openDecisionForm } from './decisionForm.js';

const VIEW_KEY = 'tm.decisionView';
const STATUS_FILTERS = [
  ['live', '有効なもの'], ['all', 'すべて'], ['draft', '検討中'], ['decided', '決定'],
  ['review', '見直し中'], ['superseded', '置き換え済み'], ['withdrawn', '取り消し'],
];

export async function render(container, route) {
  const projectId = route.projectId;
  const project = store.project(projectId);
  const canEdit = ['owner', 'editor'].includes(project?.my_role);
  const state = {
    q: '', status: 'live', category: '',
    view: (() => { try { return localStorage.getItem(VIEW_KEY) || 'list'; } catch { return 'list'; } })(),
  };
  let data = await api.get(`/api/projects/${projectId}/decisions`);

  const add = async () => {
    const saved = await openDecisionForm({ projectId, decisions: data.decisions });
    if (saved) { await reload(); openDecisionDetail(saved.id, { onChange: reload }); }
  };
  setHeader(`${project?.name || ''} — 決定`, [
    canEdit ? el('button', { class: 'btn btn-primary', onClick: add }, ...iconLabel('plus', '決定を記録')) : null,
  ]);

  const search = el('input', { class: 'input', type: 'search', placeholder: '件名・決定内容・理由で探す' });
  search.addEventListener('input', () => { state.q = search.value.trim().toLowerCase(); draw(); });
  const statusSelect = el('select', { class: 'select', style: { maxWidth: '150px' } },
    ...STATUS_FILTERS.map(([value, label]) => el('option', { value }, label)));
  statusSelect.addEventListener('change', () => { state.status = statusSelect.value; draw(); });
  const categorySelect = el('select', { class: 'select', style: { maxWidth: '170px' } });
  categorySelect.addEventListener('change', () => { state.category = categorySelect.value; draw(); });
  const viewSeg = el('div', { class: 'seg' },
    ...[['list', '一覧'], ['timeline', '時系列']].map(([value, label]) => el('button', {
      type: 'button', dataset: { value },
      onClick: () => {
        state.view = value;
        try { localStorage.setItem(VIEW_KEY, value); } catch { /* private mode */ }
        draw();
      },
    }, label)));
  const host = el('div', {});
  const summary = el('div', { class: 'page-sub' });

  fill(container,
    projectTabs(projectId, 'decisions'),
    el('div', { class: 'page-head' }, el('div', { class: 'grow' }, summary)),
    el('div', { class: 'card' },
      el('div', { class: 'toolbar decision-toolbar' }, search, statusSelect, categorySelect, viewSeg),
      el('div', { class: 'card-body' }, host)));

  async function reload() {
    data = await api.get(`/api/projects/${projectId}/decisions`);
    draw();
  }

  function visible() {
    return data.decisions.filter((d) => {
      if (state.status === 'live' && ['superseded', 'withdrawn'].includes(d.status)) return false;
      if (!['live', 'all'].includes(state.status) && d.status !== state.status) return false;
      if (state.category && d.category !== state.category) return false;
      if (state.q && !`${d.title} ${d.what || ''} ${d.why || ''}`.toLowerCase().includes(state.q)) return false;
      return true;
    });
  }

  function draw() {
    const cats = data.categories || [];
    fill(categorySelect, el('option', { value: '' }, '分類：すべて'),
      ...cats.map((c) => el('option', { value: c, selected: state.category === c ? true : null }, c)));
    categorySelect.hidden = !cats.length;
    for (const b of viewSeg.children) b.classList.toggle('active', b.dataset.value === state.view);
    const all = data.decisions;
    const counts = {
      decided: all.filter((d) => d.status === 'decided').length,
      review: all.filter((d) => d.status === 'review').length,
      draft: all.filter((d) => d.status === 'draft').length,
    };
    fill(summary,
      `全 ${all.length} 件 ・ 決定 ${counts.decided} ・ 検討中 ${counts.draft}`,
      counts.review ? el('span', { class: 'badge warn-badge', style: { marginLeft: '8px' },
        text: `見直し中 ${counts.review}` }) : null);
    const rows = visible();
    if (!rows.length) {
      fill(host, el('div', { class: 'empty' },
        el('div', { class: 'big', text: '⚖️' }),
        all.length ? '条件に合う決定はありません'
          : 'まだ決定の記録がありません。「何を・なぜ・誰が決めたか」を残しておくと、あとから方針の理由をたどれます。',
        canEdit && !all.length
          ? el('div', { style: { marginTop: '12px' } }, el('button', { class: 'btn btn-primary', onClick: add }, '最初の決定を記録'))
          : null));
      return;
    }
    fill(host, state.view === 'timeline' ? timeline(rows) : el('div', { class: 'decision-list' }, ...rows.map(card)));
  }

  function card(d) {
    const open = () => openDecisionDetail(d.id, { onChange: reload });
    return el('button', { type: 'button', class: `decision-card st-${d.status}`, onClick: open },
      el('div', { class: 'decision-card-head' },
        el('span', { class: 'decision-no', text: `D-${d.seq}` }),
        el('span', { class: 'decision-title', text: d.title }),
        statusBadge(d.status, d.status_label),
        el('span', { class: 'decision-date', text: d.decided_on ? formatDate(d.decided_on) : '—' })),
      d.what ? el('div', { class: 'decision-line' }, el('b', { text: '何を ' }), el('span', { text: firstLine(d.what) })) : null,
      d.why ? el('div', { class: 'decision-line' }, el('b', { text: 'なぜ ' }), el('span', { text: firstLine(d.why) })) : null,
      el('div', { class: 'decision-meta' },
        d.people.length
          ? el('span', { class: 'avatar-stack', title: d.people.map((p) => p.name).join('、') },
            ...d.people.slice(0, 4).map((p) => avatar(p, 'sm')))
          : null,
        d.people.length ? el('span', { class: 'cell-mut', text: d.people.map((p) => p.name).join('、') }) : null,
        d.category ? el('span', { class: 'decision-chip', text: d.category }) : null,
        d.rejected_count ? el('span', { class: 'decision-chip', text: `却下した案 ${d.rejected_count}` }) : null,
        d.broken_count ? el('span', { class: 'decision-chip warn', text: `崩れた前提 ${d.broken_count}` }) : null,
        d.supersedes ? el('span', { class: 'decision-chip', text: `D-${d.supersedes.seq} を置き換え` }) : null,
        d.superseded_by.length
          ? el('span', { class: 'decision-chip', text: `→ D-${d.superseded_by.map((x) => x.seq).join(', D-')} に置き換え` })
          : null,
        d.guest_visible && !store.isGuest() ? el('span', { class: 'decision-chip guest', text: '社外にも公開' }) : null,
        d.version > 1 && !store.isGuest() ? el('span', { class: 'cell-mut', text: `第 ${d.version} 版` }) : null));
  }

  /** 決めた日の順に、月ごとに縦に並べる。検討中は一番上にまとめる。 */
  function timeline(rows) {
    const drafts = rows.filter((d) => d.status === 'draft');
    const dated = rows.filter((d) => d.status !== 'draft')
      .sort((a, b) => String(b.decided_on).localeCompare(String(a.decided_on)) || b.seq - a.seq);
    const groups = [];
    for (const d of dated) {
      const month = d.decided_on ? d.decided_on.slice(0, 7) : '日付なし';
      if (!groups.length || groups[groups.length - 1].month !== month) groups.push({ month, items: [] });
      groups[groups.length - 1].items.push(d);
    }
    const item = (d) => el('button', {
      type: 'button', class: `tl-item st-${d.status}`,
      onClick: () => openDecisionDetail(d.id, { onChange: reload }),
    },
    el('span', { class: 'tl-dot' }),
    el('span', { class: 'tl-date', text: d.decided_on ? formatDate(d.decided_on) : '' }),
    el('span', { class: 'tl-body' },
      el('span', { class: 'tl-title' }, el('span', { class: 'decision-no', text: `D-${d.seq}` }), ` ${d.title}`),
      d.supersedes ? el('span', { class: 'tl-sub', text: `D-${d.supersedes.seq}「${d.supersedes.title}」を置き換え` }) : null,
      d.why ? el('span', { class: 'tl-sub', text: `なぜ：${firstLine(d.why)}` }) : null),
    statusBadge(d.status, d.status_label));
    return el('div', { class: 'decision-timeline' },
      drafts.length ? el('div', { class: 'tl-group' },
        el('div', { class: 'tl-month', text: '検討中' }), ...drafts.map(item)) : null,
      ...groups.map((g) => el('div', { class: 'tl-group' },
        el('div', { class: 'tl-month', text: g.month === '日付なし' ? g.month : `${g.month.replace('-', '年')}月` }),
        ...g.items.map(item))));
  }

  draw();
}

function firstLine(text) {
  const line = String(text || '').split('\n').find((l) => l.trim()) || '';
  return line.length > 120 ? `${line.slice(0, 120)}…` : line;
}
