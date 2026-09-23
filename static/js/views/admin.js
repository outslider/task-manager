/* Administration: users, groups and system settings. */
import { api } from '../api.js';
import { setHeader } from '../app.js';
import { store } from '../store.js';
import { avatar, clear, confirmDialog, el, fill, openModal, toast } from '../util.js';
import { ACCENT_PRESETS, applyAccent } from '../theme.js';
import { iconPicker } from './pickers.js';

export async function render(container, route) {
  if (!store.isAdmin()) {
    fill(container, el('div', { class: 'card' },
      el('div', { class: 'empty' }, el('div', { class: 'big', text: '🔒' }),
        'このページは管理者のみ利用できます')));
    return;
  }
  const tab = route.tab || 'users';
  const titles = {
    users: 'ユーザー管理', groups: 'グループ管理', settings: 'システム設定',
    taxonomy: '状態とカテゴリ',
    queues: 'チケット窓口',
  };
  setHeader(titles[tab]);
  if (tab === 'users') await renderUsers(container);
  else if (tab === 'groups') await renderGroups(container);
  else if (tab === 'taxonomy') await renderTaxonomy(container);
  else if (tab === 'queues') await renderQueues(container);
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
            class: 'btn btn-sm btn-quiet-danger',
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
          el('div', { style: { display: 'flex', gap: '6px' } },
            el('button', { class: 'btn btn-sm', onClick: () => editGroup(group) }, '編集'),
            el('button', {
              class: 'btn btn-sm btn-quiet-danger',
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
  /** 会社独自の休業日（年末年始や夏季休暇）を足し引きする小さな一覧。 */
  const holidayEditor = () => {
    const host = el('div', { class: 'holiday-list' });
    const dayInput = el('input', { class: 'input', type: 'date', style: { maxWidth: '160px' } });
    const nameInput = el('input', { class: 'input', placeholder: '例）年末年始休業' });
    const year = new Date().getFullYear();

    const load = async () => {
      try {
        const result = await api.holidays({ from: `${year}-01-01`, to: `${year + 1}-12-31` });
        const company = (result.holidays || []).filter((h) => h.company);
        fill(host, ...(company.length
          ? company.map((h) => el('div', { class: 'holiday-row' },
            el('span', { text: `${h.day}　${h.name}` }),
            el('button', {
              class: 'icon-btn', title: '削除',
              onClick: async () => {
                await api.del(`/api/holidays/${h.day}`);
                load();
              },
            }, '×')))
          : [el('div', { class: 'hint', text: '会社独自の休業日はまだありません' })]));
      } catch (error) { toast(error.message, 'error'); }
    };

    load();
    return el('div', { class: 'field' },
      el('label', { text: '会社の休業日（祝日以外）' }),
      host,
      el('div', { style: { display: 'flex', gap: '6px', marginTop: '8px', flexWrap: 'wrap' } },
        dayInput, nameInput,
        el('button', {
          class: 'btn btn-sm',
          onClick: async (event) => {
            if (!dayInput.value) { toast('日付を選んでください', 'error'); return; }
            const button = event.currentTarget;
            button.disabled = true;
            try {
              await api.post('/api/holidays', {
                day: dayInput.value, name: nameInput.value.trim() || '休業日',
              });
              dayInput.value = ''; nameInput.value = '';
              load();
            } catch (error) { toast(error.message, 'error'); }
            button.disabled = false;
          },
        }, '＋ 追加')),
      el('div', { class: 'hint',
        text: `${year}年と${year + 1}年ぶんを表示しています。祝日は自動で入るので、ここには追加不要です。` }));
  };

  const eventPicker = (key, label, catalog, hint) => {
    const selected = new Set(String(s[key] || '').split(',').filter(Boolean));
    const boxes = (catalog || []).map((event) => {
      const node = el('input', { type: 'checkbox', checked: selected.has(event.value) ? true : null });
      return { event, node };
    });
    fields[key] = () => boxes.filter(({ node }) => node.checked)
      .map(({ event }) => event.value).join(',');
    return el('div', { class: 'field' },
      el('label', { text: label }),
      el('div', { class: 'check-list' },
        ...boxes.map(({ event, node }) => el('label', { class: 'check check-row' },
          node,
          el('span', {},
            el('span', { text: event.label }),
            el('span', { class: 'hint', text: event.help }))))),
      hint ? el('div', { class: 'hint', text: hint }) : null);
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
              const button = event.currentTarget;
              button.disabled = true;
              try {
                await save();
                const result = await api.post('/api/settings/test-mail',
                  { to: testTo.value.trim() || store.user.email });
                toast(result.message, result.ok ? 'ok' : 'error');
              } catch (error) { toast(error.message, 'error'); }
              button.disabled = false;
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
              const button = event.currentTarget;
              button.disabled = true;
              try {
                await save();
                const result = await api.post('/api/admin/run-digest', {});
                toast(`${result.sent} 名に通知しました（期限通知 ${result.due_notifications} 件）`, 'ok');
                store.refreshUnread().catch(() => {});
              } catch (error) { toast(error.message, 'error'); }
              button.disabled = false;
            },
          }, '今すぐ日次サマリを送る')))),
    el('div', { class: 'grid cols-2', style: { marginTop: '14px' } },
      el('div', { class: 'card' },
        el('div', { class: 'card-head' }, el('h2', {}, 'Slack 通知（任意）'),
          el('span', {
            class: `badge ${data.slack_ready ? 'done' : 'todo'}`,
            text: data.slack_ready ? '有効' : '無効',
          })),
        el('div', { class: 'card-body' },
          el('div', { class: 'page-sub',
            text: '期限超過・本日期限のまとめと、影響度の高い課題の起票を Slack に流します。'
              + 'こちらから送るだけなので、インターネットへの公開は不要です。' }),
          toggle('slack_enabled', 'Slack 通知を使う'),
          input('slack_webhook_url', 'Incoming Webhook URL',
            { placeholder: 'https://hooks.slack.com/services/...' }),
          eventPicker('slack_events', 'Slack に流す内容', data.slack_events,
            'プロジェクト設定で個別に上書きできます。'),
          el('div', { class: 'hint',
            text: 'プロジェクトごとに別のチャンネルへ送りたい場合も、プロジェクト設定で指定できます。' }),
          el('button', {
            class: 'btn', style: { marginTop: '10px' },
            onClick: async (event) => {
              const button = event.currentTarget;
              button.disabled = true;
              try {
                await save();
                const result = await api.post('/api/settings/test-slack', {});
                toast(result.message, result.ok ? 'ok' : 'error');
              } catch (error) { toast(error.message, 'error'); }
              button.disabled = false;
            },
          }, 'テスト送信'))),
      el('div', { class: 'card' },
        el('div', { class: 'card-head' }, el('h2', {}, '稼働時間と休日')),
        el('div', { class: 'card-body' },
          el('div', { class: 'page-sub',
            text: '負荷ビューで「1人が週にどれだけ持てるか」の基準に使います。' }),
          input('work_hours_per_day', '1日の稼働時間 (h)', { type: 'number' }),
          el('div', { class: 'hint', text: '土日を除いた5日分が1週間の上限になります（既定 8h → 40h/週）。' }),
          toggle('use_holidays', '日本の祝日を休みとして扱う',
            'ガントで網掛けし、負荷計算ではその週に使える時間を減らします。'
            + '春分・秋分、振替休日、国民の休日も自動で計算します。'),
          holidayEditor()))),
    el('div', { class: 'card', style: { marginTop: '14px' } },
      el('div', { class: 'card-head' }, el('h2', {}, 'Claude 連携（任意）'),
        el('span', {
          class: `badge ${data.llm_ready ? 'done' : 'todo'}`,
          text: data.llm_ready ? '有効' : '無効',
        })),
      el('div', { class: 'card-body' },
        el('div', { class: 'page-sub',
          text: '自然言語からのタスク登録と、タスクの自動分解に Claude を使います。'
            + '未設定でもキーワードと定型テンプレートによる簡易解析で動作します。' }),
        data.llm_sdk
          ? null
          : el('div', { class: 'warn-box',
            text: 'サーバーに anthropic パッケージが入っていません。'
              + '有効にするには pip install anthropic を実行してください。' }),
        toggle('llm_enabled', 'Claude API を使う'),
        input('llm_api_key', 'API キー', { type: 'password', placeholder: 'sk-ant-...' }),
        modelField(),
        el('div', { class: 'hint',
          text: 'タスクの文面が Anthropic に送信されます。社内規程を確認のうえ有効にしてください。' }),
        el('button', {
          class: 'btn', style: { marginTop: '10px' },
          onClick: async (event) => {
            const button = event.currentTarget;
            button.disabled = true;
            try {
              await save();
              const result = await api.post('/api/settings/test-llm', {});
              toast(result.message, result.ok ? 'ok' : 'error');
            } catch (error) { toast(error.message, 'error'); }
            button.disabled = false;
          },
        }, '接続をテスト'))),
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
          const button = event.currentTarget;
          button.disabled = true;
          try {
            await save();
            toast('設定を保存しました', 'ok');
          } catch (error) { toast(error.message, 'error'); }
          button.disabled = false;
        },
      }, '設定を保存')));

  function modelField() {
    const node = el('select', { class: 'select' },
      ...(data.llm_models || []).map(([value, label]) =>
        el('option', { value, selected: s.llm_model === value ? true : null }, label)));
    fields.llm_model = () => node.value;
    return el('div', { class: 'field' }, el('label', { text: 'モデル' }), node);
  }

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


