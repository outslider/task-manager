const { ico, C, BASE_CSS } = require('./common.js');

function card({ x, y, w, h, color, icon, name, what, points, who }) {
  return `<div class="card" style="left:${x}px;top:${y}px;width:${w}px;height:${h}px;--c:${color}">
    <div class="card-top"><span class="badge">${ico(icon, 26, '#fff', 2)}</span>
      <div><div class="name">${name}</div><div class="what">${what}</div></div></div>
    <ul>${points.map((p) => `<li>${p}</li>`).join('')}</ul>
    ${who ? `<div class="who">${who}</div>` : ''}
  </div>`;
}
function label({ x, y, text, color, w = 'auto', align = 'center' }) {
  return `<div class="lbl" style="left:${x}px;top:${y}px;color:${color};text-align:${align};${w !== 'auto' ? `width:${w}px;` : ''}">${text}</div>`;
}

const svgArrows = `
<svg class="arrows" width="1920" height="1080" viewBox="0 0 1920 1080">
  <defs>
    ${['task', 'issue', 'decision', 'ticket', 'muted'].map((k) => `
    <marker id="ah-${k}" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0 0 L10 5 L0 10 z" fill="${k === 'muted' ? C.faint : C[k]}"/></marker>`).join('')}
  </defs>
  <!-- チケット → タスク -->
  <path d="M430 468 H 582" stroke="${C.ticket}" stroke-width="3.5" fill="none" marker-end="url(#ah-ticket)"/>
  <!-- チケット → 課題（上の通路） -->
  <path d="M430 392 H 470 Q 482 392 482 380 V 322 Q 482 310 494 310 H 1240 Q 1252 310 1252 322 V 348" stroke="${C.ticket}" stroke-width="3.5" fill="none" marker-end="url(#ah-ticket)"/>
  <!-- 課題 → タスク（紐づくタスク） -->
  <path d="M1044 470 H 990" stroke="${C.issue}" stroke-width="3.5" fill="none" marker-end="url(#ah-issue)"/>
  <!-- 課題 → 決定 -->
  <path d="M1300 588 V 740 Q 1300 752 1288 752 H 1226" stroke="${C.issue}" stroke-width="3.5" fill="none" marker-end="url(#ah-issue)"/>
  <!-- 決定 ↔ タスク（関連） -->
  <path d="M810 752 H 716 Q 704 752 704 740 V 596" stroke="${C.decision}" stroke-width="3.5" fill="none" marker-start="url(#ah-decision)" marker-end="url(#ah-decision)" stroke-dasharray="0"/>
  <!-- タスクの中：親子・依存 -->
