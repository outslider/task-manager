/* 横断検索。タスク・課題・コメント・プロジェクト・自分の ToDo をまとめて探す。
 *
 * トップバーの検索窓（または「/」キー）から開く。結果はその場に重ねて出し、
 * 選ぶとそれぞれの画面へ飛ぶ。 */
import { api } from '../api.js';
import { closeAllOverlays, debounce, dueClass, el, fill, formatDate } from '../util.js';

const KIND_ROUTE = {
  project: (item) => `#/p/${item.id}/tasks`,
};

let panel = null;
let currentInput = null;

export function attachSearch(input) {
  currentInput = input;
  const run = debounce(async () => {
    const keyword = input.value.trim();
    if (keyword.length < 2) { hide(); return; }
    try {
      const data = await api.get('/api/search', { q: keyword });
      show(input, data);
    } catch { hide(); }
  }, 220);

  input.addEventListener('input', run);
  input.addEventListener('focus', () => { if (input.value.trim().length >= 2) run(); });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { input.value = ''; hide(); input.blur(); }
    if (event.key === 'ArrowDown' && panel) {
      event.preventDefault();
      panel.querySelector('.search-hit')?.focus();
    }
  });
  document.addEventListener('mousedown', (event) => {
    if (panel && !panel.contains(event.target) && event.target !== input) hide();
  });
}

export function focusSearch() {
  if (currentInput) { currentInput.focus(); currentInput.select(); }
}

function hide() {
  if (panel) { panel.remove(); panel = null; }
}

function show(input, data) {
  hide();
  panel = el('div', { class: 'search-panel' });
  if (!data.total) {
    panel.appendChild(el('div', { class: 'search-empty' },
      data.message || `「${data.q}」に一致するものはありませんでした`));
  } else {
    for (const group of data.groups) {
      panel.appendChild(el('div', { class: 'search-group' },
        el('span', { text: `${group.icon} ${group.label}` }),
        el('span', { class: 'hint', text: `${group.items.length} 件` })));
      for (const item of group.items) {
        panel.appendChild(hit(group.kind, item, input));
      }
    }
  }
  const box = input.getBoundingClientRect();
  panel.style.top = `${box.bottom + 6}px`;
  panel.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - 440))}px`;
  document.body.appendChild(panel);
}

function hit(kind, item, input) {
  const open = async () => {
    hide();
    input.value = '';
    if (kind === 'task') {
      closeAllOverlays();
      const { openTaskDetail } = await import('./taskDetail.js');
      openTaskDetail(item.id);
    } else if (kind === 'issue') {
      closeAllOverlays();
      const { openIssueDetail } = await import('./issueDetail.js');
      openIssueDetail(item.id);
    } else if (kind === 'comment') {
      closeAllOverlays();
      if (item.task_id) {
        const { openTaskDetail } = await import('./taskDetail.js');
        openTaskDetail(item.task_id);
      } else {
        const { openIssueDetail } = await import('./issueDetail.js');
        openIssueDetail(item.issue_id);
      }
    } else if (kind === 'todo') {
      location.hash = '#/todos';
    } else if (KIND_ROUTE[kind]) {
      location.hash = KIND_ROUTE[kind](item);
    }
  };

  const node = el('button', { class: 'search-hit', type: 'button', onClick: open });
  node.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      nextHit(node, 1)?.focus();
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      const previous = nextHit(node, -1);
      if (previous) previous.focus(); else input.focus();
    }
  });

  if (kind === 'comment') {
    fill(node,
      el('span', { class: 'search-title', text: item.excerpt || item.body }),
      el('span', { class: 'search-sub',
        text: `${item.user_name || '不明'} · ${item.parent_title || ''}`
          + (item.project_name ? ` · ${item.project_name}` : '') }));
    return node;
  }
  if (kind === 'project') {
    fill(node,
      el('span', { class: 'search-title' },
        el('span', { class: 'dot', style: { background: item.color } }),
        el('span', { text: item.name })),
      el('span', { class: 'search-sub', text: item.description || '' }));
    return node;
  }
  if (kind === 'todo') {
    fill(node,
      el('span', { class: 'search-title',
        text: (item.is_done ? '✓ ' : '') + item.title }),
      el('span', { class: 'search-sub',
        text: item.due_date ? `期限 ${formatDate(item.due_date)}` : '' }));
    return node;
  }
  fill(node,
    el('span', { class: 'search-title',
      text: (item.is_milestone ? '◆ ' : '') + (item.seq ? `#${item.seq} ` : '') + item.title }),
    el('span', { class: 'search-sub' },
      el('span', { class: 'dot', style: { background: item.project_color || '#98a2b3' } }),
      el('span', { text: item.project_name || '' }),
      item.assignee_name ? el('span', { text: ` · ${item.assignee_name}` }) : null,
      item.due_date
        ? el('span', {
          class: dueClass(item.due_date, item.status) || '',
          text: ` · ${formatDate(item.due_date)}`,
        })
        : null));
  return node;
}

function nextHit(node, direction) {
  const all = [...panel.querySelectorAll('.search-hit')];
  const index = all.indexOf(node);
  return all[index + direction] || null;
}
