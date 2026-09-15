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

/** コメント本文を、@メンバーだけ色を変えて表示する。 */
export function commentText(body, people) {
  const node = el('div', { class: 'comment-text' });
  const text = String(body || '');
  const names = (people || [])
    .map((p) => p.name)
    .filter(Boolean)
    .sort((a, b) => b.length - a.length);
  if (!names.length || !/[@＠]/.test(text)) {
    node.textContent = text;
    return node;
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
      node.append(rest.slice(0, at + 1));
      rest = after;
      continue;
    }
    const label = after.startsWith(hit) ? hit : hit.replace(/\s|　/g, '');
    if (at) node.append(rest.slice(0, at));
    node.append(el('span', { class: 'mention', text: `@${label}` }));
    rest = after.slice(label.length);
  }
  if (rest) node.append(rest);
  return node;
}
