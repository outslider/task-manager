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
  const before = JSON.stringify([...members.entries()].sort());

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
