/* 決定を記録する・直す画面。決めたあとに直すときは、変えた理由を書いてもらう。 */
import { api } from '../api.js';
import { store } from '../store.js';
import { el, fill, openModal, toast } from '../util.js';
import { chipPicker } from './pickers.js';

const SETTLED = ['decided', 'review', 'superseded', 'withdrawn'];

/**
 * @param {object} o projectId, decision（直すとき）, decisions（同じプロジェクトの一覧。置き換えの候補）
 * @returns {Promise<object|null>} 保存した決定
 */
export async function openDecisionForm({ projectId, decision = null, decisions = [] }) {
  const editing = Boolean(decision);
  const [taskData, issueData] = await Promise.all([
    api.projectTasks(projectId),
    api.get(`/api/projects/${projectId}/issues`).catch(() => ({ issues: [] })),
  ]);
  const project = store.project(projectId);
  const guestTabOpen = (project?.guest_tabs || []).includes('decisions');

  const title = el('input', { class: 'input', placeholder: '例）認証は社内の SSO に寄せる', value: decision?.title || '' });
  const status = el('select', { class: 'select' },
    ...(store.meta.decision_statuses || []).map((s) => el('option', {
      value: s.value, selected: (decision?.status || 'decided') === s.value ? true : null,
    }, s.label)));
  const decidedOn = el('input', { class: 'input', type: 'date', value: decision?.decided_on || '' });
  const categories = [...new Set(decisions.map((d) => d.category).filter(Boolean))];
  const category = el('input', { class: 'input', placeholder: '例）技術 / 体制 / 予算', list: 'decision-cats', value: decision?.category || '' });
  const catList = el('datalist', { id: 'decision-cats' }, ...categories.map((c) => el('option', { value: c })));
  const what = el('textarea', { class: 'textarea', rows: 3, placeholder: '決めた内容を、あとから読んで分かるように' });
  what.value = decision?.what || '';
  const why = el('textarea', { class: 'textarea', rows: 3, placeholder: 'なぜそうしたか。比べた観点、決め手になった事実' });
  why.value = decision?.why || '';
  const people = chipPicker((taskData.members || []).map((m) => ({ id: m.id, title: m.name })),
    (decision?.people || []).map((p) => p.id), {
      placeholder: '決めた人を選ぶ…', emptyText: '（未設定）', exhausted: '選べる人はもういません',
    });
  const tasks = chipPicker((taskData.tasks || []).filter((t) => !t.is_heading),
    decision?.links?.tasks || [], { placeholder: '関連するタスクを選ぶ…', emptyText: '（なし）' });
  const issues = chipPicker((issueData.issues || []).map((i) => ({ id: i.id, title: `#${i.seq} ${i.title}` })),
    decision?.links?.issues || [], { placeholder: '関連する課題を選ぶ…', emptyText: '（なし）' });
  const supersedes = el('select', { class: 'select' },
    el('option', { value: '' }, '（なし）'),
    ...decisions.filter((d) => d.id !== decision?.id).map((d) => el('option', {
      value: d.id, selected: decision?.supersedes_id === d.id ? true : null,
    }, `D-${d.seq} ${d.title}（${d.status_label}）`)));
  const guestVisible = el('input', { type: 'checkbox', checked: decision?.guest_visible ? true : null });
  const reason = el('textarea', { class: 'textarea', rows: 2, placeholder: '例）協力会社の人は SSO のアカウントを持っていないと分かったため' });

  // 検討した案：案ごとに採用／却下と理由
  const options = (decision?.options || []).map((o) => ({ ...o }));
  if (!editing) options.push({ title: '', detail: '', adopted: true, reason: '' });
  const optionHost = el('div', { class: 'decision-edit-list' });
  const drawOptions = () => fill(optionHost, ...options.map((o, i) => {
    const name = el('input', { class: 'input', placeholder: `案 ${i + 1}`, value: o.title });
    name.addEventListener('input', () => { o.title = name.value; });
    const pick = el('select', { class: 'select decision-adopt' },
      el('option', { value: '1', selected: o.adopted ? true : null }, '採用'),
      el('option', { value: '0', selected: o.adopted ? null : true }, '却下'));
    pick.addEventListener('change', () => { o.adopted = pick.value === '1'; row.classList.toggle('rejected', !o.adopted); });
    const why2 = el('input', { class: 'input', placeholder: '理由（却下した理由は特に残しておくと役立ちます）', value: o.reason || '' });
    why2.addEventListener('input', () => { o.reason = why2.value; });
    const row = el('div', { class: `decision-edit-row option${o.adopted ? '' : ' rejected'}` },
      name, pick, why2,
      el('button', { type: 'button', class: 'icon-btn', title: '外す', onClick: () => { options.splice(i, 1); drawOptions(); } }, '×'));
    return row;
  }), el('button', {
    type: 'button', class: 'btn btn-sm',
    onClick: () => { options.push({ title: '', detail: '', adopted: false, reason: '' }); drawOptions(); },
  }, '＋ 案を追加'));
  drawOptions();

  // 前提条件：崩れたら印を付け、見直す日を決められる
  const premises = (decision?.premises || []).map((p) => ({ ...p }));
  const premiseHost = el('div', { class: 'decision-edit-list' });
  const drawPremises = () => fill(premiseHost, ...premises.map((p, i) => {
    const text = el('input', { class: 'input', placeholder: '例）全社員が SSO のアカウントを持っている', value: p.text });
    text.addEventListener('input', () => { p.text = text.value; });
    const date = el('input', { class: 'input', type: 'date', title: '見直す日', value: p.review_on || '' });
    date.addEventListener('change', () => { p.review_on = date.value || null; });
    const broken = el('input', { type: 'checkbox', checked: p.broken ? true : null });
    broken.addEventListener('change', () => { p.broken = broken.checked; });
    return el('div', { class: 'decision-edit-row premise' }, text, date,
      el('label', { class: 'check', title: 'この前提は成り立たなくなった' }, broken, el('span', { text: '崩れた' })),
      el('button', { type: 'button', class: 'icon-btn', title: '外す', onClick: () => { premises.splice(i, 1); drawPremises(); } }, '×'));
  }), el('button', {
    type: 'button', class: 'btn btn-sm',
    onClick: () => { premises.push({ text: '', review_on: null, broken: false }); drawPremises(); },
  }, '＋ 前提を追加'));
  drawPremises();

  const reasonField = el('div', { class: 'field decision-reason-field' },
    el('label', { text: '変えた理由 *（決めたあとに変えるときは必須）' }), reason,
    el('div', { class: 'hint', text: '変更の履歴に残ります。あとから「なぜ変わったのか」を追えるように書いてください。' }));
  reasonField.hidden = !(editing && SETTLED.includes(decision.status));

  const error = el('div', { class: 'login-error', hidden: true });
  return openModal({
    title: editing ? `D-${decision.seq} を直す` : '決定を記録',
    wide: true,
    build: () => el('div', { class: 'decision-form' },
      error,
      el('div', { class: 'field' }, el('label', { text: '件名 *' }), title),
      el('div', { class: 'row' },
        el('div', { class: 'field' }, el('label', { text: '状態' }), status),
        el('div', { class: 'field' }, el('label', { text: '決めた日' }), decidedOn),
        el('div', { class: 'field' }, el('label', { text: '分類' }), category, catList)),
      el('div', { class: 'field' }, el('label', { text: '何を決めたか' }), what),
      el('div', { class: 'field' }, el('label', { text: 'なぜ（理由・根拠）' }), why),
      el('div', { class: 'field' }, el('label', { text: '決めた人' }), people.node),
      el('div', { class: 'field' }, el('label', { text: '検討した案（採用・却下）' }), optionHost),
      el('div', { class: 'field' }, el('label', { text: '前提条件' }), premiseHost,
        el('div', { class: 'hint', text: 'この決定が成り立つための前提。崩れたら印を付け、状態を「見直し中」にします。' })),
      el('div', { class: 'row' },
        el('div', { class: 'field' }, el('label', { text: '関連するタスク' }), tasks.node),
        el('div', { class: 'field' }, el('label', { text: '関連する課題' }), issues.node)),
      el('div', { class: 'field' }, el('label', { text: 'この決定が置き換える、前の決定' }), supersedes,
        el('div', { class: 'hint', text: '状態を「決定」で保存すると、前の決定は「置き換え済み」になります。' })),
      el('div', { class: 'field' },
        el('label', { class: 'check' }, guestVisible, el('span', { text: '社外ユーザーにも見せる' })),
        el('div', { class: 'hint',
          text: guestTabOpen
            ? '「検討中」のあいだは見せません。社外ユーザーには変更の履歴も見せません。'
            : 'いまは、このプロジェクトの「決定」タブを社外ユーザーに見せていません（プロジェクト設定の「タブ」で変えられます）。' })),
      reasonField),
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', {
        class: 'btn btn-primary',
        onClick: async () => {
          error.hidden = true;
          const payload = {
            title: title.value.trim(), status: status.value, decided_on: decidedOn.value || null,
            category: category.value.trim(), what: what.value, why: why.value,
            people: people.ids(), guest_visible: guestVisible.checked,
            supersedes_id: supersedes.value ? Number(supersedes.value) : null,
            options: options.filter((o) => o.title.trim()),
            premises: premises.filter((p) => p.text.trim()),
            links: { tasks: tasks.ids(), issues: issues.ids() },
          };
          if (editing) payload.reason = reason.value.trim();
          try {
            const result = editing
              ? await api.patch(`/api/decisions/${decision.id}`, payload)
              : await api.post(`/api/projects/${projectId}/decisions`, payload);
            toast(editing ? '保存しました（変更の履歴に残りました）' : '決定を記録しました', 'ok');
            close(result.decision);
          } catch (err) {
            error.textContent = err.message;
            error.hidden = false;
            if (err.message.includes('理由')) { reasonField.hidden = false; reason.focus(); }
          }
        },
      }, editing ? '保存' : '記録する'),
    ],
  });
}