/* ------------------------------------------------------- 状態とカテゴリ */

async function renderTaxonomy(container) {
  const data = await api.get('/api/admin/taxonomy');
  const statuses = data.statuses.map((s) => ({ ...s }));
  const categories = data.categories.map((c) => ({ ...c }));

  const statusHost = el('div', {});
  const categoryHost = el('div', {});

  /** 色見本。並べて選べるようにして、色がばらけないようにする。 */
  const swatches = (current, onPick) => {
    const options = ['#98a2b3', '#3b6ef5', '#9061f9', '#17a673', '#e14c4c',
      '#e8912b', '#0ea5e9', '#14b8a6', '#8b5cf6', '#ef4444', '#64748b', '#1e293b'];
    const row = el('div', { class: 'swatch-row' });
    const draw = (value) => {
      fill(row, ...options.map((color) => el('button', {
        type: 'button', class: `swatch sm${color === value ? ' active' : ''}`,
        style: { background: color }, title: color,
        onClick: () => { onPick(color); draw(color); },
      })), custom);
    };
    const custom = el('input', {
      type: 'color', class: 'input', value: current, title: '色を自由に選ぶ',
      onInput: (event) => { onPick(event.target.value); },
    });
    draw(current);
    return row;
  };

  function drawStatuses() {
    fill(statusHost, ...statuses.map((status, index) => {
      const label = el('input', { class: 'input', value: status.label, maxlength: 40 });
      label.addEventListener('input', () => { status.label = label.value; });
      const chip = el('span', { class: 'badge', style: { background: status.color, color: '#fff' },
        text: status.label || status.value });
      label.addEventListener('input', () => { chip.textContent = label.value || status.value; });
      return el('div', { class: 'tx-row' },
        el('div', { class: 'tx-key' }, chip),
        label,
        swatches(status.color, (color) => {
          status.color = color;
          chip.style.background = color;
        }),
        el('div', { style: { display: 'flex', gap: '2px' } },
          el('button', {
            class: 'icon-btn', title: '上へ', disabled: index === 0 ? true : null,
            onClick: () => { move(statuses, index, -1); drawStatuses(); },
          }, '↑'),
          el('button', {
            class: 'icon-btn', title: '下へ',
            disabled: index === statuses.length - 1 ? true : null,
            onClick: () => { move(statuses, index, 1); drawStatuses(); },
          }, '↓')));
    }));
  }

  function drawCategories() {
    fill(categoryHost, ...categories.map((cat, index) => {
      const label = el('input', { class: 'input', value: cat.label, maxlength: 60,
        placeholder: 'カテゴリ名' });
      label.addEventListener('input', () => { cat.label = label.value; });
      const iconButton = el('button', {
        class: 'tx-icon', type: 'button', title: '記号を選ぶ',
        onClick: () => pickIcon(cat, iconButton),
      }, cat.icon || '＋');
      return el('div', { class: 'tx-row' },
        el('div', { class: 'tx-key' }, iconButton),
        label,
        swatches(cat.color, (color) => { cat.color = color; }),
        el('div', { style: { display: 'flex', gap: '2px', alignItems: 'center' } },
          cat.used
            ? el('span', { class: 'hint', title: `${cat.used} 件のタスクで使われています`,
              text: `${cat.used}件` })
            : null,
          el('button', {
            class: 'icon-btn', title: '上へ', disabled: index === 0 ? true : null,
            onClick: () => { move(categories, index, -1); drawCategories(); },
          }, '↑'),
          el('button', {
            class: 'icon-btn', title: '下へ',
            disabled: index === categories.length - 1 ? true : null,
            onClick: () => { move(categories, index, 1); drawCategories(); },
          }, '↓'),
          el('button', {
            class: 'icon-btn', title: '削除',
            onClick: async () => {
              if (cat.used && !await confirmDialog(
                `「${cat.label}」は ${cat.used} 件のタスクで使われています。\n`
                + '削除すると、それらは「未分類」に戻ります。',
                { danger: true, okLabel: '削除する' })) return;
              categories.splice(index, 1);
              drawCategories();
            },
          }, '×')));
    }));
    if (!categories.length) {
      fill(categoryHost, el('div', { class: 'hint', text: 'カテゴリがありません' }));
    }
  }

  async function pickIcon(cat, button) {
    const chosen = await openModal({
      title: '記号を選ぶ',
      build: (close) => el('div', {},
        el('p', { class: 'page-sub',
          text: '見た目を揃えるため、ここに用意した記号から選びます。' }),
        el('div', { class: 'icon-grid' },
          ...data.icons.map((icon) => el('button', {
            type: 'button', class: `icon-pick${icon === cat.icon ? ' active' : ''}`,
            onClick: () => close(icon),
          }, icon)))),
      footer: (close) => [
        el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      ],
    });
    if (chosen) {
      cat.icon = chosen;
      button.textContent = chosen;
    }
  }

  const move = (list, index, direction) => {
    const to = index + direction;
    if (to < 0 || to >= list.length) return;
    list.splice(to, 0, list.splice(index, 1)[0]);
  };

  drawStatuses();
  drawCategories();

  const paletteSelect = el('select', { class: 'select', style: { maxWidth: '200px' } },
    el('option', { value: '' }, '配色をまとめて選ぶ…'),
    ...data.palettes.map((p) => el('option', { value: p.key }, p.label)));
  paletteSelect.addEventListener('change', () => {
    const palette = data.palettes.find((p) => p.key === paletteSelect.value);
    if (!palette) return;
    for (const status of statuses) {
      if (palette.colors[status.value]) status.color = palette.colors[status.value];
    }
    paletteSelect.value = '';
    drawStatuses();
  });

  const save = async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const result = await api.put('/api/admin/taxonomy', { statuses, categories });
      await store.loadBase();
      toast(result.removed?.length
        ? `保存しました（${result.removed.length} 件のカテゴリを削除）`
        : '保存しました', 'ok');
      renderTaxonomy(container);
    } catch (error) {
      toast(error.message, 'error');
      button.disabled = false;
    }
  };

  fill(container,
    el('div', { class: 'card' },
      el('div', { class: 'card-head' }, el('h2', {}, '状態'), paletteSelect),
      el('div', { class: 'card-body' },
        el('p', { class: 'page-sub', text: data.status_note }),
        statusHost)),
    el('div', { class: 'card', style: { marginTop: '14px' } },
      el('div', { class: 'card-head' },
        el('h2', {}, 'カテゴリ'),
        el('button', {
          class: 'btn btn-sm',
          onClick: () => {
            categories.push({ value: '', label: '新しいカテゴリ', color: '#3b6ef5',
              icon: data.icons[0], used: 0 });
            drawCategories();
          },
        }, '＋ 追加')),
      el('div', { class: 'card-body' },
        el('p', { class: 'page-sub',
          text: '並び順・名前・色・記号を変えられます。'
            + '削除したカテゴリを使っていたタスクは「未分類」に戻ります。' }),
        categoryHost)),
    el('div', { style: { marginTop: '14px' } },
      el('button', { class: 'btn btn-primary', onClick: save }, '保存')));
}


