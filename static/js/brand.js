/* ブランドマーク。アクセントカラーに追随させるため、色は CSS 変数で塗る。 */
import { el, svgEl } from './util.js';
import { store } from './store.js';

let seq = 0;

/**
 * ロゴマーク（角丸の四角＋タスク行＋チェック）。
 * @param {number} size 一辺のピクセル数
 */
export function brandMark(size = 28) {
  const id = `tm-mark-${++seq}`;
  const stop = (offset, variable) => {
    const node = svgEl('stop', { offset });
    node.style.stopColor = `var(${variable})`;
    return node;
  };
  return svgEl('svg', {
    width: size, height: size, viewBox: '0 0 32 32', class: 'brand-mark-svg',
    'aria-hidden': 'true', focusable: 'false',
  },
  svgEl('defs', {},
    svgEl('linearGradient', { id, x1: '0', y1: '0', x2: '1', y2: '1' },
      stop('0', '--accent'), stop('1', '--accent-2'))),
  svgEl('rect', { width: 32, height: 32, rx: 10, fill: `url(#${id})` }),
  svgEl('rect', { x: 7, y: 8.6, width: 11, height: 3, rx: 1.5, fill: '#fff', opacity: '.95' }),
  svgEl('rect', { x: 7, y: 13.6, width: 6.5, height: 3, rx: 1.5, fill: '#fff', opacity: '.55' }),
  svgEl('path', {
    d: 'M12.4 20.6l3.4 3.4L25 14.8', stroke: '#fff', 'stroke-width': 3.4, fill: 'none',
    'stroke-linecap': 'round', 'stroke-linejoin': 'round',
  }));
}

/** マーク＋アプリ名。size は 'sm' | 'md' | 'lg'。 */
export function brandLockup(size = 'md') {
  const marks = { sm: 24, md: 28, lg: 40 };
  return el('div', { class: `brand brand-${size}` },
    brandMark(marks[size] || 28),
    el('span', { class: 'brand-name', text: store.ui.app_name || 'タスク管理' }));
}
