"""ゴミ箱。消したものを 30 日だけ取っておき、元に戻せるようにする。

やり方は「消す直前の中身を写し取ってから、今までどおり本当に消す」。
各テーブルに削除済み印を付けて全画面の問い合わせに「除く」条件を足して回る、
という作りにはしていない。足し忘れが一箇所でもあると、消したはずのものが
どこかの画面に出てしまうため。写して消す方式なら、その事故が起きない。

戻すときは元の ID のまま入れ直す。AUTO_INCREMENT は減らないので番号が
他に取られている心配はなく、依存線や課題との紐づけもそのまま復元できる。
添付ファイルの実体は、ゴミ箱から消える日まで置いておく。
"""
import json
import logging
import os
from datetime import timedelta

from . import db
from .config import UPLOAD_DIR

log = logging.getLogger("tm.trash")

# 何日置いておくか。過ぎたものは日次バッチで本当に消す。
KEEP_DAYS = 30

LABELS = {"task": "タスク", "issue": "課題", "ticket": "チケット"}

# 入れ直すときに、参照先がもう無かった場合の扱い。
#   "null" … その列だけ空にして入れる（担当者が退職した、など）
#   "drop" … その行はあきらめる（相手がいないと意味をなさない行）
LINKS = {
    "tasks": {"parent_id": ("tasks", "null"), "assignee_id": ("users", "null"),
              "created_by": ("users", "null")},
    "issues": {"owner_id": ("users", "null"), "raised_by": ("users", "null")},
    "tickets": {"queue_id": ("ticket_queues", "drop"),
                "category_id": ("ticket_categories", "null"),
                "requester_id": ("users", "null"), "assignee_id": ("users", "null")},
    "task_deps": {"task_id": ("tasks", "drop"), "depends_on_id": ("tasks", "drop")},
    "comments": {"task_id": ("tasks", "drop"), "issue_id": ("issues", "drop"),
                 "ticket_id": ("tickets", "drop"), "user_id": ("users", "null")},
    "attachments": {"task_id": ("tasks", "drop"), "issue_id": ("issues", "drop"),
                    "ticket_id": ("tickets", "drop"), "uploaded_by": ("users", "null")},
    "issue_tasks": {"issue_id": ("issues", "drop"), "task_id": ("tasks", "drop")},
    "ticket_tasks": {"ticket_id": ("tickets", "drop"), "task_id": ("tasks", "drop")},
    "ticket_issues": {"ticket_id": ("tickets", "drop"), "issue_id": ("issues", "drop")},
}


def _rows(table, where, params):
    return [dict(r) for r in db.query(
        "SELECT * FROM {} WHERE {}".format(table, where), params)]


# --------------------------------------------------------------------------
# 写し取る
# --------------------------------------------------------------------------

def snapshot_task(task_id, ids):
    """タスクと、その子孫すべて。ids は親が先に並んでいること。"""
    group = tuple(ids)
    # tasks は親から先に入れないと parent_id が刺さらないので、ids の並びを保つ
    by_id = {r["id"]: r for r in _rows("tasks", "id IN %s", (group,))}
    return [
        ("tasks", [by_id[i] for i in ids if i in by_id]),
        ("comments", _rows("comments", "task_id IN %s", (group,))),
        ("attachments", _rows("attachments", "task_id IN %s", (group,))),
        ("task_deps", _rows("task_deps", "task_id IN %s OR depends_on_id IN %s",
                            (group, group))),
        ("issue_tasks", _rows("issue_tasks", "task_id IN %s", (group,))),
        ("ticket_tasks", _rows("ticket_tasks", "task_id IN %s", (group,))),
    ]


def snapshot_issue(issue_id):
    return [
        ("issues", _rows("issues", "id=%s", (issue_id,))),
        ("comments", _rows("comments", "issue_id=%s", (issue_id,))),
        ("attachments", _rows("attachments", "issue_id=%s", (issue_id,))),
        ("issue_tasks", _rows("issue_tasks", "issue_id=%s", (issue_id,))),
        ("ticket_issues", _rows("ticket_issues", "issue_id=%s", (issue_id,))),
    ]


def snapshot_ticket(ticket_id):
    return [
        ("tickets", _rows("tickets", "id=%s", (ticket_id,))),
        ("comments", _rows("comments", "ticket_id=%s", (ticket_id,))),
        ("attachments", _rows("attachments", "ticket_id=%s", (ticket_id,))),
        ("ticket_tasks", _rows("ticket_tasks", "ticket_id=%s", (ticket_id,))),
        ("ticket_issues", _rows("ticket_issues", "ticket_id=%s", (ticket_id,))),
    ]


