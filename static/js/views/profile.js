/* Personal settings: display name, colour, notification preference, password. */
import { api } from '../api.js';
import { defaultNavOrder, navChoices, setHeader } from '../app.js';
import { store } from '../store.js';
import { el, fill, svgEl, toast } from '../util.js';
import { ACCENT_PRESETS, applyAccent, applyTheme } from '../theme.js';
import { securityCard } from './security.js';
import { icon } from '../icons.js';

// スマホ下部のタブに出る数（app.js の MOBILE_NAV_COUNT と同じ）
const MOBILE_TABS = 4;

export async function render(container) {
  setHeader('プロフィール設定');
  const user = store.user;
  const notifySettings = await api.notificationSettings();

  const name = el('input', { class: 'input' });
  name.value = user.name;
  const color = el('input', { type: 'color', class: 'input', style: { height: '38px', padding: '2px' } });
  color.value = user.avatar_color || '#4f8cff';

  const current = el('input', { class: 'input', type: 'password', autocomplete: 'current-password' });
  const next = el('input', { class: 'input', type: 'password', autocomplete: 'new-password' });
  const confirm = el('input', { class: 'input', type: 'password', autocomplete: 'new-password' });

  const themeSelect = el('select', { class: 'select' },
    el('option', { value: 'auto' }, '端末の設定に合わせる'),
    el('option', { value: 'light' }, 'ライト'),
    el('option', { value: 'dark' }, 'ダーク'));
  themeSelect.value = user.ui_theme || 'auto';
  themeSelect.addEventListener('change', () => applyTheme(themeSelect.value));

  const tint = el('input', { type: 'checkbox', checked: user.ui_project_tint !== false ? true : null });
  const accentState = { value: store.accent() };
  const swatches = el('div', { class: 'swatches' });
  const customInput = el('input', {
    type: 'color', class: 'input', style: { height: '38px', padding: '2px', maxWidth: '80px' },
  });
  customInput.value = accentState.value;
  const drawSwatches = () => {
    fill(swatches, ...ACCENT_PRESETS.map((preset) => el('button', {
      type: 'button',
      class: `swatch${preset.value === accentState.value ? ' active' : ''}`,
      style: { background: preset.value },
      title: preset.label,
      onClick: () => pick(preset.value),
    })));
  };
  const pick = (value) => {
    accentState.value = value;
    customInput.value = value;
    applyAccent(value);
    drawSwatches();
  };
  customInput.addEventListener('input', () => pick(customInput.value));
  drawSwatches();

  const grid = el('div', { class: 'grid cols-2' },
    el('div', { class: 'card' },
      el('div', { class: 'card-head' }, el('h2', {}, '基本情報')),
      el('div', { class: 'card-body' },
        el('div', { class: 'field' }, el('label', { text: '氏名' }), name),
        el('div', { class: 'field' }, el('label', { text: 'メールアドレス' }),
          el('input', { class: 'input', value: user.email, disabled: true }),
          el('div', { class: 'hint', text: '変更は管理者に依頼してください' })),
        el('div', { class: 'row' },
          el('div', { class: 'field' }, el('label', { text: 'アイコンの色' }), color),
          el('div', { class: 'field' }, el('label', { text: '画面テーマ' }), themeSelect)),
        el('div', { class: 'field' },
          el('label', { text: '全体の色合い（アクセントカラー）' }),
          el('div', { style: { display: 'flex', gap: '10px', alignItems: 'center',
            flexWrap: 'wrap' } }, swatches, customInput,
          el('button', {
            class: 'btn btn-sm', type: 'button',
            onClick: () => pick(store.ui.accent_default || '#3b6ef5'),
          }, '既定に戻す')),
          el('div', { class: 'hint',
            text: '選ぶとすぐ画面に反映されます。「保存」で次回以降も引き継がれます。' })),
        el('div', { class: 'field' },
          el('label', { class: 'check' }, tint,
            el('span', { text: 'プロジェクトの画面を、そのプロジェクトの色で染める' })),
          el('div', { class: 'hint',
            text: '見出しの帯・ボタン・背景がプロジェクトの色になり、どのプロジェクトを見ているか一目で分かります。' })),
        el('div', { class: 'field' },
          el('label', { text: 'ブラウザ通知（期限アラーム）' }),
          browserNotifyControl()),
        el('button', {
          class: 'btn btn-primary',
          onClick: async (event) => {
            const button = event.currentTarget;
            button.disabled = true;
            try {
              const result = await api.patch('/api/auth/profile', {
                name: name.value.trim(),
                avatar_color: color.value,
                ui_theme: themeSelect.value,
                ui_accent: accentState.value,
                ui_project_tint: tint.checked,
              });
              store.user = result.user;
              store.emit();
              toast('保存しました', 'ok');
            } catch (error) { toast(error.message, 'error'); }
            button.disabled = false;
          },
        }, '保存'))),
    el('div', { class: 'card' },
      el('div', { class: 'card-head' }, el('h2', {}, 'パスワード変更')),
      el('div', { class: 'card-body' },
        el('div', { class: 'field' }, el('label', { text: '現在のパスワード' }), current),
        el('div', { class: 'field' }, el('label', { text: '新しいパスワード（8文字以上）' }), next),
        el('div', { class: 'field' }, el('label', { text: '新しいパスワード（確認）' }), confirm),
        el('button', {
          class: 'btn btn-primary',
          onClick: async (event) => {
            const button = event.currentTarget;
            if (next.value !== confirm.value) {
              toast('新しいパスワードが一致しません', 'error');
              return;
            }
            button.disabled = true;
            try {
              await api.post('/api/auth/password', {
                current_password: current.value, new_password: next.value,
              });
              current.value = ''; next.value = ''; confirm.value = '';
              toast('パスワードを変更しました', 'ok');
            } catch (error) { toast(error.message, 'error'); }
            button.disabled = false;
          },
        }, 'パスワードを変更'))));

  fill(container, grid, securityCard(), navOrderCard(), notificationCard(notifySettings));
}


