/* ヘルプ：使い方の見取り図（関係の全体図・役割ごとの権限）をいつでも見られるように置く。
 * 図は tools/help-slides で作った 16:9 の画像。機能を変えたらそちらで作り直す。 */
import { setHeader } from '../app.js';
import { store } from '../store.js';
import { el } from '../util.js';
import { icon, iconLabel } from '../icons.js';

const FIGURES = [
  {
    file: 'img/help/relationships.png',
    download: '関係の全体図.png',
    title: 'プロジェクト・タスク・課題・チケット・決定の関係',
    alt: 'プロジェクトの中にタスク・課題・決定があり、チケットは窓口から「タスクにする」「課題にする」で入る。'
      + '課題には紐づくタスクがあり、決まったら決定として記録する。決定は関連するタスク・課題とつながる。',
  },
  {
    file: 'img/help/roles.png',
    download: '役割ごとの権限.png',
    title: '役割ごとにできること・できないこと',
    alt: 'アカウントの種類（管理者・運用管理者・社内ユーザー・社外ユーザー）と、'
      + 'プロジェクトでの役割（プロジェクト管理者・編集可・コメント可・閲覧のみ・社外ユーザー）ごとの、できること・できないことの表。',
  },
];

export async function render(container) {
  setHeader('ヘルプ');
  const guest = store.isGuest();
  const tips = [
    ['search', '探す', '上の検索欄（/ キー）で、タスク・課題・決定・チケットをまとめて探せます。'],
    guest ? null : ['bolt', 'すばやく足す', '「クイック追加」（n キー）で、「明日までに佐藤さん 見積」のように一文でタスクを登録できます。'],
    ['sun', '毎朝の確認', '「今日の確認」に、自分の期限・担当の課題とチケットがまとまります。'],
    ['users', '権限が足りないとき', guest
      ? 'プロジェクトの担当者に相談してください。'
      : 'プロジェクトの中の操作はプロジェクト管理者（メンバーの設定）に、全体の設定は管理者か運用管理者に依頼してください。'],
  ].filter(Boolean);

  container.append(
    el('div', { class: 'page-head' },
      el('div', { class: 'grow' },
        el('div', { class: 'page-sub', text: 'このアプリの全体像と、役割ごとにできることをまとめた図です。画像を押すと原寸で開きます。' }))),
    ...FIGURES.map((f) => el('div', { class: 'card help-figure' },
      el('div', { class: 'card-head' },
        el('h2', {}, f.title),
        el('a', { class: 'btn btn-sm', href: f.file, target: '_blank', rel: 'noopener' }, ...iconLabel('eye', '原寸で開く', 14)),
        el('a', { class: 'btn btn-sm', href: f.file, download: f.download }, ...iconLabel('download', 'ダウンロード', 14))),
      el('div', { class: 'card-body' },
        el('a', { href: f.file, target: '_blank', rel: 'noopener', title: '原寸で開く' },
          el('img', { src: f.file, alt: f.alt, loading: 'lazy', width: 1920, height: 1080 }))))),
    el('div', { class: 'card' },
      el('div', { class: 'card-head' }, el('h2', {}, icon('help', { size: 16 }), '困ったときは')),
      el('div', { class: 'card-body help-tips' },
        ...tips.map(([name, head, text]) => el('div', { class: 'help-tip' },
          el('span', { class: 'help-tip-ico' }, icon(name, { size: 18 })),
          el('div', {}, el('b', { text: head }), el('div', { class: 'hint', text })))))));
}
