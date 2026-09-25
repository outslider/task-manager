/* Login screen: password, then the authenticator code when the account uses it.
   Also the "forgot password" request and the reset page the mail links to. */
import { api } from '../api.js';
import { el, fill } from '../util.js';
import { brandLockup } from '../brand.js';

function errorBox() {
  return el('div', { class: 'login-error', hidden: true, role: 'alert' });
}

function showError(box, message) {
  box.textContent = message;
  box.hidden = false;
}

function page(root, ...content) {
  fill(root, el('div', { class: 'login-page' },
    el('div', { class: 'login-glow', 'aria-hidden': 'true' }),
    el('div', { class: 'card login-card' }, brandLockup('lg'), ...content)));
}

function busy(button, on, label) {
  button.disabled = on;
  button.textContent = on ? '確認中…' : label;
}

export function renderLogin(root, onSuccess, { notice = '' } = {}) {
  const error = errorBox();
  const email = el('input', {
    class: 'input', type: 'email', autocomplete: 'username', required: true,
    placeholder: 'you@example.com',
  });
  const password = el('input', {
    class: 'input', type: 'password', autocomplete: 'current-password', required: true,
    placeholder: '••••••••',
  });
  const submit = el('button', { class: 'btn btn-primary btn-block', type: 'submit' }, 'ログイン');

  const form = el('form', {
    onSubmit: async (event) => {
      event.preventDefault();
      error.hidden = true;
      busy(submit, true, 'ログイン');
      try {
        const data = await api.login(email.value.trim(), password.value);
        if (data.mfa_required) {
          renderCodeStep(root, data.challenge, onSuccess);
          return;
        }
        onSuccess();
      } catch (err) {
        showError(error, err.message);
        busy(submit, false, 'ログイン');
        password.select();
      }
    },
  },
  notice ? el('div', { class: 'login-notice', text: notice }) : null,
  error,
  el('div', { class: 'field' }, el('label', { text: 'メールアドレス' }), email),
  el('div', { class: 'field' },
    el('div', { class: 'label-row' },
      el('label', { text: 'パスワード' }),
      el('a', {
        href: '#', class: 'login-link',
        onClick: (event) => { event.preventDefault(); renderRecovery(root, onSuccess, email.value.trim()); },
      }, 'パスワードを忘れた場合')),
    password),
  submit);

  page(root, el('p', { class: 'page-sub', text: 'アカウント情報を入力してください。' }), form);
  email.focus();
}

/** パスワードのあと。認証アプリの 6 桁か、予備コードを入れる。 */
function renderCodeStep(root, challenge, onSuccess) {
  const error = errorBox();
  let useRecovery = false;
  const code = el('input', {
    class: 'input code-input', inputmode: 'numeric', autocomplete: 'one-time-code',
    maxlength: 6, placeholder: '123456', required: true,
  });
  const label = el('label', { text: '認証アプリの 6 桁のコード' });
  const hint = el('p', { class: 'page-sub',
    text: 'このアカウントは多要素認証を使っています。スマホの認証アプリに表示されているコードを入れてください。' });
  const submit = el('button', { class: 'btn btn-primary btn-block', type: 'submit' }, '確認');
  const toggle = el('a', {
    href: '#', class: 'login-link',
    onClick: (event) => {
      event.preventDefault();
      useRecovery = !useRecovery;
      code.value = '';
      code.maxLength = useRecovery ? 12 : 6;
      code.inputMode = useRecovery ? 'text' : 'numeric';
      code.placeholder = useRecovery ? 'abcd-efgh' : '123456';
      code.classList.toggle('code-input', !useRecovery);
      label.textContent = useRecovery ? '予備コード（設定のときに控えたもの）' : '認証アプリの 6 桁のコード';
      toggle.textContent = useRecovery ? '認証アプリのコードを使う' : 'スマホが手元にない場合（予備コード）';
      code.focus();
    },
  }, 'スマホが手元にない場合（予備コード）');

  const form = el('form', {
    onSubmit: async (event) => {
      event.preventDefault();
      error.hidden = true;
      busy(submit, true, '確認');
      try {
        const data = await api.post('/api/auth/mfa/verify', { challenge, code: code.value.trim() });
        if (data.recovery_left !== undefined && data.recovery_left <= 3) {
          sessionStorage.setItem('tm.recoveryWarn', String(data.recovery_left));
        }
        onSuccess();
      } catch (err) {
        if (err.detail?.restart) {
          renderLogin(root, onSuccess, { notice: err.message });
          return;
        }
        showError(error, err.message);
        busy(submit, false, '確認');
        code.select();
      }
    },
  },
  error,
  el('div', { class: 'field' }, label, code),
  submit,
  el('div', { class: 'login-foot' },
    toggle,
    el('a', {
      href: '#', class: 'login-link',
      onClick: (event) => { event.preventDefault(); renderLogin(root, onSuccess); },
    }, 'ログインをやり直す')));

  page(root, hint, form);
  code.focus();
  // 6 桁そろったら自動で送る
  code.addEventListener('input', () => {
    if (!useRecovery && /^\d{6}$/.test(code.value)) form.requestSubmit();
  });
}

