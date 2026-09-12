/* Login screen. */
import { api } from '../api.js';
import { el, fill } from '../util.js';
import { store } from '../store.js';
import { brandLockup } from '../brand.js';

export function renderLogin(root, onSuccess) {
  const error = el('div', {
    class: 'badge blocked', hidden: true,
    style: { display: 'block', padding: '8px 12px', borderRadius: '8px', marginBottom: '12px' },
  });
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
      submit.disabled = true;
      submit.textContent = '確認中…';
      try {
        await api.login(email.value.trim(), password.value);
        onSuccess();
      } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
        submit.disabled = false;
        submit.textContent = 'ログイン';
        password.select();
      }
    },
  },
  error,
  el('div', { class: 'field' }, el('label', { text: 'メールアドレス' }), email),
  el('div', { class: 'field' }, el('label', { text: 'パスワード' }), password),
  submit);

  fill(root, el('div', { class: 'login-page' },
    el('div', { class: 'card login-card' },
      brandLockup('lg'),
      el('p', { class: 'page-sub', text: 'アカウント情報を入力してください。' }),
      form)));
  email.focus();
}