/* --------------------------------------------------------------- 窓口 */

/** チケットの受付窓口。部署や用途ごとに分けて、閉じた窓口は受付だけ止める。 */
async function renderQueues(container) {
  const grid = el('div', { class: 'grid cols-3' });
  const kindLabel = (value) => {
    const found = (store.meta?.tickets?.kinds || []).find((k) => k.value === value);
    return found ? `${found.icon} ${found.label}` : '依頼';
  };

  fill(container,
    el('div', { class: 'page-head' },
      el('div', { class: 'grow' },
        el('div', { class: 'page-sub',
          text: 'チケットを受ける窓口です。部署や用途ごとに分けられます。'
            + '使わなくなった窓口は「受付停止」にすると、新しい起票だけ止まります。' })),
      el('button', { class: 'btn btn-primary', onClick: () => edit(null) }, '＋ 窓口を追加')),
    grid);

  async function load() {
    const { queues } = await api.get('/api/ticket-queues');
    clear(grid);
    if (!queues.length) {
      grid.append(el('div', { class: 'card' },
        el('div', { class: 'empty' }, el('div', { class: 'big', text: '📮' }), '窓口がありません')));
    }
    for (const queue of queues) {
      grid.append(el('div', { class: `card${queue.is_active ? '' : ' is-muted'}` },
        el('div', { class: 'card-head' },
          el('h2', {}, `${queue.icon || '📮'} ${queue.name}`),
          queue.is_active
            ? el('span', { class: 'badge', text: `未完了 ${queue.open_count}` })
            : el('span', { class: 'badge blocked', text: '受付停止' })),
        el('div', { class: 'card-body' },
          el('div', { class: 'page-sub', text: queue.description || '（説明なし）' }),
          el('div', { class: 'meta-row', style: { margin: '10px 0' } },
            el('span', { class: 'legend-swatch',
              style: { background: queue.color, width: '14px', height: '14px' } }),
            queue.project_id
              ? el('span', { class: 'badge',
                text: `📁 ${queue.project_name}${queue.project_archived ? '（終了）' : ''}` })
              : el('span', { class: 'hint', text: 'プロジェクトの紐づけなし' }),
            el('span', { class: 'hint', text: `全 ${queue.ticket_count} 件` })),
          el('div', { class: 'hint', style: { margin: '8px 0 4px' },
            text: `既定の種別: ${kindLabel(queue.default_kind)}` }),
          el('div', { class: 'meta-row', style: { marginBottom: '10px' } },
            queue.categories?.length
              ? el('span', { class: 'hint', text: '分類:' })
              : el('span', { class: 'hint', text: '分類なし' }),
            ...(queue.categories || []).map((cat) => el('span', {
              class: 'cat-chip',
              style: { background: `${cat.color}1f`, color: cat.color,
                borderColor: `${cat.color}55` },
            }, cat.label))),
          el('div', { style: { display: 'flex', gap: '6px' } },
            el('button', { class: 'btn btn-sm', onClick: () => edit(queue) }, '編集'),
            el('button', {
              class: 'btn btn-sm btn-quiet-danger',
              onClick: async () => {
                if (!await confirmDialog(`「${queue.name}」を削除しますか？`,
                  { danger: true, okLabel: '削除する' })) return;
                try {
                  await api.del(`/api/ticket-queues/${queue.id}`);
                  toast('削除しました', 'ok');
                  load();
                } catch (error) { toast(error.message, 'error'); }
              },
            }, '削除')))));
    }
  }

  async function edit(queue) {
    const name = el('input', { class: 'input', placeholder: '例）情シス窓口' });
    name.value = queue?.name || '';
    const description = el('input', {
      class: 'input', placeholder: '何を受ける窓口かひとこと',
    });
    description.value = queue?.description || '';
    const icon = iconPicker(queue?.icon || '',
      store.meta?.tickets?.queue_icons || ['📮']);
    const meta = store.meta?.tickets || { kinds: [] };
    const defaultKind = el('select', { class: 'select' },
      ...meta.kinds.map((k) => el('option', {
        value: k.value,
        selected: (queue?.default_kind || 'request') === k.value ? true : null,
      }, `${k.icon} ${k.label}`)));
    // 分類は窓口ごと。行を足したり消したりして、保存でまとめて反映する。
    const cats = (queue?.categories || []).map((c) => ({ ...c }));
    const catHost = el('div', { class: 'queue-cats' });
    const drawCats = () => {
      fill(catHost, ...cats.map((cat, index) => {
        const label = el('input', {
          class: 'input', value: cat.label, maxlength: 60, placeholder: '分類名',
        });
        label.addEventListener('input', () => { cat.label = label.value; });
        const color = el('input', { type: 'color', class: 'input', value: cat.color || '#98a2b3' });
        color.addEventListener('input', () => { cat.color = color.value; });
        return el('div', { class: 'queue-cat-row' },
          label, color,
          el('button', {
            class: 'icon-btn', type: 'button', title: '上へ',
            disabled: index === 0 ? true : null,
            onClick: () => { cats.splice(index - 1, 0, cats.splice(index, 1)[0]); drawCats(); },
          }, '↑'),
          el('button', {
            class: 'icon-btn', type: 'button', title: '下へ',
            disabled: index === cats.length - 1 ? true : null,
            onClick: () => { cats.splice(index + 1, 0, cats.splice(index, 1)[0]); drawCats(); },
          }, '↓'),
          el('button', {
            class: 'icon-btn', type: 'button', title: '削除',
            onClick: () => { cats.splice(index, 1); drawCats(); },
          }, '×'));
      }),
      cats.length
        ? null
        : el('div', { class: 'hint', text: '分類なしで運用します（起票時に分類は出ません）' }),
      el('button', {
        class: 'btn btn-sm', type: 'button', style: { marginTop: '6px' },
        onClick: () => { cats.push({ label: '', color: '#98a2b3' }); drawCats(); },
      }, '＋ 分類を追加'));
    };
    drawCats();
    const project = el('select', { class: 'select' },
      el('option', { value: '', selected: queue?.project_id ? null : true },
        'どのプロジェクトにも紐づけない'),
      ...store.projects.filter((p) => !p.archived || p.id === queue?.project_id)
        .map((p) => el('option', {
          value: p.id, selected: String(queue?.project_id || '') === String(p.id) ? true : null,
        }, p.name)));
    // 誰が読めるか。「メンバーだけ」はプロジェクトの紐づけが要る。
    const visibility = el('select', { class: 'select' },
      el('option', { value: 'all',
        selected: (queue?.visibility || 'all') === 'all' ? true : null },
      '社内の誰でも見られる'),
      el('option', { value: 'project',
        selected: queue?.visibility === 'project' ? true : null },
      '紐づけたプロジェクトのメンバーだけ'));
    const visNote = el('div', { class: 'hint' });
    const syncVis = () => {
      const limited = visibility.value === 'project';
      visNote.textContent = limited
        ? (project.value
          ? 'この窓口のチケットは、そのプロジェクトのメンバー（と管理者）だけに見えます。'
            + '一覧・検索・集計のどこにも出ません。'
          : '⚠ 先に上でプロジェクトを選んでください。')
        : 'この窓口のチケットは、ログインしている人なら誰でも読めます。';
      visNote.style.color = limited && !project.value ? 'var(--danger)' : '';
    };
    visibility.addEventListener('change', syncVis);
    project.addEventListener('change', syncVis);
    syncVis();

    const color = el('input', { type: 'color', class: 'input', value: queue?.color || '#3b6ef5' });
    const order = el('input', { class: 'input', type: 'number', step: '10' });
    order.value = String(queue?.sort_order ?? 0);
    const active = el('input', { type: 'checkbox' });
    active.checked = queue ? Boolean(queue.is_active) : true;

    const result = await openModal({
      title: queue ? '窓口を編集' : '窓口を追加',
      build: () => el('div', {},
        el('div', { class: 'field' }, el('label', { text: '窓口名 *' }), name),
        el('div', { class: 'field' }, el('label', { text: '説明' }), description),
        el('div', { class: 'field' },
          el('label', { text: 'プロジェクト' }), project,
          el('div', { class: 'hint',
            text: '紐づけると、この窓口のチケットを「タスクにする」とき、'
              + 'そのプロジェクトが最初から選ばれます。' })),
        el('div', { class: 'field' },
          el('label', { text: '誰が見られるか' }), visibility, visNote),
        el('div', { class: 'field' },
          el('label', { text: '起票したときの種別' }), defaultKind,
          el('div', { class: 'hint', text: 'この窓口で最初から選ばれている種別です' })),
        el('div', { class: 'field' },
          el('label', { text: '分類（この窓口だけ）' }), catHost,
          el('div', { class: 'hint',
            text: '「PC・端末」「ネットワーク」のように、この窓口の中の分け方を決められます。'
              + '消した分類を使っていたチケットは「分類なし」に戻ります。' })),
        el('div', { class: 'row' },
          el('div', { class: 'field' }, el('label', { text: '記号' }), icon.node,
            el('div', { class: 'hint', text: 'クリックして選びます' })),
          el('div', { class: 'field' }, el('label', { text: '色' }), color),
          el('div', { class: 'field' }, el('label', { text: '並び順' }), order)),
        el('div', { class: 'field' },
          el('label', { class: 'check' }, active, el('span', { text: '新しい起票を受け付ける' })))),
      footer: (close) => [
        el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
        el('button', {
          class: 'btn btn-primary',
          onClick: async () => {
            const payload = {
              name: name.value.trim(), description: description.value.trim(),
              icon: icon.value(), color: color.value,
              project_id: project.value ? Number(project.value) : null,
              visibility: visibility.value,
              default_kind: defaultKind.value,
              categories: cats.filter((c) => c.label.trim()),
              sort_order: Number(order.value) || 0, is_active: active.checked,
            };
            if (!payload.name) { toast('窓口名を入れてください', 'error'); return; }
            if (payload.visibility === 'project' && !payload.project_id) {
              toast('「メンバーだけ」にするには、プロジェクトを選んでください', 'error');
              return;
            }
            try {
              if (queue) await api.patch(`/api/ticket-queues/${queue.id}`, payload);
              else await api.post('/api/ticket-queues', payload);
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
