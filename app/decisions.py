"""意思決定ログ。プロジェクトで「何を・なぜ・誰が決めたか」と、却下した案・前提条件・変更の履歴を残す。

- 承認の手順は持たない。記録した人が状態を「決定」にすれば決定
- 決定のあとに中身を変えるときは、変えた理由が要る。変えるたびに版（snapshot）を残す
- 新しい決定が前の決定を置き換えたら、前の決定は「置き換え済み」になる（理由付きの版が残る）
- 社外ユーザーには、「社外にも見せる」を付けた決定だけ（検討中のものは出さない）。版の履歴は出さない
"""
import json

from . import db

STATUS_LABEL = {
    "draft": "検討中",
    "decided": "決定",
    "review": "見直し中",
    "superseded": "置き換え済み",
    "withdrawn": "取り消し",
}
STATUSES = tuple(STATUS_LABEL)
# 決めたあと（＝中身を変えるなら理由が要る）の状態
SETTLED = ("decided", "review", "superseded", "withdrawn")

FIELD_LABEL = {
    "title": "件名", "status": "状態", "what": "決定内容", "why": "理由", "decided_on": "決めた日",
    "category": "分類", "guest_visible": "社外への公開", "supersedes_id": "置き換えた決定",
    "people": "決めた人", "options": "検討した案", "premises": "前提条件", "links": "関連",
}


def load(decision_id):
    """1 件を、決めた人・案・前提・関連まで含めて読む。無ければ None。"""
    row = db.query_one("SELECT * FROM decisions WHERE id=%s", (decision_id,))
    if not row:
        return None
    row["people"] = db.query(
        "SELECT u.id, u.name, u.avatar_color FROM decision_people dp JOIN users u ON u.id = dp.user_id "
        "WHERE dp.decision_id=%s ORDER BY u.name", (decision_id,))
    row["options"] = db.query(
        "SELECT title, detail, adopted, reason FROM decision_options WHERE decision_id=%s "
        "ORDER BY sort_order, id", (decision_id,))
    for option in row["options"]:
        option["adopted"] = bool(option["adopted"])
    row["premises"] = db.query(
        "SELECT text, review_on, broken FROM decision_premises WHERE decision_id=%s "
        "ORDER BY sort_order, id", (decision_id,))
    for premise in row["premises"]:
        premise["broken"] = bool(premise["broken"])
        premise["review_on"] = premise["review_on"].isoformat() if premise["review_on"] else None
    links = db.query("SELECT kind, target_id FROM decision_links WHERE decision_id=%s "
                     "ORDER BY kind, target_id", (decision_id,))
    row["links"] = {"tasks": [l["target_id"] for l in links if l["kind"] == "task"],
                    "issues": [l["target_id"] for l in links if l["kind"] == "issue"]}
    row["guest_visible"] = bool(row["guest_visible"])
    return row


def snapshot(row):
    """版として残す中身。比べやすいよう、並びと型をそろえる。"""
    return {
        "title": row["title"], "status": row["status"], "what": row["what"] or "",
        "why": row["why"] or "",
        "decided_on": row["decided_on"].isoformat() if hasattr(row["decided_on"], "isoformat")
        else row["decided_on"],
        "category": row["category"] or "", "guest_visible": bool(row["guest_visible"]),
        "supersedes_id": row["supersedes_id"],
        "people": [{"id": p["id"], "name": p["name"]} for p in row["people"]],
        "options": [{k: o[k] for k in ("title", "detail", "adopted", "reason")} for o in row["options"]],
        "premises": [{k: p[k] for k in ("text", "review_on", "broken")} for p in row["premises"]],
        "links": row["links"],
    }


def changed_fields(before, after):
    """変わった項目の名前（画面に出す言葉）。"""
    labels = []
    for key, label in FIELD_LABEL.items():
        a, b = before.get(key), after.get(key)
        if key == "people":
            a, b = sorted(p["id"] for p in a or []), sorted(p["id"] for p in b or [])
        if a != b:
            labels.append(label)
    return labels


def record_version(decision_id, version, snap, changes, reason, user_id):
    db.insert(
        "INSERT INTO decision_versions(decision_id, version, snapshot, changes, reason, changed_by, "
        "created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)",
        (decision_id, version, json.dumps(snap, ensure_ascii=False, default=str),
         "・".join(changes)[:500], reason or "", user_id, db.now()))


def chain_contains(start_id, target_id):
    """start から「置き換えた決定」をたどって target に行き着くか（輪にしないため）。"""
    seen = set()
    current = start_id
    while current and current not in seen:
        if current == target_id:
            return True
        seen.add(current)
        current = db.scalar("SELECT supersedes_id AS s FROM decisions WHERE id=%s", (current,))
    return False
