/* 共有リンク集。全体で使うものと、プロジェクトごとのものをまとめて出す。 */
import { api } from '../api.js';
import { setHeader } from '../app.js';
import { store } from '../store.js';
import { confirmDialog, el, fill, openModal, skeleton, toast } from '../util.js';
import { icon, iconLabel } from '../icons.js';

const VIEW_KEY = 'tm.links.view';
const UNCATEGORIZED = '';

/** リンクの種類。行の左に出すアイコンで、開く前に何が開くか分かるようにする。 */
function linkKind(url) {
  const value = String(url || '').toLowerCase();
  if (value.startsWith('mailto:')) return { icon: 'mail', label: 'メール', tone: 'mail' };
  if (value.startsWith('\\\\') || value.startsWith('file:')) {
    return { icon: 'folder', label: '共有フォルダ', tone: 'folder' };
  }
  if (/\.(pdf|docx?|xlsx?|pptx?|vsdx?|csv|txt|zip)(\?|#|$)/.test(value)) {
    return { icon: 'file', label: 'ファイル', tone: 'file' };
  }
  return { icon: 'globe', label: 'Web', tone: 'web' };
}

/** URL を短く見せる（https:// と末尾の / を落とす）。 */
function shortUrl(url) {
  return String(url || '').replace(/^https?:\/\//i, '').replace(/^mailto:/i, '').replace(/\/$/, '');
}

function loadView() {
  try { return localStorage.getItem(VIEW_KEY) === 'scope' ? 'scope' : 'category'; } catch { return 'category'; }
}

export async function render(container) {
  setHeader('リンク集');
  const state = { data: null, query: '', view: loadView(), category: null };

  const search = el('input', {
    class: 'input', type: 'search', placeholder: 'タイトル・URL・補足で絞り込む',
    style: { maxWidth: '260px' },
    onInput: (event) => { state.query = event.target.value.trim().toLowerCase(); draw(); },
  });
  const listHost = el('div', {});
  const chipsHost = el('div', { class: 'link-chips' });

  const addButton = el('button', {
    class: 'btn btn-primary',
    onClick: () => edit(null),
  }, ...iconLabel('plus', 'リンクを追加'));

  const viewSeg = el('div', { class: 'seg' }, ...[
    ['category', '分類ごと'], ['scope', '置き場所ごと'],
  ].map(([value, label]) => el('button', {
    type: 'button', class: state.view === value ? 'active' : '',
    title: value === 'category'
      ? '分類でまとめます（全体とプロジェクトのものを同じ分類に並べます）'
      : '全体・プロジェクトごとにまとめます',
    onClick: (event) => {
      state.view = value;
      try { localStorage.setItem(VIEW_KEY, value); } catch { /* 覚えなくてよい */ }
      [...viewSeg.children].forEach((b) => b.classList.toggle('active', b === event.currentTarget));
      draw();
    },
  }, label)));

  fill(container,
    el('div', { class: 'link-page' },
      el('div', { class: 'link-toolbar' },
        el('div', { class: 'page-sub grow', style: { margin: '0' },
          text: '社内の手順書や共有フォルダなど、よく開くものを置いておく場所です。' }),
        viewSeg, search, addButton),
      chipsHost,
      listHost));

  async function load() {
    fill(listHost, skeleton('rows', 4));
    try {
      state.data = await api.get('/api/links');
      draw();
    } catch (error) {
      fill(listHost, el('div', { class: 'empty', text: error.message }));
    }
  }

  const categoryName = (cat) => cat || '未分類';
  /** 分類の並び。名前順で、未分類はいちばん後ろ。 */
  const byCategory = (a, b) => {
    if (!a !== !b) return a ? -1 : 1;
    return a.localeCompare(b, 'ja');
  };

  /** 分類の絞り込みボタン。件数つきで並べ、押すとその分類だけにする。 */
  function drawChips(links) {
    const counts = new Map();
    for (const link of links) {
      const cat = link.category || UNCATEGORIZED;
      counts.set(cat, (counts.get(cat) || 0) + 1);
    }
    if (state.category !== null && !counts.has(state.category)) state.category = null;
    const chip = (value, label, count) => el('button', {
      type: 'button', class: `link-chip${state.category === value ? ' active' : ''}`,
      onClick: () => { state.category = state.category === value ? null : value; draw(); },
    }, el('span', { text: label }), el('span', { class: 'n', text: String(count) }));
    fill(chipsHost,
      counts.size > 1
        ? [chip(null, 'すべて', links.length),
          ...[...counts.keys()].sort(byCategory).map((cat) =>
            chip(cat, categoryName(cat), counts.get(cat)))]
        : []);
  }

  function draw() {
    const data = state.data;
    if (!data) return;
    addButton.hidden = !data.can_add_shared && !data.projects.length;
    const match = (link) => !state.query
      || `${link.title} ${link.url} ${link.note}`.toLowerCase().includes(state.query);
    const found = data.links.filter(match);
    drawChips(found);
    const links = state.category === null
      ? found : found.filter((link) => (link.category || UNCATEGORIZED) === state.category);
    if (!links.length) {
      fill(listHost, el('div', { class: 'card' }, el('div', { class: 'empty' },
        el('div', { class: 'big', text: '🔗' }),
        el('div', { text: state.query ? '一致するリンクがありません' : 'まだリンクがありません' }),
        state.query || addButton.hidden
          ? null
          : el('button', {
            class: 'btn btn-primary', style: { marginTop: '12px' },
            onClick: () => edit(null),
          }, ...iconLabel('plus', '最初のリンクを追加')))));
      return;
    }
    fill(listHost, el('div', { class: 'link-board' },
      ...(state.view === 'scope' ? scopeCards(links) : categoryCards(links))));
  }

  /** 分類ごと。全体とプロジェクトのリンクを同じ分類のカードに並べる。 */
  function categoryCards(links) {
    const groups = new Map();
    for (const link of links) {
      const cat = link.category || UNCATEGORIZED;
      if (!groups.has(cat)) groups.set(cat, []);
      groups.get(cat).push(link);
    }
    return [...groups.keys()].sort(byCategory).map((cat) => card({
      title: categoryName(cat), muted: !cat, items: groups.get(cat), showScope: true,
    }));
  }

  /** 置き場所ごと。全体 → プロジェクトの順に、カードの中を分類で区切る。 */
  function scopeCards(links) {
    const groups = new Map();
    for (const link of links) {
      const key = link.project_id ? String(link.project_id) : '';
      if (!groups.has(key)) {
        groups.set(key, {
          title: link.project_id ? link.project_name : '全体で共有',
          color: link.project_id ? link.project_color : null, items: [],
        });
      }
      groups.get(key).items.push(link);
    }
    return [...groups.values()].map((group) => card({
      title: group.title, color: group.color, items: group.items, byCategory: true,
    }));
  }

  function card({ title, color = null, muted = false, items, showScope = false, byCategory: split = false }) {
    let body = items.map((link) => row(link, showScope));
    if (split) {
      const cats = new Map();
      for (const link of items) {
        const cat = link.category || UNCATEGORIZED;
        if (!cats.has(cat)) cats.set(cat, []);
        cats.get(cat).push(link);
      }
      // 分類が 1 つだけなら、見出しを挟まない
      body = cats.size > 1 || !cats.has(UNCATEGORIZED)
        ? [...cats.keys()].sort(byCategory).flatMap((cat) => [
          el('div', { class: 'link-subhead', text: categoryName(cat) }),
          ...cats.get(cat).map((link) => row(link, false))])
        : body;
    }
    return el('section', { class: `link-card${muted ? ' muted' : ''}` },
      el('header', { class: 'link-card-head' },
        color ? el('span', { class: 'dot', style: { background: color } }) : null,
        el('span', { class: 'name', text: title }),
        el('span', { class: 'n', text: `${items.length}` })),
      el('div', { class: 'link-card-body' }, ...body));
  }

  function row(link, showScope) {
    const kind = linkKind(link.url);
    return el('div', { class: 'link-item' },
      el('span', { class: `link-ico ${kind.tone}`, title: kind.label }, icon(kind.icon, { size: 16 })),
      el('a', {
        class: 'link-main', href: link.url, target: '_blank', rel: 'noopener noreferrer',
        title: link.url,
      },
      el('span', { class: 'link-title', text: link.title }),
      // 2 行目：どのプロジェクトのものか（分類ごとの表示のとき）と、補足か URL
      el('span', { class: 'link-meta' },
        showScope && link.project_id
          ? el('span', { class: 'link-scope', title: `${link.project_name} のメンバーだけに見えます` },
            el('i', { style: { background: link.project_color } }), link.project_name)
          : null,
        el('span', { class: link.note ? 'link-note' : 'link-url',
          text: link.note || shortUrl(link.url) }))),
      el('div', { class: 'link-actions' },
        el('button', {
          class: 'icon-btn', title: 'URL をコピー',
          onClick: async () => {
            try {
              await navigator.clipboard.writeText(link.url);
              toast('コピーしました', 'ok');
            } catch { toast('コピーできませんでした', 'error'); }
          },
        }, icon('copy', { size: 15 })),
        link.can_edit
          ? el('button', { class: 'icon-btn', title: '編集', onClick: () => edit(link) },
            icon('pencil', { size: 15 }))
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
          }, icon('trash', { size: 15 }))
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
      el('option', { value: '', selected: link && !link.project_id ? true : null },
        '全体で共有'),
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
            text: '全体で共有すると全員に見えます（直せるのは置いた人と管理者です）。'
              + 'プロジェクトを選ぶと、そのメンバーだけに見えます。' }))),
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
