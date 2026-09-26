/* プロジェクトのメンバーと権限。プロジェクト管理者（と管理者）が、追加・外す・権限の変更をする。
   いまのメンバーだけを並べ、足したい人やグループは検索して加える。 */
import { api } from '../api.js';
import { store } from '../store.js';
import { avatar, el, fill, openModal, toast } from '../util.js';

const ROLE_RANK = { viewer: 1, commenter: 2, editor: 3, owner: 4 };
const GUEST_MAX_ROLE = 'commenter';
const ROLE_FALLBACK = { owner: 'プロジェクト管理者', editor: '編集可', commenter: 'コメント可', viewer: '閲覧のみ' };

function roleName(role) {
  const found = (store.meta.project_roles || []).find((r) => r.value === role);
  return found ? found.label.split('（')[0] : (ROLE_FALLBACK[role] || '—');
}

/* 選んでいる権限と、実際に効く権限が違うときの説明。サーバーの auth.project_role と同じ決め方：
   管理者アカウントとプロジェクト管理者は常に最上位、それ以外は個人とグループのうち強い方、
   社外ユーザーはコメント可まで。違いがなければ null。 */
export function effectiveNote(user, members, groups, ownerId) {
  if (user.id === ownerId) return null;
  const direct = members.get(`user:${user.id}`) || '';
  if (user.role === 'admin') {
    return { text: '管理者アカウントなので、実際はどのプロジェクトでもプロジェクト管理者です' };
  }
  const via = [];
  let best = direct;
  for (const group of groups) {
    const role = members.get(`group:${group.id}`);
    if (!role || !group.members.some((m) => m.id === user.id)) continue;
    via.push({ name: group.name, role });
    if ((ROLE_RANK[role] || 0) > (ROLE_RANK[best] || 0)) best = role;
  }
  const strongest = via.filter((v) => v.role === best).map((v) => v.name).join('・');
  if (user.role === 'guest' && (ROLE_RANK[best] || 0) > ROLE_RANK[GUEST_MAX_ROLE]) {
    if (direct === GUEST_MAX_ROLE) return null;
    const why = `社外ユーザーの上限。${strongest} では${roleName(best)}`;
    if (!direct) return { text: `${strongest} 経由で${roleName(GUEST_MAX_ROLE)}（${why}）` };
    return { text: `実際は${roleName(GUEST_MAX_ROLE)}です（${why}）`, warn: true };
  }
  if (best === direct) return null;
  if (!direct) return { text: `${strongest} 経由で${roleName(best)}` };
  return { text: `実際は${roleName(best)}です（${strongest} 経由。個別の設定より強いため）`, warn: true };
}

