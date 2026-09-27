const { ico, C, BASE_CSS } = require('./common.js');
const Y = '<span class="y">○</span>';
const N = '<span class="n">—</span>';
const P = (note) => `<span class="p">△</span><small>${note}</small>`;
const YN = (note) => `<span class="y">○</span><small>${note}</small>`;

const account = {
  cols: [['管理者', '#11151c'], ['運用管理者', '#4f5bd5'], ['社内ユーザー', '#2f6fea'], ['社外ユーザー', '#b25e09']],
  rows: [
    ['見られるプロジェクト', [YN('すべて'), YN('参加分'), YN('参加分'), P('参加分の一部')]],
    ['プロジェクトを作る', [Y, Y, Y, N]],
    ['チケットを起票する', [Y, Y, Y, P('自社のぶん')]],
    ['チケットの担当・状態・期限', [Y, Y, Y, N]],
    ['チケットの削除', [Y, P('自分の起票'), P('自分の起票'), N]],
    ['雛形・負荷・ゴミ箱・AI 機能', [Y, Y, Y, N]],
    ['グループの設定', [Y, Y, N, N]],
    ['ユーザーの追加・編集', [Y, P('社内・社外のみ'), N, N]],
    ['状態とカテゴリ・休業日・窓口', [Y, Y, N, N]],
    ['停止・削除・パスワード再発行', [Y, N, N, N]],
    ['システム設定・ログイン履歴', [Y, N, N, N]],
  ],
};
const project = {
  cols: [['プロジェクト管理者', '#4f5bd5'], ['編集可', '#2f6fea'], ['コメント可', '#12966a'], ['閲覧のみ', '#58637a'], ['社外ユーザー', '#b25e09']],
  rows: [
    ['タスク・ガント・課題・決定を見る', [Y, Y, Y, Y, P('見せるタブのみ')]],
    ['コメントを書く', [Y, Y, Y, N, Y]],
    ['担当タスクの進捗・状態の更新', [Y, Y, Y, Y, Y]],
    ['タスクの追加・編集・削除', [Y, Y, N, N, N]],
    ['課題の起票・編集', [Y, Y, N, N, N]],
    ['決定の記録・編集', [Y, Y, N, N, N]],
    ['チケットをタスク・課題にする', [Y, Y, N, N, N]],
    ['決定の削除', [Y, N, N, N, N]],
    ['メンバーの追加・権限の変更', [Y, N, N, N, N]],
    ['アカウントを作ってメンバーに追加', [P('設定による'), N, N, N, N]],
    ['プロジェクトの設定・削除', [Y, N, N, N, N]],
  ],
};
function table(t, icon, title, sub, color) {
  return `<section style="--c:${color}">
    <div class="sec-head">${ico(icon, 28, color, 2)}<div><h2>${title}</h2><div class="sec-sub">${sub}</div></div></div>
    <table><thead><tr><th></th>${t.cols.map(([n, c]) => `<th style="color:${c}">${n}</th>`).join('')}</tr></thead>
    <tbody>${t.rows.map(([label, cells]) => `<tr><td class="lab">${label}</td>${cells.map((x) => `<td>${x}</td>`).join('')}</tr>`).join('')}</tbody></table>
  </section>`;
}
const html = `<!doctype html><html><head><meta charset="utf-8"><style>
${BASE_CSS}
.wrap { position: absolute; left: 72px; right: 72px; top: 208px; display: grid; grid-template-columns: 790px 1fr; gap: 40px; }
section { background: #fff; border: 2px solid ${C.border}; border-top: 6px solid var(--c); border-radius: 18px; padding: 16px 22px 10px; }
.sec-head { display: flex; gap: 12px; align-items: center; margin-bottom: 6px; }
h2 { font-size: 28px; font-weight: 800; }
.sec-sub { font-size: 16px; color: ${C.muted}; margin-top: 2px; }
table { width: 100%; border-collapse: collapse; table-layout: fixed; }
th { font-size: 16.5px; font-weight: 800; padding: 4px 4px 8px; border-bottom: 2px solid ${C.border}; text-align: center; line-height: 1.25; }
th:first-child { width: 36%; }
td { text-align: center; padding: 0 4px; height: 49px; border-bottom: 1px solid ${C.border}; font-size: 22px; line-height: 1.1; }
tr:nth-child(even) td { background: #fafbfc; }
td.lab { text-align: left; font-size: 18px; font-weight: 700; padding-left: 6px; line-height: 1.3; }
.y { color: ${C.decision}; font-weight: 800; }
.n { color: #c3c9d4; font-weight: 700; }
.p { color: ${C.issue}; font-weight: 800; }
td small { display: block; font-size: 12.5px; color: ${C.muted}; font-weight: 600; margin-top: 3px; line-height: 1.25; }
.legend { display: flex; gap: 22px; font-size: 17px; color: ${C.muted}; margin-top: 12px; }
.notes { position: absolute; left: 72px; right: 72px; bottom: 26px; display: flex; gap: 14px; }
.note { flex: 1; background: ${C.bg}; border-radius: 12px; padding: 10px 14px; font-size: 15px; color: ${C.muted}; line-height: 1.5; display: flex; gap: 10px; }
.note b { color: ${C.ink}; }
.note svg { flex: none; margin-top: 2px; }
</style></head><body>
<div class="head">
  <div class="kicker">タスク管理 ─ 権限の早見表</div>
  <h1>役割ごとにできること・できないこと</h1>
  <div class="sub">権限は 2 段階です。<b>アカウントの種類</b>で全体の設定を、<b>プロジェクトでの役割</b>でプロジェクトの中の操作を決めます。</div>
</div>
<div class="wrap">
  ${table(account, 'users', 'アカウントの種類', '管理 ＞ ユーザー で決める（全体に効く）', '#11151c')}
  ${table(project, 'folder', 'プロジェクトでの役割', 'プロジェクトの「メンバー」で決める（そのプロジェクトだけに効く）', C.project)}
</div>
<div class="notes">
  <div class="note">${ico('check', 20, C.decision, 2)}<div><b>○ できる　△ 条件付き　— できない</b><br>管理者はすべてのプロジェクトで「プロジェクト管理者」と同じことができます。</div></div>
  <div class="note">${ico('users', 20, C.project, 2)}<div><b>個人とグループで役割が違うときは、強い方</b><br>運用管理者・社内ユーザーのプロジェクトの中での操作は、右の表の役割に従います。</div></div>
  <div class="note">${ico('globe', 20, '#b25e09', 2)}<div><b>社外ユーザーの役割は「コメント可」が上限</b><br>見せるタブはプロジェクトごとに選び、ほかの人のメールアドレスは見えません。</div></div>
  <div class="note">${ico('lock', 20, C.muted, 2)}<div><b>マイ ToDo は本人だけ</b><br>管理者にも見えず、管理者の「この人として見る」でも開けません。</div></div>
</div>
</body></html>`;
module.exports = html;
