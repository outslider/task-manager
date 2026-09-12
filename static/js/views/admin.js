/* Administration: users, groups and system settings. */
import { api } from '../api.js';
import { setHeader } from '../app.js';
import { store } from '../store.js';
import { avatar, clear, confirmDialog, el, fill, openModal, toast } from '../util.js';
import { ACCENT_PRESETS, applyAccent } from '../theme.js';

export async function render(container, route) {
  if (!store.isAdmin()) {
    fill(container, el('div', { class: 'card' },
      el('div', { class: 'empty' }, el('div', { class: 'big', text: '🔒' }),
        'このページは管理者のみ利用できます')));
    return;
  }
  const tab = route.tab || 'users';
  const titles = { users: 'ユーザー管理', groups: 'グループ管理', settings: 'システム設定' };
  setHeader(titles[tab]);
  if (tab === 'users') await renderUsers(container);
  else if (tab === 'groups') await renderGroups(container);
  else await renderSettings(container);
}

/* ------------------------------------------------------------------ users */

async function renderUsers(container) {
  const body = el('tbody', {});
  fill(container, 
    el('div', { class: 'page-head' },
      el('div', { class: 'grow' },
        el('div', { class: 'page-sub',
          text: '社内メンバーのアカウントを管理します。管理者はすべてのプロジェクトにアクセスできます。' })),
      el('button', { class: 'btn btn-primary', onClick: () => editUser(null) }, '＋ ユーザー追加')),
    el('div', { class: 'card' }, el('div', { class: 'table-wrap' },
      el('table', { class: 'table' },
        el('thead', {}, el('tr', {},
          el('th', { text: '名前' }), el('th', { text: 'メールアドレス' }),
          el('th', { text: '権限' }), el('th', { text: 'メール通知' }),
          el('th', { text: '状態' }), el('th', {}))),
        body))));

  async function load() {
    const data = await api.users({ include_inactive: 1 });
    store.setUsers(data.users.filter((u) => u.is_active));
    clear(body);
    for (const user of data.users) {
      body.append(el('tr', {},
        el('td', {}, el('div', { style: { display: 'flex', gap: '8px', alignItems: 'center' } },
          avatar(user, 'sm'), el('span', { text: user.name }))),
        el('td', { text: user.email }),
        el('td', {}, el('span', {
          class: `badge ${user.role === 'admin' ? 'doing' : ''}`.trim(),
          text: user.role === 'admin' ? '管理者' : 'メンバー',
        })),
        el('td', { text: user.email_notify ? 'あり' : 'なし' }),
        el('td', {}, user.is_active
          ? el('span', { class: 'badge done', text: '有効' })
          : el('span', { class: 'badge blocked', text: '停止中' })),
        el('td', { style: { textAlign: 'right', whiteSpace: 'nowrap' } },
          el('button', { class: 'btn btn-sm', onClick: () => editUser(user) }, '編集'),
          ' ',
          el('button', { class: 'btn btn-sm', onClick: () => resetPassword(user) }, 'PW再発行'),
          ' ',
          el('button', {
            class: 'btn btn-sm btn-danger',
            onClick: async () => {
              if (!await confirmDialog(
                `「${user.name}」を削除します。\n担当タスクは「未割当」になります。`,
                { danger: true, okLabel: '削除する' })) return;
              try {
                await api.del(`/api/users/${user.id}`);
                toast('削除しました', 'ok');
                load();
              } catch (error) { toast(error.message, 'error'); }
            },
          }, '削除'))));
    }
  }

  async function editUser(user) {
    const name = el('input', { class: 'input' });
    name.value = user?.name || '';
    const email = el('input', { class: 'input', type: 'email' });
    email.value = user?.email || '';
    const role = el('select', { class: 'select' },
      el('option', { value: 'member', selected: user?.role !== 'admin' ? true : null }, 'メンバー'),
      el('option', { value: 'admin', selected: user?.role === 'admin' ? true : null }, '管理者'));
    const active = el('input', { type: 'checkbox' });
    active.checked = user ? Boolean(user.is_active) : true;
    const password = el('input', {
      class: 'input', type: 'text', placeholder: '空欄なら自動生成（8文字以上）',
    });

    const result = await openModal({
      title: user ? 'ユーザーを編集' : 'ユーザーを追加',
      build: () => el('div', {},
        el('div', { class: 'field' }, el('label', { text: '氏名 *' }), name),
        el('div', { class: 'field' }, el('label', { text: 'メールアドレス *' }), email),
        el('div', { class: 'row' },
          el('div', { class: 'field' }, el('label', { text: '権限' }), role),
          user
            ? el('div', { class: 'field' }, el('label', { text: 'アカウント' }),
              el('label', { class: 'check' }, active, el('span', { text: '有効にする' })))
            : null),
        user ? null : el('div', { class: 'field' },
          el('label', { text: '初期パスワード' }), password)),
      footer: (close) => [
        el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
        el('button', {
          class: 'btn btn-primary',
          onClick: async () => {
            const payload = {
              name: name.value.trim(), email: email.value.trim(), role: role.value,
            };
            if (user) payload.is_active = active.checked;
            else if (password.value) payload.password = password.value;
            try {
              if (user) {
                await api.patch(`/api/users/${user.id}`, payload);
                toast('保存しました', 'ok');
              } else {
                const created = await api.post('/api/users', payload);
                await showPassword(created.user.email, created.initial_password);
              }
              close(true);
            } catch (error) { toast(error.message, 'error'); }
          },
        }, '保存'),
      ],
    });
    if (result) load();
  }

  async function resetPassword(user) {
    if (!await confirmDialog(`「${user.name}」のパスワードを再発行しますか？`)) return;
    try {
      const result = await api.post(`/api/users/${user.id}/password`, {});
      await showPassword(user.email, result.password);
    } catch (error) { toast(error.message, 'error'); }
  }

  function showPassword(email, password) {
    return openModal({
      title: 'パスワードを控えてください',
      build: () => el('div', {},
        el('p', { text: 'この画面を閉じると再表示できません。本人に安全な方法で伝えてください。' }),
        el('div', { class: 'field' }, el('label', { text: 'メールアドレス' }),
          el('input', { class: 'input', value: email, readonly: true })),
        el('div', { class: 'field' }, el('label', { text: 'パスワード' }),
          el('input', { class: 'input', value: password, readonly: true }))),
      footer: (close) => [
        el('button', { class: 'btn btn-primary', onClick: () => close(true) }, '控えました'),
      ],
    });
  }

  await load();
}