/** メンバーの画面を開く。保存したら true で解決する。 */
export async function openMembers(projectId) {
  const [detail, groupData] = await Promise.all([api.project(projectId), api.groups()]);
  const project = detail.project;
  const groups = groupData.groups || [];
  const members = new Map();
  for (const m of project.members) members.set(`${m.principal_type}:${m.id}`, m.role);
  let before = JSON.stringify([...members.entries()].sort());

  // 候補：社内・社外のユーザーとグループ
  const everyone = [
    ...store.users.map((u) => ({
      key: `user:${u.id}`, type: 'user', id: u.id, name: u.name, obj: u, guest: u.role === 'guest',
      sub: u.role === 'guest' ? `社外ユーザー・${u.organization_name || '会社未設定'}` : (u.email || ''),
      search: `${u.name} ${u.email || ''} ${u.organization_name || ''}`.toLowerCase(),
    })),
    ...groups.map((g) => ({
      key: `group:${g.id}`, type: 'group', id: g.id, name: g.name, obj: g,
      sub: `グループ・${g.members.length} 名（${g.members.slice(0, 4).map((m) => m.name).join('、')}`
        + `${g.members.length > 4 ? ' ほか' : ''}）`,
      search: `${g.name} ${g.members.map((m) => m.name).join(' ')}`.toLowerCase(),
    })),
  ];
  const byKey = new Map(everyone.map((row) => [row.key, row]));
  const userRow = (u) => ({
    key: `user:${u.id}`, type: 'user', id: u.id, name: u.name, obj: u, guest: u.role === 'guest',
    sub: u.role === 'guest' ? `社外ユーザー・${u.organization_name || '会社未設定'}` : (u.email || ''),
    search: `${u.name} ${u.email || ''} ${u.organization_name || ''}`.toLowerCase(),
  });
  const isOwnerRow = (row) => row.type === 'user' && row.id === project.owner_id;

  const listHost = el('div', { class: 'member-list' });
  const countEl = el('span', { class: 'badge' });
  const changedEl = el('span', { class: 'hint', style: { margin: '0 auto 0 0' } });
  const search = el('input', { class: 'input', type: 'search', placeholder: '名前・メールアドレス・会社名・グループ名で探す' });
  const candHost = el('div', { class: 'member-cands' });

  const allowedRoles = (row) => (row.guest
    ? store.meta.project_roles.filter((r) => ['commenter', 'viewer'].includes(r.value))
    : store.meta.project_roles);
  const defaultRole = (row) => (row.guest ? GUEST_MAX_ROLE : 'editor');

  function memberRow(row) {
    const owner = isOwnerRow(row);
    const role = members.get(row.key);
    const select = el('select', { class: 'select member-role', disabled: owner ? true : null },
      ...allowedRoles(row).map((r) => el('option', {
        value: r.value, selected: role === r.value ? true : null,
      }, r.label.split('（')[0])));
    select.addEventListener('change', () => { members.set(row.key, select.value); draw(); });
    const note = row.type === 'user' ? effectiveNote(row.obj, members, groups, project.owner_id) : null;
    return el('div', { class: 'att-item member-row' },
      row.type === 'user'
        ? avatar(row.obj, 'sm')
        : el('span', { class: 'avatar sm', style: { background: '#98a2b3' } }, '👥'),
      el('div', { class: 'name' },
        el('div', {}, row.name,
          owner ? el('span', { class: 'owner-badge', text: 'プロジェクト管理者' }) : null,
          row.guest ? el('span', { class: 'guest-badge', text: '社外' }) : null),
        el('div', { class: 'hint', text: row.sub })),
      select,
      el('button', {
        class: 'icon-btn member-remove', type: 'button',
        title: owner ? 'プロジェクト管理者は外せません（プロジェクト設定で交代できます）' : 'このプロジェクトから外す',
        disabled: owner ? true : null,
        onClick: () => { members.delete(row.key); draw(); },
      }, '×'),
      note ? el('div', { class: 'note-host' },
        el('div', { class: `role-note${note.warn ? ' warn' : ''}`, text: note.text })) : null);
  }

  function drawCandidates() {
    const query = search.value.trim().toLowerCase();
    const pool = everyone.filter((row) => !members.has(row.key) && (!query || row.search.includes(query)));
    const shown = pool.slice(0, query ? 12 : 6);
    fill(candHost,
      ...shown.map((row) => el('button', {
        type: 'button', class: 'member-cand',
        onClick: () => {
          members.set(row.key, defaultRole(row));
          search.value = '';
          draw();
          toast(`${row.name} を${roleName(defaultRole(row))}で追加しました（保存で確定）`, 'ok');
        },
      },
      row.type === 'user' ? avatar(row.obj, 'sm')
        : el('span', { class: 'avatar sm', style: { background: '#98a2b3' } }, '👥'),
      el('span', { class: 'member-cand-name' },
        el('span', { text: row.name }),
        row.guest ? el('span', { class: 'guest-badge', text: '社外' }) : null,
        el('span', { class: 'hint', text: row.sub })),
      el('span', { class: 'member-cand-add', text: '＋ 追加' }))),
      pool.length > shown.length
        ? el('div', { class: 'hint', text: `ほか ${pool.length - shown.length} 件。名前で絞り込めます` })
        : null,
      !pool.length
        ? el('div', { class: 'hint', text: query ? '当てはまる人・グループはいません（アカウントの作成は管理者が行います）' : '追加できる人・グループはもういません' })
        : null);
  }

  function draw() {
    const rows = [...members.keys()].map((key) => byKey.get(key)).filter(Boolean)
      .sort((a, b) => (isOwnerRow(b) - isOwnerRow(a)) || (a.type === b.type ? 0 : a.type === 'user' ? -1 : 1)
        || a.name.localeCompare(b.name, 'ja'));
    fill(listHost, ...rows.map(memberRow));
    countEl.textContent = `${rows.length}`;
    const changed = JSON.stringify([...members.entries()].sort()) !== before;
    changedEl.textContent = changed ? '変更があります（保存で確定します）' : '';
    drawCandidates();
  }
  search.addEventListener('input', drawCandidates);
  draw();

  return openModal({
    title: `${project.name} のメンバーと権限`,
    wide: true,
    build: () => el('div', {},
      el('div', { class: 'member-section-head' }, el('strong', { text: 'メンバー' }), countEl),
      listHost,
      el('div', { class: 'member-section-head', style: { marginTop: '18px' } }, el('strong', { text: '＋ メンバーを追加' })),
      search,
      candHost,
      (store.meta.creatable_account_roles || []).length
        ? el('div', { class: 'member-create' },
          el('span', { class: 'hint', text: 'まだアカウントの無い人は' }),
          el('button', {
            type: 'button', class: 'btn btn-sm',
            onClick: async () => {
              const made = await createAccount(project);
              if (!made) return;
              const row = userRow(made.user);
              everyone.push(row);
              byKey.set(row.key, row);
              members.set(row.key, made.project_role);
              // サーバーではもうメンバーになっているので、「変更あり」には数えない
              before = JSON.stringify([...members.entries()].sort());
              store.setUsers([...store.users, made.user]);
              store.forgetMembers(project.id);
              draw();
            },
          }, '＋ 新しいアカウントを作って追加'))
        : null,
      el('p', { class: 'hint', style: { marginTop: '12px' },
        text: 'ユーザー個別、またはグループ単位で権限を付けられます。両方に当てはまる人は強い方の権限が効きます'
          + '（個別の設定でグループより下げることはできません）。社外ユーザーは「コメント可」までで、'
          + '担当になったタスクの進捗・状態は自分で更新できます。' })),
    footer: (close) => [
      changedEl,
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', {
        class: 'btn btn-primary',
        onClick: async () => {
          const payload = [...members.entries()].map(([key, role]) => {
            const [principal_type, id] = key.split(':');
            return { principal_type, principal_id: Number(id), role };
          });
          try {
            await api.put(`/api/projects/${project.id}/members`, { members: payload });
            store.forgetMembers(project.id);
            await store.refreshProjects();
            toast('メンバーを更新しました', 'ok');
            close(true);
          } catch (error) { toast(error.message, 'error'); }
        },
      }, '保存'),
    ],
  });
}