/** メールで受け取る通知の種類と、黙らせたいプロジェクトを選ぶ。 */
/**
 * 左メニューの並び替え。よく使うものを上に持ってこられるようにする。
 * スマホ下部のタブは、この並びの先頭 4 つを使う。
 */
/** つまみの印（点 6 つ）。記号の文字はフォントによって出ないので SVG で描く。 */
function gripIcon() {
  return svgEl('svg', { width: 12, height: 18, viewBox: '0 0 12 18', 'aria-hidden': 'true' },
    ...[3, 9].flatMap((x) => [3, 9, 15].map((y) => svgEl('circle', { cx: x, cy: y, r: 1.6, fill: 'currentColor' }))));
}

function navOrderCard() {
  let items = navChoices();
  const byId = new Map(items.map((item) => [item.id, item]));
  const listHost = el('div', { class: 'nav-order' });
  const status = el('span', { class: 'nav-order-status', 'aria-live': 'polite' });
  let saveTimer = null;

  // 並べ替えたらすぐ左メニューに映し、少し待ってから保存する（保存ボタンは押さなくてよい）
  const commit = () => {
    const ids = items.map((item) => item.id);
    store.user = { ...store.user, nav_order: ids };
    store.emit();
    status.textContent = '保存しています…';
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      try {
        const result = await api.patch('/api/auth/profile', { nav_order: ids });
        store.user = result.user;
        status.textContent = '✓ 保存しました';
      } catch (error) {
        status.textContent = '';
        toast(error.message, 'error');
      }
    }, 500);
  };

  const move = (index, direction) => {
    const to = index + direction;
    if (to < 0 || to >= items.length) return;
    items.splice(to, 0, items.splice(index, 1)[0]);
    draw();
    commit();
    listHost.children[to]?.querySelector(direction < 0 ? '.up' : '.down')?.focus();
  };

  /** つまみを押したまま上下に動かす。マウスでも指でも同じ動き。 */
  const startDrag = (event, row) => {
    event.preventDefault();
    // 行を DOM の中で動かすとポインターの捕捉が外れるので、動きは文書全体で受ける
    const pointerId = event.pointerId;
    row.classList.add('dragging');
    listHost.classList.add('sorting');
    const onMove = (e) => {
      if (e.pointerId !== pointerId) return;
      const others = [...listHost.children].filter((node) => node !== row);
      const next = others.find((node) => {
        const rect = node.getBoundingClientRect();
        return e.clientY < rect.top + rect.height / 2;
      });
      if (next) { if (row.nextSibling !== next) listHost.insertBefore(row, next); }
      else if (listHost.lastChild !== row) listHost.appendChild(row);
    };
    const onUp = (e) => {
      if (e.pointerId !== pointerId) return;
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
      document.removeEventListener('pointercancel', onUp);
      const order = [...listHost.children].map((node) => byId.get(node.dataset.id));
      const changed = order.some((item, i) => item !== items[i]);
      items = order;
      draw();
      if (changed) commit();
    };
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
    document.addEventListener('pointercancel', onUp);
  };

  function draw() {
    listHost.classList.remove('sorting');
    fill(listHost, ...items.map((item, index) => {
      const row = el('div', {
        class: `nav-order-row${index < MOBILE_TABS ? ' on-mobile' : ''}${index === MOBILE_TABS - 1 ? ' mobile-last' : ''}`,
        dataset: { id: item.id },
      });
      row.append(
        el('span', {
          class: 'nav-order-grip', title: '押したまま上下に動かして並べ替え',
          onPointerdown: (event) => startDrag(event, row),
        }, gripIcon()),
        el('span', { class: 'nav-order-ico' }, item.icon ? icon(item.icon) : null),
        el('span', { class: 'grow', text: item.label }),
        el('button', {
          class: 'icon-btn up', title: '上へ', 'aria-label': `${item.label}を上へ`,
          disabled: index === 0 ? true : null, onClick: () => move(index, -1),
        }, '↑'),
        el('button', {
          class: 'icon-btn down', title: '下へ', 'aria-label': `${item.label}を下へ`,
          disabled: index === items.length - 1 ? true : null, onClick: () => move(index, 1),
        }, '↓'));
      return row;
    }));
  }
  draw();

  return el('div', { class: 'card', style: { marginTop: '14px' } },
    el('div', { class: 'card-head' },
      el('h2', {}, '左メニューの並び順'),
      status,
      el('button', {
        class: 'btn btn-sm',
        onClick: () => {
          const order = defaultNavOrder();
          items = [...items].sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
          draw();
          commit();
        },
      }, '既定の並びに戻す')),
    el('div', { class: 'card-body' },
      el('p', { class: 'page-sub', style: { marginTop: 0 },
        text: '左のつまみ（点の印）を押したまま上下に動かすか、↑↓ で並べ替えます。動かすとすぐ左メニューに反映され、自動で保存されます'
          + '（自分の画面だけ）。スマホでは、色の付いた上の 4 つが画面下のタブになります。' }),
      listHost));
}

