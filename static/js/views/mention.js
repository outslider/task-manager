/* コメント欄の「@メンバー」。
 *
 * 氏名に空白が入る（「佐藤 花子」）ので、単語単位ではなく
 * 「@ から入力中の文字列」を丸ごと候補と突き合わせる。 */
import { el, fill } from '../util.js';

/** @ のあと、名前として読む最大の長さ。 */
const MAX_NAME = 24;

/** 「@」以降の入力中文字列を取り出す。@ が無ければ null。 */
export function pendingMention(text, caret) {
  const before = text.slice(0, caret);
  const at = Math.max(before.lastIndexOf('@'), before.lastIndexOf('＠'));
  if (at < 0) return null;
  const word = before.slice(at + 1);
  if (word.length > MAX_NAME || word.includes('\n')) return null;
  // メールアドレスの途中（a@b）は対象外
  const prev = at > 0 ? before[at - 1] : '';
  if (prev && !/[\s　(（「【]/.test(prev)) return null;
  return { at, word };
}

export function matchPeople(people, word) {
  const key = word.trim().toLowerCase().replace(/\s|　/g, '');
  if (!key) return people.slice(0, 8);
  return people.filter((p) => {
    const name = (p.name || '').toLowerCase().replace(/\s|　/g, '');
    const mail = (p.email || '').toLowerCase();
    return name.includes(key) || mail.includes(key);
  }).slice(0, 8);
}

/**
 * テキストエリアに候補選択をつける。
 * @param {HTMLTextAreaElement} input
 * @param {Function} getPeople 候補（プロジェクトのメンバー）を返す関数
 */
export function attachMentions(input, getPeople) {
  let menu = null;
  let index = 0;
  let shown = [];

  const close = () => {
    if (menu) { menu.remove(); menu = null; }
    shown = [];
  };

  const insert = (person) => {
    const state = pendingMention(input.value, input.selectionStart);
    if (!state) { close(); return; }
    const after = input.value.slice(input.selectionStart);
    const head = input.value.slice(0, state.at);
    const inserted = `@${person.name} `;
    input.value = head + inserted + after;
    const caret = head.length + inserted.length;
    input.setSelectionRange(caret, caret);
    input.focus();
    close();
  };

  const draw = () => {
    if (!shown.length) { close(); return; }
    if (!menu) {
      menu = el('div', { class: 'mention-menu' });
      document.body.appendChild(menu);
    }
    fill(menu, ...shown.map((person, i) => el('button', {
      type: 'button',
      class: `mention-option${i === index ? ' active' : ''}`,
      // blur でメニューが閉じる前に選択を確定させる
      onMouseDown: (event) => { event.preventDefault(); insert(person); },
    },
    el('span', { class: 'mention-name', text: person.name }),
    el('span', { class: 'hint', text: person.email || '' }))));
    const box = input.getBoundingClientRect();
    menu.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - 280))}px`;
    const below = window.innerHeight - box.bottom;
    if (below < 200 && box.top > 200) {
      menu.style.top = 'auto';
      menu.style.bottom = `${window.innerHeight - box.top + 4}px`;
    } else {
      menu.style.bottom = 'auto';
      menu.style.top = `${box.bottom + 4}px`;
    }
  };

  const refresh = () => {
    const state = pendingMention(input.value, input.selectionStart);
    if (!state) { close(); return; }
    shown = matchPeople(getPeople() || [], state.word);
    index = 0;
    draw();
  };

  input.addEventListener('input', refresh);
  input.addEventListener('click', refresh);
  input.addEventListener('blur', () => setTimeout(close, 120));
  input.addEventListener('keydown', (event) => {
    if (!menu || !shown.length) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      index = (index + 1) % shown.length;
      draw();
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      index = (index - 1 + shown.length) % shown.length;
      draw();
    } else if (event.key === 'Enter' || event.key === 'Tab') {
      event.preventDefault();
      event.stopPropagation();
      insert(shown[index]);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      close();
    }
  }, true);

  return { close };
}

/* URL らしき並び。末尾の句読点や閉じ括弧は URL に含めない。 */
const URL_PATTERN = /https?:\/\/[^\s　<>"'）］｝】]+/g;
const TRAILING_JUNK = /[.,;:!?。、）)\]］}｝】”"'…]+$/;

/** 本文の中の URL を、実際に開けるリンクにして返す。 */
export function linkify(text, into) {
  const node = into || el('span', {});
  const source = String(text);
  let last = 0;
  for (const match of source.matchAll(URL_PATTERN)) {
    let url = match[0];
    // 「(https://example.com)」のように囲まれている場合、閉じ括弧は URL ではない
    const opened = (url.match(/\(/g) || []).length;
    const closed = (url.match(/\)/g) || []).length;
    if (closed > opened) url = url.slice(0, url.lastIndexOf(')'));
    url = url.replace(TRAILING_JUNK, '');
    if (!url) continue;
    if (match.index > last) node.append(source.slice(last, match.index));
    node.append(el('a', {
      href: url, target: '_blank', rel: 'noopener noreferrer nofollow',
      class: 'auto-link', title: url, text: shorten(url),
    }));
    last = match.index + url.length;
  }
  if (last < source.length) node.append(source.slice(last));
  return node;
}

/** 長い URL は途中を省いて表示する（行が崩れないように）。 */
function shorten(url) {
  if (url.length <= 60) return url;
  return `${url.slice(0, 40)}…${url.slice(-15)}`;
}

/**
 * メモやコメントの本文を表示用の要素にする。
 * URL はリンクに、@メンバーは色付きにする。改行はそのまま残す。
 */
export function richText(body, people, className = 'comment-text') {
  const node = el('div', { class: className });
  const text = String(body || '');
  const names = (people || [])
    .map((p) => p.name)
    .filter(Boolean)
    .sort((a, b) => b.length - a.length);

  if (!names.length || !/[@＠]/.test(text)) {
    return linkify(text, node);
  }
  let rest = text;
  let guard = 0;
  while (rest && guard < 500) {
    guard += 1;
    const at = rest.search(/[@＠]/);
    if (at < 0) break;
    const after = rest.slice(at + 1);
    const hit = names.find((name) =>
      after.startsWith(name) || after.startsWith(name.replace(/\s|　/g, '')));
    if (hit === undefined) {
      linkify(rest.slice(0, at + 1), node);
      rest = after;
      continue;
    }
    const label = after.startsWith(hit) ? hit : hit.replace(/\s|　/g, '');
    if (at) linkify(rest.slice(0, at), node);
    node.append(el('span', { class: 'mention', text: `@${label}` }));
    rest = after.slice(label.length);
  }
  if (rest) linkify(rest, node);
  return node;
}

/** 以前からの呼び名。中身は richText と同じ。 */
export const commentText = richText;
