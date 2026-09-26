/* 決定の詳細（右から出る画面）。何を・なぜ・誰が・検討した案・前提条件・関連・変更履歴。 */
import { api } from '../api.js';
import { store, STATUS_LABEL, ISSUE_STATUS_LABEL } from '../store.js';
import {
  avatar, confirmDialog, el, fill, formatDate, openDrawer, openModal, skeleton, toast,
} from '../util.js';
import { icon } from '../icons.js';

let openInstance = null;

export function statusBadge(status, label) {
  return el('span', { class: `decision-status st-${status}`, text: label || status });
}

export async function openDecisionDetail(decisionId, { onChange } = {}) {
  if (openInstance) openInstance.close();
  const instance = openDrawer({
    build: (drawer) => { drawer.appendChild(skeleton('text', 4)); },
    onClose: () => { openInstance = null; if (onChange) onChange(); },
  });
  openInstance = instance;
  await draw(instance, decisionId, onChange);
  return instance;
}

async function draw(instance, decisionId, onChange) {
  let data;
  try {
    data = await api.get(`/api/decisions/${decisionId}`);
  } catch (error) {
    fill(instance.drawer, el('div', { class: 'empty', text: error.message }));
    return;
  }
  const d = data.decision;
  const canEdit = ['owner', 'editor'].includes(data.my_role) && !store.acting;
  const reload = async () => { await draw(instance, decisionId, onChange); if (onChange) onChange(); };

  const head = el('div', { class: 'drawer-head' },
    el('div', { style: { minWidth: 0, flex: 1 } },
      el('div', { class: 'breadcrumb' }, `${data.project?.name || ''} ・ 決定 D-${d.seq}`),
      el('h2', { text: d.title, style: { whiteSpace: 'normal' } })),
    canEdit ? el('button', {
      class: 'icon-btn', title: '編集',
      onClick: async () => {
        const list = await api.get(`/api/projects/${d.project_id}/decisions`);
        const { openDecisionForm } = await import('./decisionForm.js');
        const saved = await openDecisionForm({ projectId: d.project_id, decision: d, decisions: list.decisions });
        if (saved) reload();
      },
    }, '✏️') : null,
    data.my_role === 'owner' && !store.acting ? el('button', {
      class: 'icon-btn', title: '削除',
      onClick: async () => {
        if (!await confirmDialog(
          `決定 D-${d.seq}「${d.title}」を削除しますか？\n変更の履歴も消え、元に戻せません。`
          + '\n残しておきたい場合は、削除せずに状態を「取り消し」にしてください。',
          { danger: true, okLabel: '削除する' })) return;
        try {
          await api.del(`/api/decisions/${d.id}`);
          toast('削除しました', 'ok');
          instance.close();
        } catch (error) { toast(error.message, 'error'); }
      },
    }, icon('trash')) : null,
    el('button', { class: 'icon-btn', title: '閉じる', onClick: () => instance.close() }, icon('close')));

  const facts = el('div', { class: 'decision-facts' },
    statusBadge(d.status, d.status_label),
    el('span', { text: d.decided_on ? `${formatDate(d.decided_on)} に決定` : '日付未定' }),
    d.category ? el('span', { class: 'decision-chip', text: d.category }) : null,
    d.guest_visible && !store.isGuest() ? el('span', { class: 'decision-chip guest', text: '社外にも公開' }) : null,
    !store.isGuest() && d.version > 1 ? el('span', { class: 'cell-mut', text: `第 ${d.version} 版` }) : null);

  const chain = [];
  if (data.supersedes) chain.push(el('div', { class: 'decision-chain' }, 'この決定は ', refLink(data.supersedes, reload), ' を置き換えました'));
  for (const later of data.superseded_by || []) {
    chain.push(el('div', { class: 'decision-chain later' }, 'この決定は ', refLink(later, reload), ' に置き換えられました'));
  }

  const body = el('div', { class: 'drawer-body decision-body' },
    facts,
    ...chain,
    section('何を決めたか', d.what ? el('div', { class: 'decision-text', text: d.what }) : muted('（未記入）')),
    section('なぜ（理由・根拠）', d.why ? el('div', { class: 'decision-text', text: d.why }) : muted('（未記入）')),
    section('決めた人', d.people.length
      ? el('div', { class: 'decision-people' }, ...d.people.map((p) => el('span', { class: 'decision-person' }, avatar(p, 'sm'), p.name)))
      : muted('（未記入）')),
    section(`検討した案（${d.options.length}）`, d.options.length
      ? el('div', { class: 'decision-options' }, ...d.options.map((o) => el('div', { class: `decision-option ${o.adopted ? 'adopted' : 'rejected'}` },
        el('div', { class: 'decision-option-head' },
          el('span', { class: 'decision-option-mark', text: o.adopted ? '採用' : '却下' }),
          el('strong', { text: o.title })),
        o.detail ? el('div', { class: 'decision-text small', text: o.detail }) : null,
        o.reason ? el('div', { class: 'decision-reason' }, el('b', { text: o.adopted ? '採用した理由：' : '却下した理由：' }), o.reason) : null)))
      : muted('（記録なし）')),
    section(`前提条件（${d.premises.length}）`, d.premises.length
      ? el('ul', { class: 'decision-premises' }, ...d.premises.map((p) => el('li', { class: p.broken ? 'broken' : '' },
        el('span', { class: 'premise-mark', text: p.broken ? '✕ 崩れた' : '✓' }),
        el('span', { class: 'premise-text', text: p.text }),
        p.review_on ? el('span', { class: `premise-review${!p.broken && p.review_on <= today() ? ' due' : ''}`,
          text: `見直し ${formatDate(p.review_on)}` }) : null)))
      : muted('（記録なし）')),
    section('関連', (data.tasks.length || data.issues.length)
      ? el('div', { class: 'decision-links' },
        ...data.tasks.map((t) => el('button', {
          type: 'button', class: 'decision-link',
          onClick: async () => { const { openTaskDetail } = await import('./taskDetail.js'); openTaskDetail(t.id); },
        }, '✓ ', t.title, el('span', { class: 'cell-mut', text: `　${STATUS_LABEL[t.status] || t.status}` }))),
        ...data.issues.map((i) => el('button', {
          type: 'button', class: 'decision-link',
          onClick: async () => { const { openIssueDetail } = await import('./issueDetail.js'); openIssueDetail(i.id); },
        }, `📌 #${i.seq} `, i.title, el('span', { class: 'cell-mut', text: `　${ISSUE_STATUS_LABEL[i.status] || i.status}` }))))
      : muted('（なし）')),
    data.versions ? section('変更履歴', versionList(d, data)) : null);

  fill(instance.drawer, head, body);
}