/** パスワードを忘れたとき。 */
function renderRecovery(root, onSuccess, prefill = '') {
  const error = errorBox();
  const email = el('input', {
    class: 'input', type: 'email', autocomplete: 'username', required: true,
    placeholder: 'you@example.com',
  });
  email.value = prefill;
  const submit = el('button', { class: 'btn btn-primary btn-block', type: 'submit' }, '再設定を依頼する');
  const back = el('a', {
    href: '#', class: 'login-link',
    onClick: (event) => { event.preventDefault(); renderLogin(root, onSuccess); },
  }, 'ログインに戻る');
  const form = el('form', {
    onSubmit: async (event) => {
      event.preventDefault();
      error.hidden = true;
      busy(submit, true, '再設定を依頼する');
      try {
        const data = await api.post('/api/auth/recovery', { email: email.value.trim() });
        page(root,
          el('div', { class: 'login-done' },
            el('div', { class: 'login-done-icon', 'aria-hidden': 'true', text: data.via === 'mail' ? '✉' : '✓' }),
            el('p', { text: data.message })),
          el('button', { class: 'btn btn-block', onClick: () => renderLogin(root, onSuccess) }, 'ログインに戻る'));
      } catch (err) {
        showError(error, err.message);
        busy(submit, false, '再設定を依頼する');
      }
    },
  },
  error,
  el('div', { class: 'field' }, el('label', { text: 'メールアドレス' }), email),
  submit,
  el('div', { class: 'login-foot' }, back));
  page(root,
    el('h2', { class: 'login-title', text: 'パスワードの再設定' }),
    el('p', { class: 'page-sub',
      text: '登録しているメールアドレスを入れてください。再設定の案内をお送りします'
        + '（メールを送れない設定のときは、管理者に依頼が届きます）。' }),
    form);
  email.focus();
}

/** メールのリンクから開く、新しいパスワードを決める画面。 */
export async function renderReset(root, token, onDone) {
  const goLogin = (notice) => {
    history.replaceState(null, '', location.pathname);
    renderLogin(root, onDone, { notice });
  };
  let valid = false;
  try { valid = (await api.get(`/api/auth/reset/${encodeURIComponent(token)}`)).ok; } catch { /* offline */ }
  if (!valid) {
    page(root,
      el('h2', { class: 'login-title', text: 'リンクが使えません' }),
      el('p', { class: 'page-sub', text: 'リンクの期限（60 分）が切れているか、すでに使われています。もう一度依頼してください。' }),
      el('button', { class: 'btn btn-primary btn-block', onClick: () => goLogin('') }, 'ログイン画面へ'));
    return;
  }
  const error = errorBox();
  const next = el('input', { class: 'input', type: 'password', autocomplete: 'new-password', required: true, minlength: 8 });
  const again = el('input', { class: 'input', type: 'password', autocomplete: 'new-password', required: true });
  const submit = el('button', { class: 'btn btn-primary btn-block', type: 'submit' }, 'パスワードを設定');
  const form = el('form', {
    onSubmit: async (event) => {
      event.preventDefault();
      error.hidden = true;
      if (next.value !== again.value) { showError(error, '確認のパスワードが一致しません'); return; }
      busy(submit, true, 'パスワードを設定');
      try {
        await api.post('/api/auth/reset', { token, password: next.value });
        goLogin('新しいパスワードを設定しました。ログインしてください。');
      } catch (err) {
        showError(error, err.message);
        busy(submit, false, 'パスワードを設定');
      }
    },
  },
  error,
  el('div', { class: 'field' }, el('label', { text: '新しいパスワード（8 文字以上）' }), next),
  el('div', { class: 'field' }, el('label', { text: '新しいパスワード（確認）' }), again),
  submit);
  page(root, el('h2', { class: 'login-title', text: '新しいパスワード' }), form);
  next.focus();
}