</svg>`;

const html = `<!doctype html><html><head><meta charset="utf-8"><style>
${BASE_CSS}
.arrows { position: absolute; left: 0; top: 0; z-index: 2; pointer-events: none; }
.frame { position: absolute; border-radius: 22px; }
.project { left: 540px; top: 236px; width: 940px; height: 628px; border: 3px solid ${C.project};
  background: linear-gradient(180deg, #f3f4fd 0, #fafaff 100%); }
.project-head { position: absolute; left: 28px; top: -24px; display: flex; align-items: center; gap: 12px;
  background: ${C.project}; color: #fff; padding: 8px 20px 8px 14px; border-radius: 12px; font-size: 25px; font-weight: 800; }
.project-note { position: absolute; right: 26px; top: 16px; font-size: 17px; color: ${C.project}; font-weight: 600; }
.card { position: absolute; z-index: 3; background: #fff; border: 2px solid color-mix(in srgb, var(--c) 35%, #fff);
  border-top: 6px solid var(--c); border-radius: 16px; padding: 18px 22px 16px; box-shadow: 0 2px 0 rgba(16,24,40,.03), 0 10px 24px -14px rgba(16,24,40,.25); }
.card-top { display: flex; gap: 14px; align-items: center; }
.badge { width: 50px; height: 50px; border-radius: 13px; background: var(--c); display: grid; place-items: center; flex: none; }
.name { font-size: 30px; font-weight: 800; color: var(--c); line-height: 1.15; }
.what { font-size: 19px; font-weight: 700; color: ${C.ink}; margin-top: 2px; }
.card ul { margin: 12px 0 0; padding-left: 22px; font-size: 18px; line-height: 1.55; color: ${C.muted}; }
.card li::marker { color: var(--c); }
.who { position: absolute; left: 22px; right: 22px; bottom: 14px; font-size: 16px; color: ${C.faint};
  border-top: 1px dashed ${C.border}; padding-top: 8px; }
.lbl { position: absolute; z-index: 4; font-size: 18px; font-weight: 800; background: #fff; padding: 2px 10px; border-radius: 8px; line-height: 1.35; white-space: nowrap; }
.lbl small { display: block; font-size: 15px; font-weight: 600; color: ${C.muted}; }
.side { position: absolute; left: 1528px; top: 236px; width: 320px; height: 628px; border-radius: 20px;
  background: ${C.bg}; border: 2px solid ${C.border}; padding: 22px 22px; }
.side h3 { font-size: 22px; font-weight: 800; display: flex; align-items: center; gap: 8px; }
.side p { font-size: 15.5px; color: ${C.muted}; margin-top: 4px; }
.side .item { margin-top: 12px; padding-top: 11px; border-top: 1px solid ${C.border}; }
.side .item b { font-size: 19px; display: flex; align-items: center; gap: 8px; }
.side .item span { display: block; font-size: 15.5px; color: ${C.muted}; margin-top: 3px; line-height: 1.5; }
.flow { position: absolute; left: 72px; right: 72px; top: 900px; height: 118px; border-radius: 18px; background: ${C.bg};
  border: 2px solid ${C.border}; display: flex; align-items: center; padding: 0 26px; gap: 10px; }
.flow .lead { font-size: 18px; font-weight: 800; color: ${C.muted}; width: 64px; flex: none; line-height: 1.3; }
.step { display: flex; align-items: center; gap: 10px; background: #fff; border: 2px solid color-mix(in srgb, var(--c) 40%, #fff);
  border-radius: 12px; padding: 10px 12px; flex: 1 1 0; min-width: 0; }
.step .b { width: 36px; height: 36px; border-radius: 10px; background: var(--c); display: grid; place-items: center; flex: none; }
.step .t { font-size: 14px; font-weight: 800; color: var(--c); }
.step .d { font-size: 17px; font-weight: 700; color: ${C.ink}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.go { flex: none; text-align: center; font-size: 13px; color: ${C.faint}; font-weight: 700; width: 66px; line-height: 1.2; white-space: nowrap; font-size: 12.5px; }
.go i { display: block; font-style: normal; font-size: 22px; color: ${C.faint}; }
</style></head><body>
<div class="head">
  <div class="kicker">タスク管理 ─ 全体の見取り図</div>
  <h1>プロジェクト・タスク・課題・チケット・決定の関係</h1>
  <div class="sub">作業は<b style="color:${C.task}">タスク</b>、決めないと進まない論点は<b style="color:${C.issue}">課題</b>、依頼や問い合わせは<b style="color:${C.ticket}">チケット</b>、決めたことは<b style="color:${C.decision}">決定</b>に書きます。</div>
</div>

${card({ x: 64, y: 330, w: 366, h: 350, color: C.ticket, icon: 'ticket', name: 'チケット', what: '依頼・問い合わせ・障害',
  points: ['窓口ごとに受け付ける', '担当・状態・期限・対応時間を社内で管理', '社外の人も自社のぶんを起票できる', '中身によってタスクや課題へ渡す', '窓口はプロジェクトに紐づけられる'],
  who: '起票：社内の全員／社外ユーザー' })}

<div class="frame project">
  <div class="project-head">${ico('folder', 26, '#fff', 2)}プロジェクト</div>
  <div class="project-note">メンバーと権限・使うタブ・色をプロジェクトごとに決める</div>
</div>

${card({ x: 584, y: 350, w: 404, h: 246, color: C.task, icon: 'list', name: 'タスク', what: 'やる作業',
  points: ['担当・期限・進捗', '親子に分ける／先行 → 後続の依存', 'ガント・負荷・ボトルネックで見る', 'コメント・添付・定例（繰り返し）'] })}
${card({ x: 1046, y: 350, w: 404, h: 236, color: C.issue, icon: 'pin', name: '課題', what: '決めないと進まない論点',
  points: ['対応者・影響度・期限', '解決のためのタスクを紐づける', '経緯をコメントで残す', 'チケットから作ることもできる'] })}
${card({ x: 812, y: 628, w: 412, h: 214, color: C.decision, icon: 'scale', name: '決定', what: '何を・なぜ・誰が決めたか',
  points: ['却下した案・前提条件・変更履歴', '新しい決定で前の決定を置き換え', '課題・定例会議の回から記録できる'] })}

${svgArrows}
${label({ x: 440, y: 480, text: '作業なら<small>＋タスクにする</small>', color: C.ticket })}
${label({ x: 700, y: 293, text: '決める必要があるなら ＋課題にする', color: C.ticket })}
${label({ x: 984, y: 408, text: '紐づく<small>タスク</small>', color: C.issue })}
${label({ x: 1310, y: 648, text: '決まったら<small>決定として記録</small>', color: C.issue, align: 'left' })}
${label({ x: 576, y: 648, text: '関連する<small>タスク・課題</small>', color: C.decision })}

<div class="side">
  <h3>${ico('sun', 24, C.ink)}横断して見る</h3>
  <p>左メニューから、参加している全プロジェクトをまとめて見られます。</p>
  <div class="item"><b>${ico('sun', 20, C.muted)}今日の確認</b><span>自分の期限・担当の課題とチケット・見直し日が来た決定</span></div>
  <div class="item"><b>${ico('check', 20, C.muted)}マイタスク</b><span>全プロジェクトの自分の担当タスク</span></div>
  <div class="item"><b>${ico('pin', 20, C.muted)}課題 ・ ${ico('scale', 20, C.muted)}決定</b><span>プロジェクトをまたいだ一覧。プロジェクトで絞れる</span></div>
  <div class="item"><b>${ico('ticket', 20, C.muted)}チケット ・ ${ico('chart', 20, C.muted)}全体ガント</b><span>窓口ごとの受付状況と、複数プロジェクトの日程</span></div>
  <div class="item"><b>${ico('eye', 20, C.muted)}社外の人に見せる</b><span>プロジェクトごとに見せるタブを選び、決定・リンクは 1 件ずつ「社外にも見せる」を付ける</span></div>
</div>

<div class="flow">
  <div class="lead">使い方<br>の例</div>
  <div class="step" style="--c:${C.ticket}"><span class="b">${ico('ticket', 20, '#fff', 2)}</span><div style="min-width:0"><div class="t">チケット</div><div class="d">「ログインできない」</div></div></div>
  <div class="go"><i>→</i>調べる</div>
  <div class="step" style="--c:${C.task}"><span class="b">${ico('list', 20, '#fff', 2)}</span><div style="min-width:0"><div class="t">タスク</div><div class="d">原因を調査する</div></div></div>
  <div class="go"><i>→</i>論点が出る</div>
  <div class="step" style="--c:${C.issue}"><span class="b">${ico('pin', 20, '#fff', 2)}</span><div style="min-width:0"><div class="t">課題</div><div class="d">認証方式を変えるか</div></div></div>
  <div class="go"><i>→</i>決める</div>
  <div class="step" style="--c:${C.decision}"><span class="b">${ico('scale', 20, '#fff', 2)}</span><div style="min-width:0"><div class="t">決定</div><div class="d">社内 SSO に寄せる</div></div></div>
  <div class="go"><i>→</i>作業へ</div>
  <div class="step" style="--c:${C.task}"><span class="b">${ico('list', 20, '#fff', 2)}</span><div style="min-width:0"><div class="t">タスク</div><div class="d">SSO 対応を実装</div></div></div>
</div>
</body></html>`;
module.exports = html;
