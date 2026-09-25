/* 多要素認証の設定と、自分のログイン履歴。
   プロフィール設定のカードと、必須なのに未設定の人が最初に見る画面で使う。 */
import { api } from '../api.js';
import { store } from '../store.js';
import { clear, el, fill, openModal, toast } from '../util.js';
import { brandLockup } from '../brand.js';

/** 認証アプリの登録 → 6 桁の確認 → 予備コードの控え、までを host の中で進める。 */
function setupSteps(host, onDone) {
  const error = el('div', { class: 'login-error', hidden: true, role: 'alert' });
  const code = el('input', {
    class: 'input code-input', inputmode: 'numeric', autocomplete: 'one-time-code',
    maxlength: 6, placeholder: '123456',
  });
  const confirm = el('button', { class: 'btn btn-primary', type: 'submit' }, '確認して有効にする');

  const start = async () => {
    fill(host, el('div', { class: 'empty', text: '準備しています…' }));
    let setup;
    try {
      setup = await api.post('/api/auth/mfa/setup');
    } catch (err) {
      fill(host, el('div', { class: 'login-error', text: err.message }));
      return;
    }
    const qr = setup.qr_svg
      ? el('div', { class: 'mfa-qr', 'aria-label': 'QR コード' })
      : null;
    if (qr) qr.innerHTML = setup.qr_svg;   // サーバーが segno で作った SVG（利用者の入力は含まない）
    const secret = setup.secret.replace(/(.{4})/g, '$1 ').trim();
    fill(host,
      el('ol', { class: 'mfa-steps' },
        el('li', {},
          el('strong', { text: '認証アプリを用意する' }),
          el('span', { class: 'hint',
            text: 'Google Authenticator・Microsoft Authenticator・1Password など、6 桁のコードを出すアプリなら何でも使えます。' })),
        el('li', {},
          el('strong', { text: 'アプリで QR コードを読み取る' }),
          el('div', { class: 'mfa-qr-row' }, qr,
            el('div', { class: 'mfa-secret' },
              el('span', { class: 'hint', text: '読み取れないときは、この鍵を手で入れてください' }),
              el('code', { text: secret }),
              el('button', {
                class: 'btn btn-sm', type: 'button',
                onClick: () => navigator.clipboard?.writeText(setup.secret).then(() => toast('鍵をコピーしました', 'ok')),
              }, 'コピー')))),
        el('li', {},
          el('strong', { text: 'アプリに出た 6 桁を入れる' }),
          el('form', {
            class: 'mfa-confirm',
            onSubmit: async (event) => {
              event.preventDefault();
              error.hidden = true;
              confirm.disabled = true;
              try {
                const data = await api.post('/api/auth/mfa/enable', { code: code.value.trim() });
                showCodes(host, data.recovery_codes, onDone);
              } catch (err) {
                error.textContent = err.message;
                error.hidden = false;
                confirm.disabled = false;
                code.select();
              }
            },
          }, code, confirm),
          error)));
    code.focus();
  };
  start();
}

