# ヘルプ画面の図

アプリの「ヘルプ」に出す 2 枚の図（16:9）を作るためのものです。本体の動作には不要です。

| ファイル | 図 | 書き出し先 |
|---|---|---|
| `slide1.js` | プロジェクト・タスク・課題・チケット・決定の関係 | `static/img/help/relationships.png` |
| `slide2.js` | 役割ごとにできること・できないこと | `static/img/help/roles.png` |

機能や権限を変えたら、`slide1.js` / `slide2.js` の文言や表を直して作り直します。
アイコンは `static/js/icons.js` の線画をそのまま読み込むので、画面と同じ絵になります。
日本語フォントは Noto Sans CJK JP を使います。

```bash
cd tools/help-slides
npm install
node render.js
```