/* ----------------------------------------------------------------- groups */

async function renderGroups(container) {
  const grid = el('div', { class: 'grid cols-3' });
  fill(container, 
    el('div', { class: 'page-head' },
      el('div', { class: 'grow' },
        el('div', { class: 'page-sub',
          text: '部署やチーム単位でまとめ、プロジェクト権限をグループごとに付与できます。' })),
      el('button', { class: 'btn btn-primary', onClick: () => editGroup(null) }, '＋ グループ追加')),
    grid);

  async function load() {
    const data = await api.groups();
    clear(grid);
    if (data.groups.length === 0) {
      grid.append(el('div', { class: 'card' },
        el('div', { class: 'empty' }, el('div', { class: 'big', text: '🏷️' }), 'グループがありません')));
    }
    for (const group of data.groups) {
      grid.append(el('div', { class: 'card' },
        el('div', { class: 'card-head' },
          el('h2', { text: group.name }),
          el('span', { class: 'badge', text: `${group.members.length} 名` })),
        el('div', { class: 'card-body' },
          el('div', { class: 'page-sub', text: group.description || '（説明なし）' }),
          el('div', { class: 'avatar-stack', style: { flexWrap: 'wrap', margin: '10px 0' } },
            ...group.members.map((m) => avatar(m, 'sm'))),
          el('div', { class: 'row', style: { gap: '6px' } },
            el('button', { class: 'btn btn-sm', onClick: () => editGroup(group) }, '編集'),
            el('button', {
              class: 'btn btn-sm btn-danger',
              onClick: async () => {
                if (!await confirmDialog(`「${group.name}」を削除しますか？`,
                  { danger: true, okLabel: '削除する' })) return;
                await api.del(`/api/groups/${group.id}`);
                toast('削除しました', 'ok');
                load();
              },
            }, '削除')))));
    }
  }

  async function editGroup(group) {
    const name = el('input', { class: 'input' });
    name.value = group?.name || '';
    const description = el('input', { class: 'input' });
    description.value = group?.description || '';
    const selected = new Set((group?.members || []).map((m) => m.id));
    const memberList = el('div', { style: { maxHeight: '260px', overflowY: 'auto' } },
      ...store.users.map((user) => {
        const check = el('input', { type: 'checkbox', checked: selected.has(user.id) ? true : null });
        check.addEventListener('change', () => {
          if (check.checked) selected.add(user.id); else selected.delete(user.id);
        });
        return el('label', { class: 'att-item', style: { cursor: 'pointer' } },
          check, avatar(user, 'sm'),
          el('span', { class: 'name', text: user.name }),
          el('span', { class: 'size', text: user.email }));
      }));

    const result = await openModal({
      title: group ? 'グループを編集' : 'グループを追加',
      build: () => el('div', {},
        el('div', { class: 'field' }, el('label', { text: 'グループ名 *' }), name),
        el('div', { class: 'field' }, el('label', { text: '説明' }), description),
        el('div', { class: 'field' }, el('label', { text: 'メンバー' }), memberList)),
      footer: (close) => [
        el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
        el('button', {
          class: 'btn btn-primary',
          onClick: async () => {
            const payload = {
              name: name.value.trim(), description: description.value, user_ids: [...selected],
            };
            try {
              if (group) await api.patch(`/api/groups/${group.id}`, payload);
              else await api.post('/api/groups', payload);
              toast('保存しました', 'ok');
              close(true);
            } catch (error) { toast(error.message, 'error'); }
          },
        }, '保存'),
      ],
    });
    if (result) load();
  }

  await load();
}