/** 新しい人のアカウントを作って、このプロジェクトに入れる。作れたら { user, project_role } を返す。 */
async function createAccount(project) {
  const kinds = store.meta.creatable_account_roles || [];
  const kind = el('select', { class: 'select' },
    ...kinds.map((value) => el('option', { value }, value === 'guest' ? '社外ユーザー（協力会社・お客さま）' : '社内ユーザー')));
  const name = el('input', { class: 'input', placeholder: '例）山田 太郎', autocomplete: 'off' });
  const email = el('input', { class: 'input', type: 'email', placeholder: 'taro@example.co.jp', autocomplete: 'off' });
  const company = el('input', { class: 'input', placeholder: '例）○○株式会社', list: 'member-org-list' });
  const orgList = el('datalist', { id: 'member-org-list' });
  const expires = el('input', { class: 'input', type: 'date' });
  const role = el('select', { class: 'select' });
  const drawRoles = () => {
    const guest = kind.value === 'guest';
    const choices = store.meta.project_roles.filter((r) => r.value !== 'owner'
      && (!guest || ['commenter', 'viewer'].includes(r.value)));
    fill(role, ...choices.map((r) => el('option', {
      value: r.value, selected: r.value === (guest ? 'commenter' : 'editor') ? true : null,
    }, r.label.split('（')[0])));
    guestFields.hidden = !guest;
  };
  const guestFields = el('div', {},
    el('div', { class: 'row' },
      el('div', { class: 'field' }, el('label', { text: '会社名 *' }), company, orgList),
      el('div', { class: 'field' }, el('label', { text: '有効期限（任意）' }), expires)),
    el('div', { class: 'hint', style: { marginTop: '-4px', marginBottom: '10px' },
      text: '社外ユーザーは、参加しているプロジェクトの中だけを見られます。権限は「コメント可」までで、担当になったタスクの進捗は自分で更新できます。' }));
  kind.addEventListener('change', drawRoles);
  drawRoles();
  api.get('/api/organizations').then((data) => fill(orgList,
    ...(data.organizations || []).map((o) => el('option', { value: o.name })))).catch(() => {});

  const error = el('div', { class: 'login-error', hidden: true });
  const made = await openModal({
    title: `新しいアカウントを作って「${project.name}」に追加`,
    build: (close) => el('form', {
      onSubmit: (event) => { event.preventDefault(); submit(close); },
    },
    error,
    kinds.length > 1 ? el('div', { class: 'field' }, el('label', { text: '種類' }), kind) : null,
    el('div', { class: 'field' }, el('label', { text: '氏名 *' }), name),
    el('div', { class: 'field' }, el('label', { text: 'メールアドレス *（ログインに使います）' }), email),
    guestFields,
    el('div', { class: 'field' }, el('label', { text: 'このプロジェクトでの権限' }), role),
    el('div', { class: 'hint',
      text: '作ると初期パスワードを一度だけ表示します。本人に安全な方法で伝えてください。'
        + 'アカウントの編集・停止・削除は管理者が行います。' }),
    el('button', { type: 'submit', hidden: true })),
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', { class: 'btn btn-primary', onClick: () => submit(close) }, '作成して追加'),
    ],
  });
  if (made) await showInitialPassword(made.user.email, made.initial_password);
  return made;

  async function submit(close) {
    error.hidden = true;
    const body = {
      role: kind.value || kinds[0], name: name.value.trim(), email: email.value.trim(),
      project_role: role.value,
    };
    if (body.role === 'guest') {
      body.organization = company.value.trim();
      if (expires.value) body.expires_on = expires.value;
    }
    try {
      close(await api.post(`/api/projects/${project.id}/accounts`, body));
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    }
  }
}

