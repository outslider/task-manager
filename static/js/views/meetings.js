/* 定例会議。ガントの 1 行に開催日を点で並べる。
 *
 * ここにあるのは入力まわりだけ（決まりの登録と、1 回ごとの中止・日にち変更）。
 * 開催日の計算はサーバーが持っている。祝日の暦や例外と突き合わせるため、
 * 画面側で同じ計算をもう 1 つ持つと、どこかで食い違うので持たない。 */
import { api } from '../api.js';
import {
  confirmDialog, el, fill, formatDate, openModal, parseDate, toast, today, toISO,
} from '../util.js';
import { markerChar, store } from '../store.js';
import { option } from './pickers.js';
import { buildTree } from './tasks.js';

const WEEKDAYS = ['月', '火', '水', '木', '金', '土', '日'];
const WEEK_INTERVALS = [[1, '毎週'], [2, '隔週'], [3, '3週ごと'], [4, '4週ごと']];
const MONTH_INTERVALS = [[1, '毎月'], [2, '2か月ごと'], [3, '3か月ごと'], [6, '半年ごと']];
const NTHS = [[1, '第1'], [2, '第2'], [3, '第3'], [4, '第4'], [-1, '最終']];
const HOLIDAY_RULES = [
  ['skip', 'その回は休み'],
  ['next', '翌営業日にずらす'],
  ['prev', '前営業日にずらす'],
  ['keep', 'そのまま'],
];
// 週次の定例は祝日なら休み、月次の締め会議はずらす、というのが多い
const DEFAULT_HOLIDAY = { weekly: 'skip', monthly: 'next' };

/** 「9/23(火)」 */
export function dayLabel(iso) {
  return formatDate(iso, true);
}

/**
 * ガントで置く場所の候補。タスクの木の並びのまま、見出しとマイルストーンも含めて出す。
 * 見出しを選ぶと、その区切りの先頭に並ぶ。マイルストーンを選ぶと、その下に並ぶ
 * （「リリース判定会」のように、節目にひもづく打ち合わせがあるため）。
 */
