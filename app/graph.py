"""Dependency graph analysis.

Turns the finish-to-start dependencies between tasks into the numbers a
planner actually needs: how much work each task is holding up, which chain
determines the project end date (the critical path), and where the dates
contradict the dependencies.
"""
from datetime import date

OPEN = ("todo", "doing", "review", "blocked")


def _as_date(value):
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def duration_days(task):
    """Working span of a task in days.  Milestones are points in time."""
    if task.get("is_milestone"):
        return 0
    start = _as_date(task.get("start_date"))
    due = _as_date(task.get("due_date"))
    if start and due:
        return max(1, (due - start).days + 1)
    return 1


def analyze(tasks, deps, today=None):
    """Return {task_id: metrics} plus project-level findings.

    metrics per task:
      blocks_direct     直接ブロックしている後続タスク数
      blocks_total      間接も含めてブロックしている後続タスク数
      blocks_open       そのうち未完了のもの
      blocked_by        先行タスク数
      blocked_by_open   未完了の先行タスク数
      is_blocked        未完了の先行タスクを持つ（着手できない）
      slack_days        余裕日数（0 ならクリティカルパス上）
      is_critical       クリティカルパス上にある
      downstream_ids    影響が波及するタスク id
    """
    today = today or date.today()
    by_id = {t["id"]: t for t in tasks}
    successors = {t["id"]: [] for t in tasks}
    predecessors = {t["id"]: [] for t in tasks}
    edges = []
    for dep in deps:
        task_id, pred_id = dep["task_id"], dep["depends_on_id"]
        if task_id not in by_id or pred_id not in by_id:
            continue
        successors[pred_id].append(task_id)
        predecessors[task_id].append(pred_id)
        edges.append((pred_id, task_id))

    order = _topological_order(by_id, successors, predecessors)
    downstream = _downstream_sets(order, successors)
    early, late, project_end = _critical_path(order, by_id, successors, predecessors)

    metrics = {}
    for task_id, task in by_id.items():
        reach = downstream[task_id]
        preds = predecessors[task_id]
        open_preds = [p for p in preds if by_id[p]["status"] in OPEN]
        slack = late[task_id][0] - early[task_id][0]
        metrics[task_id] = {
            "blocks_direct": len(successors[task_id]),
            "blocks_total": len(reach),
            "blocks_open": sum(1 for i in reach if by_id[i]["status"] in OPEN),
            "blocked_by": len(preds),
            "blocked_by_open": len(open_preds),
            "is_blocked": bool(open_preds) and task["status"] in OPEN,
            "slack_days": slack,
            "is_critical": slack == 0 and bool(successors[task_id] or preds),
            "downstream_ids": sorted(reach),
        }
    return {
        "metrics": metrics,
        "conflicts": _conflicts(edges, by_id),
        "critical_path": _critical_chain(order, by_id, metrics, successors, predecessors),
        "project_span_days": project_end,
    }


def _topological_order(by_id, successors, predecessors):
    """Kahn's algorithm.  Any nodes left in a cycle are appended at the end."""
    indegree = {tid: len(predecessors[tid]) for tid in by_id}
    queue = [tid for tid, n in indegree.items() if n == 0]
    order = []
    while queue:
        node = queue.pop(0)
        order.append(node)
        for succ in successors[node]:
            indegree[succ] -= 1
            if indegree[succ] == 0:
                queue.append(succ)
    if len(order) < len(by_id):
        order += [tid for tid in by_id if tid not in set(order)]
    return order


def _downstream_sets(order, successors):
    """Transitive successors of every node, computed in reverse topological order."""
    reach = {}
    for node in reversed(order):
        acc = set()
        for succ in successors[node]:
            acc.add(succ)
            acc |= reach.get(succ, set())
        acc.discard(node)
        reach[node] = acc
    return reach


def _critical_path(order, by_id, successors, predecessors):
    """Standard CPM forward/backward pass over task durations."""
    early = {}   # id -> (earliest start, earliest finish)
    for node in order:
        start = max((early[p][1] for p in predecessors[node] if p in early), default=0)
        early[node] = (start, start + duration_days(by_id[node]))
    project_end = max((f for _, f in early.values()), default=0)

    late = {}    # id -> (latest start, latest finish)
    for node in reversed(order):
        finish = min((late[s][0] for s in successors[node] if s in late), default=project_end)
        late[node] = (finish - duration_days(by_id[node]), finish)
    return early, late, project_end


def _critical_chain(order, by_id, metrics, successors, predecessors):
    """The longest dependency chain, as an ordered list of task ids."""
    chain = [tid for tid in order
             if metrics[tid]["is_critical"] and (successors[tid] or predecessors[tid])]
    return chain


def _conflicts(edges, by_id):
    """Dependencies whose dates contradict the ordering."""
    out = []
    for pred_id, succ_id in edges:
        pred, succ = by_id[pred_id], by_id[succ_id]
        pred_due = _as_date(pred.get("due_date"))
        succ_start = _as_date(succ.get("start_date")) or _as_date(succ.get("due_date"))
        if pred_due and succ_start and pred_due > succ_start:
            out.append({
                "task_id": succ_id, "task_title": succ["title"],
                "depends_on_id": pred_id, "depends_on_title": pred["title"],
                "overlap_days": (pred_due - succ_start).days,
                "kind": "date_order",
            })
    return out


def bottlenecks(tasks, deps, today=None, limit=20):
    """Rank the incomplete tasks that are holding up the most work."""
    today = today or date.today()
    result = analyze(tasks, deps, today)
    metrics = result["metrics"]
    by_id = {t["id"]: t for t in tasks}
    ranked = []
    for task_id, task in by_id.items():
        if task["status"] == "done":
            continue
        m = metrics[task_id]
        due = _as_date(task.get("due_date"))
        overdue_days = (today - due).days if due and due < today else 0
        reasons = []
        if m["blocks_open"]:
            reasons.append("後続 {} 件が待機".format(m["blocks_open"]))
        if m["is_critical"]:
            reasons.append("クリティカルパス上")
        if overdue_days:
            reasons.append("期限 {} 日超過".format(overdue_days))
        if m["is_blocked"]:
            reasons.append("先行 {} 件が未完了".format(m["blocked_by_open"]))
        if task["status"] == "blocked":
            reasons.append("ブロック中として登録")
        if not reasons:
            continue
        score = (m["blocks_open"] * 10
                 + (15 if m["is_critical"] else 0)
                 + min(overdue_days, 30)
                 + (8 if task["status"] == "blocked" else 0)
                 + (3 if m["is_blocked"] else 0))
        ranked.append({
            "id": task_id,
            "title": task["title"],
            "status": task["status"],
            "category": task.get("category", ""),
            "priority": task.get("priority", 1),
            "progress": task.get("progress", 0),
            "due_date": task.get("due_date"),
            "assignee_id": task.get("assignee_id"),
            "assignee_name": task.get("assignee_name"),
            "score": score,
            "reasons": reasons,
            "overdue_days": overdue_days,
            **{k: m[k] for k in ("blocks_direct", "blocks_total", "blocks_open",
                                 "blocked_by", "blocked_by_open", "is_blocked",
                                 "is_critical", "slack_days")},
        })
    ranked.sort(key=lambda r: (-r["score"], r["due_date"] is None,
                               str(r["due_date"]), r["id"]))
    return {
        "bottlenecks": ranked[:limit],
        "conflicts": result["conflicts"],
        "critical_path": result["critical_path"],
        "metrics": metrics,
    }
