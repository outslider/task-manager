/* マイ ToDo の繰り返し設定。
 *
 * 「毎月1日の締め作業を、3日前に ToDo として出す」のような自分だけの定例。
 * 規則も、そこから出てくる ToDo も本人にしか見えない。
 * 日付の進め方はプロジェクトの定例タスクと同じ仕組みを使っている。 */
import { api } from '../api.js';
import {
  confirmDialog, el, fill, formatDate, openModal,
  skeleton, toast, today, toISO,
} from '../util.js';
import { option } from './pickers.js';

const WEEKDAYS = ['月', '火', '水', '木', '金', '土', '日'];
// ToDo は「毎月◯日」が一番多いので、それを先頭に置いておく
const FREQ = [['monthly', '毎月'], ['weekly', '毎週'], ['daily', '毎日']];

/** 予定日から lead_days 日前を引いた、実際に ToDo が出る日。 */
export function appearsOn(nextOn, leadDays) {
  if (!nextOn) return '';
  const at = new Date(`${nextOn}T00:00:00`);
  at.setDate(at.getDate() - (Number(leadDays) || 0));
  return toISO(at);
}

/** 一覧と編集をまとめたダイアログ。閉じたときに変更があったかを返す。 */
export async function openTodoRecurrences({ onChange } = {}) {
  const listHost = el('div', {});
  let changed = false;

  const load = async () => {
    fill(listHost, skeleton('rows', 3));
    try {
      const data = await api.get('/api/todo-recurrences');
      fill(listHost, ...(data.recurrences.length
        ? data.recurrences.map(row)
        : [el('div', { class: 'empty' },
          el('div', { class: 'big', text: '🔁' }),
          '繰り返しの ToDo はまだありません',
          el('div', { class: 'hint', style: { marginTop: '8px' },
            text: '毎月1日の締め作業、毎週金曜の週報など、'
              + '忘れたくない用事を登録しておくと自動で出てきます。' }))]));
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
        `次は ${formatDate(rule.next_on)} の予定`,
        rule.lead_days
          ? ` ・ ${formatDate(appearsOn(rule.next_on, rule.lead_days))} に出ます`
          + `（${rule.lead_days}日前）`
          : ' ・ 当日に出ます')),
    el('div', { style: { display: 'flex', gap: '6px' } },
      el('button', {
        class: 'btn btn-sm', title: '次回ぶんを今すぐ ToDo にする',
        onClick: async (event) => {
          const button = event.currentTarget;
          button.disabled = true;
          try {
            await api.post(`/api/todo-recurrences/${rule.id}/run`, {});
            toast('ToDo に出しました', 'ok');
            changed = true;
            load();
          } catch (error) {
            toast(error.message, 'error');
            button.disabled = false;
          }
        },
      }, '今すぐ出す'),
      el('button', {
        class: 'btn btn-sm', title: '今回は出さずに、次回へ送る',
        onClick: async (event) => {
          const button = event.currentTarget;
          button.disabled = true;
          try {
            const result = await api.post(`/api/todo-recurrences/${rule.id}/skip`, {});
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
          if (await openTodoRecurrenceForm(rule)) { changed = true; load(); }
        },
      }, '編集'),
      el('button', {
        class: 'icon-btn', title: '削除',
        onClick: async () => {
          if (!await confirmDialog(`「${rule.title}」の繰り返しをやめますか？`,
            { danger: true, okLabel: 'やめる' })) return;
          await api.del(`/api/todo-recurrences/${rule.id}`);
          toast('繰り返しをやめました（出ている ToDo はそのまま残ります）', 'ok');
          changed = true;
          load();
        },
      }, '×')));

  await openModal({
    title: '繰り返しの ToDo',
    wide: true,
    build: () => {
      load();
      return el('div', {},
        el('p', { class: 'page-sub',
          text: '決まった日にやることを登録しておくと、指定した日数だけ前に'
            + 'マイ ToDo へ自動で出てきます。ここも自分だけに見えます。' }),
        listHost);
    },
    footer: (close) => [
      el('button', {
        class: 'btn btn-primary', style: { marginRight: 'auto' },
        onClick: async () => {
          if (await openTodoRecurrenceForm(null)) { changed = true; load(); }
        },
      }, '＋ 繰り返しを追加'),
      el('button', { class: 'btn', onClick: () => close(null) }, '閉じる'),
    ],
  });
  if (changed && onChange) onChange();
  return changed;
}