function showInitialPassword(email, password) {
  const copy = `ログイン: ${location.origin}${location.pathname}\nメールアドレス: ${email}\n初期パスワード: ${password}`;
  return openModal({
    title: 'アカウントを作りました',
    build: () => el('div', {},
      el('p', { text: 'この画面を閉じると、初期パスワードは二度と表示できません。本人に安全な方法で伝えてください（ログイン後、プロフィール設定で変えられます）。' }),
      el('div', { class: 'field' }, el('label', { text: 'メールアドレス' }),
        el('input', { class: 'input', value: email, readonly: true })),
      el('div', { class: 'field' }, el('label', { text: '初期パスワード' }),
        el('input', { class: 'input mono', value: password, readonly: true })),
      el('button', {
        class: 'btn btn-sm', type: 'button',
        onClick: () => navigator.clipboard?.writeText(copy).then(() => toast('コピーしました', 'ok')),
      }, 'ログイン情報をまとめてコピー')),
    footer: (close) => [el('button', { class: 'btn btn-primary', onClick: () => close(true) }, '控えました')],
  });
}

/** プロジェクト管理者の「社外ユーザー（〇〇社）として、このプロジェクトを見る」。見るだけ・30 分。 */
export async function openGuestPreview(project) {
  const data = await api.get(`/api/projects/${project.id}/preview`);
  const orgs = data.organizations || [];
  const select = el('select', { class: 'select' },
    ...orgs.map((o) => el('option', { value: o.id }, o.in_project ? `${o.name}（このプロジェクトに参加中）` : o.name)),
    el('option', { value: '' }, '会社を指定しない'));
  const ok = await openModal({
    title: `「${project.name}」を社外ユーザーとして見る`,
    build: () => el('div', {},
      el('p', { class: 'page-sub',
        text: '実在の人ではなく、このプロジェクトだけに「コメント可」で入っている社外ユーザーとして画面を表示します。' }),
      el('div', { class: 'field' }, el('label', { text: 'どの会社の人として見るか' }), select,
        el('div', { class: 'hint', text: 'チケットは、その会社のものだけが見えます。' })),
      el('ul', { class: 'preview-notes' },
        el('li', { text: '見るだけです。変更・コメント・起票はできません' }),
        el('li', { text: '社外ユーザーに見せていないタブ・社内のみの添付やメモ・ほかの会社のチケットは出ません' }),
        el('li', { text: 'このプロジェクトの外（ほかのプロジェクトなど）は見えません' }),
        el('li', { text: '30 分で自動的に自分の表示に戻ります' }))),
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(false) }, 'キャンセル'),
      el('button', { class: 'btn btn-primary', onClick: () => close(true) }, '社外ユーザーとして見る'),
    ],
  });
  if (!ok) return;
  try {
    await api.post(`/api/projects/${project.id}/preview`, { organization_id: select.value ? Number(select.value) : null });
    location.hash = `#/p/${project.id}/tasks`;
    location.reload();
  } catch (error) { toast(error.message, 'error'); }
}
