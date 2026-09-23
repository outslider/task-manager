/* 見出しによる区切り。タスク一覧とガントで同じものを使う。
 *
 * 見出しには段（1=大・2=中・3=小）があり、区切りの範囲は
 * 「次の、同じ段かそれより大きい見出しまで」。大見出しの中に中・小見出しが入る。
 * 見出しはタスクの親子関係とは別物で、同じ親を持つ兄弟の並びの中にだけ効く。
 * そのため、ここでは兄弟の並び 1 本だけを受け取って区切る。
 */

/**
 * 兄弟の並びを見出しで区切り、画面に出す順に並べ直す。
 *
 * @param {Array} list 同じ親を持つタスク（並び順どおり）
 * @param {Set} collapsed たたんでいる見出しの id。たたんだ見出しの範囲は出さない
 * @param {Set|null} keep 絞り込み中に残すタスクの id。渡すと、絞り込みで中身が
 *   1 件も残らなくなった見出しは出さない（空の区切りだけが並ぶのを避けるため）。
 *   もともと中身の無い見出しは出す
 * @param {Set|null} pinned 中身が空でも出す見出しの id（ガントで定例会議を置いたもの）
 * @param {Set|null} filled 絞り込む前に中身があった見出しの id。list がすでに絞り込み
 *   済みのとき（ガント）に渡す。渡さなければ list から数える
 * @returns {Array<{task, heading?: true, level?: number, count?: number, outline: number}>}
 *   outline はその行を囲んでいる見出しの数（字下げに使う）
 */
export function sectionize(list, collapsed, keep = null, pinned = null, filled = null) {
  // 1) 見出しの入れ子を組み立てる
  const root = { children: [] };
  const stack = [{ level: 0, node: root }];
  for (const task of list) {
    if (task.is_heading) {
      const level = headingLevel(task);
      while (stack.length > 1 && stack[stack.length - 1].level >= level) stack.pop();
      const node = { heading: task, level, children: [] };
      stack[stack.length - 1].node.children.push(node);
      stack.push({ level, node });
    } else {
      stack[stack.length - 1].node.children.push({ task });
    }
  }

  // 2) 中身の数え上げ（見出しの件数と、空の区切りを隠す判断に使う）
  const passes = (task) => !keep || keep.has(task.id);
  const count = (node, all = false) => node.children.reduce(
    (sum, child) => sum + (child.heading ? count(child, all)
      : ((all || passes(child.task)) ? 1 : 0)), 0);
  const pinnedInside = (node) => Boolean(pinned?.has(node.heading.id))
    || node.children.some((child) => child.heading && pinnedInside(child));

  // 3) 画面に出す順に平らにする
  const out = [];
  const emit = (node, outline) => {
    for (const child of node.children) {
      if (!child.heading) {
        if (passes(child.task)) out.push({ task: child.task, outline });
        continue;
      }
      const total = count(child);
      // 隠すのは「中身はあるのに、絞り込みで全部消えた」区切りだけ。
      // 足したばかりで中身がまだ無い見出しまで隠すと、足したのに出てこなくなる
      const hadItems = filled ? filled.has(child.heading.id) : count(child, true) > 0;
      if (keep && !total && hadItems && !pinnedInside(child)) continue;
      out.push({ task: child.heading, heading: true, level: child.level, count: total, outline });
      if (!collapsed.has(child.heading.id)) emit(child, outline + 1);
    }
  };
  emit(root, 0);
  return out;
}

export function headingLevel(task) {
  const level = Number(task.heading_level) || 1;
  return Math.min(3, Math.max(1, level));
}

export const HEADING_LEVEL_LABEL = { 1: '大見出し', 2: '中見出し', 3: '小見出し' };
