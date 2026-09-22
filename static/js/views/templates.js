/* 雛形。よくある一式を取っておき、何度でも起こせるようにする。
 *
 * 中では日付を「起点から何日目か」で持っているので、使うときに起点の日を
 * 決めれば、そのぶんずれた日付が入る。毎回日付を直す手間が消える。 */
import { api } from '../api.js';
import { store } from '../store.js';
import {
  confirmDialog, el, fill, formatDate, openModal,
  skeleton, toast, today, toISO,
} from '../util.js';
import { icon } from '../icons.js';
import { option } from './pickers.js';

const SCOPE_ICON = { project: '📁', tasks: '🧩' };

/**
 * 雛形の一覧。使う・名前を変える・消すができる。
 * project を渡すと、そのプロジェクトを雛形にする導線も出す。
 */
export async function openTemplates({ scope = '', project = null, onApplied } = {}) {
  const listHost = el('div', {});
  let applied = false;

  const load = async () => {
    fill(listHost, skeleton('rows', 3));
    try {
      const data = await api.get(`/api/templates${scope ? `?scope=${scope}` : ''}`);
      fill(listHost, ...(data.templates.length
        ? data.templates.map(row)
        : [el('div', { class: 'empty' },
          icon('blocks', { size: 30 }),
          '雛形はまだありません',
          el('div', { class: 'hint', style: { marginTop: '8px' },
            text: 'タスクの ⋯ メニューの「雛形として保存」、または'
              + 'プロジェクト画面から作れます。' }))]));
    } catch (error) {
      fill(listHost, el('div', { class: 'empty', text: error.message }));
    }
  };

  const row = (tpl) => el('div', { class: 'rec-row' },
    el('div', { style: { minWidth: 0 } },
      el('div', { style: { display: 'flex', gap: '8px', alignItems: 'center',
        flexWrap: 'wrap' } },
      el('strong', { text: `${SCOPE_ICON[tpl.scope] || ''} ${tpl.name}` }),
      el('span', { class: 'badge doing', text: `${tpl.task_count} タスク` }),
      el('span', { class: 'badge',
        text: tpl.scope === 'project' ? 'プロジェクト一式' : 'タスクのかたまり' })),
      el('div', { class: 'hint' },
        tpl.description ? `${tpl.description} ・ ` : '',
        `${tpl.created_by_name || '不明'} が作成`)),
    el('div', { style: { display: 'flex', gap: '6px' } },
      el('button', {
        class: 'btn btn-sm btn-primary',
        onClick: async () => {
          if (await openApplyForm(tpl)) { applied = true; load(); }
        },
      }, 'この雛形から作る'),
      el('button', {
        class: 'btn btn-sm',
        onClick: async () => { if (await openRenameForm(tpl)) load(); },
      }, '名前'),
      el('button', {
        class: 'icon-btn', title: '削除',
        onClick: async () => {
          if (!await confirmDialog(`雛形「${tpl.name}」を削除しますか？`,
            { danger: true, okLabel: '削除' })) return;
          try {
            await api.del(`/api/templates/${tpl.id}`);
            toast('削除しました', 'ok');
            load();
          } catch (error) { toast(error.message, 'error'); }
        },
      }, '×')));

  await openModal({
    title: '雛形',
    wide: true,
    build: () => {
      load();
      return el('div', {},
        el('p', { class: 'page-sub',
          text: '「サーバー移設一式」のような、毎回ほぼ同じ作業のかたまりを取っておけます。'
            + '中の日付は起点からの日数で覚えているので、使うときに開始日を決めるだけです。' }),
        listHost);
    },
    footer: (close) => [
      project
        ? el('button', {
          class: 'btn', style: { marginRight: 'auto' },
          title: 'いま開いているプロジェクトの構成を雛形にします',
          onClick: async () => { if (await openSaveTemplate({ project })) load(); },
        }, `＋ 「${project.name}」を雛形にする`)
        : null,
      el('button', { class: 'btn', onClick: () => close(null) }, '閉じる'),
    ],
  });
  if (applied && onApplied) onApplied();
  return applied;
}

/** 雛形として保存する（タスクのかたまり、またはプロジェクト一式）。 */
export async function openSaveTemplate({ task = null, project = null }) {
  const scope = task ? 'tasks' : 'project';
  const f = {};
  const result = await openModal({
    title: '雛形として保存',
    build: () => {
      f.name = el('input', {
        class: 'input', maxlength: 200,
        value: task ? task.title : (project?.name || ''),
      });
      f.description = el('textarea', { class: 'textarea', rows: 2, maxlength: 1000 });
      return el('div', {},
        el('p', { class: 'page-sub',
          text: task
            ? `「${task.title}」とその配下を、いつでも起こせる雛形にします。`
            : `「${project.name}」のタスク構成を、新しいプロジェクトの雛形にします。` }),
        el('div', { class: 'hint', style: { marginBottom: '10px' },
          text: '担当者・状態・進捗・コメント・添付は雛形には入りません。'
            + '日付は「起点から何日目か」として覚えます。' }),
        el('div', { class: 'field' }, el('label', { text: '雛形の名前 *' }), f.name),
        el('div', { class: 'field' }, el('label', { text: '説明' }), f.description));
    },
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', {
        class: 'btn btn-primary',
        onClick: async (event) => {
          const name = f.name.value.trim();
          if (!name) { toast('雛形の名前を入れてください', 'error'); return; }
          const button = event.currentTarget;
          button.disabled = true;
          try {
            const data = await api.post('/api/templates', {
              name, description: f.description.value, scope,
              task_id: task?.id, project_id: project?.id,
            });
            toast(`雛形「${name}」を保存しました（${data.template.task_count} タスク）`, 'ok');
            close(data.template);
          } catch (error) {
            toast(error.message, 'error');
            button.disabled = false;
          }
        },
      }, '保存'),
    ],
  });
  return result;
}