def describe(payload):
    """一覧に出す「子タスク3件・コメント5件」の部分。"""
    counts = {name: len(rows) for name, rows in payload}
    head = next(iter(payload))[0]
    parts = []
    if head == "tasks" and counts.get("tasks", 0) > 1:
        parts.append("子タスク {} 件".format(counts["tasks"] - 1))
    for name, label in (("comments", "コメント"), ("attachments", "添付"),
                        ("task_deps", "依存関係")):
        if counts.get(name):
            parts.append("{} {} 件".format(label, counts[name]))
    return "・".join(parts)


def keep(kind, item_id, payload, title, project_id, user_id):
    """消す直前の中身をゴミ箱へ写す。ゴミ箱側の id を返す。"""
    now = db.now()
    return db.insert(
        "INSERT INTO trash(kind, item_id, project_id, title, summary, payload, "
        "deleted_by, deleted_at, purge_after) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (kind, item_id, project_id, title[:300], describe(payload)[:300],
         json.dumps(payload, ensure_ascii=False, default=str),
         user_id, now, (now + timedelta(days=KEEP_DAYS)).date()))


# --------------------------------------------------------------------------
# 戻す
# --------------------------------------------------------------------------

def _exists(table, value):
    return bool(db.scalar(
        "SELECT 1 AS x FROM {} WHERE id=%s".format(table), (value,), default=0))


def _fix(table, row, coming_back):
    """参照先を確かめる。行ごと諦めるときは None を返す。

    coming_back は、この復元で一緒に入れ直す予定の {表: {id,...}}。
    まだ DB に無くても「これから入る」ものは在るものとして扱う。
    """
    out = dict(row)
    for column, (target, how) in LINKS.get(table, {}).items():
        value = out.get(column)
        if value is None:
            continue
        if value in coming_back.get(target, ()) or _exists(target, value):
            continue
        if how == "drop":
            return None
        out[column] = None
    return out


def restore(entry):
    """ゴミ箱の 1 件を元に戻す。戻せた行数と、諦めた行数を返す。"""
    payload = json.loads(entry["payload"])
    # 一緒に入れ直すものは「在る」ものとして参照を通す
    coming_back = {}
    for table, rows in payload:
        coming_back.setdefault(table, set()).update(
            r["id"] for r in rows if "id" in r)

    restored, skipped = 0, 0
    with db.transaction():
        for table, rows in payload:
            for row in rows:
                fixed = _fix(table, row, coming_back)
                if fixed is None:
                    skipped += 1
                    continue
                columns = list(fixed.keys())
                # 何かの拍子に同じ行が既にあっても倒れないよう IGNORE。
                # 入らなかったぶんは数えない（rowcount が 0 で返る）。
                written = db.execute(
                    "INSERT IGNORE INTO {}({}) VALUES({})".format(
                        table, ",".join(columns), ",".join(["%s"] * len(columns))),
                    tuple(fixed[c] for c in columns))
                if written:
                    restored += 1
                else:
                    skipped += 1
        db.execute("DELETE FROM trash WHERE id=%s", (entry["id"],))
    return restored, skipped


def blocked_reason(entry):
    """戻せない事情があれば、その説明。戻せるなら None。"""
    payload = json.loads(entry["payload"])
    head_table, head_rows = payload[0]
    if not head_rows:
        return "中身が残っていません"
    row = head_rows[0]
    if head_table in ("tasks", "issues"):
        if not _exists("projects", row["project_id"]):
            return "プロジェクトごと無くなっているため戻せません"
    if head_table == "tickets" and not _exists("ticket_queues", row["queue_id"]):
        return "受付窓口が無くなっているため戻せません"
    if _exists(head_table, row["id"]):
        return "同じ番号のものが既にあります"
    return None


# --------------------------------------------------------------------------
# 本当に消す
# --------------------------------------------------------------------------

def stored_files(entry):
    """この 1 件が抱えている、サーバー上のファイル名。"""
    payload = json.loads(entry["payload"])
    names = []
    for table, rows in payload:
        if table != "attachments":
            continue
        names.extend(r.get("stored_name") for r in rows
                     if r.get("kind") == "file" and r.get("stored_name"))
    return names


def remove_stored_file(stored_name):
    """アップロードされたファイルの実体を消す。無ければ何もしない。"""
    if not stored_name:
        return
    path = os.path.join(UPLOAD_DIR, os.path.basename(stored_name))
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def purge(entry):
    for name in stored_files(entry):
        remove_stored_file(name)
    db.execute("DELETE FROM trash WHERE id=%s", (entry["id"],))


def purge_expired(today=None):
    """置いておく日数を過ぎたものを本当に消す。戻り値は件数。"""
    today = today or db.today()
    gone = 0
    for entry in db.query("SELECT * FROM trash WHERE purge_after < %s", (today,)):
        try:
            purge(entry)
            gone += 1
        except Exception as error:  # 1件の失敗で全体を止めない
            log.warning("trash %s purge failed: %s", entry["id"], error)
    return gone
