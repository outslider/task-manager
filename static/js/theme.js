/* Colour scheme: light/dark mode plus a configurable accent colour.

   The accent is a single hex value; every shade the UI needs is derived from
   it so any colour produces a coherent palette in both light and dark mode. */

export const ACCENT_PRESETS = [
  { label: 'ブルー', value: '#3b6ef5' },
  { label: 'インディゴ', value: '#6366f1' },
  { label: 'パープル', value: '#8b5cf6' },
  { label: 'ティール', value: '#0d9488' },
  { label: 'グリーン', value: '#16a34a' },
  { label: 'アンバー', value: '#d97706' },
  { label: 'ローズ', value: '#e11d48' },
  { label: 'スレート', value: '#475569' },
];

const THEME_KEY = 'tm.theme';
const ACCENT_KEY = 'tm.accent';
const DEFAULT_ACCENT = '#3b6ef5';

let currentAccent = DEFAULT_ACCENT;

/* ---------------- colour maths ---------------- */

function hexToRgb(hex) {
  let value = String(hex || '').trim().replace('#', '');
  if (value.length === 3) value = value.split('').map((c) => c + c).join('');
  if (!/^[0-9a-fA-F]{6}$/.test(value)) return null;
  return [
    parseInt(value.slice(0, 2), 16),
    parseInt(value.slice(2, 4), 16),
    parseInt(value.slice(4, 6), 16),
  ];
}

function rgbToHsl([r, g, b]) {
  const rn = r / 255;
  const gn = g / 255;
  const bn = b / 255;
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l * 100];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h;
  if (max === rn) h = ((gn - bn) / d + (gn < bn ? 6 : 0)) / 6;
  else if (max === gn) h = ((bn - rn) / d + 2) / 6;
  else h = ((rn - gn) / d + 4) / 6;
  return [h * 360, s * 100, l * 100];
}

function hsl(h, s, l) {
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  return `hsl(${Math.round(h)} ${clamp(s, 0, 100).toFixed(1)}% ${clamp(l, 0, 100).toFixed(1)}%)`;
}

