/* タスクの親子関係を組み替えるための共通部品。
 *
 * ドラッグ＆ドロップはタッチ端末で使えないので、スマホからでも操作できるよう
 * メニューとダイアログからも同じことができるようにしている。 */
import { api } from '../api.js';
import { store } from '../store.js';
import { el, fill, openModal, toast } from '../util.js';

/** 最大階層はサーバー側の設定（/api/meta）に合わせる。 */
export function maxDepth() {
  return Number(store.meta?.max_depth) || 8;
}

/** 親をたどる順に並べた祖先（トップレベルに近い順）。 */
export function ancestorsOf(tasks, taskId) {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  const out = [];
  let node = byId.get(byId.get(taskId)?.parent_id);
  let guard = 0;
  while (node && guard < 25) {
    out.unshift(node);
    node = byId.get(node.parent_id);
    guard += 1;
  }
  return out;
}

export function depthOf(tasks, taskId) {
  return ancestorsOf(tasks, taskId).length;
}

/** taskId とその子孫の ID。親に指定できない相手を除くために使う。 */
export function subtreeIds(tasks, taskId) {
  const ids = new Set([taskId]);
  let added = true;
  let guard = 0;
  while (added && guard < 30) {
    added = false;
    guard += 1;
    for (const t of tasks) {
      if (t.parent_id !== null && ids.has(t.parent_id) && !ids.has(t.id)) {
        ids.add(t.id);
        added = true;
      }
    }
  }
  return ids;
}

/** taskId を根とする部分木の高さ（本人だけなら 0）。 */
export function subtreeHeight(tasks, taskId) {
  const children = tasks.filter((t) => t.parent_id === taskId);
  if (!children.length) return 0;
  return 1 + Math.max(...children.map((c) => subtreeHeight(tasks, c.id)));
}

/** 同じ親のタスクを、画面と同じ並び順で返す。 */
export function siblingsOf(tasks, parentId) {
  return tasks
    .filter((t) => (t.parent_id ?? null) === (parentId ?? null))
    .sort((a, b) => (a.sort_order - b.sort_order) || (a.id - b.id));
}

/**
 * 親を付け替えて保存する。移動先の末尾に置かれる。
 * @returns {Promise<boolean>} 保存できたか
 */
export async function setParent(task, parentId, tasks) {
  const current = task.parent_id ?? null;
  const next = parentId ?? null;
  if (current === next) return false;
  if (next !== null) {
    if (subtreeIds(tasks, task.id).has(next)) {
      toast('自分自身や子タスクの下には移動できません', 'error');
      return false;
    }
    const resulting = depthOf(tasks, next) + 2 + subtreeHeight(tasks, task.id);
    if (resulting > maxDepth()) {
      toast(`階層が深すぎます（最大 ${maxDepth()} 階層）`, 'error');
      return false;
    }
  }
  try {
    await api.patch(`/api/tasks/${task.id}`, { parent_id: next });
    return true;
  } catch (error) {
    toast(error.message, 'error');
    return false;
  }
}

/** 直前の兄弟タスクの子にする（アウトライナーのインデント）。 */
export function indentTarget(tasks, task) {
  const siblings = siblingsOf(tasks, task.parent_id ?? null);
  const index = siblings.findIndex((t) => t.id === task.id);
  return index > 0 ? siblings[index - 1] : null;
}

/** 親と同じ階層に上げる。トップレベルなら null（＝これ以上上げられない）。 */
export function outdentTarget(tasks, task) {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  return task.parent_id ? byId.get(task.parent_id) ?? null : null;
}

/**
 * 親タスクを選ぶダイアログ。
 * @returns {Promise<number|null|undefined>} 選んだ親の ID / トップレベルなら null /
 *   キャンセルなら undefined
 */
export function openParentPicker(task, tasks) {
  const blocked = subtreeIds(tasks, task.id);
  const height = subtreeHeight(tasks, task.id);
  const current = task.parent_id ?? null;
  let chosen = current;

  const candidates = tasks.filter((t) => !blocked.has(t.id));
  const list = el('div', { class: 'parent-pick-list' });
  const search = el('input', {
    class: 'input', type: 'search', placeholder: 'タスク名で絞り込む',
  });

  const draw = () => {
    const keyword = search.value.trim().toLowerCase();
    const rows = [row(null, 'トップレベルに置く', 0, false)];
    for (const t of candidates) {
      const depth = depthOf(tasks, t.id);
      const tooDeep = depth + 2 + height > maxDepth();
      if (keyword && !t.title.toLowerCase().includes(keyword)) continue;
      rows.push(row(t.id, t.title, depth + 1, tooDeep));
    }
    fill(list, ...rows);
    if (rows.length === 1 && keyword) {
      list.appendChild(el('div', { class: 'empty', text: '一致するタスクがありません' }));
    }
    // 長いプロジェクトでも今の親がすぐ目に入るようにする
    const active = list.querySelector('.parent-pick.active');
    if (active) active.scrollIntoView({ block: 'nearest' });
  };

  function row(id, label, indent, disabled) {
    return el('button', {
      type: 'button',
      class: `parent-pick${chosen === id ? ' active' : ''}${disabled ? ' disabled' : ''}`,
      style: { paddingLeft: `${10 + indent * 16}px` },
      disabled: disabled ? true : null,
      title: disabled ? `ここに入れると最大 ${maxDepth()} 階層を超えます` : null,
      onClick: () => { chosen = id; draw(); },
    },
    el('span', { class: 'parent-pick-label', text: label }),
    id === current ? el('span', { class: 'hint', text: '現在の親' }) : null,
    disabled ? el('span', { class: 'hint', text: '階層が深すぎます' }) : null);
  }

  search.addEventListener('input', draw);
  draw();

  return openModal({
    title: `「${task.title}」の親タスク`,
    build: () => el('div', {},
      el('div', { class: 'field' }, search),
      list,
      el('div', { class: 'hint', text: height
        ? `このタスクには子タスクがあるため、移動先の階層は ${height + 1} 段ぶん必要です。`
        : '一覧では、行を他の行の中央にドラッグしても子タスクにできます。' })),
    footer: (close) => [
      el('button', { class: 'btn', onClick: () => close(undefined) }, 'キャンセル'),
      el('button', {
        class: 'btn btn-primary',
        onClick: () => close(chosen === current ? undefined : chosen),
      }, 'ここに移動する'),
    ],
  });
}