function notificationCard(data) {
  const prefs = data.prefs || {};
  const master = el('input', { type: 'checkbox' });
  master.checked = Boolean(prefs.email_notify);

  const eventBoxes = data.events.map((event) => {
    const box = el('input', { type: 'checkbox' });
    box.checked = prefs[event.value] !== false;
    return { event, box };
  });

  const muted = new Set(data.muted_project_ids || []);
  const projectBoxes = (data.projects || []).map((project) => {
    const box = el('input', { type: 'checkbox' });
    box.checked = !muted.has(project.id);
    if (!project.notify_enabled) box.checked = false;
    return { project, box };
  });

  const syncEnabled = () => {
    const on = master.checked;
    for (const { box } of eventBoxes) box.disabled = !on;
    for (const { project, box } of projectBoxes) box.disabled = !on || !project.notify_enabled;
  };
  master.addEventListener('change', syncEnabled);
  syncEnabled();

  return el('div', { class: 'card', style: { marginTop: '16px' } },
    el('div', { class: 'card-head' },
      el('h2', {}, 'メール通知'),
      el('span', { class: 'hint',
        text: data.email_ready ? '' : 'メール送信は未設定です（管理者設定で有効にできます）' })),
    el('div', { class: 'card-body' },
      el('div', { class: 'field' },
        el('label', { class: 'check' }, master,
          el('span', { text: '通知をメールでも受け取る' })),
        el('div', { class: 'hint',
          text: 'オフにしても、画面右上のベルには通知が残ります。' })),
      el('div', { class: 'field' },
        el('label', { text: '受け取る通知' }),
        el('div', { class: 'check-list' },
          ...eventBoxes.map(({ event, box }) => el('label', { class: 'check check-row' },
            box,
            el('span', {},
              el('span', { text: event.label }),
              el('span', { class: 'hint', text: event.help })))))),
      projectBoxes.length
        ? el('div', { class: 'field' },
          el('label', { text: 'プロジェクトごとの受け取り' }),
          el('div', { class: 'check-list' },
            ...projectBoxes.map(({ project, box }) => el('label', { class: 'check check-row' },
              box,
              el('span', {},
                el('span', { class: 'name-line' },
                  el('span', { class: 'dot', style: { background: project.color } }),
                  el('span', { text: project.name })),
                project.notify_enabled
                  ? null
                  : el('span', { class: 'hint', text: 'このプロジェクトは通知が停止されています' }))))),
          el('div', { class: 'hint',
            text: 'チェックを外したプロジェクトからは、メールも日次レポートも届かなくなります。' }))
        : null,
      el('button', {
        class: 'btn btn-primary',
        onClick: async (event) => {
          const button = event.currentTarget;
          button.disabled = true;
          try {
            const payload = {
              email_notify: master.checked,
              muted_project_ids: projectBoxes
                .filter(({ box }) => !box.checked)
                .map(({ project }) => project.id),
            };
            for (const { event: item, box } of eventBoxes) payload[item.value] = box.checked;
            await api.saveNotificationSettings(payload);
            store.user = { ...store.user, email_notify: master.checked };
            store.emit();
            toast('通知設定を保存しました', 'ok');
          } catch (error) { toast(error.message, 'error'); }
          button.disabled = false;
        },
      }, '通知設定を保存')));
}

function browserNotifyControl() {
  const status = el('div', { class: 'hint' });
  const button = el('button', { class: 'btn' }, 'ブラウザ通知を有効にする');
  const sync = () => {
    if (!('Notification' in window)) {
      button.disabled = true;
      status.textContent = 'このブラウザは通知に対応していません。';
      return;
    }
    if (Notification.permission === 'granted') {
      button.disabled = true;
      button.textContent = '有効になっています';
      status.textContent = '期限超過や新しいコメントをデスクトップ通知でお知らせします。';
    } else if (Notification.permission === 'denied') {
      button.disabled = true;
      status.textContent = 'ブラウザ側でブロックされています。サイトの通知設定を変更してください。';
    } else {
      status.textContent = '許可すると、通知をデスクトップに表示します。';
    }
  };
  button.addEventListener('click', async () => {
    await Notification.requestPermission();
    sync();
  });
  sync();
  return el('div', {}, button, status);
}
