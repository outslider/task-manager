/* 会議室モード。画面をそのまま会議室のモニタに映すための表示。
 *
 * 資料にするなら PowerPoint 出力があるので、こちらは「その場で動かしながら
 * 見せる」ほうを担当する。やることは 3 つだけ ——
 * 余計な枠を消す、文字を大きくする、薄い色をやめる。
 *
 * 一時的な見た目なので設定には保存しない。ただし読み込み直しただけで
 * 解けると会議中に困るので、そのタブが開いているあいだは覚えておく。
 */
import { el } from './util.js';
import { icon } from './icons.js';

const KEY = 'tm.present';
let chip = null;

export function isPresenting() {
  return document.documentElement.dataset.present === '1';
}

export function setPresenting(on) {
  const root = document.documentElement;
  if (on) root.dataset.present = '1';
  else delete root.dataset.present;
  try {
    if (on) sessionStorage.setItem(KEY, '1');
    else sessionStorage.removeItem(KEY);
  } catch { /* プライベートウィンドウなどでは覚えなくてよい */ }
  drawChip(on);
  // ガントは幅を見て描いているので、枠が消えたあとに引き直す
  import('./app.js').then((m) => m.refreshRoute());
}

export function togglePresenting() {
  setPresenting(!isPresenting());
}

/** 抜け出す口を必ず 1 つ残す。Esc でも抜けられる。 */
function drawChip(on) {
  if (!on) {
    chip?.remove();
    chip = null;
    return;
  }
  if (chip) return;
  chip = el('button', {
    class: 'present-exit', title: '会議室モードを終わる（Esc）',
    onClick: () => setPresenting(false),
  }, icon('close', { size: 16 }), el('span', { text: '会議室モードを終わる' }));
  document.body.appendChild(chip);
}

/** 起動時に一度だけ呼ぶ。 */
export function initPresenting() {
  let saved = null;
  try { saved = sessionStorage.getItem(KEY); } catch { /* 読めなければ普通の表示 */ }
  if (saved === '1') {
    document.documentElement.dataset.present = '1';
    drawChip(true);
  }
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && isPresenting()) {
      event.preventDefault();
      setPresenting(false);
    }
  });
}
