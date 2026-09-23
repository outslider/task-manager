/* 画面の枠まわりで使う線画アイコン。
 *
 * 絵文字をやめた理由は 2 つ。OS ごとに絵が変わって同じ画面が人によって
 * 違う顔になること、多色なのでアクセントカラーと喧嘩することの 2 つ。
 * こちらは currentColor で描くので、文字と同じ色に揃う。
 *
 * ただし「利用者が選んだ絵文字」は絵文字のまま残す。窓口のアイコン、
 * カテゴリの記号、ガントのマーカーは中身であって枠ではないため。
 */
import { el } from './util.js';

// 24×24 の座標で描く。線は currentColor、太さは 1.75。
const PATHS = {
  sun: ['<circle cx="12" cy="12" r="4"/>',
    '<path d="M12 2.6v2.1M12 19.3v2.1M4.4 4.4l1.5 1.5M18.1 18.1l1.5 1.5'
    + 'M2.6 12h2.1M19.3 12h2.1M4.4 19.6l1.5-1.5M18.1 5.9l1.5-1.5"/>'],
  check: ['<circle cx="12" cy="12" r="8.6"/>', '<path d="m8.2 12.3 2.6 2.6 5-5.4"/>'],
  note: ['<path d="M5.5 4.5h13v15h-13z"/>',
    '<path d="M8.8 9.2h6.4M8.8 12.8h6.4M8.8 16.4h3.8"/>'],
  folder: ['<path d="M3.5 6.8a1.8 1.8 0 0 1 1.8-1.8h3.4l2 2.2h8a1.8 1.8 0 0 1 1.8 1.8v8.2'
    + 'a1.8 1.8 0 0 1-1.8 1.8H5.3a1.8 1.8 0 0 1-1.8-1.8z"/>'],
  chart: ['<path d="M4 4.5v15h15.5"/>', '<path d="M7.5 8.5h7M7.5 12h10M7.5 15.5h4.5"/>'],
  link: ['<path d="M10.2 13.1a4.3 4.3 0 0 0 6.5.5l2.6-2.6a4.3 4.3 0 0 0-6.1-6.1l-1.5 1.5"/>',
    '<path d="M13.8 10.9a4.3 4.3 0 0 0-6.5-.5l-2.6 2.6a4.3 4.3 0 0 0 6.1 6.1l1.5-1.5"/>'],
  pin: ['<path d="M9.2 3.5h5.6l-.9 5.6 2.8 2.8H7.3l2.8-2.8z"/>', '<path d="M12 11.9v8.6"/>'],
  ticket: ['<path d="M4 8.4A1.6 1.6 0 0 1 5.6 6.8h12.8A1.6 1.6 0 0 1 20 8.4v1.4'
    + 'a2.2 2.2 0 0 0 0 4.4v1.4a1.6 1.6 0 0 1-1.6 1.6H5.6A1.6 1.6 0 0 1 4 15.6v-1.4'
    + 'a2.2 2.2 0 0 0 0-4.4z"/>',
  '<path stroke-dasharray="2.6 2.6" d="M14.2 7.6v8.8"/>'],
  bell: ['<path d="M17.6 9.6a5.6 5.6 0 1 0-11.2 0c0 4.4-1.8 5.6-1.8 5.6h14.8s-1.8-1.2-1.8-5.6"/>',
    '<path d="M13.6 18.4a1.9 1.9 0 0 1-3.2 0"/>'],
  trash: ['<path d="M4.4 6.9h15.2"/>',
    '<path d="M6.5 6.9 7.6 20h8.8l1.1-13.1M9.4 6.9V4.2h5.2v2.7"/>',
    '<path d="M10.3 10.5v5.8M13.7 10.5v5.8"/>'],
  users: ['<circle cx="9.4" cy="7.6" r="3.4"/>',
    '<path d="M3.4 19.8v-1.3a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v1.3"/>',
    '<path d="M16.6 4.6a3.4 3.4 0 0 1 0 6M17.8 14.7a4 4 0 0 1 2.8 3.8v1.3"/>'],
  tag: ['<path d="M19.8 12.6 12.4 20 4.2 11.8V4.2h7.6z"/>', '<circle cx="8.4" cy="8.4" r="1.3"/>'],
  palette: ['<path d="M12 3.6a8.4 8.4 0 1 0 0 16.8 1.9 1.9 0 0 0 1.9-1.9c0-.5-.2-.9-.5-1.2'
    + 'a1.8 1.8 0 0 1 1.3-3.1h1.8a4.1 4.1 0 0 0 4.1-4.1c0-3.6-3.9-6.5-8.6-6.5z"/>',
  '<circle cx="8" cy="10" r="1.1"/><circle cx="12" cy="7.6" r="1.1"/>'
    + '<circle cx="16" cy="9.6" r="1.1"/>'],
  inbox: ['<path d="M3.8 13.4h3.9l1.7 2.6h5.2l1.7-2.6h3.9"/>',
    '<path d="M3.8 13.4 6.6 4.8h10.8l2.8 8.6v4.6a1.8 1.8 0 0 1-1.8 1.8H5.6'
    + 'a1.8 1.8 0 0 1-1.8-1.8z"/>'],
  gear: ['<path d="M4.2 7.2h5.4M13.4 7.2h6.4M4.2 12h9.6M17.6 12h2.2'
    + 'M4.2 16.8h3.4M11.4 16.8h8.4"/>',
  '<circle cx="11.5" cy="7.2" r="1.9"/><circle cx="15.5" cy="12" r="1.9"/>'
    + '<circle cx="9.5" cy="16.8" r="1.9"/>'],
  user: ['<circle cx="12" cy="8.2" r="3.7"/>',
    '<path d="M5 20.2v-1.1a5 5 0 0 1 5-5h4a5 5 0 0 1 5 5v1.1"/>'],
  plus: ['<path d="M12 5.2v13.6M5.2 12h13.6"/>'],
  close: ['<path d="M6.4 6.4l11.2 11.2M17.6 6.4 6.4 17.6"/>'],
  search: ['<circle cx="10.8" cy="10.8" r="6.4"/>', '<path d="m15.6 15.6 4 4"/>'],
  download: ['<path d="M12 3.8v11.4M7.6 11l4.4 4.2 4.4-4.2"/>', '<path d="M4.6 19.4h14.8"/>'],
  upload: ['<path d="M12 15.6V4.2M7.6 8.4 12 4.2l4.4 4.2"/>', '<path d="M4.6 19.4h14.8"/>'],
  copy: ['<path d="M9 9.2a1.8 1.8 0 0 1 1.8-1.8h7.4A1.8 1.8 0 0 1 20 9.2v7.4'
    + 'a1.8 1.8 0 0 1-1.8 1.8h-7.4A1.8 1.8 0 0 1 9 16.6z"/>',
  '<path d="M15 7.4V5.6a1.8 1.8 0 0 0-1.8-1.8H5.8A1.8 1.8 0 0 0 4 5.6v7.4'
    + 'a1.8 1.8 0 0 0 1.8 1.8h1.8"/>'],
  blocks: ['<path d="M4.2 4.2h6.2v6.2H4.2zM13.6 4.2h6.2v6.2h-6.2zM4.2 13.6h6.2v6.2H4.2z"/>',
    '<path d="M16.7 13.6v6.2M13.6 16.7h6.2"/>'],
  repeat: ['<path d="M4.6 11.2V10a4.4 4.4 0 0 1 4.4-4.4h9.2"/>',
    '<path d="m15.4 2.6 3.4 3-3.4 3"/>',
    '<path d="M19.4 12.8V14a4.4 4.4 0 0 1-4.4 4.4H5.8"/>',
    '<path d="m8.6 21.4-3.4-3 3.4-3"/>'],
  pencil: ['<path d="m14.6 5.2 4.2 4.2M4.6 19.4l.9-4 10-10 3.1 3.1-10 10z"/>'],
  eye: ['<path d="M2.8 12s3.6-6 9.2-6 9.2 6 9.2 6-3.6 6-9.2 6-9.2-6-9.2-6z"/>',
    '<circle cx="12" cy="12" r="2.7"/>'],
  calendar: ['<path d="M4.4 7.4a1.8 1.8 0 0 1 1.8-1.8h11.6a1.8 1.8 0 0 1 1.8 1.8v10.4'
    + 'a1.8 1.8 0 0 1-1.8 1.8H6.2a1.8 1.8 0 0 1-1.8-1.8z"/>',
  '<path d="M4.4 10.2h15.2M8.6 3.6v3.4M15.4 3.6v3.4"/>'],
  bolt: ['<path d="M13.4 2.8 4.8 13.4h6L10.6 21.2l8.6-10.6h-6z"/>'],
  screen: ['<path d="M3.4 5.4a1.6 1.6 0 0 1 1.6-1.6h14a1.6 1.6 0 0 1 1.6 1.6v9.2'
    + 'a1.6 1.6 0 0 1-1.6 1.6H5a1.6 1.6 0 0 1-1.6-1.6z"/>',
  '<path d="M9.4 20.2h5.2M12 16.2v4"/>'],
  menu: ['<path d="M4.4 7h15.2M4.4 12h15.2M4.4 17h15.2"/>'],
  power: ['<path d="M12 3.4v8.2"/>',
    '<path d="M7.4 6.4a7.4 7.4 0 1 0 9.2 0"/>'],
  list: ['<path d="M4.2 6.6h15.6M4.2 12h15.6M4.2 17.4h10.4"/>'],
  gauge: ['<path d="M4 17.6a8.6 8.6 0 1 1 16 0"/>',
    '<path d="m12 17.6 3.6-5.4"/>', '<circle cx="12" cy="17.6" r="1.1"/>'],
  block: ['<circle cx="12" cy="12" r="8.4"/>', '<path d="m6.6 6.6 10.8 10.8"/>'],
  filter: ['<path d="M3.8 5.4h16.4l-6.4 7.4v5.8l-3.6 2v-7.8z"/>'],
  expand: ['<path d="m8.4 9.6 3.6-3.6 3.6 3.6M8.4 14.4l3.6 3.6 3.6-3.6"/>'],
  collapse: ['<path d="m8.4 6.6 3.6 3.6 3.6-3.6M8.4 17.4l3.6-3.6 3.6 3.6"/>'],
  mail: ['<path d="M4 7a1.8 1.8 0 0 1 1.8-1.8h12.4A1.8 1.8 0 0 1 20 7v10a1.8 1.8 0 0 1-1.8 1.8H5.8'
    + 'A1.8 1.8 0 0 1 4 17z"/>', '<path d="m4.6 7.4 7.4 5.4 7.4-5.4"/>'],
  file: ['<path d="M6.4 3.8h7.4l4 4v11.4a1.2 1.2 0 0 1-1.2 1.2H6.4a1.2 1.2 0 0 1-1.2-1.2V5'
    + 'a1.2 1.2 0 0 1 1.2-1.2z"/>', '<path d="M13.6 3.8v4.2h4.2M8.6 13h6.8M8.6 16.4h4.4"/>'],
  globe: ['<circle cx="12" cy="12" r="8.4"/>',
    '<path d="M3.8 12h16.4M12 3.6c2.4 2.4 3.4 5.2 3.4 8.4s-1 6-3.4 8.4c-2.4-2.4-3.4-5.2-3.4-8.4'
    + 's1-6 3.4-8.4z"/>'],
};