/** 規則の作成・編集。 */
export async function openTodoRecurrenceForm(rule = null) {
  const now = new Date();
  const state = {
    freq: rule?.freq || 'monthly',
    weekdays: new Set((rule?.weekdays || String(now.getDay() === 0 ? 6 : now.getDay() - 1))
      .split(',').filter((v) => v !== '').map(Number)),
  };
  const f = {};
  const freqExtra = el('div', {});
  const preview = el('div', { class: 'hint' });

  /** 「次は◯日の予定。◯日に出ます」を、入力に合わせて出し続ける。 */
  const drawPreview = () => {
    const at = appearsOn(f.next.value, f.lead.value);
    preview.textContent = f.next.value
      ? (Number(f.lead.value) > 0
        ? `${formatDate(f.next.value)} の予定 → ${formatDate(at)} に ToDo が出ます`
        : `${formatDate(f.next.value)} の当日に ToDo が出ます`)
      : '';
  };

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
        value: rule?.month_day || now.getDate(),
      });
      fill(freqExtra, el('div', { class: 'field' },
        el('label', { text: '毎月の予定日' }), f.monthDay,
        el('div', { class: 'hint', text: 'その日が無い月（31日など）は、月の最終日になります。' })));
    } else {
      fill(freqExtra);
    }
  };

  return openModal({
    title: rule ? '繰り返しを編集' : '繰り返しを追加',
    build: () => {
      f.title = el('input', {
        class: 'input', maxlength: 300, value: rule?.title || '',
        placeholder: '例）月初の締め作業を出す',
      });
      f.note = el('textarea', { class: 'textarea', rows: 2, maxlength: 1000 });
      f.note.value = rule?.note || '';
      f.freq = el('select', { class: 'select' },
        ...FREQ.map(([value, label]) => option(value, label, state.freq === value)));
      f.freq.addEventListener('change', () => { state.freq = f.freq.value; drawFreqExtra(); });
      f.interval = el('input', {
        class: 'input', type: 'number', min: 1, max: 99, value: rule?.interval_n || 1,
      });
      f.lead = el('input', {
        class: 'input', type: 'number', min: 0, max: 60, value: rule?.lead_days ?? 3,
      });
      f.next = el('input', {
        class: 'input', type: 'date', value: rule?.next_on || toISO(today()),
      });
      f.lead.addEventListener('input', drawPreview);
      f.next.addEventListener('input', drawPreview);
      f.next.addEventListener('change', drawPreview);
      f.active = el('input', { type: 'checkbox', checked: rule ? Boolean(rule.active) : true });
      drawFreqExtra();
      drawPreview();

      return el('div', {},
        el('div', { class: 'field' }, el('label', { text: 'やること *' }), f.title),
        el('div', { class: 'row' },
          el('div', { class: 'field' }, el('label', { text: '繰り返し' }), f.freq),
          el('div', { class: 'field' }, el('label', { text: '間隔' }), f.interval,
            el('div', { class: 'hint', text: '1 なら毎回。2 なら1回とばし。' }))),
        freqExtra,
        el('div', { class: 'row' },
          el('div', { class: 'field' }, el('label', { text: '次回の予定日' }), f.next),
          el('div', { class: 'field' }, el('label', { text: '何日前に出すか' }), f.lead)),
        preview,
        el('div', { class: 'field' }, el('label', { text: 'メモ' }), f.note),
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
            note: f.note.value,
            freq: state.freq,
            interval_n: Number(f.interval.value || 1),
            weekdays: [...state.weekdays].sort().join(','),
            month_day: f.monthDay ? Number(f.monthDay.value) : null,
            lead_days: Number(f.lead.value || 0),
            next_on: f.next.value,
            active: f.active.checked,
          };
          if (!payload.title) { toast('やることを入力してください', 'error'); return; }
          button.disabled = true;
          try {
            if (rule) await api.patch(`/api/todo-recurrences/${rule.id}`, payload);
            else await api.post('/api/todo-recurrences', payload);
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