function versionList(d, data) {
  return el('ol', { class: 'decision-versions' },
    ...data.versions.map((v) => el('li', {},
      el('div', { class: 'version-head' },
        el('span', { class: 'version-no', text: `第 ${v.version} 版` }),
        el('span', { class: 'cell-mut', text: `${v.created_at.replace(/-/g, '/')}　${v.changed_by_name || ''}` }),
        v.version < d.version ? el('button', {
          type: 'button', class: 'btn btn-sm', onClick: () => showVersion(d, v.version),
        }, 'この版を見る') : el('span', { class: 'hint', text: '（いまの内容）' })),
      el('div', { class: 'version-changes', text: v.changes ? `変更：${v.changes}` : '' }),
      v.reason ? el('div', { class: 'version-reason', text: `理由：${v.reason}` }) : null)));
}

async function showVersion(d, version) {
  const data = await api.get(`/api/decisions/${d.id}/versions/${version}`);
  const s = data.snapshot;
  openModal({
    title: `D-${d.seq} の第 ${version} 版（${data.created_at.replace(/-/g, '/')}）`,
    wide: true,
    build: () => el('div', { class: 'decision-body' },
      el('div', { class: 'decision-facts' }, statusBadge(s.status, s.status_label),
        el('span', { text: s.decided_on ? `${formatDate(s.decided_on)} に決定` : '日付未定' })),
      section('件名', el('div', { class: 'decision-text', text: s.title })),
      section('何を決めたか', el('div', { class: 'decision-text', text: s.what || '（未記入）' })),
      section('なぜ', el('div', { class: 'decision-text', text: s.why || '（未記入）' })),
      section('決めた人', el('div', { text: (s.people || []).map((p) => p.name).join('、') || '（未記入）' })),
      section('検討した案', el('ul', {}, ...(s.options || []).map((o) => el('li', {},
        `${o.adopted ? '【採用】' : '【却下】'}${o.title}${o.reason ? `　— ${o.reason}` : ''}`)))),
      section('前提条件', el('ul', {}, ...(s.premises || []).map((p) => el('li', {},
        `${p.broken ? '【崩れた】' : ''}${p.text}${p.review_on ? `（見直し ${formatDate(p.review_on)}）` : ''}`))))),
    footer: (close) => [el('button', { class: 'btn', onClick: () => close() }, '閉じる')],
  });
}

function refLink(ref, reload) {
  return el('button', {
    type: 'button', class: 'decision-ref',
    onClick: () => openDecisionDetail(ref.id, { onChange: reload }),
  }, `D-${ref.seq}「${ref.title}」`, el('span', { class: 'cell-mut', text: `（${ref.status_label}）` }));
}

function section(title, content) {
  return el('div', { class: 'decision-section' },
    el('div', { class: 'section-title' }, el('span', { text: title }), el('span', { class: 'line' })),
    content);
}

function muted(text) { return el('div', { class: 'hint', text }); }

function today() { return new Date().toISOString().slice(0, 10); }