/**
 * 線画アイコンを 1 つ作る。
 * @param {string} name PATHS のキー
 * @param {object} options size（既定 18）、class（追加のクラス）
 */
export function icon(name, { size = 18, class: cls = '' } = {}) {
  const body = PATHS[name];
  const node = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  node.setAttribute('viewBox', '0 0 24 24');
  node.setAttribute('width', size);
  node.setAttribute('height', size);
  node.setAttribute('fill', 'none');
  node.setAttribute('stroke', 'currentColor');
  node.setAttribute('stroke-width', '1.75');
  node.setAttribute('stroke-linecap', 'round');
  node.setAttribute('stroke-linejoin', 'round');
  node.setAttribute('aria-hidden', 'true');
  node.setAttribute('class', `ico-svg ${cls}`.trim());
  // 用意していない名前でも、空の枠だけ返して画面は壊さない
  node.innerHTML = (body || []).join('');
  return node;
}

/**
 * ボタンの中身。アイコンと文字を並べて返す。
 * 例）el('button', { class: 'btn' }, ...iconLabel('upload', '取り込み'))
 */
export function iconLabel(name, text, size = 16) {
  return [icon(name, { size }), el('span', { text })];
}


export function hasIcon(name) {
  return Boolean(PATHS[name]);
}

export const ICON_NAMES = Object.keys(PATHS);
