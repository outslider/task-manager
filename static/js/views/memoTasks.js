/* 会議メモから、やることをまとめて拾って登録する。
 *
 * 読み取りは Claude（使えないときは行ごとの簡易読み取り）。
 * 拾ったものはそのまま登録せず、必ず画面で直せるようにしてから
 * 表計算からの取り込みと同じ API に流す。 */
import { api } from '../api.js';
import { categorySelect, option, userSelect } from './pickers.js';
import { el, fill, openModal, toast } from '../util.js';

const IMPORTANCE = [[0, '低'], [1, '中'], [2, '高'], [3, '最重要']];

export async function openMemoDialog(project) {
  const state = { rows: [], engine: '', read: false };

  const memo = el('textarea', {
    class: 'textarea', rows: 9, spellcheck: 'false',
    placeholder: '会議メモや打ち合わせの記録をそのまま貼り付けてください。\n'
      + '例）\n・移行手順書のレビューを鈴木さんが今週金曜までに\n'
      + '・検証環境の用意は高橋さん、月末まで（優先度高め）',
  });
  const listHost = el('div', {});
  const summary = el('div', { class: 'hint' });
  const noticeHost = el('div', {});
  const problemHost = el('div', {});

  const readButton = el('button', {
    class: 'btn btn-primary',
    onClick: () => read(),
  }, '🔍 やることを拾う');

  async function read() {
    const text = memo.value.trim();
    if (!text) { toast('メモを貼り付けてください', 'error'); return; }
    readButton.disabled = true;
    readButton.textContent = '読み取り中…';
    fill(noticeHost);
    fill(problemHost);
    try {
      const result = await api.post('/api/nl/extract', { text, project_id: project.id });
      state.rows = result.rows.map((row) => ({ ...row, use: true }));
      state.engine = result.engine;
      state.read = true;
      if (result.warning) {
        noticeHost.append(el('div', { class: 'warn-box', text: result.warning }));
      } else if (result.engine === 'rule') {
        noticeHost.append(el('div', { class: 'warn-box',
          text: 'Claude 連携が無効なので、1 行 1 件として読み取りました。'
            + '要らない行はチェックを外してください。' }));
      }
      draw();
    } catch (error) {
      toast(error.message, 'error');
    }
    readButton.disabled = false;
    readButton.textContent = '🔍 やることを拾う';
  }

  function draw() {
    if (!state.rows.length) {
      fill(listHost, el('div', { class: 'empty' },
        el('div', { class: 'big', text: '🗒️' }),
        state.read ? 'やることらしい記述が見つかりませんでした' : ''));
      summary.textContent = '';
      return;
    }
    fill(listHost, el('div', { class: 'memo-rows' }, ...state.rows.map(row)));
    count();
  }

  function count() {
    const picked = state.rows.filter((r) => r.use).length;
    summary.textContent = `${state.rows.length} 件を読み取りました（${picked} 件を登録します）`;
  }

  function row(item) {
    const use = el('input', { type: 'checkbox' });
    use.checked = item.use;
    use.addEventListener('change', () => { item.use = use.checked; count(); });

    const title = el('input', { class: 'input' });
    title.value = item.title;
    title.addEventListener('input', () => { item.title = title.value; });

    const assignee = userSelect(null, { emptyLabel: '未割当' });
    // API は氏名で返すので、名前で選択状態を合わせる
    for (const opt of assignee.options) {
      if (opt.textContent === item.assignee) opt.selected = true;
    }
    assignee.addEventListener('change', () => {
      item.assignee = assignee.selectedOptions[0]?.textContent === '未割当'
        ? '' : assignee.selectedOptions[0]?.textContent || '';
    });

    const due = el('input', { class: 'input', type: 'date' });
    due.value = item.due_date || '';
    due.addEventListener('change', () => { item.due_date = due.value; });

    const category = categorySelect(item.category);
    category.addEventListener('change', () => { item.category = category.value; });

    const priority = el('select', { class: 'select' },
      ...IMPORTANCE.map(([value, label]) => option(value, label, value === item.priority)));
    priority.addEventListener('change', () => { item.priority = Number(priority.value); });

    return el('div', { class: 'memo-row' },
      el('label', { class: 'check memo-use' }, use),
      el('div', { class: 'memo-fields' },
        title,
        el('div', { class: 'memo-sub' }, assignee, due, category, priority),
        item.source
          ? el('div', { class: 'hint memo-source', text: `メモ: ${item.source}` })
          : null));
  }

  function picked() {
    return state.rows
      .filter((r) => r.use && r.title.trim())
      .map((r) => ({
        title: r.title.trim(),
        assignee: r.assignee || '',
        due_date: r.due_date || '',
        category: r.category || '',
        priority: r.priority,
        description: r.description || '',
      }));
  }

  function showProblems(problems, created) {
    fill(problemHost);
    if (!problems.length) {
      problemHost.append(el('div', { class: 'warn-box ok',
        text: `${created} 件をこのまま登録できます` }));
      return;
    }
    problemHost.append(el('div', { class: 'warn-box' },
      el('strong', { text: `${problems.length} 件に確認したいところがあります` }),
      el('ul', {}, ...problems.slice(0, 8).map((p) =>
        el('li', { text: `${p.line} 件目: ${p.message}` })))));
  }

  const saved = await openModal({
    title: `${project.name} — メモから起票`,
    wide: true,
    build: () => el('div', {},
      el('p', { class: 'page-sub',
        text: '会議メモを貼り付けると、やることを拾って一覧にします。'
          + '中身を直してから、要るものだけ登録できます。' }),
      el('div', { class: 'field' }, el('label', { text: 'メモ' }), memo),
      el('div', { style: { marginBottom: '12px' } }, readButton),
      noticeHost, listHost, problemHost, summary),
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', {
        class: 'btn',
        onClick: async (event) => {
          const rows = picked();
          if (!rows.length) { toast('登録するものがありません', 'error'); return; }
          const button = event.currentTarget;
          button.disabled = true;
          try {
            const result = await api.post(`/api/projects/${project.id}/tasks/import`,
              { rows, dry_run: true });
            showProblems(result.problems, result.would_create);
          } catch (error) { toast(error.message, 'error'); }
          button.disabled = false;
        },
      }, '確認する'),
      el('button', {
        class: 'btn btn-primary',
        onClick: async (event) => {
          const rows = picked();
          if (!rows.length) { toast('登録するものがありません', 'error'); return; }
          const button = event.currentTarget;
          button.disabled = true;
          try {
            const result = await api.post(`/api/projects/${project.id}/tasks/import`, { rows });
            toast(`${result.created} 件を登録しました`, 'ok');
            close(true);
          } catch (error) {
            toast(error.message, 'error');
            button.disabled = false;
          }
        },
      }, '登録する'),
    ],
  });
  return saved;
}