/** Relative luminance, used to keep text on the accent readable. */
function luminance([r, g, b]) {
  const channel = (v) => {
    const n = v / 255;
    return n <= 0.03928 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/* ---------------- application ---------------- */

export function isDarkMode() {
  const explicit = document.documentElement.getAttribute('data-theme');
  if (explicit) return explicit === 'dark';
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

/** Derive and install the accent shades as CSS custom properties.
    persist=false はプロジェクトの色を一時的に当てるとき（本人の色として覚えない）。 */
export function applyAccent(hex, { persist = true } = {}) {
  const rgb = hexToRgb(hex);
  if (!rgb) return;
  if (persist) {
    currentAccent = hex;
    userAccent = hex;
  }
  const [h, s, l] = rgbToHsl(rgb);
  const dark = isDarkMode();
  const root = document.documentElement.style;

  if (dark) {
    // Dark backgrounds need a lighter, slightly desaturated accent to stay legible.
    const base = Math.max(l, 58);
    root.setProperty('--accent', hsl(h, Math.min(s, 88), base));
    root.setProperty('--accent-hover', hsl(h, Math.min(s, 88), base + 8));
    root.setProperty('--accent-soft', hsl(h, Math.min(s, 60) * 0.7, 22));
  } else {
    const base = Math.min(l, 60);
    root.setProperty('--accent', hsl(h, s, base));
    root.setProperty('--accent-hover', hsl(h, s, base - 9));
    root.setProperty('--accent-soft', hsl(h, Math.min(s, 85), 94));
  }
  // ロゴやグラデーションで使う第2色。色相を少しずらして深みを出す。
  const hue2 = (h + 26) % 360;
  root.setProperty('--accent-2', dark
    ? hsl(hue2, Math.min(s, 88), Math.max(l, 58) + 4)
    : hsl(hue2, Math.min(s + 6, 92), Math.min(l, 60) + 4));
  root.setProperty('--accent-contrast', luminance(rgb) > 0.55 ? '#1b2028' : '#ffffff');
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', hex);
  if (persist) {
    try { localStorage.setItem(ACCENT_KEY, hex); } catch { /* private mode */ }
  }
}

/* ---------------- project theme ---------------- */

let userAccent = DEFAULT_ACCENT;
let activeProject = null;
const PROJECT_VARS = ['--proj', '--proj-2', '--proj-deep', '--proj-soft', '--proj-bg', '--proj-ink',
  '--proj-line'];

/** プロジェクトの色から、帯・背景・線に使う色一式を作る。 */
export function projectColors(hex, dark = isDarkMode()) {
  const rgb = hexToRgb(hex) || hexToRgb('#4f6bff');
  const [h, s, l] = rgbToHsl(rgb);
  const sat = Math.max(s, 38);
  if (dark) {
    return {
      '--proj': hsl(h, Math.min(sat, 80), Math.min(Math.max(l, 42), 52)),
      '--proj-2': hsl((h + 34) % 360, Math.min(sat, 78), 40),
      '--proj-deep': hsl(h, Math.min(sat, 70), 22),
      '--proj-soft': hsl(h, Math.min(sat, 50) * 0.6, 17),
      '--proj-bg': hsl(h, Math.min(sat, 40) * 0.35, 8.5),
      '--proj-ink': '#ffffff',
      '--proj-line': hsl(h, Math.min(sat, 60), 60),
    };
  }
  const base = Math.min(Math.max(l, 40), 56);
  const light = luminance(rgb) > 0.5;
  return {
    '--proj': hsl(h, sat, base),
    '--proj-2': hsl((h + 34) % 360, Math.min(sat + 6, 94), Math.min(base + 6, 62)),
    '--proj-deep': hsl(h, Math.min(sat, 80), Math.max(base - 18, 24)),
    '--proj-soft': hsl(h, Math.min(sat, 85), 95),
    '--proj-bg': hsl(h, Math.min(sat, 60) * 0.5, 97.2),
    '--proj-ink': light ? '#141922' : '#ffffff',
    '--proj-line': hsl(h, sat, base),
  };
}

/** プロジェクトの画面に入ったら、その色で染める。null で本人の色に戻す。 */
export function applyProjectTheme(project, { tint = true } = {}) {
  const root = document.documentElement;
  const next = project && tint ? project : null;
  activeProject = next;
  if (!next) {
    root.removeAttribute('data-project');
    for (const name of PROJECT_VARS) root.style.removeProperty(name);
    applyAccent(userAccent, { persist: false });
    return;
  }
  root.setAttribute('data-project', next.theme || 'aurora');
  for (const [name, value] of Object.entries(projectColors(next.color))) root.style.setProperty(name, value);
  applyAccent(next.color, { persist: false });
}

export function applyTheme(value) {
  const root = document.documentElement;
  if (value === 'auto' || !value) root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', value);
  try { localStorage.setItem(THEME_KEY, value || 'auto'); } catch { /* private mode */ }
  applyAccent(currentAccent);   // shades depend on light/dark
  if (activeProject) applyProjectTheme(activeProject);
}

export function savedTheme() {
  try { return localStorage.getItem(THEME_KEY) || 'auto'; } catch { return 'auto'; }
}

export function savedAccent() {
  try { return localStorage.getItem(ACCENT_KEY) || DEFAULT_ACCENT; } catch { return DEFAULT_ACCENT; }
}

/** Called once at start-up, and again after the session tells us the user's choice. */
export function initTheme({ theme, accent } = {}) {
  applyTheme(theme || savedTheme());
  applyAccent(accent || savedAccent());
}

// Follow the OS when the user has not made an explicit choice.
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if (document.documentElement.getAttribute('data-theme')) return;
  applyAccent(currentAccent);
  if (activeProject) applyProjectTheme(activeProject);
});

// Apply the cached values immediately so there is no flash of the default colour.
initTheme();