export function placementCandidates(tasks) {
  const { children } = buildTree(tasks);
  const out = [];
  const walk = (parentId, depth) => {
    for (const task of children.get(parentId) || []) {
      let label = task.title;
      if (task.is_heading) label = `【見出し】${task.title}`;
      else if (task.is_milestone) label = `${markerChar(task)} ${task.title}`;
      out.push({ id: task.id, depth, heading: Boolean(task.is_heading), label });
      walk(task.id, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

/** 月曜=0 の曜日番号。Date.getDay() は日曜=0 なので直す。 */
function mondayIndex(date) {
  return (date.getDay() + 6) % 7;
}

/**
 * 定例の登録・編集。保存したら true、消したら 'deleted' を返す。
 * @param {object} project 追加先（編集のときは meeting.project_id を使う）
 * @param {object|null} meeting 編集する定例
 * @param {Array} tasks 置き場所の候補（そのプロジェクトのタスク）
 */
export async function openMeetingForm({ project, meeting = null, tasks = [] }) {
  const now = today();
  const state = {
    freq: meeting?.freq || 'weekly',
    weekdays: new Set(meeting?.weekdays?.length ? meeting.weekdays : [mondayIndex(now)]),
    monthMode: meeting?.month_mode || 'day',
    interval: Number(meeting?.interval_n || 1),
    dates: new Set(meeting?.dates || []),
    // 休日の扱いを自分で選んだら、繰り返しの種類を変えても上書きしない
    holidayTouched: Boolean(meeting),
  };
  const f = {};
  const freqHost = el('div', {});
  const preview = el('div', { class: 'hint meeting-preview' });
  // 「日付を指定」では使わない欄。選んだ日は休日でもずらさず、開始・終了も要らない
  const ruleOnly = [];
  let previewTimer = null;
  let previewSeq = 0;

  const payload = () => ({
    parent_id: f.parent.value ? Number(f.parent.value) : null,
    title: f.title.value.trim(),
    freq: state.freq,
    interval_n: Number(f.interval?.value || 1),
    weekdays: [...state.weekdays].sort(),
    month_mode: state.monthMode,
    month_day: f.monthDay ? Number(f.monthDay.value) || null : null,
    nth: f.nth ? Number(f.nth.value) : null,
    nth_weekday: f.nthWeekday ? Number(f.nthWeekday.value) : null,
    dates: [...state.dates].sort(),
    time_text: f.time.value.trim(),
    holiday_rule: f.holiday.value,
    start_on: f.start.value || null,
    end_on: f.end.value || null,
  });

  /** 入力に合わせて「次は 9/29(火)・10/6(火)…」を出す。 */
  const drawPreview = () => {
    clearTimeout(previewTimer);
    if (state.freq === 'dates' && !state.dates.size) {
      // まだ 1 日も選んでいないうちは、叱らずに空けておく
      previewSeq += 1;
      preview.textContent = '';
      return;
    }
    previewTimer = setTimeout(async () => {
      const seq = ++previewSeq;
      try {
        const data = await api.post('/api/meetings/preview', payload());
        if (seq !== previewSeq) return;             // 古い応答は捨てる
        preview.classList.remove('error');
        const none = state.freq === 'dates'
          ? '選んだ日はすべて過ぎています'
          : 'この決まりでは、この先 1 年ほど開催日がありません';
        preview.textContent = data.next.length
          ? `次の開催: ${data.next.map((n) => dayLabel(n.date)
            + (n.shifted_from ? `（${formatDate(n.shifted_from)}が休日のため）` : '')).join('、')}`
          : none;
      } catch (error) {
        if (seq !== previewSeq) return;
        preview.classList.add('error');
        preview.textContent = error.message;
      }
    }, 250);
  };

  /** 選んだ日の一覧。押すと外せる。 */
  const drawDates = (host) => {
    const sorted = [...state.dates].sort();
    fill(host, ...(sorted.length
      ? sorted.map((iso) => el('button', {
        type: 'button', class: 'date-chip', title: 'この日を外す',
        onClick: () => { state.dates.delete(iso); drawDates(host); drawPreview(); },
      }, el('span', { text: dayLabel(iso) }), el('span', { class: 'x', text: '×' })))
      : [el('span', { class: 'hint', text: 'まだ日付がありません' })]));
  };

  const drawFreq = () => {
    f.monthDay = null;
    f.nth = null;
    f.nthWeekday = null;
    for (const node of ruleOnly) node.hidden = state.freq === 'dates';
    if (state.freq === 'dates') {
      const chips = el('div', { class: 'date-chips' });
      const picker = el('input', { class: 'input', type: 'date', style: { maxWidth: '170px' } });
      const add = () => {
        if (!picker.value) { toast('日付を選んでください', 'error'); return; }
        state.dates.add(picker.value);
        picker.value = '';
        drawDates(chips);
        drawPreview();
      };
      // 日付を選んだらすぐ足す。何日も続けて選べるように、欄は空に戻す
      picker.addEventListener('change', () => { if (picker.value) add(); });
      drawDates(chips);
      fill(freqHost, el('div', { class: 'field' },
        el('label', { text: '開催日（何日でも）' }),
        el('div', { style: { display: 'flex', gap: '8px', alignItems: 'center' } },
          picker, el('span', { class: 'hint', text: '選ぶとその場で足されます' })),
        chips,
        el('div', { class: 'hint',
          text: '1 回だけの会議も、不定期に何回かある会議も、1 行にまとめて並べられます。' })));
      return;
    }
    const intervals = state.freq === 'weekly' ? WEEK_INTERVALS : MONTH_INTERVALS;
    const current = state.interval;
    f.interval = el('select', {
      class: 'select',
      onChange: () => { state.interval = Number(f.interval.value); drawPreview(); },
    },
      ...intervals.map(([value, label]) => option(value, label, value === current)));
    if (!intervals.some(([value]) => value === current)) {
      // 画面の選択肢に無い間隔（5週ごとなど）で保存されていても消さない
      f.interval.appendChild(option(current, `${current}${state.freq === 'weekly' ? '週' : 'か月'}ごと`, true));
    }

    if (state.freq === 'weekly') {
      fill(freqHost, el('div', { class: 'row' },
        el('div', { class: 'field', style: { flex: '0 0 130px' } },
          el('label', { text: '間隔' }), f.interval),
        el('div', { class: 'field' },
          el('label', { text: '曜日' }),
          el('div', { class: 'wd-row' }, ...WEEKDAYS.map((label, index) => el('button', {
            type: 'button', class: `wd-btn${state.weekdays.has(index) ? ' active' : ''}`,
            onClick: (event) => {
              if (state.weekdays.has(index)) state.weekdays.delete(index);
              else state.weekdays.add(index);
              event.currentTarget.classList.toggle('active', state.weekdays.has(index));
              drawPreview();
            },
          }, label))))));
      return;
    }

    f.mode = el('select', {
      class: 'select',
      onChange: () => { state.monthMode = f.mode.value; drawFreq(); drawPreview(); },
    }, option('day', '日にちで決める（毎月◯日）', state.monthMode === 'day'),
    option('nth', '曜日で決める（第◯◯曜日）', state.monthMode === 'nth'));
    let detail;
    if (state.monthMode === 'nth') {
      const startDate = parseDate(f.start?.value) || now;
      f.nth = el('select', { class: 'select', onChange: drawPreview },
        ...NTHS.map(([value, label]) => option(value, label,
          value === Number(meeting?.nth ?? Math.min(4, Math.ceil(startDate.getDate() / 7))))));
      f.nthWeekday = el('select', { class: 'select', onChange: drawPreview },
        ...WEEKDAYS.map((label, index) => option(index, `${label}曜日`,
          index === Number(meeting?.nth_weekday ?? mondayIndex(startDate)))));
      detail = el('div', { style: { display: 'flex', gap: '6px' } }, f.nth, f.nthWeekday);
    } else {
      f.monthDay = el('input', {
        class: 'input', type: 'number', min: 1, max: 31, style: { maxWidth: '90px' },
        value: meeting?.month_day || (parseDate(f.start?.value) || now).getDate(),
      });
      f.monthDay.addEventListener('input', drawPreview);
      detail = el('div', { style: { display: 'flex', gap: '6px', alignItems: 'center' } },
        f.monthDay, el('span', { text: '日' }),
        el('span', { class: 'hint', text: '31 日などが無い月は、月末に行います' }));
    }
    fill(freqHost,
      el('div', { class: 'row' },
        el('div', { class: 'field', style: { flex: '0 0 130px' } },
          el('label', { text: '間隔' }), f.interval),
        el('div', { class: 'field' }, el('label', { text: '決め方' }), f.mode)),
      el('div', { class: 'field' }, el('label', { text: '開催日' }), detail));
  };

  const result = await openModal({
    title: meeting ? '定例を編集' : '定例を追加',
    build: () => {
      f.title = el('input', {
        class: 'input', maxlength: 200, value: meeting?.title || '',
        placeholder: '例）週次定例、ステアリングコミッティ',
      });
      // 置き場所。「開発フェーズの定例」のように、関係するタスクの下に並べられる
      f.parent = el('select', { class: 'select' },
        option('', '先頭の「定例」にまとめる', !meeting?.parent_id),
        ...placementCandidates(tasks).map((t) => option(t.id, `${'　'.repeat(t.depth)}${t.label}`,
          t.id === meeting?.parent_id)));
      if (meeting?.parent_id && ![...f.parent.options].some((o) => Number(o.value) === meeting.parent_id)) {
        // 候補に無いタスク（消えかけの行など）に置いてあっても、黙って外さない
        const current = tasks.find((t) => t.id === meeting.parent_id);
        f.parent.appendChild(option(meeting.parent_id, current?.title || `#${meeting.parent_id}`, true));
      }
      f.freq = el('div', { class: 'seg' }, ...[['weekly', '毎週・隔週'], ['monthly', '毎月'],
        ['dates', '日付を指定']]
        .map(([value, label]) => el('button', {
          type: 'button', class: state.freq === value ? 'active' : '',
          onClick: (event) => {
            if (state.freq !== value) state.interval = 1;
            state.freq = value;
            [...f.freq.children].forEach((b) => b.classList.toggle('active', b === event.currentTarget));
            if (!state.holidayTouched && DEFAULT_HOLIDAY[value]) {
              f.holiday.value = DEFAULT_HOLIDAY[value];
            }
            drawFreq();
            drawPreview();
          },
        }, label)));
      f.time = el('input', {
        class: 'input', maxlength: 20, value: meeting?.time_text || '',
        placeholder: '例）10:00', style: { maxWidth: '140px' },
      });
      f.holiday = el('select', { class: 'select' },
        ...HOLIDAY_RULES.map(([value, label]) => option(value, label,
          value === (meeting?.freq !== 'dates' && meeting?.holiday_rule
            ? meeting.holiday_rule : (DEFAULT_HOLIDAY[state.freq] || 'skip')))));
      f.holiday.addEventListener('change', () => { state.holidayTouched = true; drawPreview(); });
      f.start = el('input', {
        class: 'input', type: 'date',
        value: (meeting?.freq !== 'dates' && meeting?.start_on) || toISO(now),
      });
      f.end = el('input', { class: 'input', type: 'date', value: meeting?.end_on || '' });
      for (const input of [f.start, f.end]) input.addEventListener('change', drawPreview);
      f.time.addEventListener('input', drawPreview);
      const holidayField = el('div', { class: 'field' },
        el('label', { text: '休日に当たったら' }), f.holiday);
      const periodRow = el('div', { class: 'row' },
        el('div', { class: 'field' }, el('label', { text: '開始日' }), f.start),
        el('div', { class: 'field' }, el('label', { text: '終了日（空なら続ける）' }), f.end));
      ruleOnly.push(holidayField, periodRow);
      drawFreq();
      drawPreview();

      return el('div', {},
        el('div', { class: 'field' }, el('label', { text: '会議の名前 *' }), f.title),
        el('div', { class: 'field' }, el('label', { text: 'ガントで置く場所' }), f.parent,
          el('div', { class: 'hint',
            text: 'タスクを選ぶとその子として、見出しを選ぶとその区切りの先頭に並びます。'
              + '選んだタスクや見出しをたたむと一緒に隠れます。' })),
        el('div', { class: 'field' }, el('label', { text: '繰り返し' }), f.freq),
        freqHost,
        el('div', { class: 'row' },
          el('div', { class: 'field', style: { flex: '0 0 150px' } },
            el('label', { text: '時刻' }), f.time),
          holidayField),
        periodRow,
        preview,
        el('div', { class: 'hint', style: { marginTop: '8px' },
          text: '1 回ごとの中止や日にちの変更は、ガントの点を押して行えます。' }));
    },
    footer: (close) => [
      meeting ? el('button', {
        class: 'btn btn-danger', style: { marginRight: 'auto' },
        onClick: async () => {
          if (!await confirmDialog(
            `定例「${meeting.title}」を削除しますか？\n中止・日にち変更の記録も一緒に消えます。`,
            { danger: true, okLabel: '削除する' })) return;
          try {
            await api.del(`/api/meetings/${meeting.id}`);
            toast('削除しました', 'ok');
            close('deleted');
          } catch (error) { toast(error.message, 'error'); }
        },
      }, '削除') : null,
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', {
        class: 'btn btn-primary',
        onClick: async (event) => {
          const body = payload();
          if (!body.title) { toast('会議の名前を入れてください', 'error'); return; }
          if (body.freq === 'weekly' && !body.weekdays.length) {
            toast('曜日を 1 つ以上選んでください', 'error');
            return;
          }
          if (body.freq === 'dates' && !body.dates.length) {
            toast('日付を 1 つ以上選んでください', 'error');
            return;
          }
          const button = event.currentTarget;
          button.disabled = true;
          try {
            if (meeting) await api.patch(`/api/meetings/${meeting.id}`, body);
            else await api.post(`/api/projects/${project.id}/meetings`, body);
            toast(meeting ? '保存しました' : `定例「${body.title}」を追加しました`, 'ok');
            close(true);
          } catch (error) {
            toast(error.message, 'error');
            button.disabled = false;
          }
        },
      }, '保存'),
    ],
  });
  clearTimeout(previewTimer);
  return result;
}

/**
 * 1 回ぶんの操作。中止・日にち変更・元に戻す。変更したら true を返す。
 * occurrence.planned がその回の鍵（休日でずらしたあとの予定日）。
 */
export async function openOccurrenceDialog(meeting, occurrence) {
  const planned = occurrence.planned;
  const f = {};
  const statusText = {
    normal: '予定どおり',
    cancelled: '中止',
    moved: `${dayLabel(planned)} から ${dayLabel(occurrence.date)} に変更`,
  }[occurrence.status];

  const send = async (close, button, body, message) => {
    button.disabled = true;
    try {
      await api.put(`/api/meetings/${meeting.id}/exceptions/${planned}`, body);
      toast(message, 'ok');
      close(true);
    } catch (error) {
      toast(error.message, 'error');
      button.disabled = false;
    }
  };

  return openModal({
    title: `${meeting.title} — ${dayLabel(occurrence.date)}`,
    build: () => {
      f.note = el('input', {
        class: 'input', maxlength: 200, value: occurrence.note || '',
        placeholder: '例）出張のため、全社行事と重なるため',
      });
      f.moveTo = el('input', {
        class: 'input', type: 'date', style: { maxWidth: '170px' },
        value: occurrence.status === 'moved' ? occurrence.date : planned,
      });
      return el('div', {},
        el('p', { class: 'page-sub' },
          `${meeting.summary} の回です。いまの状態: `, el('strong', { text: statusText })),
        occurrence.shifted_from
          ? el('div', { class: 'hint', style: { marginBottom: '10px' },
            text: `本来は ${dayLabel(occurrence.shifted_from)} ですが、休日のためこの日にずらしています。` })
          : null,
        meeting.project_id && occurrence.status !== 'cancelled'
          && (store.project(meeting.project_id)?.tabs || ['decisions']).includes('decisions')
          ? el('div', { class: 'meeting-decision' },
            el('span', { class: 'hint', text: 'この回で決まったことを、意思決定ログに残せます' }),
            el('button', {
              type: 'button', class: 'btn btn-sm',
              onClick: async () => {
                const list = await api.get(`/api/projects/${meeting.project_id}/decisions`);
                const { openDecisionForm } = await import('./decisionForm.js');
                const saved = await openDecisionForm({
                  projectId: meeting.project_id, decisions: list.decisions,
                  prefill: {
                    title: '', status: 'decided', decided_on: occurrence.date,
                    meeting_id: meeting.id, meeting_on: occurrence.date, meeting_title: meeting.title,
                  },
                });
                if (saved) toast(`D-${saved.seq} を記録しました（決定タブで見られます）`, 'ok');
              },
            }, '⚖️ 決定として記録'))
          : null,
        el('div', { class: 'field' }, el('label', { text: 'メモ（理由など）' }), f.note),
        el('div', { class: 'field' },
          el('label', { text: '日にちを変えるなら' }),
          el('div', { style: { display: 'flex', gap: '8px', alignItems: 'center' } },
            f.moveTo, el('span', { class: 'hint', text: 'に変える' }))));
    },
    footer: (close) => [
      occurrence.status !== 'normal' ? el('button', {
        class: 'btn', style: { marginRight: 'auto' },
        title: '中止・日にち変更を取り消して、決まりどおりに戻します',
        onClick: async (event) => {
          const button = event.currentTarget;
          button.disabled = true;
          try {
            await api.del(`/api/meetings/${meeting.id}/exceptions/${planned}`);
            toast(`${dayLabel(planned)} を予定どおりに戻しました`, 'ok');
            close(true);
          } catch (error) {
            toast(error.message, 'error');
            button.disabled = false;
          }
        },
      }, '予定どおりに戻す') : null,
      el('button', {
        class: 'btn',
        onClick: (event) => {
          const target = f.moveTo.value;
          if (!target) { toast('変更先の日付を選んでください', 'error'); return; }
          if (target === planned) {
            toast('元の日と同じです。予定どおりに戻すなら左のボタンを押してください', 'error');
            return;
          }
          send(close, event.currentTarget,
            { action: 'move', moved_to: target, note: f.note.value },
            `${dayLabel(planned)} の回を ${dayLabel(target)} に変えました`);
        },
      }, '日にちを変える'),
      el('button', {
        class: 'btn btn-danger',
        onClick: (event) => send(close, event.currentTarget,
          { action: 'cancel', note: f.note.value },
          `${dayLabel(planned)} の回を中止にしました`),
      }, occurrence.status === 'cancelled' ? 'メモを保存' : 'この回を中止'),
    ],
  });
}

/** 点の吹き出しに出す文。 */
export function occurrenceTitle(meeting, occurrence, projectName = '') {
  const lines = [
    `${projectName ? `${projectName} / ` : ''}${meeting.title}`,
    `${dayLabel(occurrence.date)}${meeting.time_text ? ` ${meeting.time_text}` : ''}`,
  ];
  if (occurrence.status === 'cancelled') lines.push('中止');
  if (occurrence.status === 'moved') lines.push(`${dayLabel(occurrence.planned)} から変更`);
  if (occurrence.shifted_from) lines.push(`${dayLabel(occurrence.shifted_from)} が休日のためずらし`);
  if (occurrence.note) lines.push(occurrence.note);
  return lines.join('\n');
}

