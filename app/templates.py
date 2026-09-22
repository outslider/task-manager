"""タスクの雛形。よくある一式を保存しておき、何度でも起こせるようにする。

雛形の中では日付を「起点から何日目か」で持つ。絶対日付で残すと次に使うときに
必ず直すことになり、雛形の意味が薄れるため。使うときに起点の日を渡すと、
そこから数えた日付が入る。開始日・期限のどちらかが空なら、空のまま残す。

依存関係は、木のなかでの位置（0 から振った通し番号）で持つ。他のプロジェクトへ
差し込んでも、雛形の中で閉じた依存だけがそのまま再現される。
"""
import json

SCOPES = {"project": "プロジェクト一式", "tasks": "タスクのかたまり"}

# 雛形に残すタスクの項目。状態・進捗・担当は毎回変わるので持たない。
FIELDS = ("title", "description", "category", "priority", "estimate_hours",
          "is_milestone", "marker")


def _offset(start, value):
    """起点からの日数。日付が入っていなければ None。"""
    if not start or not value:
        return None
    return (value - start).days


def build(rows, deps, root_ids, keep_people=False):
    """タスクの行から雛形の中身を組み立てる。

    rows は同じプロジェクトのタスク（辞書）、deps は (task_id, depends_on_id)。
    root_ids に挙げたものが雛形の根になる。

    keep_people は複製のときだけ True。雛形は使い回すものなので担当者を持たないが、
    同じプロジェクト内のコピーでは担当がそのままのほうが手戻りが少ない。
    """
    by_id = {r["id"]: r for r in rows}
    children = {}
    for row in rows:
        children.setdefault(row["parent_id"], []).append(row)
    for group in children.values():
        group.sort(key=lambda r: (r["sort_order"], r["id"]))

    # 起点は、雛形に含まれるタスクのいちばん早い開始日（無ければ期限）
    dates = [d for r in rows for d in (r["start_date"], r["due_date"]) if d]
    start = min(dates) if dates else None

    order = []                      # 通し番号 → 元の id

    def node(row):
        order.append(row["id"])
        item = {f: row[f] for f in FIELDS}
        item["estimate_hours"] = (float(row["estimate_hours"])
                                  if row["estimate_hours"] is not None else None)
        item["is_milestone"] = 1 if row["is_milestone"] else 0
        if keep_people:
            item["assignee_id"] = row["assignee_id"]
        # 開始と期限を別々の「何日目」で持つ。片方だけ入っていた場合に、
        # もう片方を勝手に埋めてしまわないようにするため。
        item["day"] = _offset(start, row["start_date"])
        item["due_day"] = _offset(start, row["due_date"])
        item["children"] = [node(c) for c in children.get(row["id"], [])]
        return item

    roots = [node(by_id[i]) for i in root_ids if i in by_id]
    position = {task_id: i for i, task_id in enumerate(order)}
    links = [[position[a], position[b]] for a, b in deps
             if a in position and b in position]
    return {"roots": roots, "deps": links}


def height(body):
    """雛形の深さ。1 段だけなら 1。差し込み先が深すぎないかを見るのに使う。"""
    def walk(items):
        return 1 + max([walk(i.get("children") or []) for i in items], default=-1)
    return max(walk(body.get("roots") or []), 0)


def count(body):
    """雛形に入っているタスクの数。"""
    def walk(items):
        return sum(1 + walk(i.get("children") or []) for i in items)
    return walk(body.get("roots") or [])


def dump(body):
    return json.dumps(body, ensure_ascii=False, default=str)


def load(row):
    body = json.loads(row["body"])
    return body if isinstance(body, dict) else {"roots": [], "deps": []}
