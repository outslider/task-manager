/* AI（Claude）を使う場所の印。
 *
 * AI を呼ぶボタンには押す前から「AI」の印を付け、AI が作った結果には
 * 「AIの提案」と付ける。押したときだけ呼ぶこと、入力が外部の API に送られること、
 * 結果は確かめてから使うものであることを、同じ見た目でどこでも伝えるため。
 * AI を使えない設定のときは、ボタンの印は出さない（簡易解析で動くので）。
 */
import { el } from './util.js';
import { icon } from './icons.js';
import { store } from './store.js';

export const AI_NOTE = 'Claude（AI）を使います。押したときだけ呼び出し、'
  + '入力した内容は Anthropic の API に送られます。';
const RESULT_NOTE = 'Claude（AI）が作った内容です。確かめてから使ってください。';

/** AI（Claude）が使える設定か。 */
export function aiEnabled() {
  return Boolean(store.meta?.llm_available);
}

/** 「AI」の印。 */
export function aiBadge({ label = 'AI', title = AI_NOTE, small = false } = {}) {
  return el('span', { class: `ai-badge${small ? ' sm' : ''}`, title },
    icon('sparkle', { size: small ? 11 : 12 }), el('span', { text: label }));
}

/** AI を呼ぶボタンに添える印。AI を使えない設定なら何も出さない。 */
export function aiMark(options = {}) {
  return aiEnabled() ? aiBadge({ small: true, ...options }) : null;
}

/**
 * 結果に付ける印。AI が作ったなら「AIの提案」、決まった規則で読み取ったなら「簡易解析」。
 * @param {string} engine サーバーが返す engine（'llm' | 'rule'）
 */
export function engineBadge(engine) {
  if (engine === 'llm') return aiBadge({ label: 'AIの提案', title: RESULT_NOTE });
  return el('span', { class: 'engine-badge', title: 'AI を使わず、決まった規則で読み取りました',
    text: '簡易解析' });
}
