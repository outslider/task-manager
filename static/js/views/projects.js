/* Project list, creation, settings and member/permission management. */
import { api } from '../api.js';
import { projectTile, setHeader } from '../app.js';
import { store } from '../store.js';
import { avatar, clear, confirmDialog, el, fill, openModal, toast } from '../util.js';
import { iconLabel } from '../icons.js';
import { projectColors } from '../theme.js';
import { openMembers } from './members.js';

export async function render(container) {
  let projects = await store.refreshProjects();
  const state = { showArchived: false };

  setHeader('プロジェクト', [
    el('button', {
      class: 'btn', title: '取っておいた一式から、プロジェクトごと起こします',
      onClick: async () => {
        const { openTemplates } = await import('./templates.js');
        await openTemplates({ onApplied: reload });
      },
    }, ...iconLabel('blocks', '雛形')),
    el('button', { class: 'btn btn-primary', onClick: () => editProject(null) }, ...iconLabel('plus', '新規プロジェクト')),
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
    return el('div', {
      class: `card proj-card${project.archived ? ' archived' : ''}`,
      style: projectColors(project.color),
    },
      el('a', {
        class: 'proj-banner', href: `#/p/${project.id}/tasks`,
        dataset: { pattern: project.theme || 'aurora' },
      },
        projectTile(project, 'md'),
        el('span', { class: 'proj-banner-name', text: project.name }),
        el('span', { class: 'proj-banner-badges' },
          project.archived ? el('span', { class: 'hero-chip', text: 'アーカイブ' }) : null,
          el('span', { class: 'hero-chip', text: roleLabel(project.my_role) }))),
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
        memberStrip(project, isOwner),
        el('div', { style: { display: 'flex', gap: '6px', marginTop: '12px', flexWrap: 'wrap' } },
          el('a', { class: 'btn btn-sm', href: `#/p/${project.id}/tasks` }, 'タスク'),
          // 使っていないタブ（社外ユーザーに見せていないタブ）への近道は出さない
          (!project.tabs || project.tabs.includes('gantt'))
            ? el('a', { class: 'btn btn-sm', href: `#/p/${project.id}/gantt` }, 'ガント') : null,
          (!project.tabs || project.tabs.includes('issues'))
            ? el('a', { class: 'btn btn-sm', href: `#/p/${project.id}/issues` }, '課題') : null,
          isOwner
            ? el('button', { class: 'btn btn-sm', onClick: () => manageMembers(project) }, 'メンバー')
            : null,
          isOwner
            ? el('button', { class: 'btn btn-sm', onClick: () => editProject(project) }, '設定')
            : null)));
  }

  /** 誰が参加しているか一目で分かるように、カードに顔を並べる。 */
  function memberStrip(project, isOwner) {
    const people = project.members || [];
    if (!people.length) {
      return isOwner
        ? el('div', { class: 'member-strip' },
          el('button', {
            class: 'btn btn-sm', onClick: () => manageMembers(project),
          }, '＋ メンバーを追加'))
        : null;
    }
    const shown = people.slice(0, 6);
    return el('div', {
      class: `member-strip${isOwner ? ' clickable' : ''}`,
      title: people.map((u) => u.name).join('、'),
      onClick: isOwner ? () => manageMembers(project) : null,
    },
    el('span', { class: 'avatar-stack' }, ...shown.map((u) => avatar(u, 'sm'))),
    people.length > shown.length
      ? el('span', { class: 'cell-mut', text: `＋${people.length - shown.length}` })
      : null,
    el('span', { class: 'cell-mut', text: `${people.length} 名` }));
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
    const notifyEnabled = el('input', { type: 'checkbox' });
    notifyEnabled.checked = project ? Boolean(project.notify_enabled) : true;
    const slackEventKeys = (project?.slack_events || '').split(',').filter(Boolean);
    const slackEventBoxes = (store.meta?.slack_events || []).map((event) => {
      const box = el('input', { type: 'checkbox' });
      box.checked = slackEventKeys.includes(event.value);
      return { event, box };
    });
    const perProjectEvents = el('input', { type: 'checkbox' });
    perProjectEvents.checked = slackEventKeys.length > 0;
    const syncSlackEvents = () => {
      for (const { box } of slackEventBoxes) {
        box.disabled = !perProjectEvents.checked || !notifyEnabled.checked;
      }
    };
    perProjectEvents.addEventListener('change', syncSlackEvents);
    notifyEnabled.addEventListener('change', syncSlackEvents);
    syncSlackEvents();
    // タブの出し分け。「使う」は全員向け（画面をすっきりさせるため）、「社外にも見せる」は
    // 社外ユーザー向け（見せないものはサーバーでも閉じる）。タスクはいつも出す
    const TAB_CHOICES = [['gantt', 'ガント'], ['workload', '負荷'], ['bottlenecks', 'ボトルネック'],
      ['issues', '課題'], ['tickets', 'チケット']];
    const hiddenNow = new Set(project?.tabs_hidden || []);
    const guestNow = new Set(project?.guest_tabs || ['tasks', 'gantt', 'issues', 'tickets']);
    const tabRows = TAB_CHOICES.map(([key, label]) => {
      const use = el('input', { type: 'checkbox', checked: hiddenNow.has(key) ? null : true });
      const guest = el('input', {
        type: 'checkbox', checked: guestNow.has(key) && key !== 'workload' ? true : null,
        disabled: key === 'workload' ? true : null,
      });
      const sync = () => { guest.disabled = key === 'workload' || !use.checked; };
      use.addEventListener('change', sync);
      sync();
      return { key, label, use, guest };
    });
    const tabTable = el('table', { class: 'tab-table' },
      el('thead', {}, el('tr', {},
        el('th', { text: 'タブ' }), el('th', { text: '使う' }), el('th', { text: '社外ユーザーにも見せる' }))),
      el('tbody', {},
        el('tr', {}, el('td', { text: 'タスク' }), el('td', { text: '常に' }), el('td', { text: '常に' })),
        ...tabRows.map((row) => el('tr', {},
          el('td', { text: row.label }),
          el('td', {}, row.use),
          el('td', {}, row.guest,
            row.key === 'workload' ? el('span', { class: 'hint', text: ' 担当者の工数が並ぶため見せません' }) : null)))));

    // プロジェクト管理者は社内の人だけ（社外ユーザーはコメント可まで）
    const ownerSelect = el('select', { class: 'select' },
      ...store.users.filter((u) => u.role !== 'guest').map((u) => el('option', {
        value: u.id, selected: (project?.owner_id ?? store.user.id) === u.id ? true : null,
      }, u.name)));

    // ---- 見た目：色・帯の模様・アイコン。選ぶとその場で見本が変わる ----
    const look = {
      color: project?.color || (store.meta?.project_colors || ['#4f6bff'])[
        store.projects.length % (store.meta?.project_colors?.length || 1)],
      theme: project?.theme || 'aurora',
      icon: project?.icon || '',
    };
    color.value = look.color;
    const preview = el('div', { class: 'look-preview' });
    const drawPreview = () => {
      const sample = { name: name.value.trim() || 'プロジェクト名', color: look.color, icon: look.icon };
      fill(preview, el('div', {
        class: 'proj-head mini', dataset: { pattern: look.theme }, style: projectColors(look.color),
      },
      el('div', { class: 'proj-hero' },
        projectTile(sample, 'lg'),
        el('div', { class: 'proj-hero-text' },
          el('div', { class: 'proj-hero-name', text: sample.name }),
          el('div', { class: 'proj-hero-desc', text: description.value.split('\n')[0] || '説明がここに出ます' }))),
      el('div', { class: 'proj-tabs' },
        el('span', { class: 'proj-tab active' }, 'タスク'),
        el('span', { class: 'proj-tab' }, 'ガント'),
        el('span', { class: 'proj-tab' }, '課題'))));
      for (const [key, value] of Object.entries(projectColors(look.color))) patternHost.style.setProperty(key, value);
      for (const node of swatchHost.children) node.classList.toggle('active', node.dataset.value === look.color);
      for (const node of patternHost.children) node.classList.toggle('active', node.dataset.value === look.theme);
      for (const node of iconHost.children) node.classList.toggle('active', node.dataset.value === look.icon);
    };
    const swatchHost = el('div', { class: 'swatches' },
      ...(store.meta?.project_colors || []).map((value) => el('button', {
        type: 'button', class: 'swatch', dataset: { value }, style: { background: value },
        title: value, onClick: () => { look.color = value; color.value = value; drawPreview(); },
      })));
    color.addEventListener('input', () => { look.color = color.value; drawPreview(); });
    const PATTERN_LABEL = {
      aurora: 'オーロラ', mesh: 'メッシュ', lines: 'ストライプ', dots: 'ドット', waves: 'ウェーブ', plain: '無地',
    };
    const patternHost = el('div', { class: 'pattern-picks' },
      ...(store.meta?.project_themes || Object.keys(PATTERN_LABEL)).map((value) => el('button', {
        type: 'button', class: 'pattern-pick', dataset: { value },
        onClick: () => { look.theme = value; drawPreview(); },
      },
      el('span', { class: 'pattern-sample proj-head', dataset: { pattern: value } }),
      el('span', { text: PATTERN_LABEL[value] || value }))));
    const ICONS = ['', '🚀', '📦', '🏗️', '💻', '🖥️', '🗄️', '🔧', '📈', '🎯', '📣', '🧪', '🛒', '🏢', '🌏',
      '🔒', '📱', '🎨', '📚', '🤝', '⚡', '🌱', '🏭', '🚚', '💡'];
    const iconHost = el('div', { class: 'icon-picks' },
      ...ICONS.map((value) => el('button', {
        type: 'button', class: 'icon-pick', dataset: { value },
        title: value ? value : '名前の頭文字',
        onClick: () => { look.icon = value; drawPreview(); },
      }, value || (Array.from(name.value.trim() || '頭')[0]))));
    name.addEventListener('input', drawPreview);
    description.addEventListener('input', drawPreview);
    drawPreview();

    const slackTest = el('button', {
      class: 'btn btn-sm', type: 'button',
      onClick: async () => {
        slackTest.disabled = true;
        try {
          const result = project
            ? await api.post('/api/settings/test-slack', {
              project_id: project.id, webhook_url: slack.value.trim(),
            })
            : { ok: false, message: 'プロジェクトを作ったあとで試せます' };
          toast(result.message, result.ok ? 'ok' : 'error');
        } catch (error) { toast(error.message, 'error'); }
        slackTest.disabled = false;
      },
    }, 'テスト送信');
    const slackNote = !store.meta?.slack_enabled
      ? el('div', { class: 'warn-box', style: { marginTop: '8px' },
        text: '管理者設定で Slack 通知がオフになっているため、いまは送られません（テスト送信はできます）。' })
      : null;

    const panes = {
      basic: el('div', {},
        el('div', { class: 'field' }, el('label', { text: 'プロジェクト名 *' }), name),
        el('div', { class: 'field' }, el('label', { text: '説明' }), description),
        el('div', { class: 'field' }, el('label', { text: 'プロジェクト管理者' }), ownerSelect),
        project
          ? el('div', { class: 'field' },
            el('label', { class: 'check' }, archived,
              el('span', { text: 'アーカイブする（一覧から隠す）' })))
          : null),
      look: el('div', {},
        preview,
        el('div', { class: 'field' }, el('label', { text: '色' }),
          el('div', { class: 'swatch-row' }, swatchHost, el('label', { class: 'swatch-custom', title: '好きな色' }, color))),
        el('div', { class: 'field' }, el('label', { text: '帯の模様' }), patternHost),
        el('div', { class: 'field' }, el('label', { text: 'アイコン' }), iconHost),
        el('div', { class: 'hint',
          text: 'プロジェクトの画面では、見出しの帯・ボタン・背景がこの色になります（各自のプロフィール設定で止められます）。' })),
      notify: el('div', {},
        el('div', { class: 'field' },
          el('label', { class: 'check' }, notifyEnabled,
            el('span', { text: 'このプロジェクトの通知を送る' })),
          el('div', { class: 'hint',
            text: 'オフにすると、メールも Slack も一切送りません（画面の通知も止まります）。' })),
        el('div', { class: 'field' },
          el('label', { text: 'Slack の通知先（このプロジェクト用のチャンネル）' }),
          el('div', { class: 'input-with-btn' }, slack, slackTest),
          el('div', { class: 'hint',
            text: store.meta?.slack_has_default
              ? '空欄なら、管理者設定の全体のチャンネルに送られます。'
              : '空欄なら送りません（全体のチャンネルは設定されていません）。' }),
          slackNote),
        slackEventBoxes.length
          ? el('div', { class: 'field' },
            el('label', { class: 'check' }, perProjectEvents,
              el('span', { text: 'Slack に流す種類をこのプロジェクトだけ変える' })),
            el('div', { class: 'check-list' },
              ...slackEventBoxes.map(({ event, box }) => el('label', { class: 'check check-row' },
                box,
                el('span', {},
                  el('span', { text: event.label }),
                  el('span', { class: 'hint', text: event.help }))))),
            el('div', { class: 'hint',
              text: 'チェックしない場合は管理者設定の選択に従います。' }))
          : null),
      tabs: project
        ? el('div', {},
          tabTable,
          el('div', { style: { margin: '10px 0' } },
            el('button', {
              class: 'btn btn-sm', type: 'button',
              onClick: async () => {
                const { openGuestPreview } = await import('./members.js');
                openGuestPreview(project);
              },
            }, '👁 社外ユーザーとしてプレビュー'),
            el('span', { class: 'hint', text: '　保存したあとの見え方を、社外ユーザーの立場で確かめられます' })),
          el('div', { class: 'hint',
            text: '「使う」を外したタブは、このプロジェクトの画面から隠れます。'
              + '社外ユーザーに見せないタブは、画面だけでなくデータも社外ユーザーには閉じます'
              + '（ガントはタスク一覧と同じデータなので、隠れるのは画面だけです）。' }))
        : null,
    };
    const PANE_LABEL = { basic: '基本', look: '見た目', notify: '通知', tabs: 'タブ' };
    const paneHost = el('div', { class: 'pane-host' });
    const paneTabs = el('div', { class: 'seg pane-tabs' });
    const showPane = (key) => {
      fill(paneHost, panes[key]);
      for (const node of paneTabs.children) node.classList.toggle('active', node.dataset.key === key);
    };
    for (const key of Object.keys(panes)) {
      if (!panes[key]) continue;
      paneTabs.append(el('button', { type: 'button', dataset: { key }, onClick: () => showPane(key) }, PANE_LABEL[key]));
    }
    showPane('basic');

    const result = await openModal({
      title: project ? 'プロジェクト設定' : '新規プロジェクト',
      wide: true,
      build: () => el('div', {}, paneTabs, paneHost),
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
              color: look.color,
              theme: look.theme,
              icon: look.icon,
              owner_id: Number(ownerSelect.value),
              slack_webhook_url: slack.value.trim(),
              notify_enabled: notifyEnabled.checked,
              slack_events: perProjectEvents.checked
                ? slackEventBoxes.filter(({ box }) => box.checked).map(({ event }) => event.value)
                : [],
            };
            if (project) {
              payload.archived = archived.checked;
              payload.tabs_hidden = tabRows.filter((row) => !row.use.checked).map((row) => row.key);
              payload.guest_tabs = ['tasks', ...tabRows
                .filter((row) => row.use.checked && row.guest.checked && row.key !== 'workload')
                .map((row) => row.key)];
            }
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
    if (await openMembers(project.id)) reload();
  }

  draw();
}

function roleLabel(role) {
  return { owner: 'プロジェクト管理者', editor: '編集可', commenter: 'コメント可', viewer: '閲覧のみ' }[role] || '—';
}
