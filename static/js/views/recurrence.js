/* 繰り返し（定例タスク）の設定。プロジェクト単位で規則を管理する。 */
import { api } from '../api.js';
import { store } from '../store.js';
import {
  confirmDialog, el, fill, formatDate, openModal,
  skeleton, toast, today, toISO,
} from '../util.js';
import { categorySelect, option, userSelect } from './pickers.js';

const WEEKDAYS = ['月', '火', '水', '木', '金', '土', '日'];
const MAX_DEPTH = 8;
const FREQ = [['weekly', '毎週'], ['monthly', '毎月'], ['daily', '毎日']];

/** 一覧と編集をまとめたダイアログ。 */
export async function openRecurrenceManager(project, { onChange } = {}) {
  const listHost = el('div', {});
  const canEdit = store.canEdit(project);
  let changed = false;

  const load = async () => {
    fill(listHost, skeleton('rows', 3));
    try {
      const data = await api.get(`/api/projects/${project.id}/recurrences`);
      fill(listHost, ...(data.recurrences.length
        ? data.recurrences.map(row)
        : [el('div', { class: 'empty' },
          el('div', { class: 'big', text: '🔁' }),
          '定例タスクは登録されていません',
          el('div', { class: 'hint', style: { marginTop: '8px' },
            text: '毎週の議事録、月末の締め作業などを登録しておくと自動で起票されます。' }))]));
    } catch (error) {
      fill(listHost, el('div', { class: 'empty', text: error.message }));
    }
  };

  const row = (rule) => el('div', { class: `rec-row${rule.active ? '' : ' off'}` },
    el('div', { style: { minWidth: 0 } },
      el('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' } },
        el('strong', { text: rule.title }),
        el('span', { class: 'badge doing', text: rule.summary }),
        rule.active ? null : el('span', { class: 'badge', text: '停止中' })),
      el('div', { class: 'hint' },
        `次回の期限 ${formatDate(rule.next_on)}`,
        rule.lead_days ? `（${rule.lead_days}日前に作成）` : '（当日に作成）',
        rule.assignee_name ? ` ・ 担当 ${rule.assignee_name}` : ' ・ 担当未設定',
        rule.estimate_hours ? ` ・ 見積 ${Number(rule.estimate_hours)}h` : '',
        rule.parent_title ? ` ・ まとめ先 ${rule.parent_title}` : '')),
    canEdit
      ? el('div', { style: { display: 'flex', gap: '6px' } },
        el('button', {
          class: 'btn btn-sm',
          title: '次回分を今すぐ作る',
          onClick: async () => {
            try {
              await api.post(`/api/recurrences/${rule.id}/run`, {});
              toast('タスクを作成しました', 'ok');
              changed = true;
              load();
            } catch (error) { toast(error.message, 'error'); }
          },
        }, '今すぐ作る'),
        el('button', {
          class: 'btn btn-sm',
          title: '今回は作らずに、次回へ送る',
          onClick: async (event) => {
            const button = event.currentTarget;
            button.disabled = true;
            try {
              const result = await api.post(`/api/recurrences/${rule.id}/skip`, {});
              toast(`${formatDate(result.skipped)} を飛ばしました`
                + `（次回 ${formatDate(result.next_on)}）`, 'ok');
              changed = true;
              load();
            } catch (error) {
              toast(error.message, 'error');
              button.disabled = false;
            }
          },
        }, '⏭ 次回を飛ばす'),
        el('button', {
          class: 'btn btn-sm',
          onClick: async () => {
            if (await openRecurrenceForm(project, rule)) { changed = true; load(); }
          },
        }, '編集'),
        el('button', {
          class: 'icon-btn', title: '削除',
          onClick: async () => {
            if (!await confirmDialog(`「${rule.title}」の繰り返し設定を削除しますか？`,
              { danger: true, okLabel: '削除する' })) return;
            await api.del(`/api/recurrences/${rule.id}`);
            changed = true;
            load();
          },
        }, '×'))
      : null);

  await openModal({
    title: `${project.name} の定例タスク`,
    wide: true,
    build: () => {
      load();
      return el('div', {},
        el('p', { class: 'page-sub',
          text: '登録した規則にしたがって、日次バッチがタスクを自動で起票します。' }),
        listHost);
    },
    footer: (close) => [
      canEdit
        ? el('button', {
          class: 'btn btn-primary', style: { marginRight: 'auto' },
          onClick: async () => {
            if (await openRecurrenceForm(project, null)) { changed = true; load(); }
          },
        }, '＋ 定例を追加')
        : null,
      el('button', { class: 'btn', onClick: () => close(null) }, '閉じる'),
    ],
  });
  if (changed && onChange) onChange();
  return changed;
}

