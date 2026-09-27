const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'js', 'icons.js'), 'utf8');
const body = src.slice(src.indexOf('const PATHS = {') + 'const PATHS = '.length, src.indexOf('\n};', src.indexOf('const PATHS = {')) + 2);
const PATHS = eval('(' + body + ')');
function ico(name, size = 24, color = 'currentColor', sw = 1.75) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round">${(PATHS[name] || []).join('')}</svg>`;
}
const C = {
  project: '#4f5bd5', task: '#2f6fea', issue: '#e0801a', ticket: '#8b54e8', decision: '#12966a',
  ink: '#11151c', muted: '#58637a', faint: '#8a95a8', border: '#e3e6eb', bg: '#f5f6f8',
};
const BASE_CSS = `
* { box-sizing: border-box; margin: 0; }
html, body { width: 1920px; height: 1080px; }
body { font-family: "Noto Sans CJK JP", sans-serif; color: ${C.ink}; background: #fff; position: relative; overflow: hidden; }
.kicker { font-size: 20px; font-weight: 700; color: ${C.project}; letter-spacing: .08em; }
h1 { font-size: 46px; font-weight: 800; letter-spacing: -.01em; margin-top: 6px; }
.sub { font-size: 21px; color: ${C.muted}; margin-top: 8px; }
.head { position: absolute; left: 72px; top: 52px; right: 72px; }
.foot { position: absolute; left: 72px; right: 72px; bottom: 34px; font-size: 16px; color: ${C.faint}; display: flex; justify-content: space-between; }
`;
module.exports = { ico, C, BASE_CSS };