/** 雛形を使う。プロジェクト一式なら新しいプロジェクトを作る。 */
export async function openApplyForm(tpl) {
  const f = {};
  const forProject = tpl.scope === 'project';
  const editable = store.projects.filter((p) => !p.archived && store.canEdit(p));

  const result = await openModal({
    title: `雛形「${tpl.name}」から作る`,
    build: () => {
      f.start = el('input', { class: 'input', type: 'date', value: toISO(today()) });
      if (forProject) {
        f.name = el('input', { class: 'input', maxlength: 200, value: tpl.name });
        return el('div', {},
          el('p', { class: 'page-sub',
            text: `${tpl.task_count} 件のタスクを持つプロジェクトを新しく作ります。` }),
          el('div', { class: 'field' },
            el('label', { text: 'プロジェクト名 *' }), f.name),
          el('div', { class: 'field' },
            el('label', { text: '開始日' }), f.start,
            el('div', { class: 'hint', text: 'この日を 1 日目として日程を置きます。' })));
      }

      const parentHost = el('div', {});
      f.parent = null;
      const drawParents = async () => {
        f.parent = el('select', { class: 'select' }, option('', '（いちばん上に置く）', true));
        fill(parentHost, f.parent);
        const pid = Number(f.project.value);
        if (!pid) return;
        try {
          const data = await api.projectTasks(pid);
          const { parentCandidates } = await import('./recurrence.js');
          f.parent = el('select', { class: 'select' },
            option('', '（いちばん上に置く）', true),
            ...parentCandidates(data.tasks).map((t) =>
              option(t.id, `${'　'.repeat(t.depth)}${t.title}`)));
          fill(parentHost, f.parent);
        } catch { /* 取れなくても、いちばん上には置ける */ }
      };
      f.project = el('select', { class: 'select' },
        ...editable.map((p) => option(p.id, p.name)));
      f.project.addEventListener('change', drawParents);
      drawParents();

      return el('div', {},
        el('p', { class: 'page-sub',
          text: `${tpl.task_count} 件のタスクを、選んだところに差し込みます。` }),
        el('div', { class: 'field' },
          el('label', { text: '差し込み先のプロジェクト *' }), f.project),
        el('div', { class: 'field' },
          el('label', { text: 'まとめる親タスク' }), parentHost,
          el('div', { class: 'hint', text: '指定するとその子として入ります。' })),
        el('div', { class: 'field' },
          el('label', { text: '開始日' }), f.start,
          el('div', { class: 'hint', text: 'この日を 1 日目として日程を置きます。' })));
    },
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', {
        class: 'btn btn-primary',
        onClick: async (event) => {
          const button = event.currentTarget;
          const payload = { start_date: f.start.value || null };
          if (forProject) {
            payload.name = f.name.value.trim();
            if (!payload.name) { toast('プロジェクト名を入れてください', 'error'); return; }
          } else {
            if (!f.project.value) { toast('差し込み先を選んでください', 'error'); return; }
            payload.project_id = Number(f.project.value);
            if (f.parent?.value) payload.parent_id = Number(f.parent.value);
          }
          button.disabled = true;
          try {
            const data = await api.post(`/api/templates/${tpl.id}/apply`, payload);
            toast(`${data.created} 件のタスクを作りました`, 'ok');
            close(data);
          } catch (error) {
            toast(error.message, 'error');
            button.disabled = false;
          }
        },
      }, '作る'),
    ],
  });
  if (result && forProject) {
    await store.refreshProjects();
    location.hash = `#/p/${result.project.id}/tasks`;
  } else if (result) {
    location.hash = `#/p/${result.project_id}/tasks`;
  }
  return result;
}

async function openRenameForm(tpl) {
  const f = {};
  return openModal({
    title: '雛形の名前',
    build: () => {
      f.name = el('input', { class: 'input', maxlength: 200, value: tpl.name });
      f.description = el('textarea', { class: 'textarea', rows: 2, maxlength: 1000 });
      f.description.value = tpl.description || '';
      return el('div', {},
        el('div', { class: 'field' }, el('label', { text: '名前 *' }), f.name),
        el('div', { class: 'field' }, el('label', { text: '説明' }), f.description),
        el('div', { class: 'hint',
          text: `${formatDate(tpl.created_at)} 作成 ・ ${tpl.task_count} タスク` }));
    },
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(null) }, 'キャンセル'),
      el('button', {
        class: 'btn btn-primary',
        onClick: async (event) => {
          const button = event.currentTarget;
          button.disabled = true;
          try {
            await api.patch(`/api/templates/${tpl.id}`, {
              name: f.name.value.trim(), description: f.description.value,
            });
            toast('保存しました', 'ok');
            close(true);
          } catch (error) {
            toast(error.message, 'error');
            button.disabled = false;
          }
        },
      }, '保存'),
    ],
  });
}