/** 親に指定できるタスク。階層順に並べ、これ以上深くできないものは外す。 */
export function parentCandidates(tasks) {
  const children = new Map();
  const byId = new Map(tasks.map((t) => [t.id, t]));
  for (const task of tasks) {
    const key = byId.has(task.parent_id) ? task.parent_id : null;
    if (!children.has(key)) children.set(key, []);
    children.get(key).push(task);
  }
  for (const list of children.values()) {
    list.sort((a, b) => (a.sort_order - b.sort_order) || (a.id - b.id));
  }
  const out = [];
  const walk = (parentId, depth) => {
    for (const task of children.get(parentId) || []) {
      // 子を1段ぶら下げる余地が要る。マイルストーンは束ね役に向かない
      if (depth + 2 <= MAX_DEPTH && !task.is_milestone && !task.is_heading) {
        out.push({ id: task.id, title: task.title, depth });
      }
      walk(task.id, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

/**
 * 規則の作成・編集。task を渡すと、そのタスクの内容を初期値にする。
 */
export async function openRecurrenceForm(project, rule = null, task = null) {
  const members = await store.members(project.id);
  // 「毎週の打ち合わせ」をひとまとまりで扱えるよう、束ねる親タスクを選べるようにする
  let candidates = [];
  try {
    const data = await api.projectTasks(project.id);
    candidates = parentCandidates(data.tasks);
  } catch { /* 取れなくても規則自体は編集できる */ }
  const source = rule || task || {};
  const state = {
    freq: rule?.freq || 'weekly',
    weekdays: new Set((rule?.weekdays || String(new Date().getDay() === 0 ? 6 : new Date().getDay() - 1))
      .split(',').filter((v) => v !== '').map(Number)),
  };
  const f = {};
  const freqExtra = el('div', {});

  const drawFreqExtra = () => {
    if (state.freq === 'weekly') {
      fill(freqExtra, el('div', { class: 'field' },
        el('label', { text: '曜日' }),
        el('div', { class: 'wd-row' }, ...WEEKDAYS.map((label, index) => {
          const active = state.weekdays.has(index);
          return el('button', {
            type: 'button', class: `wd-btn${active ? ' active' : ''}`,
            onClick: () => {
              if (state.weekdays.has(index)) state.weekdays.delete(index);
              else state.weekdays.add(index);
              drawFreqExtra();
            },
          }, label);
        }))));
    } else if (state.freq === 'monthly') {
      f.monthDay = el('input', {
        class: 'input', type: 'number', min: 1, max: 31,
        value: rule?.month_day || new Date().getDate(),
      });
      fill(freqExtra, el('div', { class: 'field' },
        el('label', { text: '毎月の実施日' }), f.monthDay,
        el('div', { class: 'hint', text: '月末が足りない月は、その月の最終日になります。' })));
    } else {
      fill(freqExtra);
    }
  };

  return openModal({
    title: rule ? '定例タスクを編集' : '定例タスクを追加',
    wide: true,
    build: () => {
      f.title = el('input', { class: 'input', value: source.title || '' });
      f.description = el('textarea', { class: 'textarea', rows: 2 });
      f.description.value = source.description || '';
      f.category = categorySelect(source.category || '');
      f.assignee = userSelect(source.assignee_id, { people: members });
      f.priority = el('select', { class: 'select' },
        ...[[3, '最重要'], [2, '高'], [1, '中'], [0, '低']].map(([value, label]) =>
          option(value, label, String(source.priority ?? 1) === String(value))));
      f.estimate = el('input', {
        class: 'input', type: 'number', min: 0, step: 0.5, placeholder: '任意',
        value: source.estimate_hours ?? '',
      });
      f.freq = el('select', { class: 'select' },
        ...FREQ.map(([value, label]) => option(value, label, state.freq === value)));
      f.freq.addEventListener('change', () => { state.freq = f.freq.value; drawFreqExtra(); });
      f.interval = el('input', {
        class: 'input', type: 'number', min: 1, max: 99, value: rule?.interval_n || 1,
      });
      f.lead = el('input', {
        class: 'input', type: 'number', min: 0, max: 60,
        value: rule?.lead_days ?? 3,
      });
      f.next = el('input', {
        class: 'input', type: 'date',
        value: rule?.next_on || task?.due_date || toISO(today()),
      });
      f.parent = el('select', { class: 'select' },
        option('', '（まとめない）', !source.parent_id),
        ...candidates.map((t) => option(
          t.id, `${'　'.repeat(t.depth)}${t.title}`, Number(source.parent_id) === t.id)));
      f.active = el('input', { type: 'checkbox', checked: rule ? Boolean(rule.active) : true });
      drawFreqExtra();

      return el('div', {},
        el('div', { class: 'field' }, el('label', { text: 'タスク名 *' }), f.title),
        el('div', { class: 'row' },
          el('div', { class: 'field' }, el('label', { text: '繰り返し' }), f.freq),
          el('div', { class: 'field' }, el('label', { text: '間隔' }), f.interval,
            el('div', { class: 'hint', text: '1 なら毎回。2 なら1回とばし。' }))),
        freqExtra,
        el('div', { class: 'row' },
          el('div', { class: 'field' }, el('label', { text: '次回の期限' }), f.next),
          el('div', { class: 'field' }, el('label', { text: '何日前に作るか' }), f.lead)),
        el('div', { class: 'row' },
          el('div', { class: 'field' }, el('label', { text: '担当者' }), f.assignee),
          el('div', { class: 'field' }, el('label', { text: 'カテゴリ' }), f.category)),
        el('div', { class: 'row' },
          el('div', { class: 'field' }, el('label', { text: '重要度' }), f.priority),
          el('div', { class: 'field' }, el('label', { text: '見積 (h)' }), f.estimate)),
        el('div', { class: 'field' }, el('label', { text: 'メモ' }), f.description),
        el('div', { class: 'field' },
          el('label', { text: 'まとめる親タスク' }), f.parent,
          el('div', { class: 'hint',
            text: '指定すると、毎回のタスクがその子として作られます。'
              + 'ガントでは親を折りたたんで1行にでき、進捗も自動で集計されます。' })),
        el('div', { class: 'field' },
          el('label', { class: 'check' }, f.active, el('span', { text: '有効にする' }))));
    },
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', {
        class: 'btn btn-primary',
        onClick: async (event) => {
          const button = event.currentTarget;
          const payload = {
            title: f.title.value.trim(),
            description: f.description.value,
            category: f.category.value,
            assignee_id: f.assignee.value ? Number(f.assignee.value) : null,
            priority: Number(f.priority.value),
            estimate_hours: f.estimate.value === '' ? null : Number(f.estimate.value),
            freq: state.freq,
            interval_n: Number(f.interval.value || 1),
            weekdays: [...state.weekdays].sort().join(','),
            month_day: f.monthDay ? Number(f.monthDay.value) : null,
            lead_days: Number(f.lead.value || 0),
            next_on: f.next.value,
            parent_id: f.parent.value ? Number(f.parent.value) : null,
            active: f.active.checked,
          };
          if (!payload.title) { toast('タスク名を入力してください', 'error'); return; }
          button.disabled = true;
          try {
            if (rule) await api.patch(`/api/recurrences/${rule.id}`, payload);
            else await api.post(`/api/projects/${project.id}/recurrences`, payload);
            toast('保存しました', 'ok');
            close(true);
          } catch (error) {
            toast(error.message, 'error');
            button.disabled = false;
          }
        },
      }, '保存'),
    ],
  });
}