/** 予備コードを見せる。この画面を閉じたら二度と出せない。 */
function showCodes(host, codes, onDone) {
  const text = codes.join('\n');
  const done = el('button', { class: 'btn btn-primary', disabled: true, onClick: onDone }, '控えたので続ける');
  const saved = el('input', { type: 'checkbox' });
  saved.addEventListener('change', () => { done.disabled = !saved.checked; });
  fill(host,
    el('div', { class: 'mfa-codes-head' },
      el('div', { class: 'mfa-ok', 'aria-hidden': 'true', text: '✓' }),
      el('div', {},
        el('strong', { text: '多要素認証を有効にしました' }),
        el('div', { class: 'hint',
          text: 'スマホをなくしたときは、下の予備コードでログインできます（1 つにつき 1 回）。いま控えてください。この画面を閉じると、もう表示できません。' }))),
    el('div', { class: 'mfa-codes' }, ...codes.map((c) => el('code', { text: c }))),
    el('div', { class: 'row-actions' },
      el('button', {
        class: 'btn btn-sm', type: 'button',
        onClick: () => navigator.clipboard?.writeText(text).then(() => toast('コピーしました', 'ok')),
      }, 'コピー'),
      el('button', {
        class: 'btn btn-sm', type: 'button',
        onClick: () => {
          const name = (store.ui.app_name || 'task-manager').replace(/[\\/:*?"<>|\s]+/g, '_');
          const link = el('a', {
            href: URL.createObjectURL(new Blob([`${store.ui.app_name || ''} の予備コード\n\n${text}\n`], { type: 'text/plain' })),
            download: `${name}_予備コード.txt`,
          });
          link.click();
          setTimeout(() => URL.revokeObjectURL(link.href), 1000);
        },
      }, 'ファイルに保存')),
    el('label', { class: 'check', style: { marginTop: '12px' } }, saved, el('span', { text: '予備コードを安全な場所に控えました' })),
    el('div', { class: 'row-actions', style: { justifyContent: 'flex-end' } }, done));
}

/** 必須なのに未設定の人が、ログインしてすぐ見る画面。済むまで他の画面には進めない。 */
export function renderForcedSetup(root, onDone) {
  const host = el('div', {});
  fill(root, el('div', { class: 'login-page' },
    el('div', { class: 'login-glow', 'aria-hidden': 'true' }),
    el('div', { class: 'card login-card wide' },
      brandLockup('lg'),
      el('h2', { class: 'login-title', text: '多要素認証を設定してください' }),
      el('p', { class: 'page-sub',
        text: `${store.user?.name || ''} さん、このアプリではログインのときにスマホの認証アプリのコードも確かめます。最初に一度だけ設定してください。` }),
      host,
      el('div', { class: 'login-foot' },
        el('a', {
          href: '#', class: 'login-link',
          onClick: async (event) => {
            event.preventDefault();
            await api.post('/api/auth/logout');
            location.reload();
          },
        }, 'ログアウト')))));
  setupSteps(host, onDone);
}

async function askPassword(title, message, okLabel) {
  const password = el('input', { class: 'input', type: 'password', autocomplete: 'current-password' });
  return openModal({
    title,
    build: (close) => el('form', {
      onSubmit: (event) => { event.preventDefault(); close(password.value); },
    },
    el('p', { class: 'page-sub', text: message }),
    el('div', { class: 'field' }, el('label', { text: 'パスワード' }), password)),
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', { class: 'btn btn-primary', onClick: () => close(password.value) }, okLabel),
    ],
  });
}

/** プロフィール設定の「セキュリティ」カード。 */
export function securityCard() {
  const body = el('div', { class: 'card-body' });
  const history = el('div', {});

  const draw = async () => {
    const state = await api.get('/api/auth/mfa');
    store.user.mfa_enabled = state.enabled;
    clear(body);
    body.append(
      el('div', { class: 'mfa-state' },
        el('span', { class: `status-dot ${state.enabled ? 'on' : 'off'}`, 'aria-hidden': 'true' }),
        el('div', {},
          el('strong', { text: state.enabled ? '多要素認証：使っています' : '多要素認証：使っていません' }),
          el('div', { class: 'hint',
            text: state.enabled
              ? `ログインのときに、パスワードと認証アプリのコードを確かめます。予備コードの残り ${state.recovery_left} 個。`
              : 'パスワードに加えて、スマホの認証アプリのコードでも本人か確かめます。パスワードが漏れても、他人はログインできません。'
                + (state.required ? '（このアプリでは必須です）' : '') }))),
      el('div', { class: 'row-actions' },
        state.enabled
          ? [
            el('button', {
              class: 'btn btn-sm',
              onClick: async () => {
                const password = await askPassword('予備コードを作り直す',
                  '今の予備コードは使えなくなり、新しい 10 個を表示します。', '作り直す');
                if (password === null || password === undefined) return;
                try {
                  const data = await api.post('/api/auth/mfa/recovery-codes', { password });
                  openModal({
                    title: '新しい予備コード',
                    build: (close) => {
                      const host = el('div', {});
                      showCodes(host, data.recovery_codes, () => close(true));
                      return host;
                    },
                  });
                  draw();
                } catch (err) { toast(err.message, 'error'); }
              },
            }, '予備コードを作り直す'),
            el('button', {
              class: 'btn btn-sm btn-quiet-danger',
              onClick: async () => {
                const password = await askPassword('多要素認証を解除する',
                  state.required
                    ? 'このアプリでは必須のため、解除するとすぐに設定し直す画面になります（スマホを替えたときなど）。'
                    : '解除すると、パスワードだけでログインできるようになります。',
                  '解除する');
                if (password === null || password === undefined) return;
                try {
                  const data = await api.post('/api/auth/mfa/disable', { password });
                  toast('多要素認証を解除しました', 'ok');
                  if (data.required) { location.reload(); return; }
                  draw();
                } catch (err) { toast(err.message, 'error'); }
              },
            }, '解除する'),
          ]
          : el('button', {
            class: 'btn btn-primary btn-sm',
            onClick: () => {
              openModal({
                title: '多要素認証を設定',
                wide: true,
                build: (close) => {
                  const host = el('div', {});
                  setupSteps(host, () => close(true));
                  return host;
                },
              }).then(() => draw());
            },
          }, '設定する')),
      el('div', { class: 'sec-sub' }, el('strong', { text: '最近のログイン' }),
        el('span', { class: 'hint', text: '　覚えのないものがあれば、パスワードを変えて管理者に知らせてください' })),
      history);
    const events = (await api.get('/api/auth/logins')).events.slice(0, 8);
    fill(history, events.length
      ? el('ul', { class: 'sec-history' }, ...events.map((e) => el('li', { class: e.event === 'failed' ? 'bad' : '' },
        el('span', { class: 'when', text: e.created_at.slice(5, 16).replace('-', '/') }),
        el('span', { class: 'what', text: e.event_label + (e.reason_label ? `（${e.reason_label}）` : '') }),
        el('span', { class: 'where', text: `${e.device}　${e.ip}` }))))
      : el('div', { class: 'hint', text: '記録はまだありません' }));
  };
  draw();
  return el('div', { class: 'card', style: { marginTop: '16px' } },
    el('div', { class: 'card-head' }, el('h2', {}, 'セキュリティ')),
    body);
}