/* --------------------------------------------------------------- settings */

async function renderSettings(container) {
  const data = await api.get('/api/settings');
  const s = data.settings;
  const fields = {};

  const input = (key, label, options = {}) => {
    const node = el('input', {
      class: 'input', type: options.type || 'text', placeholder: options.placeholder || '',
    });
    node.value = s[key] ?? '';
    fields[key] = () => node.value;
    return el('div', { class: 'field' }, el('label', { text: label }), node,
      options.hint ? el('div', { class: 'hint', text: options.hint }) : null);
  };
  const toggle = (key, label, hint) => {
    const node = el('input', { type: 'checkbox', checked: s[key] === '1' ? true : null });
    fields[key] = () => (node.checked ? '1' : '0');
    return el('div', { class: 'field' },
      el('label', { class: 'check' }, node, el('span', { text: label })),
      hint ? el('div', { class: 'hint', text: hint }) : null);
  };

  const testTo = el('input', { class: 'input', placeholder: store.user.email });

  fill(container, 
    el('div', { class: 'grid cols-2' },
      el('div', { class: 'card' },
        el('div', { class: 'card-head' }, el('h2', {}, 'メール通知 (SMTP)'),
          el('span', {
            class: `badge ${data.email_ready ? 'done' : 'todo'}`,
            text: data.email_ready ? '有効' : '無効',
          })),
        el('div', { class: 'card-body' },
          toggle('email_enabled', 'メール送信を有効にする'),
          input('smtp_host', 'SMTP ホスト', { placeholder: 'smtp.example.co.jp' }),
          input('smtp_port', 'ポート', { placeholder: '587' }),
          toggle('smtp_tls', 'STARTTLS を使う',
            'ポート 465 の場合は自動的に SSL 接続になります'),
          input('smtp_user', 'ユーザー名'),
          input('smtp_password', 'パスワード', { type: 'password' }),
          input('mail_from', '送信元アドレス', { placeholder: 'task-manager@example.co.jp' }),
          input('app_base_url', 'アプリの URL',
            { placeholder: 'https://tasks.example.co.jp', hint: '通知メール内のリンクに使われます' }),
          el('div', { class: 'field' }, el('label', { text: 'テスト送信先' }), testTo),
          el('button', {
            class: 'btn',
            onClick: async (event) => {
              event.currentTarget.disabled = true;
              try {
                await save();
                const result = await api.post('/api/settings/test-mail',
                  { to: testTo.value.trim() || store.user.email });
                toast(result.message, 'ok');
              } catch (error) { toast(error.message, 'error'); }
              event.currentTarget.disabled = false;
            },
          }, 'テストメールを送る'))),
      el('div', { class: 'card' },
        el('div', { class: 'card-head' }, el('h2', {}, '日次の進捗確認')),
        el('div', { class: 'card-body' },
          toggle('daily_digest_enabled', '日次サマリを自動送信する',
            '担当タスクの期限超過・本日期限・直近期限をまとめて通知します'),
          input('daily_digest_time', '送信時刻 (HH:MM)', { placeholder: '09:00' }),
          input('due_soon_days', '「まもなく期限」とみなす日数', { type: 'number' }),
          el('button', {
            class: 'btn',
            onClick: async (event) => {
              event.currentTarget.disabled = true;
              try {
                await save();
                const result = await api.post('/api/admin/run-digest', {});
                toast(`${result.sent} 名に通知しました（期限通知 ${result.due_notifications} 件）`, 'ok');
                store.refreshUnread().catch(() => {});
              } catch (error) { toast(error.message, 'error'); }
              event.currentTarget.disabled = false;
            },
          }, '今すぐ日次サマリを送る')))),
    el('div', { class: 'card', style: { marginTop: '14px' } },
      el('div', { class: 'card-head' }, el('h2', {}, '見た目（組織の既定）')),
      el('div', { class: 'card-body' },
        el('div', { class: 'page-sub',
          text: 'ここで決めた色が全員の既定になります。各自が「プロフィール設定」で個別に上書きできます。' }),
        input('app_name', 'アプリ名', { placeholder: 'タスク管理' }),
        accentField())),
    el('div', { style: { marginTop: '14px' } },
      el('button', {
        class: 'btn btn-primary',
        onClick: async (event) => {
          event.currentTarget.disabled = true;
          try {
            await save();
            toast('設定を保存しました', 'ok');
          } catch (error) { toast(error.message, 'error'); }
          event.currentTarget.disabled = false;
        },
      }, '設定を保存')));

  function accentField() {
    const state = { value: s.ui_accent_default || '#3b6ef5' };
    fields.ui_accent_default = () => state.value;
    const swatches = el('div', { class: 'swatches' });
    const custom = el('input', {
      type: 'color', class: 'input',
      style: { height: '38px', padding: '2px', maxWidth: '80px' },
    });
    custom.value = state.value;
    const preview = el('div', { class: 'hint' });
    const draw = () => {
      fill(swatches, ...ACCENT_PRESETS.map((preset) => el('button', {
        type: 'button',
        class: `swatch${preset.value === state.value ? ' active' : ''}`,
        style: { background: preset.value }, title: preset.label,
        onClick: () => pick(preset.value),
      })));
      preview.textContent = `既定のアクセントカラー: ${state.value}`;
    };
    const pick = (value) => {
      state.value = value;
      custom.value = value;
      // 自分が個別設定していないときだけ、その場で見た目を確認できるようにする
      if (!store.user.ui_accent) applyAccent(value);
      draw();
    };
    custom.addEventListener('input', () => pick(custom.value));
    draw();
    return el('div', { class: 'field' },
      el('label', { text: '全体の色合い（アクセントカラー）' }),
      el('div', { style: { display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' } },
        swatches, custom),
      preview);
  }

  async function save() {
    const payload = {};
    for (const [key, read] of Object.entries(fields)) payload[key] = read();
    await api.put('/api/settings', { settings: payload });
  }
}
