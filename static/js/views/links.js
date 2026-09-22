/* 共有リンク集。全体で使うものと、プロジェクトごとのものをまとめて出す。 */
import { api } from '../api.js';
import { setHeader } from '../app.js';
import { store } from '../store.js';
import { confirmDialog, el, fill, openModal, skeleton, toast } from '../util.js';
import { iconLabel } from '../icons.js';

export async function render(container) {
  setHeader('リンク集');
  const state = { data: null, query: '' };

  const search = el('input', {
    class: 'input', type: 'search', placeholder: 'タイトル・URL で絞り込む',
    style: { maxWidth: '260px' },
    onInput: (event) => { state.query = event.target.value.trim().toLowerCase(); draw(); },
  });
  const listHost = el('div', {});

  const addButton = el('button', {
    class: 'btn btn-primary',
    onClick: () => edit(null),
  }, ...iconLabel('plus', 'リンクを追加'));

  fill(container,
    el('div', { class: 'card' },
      el('div', { class: 'card-head' },
        el('div', { class: 'grow' },
          el('div', { class: 'page-sub', style: { margin: '0' },
            text: '社内の手順書や共有フォルダなど、よく開くものを置いておく場所です。'
              + '全体で共有するものと、プロジェクトごとのものを分けて置けます。' })),
        search, addButton),
      el('div', { class: 'card-body' }, listHost)));

  async function load() {
    fill(listHost, skeleton('rows', 4));
    try {
      state.data = await api.get('/api/links');
      draw();
    } catch (error) {
      fill(listHost, el('div', { class: 'empty', text: error.message }));
    }
  }

  function draw() {
    const data = state.data;
    if (!data) return;
    addButton.hidden = !data.can_add_shared && !data.projects.length;
    const match = (link) => !state.query
      || `${link.title} ${link.url} ${link.note}`.toLowerCase().includes(state.query);
    const links = data.links.filter(match);
    if (!links.length) {
      fill(listHost, el('div', { class: 'empty' },
        el('div', { class: 'big', text: '🔗' }),
        el('div', { text: state.query ? '一致するリンクがありません' : 'まだリンクがありません' }),
        state.query || addButton.hidden
          ? null
          : el('button', {
            class: 'btn btn-primary', style: { marginTop: '12px' },
            onClick: () => edit(null),
          }, ...iconLabel('plus', '最初のリンクを追加'))));
      return;
    }
    // 全体 → プロジェクトごと に分け、その中をさらに分類でまとめる
    const groups = new Map();
    for (const link of links) {
      const key = link.project_id ? String(link.project_id) : '';
      if (!groups.has(key)) {
        groups.set(key, {
          label: link.project_id ? link.project_name : '全体で共有',
          color: link.project_id ? link.project_color : 'var(--accent)',
          buckets: new Map(),
        });
      }
      const buckets = groups.get(key).buckets;
      const cat = link.category || '';
      if (!buckets.has(cat)) buckets.set(cat, []);
      buckets.get(cat).push(link);
    }
    fill(listHost, ...[...groups.values()].map((group) => {
      const count = [...group.buckets.values()].reduce((n, list) => n + list.length, 0);
      return el('div', { class: 'link-group' },
        el('div', { class: 'link-group-head' },
          el('span', { class: 'dot', style: { background: group.color } }),
          el('span', { text: group.label }),
          el('span', { class: 'hint', text: `${count} 件` })),
        ...[...group.buckets.entries()].map(([cat, items]) => el('div', {},
          // 分類が 1 つも付いていないときは、余計な見出しを出さない
          group.buckets.size > 1 || cat
            ? el('div', { class: 'link-cat' },
              el('span', { text: cat || 'その他' }),
              el('span', { class: 'hint', text: `${items.length}` }))
            : null,
          ...items.map(row))));
    }));
  }

  function row(link) {
    return el('div', { class: 'link-row' },
      el('a', {
        class: 'link-main', href: link.url, target: '_blank', rel: 'noopener noreferrer',
        title: link.url,
      },
      el('span', { class: 'link-title', text: link.title }),
      el('span', { class: 'link-url', text: link.url })),
      link.note ? el('span', { class: 'link-note', text: link.note }) : null,
      el('div', { class: 'link-actions' },
        el('button', {
          class: 'icon-btn', title: 'URL をコピー',
          onClick: async () => {
            try {
              await navigator.clipboard.writeText(link.url);
              toast('コピーしました', 'ok');
            } catch { toast('コピーできませんでした', 'error'); }
          },
        }, '⧉'),
        link.can_edit
          ? el('button', { class: 'icon-btn', title: '編集', onClick: () => edit(link) }, '✏️')
          : null,
        link.can_edit
          ? el('button', {
            class: 'icon-btn', title: '削除',
            onClick: async () => {
              if (!await confirmDialog(`「${link.title}」を削除しますか？`,
                { danger: true, okLabel: '削除する' })) return;
              await api.del(`/api/links/${link.id}`);
              load();
            },
          }, '🗑')
          : null));
  }

  async function edit(link) {
    const data = state.data;
    const title = el('input', { class: 'input', value: link?.title || '',
      placeholder: '例）ネットワーク構成図' });
    const url = el('input', { class: 'input', value: link?.url || '',
      placeholder: 'https://… または \\\\server\\share\\…' });
    const note = el('input', { class: 'input', value: link?.note || '',
      placeholder: '補足（任意）' });
    // 表記ゆれを避けるため、すでに使われている分類を候補に出す
    const listId = 'link-cats';
    const category = el('input', {
      class: 'input', value: link?.category || '', maxlength: 40, list: listId,
      placeholder: '例）手順書、共有フォルダ、申請（任意）',
    });
    const suggestions = el('datalist', { id: listId },
      ...(data.categories || []).map((c) => el('option', { value: c })));
    const scope = el('select', { class: 'select' },
      data.can_add_shared
        ? el('option', { value: '', selected: link && !link.project_id ? true : null },
          '全体で共有')
        : null,
      ...data.projects.map((p) => el('option', {
        value: String(p.id), selected: String(link?.project_id || '') === String(p.id) ? true : null,
      }, p.name)));

    const saved = await openModal({
      title: link ? 'リンクを編集' : 'リンクを追加',
      build: () => el('div', {},
        el('div', { class: 'field' }, el('label', { text: 'タイトル *' }), title),
        el('div', { class: 'field' }, el('label', { text: 'URL *' }), url,
          el('div', { class: 'hint',
            text: '社内ファイルサーバーのパスも登録できます（環境により開けない場合があります）。' })),
        el('div', { class: 'field' }, el('label', { text: '補足' }), note),
        el('div', { class: 'field' }, el('label', { text: '分類' }), category, suggestions,
          el('div', { class: 'hint',
            text: '同じ言葉を使うとまとまります。入力欄で既存の分類から選べます。' })),
        el('div', { class: 'field' }, el('label', { text: '公開範囲' }), scope,
          el('div', { class: 'hint',
            text: '全体で共有すると全員に見えます。プロジェクトを選ぶと、'
              + 'そのメンバーだけに見えます。' }))),
      footer: (close) => [
        el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
        el('button', {
          class: 'btn btn-primary',
          onClick: async (event) => {
            const button = event.currentTarget;
            const payload = {
              title: title.value.trim(),
              url: url.value.trim(),
              note: note.value.trim(),
              category: category.value.trim(),
              project_id: scope.value ? Number(scope.value) : null,
            };
            if (!payload.title) { toast('タイトルを入力してください', 'error'); return; }
            if (!payload.url) { toast('URL を入力してください', 'error'); return; }
            button.disabled = true;
            try {
              if (link) await api.patch(`/api/links/${link.id}`, payload);
              else await api.post('/api/links', payload);
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
    if (saved) load();
  }

  await load();
}
