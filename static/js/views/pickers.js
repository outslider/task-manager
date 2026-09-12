/* Reusable form controls: user / category selects and chip-style multi pickers. */
import { store, CATEGORIES, ISSUE_CATEGORIES, category, issueCategory } from '../store.js';
import { el } from '../util.js';

export function option(value, label, selected) {
  return el('option', { value, selected: selected ? true : null }, label);
}

export function userSelect(value, { includeEmpty = true, emptyLabel = '未割当', id } = {}) {
  return el('select', { class: 'select', id },
    includeEmpty ? option('', emptyLabel, !value) : null,
    ...store.users.map((u) => option(u.id, u.name, Number(value) === u.id)));
}

export function categorySelect(value, { id } = {}) {
  return el('select', { class: 'select', id },
    option('', '未分類', !value),
    ...CATEGORIES.map((c) => option(c.value, `${c.icon} ${c.label}`, value === c.value)));
}

export function issueCategorySelect(value, { id } = {}) {
  return el('select', { class: 'select', id },
    ...ISSUE_CATEGORIES.map((c) => option(c.value, c.label, (value || 'other') === c.value)));
}

function chip(label, color, small) {
  return el('span', {
    class: 'cat-chip',
    style: {
      background: `${color}1f`, color, borderColor: `${color}55`,
      fontSize: small ? '10.5px' : '11.5px',
    },
    title: label,
  }, label);
}

export function categoryChip(value, { small = false } = {}) {
  const info = category(value);
  return chip(`${info.icon} ${info.label}`, info.color, small);
}

export function issueCategoryChip(value, { small = false } = {}) {
  const info = issueCategory(value);
  return chip(info.label, info.color, small);
}

/**
 * Multi-select rendered as removable chips plus a dropdown.
 * @returns {{node: HTMLElement, ids: () => number[]}}
 */
export function chipPicker(candidates, selectedIds = [], {
  placeholder = '選択…', emptyText = '設定なし', exhausted = '追加できる項目がありません',
  labelOf = (item) => item.title,
} = {}) {
  const chosen = new Set(selectedIds.map(Number));
  const chips = el('div', { class: 'chip-row' });
  const picker = el('select', { class: 'select' });

  const refresh = () => {
    const remaining = candidates.filter((item) => !chosen.has(item.id));
    picker.replaceChildren(
      el('option', { value: '' }, remaining.length ? placeholder : exhausted),
      ...remaining.map((item) => option(item.id, labelOf(item))));
    picker.disabled = remaining.length === 0;
    chips.replaceChildren(...(chosen.size
      ? [...chosen].map((id) => {
        const item = candidates.find((c) => c.id === id);
        return el('span', { class: 'chip' },
          el('span', { text: item ? labelOf(item) : `#${id}` }),
          el('button', {
            type: 'button', class: 'chip-x', title: '外す',
            onClick: () => { chosen.delete(id); refresh(); },
          }, '×'));
      })
      : [el('span', { class: 'hint', text: emptyText })]));
  };

  picker.addEventListener('change', () => {
    if (!picker.value) return;
    chosen.add(Number(picker.value));
    refresh();
  });
  refresh();
  return { node: el('div', {}, chips, picker), ids: () => [...chosen] };
}
