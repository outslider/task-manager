/* Project list, creation, settings and member/permission management. */
import { api } from '../api.js';
import { setHeader } from '../app.js';
import { store } from '../store.js';
import { avatar, clear, confirmDialog, el, fill, openModal, toast } from '../util.js';

export async function render(container) {
  let projects = await store.refreshProjects();
  const state = { showArchived: false };

  setHeader('プロジェクト', [
    el('button', { class: 'btn btn-primary', onClick: () => editProject(null) }, '＋ 新規プロジェクト'),
  ]);

  const grid = el('div', { class: 'grid cols-3' });
  const archivedToggle = el('label', { class: 'check' },
    el('input', {
      type: 'checkbox',
      onChange: (event) => { state.showArchived = event.target.checked; draw(); },
    }), el('span', { text: 'アーカイブ済みも表示' }));

  fill(container, 
    el('div', { class: 'page-head' },
      el('div', { class: 'grow' },
        el('div', { class: 'page-sub', text: '参加しているプロジェクトの一覧です。' })),
      archivedToggle),
    grid);

  async function reload() {
    projects = await store.refreshProjects();
    draw();
  }

  function draw() {
    const visible = projects.filter((p) => state.showArchived || !p.archived);
    clear(grid);
    if (visible.length === 0) {
      grid.append(el('div', { class: 'card' },
        el('div', { class: 'empty' },
          el('div', { class: 'big', text: '📁' }),
          'プロジェクトがありません',
          el('div', { style: { marginTop: '12px' } },
            el('button', { class: 'btn btn-primary', onClick: () => editProject(null) },
              '最初のプロジェクトを作る')))));
      return;
    }
    visible.forEach((project) => grid.append(card(project)));
  }

  function card(project) {
    const stats = project.stats
      || { total: 0, done: 0, overdue: 0, milestones: 0, blocked: 0, open_issues: 0 };
    const percent = stats.total ? Math.round((stats.done / stats.total) * 100) : 0;
    const isOwner = project.my_role === 'owner';
    return el('div', { class: 'card' },
      el('div', { class: 'card-head' },
        el('span', {
          class: 'nav-dot',
          style: { background: project.color, width: '10px', height: '10px' },
        }),
        el('h2', {}, el('a', { href: `#/p/${project.id}/tasks`, text: project.name })),
        project.archived ? el('span', { class: 'badge', text: 'アーカイブ' }) : null,
        el('span', { class: 'badge', text: roleLabel(project.my_role) })),
      el('div', { class: 'card-body' },
        el('div', { class: 'page-sub', style: { minHeight: '20px' },
          text: project.description || '（説明なし）' }),
        el('div', { style: { display: 'flex', gap: '10px', margin: '10px 0 4px', alignItems: 'center' } },
          el('span', { class: 'cell-mut', text: `${stats.done} / ${stats.total} 完了` }),
          stats.overdue
            ? el('span', { class: 'badge overdue', text: `期限超過 ${stats.overdue}` })
            : null,
          stats.blocked
            ? el('span', { class: 'badge blocking', text: `⛔ 待ち ${stats.blocked}` })
            : null,
          stats.open_issues
            ? el('span', { class: 'badge pending', text: `📌 課題 ${stats.open_issues}` })
            : null,
          stats.milestones
            ? el('span', { class: 'badge', text: `◆ ${stats.milestones}` })
            : null),
        el('div', { class: `progress${percent >= 100 ? ' done' : ''}` },
          el('i', { style: { width: `${percent}%` } })),
        el('div', { style: { display: 'flex', gap: '6px', marginTop: '12px', flexWrap: 'wrap' } },
          el('a', { class: 'btn btn-sm', href: `#/p/${project.id}/tasks` }, 'タスク'),
          el('a', { class: 'btn btn-sm', href: `#/p/${project.id}/gantt` }, 'ガント'),
          el('a', { class: 'btn btn-sm', href: `#/p/${project.id}/issues` }, '課題'),
          isOwner
            ? el('button', { class: 'btn btn-sm', onClick: () => manageMembers(project) }, 'メンバー')
            : null,
          isOwner
            ? el('button', { class: 'btn btn-sm', onClick: () => editProject(project) }, '設定')
            : null)));
  }

  async function editProject(project) {
    const name = el('input', { class: 'input', placeholder: '例）新製品リリース' });
    name.value = project?.name || '';
    const description = el('textarea', { class: 'textarea', placeholder: '目的や概要' });
    description.value = project?.description || '';
    const color = el('input', { type: 'color', class: 'input', style: { height: '38px', padding: '2px' } });
    color.value = project?.color || '#4f8cff';
    const archived = el('input', { type: 'checkbox' });
    archived.checked = Boolean(project?.archived);
    const slack = el('input', {
      class: 'input', placeholder: 'https://hooks.slack.com/services/…（任意）',
    });
    slack.value = project?.slack_webhook_url || '';
    const ownerSelect = el('select', { class: 'select' },
      ...store.users.map((u) => el('option', {
        value: u.id, selected: (project?.owner_id ?? store.user.id) === u.id ? true : null,
      }, u.name)));

    const result = await openModal({
      title: project ? 'プロジェクト設定' : '新規プロジェクト',
      build: () => el('div', {},
        el('div', { class: 'field' }, el('label', { text: 'プロジェクト名 *' }), name),
        el('div', { class: 'field' }, el('label', { text: '説明' }), description),
        el('div', { class: 'row' },
          el('div', { class: 'field' }, el('label', { text: '色' }), color),
          el('div', { class: 'field' }, el('label', { text: 'オーナー' }), ownerSelect)),
        el('div', { class: 'field' },
          el('label', { text: 'Slack の通知先' }), slack,
          el('div', { class: 'hint',
            text: '空欄なら全体設定のチャンネルに送られます。' })),
        project
          ? el('div', { class: 'field' },
            el('label', { class: 'check' }, archived,
              el('span', { text: 'アーカイブする（一覧から隠す）' })))
          : null),
      footer: (close) => [
        project
          ? el('button', {
            class: 'btn btn-danger', style: { marginRight: 'auto' },
            onClick: async () => {
              if (!await confirmDialog(
                `「${project.name}」を削除します。\nタスク・コメント・添付もすべて削除され、元に戻せません。`,
                { danger: true, okLabel: '完全に削除する' })) return;
              await api.del(`/api/projects/${project.id}`);
              toast('プロジェクトを削除しました', 'ok');
              close('deleted');
            },
          }, '削除')
          : null,
        el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
        el('button', {
          class: 'btn btn-primary',
          onClick: async () => {
            const payload = {
              name: name.value.trim(),
              description: description.value,
              color: color.value,
              owner_id: Number(ownerSelect.value),
              slack_webhook_url: slack.value.trim(),
            };
            if (project) payload.archived = archived.checked;
            if (!payload.name) { toast('プロジェクト名を入力してください', 'error'); return; }
            try {
              if (project) await api.patch(`/api/projects/${project.id}`, payload);
              else await api.post('/api/projects', payload);
              toast('保存しました', 'ok');
              close('saved');
            } catch (error) { toast(error.message, 'error'); }
          },
        }, '保存'),
      ],
    });
    if (result) reload();
  }

  async function manageMembers(project) {
    const [detail, groupData] = await Promise.all([api.project(project.id), api.groups()]);
    const members = new Map();
    for (const m of detail.project.members) {
      members.set(`${m.principal_type}:${m.id}`, m.role);
    }
    const listHost = el('div', {});

    const drawList = () => {
      clear(listHost);
      const rows = [
        ...store.users.map((u) => ({ type: 'user', id: u.id, name: u.name, sub: u.email, obj: u })),
        ...groupData.groups.map((g) => ({
          type: 'group', id: g.id, name: g.name,
          sub: `${g.members.length} 名`, obj: g,
        })),
      ];
      for (const row of rows) {
        const key = `${row.type}:${row.id}`;
        const current = members.get(key) || '';
        const select = el('select', { class: 'select', style: { maxWidth: '150px' } },
          el('option', { value: '', selected: current === '' ? true : null }, 'アクセスなし'),
          ...store.meta.project_roles.map((r) => el('option', {
            value: r.value, selected: current === r.value ? true : null,
          }, r.label.split('（')[0])));
        select.addEventListener('change', () => {
          if (select.value) members.set(key, select.value); else members.delete(key);
        });
        const isOwner = detail.project.owner_id === row.id && row.type === 'user';
        if (isOwner) select.disabled = true;
        listHost.append(el('div', { class: 'att-item' },
          row.type === 'user'
            ? avatar(row.obj, 'sm')
            : el('span', { class: 'avatar sm', style: { background: '#98a2b3' } }, '👥'),
          el('div', { class: 'name' },
            el('div', { text: row.name + (isOwner ? '（オーナー）' : '') }),
            el('div', { class: 'hint', text: row.sub })),
          select));
      }
    };
    drawList();

    const saved = await openModal({
      title: `${project.name} のメンバーと権限`,
      wide: true,
      build: () => el('div', {},
        el('p', { class: 'page-sub',
          text: 'ユーザー個別、またはグループ単位で権限を設定できます。両方に該当する場合は強い方の権限が適用されます。' }),
        listHost),
      footer: (close) => [
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
              toast('メンバーを更新しました', 'ok');
              close(true);
            } catch (error) { toast(error.message, 'error'); }
          },
        }, '保存'),
      ],
    });
    if (saved) reload();
  }

  draw();
}

function roleLabel(role) {
  return { owner: 'オーナー', editor: '編集可', commenter: 'コメント可', viewer: '閲覧のみ' }[role] || '—';
}
