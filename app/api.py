"""REST API: routing table plus handlers."""
import mimetypes
import os
import re
import secrets
from datetime import date, timedelta
from urllib.parse import quote

import pymysql

from . import (auth, db, graph, holidays, llm, mentions, nlp, notify, prefs,
               recurrence, slack, taxonomy, tickets, workload)
from .config import MAX_UPLOAD_BYTES, UPLOAD_DIR
from .http_util import (HttpError, as_bool, as_date, as_datetime, as_int, bad_request,
                        forbidden,
                        json_response, not_found, require, unauthorized, Response)

# 状態のキーは判定に使うので固定。表示名と色は画面から変えられる（taxonomy）。
STATUSES = taxonomy.STATUS_KEYS
# 重要度。緊急度は期限から自動的に決まるので、ここは純粋な重要度だけを持つ。
IMPORTANCE_LABEL = {0: "低", 1: "中", 2: "高", 3: "最重要"}
OPEN_STATUSES = notify.OPEN_STATUSES
MAX_TASK_DEPTH = 8

# ガント上の記号。空文字は既定（◆）。
MARKERS = [
    ("", "◆ ひし形（既定）"),
    ("circle", "● 丸"),
    ("square", "■ 四角"),
    ("triangle", "▲ 三角"),
    ("down", "▼ 逆三角"),
    ("star", "★ 星"),
]
MARKER_VALUES = {m[0] for m in MARKERS}


def normalize_marker(value):
    text = str(value or "").strip()
    return text if text in MARKER_VALUES else ""


# ---- 課題管理表 -------------------------------------------------------
ISSUE_STATUSES = ["open", "doing", "pending", "resolved", "closed"]
ISSUE_STATUS_LABEL = {
    "open": "未対応", "doing": "対応中", "pending": "保留",
    "resolved": "解決済", "closed": "クローズ",
}
OPEN_ISSUE_STATUSES = ("open", "doing", "pending")
SEVERITY_LABEL = {0: "低", 1: "中", 2: "高", 3: "重大"}

# value, 表示名, 色
ISSUE_CATEGORIES = [
    ("spec", "仕様・要件", "#6366f1"),
    ("tech", "技術・実装", "#3b6ef5"),
    ("schedule", "スケジュール", "#e8912b"),
    ("resource", "体制・リソース", "#14b8a6"),
    ("cost", "コスト・予算", "#8b5cf6"),
    ("quality", "品質・不具合", "#ef4444"),
    ("external", "外部・他部門", "#0ea5e9"),
    ("other", "その他", "#a1a1aa"),
]
ISSUE_CATEGORY_VALUES = {c[0] for c in ISSUE_CATEGORIES}
ISSUE_CATEGORY_LABEL = {c[0]: c[1] for c in ISSUE_CATEGORIES}



def status_label(key):
    return taxonomy.status_label(key)


def category_label(value):
    return taxonomy.category_label(value)

_ROUTES = []


def route(method, pattern):
    regex = re.compile("^" + pattern + "$")

    def decorator(fn):
        _ROUTES.append((method.upper(), regex, fn))
        return fn

    return decorator


def dispatch(ctx):
    """ctx carries method, path, query, body, user.  Returns a Response."""
    allowed = set()
    for method, regex, fn in _ROUTES:
        match = regex.match(ctx.path)
        if not match:
            continue
        if method != ctx.method:
            allowed.add(method)
            continue
        return fn(ctx, *[int(g) if g.isdigit() else g for g in match.groups()])
    if allowed:
        raise HttpError(405, "許可されていないメソッドです")
    raise not_found("API エンドポイントが存在しません")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def me(ctx):
    if not ctx.user:
        raise unauthorized()
    return ctx.user


def admin_only(ctx):
    user = me(ctx)
    if not auth.is_admin(user):
        raise forbidden("管理者のみ実行できます")
    return user


def public_user(row):
    if not row:
        return None
    return {
        "id": row["id"], "name": row["name"], "email": row["email"],
        "role": row["role"], "is_active": bool(row["is_active"]),
        "email_notify": bool(row.get("email_notify", 1)),
        "avatar_color": row.get("avatar_color", "#4f8cff"),
        "ui_theme": row.get("ui_theme", "auto"),
        "ui_accent": row.get("ui_accent", ""),
        "nav_order": [k for k in (row.get("nav_order") or "").split(",") if k],
    }


def project_or_404(user, project_id, minimum="viewer"):
    project = db.query_one("SELECT * FROM projects WHERE id=%s", (project_id,))
    if not project:
        raise not_found("プロジェクトが見つかりません")
    role = auth.project_role(user, project_id)
    if role is None:
        raise forbidden("このプロジェクトへのアクセス権がありません")
    if auth.ROLE_ORDER[role] < auth.ROLE_ORDER[minimum]:
        raise forbidden("この操作には {} 以上の権限が必要です".format(minimum))
    project["my_role"] = role
    return project


def task_or_404(user, task_id, minimum="viewer"):
    task = db.query_one("SELECT * FROM tasks WHERE id=%s", (task_id,))
    if not task:
        raise not_found("タスクが見つかりません")
    project_or_404(user, task["project_id"], minimum)
    return task


def descendant_ids(task_id):
    """All descendants of a task (breadth first, no recursion limits)."""
    out, frontier = [], [task_id]
    while frontier:
        rows = db.query(
            "SELECT id FROM tasks WHERE parent_id IN %s", (tuple(frontier),)
        )
        frontier = [r["id"] for r in rows]
        out.extend(frontier)
    return out


def subtree_height(task_id):
    """task_id を根とする部分木の高さ（本人だけなら 0）。"""
    height, frontier = 0, [task_id]
    while frontier and height < MAX_TASK_DEPTH + 2:
        rows = db.query("SELECT id FROM tasks WHERE parent_id IN %s", (tuple(frontier),))
        frontier = [r["id"] for r in rows]
        if frontier:
            height += 1
    return height


def task_depth(parent_id):
    depth = 0
    while parent_id is not None and depth < MAX_TASK_DEPTH + 2:
        parent_id = db.scalar("SELECT parent_id FROM tasks WHERE id=%s", (parent_id,))
        depth += 1
    return depth


def system_comment(task_id, user_id, text):
    db.insert(
        "INSERT INTO comments(task_id, user_id, body, kind, created_at) "
        "VALUES(%s,%s,%s,'system',%s)",
        (task_id, user_id, text, db.now()),
    )


def touch_task(task_id):
    db.execute("UPDATE tasks SET updated_at=%s WHERE id=%s", (db.now(), task_id))


def task_rows_with_rollup(rows):
    """Attach child_count and rolled-up progress/dates to a flat task list."""
    by_id = {r["id"]: r for r in rows}
    children = {}
    for r in rows:
        r["child_count"] = 0
        children.setdefault(r["parent_id"], []).append(r["id"])
    for parent_id, kids in children.items():
        if parent_id in by_id:
            by_id[parent_id]["child_count"] = len(kids)

    def resolve(task_id):
        node = by_id[task_id]
        if "rollup_progress" in node:
            return node
        kids = [by_id[k] for k in children.get(task_id, []) if k in by_id]
        if not kids:
            node["rollup_progress"] = node["progress"]
            node["rollup_start"] = node["start_date"]
            node["rollup_due"] = node["due_date"]
            node["leaf_total"] = 1
            node["leaf_done"] = 1 if node["status"] == "done" else 0
            return node
        total = done = 0
        progress_sum = 0
        starts, dues = [], []
        for kid in kids:
            resolve(kid["id"])
            total += kid["leaf_total"]
            done += kid["leaf_done"]
            progress_sum += kid["rollup_progress"] * kid["leaf_total"]
            if kid["rollup_start"]:
                starts.append(kid["rollup_start"])
            if kid["rollup_due"]:
                dues.append(kid["rollup_due"])
        if node["start_date"]:
            starts.append(node["start_date"])
        if node["due_date"]:
            dues.append(node["due_date"])
        node["leaf_total"] = total
        node["leaf_done"] = done
        node["rollup_progress"] = int(round(progress_sum / total)) if total else node["progress"]
        node["rollup_start"] = min(starts) if starts else None
        node["rollup_due"] = max(dues) if dues else None
        return node

    for r in rows:
        resolve(r["id"])
    return rows


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------

@route("POST", r"/api/auth/login")
def login(ctx):
    email = require(ctx.body, "email", "メールアドレス")
    password = require(ctx.body, "password", "パスワード")
    user = auth.authenticate(email, password)
    if not user:
        raise HttpError(401, "メールアドレスまたはパスワードが違います")
    token = auth.create_session(user["id"])
    resp = json_response({"user": public_user(user)})
    resp.add_cookie(auth.SESSION_COOKIE, token,
                    max_age=auth.SESSION_DAYS * 86400, secure=ctx.secure_cookie)
    return resp


@route("POST", r"/api/auth/logout")
def logout(ctx):
    if ctx.session_token:
        auth.destroy_session(ctx.session_token)
    resp = json_response({"ok": True})
    resp.add_cookie(auth.SESSION_COOKIE, "", max_age=0, secure=ctx.secure_cookie)
    return resp


@route("GET", r"/api/auth/me")
def whoami(ctx):
    ui = {
        "accent_default": db.get_setting("ui_accent_default", "#3b6ef5"),
        "app_name": db.get_setting("app_name", "タスク管理"),
    }
    if not ctx.user:
        return json_response({"user": None, "ui": ui})
    return json_response({
        "user": public_user(ctx.user),
        "unread": notify.unread_count(ctx.user["id"]),
        "ui": ui,
    })


@route("POST", r"/api/auth/password")
def change_password(ctx):
    user = me(ctx)
    current = require(ctx.body, "current_password", "現在のパスワード")
    new = require(ctx.body, "new_password", "新しいパスワード")
    if len(new) < 8:
        raise bad_request("パスワードは 8 文字以上にしてください")
    stored = db.scalar("SELECT password_hash FROM users WHERE id=%s", (user["id"],))
    if not auth.verify_password(current, stored):
        raise bad_request("現在のパスワードが違います")
    db.execute("UPDATE users SET password_hash=%s WHERE id=%s",
               (auth.hash_password(new), user["id"]))
    db.execute("DELETE FROM sessions WHERE user_id=%s AND token<>%s",
               (user["id"], ctx.session_token or ""))
    return json_response({"ok": True})


@route("PATCH", r"/api/auth/profile")
def update_profile(ctx):
    user = me(ctx)
    fields, params = [], []
    if "name" in ctx.body:
        fields.append("name=%s")
        params.append(require(ctx.body, "name", "氏名"))
    if "email_notify" in ctx.body:
        fields.append("email_notify=%s")
        params.append(1 if as_bool(ctx.body["email_notify"]) else 0)
    if "avatar_color" in ctx.body:
        fields.append("avatar_color=%s")
        params.append(str(ctx.body["avatar_color"])[:20])
    if ctx.body.get("ui_theme") in ("auto", "light", "dark"):
        fields.append("ui_theme=%s")
        params.append(ctx.body["ui_theme"])
    if "ui_accent" in ctx.body:
        fields.append("ui_accent=%s")
        params.append(normalize_color(ctx.body["ui_accent"]))
    if "nav_order" in ctx.body:
        # 並びは本人の好み。中身の妥当性は画面側が持つので、形だけ整えて預かる。
        wanted = ctx.body["nav_order"] or []
        if not isinstance(wanted, list):
            raise bad_request("並び順の形式が正しくありません")
        keys, seen = [], set()
        for item in wanted:
            key = re.sub(r"[^a-z0-9_-]", "", str(item).lower())[:20]
            if key and key not in seen:
                seen.add(key)
                keys.append(key)
        fields.append("nav_order=%s")
        params.append(",".join(keys)[:300])
    if fields:
        params.append(user["id"])
        db.execute("UPDATE users SET {} WHERE id=%s".format(", ".join(fields)), params)
    row = db.query_one("SELECT * FROM users WHERE id=%s", (user["id"],))
    return json_response({"user": public_user(row)})


# --------------------------------------------------------------------------
# users
# --------------------------------------------------------------------------

@route("GET", r"/api/users")
def list_users(ctx):
    me(ctx)
    include_inactive = as_bool(ctx.query.get("include_inactive"))
    sql = "SELECT * FROM users"
    if not include_inactive:
        sql += " WHERE is_active=1"
    sql += " ORDER BY name"
    return json_response({"users": [public_user(r) for r in db.query(sql)]})


@route("POST", r"/api/users")
def create_user(ctx):
    admin_only(ctx)
    email = require(ctx.body, "email", "メールアドレス")
    name = require(ctx.body, "name", "氏名")
    password = ctx.body.get("password") or secrets.token_urlsafe(9)
    role = ctx.body.get("role") if ctx.body.get("role") in ("admin", "member") else "member"
    if len(password) < 8:
        raise bad_request("パスワードは 8 文字以上にしてください")
    try:
        user_id = db.insert(
            "INSERT INTO users(email, name, password_hash, role, avatar_color, created_at) "
            "VALUES(%s,%s,%s,%s,%s,%s)",
            (email, name, auth.hash_password(password), role,
             ctx.body.get("avatar_color", "#4f8cff"), db.now()),
        )
    except pymysql.err.IntegrityError:
        raise bad_request("このメールアドレスは既に登録されています")
    row = db.query_one("SELECT * FROM users WHERE id=%s", (user_id,))
    return json_response({"user": public_user(row), "initial_password": password}, 201)


@route("PATCH", r"/api/users/(\d+)")
def update_user(ctx, user_id):
    actor = me(ctx)
    if not auth.is_admin(actor) and actor["id"] != user_id:
        raise forbidden()
    target = db.query_one("SELECT * FROM users WHERE id=%s", (user_id,))
    if not target:
        raise not_found("ユーザーが見つかりません")
    fields, params = [], []
    if "name" in ctx.body:
        fields.append("name=%s")
        params.append(require(ctx.body, "name", "氏名"))
    if "avatar_color" in ctx.body:
        fields.append("avatar_color=%s")
        params.append(str(ctx.body["avatar_color"])[:20])
    if "email_notify" in ctx.body:
        fields.append("email_notify=%s")
        params.append(1 if as_bool(ctx.body["email_notify"]) else 0)
    if auth.is_admin(actor):
        if "email" in ctx.body:
            fields.append("email=%s")
            params.append(require(ctx.body, "email", "メールアドレス"))
        if ctx.body.get("role") in ("admin", "member"):
            if target["role"] == "admin" and ctx.body["role"] != "admin" and _last_admin(user_id):
                raise bad_request("管理者が 0 人になる操作はできません")
            fields.append("role=%s")
            params.append(ctx.body["role"])
        if "is_active" in ctx.body:
            active = as_bool(ctx.body["is_active"])
            if not active and target["role"] == "admin" and _last_admin(user_id):
                raise bad_request("管理者が 0 人になる操作はできません")
            fields.append("is_active=%s")
            params.append(1 if active else 0)
            if not active:
                db.execute("DELETE FROM sessions WHERE user_id=%s", (user_id,))
    if not fields:
        raise bad_request("更新する項目がありません")
    params.append(user_id)
    try:
        db.execute("UPDATE users SET {} WHERE id=%s".format(", ".join(fields)), params)
    except pymysql.err.IntegrityError:
        raise bad_request("このメールアドレスは既に使用されています")
    return json_response({"user": public_user(db.query_one(
        "SELECT * FROM users WHERE id=%s", (user_id,)))})


def _last_admin(user_id):
    others = db.scalar(
        "SELECT COUNT(*) AS c FROM users WHERE role='admin' AND is_active=1 AND id<>%s",
        (user_id,), default=0)
    return others == 0


@route("POST", r"/api/users/(\d+)/password")
def reset_password(ctx, user_id):
    admin_only(ctx)
    password = ctx.body.get("password") or secrets.token_urlsafe(9)
    if len(password) < 8:
        raise bad_request("パスワードは 8 文字以上にしてください")
    if db.execute("UPDATE users SET password_hash=%s WHERE id=%s",
                  (auth.hash_password(password), user_id)) == 0:
        raise not_found("ユーザーが見つかりません")
    db.execute("DELETE FROM sessions WHERE user_id=%s", (user_id,))
    return json_response({"ok": True, "password": password})


@route("DELETE", r"/api/users/(\d+)")
def delete_user(ctx, user_id):
    actor = admin_only(ctx)
    if user_id == actor["id"]:
        raise bad_request("自分自身は削除できません")
    if _last_admin(user_id):
        target = db.query_one("SELECT role FROM users WHERE id=%s", (user_id,))
        if target and target["role"] == "admin":
            raise bad_request("管理者が 0 人になる操作はできません")
    # project_members.principal_id はユーザーとグループの兼用で外部キーを張れないため、
    # ここで明示的に後始末する（グループ削除と同じ扱い）
    db.execute("DELETE FROM project_members WHERE principal_type='user' AND principal_id=%s",
               (user_id,))
    db.execute("DELETE FROM users WHERE id=%s", (user_id,))
    return json_response({"ok": True})


# --------------------------------------------------------------------------
# groups
# --------------------------------------------------------------------------

@route("GET", r"/api/groups")
def list_groups(ctx):
    me(ctx)
    groups = db.query("SELECT * FROM user_groups ORDER BY name")
    members = db.query(
        "SELECT gm.group_id, u.id, u.name, u.email, u.avatar_color "
        "FROM group_members gm JOIN users u ON u.id = gm.user_id ORDER BY u.name")
    by_group = {}
    for m in members:
        by_group.setdefault(m["group_id"], []).append(
            {"id": m["id"], "name": m["name"], "email": m["email"],
             "avatar_color": m["avatar_color"]})
    for g in groups:
        g["members"] = by_group.get(g["id"], [])
    return json_response({"groups": groups})


@route("POST", r"/api/groups")
def create_group(ctx):
    admin_only(ctx)
    name = require(ctx.body, "name", "グループ名")
    try:
        group_id = db.insert(
            "INSERT INTO user_groups(name, description, created_at) VALUES(%s,%s,%s)",
            (name, ctx.body.get("description", "")[:500], db.now()))
    except pymysql.err.IntegrityError:
        raise bad_request("同名のグループが既に存在します")
    _set_group_members(group_id, ctx.body.get("user_ids"))
    return json_response({"group": db.query_one(
        "SELECT * FROM user_groups WHERE id=%s", (group_id,))}, 201)


@route("PATCH", r"/api/groups/(\d+)")
def update_group(ctx, group_id):
    admin_only(ctx)
    if not db.query_one("SELECT 1 FROM user_groups WHERE id=%s", (group_id,)):
        raise not_found("グループが見つかりません")
    if "name" in ctx.body or "description" in ctx.body:
        fields, params = [], []
        if "name" in ctx.body:
            fields.append("name=%s")
            params.append(require(ctx.body, "name", "グループ名"))
        if "description" in ctx.body:
            fields.append("description=%s")
            params.append(str(ctx.body["description"])[:500])
        params.append(group_id)
        try:
            db.execute("UPDATE user_groups SET {} WHERE id=%s".format(", ".join(fields)), params)
        except pymysql.err.IntegrityError:
            raise bad_request("同名のグループが既に存在します")
    if "user_ids" in ctx.body:
        _set_group_members(group_id, ctx.body["user_ids"])
    return json_response({"ok": True})


def _set_group_members(group_id, user_ids):
    if user_ids is None:
        return
    if not isinstance(user_ids, list):
        raise bad_request("user_ids は配列で指定してください")
    with db.transaction():
        db.execute("DELETE FROM group_members WHERE group_id=%s", (group_id,))
        db.executemany(
            "INSERT IGNORE INTO group_members(group_id, user_id) VALUES(%s,%s)",
            [(group_id, int(u)) for u in user_ids])


@route("DELETE", r"/api/groups/(\d+)")
def delete_group(ctx, group_id):
    admin_only(ctx)
    db.execute("DELETE FROM project_members WHERE principal_type='group' AND principal_id=%s",
               (group_id,))
    db.execute("DELETE FROM user_groups WHERE id=%s", (group_id,))
    return json_response({"ok": True})


# --------------------------------------------------------------------------
# projects
# --------------------------------------------------------------------------

EMPTY_STATS = {"total": 0, "done": 0, "overdue": 0, "milestones": 0, "blocked": 0,
               "open_issues": 0, "open_tickets": 0}


def project_stats(project_ids):
    if not project_ids:
        return {}
    rows = db.query(
        """
        SELECT project_id,
               COUNT(*) AS total,
               SUM(status='done') AS done,
               SUM(status<>'done' AND due_date IS NOT NULL AND due_date < %s) AS overdue,
               SUM(is_milestone=1) AS milestones,
               SUM(status <> 'done' AND EXISTS (
                   SELECT 1 FROM task_deps d JOIN tasks pt ON pt.id = d.depends_on_id
                    WHERE d.task_id = tasks.id AND pt.status <> 'done')) AS blocked
          FROM tasks WHERE project_id IN %s GROUP BY project_id
        """,
        (db.today(), tuple(project_ids)),
    )
    stats = {r["project_id"]: {k: int(r[k] or 0) for k in
                               ("total", "done", "overdue", "milestones", "blocked")}
             for r in rows}
    for r in db.query(
            "SELECT project_id, COUNT(*) AS c FROM issues "
            "WHERE project_id IN %s AND status IN %s GROUP BY project_id",
            (tuple(project_ids), OPEN_ISSUE_STATUSES)):
        stats.setdefault(r["project_id"], dict(EMPTY_STATS))["open_issues"] = int(r["c"])
    # そのプロジェクト専用の窓口に来ている未完了チケット
    for r in db.query(
            "SELECT q.project_id, COUNT(*) AS c FROM tickets t "
            "JOIN ticket_queues q ON q.id = t.queue_id "
            "WHERE q.project_id IN %s AND t.status IN %s GROUP BY q.project_id",
            (tuple(project_ids), tickets.OPEN_STATUSES)):
        stats.setdefault(r["project_id"], dict(EMPTY_STATS))["open_tickets"] = int(r["c"])
    for row in stats.values():
        row.setdefault("open_issues", 0)
        row.setdefault("open_tickets", 0)
    return stats


def project_member_users(project_id):
    """グループ経由を含めて、このプロジェクトを見られる人の一覧（担当者候補）。"""
    return project_member_users_map([project_id]).get(project_id, [])


def project_member_users_map(project_ids):
    """複数プロジェクトぶんをまとめて引く。一覧画面で 1 件ずつ引かないため。"""
    ids = [i for i in project_ids if i]
    if not ids:
        return {}
    scope = tuple(ids)
    rows = db.query(
        """
        SELECT p.id AS project_id, u.id, u.name, u.email, u.avatar_color
          FROM projects p
          JOIN users u ON u.is_active = 1
           AND (u.id = p.owner_id
                OR u.role = 'admin'
                OR EXISTS (SELECT 1 FROM project_members pm
                            WHERE pm.project_id = p.id AND pm.principal_type = 'user'
                              AND pm.principal_id = u.id)
                OR EXISTS (SELECT 1 FROM project_members pm
                            JOIN group_members gm ON gm.group_id = pm.principal_id
                           WHERE pm.project_id = p.id AND pm.principal_type = 'group'
                             AND gm.user_id = u.id))
         WHERE p.id IN %s
         ORDER BY p.id, u.name
        """, (scope,))
    out = {}
    for row in rows:
        out.setdefault(row.pop("project_id"), []).append(row)
    return out


def assignable_users(project_id):
    """担当者に指定できる人。メンバーに加えて、現にそのプロジェクトで担当になっている人も含める。

    メンバー登録が追いついていないだけで、実際には担当している、という状態が
    起きうる。取り込みのたびに「メンバーに見つかりません」で止まると使えないため。
    """
    people = project_member_users(project_id)
    known = {p["id"] for p in people}
    extra = db.query(
        "SELECT DISTINCT u.id, u.name, u.email, u.avatar_color "
        "FROM tasks t JOIN users u ON u.id = t.assignee_id "
        "WHERE t.project_id=%s AND u.is_active=1", (project_id,))
    people += [row for row in extra if row["id"] not in known]
    people.sort(key=lambda row: row["name"])
    return people


def project_member_rows(project_id):
    users = db.query(
        "SELECT pm.role, u.id, u.name, u.email, u.avatar_color FROM project_members pm "
        "JOIN users u ON u.id = pm.principal_id "
        "WHERE pm.project_id=%s AND pm.principal_type='user' ORDER BY u.name",
        (project_id,))
    groups = db.query(
        "SELECT pm.role, g.id, g.name FROM project_members pm "
        "JOIN user_groups g ON g.id = pm.principal_id "
        "WHERE pm.project_id=%s AND pm.principal_type='group' ORDER BY g.name",
        (project_id,))
    return (
        [dict(m, principal_type="user") for m in users]
        + [dict(g, principal_type="group") for g in groups]
    )


@route("GET", r"/api/projects")
def list_projects(ctx):
    user = me(ctx)
    ids = auth.visible_project_ids(user)
    if not ids:
        return json_response({"projects": []})
    include_archived = as_bool(ctx.query.get("include_archived"))
    sql = ("SELECT p.*, u.name AS owner_name FROM projects p "
           "LEFT JOIN users u ON u.id = p.owner_id WHERE p.id IN %s")
    params = [tuple(ids)]
    if not include_archived:
        sql += " AND p.archived=0"
    sql += " ORDER BY p.archived, p.name"
    projects = db.query(sql, params)
    ids = [p["id"] for p in projects]
    stats = project_stats(ids)
    members = project_member_users_map(ids)
    roles = auth.project_roles(user, ids)
    for p in projects:
        p["stats"] = stats.get(p["id"], EMPTY_STATS)
        p["my_role"] = roles.get(p["id"])
        p["members"] = members.get(p["id"], [])
    return json_response({"projects": projects})


@route("POST", r"/api/projects")
def create_project(ctx):
    user = me(ctx)
    name = require(ctx.body, "name", "プロジェクト名")
    project_id = db.insert(
        "INSERT INTO projects(name, description, color, owner_id, created_at) "
        "VALUES(%s,%s,%s,%s,%s)",
        (name, ctx.body.get("description", ""), ctx.body.get("color", "#4f8cff"),
         user["id"], db.now()))
    db.execute(
        "INSERT INTO project_members(project_id, principal_type, principal_id, role) "
        "VALUES(%s,'user',%s,'owner')", (project_id, user["id"]))
    project = db.query_one("SELECT * FROM projects WHERE id=%s", (project_id,))
    project["my_role"] = "owner"
    project["stats"] = dict(EMPTY_STATS)
    return json_response({"project": project}, 201)


@route("GET", r"/api/projects/(\d+)")
def get_project(ctx, project_id):
    user = me(ctx)
    project = project_or_404(user, project_id)
    project["members"] = project_member_rows(project_id)
    project["stats"] = project_stats([project_id]).get(project_id, EMPTY_STATS)
    return json_response({"project": project,
                          "member_users": project_member_users(project_id)})


@route("PATCH", r"/api/projects/(\d+)")
def update_project(ctx, project_id):
    user = me(ctx)
    project_or_404(user, project_id, "owner")
    fields, params = [], []
    if "slack_webhook_url" in ctx.body:
        url = str(ctx.body["slack_webhook_url"] or "").strip()
        if url and not url.startswith("https://hooks.slack.com/"):
            raise bad_request("Slack の Webhook URL は https://hooks.slack.com/ で始まります")
        fields.append("slack_webhook_url=%s")
        params.append(url[:300])
    for key, column in (("name", "name"), ("description", "description"),
                        ("color", "color")):
        if key in ctx.body:
            fields.append(column + "=%s")
            params.append(ctx.body[key])
    if "archived" in ctx.body:
        fields.append("archived=%s")
        params.append(1 if as_bool(ctx.body["archived"]) else 0)
    if "notify_enabled" in ctx.body:
        fields.append("notify_enabled=%s")
        params.append(1 if as_bool(ctx.body["notify_enabled"]) else 0)
    if "slack_events" in ctx.body:
        fields.append("slack_events=%s")
        params.append(prefs.format_events(ctx.body["slack_events"], prefs.SLACK_EVENT_KEYS))
    if "owner_id" in ctx.body:
        fields.append("owner_id=%s")
        params.append(as_int(ctx.body["owner_id"]))
    if not fields:
        raise bad_request("更新する項目がありません")
    params.append(project_id)
    db.execute("UPDATE projects SET {} WHERE id=%s".format(", ".join(fields)), params)
    return json_response({"project": db.query_one(
        "SELECT * FROM projects WHERE id=%s", (project_id,))})


@route("DELETE", r"/api/projects/(\d+)")
def delete_project(ctx, project_id):
    user = me(ctx)
    project_or_404(user, project_id, "owner")
    stored = db.query(
        "SELECT a.stored_name FROM attachments a JOIN tasks t ON t.id = a.task_id "
        "WHERE t.project_id=%s AND a.kind='file'", (project_id,))
    db.execute("DELETE FROM projects WHERE id=%s", (project_id,))
    for row in stored:
        _remove_stored_file(row["stored_name"])
    return json_response({"ok": True})


@route("PUT", r"/api/projects/(\d+)/members")
def set_project_members(ctx, project_id):
    user = me(ctx)
    project_or_404(user, project_id, "owner")
    members = ctx.body.get("members")
    if not isinstance(members, list):
        raise bad_request("members は配列で指定してください")
    rows = []
    for m in members:
        ptype = m.get("principal_type")
        pid = as_int(m.get("principal_id"))
        role = m.get("role", "editor")
        if ptype not in ("user", "group") or pid is None:
            raise bad_request("メンバー指定が不正です")
        if role not in auth.PROJECT_ROLES:
            raise bad_request("不正な権限です: {}".format(role))
        rows.append((project_id, ptype, pid, role))
    owner_id = db.scalar("SELECT owner_id FROM projects WHERE id=%s", (project_id,))
    if owner_id and not any(r[1] == "user" and r[2] == owner_id for r in rows):
        rows.append((project_id, "user", owner_id, "owner"))
    with db.transaction():
        db.execute("DELETE FROM project_members WHERE project_id=%s", (project_id,))
        db.executemany(
            "INSERT INTO project_members(project_id, principal_type, principal_id, role) "
            "VALUES(%s,%s,%s,%s)", rows)
    return json_response({"members": project_member_rows(project_id)})


# --------------------------------------------------------------------------
# tasks
# --------------------------------------------------------------------------

TASK_SELECT = """
    SELECT t.*, u.name AS assignee_name, u.avatar_color AS assignee_color,
           p.name AS project_name, p.color AS project_color,
           (SELECT COUNT(*) FROM comments c WHERE c.task_id = t.id AND c.kind='comment')
               AS comment_count,
           (SELECT COUNT(*) FROM attachments a WHERE a.task_id = t.id) AS attachment_count,
           (SELECT COUNT(*) FROM task_deps d WHERE d.depends_on_id = t.id) AS blocks_direct,
           (SELECT COUNT(*) FROM task_deps d JOIN tasks pt ON pt.id = d.depends_on_id
             WHERE d.task_id = t.id AND pt.status <> 'done') AS blocked_by_open
      FROM tasks t
      LEFT JOIN users u ON u.id = t.assignee_id
      JOIN projects p ON p.id = t.project_id
"""


@route("GET", r"/api/projects/(\d+)/tasks")
def list_project_tasks(ctx, project_id):
    user = me(ctx)
    project = project_or_404(user, project_id)
    rows = db.query(TASK_SELECT + " WHERE t.project_id=%s ORDER BY t.sort_order, t.id",
                    (project_id,))
    task_rows_with_rollup(rows)
    deps = project_deps(project_id)
    analysis = graph.analyze(rows, deps)
    for row in rows:
        row.update(slim_metrics(analysis["metrics"].get(row["id"], {})))
    return json_response({
        "tasks": rows, "deps": deps, "project": project,
        "members": project_member_users(project_id),
        "conflicts": analysis["conflicts"],
        "critical_path": analysis["critical_path"],
    })


# 俯瞰ガントは件数が多くなるので、図を描くのに要る列だけにする
GANTT_SELECT = """
    SELECT t.id, t.project_id, t.parent_id, t.title, t.status, t.priority, t.category,
           t.assignee_id, t.start_date, t.due_date, t.progress, t.estimate_hours,
           t.is_milestone, t.marker, t.sort_order,
           u.name AS assignee_name, u.avatar_color AS assignee_color,
           p.name AS project_name, p.color AS project_color
      FROM tasks t
      LEFT JOIN users u ON u.id = t.assignee_id
      JOIN projects p ON p.id = t.project_id
"""


@route("GET", r"/api/gantt")
def gantt_overview(ctx):
    """参加しているプロジェクトをまとめて 1 枚のガントにするためのデータ。"""
    user = me(ctx)
    ids = auth.visible_project_ids(user)
    wanted = ctx.query.get("project_ids")
    if wanted:
        chosen = {as_int(v) for v in str(wanted).split(",") if as_int(v)}
        ids = [i for i in ids if i in chosen]
    if not ids:
        return json_response({"tasks": [], "deps": [], "projects": [], "conflicts": [],
                              "critical_path": [], "members": []})

    scope = tuple(ids)
    rows = db.query(
        GANTT_SELECT + " WHERE t.project_id IN %s AND p.archived=0 "
        "ORDER BY p.name, t.sort_order, t.id", (scope,))
    task_rows_with_rollup(rows)
    deps = db.query(
        "SELECT d.task_id, d.depends_on_id FROM task_deps d "
        "JOIN tasks t ON t.id = d.task_id WHERE t.project_id IN %s", (scope,))
    # 依存はプロジェクトの中で閉じているので、まとめて解析しても混ざらない
    analysis = graph.analyze(rows, deps)
    for row in rows:
        row.update(slim_metrics(analysis["metrics"].get(row["id"], {})))
    projects = db.query(
        "SELECT id, name, color FROM projects WHERE id IN %s AND archived=0 ORDER BY name",
        (scope,))
    for project in projects:
        project["my_role"] = auth.project_role(user, project["id"])
    return json_response({
        "tasks": rows, "deps": deps, "projects": projects,
        "conflicts": analysis["conflicts"], "critical_path": analysis["critical_path"],
    })


def slim_metrics(metrics):
    """一覧やガントに載せる指標。影響範囲の id 一覧は重いので外す
    （必要になるのはタスク詳細だけで、そこでは改めて計算している）。"""
    return {k: v for k, v in metrics.items() if k != "downstream_ids"}


def project_deps(project_id):
    return db.query(
        "SELECT d.task_id, d.depends_on_id FROM task_deps d "
        "JOIN tasks t ON t.id = d.task_id WHERE t.project_id=%s", (project_id,))


@route("GET", r"/api/projects/(\d+)/bottlenecks")
def project_bottlenecks(ctx, project_id):
    """Which unfinished tasks are holding up the most work, and why."""
    user = me(ctx)
    project_or_404(user, project_id)
    rows = db.query(
        "SELECT t.*, u.name AS assignee_name FROM tasks t "
        "LEFT JOIN users u ON u.id = t.assignee_id WHERE t.project_id=%s", (project_id,))
    result = graph.bottlenecks(rows, project_deps(project_id),
                               limit=as_int(ctx.query.get("limit"), 20, 1, 100))
    titles = {r["id"]: r["title"] for r in rows}
    return json_response({
        "bottlenecks": result["bottlenecks"],
        "conflicts": result["conflicts"],
        "critical_path": [{"id": i, "title": titles.get(i, "")}
                          for i in result["critical_path"]],
    })


@route("GET", r"/api/tasks")
def search_tasks(ctx):
    user = me(ctx)
    ids = auth.visible_project_ids(user)
    if not ids:
        return json_response({"tasks": []})
    where = ["t.project_id IN %s"]
    params = [tuple(ids)]
    q = (ctx.query.get("q") or "").strip()
    if q:
        where.append("(t.title LIKE %s OR t.description LIKE %s)")
        params += ["%{}%".format(q), "%{}%".format(q)]
    if ctx.query.get("scope") == "mine":
        where.append("t.assignee_id=%s")
        params.append(user["id"])
    assignee = as_int(ctx.query.get("assignee_id"))
    if assignee:
        where.append("t.assignee_id=%s")
        params.append(assignee)
    project_id = as_int(ctx.query.get("project_id"))
    if project_id:
        where.append("t.project_id=%s")
        params.append(project_id)
    category = ctx.query.get("category")
    if category:
        where.append("t.category=%s")
        params.append(normalize_category(category))
    if as_bool(ctx.query.get("blocked")):
        where.append("t.status <> 'done' AND EXISTS (SELECT 1 FROM task_deps d "
                     "JOIN tasks pt ON pt.id = d.depends_on_id "
                     "WHERE d.task_id = t.id AND pt.status <> 'done')")
    status = ctx.query.get("status")
    if status == "open":
        where.append("t.status IN %s")
        params.append(OPEN_STATUSES)
    elif status in STATUSES:
        where.append("t.status=%s")
        params.append(status)
    if as_bool(ctx.query.get("overdue")):
        where.append("t.status<>'done' AND t.due_date IS NOT NULL AND t.due_date < %s")
        params.append(db.today())
    if as_bool(ctx.query.get("milestone")):
        where.append("t.is_milestone=1")
    if not as_bool(ctx.query.get("include_archived")):
        where.append("p.archived=0")
    limit = as_int(ctx.query.get("limit"), 300, 1, 2000)
    sql = (TASK_SELECT + " WHERE " + " AND ".join(where)
           + " ORDER BY (t.due_date IS NULL), t.due_date, t.priority DESC, t.id LIMIT %s")
    params.append(limit)
    return json_response({"tasks": db.query(sql, params)})


_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def normalize_color(value, default=""):
    """Accept a #rgb / #rrggbb colour, or an empty string meaning 'use the default'."""
    value = str(value or "").strip()
    if value == "":
        return default
    if not _HEX_COLOR_RE.match(value):
        raise bad_request("色は #RRGGBB 形式で指定してください")
    return value.lower()


def as_hours(value, maximum=9999):
    """工数（時間）。空なら None。"""
    if value in (None, "", "null"):
        return None
    try:
        hours = round(float(value), 1)
    except (TypeError, ValueError):
        raise bad_request("工数は数値で入力してください")
    if hours < 0:
        raise bad_request("工数は0以上で入力してください")
    return min(hours, maximum)


def _hours_label(value):
    return "未設定" if value in (None, "") else "{:g}h".format(float(value))


def normalize_category(value, current=""):
    if value is None:
        return current
    value = str(value).strip()
    if value == "":
        return ""
    if value not in taxonomy.category_values():
        raise bad_request("不明なカテゴリです: {}".format(value))
    return value


def set_task_deps(task_id, project_id, ids):
    """Replace the predecessor list of a task.  Rejects cross-project links and cycles."""
    if not isinstance(ids, list):
        raise bad_request("depends_on は配列で指定してください")
    wanted = []
    for raw in ids:
        dep_id = as_int(raw)
        if dep_id is None or dep_id == task_id or dep_id in wanted:
            continue
        other = db.query_one("SELECT project_id FROM tasks WHERE id=%s", (dep_id,))
        if not other or other["project_id"] != project_id:
            raise bad_request("先行タスクは同じプロジェクトから選んでください")
        wanted.append(dep_id)
    with db.transaction():
        db.execute("DELETE FROM task_deps WHERE task_id=%s", (task_id,))
        for dep_id in wanted:
            if _creates_dep_cycle(task_id, dep_id):
                raise bad_request("依存関係が循環します")
            db.execute("INSERT IGNORE INTO task_deps(task_id, depends_on_id) VALUES(%s,%s)",
                       (task_id, dep_id))
    return wanted


def _apply_status_progress(body, current):
    """Keep status and progress consistent when only one of them is sent."""
    status = body.get("status", current["status"] if current else "todo")
    if status not in STATUSES:
        raise bad_request("不正なステータスです: {}".format(status))
    progress = body.get("progress")
    progress = as_int(progress, current["progress"] if current else 0, 0, 100)
    if "status" in body and "progress" not in body:
        if status == "done":
            progress = 100
        elif current and current["status"] == "done" and progress == 100:
            progress = 90
    if "progress" in body and "status" not in body:
        if progress >= 100:
            status = "done"
        elif status == "done":
            status = "doing"
        elif status == "todo" and progress > 0:
            status = "doing"
    return status, progress


@route("POST", r"/api/tasks")
def create_task(ctx):
    return json_response({"task": _create_task(me(ctx), ctx.body)}, 201)


def _create_task(user, body):
    """タスクを 1 件作って、作った行を返す。

    チケットからの起票でも同じ検証を通したいので、
    HTTP の文脈から切り離してある。
    """
    ctx = _Body(body)
    project_id = as_int(ctx.body.get("project_id"))
    if project_id is None:
        raise bad_request("project_id は必須です")
    project_or_404(user, project_id, "editor")
    title = require(ctx.body, "title", "タスク名")
    parent_id = as_int(ctx.body.get("parent_id"))
    if parent_id is not None:
        parent = db.query_one("SELECT project_id FROM tasks WHERE id=%s", (parent_id,))
        if not parent or parent["project_id"] != project_id:
            raise bad_request("親タスクが同じプロジェクトにありません")
        if task_depth(parent_id) >= MAX_TASK_DEPTH:
            raise bad_request("階層が深すぎます（最大 {} 階層）".format(MAX_TASK_DEPTH))
    status, progress = _apply_status_progress(ctx.body, None)
    start_date = as_date(ctx.body.get("start_date"))
    due_date = as_date(ctx.body.get("due_date"))
    if start_date and due_date and start_date > due_date:
        raise bad_request("開始日は期限より前にしてください")
    is_milestone = 1 if as_bool(ctx.body.get("is_milestone")) else 0
    if is_milestone and not due_date:
        due_date = start_date
    sort_order = as_int(ctx.body.get("sort_order"))
    if sort_order is None:
        sort_order = (db.scalar(
            "SELECT COALESCE(MAX(sort_order), 0) AS m FROM tasks WHERE project_id=%s",
            (project_id,), default=0) or 0) + 10
    assignee_id = as_int(ctx.body.get("assignee_id"))
    ensure_member(assignee_id, project_id)
    now = db.now()
    task_id = db.insert(
        "INSERT INTO tasks(project_id, parent_id, title, description, category, status, "
        "priority, assignee_id, start_date, due_date, progress, estimate_hours, is_milestone, "
        "marker, sort_order, created_by, created_at, updated_at, completed_at) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (project_id, parent_id, title, ctx.body.get("description", ""),
         normalize_category(ctx.body.get("category")), status,
         as_int(ctx.body.get("priority"), 1, 0, 3), assignee_id, start_date, due_date,
         progress, as_hours(ctx.body.get("estimate_hours")), is_milestone,
         normalize_marker(ctx.body.get("marker")), sort_order,
         user["id"], now, now, now if status == "done" else None))
    if "depends_on" in ctx.body:
        set_task_deps(task_id, project_id, ctx.body["depends_on"])
    if assignee_id and assignee_id != user["id"]:
        _notify_assignment(task_id, title, assignee_id, user)
    return db.query_one(TASK_SELECT + " WHERE t.id=%s", (task_id,))


class _Body:
    """_create_task が ctx.body と書けるようにするだけの入れ物。"""

    def __init__(self, body):
        self.body = body or {}


def _notify_assignment(task_id, title, assignee_id, actor):
    row = db.query_one(
        "SELECT p.id AS project_id, p.name AS project_name FROM tasks t "
        "JOIN projects p ON p.id=t.project_id WHERE t.id=%s", (task_id,)) or {}
    notify.create(
        assignee_id, "assigned", "タスクが割り当てられました: {}".format(title),
        "プロジェクト: {}\n担当者に設定: {}\n{}".format(
            row.get("project_name", ""), actor["name"], notify.task_url(task_id)),
        task_id=task_id, project_id=row.get("project_id"))


@route("GET", r"/api/tasks/(\d+)")
def get_task(ctx, task_id):
    user = me(ctx)
    task_or_404(user, task_id)
    task = db.query_one(TASK_SELECT + " WHERE t.id=%s", (task_id,))
    path, parent_id = [], task["parent_id"]
    while parent_id:
        parent = db.query_one("SELECT id, title, parent_id FROM tasks WHERE id=%s", (parent_id,))
        if not parent:
            break
        path.insert(0, {"id": parent["id"], "title": parent["title"]})
        parent_id = parent["parent_id"]
    children = db.query(
        TASK_SELECT + " WHERE t.parent_id=%s ORDER BY t.sort_order, t.id", (task_id,))
    comments = db.query(
        "SELECT c.*, u.name AS user_name, u.avatar_color FROM comments c "
        "LEFT JOIN users u ON u.id = c.user_id WHERE c.task_id=%s ORDER BY c.created_at, c.id",
        (task_id,))
    attachments = db.query(
        "SELECT a.*, u.name AS uploaded_by_name FROM attachments a "
        "LEFT JOIN users u ON u.id = a.uploaded_by WHERE a.task_id=%s ORDER BY a.created_at",
        (task_id,))
    deps = db.query(
        "SELECT d.depends_on_id AS id, t.title, t.status, t.due_date FROM task_deps d "
        "JOIN tasks t ON t.id = d.depends_on_id WHERE d.task_id=%s", (task_id,))
    blocking = db.query(
        "SELECT d.task_id AS id, t.title, t.status FROM task_deps d "
        "JOIN tasks t ON t.id = d.task_id WHERE d.depends_on_id=%s", (task_id,))
    siblings = db.query(
        "SELECT id, title, status, start_date, due_date, is_milestone, priority, category "
        "FROM tasks WHERE project_id=%s", (task["project_id"],))
    analysis = graph.analyze(siblings, project_deps(task["project_id"]))
    metrics = analysis["metrics"].get(task_id, {})
    by_id = {t["id"]: t for t in siblings}
    impact = [{"id": i, "title": by_id[i]["title"], "status": by_id[i]["status"],
               "due_date": by_id[i]["due_date"]}
              for i in metrics.get("downstream_ids", []) if i in by_id]
    conflicts = [c for c in analysis["conflicts"]
                 if c["task_id"] == task_id or c["depends_on_id"] == task_id]
    issues = db.query(
        "SELECT i.id, i.seq, i.title, i.status, i.severity, i.due_date FROM issues i "
        "JOIN issue_tasks it ON it.issue_id = i.id WHERE it.task_id=%s ORDER BY i.seq",
        (task_id,))
    linked_tickets = db.query(
        "SELECT t.id, t.title, t.status, t.kind, t.on_behalf_of, "
        "       q.name AS queue_name, q.icon AS queue_icon "
        "  FROM tickets t JOIN ticket_tasks tt ON tt.ticket_id = t.id "
        "  JOIN ticket_queues q ON q.id = t.queue_id "
        " WHERE tt.task_id=%s ORDER BY t.id", (task_id,))
    return json_response({
        "task": task, "path": path, "children": children, "comments": comments,
        "attachments": attachments, "deps": deps, "blocking": blocking, "issues": issues,
        "tickets": linked_tickets,
        "metrics": metrics, "impact": impact, "conflicts": conflicts,
        "members": project_member_users(task["project_id"]),
        "my_role": auth.project_role(user, task["project_id"]),
    })


@route("PATCH", r"/api/tasks/(\d+)")
def update_task(ctx, task_id):
    user = me(ctx)
    current = task_or_404(user, task_id, "editor")
    body = ctx.body
    fields, params, notes = [], [], []

    if "title" in body:
        title = require(body, "title", "タスク名")
        if title != current["title"]:
            fields.append("title=%s")
            params.append(title)
    if "description" in body:
        fields.append("description=%s")
        params.append(body["description"] or "")
    if "category" in body:
        category = normalize_category(body["category"], current["category"])
        if category != current["category"]:
            notes.append("カテゴリ: {} → {}".format(
                category_label(current["category"]), category_label(category)))
        fields.append("category=%s")
        params.append(category)
    if "priority" in body:
        importance = as_int(body["priority"], 1, 0, 3)
        if importance != current["priority"]:
            notes.append("重要度: {} → {}".format(
                IMPORTANCE_LABEL.get(current["priority"], "-"),
                IMPORTANCE_LABEL.get(importance, "-")))
        fields.append("priority=%s")
        params.append(importance)
    if "is_milestone" in body:
        fields.append("is_milestone=%s")
        params.append(1 if as_bool(body["is_milestone"]) else 0)
    for key, label in (("estimate_hours", "見積工数"), ("actual_hours", "実績工数")):
        if key in body:
            hours = as_hours(body[key])
            current_hours = current[key]
            if (hours is None) != (current_hours is None) or (
                    hours is not None and float(hours) != float(current_hours)):
                notes.append("{}: {} → {}".format(
                    label, _hours_label(current_hours), _hours_label(hours)))
            fields.append(key + "=%s")
            params.append(hours if key == "estimate_hours" else (hours or 0))

    if "status" in body or "progress" in body:
        status, progress = _apply_status_progress(body, current)
        if status != current["status"]:
            notes.append("状態: {} → {}".format(
                status_label(current["status"]), status_label(status)))
            fields.append("completed_at=%s")
            params.append(db.now() if status == "done" else None)
        if progress != current["progress"]:
            notes.append("進捗: {}% → {}%".format(current["progress"], progress))
        fields += ["status=%s", "progress=%s"]
        params += [status, progress]

    new_assignee = current["assignee_id"]
    if "assignee_id" in body:
        new_assignee = as_int(body["assignee_id"])
        if new_assignee != current["assignee_id"]:
            ensure_member(new_assignee, current["project_id"])
            old_name = db.scalar("SELECT name AS n FROM users WHERE id=%s",
                                 (current["assignee_id"],), default="未割当") or "未割当"
            new_name = db.scalar("SELECT name AS n FROM users WHERE id=%s",
                                 (new_assignee,), default="未割当") or "未割当"
            notes.append("担当: {} → {}".format(old_name, new_name))
        fields.append("assignee_id=%s")
        params.append(new_assignee)

    for key in ("start_date", "due_date"):
        if key in body:
            value = as_date(body[key])
            old = current[key].isoformat() if current[key] else None
            if (value or None) != old:
                label = "開始日" if key == "start_date" else "期限"
                notes.append("{}: {} → {}".format(label, old or "未設定", value or "未設定"))
            fields.append(key + "=%s")
            params.append(value)

    if "parent_id" in body:
        parent_id = as_int(body["parent_id"])
        _validate_parent(task_id, parent_id, current["project_id"])
        fields.append("parent_id=%s")
        params.append(parent_id)
        if parent_id != current["parent_id"]:
            notes.append("親タスク: {} → {}".format(
                task_label(current["parent_id"]), task_label(parent_id)))
            if "sort_order" not in body:
                # 付け替え先の末尾に置く（前の階層での並び順が残ると混ざるため）
                fields.append("sort_order=%s")
                params.append(next_sort_order(current["project_id"], parent_id))
    if "marker" in body:
        fields.append("marker=%s")
        params.append(normalize_marker(body["marker"]))
    if "sort_order" in body:
        fields.append("sort_order=%s")
        params.append(as_int(body["sort_order"], 0))

    start = as_date(body["start_date"]) if "start_date" in body else current["start_date"]
    due = as_date(body["due_date"]) if "due_date" in body else current["due_date"]
    if start and due and str(start)[:10] > str(due)[:10]:
        raise bad_request("開始日は期限より前にしてください")

    if "depends_on" in body:
        before = {r["depends_on_id"] for r in db.query(
            "SELECT depends_on_id FROM task_deps WHERE task_id=%s", (task_id,))}
        after = set(set_task_deps(task_id, current["project_id"], body["depends_on"]))
        if before != after:
            notes.append("先行タスク: {} 件 → {} 件".format(len(before), len(after)))
        fields = fields or []

    if not fields and not notes:
        raise bad_request("更新する項目がありません")
    if fields:
        fields.append("updated_at=%s")
        params.append(db.now())
        params.append(task_id)
        db.execute("UPDATE tasks SET {} WHERE id=%s".format(", ".join(fields)), params)
    else:
        touch_task(task_id)

    if notes:
        system_comment(task_id, user["id"], " / ".join(notes))
    if new_assignee and new_assignee != current["assignee_id"] and new_assignee != user["id"]:
        _notify_assignment(task_id, current["title"], new_assignee, user)
    return json_response({"task": db.query_one(TASK_SELECT + " WHERE t.id=%s", (task_id,))})


def ensure_member(user_id, project_id, role_label="担当者"):
    """割り当て先がそのプロジェクトを見られる人か確かめる。

    参加していない人に割り当てると、本人には開けないタスクだけが増えてしまう。
    """
    if not user_id:
        return
    person = db.query_one("SELECT id, name, role FROM users WHERE id=%s", (user_id,))
    if not person:
        raise bad_request("指定された利用者が見つかりません")
    if auth.project_role(person, project_id) is None:
        raise bad_request(
            "{} さんはこのプロジェクトのメンバーではありません。"
            "先にメンバーに追加してください（{}）".format(person["name"], role_label))


def task_label(task_id):
    if not task_id:
        return "トップレベル"
    return db.scalar("SELECT title AS t FROM tasks WHERE id=%s", (task_id,), default="（不明）")


def next_sort_order(project_id, parent_id):
    """同じ親を持つタスクの末尾に来る並び順。"""
    if parent_id is None:
        current = db.scalar(
            "SELECT COALESCE(MAX(sort_order), 0) AS m FROM tasks "
            "WHERE project_id=%s AND parent_id IS NULL", (project_id,), default=0)
    else:
        current = db.scalar(
            "SELECT COALESCE(MAX(sort_order), 0) AS m FROM tasks WHERE parent_id=%s",
            (parent_id,), default=0)
    return int(current or 0) + 10


def _validate_parent(task_id, parent_id, project_id):
    if parent_id is None:
        return
    if parent_id == task_id:
        raise bad_request("自分自身を親にはできません")
    parent = db.query_one("SELECT project_id FROM tasks WHERE id=%s", (parent_id,))
    if not parent or parent["project_id"] != project_id:
        raise bad_request("親タスクが同じプロジェクトにありません")
    if parent_id in descendant_ids(task_id):
        raise bad_request("子孫タスクを親にはできません")
    # 動かすタスクだけでなく、その下にぶら下がる子孫まで入る深さか確かめる
    if task_depth(parent_id) + 1 + subtree_height(task_id) > MAX_TASK_DEPTH:
        raise bad_request("階層が深すぎます（最大 {} 階層）".format(MAX_TASK_DEPTH))


@route("POST", r"/api/tasks/reorder")
def reorder_tasks(ctx):
    user = me(ctx)
    project_id = as_int(ctx.body.get("project_id"))
    project_or_404(user, project_id, "editor")
    items = ctx.body.get("items")
    if not isinstance(items, list):
        raise bad_request("items は配列で指定してください")
    valid_ids = {r["id"] for r in db.query(
        "SELECT id FROM tasks WHERE project_id=%s", (project_id,))}
    updates = []
    for item in items:
        task_id = as_int(item.get("id"))
        if task_id not in valid_ids:
            raise bad_request("プロジェクト外のタスクが含まれています")
        parent_id = as_int(item.get("parent_id"))
        if parent_id is not None and parent_id not in valid_ids:
            raise bad_request("親タスクが不正です")
        updates.append((parent_id, as_int(item.get("sort_order"), 0), db.now(), task_id))
    parents = {u[3]: u[0] for u in updates}
    for task_id in parents:
        seen, cursor, depth = set(), parents.get(task_id), 1
        while cursor is not None:
            if cursor == task_id or cursor in seen:
                raise bad_request("循環する階層は指定できません")
            seen.add(cursor)
            depth += 1
            cursor = parents.get(cursor, db.scalar(
                "SELECT parent_id FROM tasks WHERE id=%s", (cursor,)))
        # 移動するタスクだけでなく、その下の子孫まで収まるか確かめる
        if depth + subtree_height(task_id) > MAX_TASK_DEPTH:
            raise bad_request("階層が深すぎます（最大 {} 階層）".format(MAX_TASK_DEPTH))
    with db.transaction():
        db.executemany(
            "UPDATE tasks SET parent_id=%s, sort_order=%s, updated_at=%s WHERE id=%s", updates)
    return json_response({"ok": True, "updated": len(updates)})


@route("DELETE", r"/api/tasks/(\d+)")
def delete_task(ctx, task_id):
    user = me(ctx)
    task_or_404(user, task_id, "editor")
    ids = [task_id] + descendant_ids(task_id)
    stored = db.query(
        "SELECT stored_name FROM attachments WHERE task_id IN %s AND kind='file'",
        (tuple(ids),))
    with db.transaction():
        for tid in reversed(ids):
            db.execute("DELETE FROM tasks WHERE id=%s", (tid,))
    for row in stored:
        _remove_stored_file(row["stored_name"])
    return json_response({"ok": True, "deleted": len(ids)})


@route("POST", r"/api/tasks/(\d+)/deps")
def add_dep(ctx, task_id):
    user = me(ctx)
    task = task_or_404(user, task_id, "editor")
    depends_on = as_int(ctx.body.get("depends_on_id"))
    if depends_on is None:
        raise bad_request("depends_on_id は必須です")
    if depends_on == task_id:
        raise bad_request("自分自身には依存できません")
    other = db.query_one("SELECT project_id FROM tasks WHERE id=%s", (depends_on,))
    if not other or other["project_id"] != task["project_id"]:
        raise bad_request("同じプロジェクトのタスクを指定してください")
    if _creates_dep_cycle(task_id, depends_on):
        raise bad_request("依存関係が循環します")
    db.execute("INSERT IGNORE INTO task_deps(task_id, depends_on_id) VALUES(%s,%s)",
               (task_id, depends_on))
    return json_response({"ok": True})


def _creates_dep_cycle(task_id, depends_on):
    """True when depends_on already (transitively) depends on task_id."""
    frontier, seen = [depends_on], set()
    while frontier:
        rows = db.query("SELECT depends_on_id FROM task_deps WHERE task_id IN %s",
                        (tuple(frontier),))
        frontier = []
        for r in rows:
            nid = r["depends_on_id"]
            if nid == task_id:
                return True
            if nid not in seen:
                seen.add(nid)
                frontier.append(nid)
    return False


@route("DELETE", r"/api/tasks/(\d+)/deps/(\d+)")
def remove_dep(ctx, task_id, depends_on):
    user = me(ctx)
    task_or_404(user, task_id, "editor")
    db.execute("DELETE FROM task_deps WHERE task_id=%s AND depends_on_id=%s",
               (task_id, depends_on))
    return json_response({"ok": True})


# --------------------------------------------------------------------------
# comments
# --------------------------------------------------------------------------

@route("POST", r"/api/tasks/(\d+)/comments")
def add_comment(ctx, task_id):
    user = me(ctx)
    task = task_or_404(user, task_id, "commenter")
    body = require(ctx.body, "body", "コメント")
    kind = ctx.body.get("kind") if ctx.body.get("kind") in ("comment", "checkin") else "comment"
    comment_id = db.insert(
        "INSERT INTO comments(task_id, user_id, body, kind, created_at) VALUES(%s,%s,%s,%s,%s)",
        (task_id, user["id"], body, kind, db.now()))
    touch_task(task_id)
    notified = _notify_comment(task, user, body)
    row = db.query_one(
        "SELECT c.*, u.name AS user_name, u.avatar_color FROM comments c "
        "LEFT JOIN users u ON u.id = c.user_id WHERE c.id=%s", (comment_id,))
    return json_response({"comment": row, "mentioned": notified}, 201)


def _notify_comment(task, actor, body):
    """担当者・作成者・すでにコメントした人、それに名前を呼ばれた人へ知らせる。"""
    recipients = set()
    if task["assignee_id"]:
        recipients.add(task["assignee_id"])
    if task["created_by"]:
        recipients.add(task["created_by"])
    for r in db.query(
            "SELECT DISTINCT user_id FROM comments WHERE task_id=%s AND kind='comment' "
            "AND user_id IS NOT NULL", (task["id"],)):
        recipients.add(r["user_id"])
    mentioned, _labels = mentions.find(body, project_member_users(task["project_id"]))
    mentioned_ids = {m["id"] for m in mentioned} - {actor["id"]}
    recipients.discard(actor["id"])
    recipients -= mentioned_ids          # 呼ばれた人には専用の通知を出す
    excerpt = body if len(body) <= 300 else body[:300] + "…"
    for user_id in mentioned_ids:
        notify.create(
            user_id, "mention", "{} さんがあなたを呼んでいます: {}".format(
                actor["name"], task["title"]),
            "{}\n\n{}".format(excerpt, notify.task_url(task["id"])),
            task_id=task["id"], project_id=task["project_id"])
    for user_id in recipients:
        notify.create(
            user_id, "comment", "コメント: {}".format(task["title"]),
            "{} さんのコメント\n\n{}\n\n{}".format(
                actor["name"], excerpt, notify.task_url(task["id"])),
            task_id=task["id"], project_id=task["project_id"])
    return [m["name"] for m in mentioned if m["id"] in mentioned_ids]


@route("DELETE", r"/api/comments/(\d+)")
def delete_comment(ctx, comment_id):
    user = me(ctx)
    comment = db.query_one("SELECT * FROM comments WHERE id=%s", (comment_id,))
    if not comment:
        raise not_found("コメントが見つかりません")
    if comment["ticket_id"]:
        # チケットはプロジェクトに属さないので、本人か管理者かだけを見る
        ticket_or_404(comment["ticket_id"])
        if comment["user_id"] != user["id"] and not auth.is_admin(user):
            raise forbidden("自分のコメントのみ削除できます")
        db.execute("DELETE FROM comments WHERE id=%s", (comment_id,))
        return json_response({"ok": True})
    if comment["task_id"]:
        owner = task_or_404(user, comment["task_id"], "commenter")
    else:
        owner = issue_or_404(user, comment["issue_id"], "commenter")
    role = auth.project_role(user, owner["project_id"])
    if comment["user_id"] != user["id"] and role != "owner" and not auth.is_admin(user):
        raise forbidden("自分のコメントのみ削除できます")
    db.execute("DELETE FROM comments WHERE id=%s", (comment_id,))
    return json_response({"ok": True})


# --------------------------------------------------------------------------
# attachments
# --------------------------------------------------------------------------

_SAFE_NAME_RE = re.compile(r"[^\w.\- ()\[\]　-〿ぁ-んァ-ヿ一-鿿]", re.UNICODE)


def _safe_filename(name):
    name = os.path.basename(name or "file").strip() or "file"
    name = _SAFE_NAME_RE.sub("_", name)
    return name[:150]


def _remove_stored_file(stored_name):
    if not stored_name:
        return
    path = os.path.join(UPLOAD_DIR, os.path.basename(stored_name))
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


@route("POST", r"/api/tasks/(\d+)/attachments")
def add_attachment(ctx, task_id):
    user = me(ctx)
    task_or_404(user, task_id, "editor")
    response = _store_attachments(ctx, user, {"task_id": task_id})
    touch_task(task_id)
    return response


def _store_attachments(ctx, user, target):
    """Save uploaded files, or register a link, against a task, issue or ticket."""
    column, owner_id = next(iter(target.items()))
    if ctx.files:
        created = []
        for upload in ctx.files.values():
            if upload.size == 0:
                continue
            if upload.size > MAX_UPLOAD_BYTES:
                raise bad_request("ファイルサイズが上限（{} MB）を超えています".format(
                    MAX_UPLOAD_BYTES // (1024 * 1024)))
            original = _safe_filename(upload.filename)
            stored = "{}_{}".format(secrets.token_hex(12), original)
            os.makedirs(UPLOAD_DIR, exist_ok=True)
            with open(os.path.join(UPLOAD_DIR, stored), "wb") as fh:
                fh.write(upload.data)
            mime = (upload.content_type or mimetypes.guess_type(original)[0]
                    or "application/octet-stream")
            created.append(db.insert(
                "INSERT INTO attachments({}, kind, name, stored_name, size, mime, "
                "uploaded_by, created_at) VALUES(%s,'file',%s,%s,%s,%s,%s,%s)".format(column),
                (owner_id, original, stored, upload.size, mime, user["id"], db.now())))
        if not created:
            raise bad_request("ファイルが選択されていません")
        rows = db.query("SELECT * FROM attachments WHERE id IN %s", (tuple(created),))
        return json_response({"attachments": rows}, 201)

    url = require(ctx.body, "url", "URL")
    if not re.match(r"^(https?://|mailto:|file://|\\\\)", url, re.IGNORECASE):
        raise bad_request("URL は http(s):// などで始めてください")
    name = (ctx.body.get("name") or url)[:300]
    att_id = db.insert(
        "INSERT INTO attachments({}, kind, name, url, uploaded_by, created_at) "
        "VALUES(%s,'link',%s,%s,%s,%s)".format(column),
        (owner_id, name, url, user["id"], db.now()))
    return json_response(
        {"attachments": [db.query_one("SELECT * FROM attachments WHERE id=%s", (att_id,))]}, 201)


def attachment_or_404(user, attachment_id, minimum="viewer"):
    att = db.query_one("SELECT * FROM attachments WHERE id=%s", (attachment_id,))
    if not att:
        raise not_found("添付が見つかりません")
    if att["task_id"]:
        task_or_404(user, att["task_id"], minimum)
    elif att["issue_id"]:
        issue_or_404(user, att["issue_id"], minimum)
    elif att["ticket_id"]:
        ticket_or_404(att["ticket_id"])  # チケットは社内の誰でも見られる
    else:
        raise not_found("添付が見つかりません")
    return att


@route("GET", r"/api/attachments/(\d+)/download")
def download_attachment(ctx, attachment_id):
    user = me(ctx)
    att = attachment_or_404(user, attachment_id)
    if att["kind"] != "file":
        raise bad_request("これはリンクです")
    path = os.path.join(UPLOAD_DIR, os.path.basename(att["stored_name"]))
    if not os.path.isfile(path):
        raise not_found("ファイル本体が見つかりません")
    with open(path, "rb") as fh:
        data = fh.read()
    disposition = "attachment; filename*=UTF-8''{}".format(quote(att["name"]))
    return Response(200, data, att["mime"] or "application/octet-stream",
                    [("Content-Disposition", disposition)])


@route("DELETE", r"/api/attachments/(\d+)")
def delete_attachment(ctx, attachment_id):
    user = me(ctx)
    att = attachment_or_404(user, attachment_id, "editor")
    db.execute("DELETE FROM attachments WHERE id=%s", (attachment_id,))
    _remove_stored_file(att["stored_name"])
    return json_response({"ok": True})


# --------------------------------------------------------------------------
# notifications
# --------------------------------------------------------------------------

@route("GET", r"/api/notifications")
def list_notifications(ctx):
    user = me(ctx)
    sql = "SELECT * FROM notifications WHERE user_id=%s"
    params = [user["id"]]
    if as_bool(ctx.query.get("unread")):
        sql += " AND is_read=0"
    sql += " ORDER BY created_at DESC, id DESC LIMIT %s"
    params.append(as_int(ctx.query.get("limit"), 100, 1, 500))
    return json_response({
        "notifications": db.query(sql, params),
        "unread": notify.unread_count(user["id"]),
    })


@route("POST", r"/api/notifications/read")
def mark_read(ctx):
    user = me(ctx)
    if as_bool(ctx.body.get("all")):
        db.execute("UPDATE notifications SET is_read=1 WHERE user_id=%s", (user["id"],))
    else:
        ids = ctx.body.get("ids") or []
        if not isinstance(ids, list) or not ids:
            raise bad_request("ids または all を指定してください")
        db.execute("UPDATE notifications SET is_read=1 WHERE user_id=%s AND id IN %s",
                   (user["id"], tuple(as_int(i, 0) for i in ids)))
    return json_response({"unread": notify.unread_count(user["id"])})


@route("DELETE", r"/api/notifications/(\d+)")
def delete_notification(ctx, notification_id):
    user = me(ctx)
    db.execute("DELETE FROM notifications WHERE id=%s AND user_id=%s",
               (notification_id, user["id"]))
    return json_response({"ok": True})


@route("GET", r"/api/me/notification-settings")
def get_notification_settings(ctx):
    """自分宛の通知をどこまで受け取るか。プロジェクト単位のミュートも含む。"""
    user = me(ctx)
    ids = auth.visible_project_ids(user)
    projects = db.query(
        "SELECT id, name, color, notify_enabled FROM projects "
        "WHERE id IN %s AND archived=0 ORDER BY name", (tuple(ids),)) if ids else []
    return json_response({
        "prefs": prefs.email_prefs(user["id"]),
        "muted_project_ids": prefs.muted_projects(user["id"]),
        "projects": projects,
        "events": [{"value": k, "label": label, "help": help_text}
                   for k, label, help_text in prefs.EMAIL_EVENTS],
        "email_ready": notify.email_configured(),
    })


@route("PUT", r"/api/me/notification-settings")
def put_notification_settings(ctx):
    user = me(ctx)
    values = {}
    if "email_notify" in ctx.body:
        values["email_notify"] = as_bool(ctx.body["email_notify"])
    for key in prefs.EMAIL_EVENT_KEYS:
        if key in ctx.body:
            values[key] = as_bool(ctx.body[key])
    saved = prefs.save_email_prefs(user["id"], values)
    muted = prefs.muted_projects(user["id"])
    if "muted_project_ids" in ctx.body:
        wanted = ctx.body["muted_project_ids"]
        if not isinstance(wanted, list):
            raise bad_request("muted_project_ids は配列で指定してください")
        visible = set(auth.visible_project_ids(user))
        muted = prefs.set_muted_projects(
            user["id"], [i for i in wanted if as_int(i, 0) in visible])
    return json_response({"prefs": saved, "muted_project_ids": muted})


# --------------------------------------------------------------------------
# 横断検索
# --------------------------------------------------------------------------

@route("GET", r"/api/search")
def search(ctx):
    """タスク・課題・コメント・プロジェクト・自分の ToDo をまとめて探す。

    見えないプロジェクトのものは一切返さない。ToDo は本人のものだけ。
    """
    user = me(ctx)
    keyword = (ctx.query.get("q") or "").strip()
    if len(keyword) < 2:
        return json_response({"q": keyword, "groups": [], "total": 0,
                              "message": "2 文字以上で検索してください"})
    limit = as_int(ctx.query.get("limit"), 8, 1, 30)
    like = "%{}%".format(keyword)
    project_ids = auth.visible_project_ids(user)
    scope = tuple(project_ids) or (0,)

    tasks = db.query(
        "SELECT t.id, t.title, t.status, t.due_date, t.is_milestone, "
        "       p.name AS project_name, p.color AS project_color, u.name AS assignee_name "
        "  FROM tasks t JOIN projects p ON p.id = t.project_id "
        "  LEFT JOIN users u ON u.id = t.assignee_id "
        " WHERE t.project_id IN %s AND (t.title LIKE %s OR t.description LIKE %s) "
        " ORDER BY (t.status='done'), (t.due_date IS NULL), t.due_date LIMIT %s",
        (scope, like, like, limit))
    issues = db.query(
        "SELECT i.id, i.seq, i.title, i.status, i.severity, i.due_date, "
        "       p.name AS project_name, p.color AS project_color "
        "  FROM issues i JOIN projects p ON p.id = i.project_id "
        " WHERE i.project_id IN %s AND (i.title LIKE %s OR i.description LIKE %s "
        "       OR i.resolution LIKE %s) "
        " ORDER BY i.severity DESC, i.seq DESC LIMIT %s",
        (scope, like, like, like, limit))
    comments = db.query(
        "SELECT c.id, c.body, c.created_at, c.task_id, c.issue_id, c.ticket_id, "
        "       u.name AS user_name, "
        "       COALESCE(t.title, i.title, tk.title) AS parent_title, "
        "       COALESCE(pt.name, pi.name, q.name) AS project_name "
        "  FROM comments c "
        "  LEFT JOIN users u ON u.id = c.user_id "
        "  LEFT JOIN tasks t ON t.id = c.task_id "
        "  LEFT JOIN projects pt ON pt.id = t.project_id "
        "  LEFT JOIN issues i ON i.id = c.issue_id "
        "  LEFT JOIN projects pi ON pi.id = i.project_id "
        "  LEFT JOIN tickets tk ON tk.id = c.ticket_id "
        "  LEFT JOIN ticket_queues q ON q.id = tk.queue_id "
        " WHERE c.kind='comment' AND c.body LIKE %s "
        "   AND (t.project_id IN %s OR i.project_id IN %s OR c.ticket_id IS NOT NULL) "
        " ORDER BY c.created_at DESC LIMIT %s",
        (like, scope, scope, limit))
    projects = db.query(
        "SELECT id, name, description, color FROM projects "
        " WHERE id IN %s AND (name LIKE %s OR description LIKE %s) ORDER BY archived, name "
        " LIMIT %s", (scope, like, like, limit))
    todos = db.query(
        "SELECT id, title, due_date, is_done FROM todos "
        " WHERE user_id=%s AND (title LIKE %s OR note LIKE %s) "
        " ORDER BY is_done, (due_date IS NULL), due_date LIMIT %s",
        (user["id"], like, like, limit))
    # チケットは社内の誰でも読めるので、プロジェクトの見える範囲では絞らない
    ticket_rows = db.query(
        "SELECT t.id, t.title, t.status, t.kind, t.due_date, t.on_behalf_of, "
        "       q.name AS queue_name, q.color AS queue_color, a.name AS assignee_name "
        "  FROM tickets t JOIN ticket_queues q ON q.id = t.queue_id "
        "  LEFT JOIN users a ON a.id = t.assignee_id "
        " WHERE t.title LIKE %s OR t.body LIKE %s OR t.resolution LIKE %s "
        "       OR t.on_behalf_of LIKE %s "
        " ORDER BY t.status IN %s, t.id DESC LIMIT %s",
        (like, like, like, like, tickets.CLOSED_STATUSES, limit))

    groups = []
    if tasks:
        groups.append({"kind": "task", "label": "タスク", "icon": "✓", "items": tasks})
    if issues:
        groups.append({"kind": "issue", "label": "課題", "icon": "📌", "items": issues})
    if todos:
        groups.append({"kind": "todo", "label": "マイ ToDo", "icon": "📝", "items": todos})
    if ticket_rows:
        groups.append({"kind": "ticket", "label": "チケット", "icon": "🎫",
                       "items": ticket_rows})
    if projects:
        groups.append({"kind": "project", "label": "プロジェクト", "icon": "📁",
                       "items": projects})
    if comments:
        for row in comments:
            body = row["body"] or ""
            at = body.find(keyword)
            start = max(0, at - 30)
            row["excerpt"] = ("…" if start else "") + body[start:start + 120].replace("\n", " ")
        groups.append({"kind": "comment", "label": "コメント", "icon": "💬", "items": comments})

    return json_response({
        "q": keyword,
        "groups": groups,
        "total": sum(len(g["items"]) for g in groups),
    })


# --------------------------------------------------------------------------
# 状態とカテゴリの設定
# --------------------------------------------------------------------------

@route("GET", r"/api/admin/taxonomy")
def get_taxonomy(ctx):
    admin_only(ctx)
    used = {r["category"]: r["c"] for r in db.query(
        "SELECT category, COUNT(*) AS c FROM tasks WHERE category <> '' GROUP BY category")}
    categories = [dict(c, used=used.get(c["value"], 0)) for c in taxonomy.categories()]
    return json_response({
        "statuses": taxonomy.statuses(),
        "categories": categories,
        "palettes": taxonomy.STATUS_PALETTES,
        "icons": taxonomy.ICON_CHOICES,
        "status_note": "状態は完了・未完了の判定に使うため、名前と色だけ変えられます。",
    })


@route("PUT", r"/api/admin/taxonomy")
def put_taxonomy(ctx):
    admin_only(ctx)
    result = {}
    if "statuses" in ctx.body:
        result["statuses"] = taxonomy.save_statuses(ctx.body["statuses"])
    if "categories" in ctx.body:
        items = ctx.body["categories"]
        if not isinstance(items, list):
            raise bad_request("categories は配列で指定してください")
        if len(items) > 40:
            raise bad_request("カテゴリは 40 件までにしてください")
        saved = taxonomy.save_categories(items)
        result["categories"] = saved["categories"]
        result["removed"] = saved["removed"]
    if not result:
        raise bad_request("更新する項目がありません")
    return json_response(result)


# --------------------------------------------------------------------------
# 共有リンク集
# --------------------------------------------------------------------------

LINK_SELECT = """
    SELECT l.*, p.name AS project_name, p.color AS project_color, u.name AS created_by_name
      FROM shared_links l
      LEFT JOIN projects p ON p.id = l.project_id
      LEFT JOIN users u ON u.id = l.created_by
"""


def link_or_404(user, link_id, write=False):
    link = db.query_one(LINK_SELECT + " WHERE l.id=%s", (link_id,))
    if not link:
        raise not_found("リンクが見つかりません")
    if link["project_id"]:
        project_or_404(user, link["project_id"], "editor" if write else "viewer")
    elif write and not auth.is_admin(user):
        raise forbidden("全体のリンクを編集できるのは管理者だけです")
    return link


def normalize_link_url(value):
    url = str(value or "").strip()
    if not url:
        raise bad_request("URL を入力してください")
    if not re.match(r"^(https?://|mailto:|file://|\\\\)", url, re.IGNORECASE):
        raise bad_request("URL は http(s):// などで始めてください")
    return url[:2000]


@route("GET", r"/api/links")
def list_links(ctx):
    """全体で共有しているリンクと、参加しているプロジェクトのリンク。"""
    user = me(ctx)
    ids = auth.visible_project_ids(user)
    sql = LINK_SELECT + " WHERE l.project_id IS NULL"
    params = []
    if ids:
        sql += " OR l.project_id IN %s"
        params.append(tuple(ids))
    sql += (" ORDER BY (l.project_id IS NOT NULL), p.name, (l.category = ''), "
            "l.category, l.sort_order, l.id")
    rows = db.query(sql, params)
    for row in rows:
        row["can_edit"] = bool(
            auth.is_admin(user) if row["project_id"] is None
            else auth.project_role(user, row["project_id"]) in ("owner", "editor"))
    return json_response({
        "links": rows,
        # 入力の表記ゆれを減らすため、すでに使われている分類を候補として渡す
        "categories": [r["category"] for r in db.query(
            "SELECT DISTINCT category FROM shared_links WHERE category <> '' "
            "ORDER BY category")],
        "can_add_shared": auth.is_admin(user),
        "projects": db.query(
            "SELECT id, name, color FROM projects WHERE id IN %s AND archived=0 ORDER BY name",
            (tuple(ids),)) if ids else [],
    })


@route("POST", r"/api/links")
def create_link(ctx):
    user = me(ctx)
    project_id = as_int(ctx.body.get("project_id"))
    if project_id:
        project_or_404(user, project_id, "editor")
    else:
        admin_only(ctx)
    title = require(ctx.body, "title", "タイトル")
    url = normalize_link_url(ctx.body.get("url"))
    now = db.now()
    order = (db.scalar(
        "SELECT COALESCE(MAX(sort_order), 0) AS m FROM shared_links WHERE "
        + ("project_id=%s" if project_id else "project_id IS NULL"),
        (project_id,) if project_id else (), default=0) or 0) + 10
    link_id = db.insert(
        "INSERT INTO shared_links(project_id, title, url, note, category, sort_order, "
        "created_by, created_at, updated_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (project_id, title[:200], url, str(ctx.body.get("note") or "")[:500],
         str(ctx.body.get("category") or "").strip()[:40], order, user["id"], now, now))
    return json_response({"link": db.query_one(LINK_SELECT + " WHERE l.id=%s", (link_id,))}, 201)


@route("PATCH", r"/api/links/(\d+)")
def update_link(ctx, link_id):
    user = me(ctx)
    current = link_or_404(user, link_id, write=True)
    fields, params = [], []
    if "title" in ctx.body:
        fields.append("title=%s")
        params.append(require(ctx.body, "title", "タイトル")[:200])
    if "url" in ctx.body:
        fields.append("url=%s")
        params.append(normalize_link_url(ctx.body["url"]))
    if "note" in ctx.body:
        fields.append("note=%s")
        params.append(str(ctx.body["note"] or "")[:500])
    if "category" in ctx.body:
        fields.append("category=%s")
        params.append(str(ctx.body["category"] or "").strip()[:40])
    if "sort_order" in ctx.body:
        fields.append("sort_order=%s")
        params.append(as_int(ctx.body["sort_order"], 0))
    if "project_id" in ctx.body:
        target = as_int(ctx.body["project_id"])
        if target:
            project_or_404(user, target, "editor")
        else:
            admin_only(ctx)
        fields.append("project_id=%s")
        params.append(target)
    if not fields:
        raise bad_request("更新する項目がありません")
    fields.append("updated_at=%s")
    params += [db.now(), current["id"]]
    db.execute("UPDATE shared_links SET {} WHERE id=%s".format(", ".join(fields)), params)
    return json_response({"link": db.query_one(LINK_SELECT + " WHERE l.id=%s", (current["id"],))})


@route("DELETE", r"/api/links/(\d+)")
def delete_link(ctx, link_id):
    user = me(ctx)
    link = link_or_404(user, link_id, write=True)
    db.execute("DELETE FROM shared_links WHERE id=%s", (link["id"],))
    return json_response({"ok": True})


# --------------------------------------------------------------------------
# 休日（祝日 + 会社の休業日）
# --------------------------------------------------------------------------

@route("GET", r"/api/holidays")
def list_holidays(ctx):
    """期間内の休日。ガントの網掛けと負荷計算で使う。"""
    me(ctx)
    start = as_date(ctx.query.get("from")) or db.today().replace(month=1, day=1).isoformat()
    end = as_date(ctx.query.get("to")) or db.today().replace(month=12, day=31).isoformat()
    if db.get_setting("use_holidays", "1") != "1":
        return json_response({"holidays": [], "enabled": False})
    found = holidays.holidays_between(as_pydate(start), as_pydate(end))
    company = set(holidays.company_holidays(start, end))
    return json_response({
        "enabled": True,
        "holidays": [{"day": day.isoformat(), "name": name,
                      "company": day in company}
                     for day, name in sorted(found.items())],
    })


@route("POST", r"/api/holidays")
def add_company_holiday(ctx):
    """会社独自の休業日（年末年始・夏季休暇など）を足す。"""
    admin_only(ctx)
    day = as_date(ctx.body.get("day"))
    if not day:
        raise bad_request("日付を指定してください")
    name = (ctx.body.get("name") or "休業日")[:100]
    db.execute(
        "INSERT INTO company_holidays(day, name, created_at) VALUES(%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE name=VALUES(name)", (day, name, db.now()))
    return json_response({"ok": True, "day": day, "name": name}, 201)


@route("DELETE", r"/api/holidays/(\d{4}-\d{2}-\d{2})")
def delete_company_holiday(ctx, day):
    admin_only(ctx)
    db.execute("DELETE FROM company_holidays WHERE day=%s", (day,))
    return json_response({"ok": True})


def as_pydate(value):
    from datetime import datetime
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


# --------------------------------------------------------------------------
# 一括取り込み / 一括編集
# --------------------------------------------------------------------------

# 取り込みで受け付ける列。value, 表示名, 説明（画面の対応づけに使う）
IMPORT_FIELDS = [
    ("title", "タスク名", "必須。先頭の空白やインデントは階層として読み取ります"),
    ("level", "階層", "0 か 1 から始まる数字。指定がなければタスク名のインデントで判断します"),
    ("parent", "親タスク名", "同じ取り込みの中の別のタスク名。階層より優先します"),
    ("assignee", "担当者", "氏名またはメールアドレス"),
    ("start_date", "開始日", "2026-04-01 / 2026/4/1 / 4月1日 など"),
    ("due_date", "期限", "同上"),
    ("category", "カテゴリ", "調査・リサーチ / 設計・企画 …（表示名でも英字でも可）"),
    ("priority", "重要度", "低 / 中 / 高 / 最重要 または 0〜3"),
    ("status", "状態", "未着手 / 進行中 / レビュー中 / 完了 / ブロック中"),
    ("progress", "進捗", "0〜100 の数字（% は付いていても構いません）"),
    ("estimate_hours", "見積 (h)", "数字。空欄でも構いません"),
    ("description", "メモ", ""),
    ("is_milestone", "マイルストーン", "○ / はい / 1 などでマイルストーン扱い"),
]
IMPORT_FIELD_KEYS = [f[0] for f in IMPORT_FIELDS]

TRUE_WORDS = {"1", "true", "yes", "y", "はい", "○", "◯", "〇", "◎", "有", "あり", "true"}
def status_by_label():
    return {row["label"]: row["value"] for row in taxonomy.statuses()}
IMPORTANCE_BY_LABEL = {label: value for value, label in IMPORTANCE_LABEL.items()}
def category_by_label():
    return {row["label"]: row["value"] for row in taxonomy.categories()}


def _import_text(value):
    return "" if value is None else str(value).strip()


def _import_date(value):
    """Excel から来がちな書き方をひととおり受ける。"""
    text = _import_text(value).replace("　", " ")
    if not text:
        return None
    text = re.sub(r"[（(].*?[)）]", "", text).strip()          # 「4/1(水)」の曜日を落とす
    match = re.match(r"^(\d{4})\D+(\d{1,2})\D+(\d{1,2})", text)
    if match:
        y, m, d = (int(g) for g in match.groups())
    else:
        match = re.match(r"^(\d{1,2})\D+(\d{1,2})\D*$", text)
        if not match:
            return None
        y = db.today().year
        m, d = (int(g) for g in match.groups())
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def _import_number(value, lo=None, hi=None):
    text = _import_text(value).replace("%", "").replace("時間", "").replace("h", "")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if lo is not None:
        number = max(lo, number)
    if hi is not None:
        number = min(hi, number)
    return number


def _import_level(row):
    """階層の深さ。数字の列があればそれ、なければタスク名のインデント。"""
    raw = _import_text(row.get("level"))
    if raw:
        digits = re.sub(r"\D", "", raw)
        if digits:
            level = int(digits)
            return max(0, level - 1) if level >= 1 and raw.strip()[0] not in "0" else level
    title = str(row.get("title") or "")
    indent = len(title) - len(title.lstrip(" 　\t"))
    return indent // 2 if indent else 0


@route("GET", r"/api/import/fields")
def import_fields(ctx):
    me(ctx)
    return json_response({
        "fields": [{"value": v, "label": label, "help": help_text}
                   for v, label, help_text in IMPORT_FIELDS],
        "statuses": list(status_by_label()),
        "categories": list(category_by_label()),
        "importance": list(IMPORTANCE_BY_LABEL),
    })


@route("POST", r"/api/projects/(\d+)/tasks/import")
def import_tasks(ctx, project_id):
    """表計算ソフトからの一括取り込み。dry_run=true なら登録せず結果だけ返す。"""
    user = me(ctx)
    project_or_404(user, project_id, "editor")
    rows = ctx.body.get("rows")
    if not isinstance(rows, list) or not rows:
        raise bad_request("取り込む行がありません")
    if len(rows) > 1000:
        raise bad_request("一度に取り込めるのは 1000 行までです")
    dry_run = as_bool(ctx.body.get("dry_run"))

    members = {}
    for person in assignable_users(project_id):
        members[person["name"].strip()] = person["id"]
        members[person["email"].strip().lower()] = person["id"]

    prepared, problems = [], []
    for index, raw in enumerate(rows):
        line = index + 1
        if not isinstance(raw, dict):
            problems.append({"line": line, "message": "行の形式が正しくありません"})
            continue
        title = _import_text(raw.get("title"))
        if not title:
            problems.append({"line": line, "message": "タスク名が空です"})
            continue

        assignee_id = None
        assignee_text = _import_text(raw.get("assignee"))
        if assignee_text:
            assignee_id = members.get(assignee_text) or members.get(assignee_text.lower())
            if not assignee_id:
                problems.append({
                    "line": line,
                    "message": "「{}」はこのプロジェクトのメンバーに見つかりません".format(
                        assignee_text),
                })

        status_text = _import_text(raw.get("status"))
        status = status_by_label().get(
            status_text, status_text if status_text in STATUSES else "todo")
        priority_text = _import_text(raw.get("priority"))
        if priority_text in IMPORTANCE_BY_LABEL:
            priority = IMPORTANCE_BY_LABEL[priority_text]
        else:
            priority = int(_import_number(priority_text, 0, 3) or 1)
        category_text = _import_text(raw.get("category"))
        category = category_by_label().get(category_text, "")
        if not category and category_text in taxonomy.category_values():
            category = category_text

        start_date = _import_date(raw.get("start_date"))
        due_date = _import_date(raw.get("due_date"))
        if start_date and due_date and start_date > due_date:
            problems.append({"line": line, "message": "開始日が期限より後になっています"})
            start_date = None

        progress = _import_number(raw.get("progress"), 0, 100)
        estimate = _import_number(raw.get("estimate_hours"), 0, 9999)
        prepared.append({
            "line": line,
            "title": title.strip(),
            "level": _import_level(raw),
            "parent": _import_text(raw.get("parent")),
            "assignee_id": assignee_id,
            "start_date": start_date,
            "due_date": due_date,
            "category": category,
            "priority": priority,
            "status": status,
            "progress": int(progress) if progress is not None else (100 if status == "done" else 0),
            "estimate_hours": estimate,
            "description": _import_text(raw.get("description")),
            "is_milestone": 1 if _import_text(raw.get("is_milestone")).lower() in TRUE_WORDS else 0,
        })

    if dry_run:
        return json_response({
            "ok": not problems, "would_create": len(prepared), "problems": problems,
            "preview": prepared[:50],
        })
    if not prepared:
        raise bad_request("取り込める行がありませんでした")

    created, by_title, stack = [], {}, {}
    # 同じ名前が複数あると「親タスク名」でどれを指すか決められない
    seen_titles = {}
    for item in prepared:
        seen_titles[item["title"]] = seen_titles.get(item["title"], 0) + 1
    now = db.now()
    base_order = (db.scalar(
        "SELECT COALESCE(MAX(sort_order), 0) AS m FROM tasks WHERE project_id=%s",
        (project_id,), default=0) or 0)
    with db.transaction():
        for offset, item in enumerate(prepared):
            parent_id = None
            if item["parent"] and item["parent"] in by_title:
                if seen_titles.get(item["parent"], 0) > 1:
                    problems.append({
                        "line": item["line"],
                        "message": "「{}」という名前のタスクが複数あるため、"
                                   "直前のものを親にしました".format(item["parent"]),
                    })
                parent_id = by_title[item["parent"]]
            else:
                level = min(item["level"], MAX_TASK_DEPTH - 1)
                # 親は「一つ浅い階層で直前に出てきたタスク」
                while level > 0 and level - 1 not in stack:
                    level -= 1
                parent_id = stack.get(level - 1) if level > 0 else None
                item["level"] = level
            if parent_id and task_depth(parent_id) >= MAX_TASK_DEPTH:
                parent_id = None
            task_id = db.insert(
                "INSERT INTO tasks(project_id, parent_id, title, description, category, status, "
                "priority, assignee_id, start_date, due_date, progress, estimate_hours, "
                "is_milestone, sort_order, created_by, created_at, updated_at, completed_at) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (project_id, parent_id, item["title"], item["description"], item["category"],
                 item["status"], item["priority"], item["assignee_id"], item["start_date"],
                 item["due_date"], item["progress"], item["estimate_hours"],
                 item["is_milestone"], base_order + (offset + 1) * 10, user["id"], now, now,
                 now if item["status"] == "done" else None))
            created.append(task_id)
            by_title[item["title"]] = task_id
            stack[item["level"]] = task_id
            for deeper in [k for k in stack if k > item["level"]]:
                stack.pop(deeper)
    return json_response({"created": len(created), "task_ids": created,
                          "problems": problems}, 201)


BULK_EDITABLE = ("assignee_id", "status", "category", "priority", "due_date", "start_date")


@route("POST", r"/api/tasks/bulk")
def bulk_update_tasks(ctx):
    """選んだタスクをまとめて更新する。プロジェクトをまたいでも構わない。"""
    user = me(ctx)
    ids = ctx.body.get("ids")
    if not isinstance(ids, list) or not ids:
        raise bad_request("対象のタスクを選んでください")
    ids = [as_int(i) for i in ids if as_int(i)]
    if len(ids) > 500:
        raise bad_request("一度に更新できるのは 500 件までです")
    action = ctx.body.get("action") or "set"
    tasks = db.query("SELECT * FROM tasks WHERE id IN %s", (tuple(ids),))
    if len(tasks) != len(set(ids)):
        raise not_found("見つからないタスクが含まれています")
    for task in tasks:
        project_or_404(user, task["project_id"], "editor")

    if action == "delete":
        targets = set()
        for task in tasks:
            targets.add(task["id"])
            targets.update(descendant_ids(task["id"]))
        stored = db.query(
            "SELECT stored_name FROM attachments WHERE task_id IN %s AND kind='file'",
            (tuple(targets),))
        with db.transaction():
            db.execute("DELETE FROM tasks WHERE id IN %s", (tuple(targets),))
        for row in stored:
            _remove_stored_file(row["stored_name"])
        return json_response({"deleted": len(targets)})

    if action == "shift":
        days = as_int(ctx.body.get("days"), 0)
        if not days:
            raise bad_request("ずらす日数を指定してください")
        moved = 0
        with db.transaction():
            for task in tasks:
                if not (task["start_date"] or task["due_date"]):
                    continue
                db.execute(
                    "UPDATE tasks SET start_date = DATE_ADD(start_date, INTERVAL %s DAY), "
                    "due_date = DATE_ADD(due_date, INTERVAL %s DAY), updated_at=%s WHERE id=%s",
                    (days, days, db.now(), task["id"]))
                moved += 1
        for task in tasks:
            system_comment(task["id"], user["id"],
                           "日程を {} 日{}にずらしました".format(abs(days),
                                                     "後ろ" if days > 0 else "前"))
        return json_response({"updated": moved})

    fields, params, notes = [], [], []
    body = ctx.body
    if "assignee_id" in body:
        assignee_id = as_int(body["assignee_id"])
        for task in tasks:
            ensure_member(assignee_id, task["project_id"])
        fields.append("assignee_id=%s")
        params.append(assignee_id)
        notes.append("担当: {}".format(
            db.scalar("SELECT name AS n FROM users WHERE id=%s", (assignee_id,),
                      default="未割当") if assignee_id else "未割当"))
    if "status" in body:
        status = body["status"] if body["status"] in STATUSES else None
        if not status:
            raise bad_request("状態の指定が正しくありません")
        fields += ["status=%s", "completed_at=%s"]
        params += [status, db.now() if status == "done" else None]
        if status == "done":
            fields.append("progress=100")
        notes.append("状態: {}".format(status_label(status)))
    if "category" in body:
        fields.append("category=%s")
        params.append(normalize_category(body["category"]))
        notes.append("カテゴリを変更")
    if "priority" in body:
        fields.append("priority=%s")
        params.append(as_int(body["priority"], 1, 0, 3))
        notes.append("重要度: {}".format(IMPORTANCE_LABEL[as_int(body["priority"], 1, 0, 3)]))
    for key in ("due_date", "start_date"):
        if key in body:
            fields.append(key + "=%s")
            params.append(as_date(body[key]))
            notes.append("{}: {}".format("期限" if key == "due_date" else "開始日",
                                         as_date(body[key]) or "未設定"))
    if not fields:
        raise bad_request("更新する項目がありません")

    fields.append("updated_at=%s")
    params.append(db.now())
    with db.transaction():
        db.execute("UPDATE tasks SET {} WHERE id IN %s".format(", ".join(fields)),
                   params + [tuple(ids)])
    note = "一括更新 — " + " / ".join(notes)
    for task in tasks:
        system_comment(task["id"], user["id"], note)
    if "assignee_id" in body and as_int(body["assignee_id"]):
        new_assignee = as_int(body["assignee_id"])
        for task in tasks:
            if task["assignee_id"] != new_assignee and new_assignee != user["id"]:
                _notify_assignment(task["id"], task["title"], new_assignee, user)
    return json_response({"updated": len(ids)})


# --------------------------------------------------------------------------
# 個人 ToDo（プロジェクトに属さない、自分だけのメモ書き）
# --------------------------------------------------------------------------

TODO_SELECT = "SELECT id, title, note, due_date, is_done, sort_order, done_at, created_at FROM todos"


def todo_or_404(user, todo_id):
    """自分の ToDo でなければ、存在自体を伏せる。"""
    todo = db.query_one("SELECT * FROM todos WHERE id=%s AND user_id=%s",
                        (todo_id, user["id"]))
    if not todo:
        raise not_found("ToDo が見つかりません")
    return todo


@route("GET", r"/api/todos")
def list_todos(ctx):
    user = me(ctx)
    sql = TODO_SELECT + " WHERE user_id=%s"
    params = [user["id"]]
    if not as_bool(ctx.query.get("include_done")):
        sql += " AND is_done=0"
    sql += " ORDER BY is_done, (due_date IS NULL), due_date, sort_order, id"
    rows = db.query(sql, params)
    return json_response({
        "todos": rows,
        "open_count": db.scalar("SELECT COUNT(*) AS c FROM todos WHERE user_id=%s AND is_done=0",
                                (user["id"],), default=0),
    })


@route("GET", r"/api/todos/(\d+)")
def get_todo(ctx, todo_id):
    user = me(ctx)
    todo_or_404(user, todo_id)
    return json_response({"todo": db.query_one(TODO_SELECT + " WHERE id=%s", (todo_id,))})


@route("POST", r"/api/todos")
def create_todo(ctx):
    user = me(ctx)
    title = require(ctx.body, "title", "ToDo")
    now = db.now()
    todo_id = db.insert(
        "INSERT INTO todos(user_id, title, note, due_date, sort_order, created_at, updated_at) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s)",
        (user["id"], title[:300], str(ctx.body.get("note") or "")[:1000],
         as_date(ctx.body.get("due_date")), next_todo_order(user["id"]), now, now))
    return json_response({"todo": db.query_one(TODO_SELECT + " WHERE id=%s", (todo_id,))}, 201)


def next_todo_order(user_id):
    current = db.scalar("SELECT COALESCE(MAX(sort_order), 0) AS m FROM todos WHERE user_id=%s",
                        (user_id,), default=0)
    return int(current or 0) + 10


@route("PATCH", r"/api/todos/(\d+)")
def update_todo(ctx, todo_id):
    user = me(ctx)
    current = todo_or_404(user, todo_id)
    fields, params = [], []
    if "title" in ctx.body:
        fields.append("title=%s")
        params.append(require(ctx.body, "title", "ToDo")[:300])
    if "note" in ctx.body:
        fields.append("note=%s")
        params.append(str(ctx.body["note"] or "")[:1000])
    if "due_date" in ctx.body:
        fields.append("due_date=%s")
        params.append(as_date(ctx.body["due_date"]))
    if "is_done" in ctx.body:
        done = as_bool(ctx.body["is_done"])
        fields += ["is_done=%s", "done_at=%s"]
        params += [1 if done else 0, db.now() if done else None]
    if "sort_order" in ctx.body:
        fields.append("sort_order=%s")
        params.append(as_int(ctx.body["sort_order"], 0))
    if not fields:
        raise bad_request("更新する項目がありません")
    fields.append("updated_at=%s")
    params += [db.now(), current["id"]]
    db.execute("UPDATE todos SET {} WHERE id=%s".format(", ".join(fields)), params)
    return json_response({"todo": db.query_one(TODO_SELECT + " WHERE id=%s", (current["id"],))})


@route("DELETE", r"/api/todos/(\d+)")
def delete_todo(ctx, todo_id):
    user = me(ctx)
    todo = todo_or_404(user, todo_id)
    db.execute("DELETE FROM todos WHERE id=%s", (todo["id"],))
    return json_response({"ok": True})


@route("POST", r"/api/todos/reorder")
def reorder_todos(ctx):
    user = me(ctx)
    ids = ctx.body.get("ids")
    if not isinstance(ids, list):
        raise bad_request("ids は配列で指定してください")
    mine = {r["id"] for r in db.query("SELECT id FROM todos WHERE user_id=%s", (user["id"],))}
    updates = []
    for index, value in enumerate(ids):
        todo_id = as_int(value)
        if todo_id not in mine:
            raise bad_request("自分の ToDo だけ並べ替えられます")
        updates.append(((index + 1) * 10, db.now(), todo_id))
    if updates:
        db.executemany("UPDATE todos SET sort_order=%s, updated_at=%s WHERE id=%s", updates)
    return json_response({"ok": True, "updated": len(updates)})


@route("POST", r"/api/todos/(\d+)/promote")
def promote_todo(ctx, todo_id):
    """ToDo が大きくなってきたら、プロジェクトのタスクに引き上げる。"""
    user = me(ctx)
    todo = todo_or_404(user, todo_id)
    project_id = as_int(ctx.body.get("project_id"))
    if not project_id:
        raise bad_request("project_id は必須です")
    project_or_404(user, project_id, "editor")
    ensure_member(user["id"], project_id)
    now = db.now()
    task_id = db.insert(
        "INSERT INTO tasks(project_id, title, description, status, priority, assignee_id, "
        "due_date, sort_order, created_by, created_at, updated_at) "
        "VALUES(%s,%s,%s,'todo',1,%s,%s,%s,%s,%s,%s)",
        (project_id, todo["title"], todo["note"], user["id"], todo["due_date"],
         next_sort_order(project_id, None), user["id"], now, now))
    db.execute("DELETE FROM todos WHERE id=%s", (todo["id"],))
    return json_response(
        {"task": db.query_one(TASK_SELECT + " WHERE t.id=%s", (task_id,))}, 201)


# --------------------------------------------------------------------------
# daily check-in
# --------------------------------------------------------------------------

@route("GET", r"/api/daily")
def daily(ctx):
    user = me(ctx)
    buckets = notify.daily_summary_for(user["id"])
    today = db.today()
    # 参加していないプロジェクトのタスクは、担当でも出さない
    visible = tuple(auth.visible_project_ids(user)) or (0,)
    recent = db.query(
        """
        SELECT t.id, t.title, t.status, t.progress, t.due_date, p.name AS project_name
          FROM tasks t JOIN projects p ON p.id = t.project_id
         WHERE t.assignee_id=%s AND t.status='done' AND t.completed_at >= %s
           AND t.project_id IN %s
         ORDER BY t.completed_at DESC LIMIT 20
        """,
        (user["id"], db.now() - timedelta(days=7), visible))
    # 開始日がまだ来ていないタスクは、動いていなくて当たり前なので数えない
    stale = db.query(
        """
        SELECT t.id, t.title, t.status, t.progress, t.due_date, p.name AS project_name,
               t.updated_at
          FROM tasks t JOIN projects p ON p.id = t.project_id
         WHERE t.assignee_id=%s AND t.status IN %s AND p.archived=0
           AND t.updated_at < %s AND t.project_id IN %s
           AND (t.start_date IS NULL OR t.start_date <= %s)
         ORDER BY t.updated_at LIMIT 20
        """,
        (user["id"], OPEN_STATUSES, db.now() - timedelta(days=7), visible, today))
    checkin = db.query_one(
        "SELECT * FROM checkins WHERE user_id=%s AND checkin_date=%s", (user["id"], today))
    issues = db.query(
        "SELECT i.id, i.seq, i.title, i.status, i.severity, i.due_date, p.name AS project_name "
        "FROM issues i JOIN projects p ON p.id = i.project_id "
        "WHERE i.owner_id=%s AND i.status IN %s AND p.archived=0 AND i.project_id IN %s "
        "ORDER BY i.severity DESC, (i.due_date IS NULL), i.due_date LIMIT 20",
        (user["id"], OPEN_ISSUE_STATUSES, visible))
    todos = db.query(
        TODO_SELECT + " WHERE user_id=%s AND is_done=0 "
        "ORDER BY (due_date IS NULL), due_date, sort_order, id LIMIT 20", (user["id"],))
    # 自分が担当のチケット。期限切れ → 期限の近い順 → 優先度の高い順で並べる。
    my_tickets = db.query(
        TICKET_BASE + " WHERE t.assignee_id=%s AND t.status IN %s "
        "ORDER BY (t.due_date IS NOT NULL AND t.due_date < %s) DESC, "
        "(t.due_date IS NULL), t.due_date, t.priority DESC, t.id LIMIT 20",
        (user["id"], tickets.OPEN_STATUSES, today))
    # 誰も受けていないチケットは、件数だけ知らせて一覧へ送る
    unclaimed = db.scalar(
        "SELECT COUNT(*) AS c FROM tickets WHERE assignee_id IS NULL AND status IN %s",
        (tickets.OPEN_STATUSES,), default=0) or 0
    return json_response({
        "date": today, "buckets": buckets, "recently_done": recent,
        "stale": stale, "checkin": checkin, "issues": issues, "todos": todos,
        "tickets": my_tickets, "unclaimed_tickets": unclaimed,
        "streak": _checkin_streak(user["id"]),
    })


def _checkin_streak(user_id):
    rows = db.query(
        "SELECT checkin_date FROM checkins WHERE user_id=%s ORDER BY checkin_date DESC LIMIT 60",
        (user_id,))
    streak, expected = 0, db.today()
    dates = {r["checkin_date"] for r in rows}
    if expected not in dates:
        expected = expected - timedelta(days=1)
    while expected in dates:
        streak += 1
        expected = expected - timedelta(days=1)
    return streak


@route("POST", r"/api/daily/update")
def daily_update(ctx):
    """Apply several quick progress updates in one go, from the daily screen."""
    user = me(ctx)
    updates = ctx.body.get("updates") or []
    if not isinstance(updates, list):
        raise bad_request("updates は配列で指定してください")
    applied = []
    for item in updates:
        task_id = as_int(item.get("task_id"))
        if task_id is None:
            continue
        current = task_or_404(user, task_id, "editor")
        patch = {}
        if "progress" in item:
            patch["progress"] = as_int(item["progress"], current["progress"], 0, 100)
        if item.get("status") in STATUSES:
            patch["status"] = item["status"]
        if "due_date" in item:
            patch["due_date"] = as_date(item["due_date"])
        notes = []
        if patch:
            status, progress = _apply_status_progress(patch, current)
            fields = ["status=%s", "progress=%s", "updated_at=%s"]
            params = [status, progress, db.now()]
            if status != current["status"]:
                notes.append("状態: {} → {}".format(
                    status_label(current["status"]), status_label(status)))
                fields.append("completed_at=%s")
                params.append(db.now() if status == "done" else None)
            if progress != current["progress"]:
                notes.append("進捗: {}% → {}%".format(current["progress"], progress))
            if "due_date" in patch:
                old = current["due_date"].isoformat() if current["due_date"] else "未設定"
                if (patch["due_date"] or "未設定") != old:
                    notes.append("期限: {} → {}".format(old, patch["due_date"] or "未設定"))
                fields.append("due_date=%s")
                params.append(patch["due_date"])
            params.append(task_id)
            db.execute("UPDATE tasks SET {} WHERE id=%s".format(", ".join(fields)), params)
        hours = as_hours(item.get("hours"))
        if hours:
            db.execute("UPDATE tasks SET actual_hours = actual_hours + %s WHERE id=%s",
                       (hours, task_id))
            notes.append("実績 +{:g}h".format(hours))
        note = (item.get("note") or "").strip()
        if note:
            db.insert(
                "INSERT INTO comments(task_id, user_id, body, kind, created_at) "
                "VALUES(%s,%s,%s,'checkin',%s)", (task_id, user["id"], note, db.now()))
            task = db.query_one("SELECT * FROM tasks WHERE id=%s", (task_id,))
            _notify_comment(task, user, note)
        if notes:
            system_comment(task_id, user["id"], "日次更新 — " + " / ".join(notes))
        applied.append(task_id)

    note = (ctx.body.get("note") or "").strip()
    today = db.today()
    db.execute(
        "INSERT INTO checkins(user_id, checkin_date, note, created_at) VALUES(%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE note=VALUES(note)", (user["id"], today, note, db.now()))
    return json_response({"ok": True, "updated": applied, "streak": _checkin_streak(user["id"])})


# --------------------------------------------------------------------------
# settings / admin
# --------------------------------------------------------------------------

SECRET_SETTINGS = ("smtp_password", "llm_api_key")


@route("GET", r"/api/settings")
def get_settings(ctx):
    admin_only(ctx)
    settings = db.all_settings()
    for key in SECRET_SETTINGS:
        settings[key] = "********" if settings.get(key) else ""
    return json_response({
        "settings": settings,
        "email_ready": notify.email_configured(),
        "llm_ready": llm.available(),
        "llm_sdk": llm.sdk_installed(),
        "llm_models": llm.MODELS,
        "slack_ready": slack.available(),
        "slack_events": [{"value": k, "label": label, "help": help_text}
                         for k, label, help_text in prefs.SLACK_EVENTS],
    })


@route("PUT", r"/api/settings")
def put_settings(ctx):
    admin_only(ctx)
    allowed = set(db.DEFAULT_SETTINGS)
    for key, value in (ctx.body.get("settings") or {}).items():
        if key not in allowed:
            continue
        if key in SECRET_SETTINGS and value == "********":
            continue
        if key == "slack_events":
            value = prefs.format_events(
                value if isinstance(value, list) else str(value or "").split(","),
                prefs.SLACK_EVENT_KEYS)
        db.set_setting(key, value)
    return json_response({"ok": True})


@route("POST", r"/api/settings/test-mail")
def test_mail(ctx):
    user = admin_only(ctx)
    to = ctx.body.get("to") or user["email"]
    ok, message = notify.send_email(
        to, "[テスト] タスク管理システムのメール設定",
        "このメールが届いていれば SMTP 設定は正しく動作しています。", user["name"])
    # テスト自体は実行できているので 200 を返し、成否は本文で伝える
    return json_response({"ok": ok, "message": message})


@route("POST", r"/api/admin/run-digest")
def run_digest(ctx):
    admin_only(ctx)
    return json_response(notify.run_daily_digest(force=True))


@route("GET", r"/api/meta")
def meta(ctx):
    me(ctx)  # カテゴリ名などは社内情報なので、ログインしていない相手には返さない
    return json_response({
        "statuses": taxonomy.statuses(),
        "importance": [{"value": k, "label": v}
                       for k, v in sorted(IMPORTANCE_LABEL.items(), reverse=True)],
        "categories": taxonomy.categories(),
        "project_roles": [
            {"value": "owner", "label": "オーナー（設定変更・削除）"},
            {"value": "editor", "label": "編集者（タスク追加・編集）"},
            {"value": "commenter", "label": "コメント可（閲覧＋コメント）"},
            {"value": "viewer", "label": "閲覧のみ"},
        ],
        "issue_statuses": [{"value": v, "label": ISSUE_STATUS_LABEL[v]} for v in ISSUE_STATUSES],
        "issue_categories": [{"value": v, "label": label, "color": color}
                             for v, label, color in ISSUE_CATEGORIES],
        "severity": [{"value": k, "label": v}
                     for k, v in sorted(SEVERITY_LABEL.items(), reverse=True)],
        "llm_available": llm.available(),
        "tickets": tickets.meta(),
        "slack_events": [{"value": k, "label": label, "help": help_text}
                         for k, label, help_text in prefs.SLACK_EVENTS],
        "slack_enabled": db.get_setting("slack_enabled", "0") == "1",
        "markers": [{"value": v, "label": label} for v, label in MARKERS],
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        "max_depth": MAX_TASK_DEPTH,
    })


# --------------------------------------------------------------------------
# 課題管理表 (issue log)
# --------------------------------------------------------------------------

ISSUE_SELECT = """
    SELECT i.*, o.name AS owner_name, o.avatar_color AS owner_color,
           r.name AS raised_by_name,
           p.name AS project_name, p.color AS project_color,
           (SELECT COUNT(*) FROM issue_tasks it WHERE it.issue_id = i.id) AS task_count,
           (SELECT COUNT(*) FROM issue_tasks it JOIN tasks t ON t.id = it.task_id
             WHERE it.issue_id = i.id AND t.status <> 'done') AS open_task_count,
           (SELECT COUNT(*) FROM comments c
             WHERE c.issue_id = i.id AND c.kind = 'comment') AS comment_count,
           (SELECT COUNT(*) FROM attachments a WHERE a.issue_id = i.id) AS attachment_count
      FROM issues i
      LEFT JOIN users o ON o.id = i.owner_id
      LEFT JOIN users r ON r.id = i.raised_by
      JOIN projects p ON p.id = i.project_id
"""


def issue_or_404(user, issue_id, minimum="viewer"):
    issue = db.query_one("SELECT * FROM issues WHERE id=%s", (issue_id,))
    if not issue:
        raise not_found("課題が見つかりません")
    project_or_404(user, issue["project_id"], minimum)
    return issue


def normalize_issue_category(value, current="other"):
    if value is None:
        return current
    value = str(value).strip() or "other"
    if value not in ISSUE_CATEGORY_VALUES:
        raise bad_request("不明な課題区分です: {}".format(value))
    return value


def issue_status(value, current="open"):
    if value is None:
        return current
    if value not in ISSUE_STATUSES:
        raise bad_request("不明なステータスです: {}".format(value))
    return value


def set_issue_tasks(issue_id, project_id, task_ids):
    """Replace the set of tasks an issue is linked to."""
    if not isinstance(task_ids, list):
        raise bad_request("task_ids は配列で指定してください")
    wanted = []
    for raw in task_ids:
        task_id = as_int(raw)
        if task_id is None or task_id in wanted:
            continue
        owner = db.query_one("SELECT project_id FROM tasks WHERE id=%s", (task_id,))
        if not owner or owner["project_id"] != project_id:
            raise bad_request("同じプロジェクトのタスクを指定してください")
        wanted.append(task_id)
    with db.transaction():
        db.execute("DELETE FROM issue_tasks WHERE issue_id=%s", (issue_id,))
        db.executemany("INSERT IGNORE INTO issue_tasks(issue_id, task_id) VALUES(%s,%s)",
                       [(issue_id, t) for t in wanted])
    return wanted


def issue_comment(issue_id, user_id, text, kind="system"):
    db.insert(
        "INSERT INTO comments(issue_id, user_id, body, kind, created_at) VALUES(%s,%s,%s,%s,%s)",
        (issue_id, user_id, text, kind, db.now()))


@route("GET", r"/api/projects/(\d+)/issues")
def list_project_issues(ctx, project_id):
    user = me(ctx)
    project = project_or_404(user, project_id)
    where = ["i.project_id = %s"]
    params = [project_id]
    _apply_issue_filters(ctx, where, params)
    rows = db.query(
        ISSUE_SELECT + " WHERE " + " AND ".join(where)
        + " ORDER BY (i.status IN ('resolved','closed')), i.severity DESC,"
          " (i.due_date IS NULL), i.due_date, i.seq",
        params)
    overall = db.query(
        "SELECT status, severity, due_date FROM issues WHERE project_id=%s", (project_id,))
    return json_response({
        "issues": rows, "project": project, "summary": issue_summary(overall),
    })


@route("GET", r"/api/issues")
def search_issues(ctx):
    """Issues across every project the user can see."""
    user = me(ctx)
    ids = auth.visible_project_ids(user)
    if not ids:
        return json_response({"issues": [], "summary": issue_summary([])})
    where = ["i.project_id IN %s"]
    params = [tuple(ids)]
    if not as_bool(ctx.query.get("include_archived")):
        where.append("p.archived = 0")
    _apply_issue_filters(ctx, where, params)
    rows = db.query(
        ISSUE_SELECT + " WHERE " + " AND ".join(where)
        + " ORDER BY (i.status IN ('resolved','closed')), i.severity DESC,"
          " (i.due_date IS NULL), i.due_date, i.id LIMIT %s",
        params + [as_int(ctx.query.get("limit"), 300, 1, 1000)])
    overall = db.query(
        "SELECT i.status, i.severity, i.due_date FROM issues i "
        "JOIN projects p ON p.id = i.project_id WHERE i.project_id IN %s AND p.archived = 0",
        (tuple(ids),))
    return json_response({"issues": rows, "summary": issue_summary(overall)})


def _apply_issue_filters(ctx, where, params):
    status = ctx.query.get("status")
    if status == "open":
        where.append("i.status IN %s")
        params.append(OPEN_ISSUE_STATUSES)
    elif status in ISSUE_STATUSES:
        where.append("i.status = %s")
        params.append(status)
    category = ctx.query.get("category")
    if category:
        where.append("i.category = %s")
        params.append(normalize_issue_category(category))
    owner = ctx.query.get("owner_id")
    if owner == "me":
        where.append("i.owner_id = %s")
        params.append(ctx.user["id"])
    elif owner == "none":
        where.append("i.owner_id IS NULL")
    elif as_int(owner):
        where.append("i.owner_id = %s")
        params.append(as_int(owner))
    if as_bool(ctx.query.get("overdue")):
        where.append("i.status IN %s AND i.due_date IS NOT NULL AND i.due_date < %s")
        params.append(OPEN_ISSUE_STATUSES)
        params.append(db.today())
    severity = as_int(ctx.query.get("min_severity"))
    if severity is not None:
        where.append("i.severity >= %s")
        params.append(severity)
    query = (ctx.query.get("q") or "").strip()
    if query:
        where.append("(i.title LIKE %s OR i.description LIKE %s OR i.resolution LIKE %s)")
        params += ["%{}%".format(query)] * 3


def issue_summary(rows):
    today = db.today()
    open_rows = [r for r in rows if r["status"] in OPEN_ISSUE_STATUSES]
    return {
        "total": len(rows),
        "open": len(open_rows),
        "overdue": sum(1 for r in open_rows if r["due_date"] and r["due_date"] < today),
        "high": sum(1 for r in open_rows if r["severity"] >= 2),
        "resolved": sum(1 for r in rows if r["status"] in ("resolved", "closed")),
    }


@route("POST", r"/api/issues")
def create_issue(ctx):
    user = me(ctx)
    project_id = as_int(ctx.body.get("project_id"))
    if project_id is None:
        raise bad_request("project_id は必須です")
    project_or_404(user, project_id, "editor")
    title = require(ctx.body, "title", "課題")
    status = issue_status(ctx.body.get("status"), "open")
    raised_on = as_date(ctx.body.get("raised_on")) or db.today().isoformat()
    ensure_member(as_int(ctx.body.get("owner_id")), project_id, "対応者")
    now = db.now()
    with db.transaction():
        seq = (db.scalar("SELECT COALESCE(MAX(seq), 0) AS m FROM issues WHERE project_id=%s "
                         "FOR UPDATE", (project_id,), default=0) or 0) + 1
        issue_id = db.insert(
            "INSERT INTO issues(project_id, seq, title, description, category, status, severity, "
            "owner_id, raised_by, raised_on, due_date, resolved_on, resolution, "
            "created_at, updated_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (project_id, seq, title, ctx.body.get("description", ""),
             normalize_issue_category(ctx.body.get("category")), status,
             as_int(ctx.body.get("severity"), 1, 0, 3), as_int(ctx.body.get("owner_id")),
             user["id"], raised_on, as_date(ctx.body.get("due_date")),
             as_date(ctx.body.get("resolved_on")), ctx.body.get("resolution", ""), now, now))
    if "task_ids" in ctx.body:
        set_issue_tasks(issue_id, project_id, ctx.body["task_ids"])
    owner_id = as_int(ctx.body.get("owner_id"))
    if owner_id and owner_id != user["id"]:
        _notify_issue_owner(issue_id, title, owner_id, user, project_id)
    severity = as_int(ctx.body.get("severity"), 1, 0, 3)
    if severity >= 2:
        base = db.get_setting("app_base_url", "").rstrip("/")
        slack.post_async(
            "📌 課題が起票されました（影響度 {}）\n*{}*\n起票: {}{}".format(
                SEVERITY_LABEL.get(severity, "-"), title, user["name"],
                "\n{}/#/issue/{}".format(base, issue_id) if base else ""),
            project_id=project_id, event="issue")
    return json_response({"issue": db.query_one(ISSUE_SELECT + " WHERE i.id=%s", (issue_id,))}, 201)


def _notify_issue_owner(issue_id, title, owner_id, actor, project_id=None):
    base = db.get_setting("app_base_url", "").rstrip("/")
    link = "{}/#/issue/{}".format(base, issue_id) if base else ""
    notify.create(owner_id, "assigned", "課題の対応者に設定されました: {}".format(title),
                  "{} さんが対応者に設定しました\n{}".format(actor["name"], link),
                  project_id=project_id, issue_id=issue_id)


@route("GET", r"/api/issues/(\d+)")
def get_issue(ctx, issue_id):
    user = me(ctx)
    issue_or_404(user, issue_id)
    issue = db.query_one(ISSUE_SELECT + " WHERE i.id=%s", (issue_id,))
    tasks = db.query(
        TASK_SELECT + " JOIN issue_tasks it ON it.task_id = t.id WHERE it.issue_id=%s "
        "ORDER BY t.sort_order, t.id", (issue_id,))
    comments = db.query(
        "SELECT c.*, u.name AS user_name, u.avatar_color FROM comments c "
        "LEFT JOIN users u ON u.id = c.user_id WHERE c.issue_id=%s ORDER BY c.created_at, c.id",
        (issue_id,))
    attachments = db.query(
        "SELECT a.*, u.name AS uploaded_by_name FROM attachments a "
        "LEFT JOIN users u ON u.id = a.uploaded_by WHERE a.issue_id=%s ORDER BY a.created_at",
        (issue_id,))
    linked_tickets = db.query(
        "SELECT t.id, t.title, t.status, t.kind, t.on_behalf_of, "
        "       q.name AS queue_name, q.icon AS queue_icon "
        "  FROM tickets t JOIN ticket_issues ti ON ti.ticket_id = t.id "
        "  JOIN ticket_queues q ON q.id = t.queue_id "
        " WHERE ti.issue_id=%s ORDER BY t.id", (issue_id,))
    return json_response({
        "issue": issue, "tasks": tasks, "comments": comments, "attachments": attachments,
        "tickets": linked_tickets,
        "members": project_member_users(issue["project_id"]),
        "my_role": auth.project_role(user, issue["project_id"]),
    })


@route("PATCH", r"/api/issues/(\d+)")
def update_issue(ctx, issue_id):
    user = me(ctx)
    current = issue_or_404(user, issue_id, "editor")
    body = ctx.body
    fields, params, notes = [], [], []

    if "title" in body:
        fields.append("title=%s")
        params.append(require(body, "title", "課題"))
    for key, label in (("description", "内容"), ("resolution", "対応方針")):
        if key in body:
            fields.append(key + "=%s")
            params.append(body[key] or "")
    if "category" in body:
        category = normalize_issue_category(body["category"], current["category"])
        if category != current["category"]:
            notes.append("区分: {} → {}".format(
                ISSUE_CATEGORY_LABEL.get(current["category"], "-"),
                ISSUE_CATEGORY_LABEL.get(category, "-")))
        fields.append("category=%s")
        params.append(category)
    if "severity" in body:
        severity = as_int(body["severity"], current["severity"], 0, 3)
        if severity != current["severity"]:
            notes.append("影響度: {} → {}".format(
                SEVERITY_LABEL.get(current["severity"], "-"), SEVERITY_LABEL.get(severity, "-")))
        fields.append("severity=%s")
        params.append(severity)
    if "status" in body:
        status = issue_status(body["status"], current["status"])
        if status != current["status"]:
            notes.append("状態: {} → {}".format(
                ISSUE_STATUS_LABEL.get(current["status"], "-"),
                ISSUE_STATUS_LABEL.get(status, "-")))
            # 解決日は状態に合わせて自動で入れる／消す
            if status in ("resolved", "closed") and not current["resolved_on"] \
                    and "resolved_on" not in body:
                fields.append("resolved_on=%s")
                params.append(db.today())
            elif status in OPEN_ISSUE_STATUSES and "resolved_on" not in body:
                fields.append("resolved_on=%s")
                params.append(None)
        fields.append("status=%s")
        params.append(status)

    new_owner = current["owner_id"]
    if "owner_id" in body:
        new_owner = as_int(body["owner_id"])
        if new_owner != current["owner_id"]:
            ensure_member(new_owner, current["project_id"], "対応者")
            notes.append("対応者: {} → {}".format(
                db.scalar("SELECT name AS n FROM users WHERE id=%s",
                          (current["owner_id"],), default="未割当") or "未割当",
                db.scalar("SELECT name AS n FROM users WHERE id=%s",
                          (new_owner,), default="未割当") or "未割当"))
        fields.append("owner_id=%s")
        params.append(new_owner)

    for key, label in (("raised_on", "発生日"), ("due_date", "期限"), ("resolved_on", "解決日")):
        if key in body:
            value = as_date(body[key])
            if key == "raised_on" and value is None:
                raise bad_request("発生日は必須です")
            old = current[key].isoformat() if current[key] else None
            if (value or None) != old:
                notes.append("{}: {} → {}".format(label, old or "未設定", value or "未設定"))
            fields.append(key + "=%s")
            params.append(value)

    if "task_ids" in body:
        before = {r["task_id"] for r in db.query(
            "SELECT task_id FROM issue_tasks WHERE issue_id=%s", (issue_id,))}
        after = set(set_issue_tasks(issue_id, current["project_id"], body["task_ids"]))
        if before != after:
            notes.append("関連タスク: {} 件 → {} 件".format(len(before), len(after)))

    if not fields and not notes:
        raise bad_request("更新する項目がありません")
    if fields:
        fields.append("updated_at=%s")
        params.append(db.now())
        params.append(issue_id)
        db.execute("UPDATE issues SET {} WHERE id=%s".format(", ".join(fields)), params)
    else:
        db.execute("UPDATE issues SET updated_at=%s WHERE id=%s", (db.now(), issue_id))

    if notes:
        issue_comment(issue_id, user["id"], " / ".join(notes))
    if new_owner and new_owner != current["owner_id"] and new_owner != user["id"]:
        _notify_issue_owner(issue_id, current["title"], new_owner, user)
    return json_response({"issue": db.query_one(ISSUE_SELECT + " WHERE i.id=%s", (issue_id,))})


@route("DELETE", r"/api/issues/(\d+)")
def delete_issue(ctx, issue_id):
    user = me(ctx)
    issue_or_404(user, issue_id, "editor")
    stored = db.query(
        "SELECT stored_name FROM attachments WHERE issue_id=%s AND kind='file'", (issue_id,))
    db.execute("DELETE FROM issues WHERE id=%s", (issue_id,))
    for row in stored:
        _remove_stored_file(row["stored_name"])
    return json_response({"ok": True})


@route("PUT", r"/api/issues/(\d+)/tasks")
def link_issue_tasks(ctx, issue_id):
    user = me(ctx)
    issue = issue_or_404(user, issue_id, "editor")
    linked = set_issue_tasks(issue_id, issue["project_id"], ctx.body.get("task_ids") or [])
    issue_comment(issue_id, user["id"], "関連タスクを {} 件に更新".format(len(linked)))
    return json_response({"task_ids": linked})


@route("POST", r"/api/issues/(\d+)/comments")
def add_issue_comment(ctx, issue_id):
    user = me(ctx)
    issue = issue_or_404(user, issue_id, "commenter")
    body = require(ctx.body, "body", "コメント")
    comment_id = db.insert(
        "INSERT INTO comments(issue_id, user_id, body, kind, created_at) "
        "VALUES(%s,%s,%s,'comment',%s)", (issue_id, user["id"], body, db.now()))
    db.execute("UPDATE issues SET updated_at=%s WHERE id=%s", (db.now(), issue_id))
    notified = _notify_issue_comment(issue, user, body)
    row = db.query_one(
        "SELECT c.*, u.name AS user_name, u.avatar_color FROM comments c "
        "LEFT JOIN users u ON u.id = c.user_id WHERE c.id=%s", (comment_id,))
    return json_response({"comment": row, "mentioned": notified}, 201)


def _notify_issue_comment(issue, actor, body):
    recipients = set()
    if issue["owner_id"]:
        recipients.add(issue["owner_id"])
    if issue["raised_by"]:
        recipients.add(issue["raised_by"])
    for r in db.query("SELECT DISTINCT user_id FROM comments WHERE issue_id=%s "
                      "AND kind='comment' AND user_id IS NOT NULL", (issue["id"],)):
        recipients.add(r["user_id"])
    mentioned, _labels = mentions.find(body, project_member_users(issue["project_id"]))
    mentioned_ids = {m["id"] for m in mentioned} - {actor["id"]}
    recipients.discard(actor["id"])
    recipients -= mentioned_ids
    base = db.get_setting("app_base_url", "").rstrip("/")
    link = "{}/#/issue/{}".format(base, issue["id"]) if base else ""
    excerpt = body if len(body) <= 300 else body[:300] + "…"
    for user_id in mentioned_ids:
        notify.create(user_id, "mention", "{} さんがあなたを呼んでいます: {}".format(
            actor["name"], issue["title"]),
            "{}\n\n{}".format(excerpt, link), project_id=issue["project_id"],
            issue_id=issue["id"])
    for user_id in recipients:
        notify.create(user_id, "comment", "課題コメント: {}".format(issue["title"]),
                      "{} さんのコメント\n\n{}\n\n{}".format(actor["name"], excerpt, link),
                      project_id=issue["project_id"], issue_id=issue["id"])
    return [m["name"] for m in mentioned if m["id"] in mentioned_ids]


@route("POST", r"/api/issues/(\d+)/attachments")
def add_issue_attachment(ctx, issue_id):
    user = me(ctx)
    issue_or_404(user, issue_id, "editor")
    return _store_attachments(ctx, user, {"issue_id": issue_id})


# --------------------------------------------------------------------------
# チケット（受付窓口に届く依頼・問い合わせ・障害）
#
# プロジェクト管理とは別建て。窓口ごとに受けて、作業が要るものだけを
# タスクへ、論点として残すものは課題へ渡す。つなぎは連結テーブルだけで、
# チケット側はプロジェクトの権限を持たない。
# --------------------------------------------------------------------------

# 一覧では 1 行ずつ数えると件数ぶんサブクエリが走るので、本体は軽くしておき、
# 関連件数は返す行ぶんだけ後からまとめて引く（attach_counts）。
TICKET_BASE = """
    SELECT t.*, q.name AS queue_name, q.color AS queue_color, q.icon AS queue_icon,
           q.project_id AS queue_project_id, qp.name AS queue_project_name,
           c.label AS category_label, c.color AS category_color,
           a.name AS assignee_name, a.avatar_color AS assignee_color,
           r.name AS requester_name, r.avatar_color AS requester_color
      FROM tickets t
      JOIN ticket_queues q ON q.id = t.queue_id
      LEFT JOIN projects qp ON qp.id = q.project_id
      LEFT JOIN ticket_categories c ON c.id = t.category_id
      LEFT JOIN users a ON a.id = t.assignee_id
      LEFT JOIN users r ON r.id = t.requester_id
"""
TICKET_SELECT = TICKET_BASE

COUNT_KEYS = ("task_count", "open_task_count", "issue_count",
              "comment_count", "attachment_count")


def attach_counts(rows):
    """関連タスク・課題・コメント・添付の件数を、まとめて 4 回で数える。"""
    for row in rows:
        for key in COUNT_KEYS:
            row[key] = 0
    ids = [row["id"] for row in rows]
    if not ids:
        return rows
    scope = tuple(ids)
    by_id = {row["id"]: row for row in rows}

    for item in db.query(
            "SELECT tt.ticket_id AS id, COUNT(*) AS n, "
            "SUM(tk.status <> 'done') AS open_n "
            "FROM ticket_tasks tt JOIN tasks tk ON tk.id = tt.task_id "
            "WHERE tt.ticket_id IN %s GROUP BY tt.ticket_id", (scope,)):
        by_id[item["id"]]["task_count"] = int(item["n"])
        by_id[item["id"]]["open_task_count"] = int(item["open_n"] or 0)
    for item in db.query(
            "SELECT ticket_id AS id, COUNT(*) AS n FROM ticket_issues "
            "WHERE ticket_id IN %s GROUP BY ticket_id", (scope,)):
        by_id[item["id"]]["issue_count"] = int(item["n"])
    for item in db.query(
            "SELECT ticket_id AS id, COUNT(*) AS n FROM comments "
            "WHERE ticket_id IN %s AND kind='comment' GROUP BY ticket_id", (scope,)):
        by_id[item["id"]]["comment_count"] = int(item["n"])
    for item in db.query(
            "SELECT ticket_id AS id, COUNT(*) AS n FROM attachments "
            "WHERE ticket_id IN %s GROUP BY ticket_id", (scope,)):
        by_id[item["id"]]["attachment_count"] = int(item["n"])
    return rows


def next_issue_seq(project_id):
    """プロジェクト内の課題 No. を採番する。"""
    return (db.scalar("SELECT COALESCE(MAX(seq), 0) AS m FROM issues WHERE project_id=%s "
                      "FOR UPDATE", (project_id,), default=0) or 0) + 1


def ticket_or_404(ticket_id):
    """チケットは社内の誰でも読める。見えない範囲がないので所属は見ない。"""
    row = db.query_one(TICKET_SELECT + " WHERE t.id=%s", (ticket_id,))
    if not row:
        raise not_found("チケットが見つかりません")
    attach_counts([row])
    return row


def can_drop_ticket(user, ticket):
    """消せるのは出した本人と管理者だけ。対応履歴を他人に消させない。"""
    return auth.is_admin(user) or ticket["requester_id"] == user["id"]


def queue_or_404(queue_id):
    row = db.query_one(QUEUE_SELECT + " WHERE q.id=%s", (tickets.OPEN_STATUSES, queue_id))
    if not row:
        raise not_found("窓口が見つかりません")
    attach_categories([row])
    return row


def save_queue_categories(queue_id, items):
    """窓口の分類を、渡された並びのとおりに作り直す。

    id が付いている行は名前と色だけ更新する。消えた分類を使っていた
    チケットは「分類なし」に戻る（外部キーが SET NULL）。
    """
    if not isinstance(items, list):
        raise bad_request("分類の形式が正しくありません")
    if len(items) > 40:
        raise bad_request("分類は 40 件までにしてください")
    keep = []
    for order, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        label = (raw.get("label") or "").strip()[:60]
        if not label:
            continue
        color = (raw.get("color") or "#98a2b3")[:20]
        current = as_int(raw.get("id"))
        if current and db.query_one(
                "SELECT 1 AS x FROM ticket_categories WHERE id=%s AND queue_id=%s",
                (current, queue_id)):
            db.execute(
                "UPDATE ticket_categories SET label=%s, color=%s, sort_order=%s WHERE id=%s",
                (label, color, (order + 1) * 10, current))
            keep.append(current)
        else:
            keep.append(db.insert(
                "INSERT INTO ticket_categories(queue_id, label, color, sort_order) "
                "VALUES(%s,%s,%s,%s)", (queue_id, label, color, (order + 1) * 10)))
    if keep:
        db.execute("DELETE FROM ticket_categories WHERE queue_id=%s AND id NOT IN %s",
                   (queue_id, tuple(keep)))
    else:
        db.execute("DELETE FROM ticket_categories WHERE queue_id=%s", (queue_id,))


def ticket_category(queue_id, value, current=None):
    """その窓口にある分類かどうかを見る。他の窓口のものは受け取らない。"""
    category_id = as_int(value)
    if category_id is None:
        return None
    row = db.query_one("SELECT queue_id FROM ticket_categories WHERE id=%s", (category_id,))
    if not row or row["queue_id"] != queue_id:
        raise bad_request("その分類はこの窓口にありません")
    return category_id


def queue_project(user, body, current=None):
    """窓口に紐づけるプロジェクト。無指定ならそのまま、null なら外す。

    紐づけは「タスクにするときの既定の行き先」を決めるためのもので、
    チケットの見える範囲は変えない（チケットは社内の誰でも読める）。
    """
    if "project_id" not in body:
        return current["project_id"] if current else None
    project_id = as_int(body["project_id"])
    if project_id is None:
        return None
    project_or_404(user, project_id)
    return project_id


QUEUE_SELECT = """
    SELECT q.*, p.name AS project_name, p.color AS project_color, p.archived AS project_archived,
           (SELECT COUNT(*) FROM tickets t WHERE t.queue_id = q.id) AS ticket_count,
           (SELECT COUNT(*) FROM tickets t WHERE t.queue_id = q.id AND t.status IN %s)
               AS open_count
      FROM ticket_queues q
      LEFT JOIN projects p ON p.id = q.project_id
"""


def attach_categories(rows):
    """窓口ごとの分類をまとめて引いて配る。1 件ずつ引くとすぐ N+1 になる。"""
    ids = [row["id"] for row in rows]
    by_queue = {}
    if ids:
        for cat in db.query(
                "SELECT id, queue_id, label, color, sort_order FROM ticket_categories "
                "WHERE queue_id IN %s ORDER BY sort_order, id", (tuple(ids),)):
            by_queue.setdefault(cat["queue_id"], []).append(cat)
    for row in rows:
        row["categories"] = by_queue.get(row["id"], [])
    return rows


@route("GET", r"/api/ticket-queues")
def list_queues(ctx):
    me(ctx)
    rows = db.query(QUEUE_SELECT + " ORDER BY q.sort_order, q.id", (tickets.OPEN_STATUSES,))
    return json_response({"queues": attach_categories(rows)})


@route("POST", r"/api/ticket-queues")
def create_queue(ctx):
    user = admin_only(ctx)
    name = require(ctx.body, "name", "窓口名")
    order = as_int(ctx.body.get("sort_order"))
    if order is None:
        order = (db.scalar("SELECT COALESCE(MAX(sort_order), 0) AS m FROM ticket_queues",
                           default=0) or 0) + 10
    try:
        queue_id = db.insert(
            "INSERT INTO ticket_queues(name, description, color, icon, project_id, "
            "default_kind, sort_order, is_active, created_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (name[:80], (ctx.body.get("description") or "")[:300],
             (ctx.body.get("color") or "#3b6ef5")[:20], (ctx.body.get("icon") or "")[:8],
             queue_project(user, ctx.body), tickets.kind(ctx.body.get("default_kind")),
             order, 0 if "is_active" in ctx.body and not as_bool(ctx.body["is_active"]) else 1,
             db.now()))
    except pymysql.err.IntegrityError:
        raise bad_request("同じ名前の窓口があります")
    if "categories" in ctx.body:
        save_queue_categories(queue_id, ctx.body["categories"])
    return json_response({"queue": queue_or_404(queue_id)}, 201)


@route("PATCH", r"/api/ticket-queues/(\d+)")
def update_queue(ctx, queue_id):
    user = admin_only(ctx)
    current = queue_or_404(queue_id)
    fields, params = [], []
    if "project_id" in ctx.body:
        fields.append("project_id=%s")
        params.append(queue_project(user, ctx.body, current))
    if "name" in ctx.body:
        fields.append("name=%s")
        params.append(require(ctx.body, "name", "窓口名")[:80])
    for key, limit in (("description", 300), ("color", 20), ("icon", 8)):
        if key in ctx.body:
            fields.append(key + "=%s")
            params.append((ctx.body.get(key) or "")[:limit])
    if "sort_order" in ctx.body:
        fields.append("sort_order=%s")
        params.append(as_int(ctx.body["sort_order"], 0))
    if "is_active" in ctx.body:
        fields.append("is_active=%s")
        params.append(1 if as_bool(ctx.body["is_active"]) else 0)
    if "default_kind" in ctx.body:
        fields.append("default_kind=%s")
        params.append(tickets.kind(ctx.body["default_kind"], current["default_kind"]))
    if "categories" in ctx.body:
        save_queue_categories(queue_id, ctx.body["categories"])
    if not fields:
        return json_response({"queue": queue_or_404(queue_id)})
    params.append(queue_id)
    try:
        db.execute("UPDATE ticket_queues SET {} WHERE id=%s".format(", ".join(fields)), params)
    except pymysql.err.IntegrityError:
        raise bad_request("同じ名前の窓口があります")
    return json_response({"queue": queue_or_404(queue_id)})


@route("DELETE", r"/api/ticket-queues/(\d+)")
def delete_queue(ctx, queue_id):
    admin_only(ctx)
    queue_or_404(queue_id)
    left = db.scalar("SELECT COUNT(*) AS c FROM tickets WHERE queue_id=%s",
                     (queue_id,), default=0)
    if left:
        raise bad_request(
            "この窓口には {} 件のチケットが残っています。"
            "先に移すか、窓口を「受付停止」にしてください".format(left))
    db.execute("DELETE FROM ticket_queues WHERE id=%s", (queue_id,))
    return json_response({"ok": True})


@route("GET", r"/api/tickets")
def list_tickets(ctx):
    user = me(ctx)
    where, params = [], []
    queue_id = as_int(ctx.query.get("queue_id"))
    if queue_id:
        where.append("t.queue_id=%s")
        params.append(queue_id)
    project_id = as_int(ctx.query.get("project_id"))
    if project_id:
        # そのプロジェクト専用の窓口に来ているものだけ
        where.append("q.project_id=%s")
        params.append(project_id)
    status = (ctx.query.get("status") or "open").strip()
    if status == "open":
        where.append("t.status IN %s")
        params.append(tickets.OPEN_STATUSES)
    elif status and status != "all":
        where.append("t.status=%s")
        params.append(tickets.status(status))
    kind = (ctx.query.get("kind") or "").strip()
    if kind in tickets.KIND_VALUES:
        where.append("t.kind=%s")
        params.append(kind)
    category_id = as_int(ctx.query.get("category_id"))
    if category_id:
        where.append("t.category_id=%s")
        params.append(category_id)
    scope = (ctx.query.get("scope") or "").strip()
    if scope == "mine":
        where.append("t.assignee_id=%s")
        params.append(user["id"])
    elif scope == "raised":
        where.append("t.requester_id=%s")
        params.append(user["id"])
    elif scope == "unassigned":
        where.append("t.assignee_id IS NULL")
    assignee_id = as_int(ctx.query.get("assignee_id"))
    if assignee_id:
        where.append("t.assignee_id=%s")
        params.append(assignee_id)
    q = (ctx.query.get("q") or "").strip()
    if q:
        where.append("(t.title LIKE %s OR t.body LIKE %s OR t.on_behalf_of LIKE %s)")
        params += ["%{}%".format(q)] * 3

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = attach_counts(db.query(
        TICKET_SELECT + clause
        + " ORDER BY t.status IN %s DESC, t.priority DESC, "
          "t.due_date IS NULL, t.due_date, t.id DESC LIMIT 400",
        tuple(params) + (tickets.CLOSED_STATUSES,)))
    today = db.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    summary = db.query_one(
        "SELECT COUNT(*) AS total, "
        "SUM(status IN %s) AS open, "
        "SUM(status='new') AS waiting, "
        "SUM(assignee_id IS NULL AND status IN %s) AS unassigned, "
        "SUM(due_date IS NOT NULL AND due_date < %s AND status IN %s) AS overdue, "
        "SUM(status='done') AS done_total, "
        "SUM(status='done' AND resolved_at >= %s) AS done_week, "
        "SUM(status='done' AND resolved_at >= %s) AS done_month "
        "FROM tickets",
        (tickets.OPEN_STATUSES, tickets.OPEN_STATUSES, today, tickets.OPEN_STATUSES,
         week_start, month_start))
    # 受けてから返すまでにかかった日数（直近 90 日に片付いたぶん）
    turnaround = db.scalar(
        "SELECT AVG(TIMESTAMPDIFF(HOUR, created_at, resolved_at)) AS m FROM tickets "
        "WHERE status='done' AND resolved_at IS NOT NULL AND resolved_at >= %s",
        (today - timedelta(days=90),))
    return json_response({
        "tickets": rows,
        "summary": {k: int(v or 0) for k, v in (summary or {}).items()},
        "turnaround_days": (round(float(turnaround) / 24, 1)
                            if turnaround is not None else None),
    })


TICKET_IMPORT_FIELDS = [
    ("title", "件名", "必須"),
    ("queue", "窓口", "窓口名。空欄なら画面で選んだ窓口に入ります"),
    ("kind", "種別", "依頼 / 問い合わせ / 障害"),
    ("status", "状態", "受付待ち / 対応中 / 保留 / 完了 / 取り下げ"),
    ("priority", "優先度", "低 / 中 / 高 / 緊急 または 0〜3"),
    ("category", "分類", "その窓口に登録してある分類名"),
    ("on_behalf_of", "依頼元", "「営業部 田中」など"),
    ("assignee", "担当", "氏名またはメールアドレス"),
    ("due_date", "期限", "2026-04-01 / 2026/4/1 / 4月1日 など"),
    ("occurred_at", "発生日時", "障害のとき。2026-04-01 09:30 など"),
    ("spent_hours", "対応時間", "かかった時間。1.5 / 90分 など。空欄でも構いません"),
    ("body", "内容", ""),
    ("resolution", "対応結果", ""),
    ("created_at", "受付日", "過去ぶんを入れるときに。空欄なら取り込んだ日時"),
]
TICKET_IMPORT_KEYS = [f[0] for f in TICKET_IMPORT_FIELDS]


@route("GET", r"/api/tickets/import-fields")
def ticket_import_fields(ctx):
    me(ctx)
    return json_response({
        "fields": [{"value": v, "label": label, "help": help_text}
                   for v, label, help_text in TICKET_IMPORT_FIELDS],
        "kinds": {label: value for value, label, _i in tickets.KINDS},
        "statuses": {label: value for value, label, _c in tickets.STATUSES},
        "priorities": {label: value for value, label in tickets.PRIORITY_LABEL.items()},
    })


def _import_hours(value):
    """「1.5」「90分」「2h」あたりをまとめて受ける。"""
    text = _import_text(value)
    if not text:
        return None
    minutes = re.match(r"^(\d+(?:\.\d+)?)\s*(?:分|min)$", text)
    if minutes:
        return round(float(minutes.group(1)) / 60, 1)
    number = _import_number(text, 0, 9999)
    return round(number, 1) if number is not None else None


def _import_datetime(value):
    """日付だけでも、時刻付きでも受ける。"""
    text = _import_text(value).replace("　", " ").replace("T", " ")
    if not text:
        return None
    day = _import_date(text.split(" ")[0])
    if not day:
        return None
    match = re.search(r"(\d{1,2})\s*[:時]\s*(\d{1,2})", text)
    if not match:
        return day + " 00:00:00"
    hour, minute = (int(g) for g in match.groups())
    return "{} {:02d}:{:02d}:00".format(day, min(hour, 23), min(minute, 59))


@route("POST", r"/api/tickets/import")
def import_tickets(ctx):
    """CSV や Excel からチケットをまとめて登録する。dry_run=true なら登録しない。"""
    user = me(ctx)
    rows = ctx.body.get("rows")
    if not isinstance(rows, list) or not rows:
        raise bad_request("取り込む行がありません")
    if len(rows) > 1000:
        raise bad_request("一度に取り込めるのは 1000 行までです")
    dry_run = as_bool(ctx.body.get("dry_run"))
    fallback_queue = as_int(ctx.body.get("queue_id"))
    fallback_row = None
    if fallback_queue:
        fallback_row = db.query_one("SELECT * FROM ticket_queues WHERE id=%s",
                                    (fallback_queue,))
        if not fallback_row:
            raise not_found("窓口が見つかりません")

    queues = {q["name"].strip(): q for q in db.query("SELECT * FROM ticket_queues")}
    categories = {}
    for row in db.query("SELECT id, queue_id, label FROM ticket_categories"):
        categories[(row["queue_id"], row["label"].strip())] = row["id"]
    people = {}
    for person in db.query("SELECT id, name, email FROM users WHERE is_active=1"):
        people[person["name"].strip()] = person["id"]
        people[person["email"].strip().lower()] = person["id"]
    kind_by_label = {label: value for value, label, _i in tickets.KINDS}
    status_by_label = {label: value for value, label, _c in tickets.STATUSES}
    priority_by_label = {label: value for value, label in tickets.PRIORITY_LABEL.items()}

    prepared, problems = [], []
    for index, raw in enumerate(rows):
        line = index + 1
        if not isinstance(raw, dict):
            problems.append({"line": line, "message": "行の形式が正しくありません"})
            continue
        title = _import_text(raw.get("title"))
        if not title:
            problems.append({"line": line, "message": "件名が空です"})
            continue

        queue_name = _import_text(raw.get("queue"))
        queue = queues.get(queue_name) if queue_name else None
        if queue_name and not queue:
            problems.append({"line": line,
                             "message": "窓口「{}」がありません".format(queue_name)})
            continue
        queue = queue or fallback_row
        queue_id = queue["id"] if queue else None
        if not queue_id:
            problems.append({"line": line, "message": "窓口が決まっていません"})
            continue

        assignee_text = _import_text(raw.get("assignee"))
        assignee_id = None
        if assignee_text:
            assignee_id = people.get(assignee_text) or people.get(assignee_text.lower())
            if not assignee_id:
                problems.append({
                    "line": line,
                    "message": "「{}」という人が見つかりません".format(assignee_text)})

        category_text = _import_text(raw.get("category"))
        category_id = None
        if category_text:
            category_id = categories.get((queue_id, category_text))
            if not category_id:
                problems.append({
                    "line": line,
                    "message": "分類「{}」はこの窓口にありません".format(category_text)})

        kind_text = _import_text(raw.get("kind"))
        default_kind = (queue or {}).get("default_kind") or tickets.DEFAULT_KIND
        kind = kind_by_label.get(kind_text) or tickets.kind(kind_text, default_kind)
        status_text = _import_text(raw.get("status"))
        status = status_by_label.get(status_text) or tickets.status(status_text)
        priority_text = _import_text(raw.get("priority"))
        if priority_text in priority_by_label:
            priority = priority_by_label[priority_text]
        else:
            number = _import_number(priority_text, 0, 3)
            priority = int(number) if number is not None else 1

        created = _import_datetime(raw.get("created_at")) or db.now()
        resolved = created if status in tickets.CLOSED_STATUSES else None
        prepared.append({
            "queue_id": queue_id, "kind": kind, "category_id": category_id,
            "title": title[:300],
            "body": _import_text(raw.get("body")), "status": status, "priority": priority,
            "on_behalf_of": _import_text(raw.get("on_behalf_of"))[:120],
            "assignee_id": assignee_id, "due_date": _import_date(raw.get("due_date")),
            "occurred_at": _import_datetime(raw.get("occurred_at")),
            "spent_hours": _import_hours(raw.get("spent_hours")),
            "resolution": _import_text(raw.get("resolution")),
            "created_at": created, "resolved_at": resolved,
        })

    if dry_run:
        return json_response({"would_create": len(prepared), "problems": problems,
                              "preview": prepared[:8]})
    if not prepared:
        raise bad_request("登録できる行がありませんでした")

    created_ids = []
    for item in prepared:
        created_ids.append(db.insert(
            "INSERT INTO tickets(queue_id, kind, category_id, title, body, status, priority, "
            "requester_id, on_behalf_of, assignee_id, due_date, occurred_at, spent_hours, "
            "resolution, resolved_at, created_at, updated_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (item["queue_id"], item["kind"], item["category_id"], item["title"],
             item["body"], item["status"],
             item["priority"], user["id"], item["on_behalf_of"], item["assignee_id"],
             item["due_date"], item["occurred_at"], item["spent_hours"], item["resolution"],
             item["resolved_at"], item["created_at"], item["created_at"])))
    return json_response({"created": len(created_ids), "ticket_ids": created_ids,
                          "problems": problems}, 201)


@route("GET", r"/api/tickets/stats")
def ticket_stats(ctx):
    """日次・週次で、受けた数と片付いた数を数える。

    受付日は created_at、完了日は resolved_at で数える。
    件数が増えても重くならないよう、集計はすべて SQL 側でやる。
    """
    me(ctx)
    unit = "week" if (ctx.query.get("unit") or "day") == "week" else "day"
    span = as_int(ctx.query.get("span"), 14 if unit == "day" else 12, 1, 60)
    today = db.today()
    step = timedelta(weeks=1) if unit == "week" else timedelta(days=1)

    def snap(day):
        """週次なら、その日を含む週の月曜に寄せる。"""
        return day - timedelta(days=day.weekday()) if unit == "week" else day

    # end は表示する最後の区切り。さかのぼれるのは半年ぶんまで。
    earliest = snap(today - timedelta(days=TICKET_STATS_MAX_DAYS))
    asked = as_date(ctx.query.get("end"))
    end = snap(date.fromisoformat(asked) if asked else today)
    end = min(end, snap(today))
    if end - (span - 1) * step < earliest:
        end = min(snap(today), earliest + (span - 1) * step)
    starts = [end - i * step for i in range(span - 1, -1, -1)]
    since = starts[0]
    until = starts[-1] + step

    where, params = [], []
    queue_id = as_int(ctx.query.get("queue_id"))
    if queue_id:
        where.append("t.queue_id=%s")
        params.append(queue_id)
    project_id = as_int(ctx.query.get("project_id"))
    if project_id:
        where.append("q.project_id=%s")
        params.append(project_id)
    clause = (" AND " + " AND ".join(where)) if where else ""
    closed = tickets.CLOSED_STATUSES

    def bucket(column):
        """日次はその日、週次はその週の月曜にまとめる。"""
        if unit == "week":
            return "DATE(DATE_SUB({0}, INTERVAL WEEKDAY({0}) DAY))".format(column)
        return "DATE({})".format(column)

    frm = " FROM tickets t JOIN ticket_queues q ON q.id = t.queue_id"
    # 受けたぶんと片付いたぶんは期間の取り方が違うので、別々に数える
    made_rows = db.query(
        "SELECT {} AS k, COUNT(*) AS n".format(bucket("t.created_at")) + frm
        + " WHERE t.created_at >= %s AND t.created_at < %s" + clause + " GROUP BY k",
        tuple([since, until] + params))
    done_rows = db.query(
        "SELECT {} AS k, COUNT(*) AS n".format(bucket("t.resolved_at")) + frm
        + " WHERE t.resolved_at >= %s AND t.resolved_at < %s AND t.status IN %s"
        + clause + " GROUP BY k",
        tuple([since, until, closed] + params))
    made_by = {str(r["k"]): int(r["n"]) for r in made_rows}
    done_by = {str(r["k"]): int(r["n"]) for r in done_rows}
    series = [{
        "key": day.isoformat(),
        "label": "{}/{}".format(day.month, day.day),
        "created": made_by.get(day.isoformat(), 0),
        "resolved": done_by.get(day.isoformat(), 0),
    } for day in starts]

    # 内訳。受けたぶん・片付いたぶんを 1 回の GROUP BY で数える。
    touched = (" WHERE ((t.created_at >= %s AND t.created_at < %s) "
               "OR (t.resolved_at >= %s AND t.resolved_at < %s AND t.status IN %s))")
    window = [since, until, since, until, closed]
    counts = ("SUM(t.created_at >= %s AND t.created_at < %s) AS created, "
              "SUM(t.resolved_at >= %s AND t.resolved_at < %s AND t.status IN %s) AS resolved, "
              "SUM(CASE WHEN t.resolved_at >= %s AND t.resolved_at < %s AND t.status IN %s "
              "    THEN t.spent_hours END) AS spent")
    count_args = [since, until, since, until, closed, since, until, closed]

    def grouped(select, joins, group):
        return db.query(
            "SELECT " + select + ", " + counts + frm + joins + touched + clause
            + " GROUP BY " + group,
            tuple(count_args + window + params))

    def spent_of(row):
        return round(float(row["spent"]), 1) if row["spent"] is not None else None

    by_queue = [{"queue_id": r["queue_id"], "name": r["name"], "color": r["color"],
                 "created": int(r["created"] or 0), "resolved": int(r["resolved"] or 0),
                 "spent_hours": spent_of(r)}
                for r in grouped("q.id AS queue_id, q.name, q.color", "", "q.id")]
    by_kind = [{"kind": r["kind"],
                "label": tickets.KIND_LABEL.get(r["kind"], r["kind"]),
                "icon": tickets.KIND_ICON.get(r["kind"], ""),
                "created": int(r["created"] or 0), "resolved": int(r["resolved"] or 0),
                "spent_hours": spent_of(r)}
               for r in grouped("t.kind", "", "t.kind")]
    by_category = [{"category_id": r["category_id"],
                    "label": r["label"] or "分類なし", "color": r["color"] or "#98a2b3",
                    "created": int(r["created"] or 0), "resolved": int(r["resolved"] or 0),
                    "spent_hours": spent_of(r)}
                   for r in grouped(
                       "t.category_id, c.label, c.color",
                       " LEFT JOIN ticket_categories c ON c.id = t.category_id",
                       "t.category_id, c.label, c.color")]
    people = db.query(
        "SELECT t.assignee_id, u.name, u.avatar_color, " + counts + ", "
        "AVG(CASE WHEN t.resolved_at >= %s AND t.resolved_at < %s AND t.status IN %s "
        "    THEN TIMESTAMPDIFF(HOUR, t.created_at, t.resolved_at) END) AS avg_hours"
        + frm + " LEFT JOIN users u ON u.id = t.assignee_id" + touched + clause
        + " GROUP BY t.assignee_id, u.name, u.avatar_color",
        tuple(count_args + [since, until, closed] + window + params))
    by_assignee = sorted([{
        "user_id": r["assignee_id"], "name": r["name"] or "未割当",
        "avatar_color": r["avatar_color"] or "#98a2b3",
        "created": int(r["created"] or 0), "resolved": int(r["resolved"] or 0),
        "spent_hours": spent_of(r),
        "turnaround_days": (round(float(r["avg_hours"]) / 24, 1)
                            if r["avg_hours"] is not None else None),
    } for r in people], key=lambda p: (-p["resolved"], -p["created"]))

    overall = db.query_one(
        "SELECT AVG(TIMESTAMPDIFF(HOUR, t.created_at, t.resolved_at)) AS avg_hours, "
        "SUM(t.spent_hours) AS spent, COUNT(t.spent_hours) AS spent_rows"
        + frm + " WHERE t.resolved_at >= %s AND t.resolved_at < %s AND t.status IN %s"
        + clause, tuple([since, until, closed] + params)) or {}
    open_now = db.scalar(
        "SELECT COUNT(*) AS c" + frm + " WHERE t.status IN %s" + clause,
        tuple([tickets.OPEN_STATUSES] + params), default=0) or 0

    return json_response({
        "unit": unit,
        "span": span,
        "end": end.isoformat(),
        "from": since.isoformat(),
        "to": (until - timedelta(days=1)).isoformat(),
        "can_go_back": since > earliest,
        "can_go_forward": end < snap(today),
        "earliest": earliest.isoformat(),
        "buckets": series,
        "totals": {
            "created": sum(b["created"] for b in series),
            "resolved": sum(b["resolved"] for b in series),
            "open_now": open_now,
            "turnaround_days": (round(float(overall["avg_hours"]) / 24, 1)
                                if overall.get("avg_hours") is not None else None),
            "spent_hours": (round(float(overall["spent"]), 1)
                            if overall.get("spent") is not None else None),
            "spent_rows": int(overall.get("spent_rows") or 0),
        },
        "by_queue": sorted(by_queue, key=lambda q: -q["created"]),
        "by_kind": sorted(by_kind, key=lambda k: -k["created"]),
        "by_assignee": by_assignee,
        "by_category": sorted(by_category, key=lambda c: -c["created"]),
        "has_categories": any(c["category_id"] for c in by_category),
    })


@route("POST", r"/api/tickets")
def create_ticket(ctx):
    user = me(ctx)
    queue_id = as_int(ctx.body.get("queue_id"))
    if queue_id is None:
        raise bad_request("窓口を選んでください")
    queue = queue_or_404(queue_id)
    if not queue["is_active"]:
        raise bad_request("この窓口はいま受付を止めています")
    title = require(ctx.body, "title", "件名")
    assignee_id = as_int(ctx.body.get("assignee_id"))
    if assignee_id and not db.query_one(
            "SELECT 1 AS x FROM users WHERE id=%s AND is_active=1", (assignee_id,)):
        raise bad_request("担当者が見つかりません")
    now = db.now()
    ticket_id = db.insert(
        "INSERT INTO tickets(queue_id, kind, category_id, title, body, status, priority, "
        "requester_id, on_behalf_of, assignee_id, due_date, occurred_at, spent_hours, "
        "created_at, updated_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (queue_id, tickets.kind(ctx.body.get("kind"), queue["default_kind"]),
         ticket_category(queue_id, ctx.body.get("category_id")),
         title, ctx.body.get("body") or "",
         tickets.status(ctx.body.get("status")),
         as_int(ctx.body.get("priority"), 1, 0, 3), user["id"],
         (ctx.body.get("on_behalf_of") or "")[:120], assignee_id,
         as_date(ctx.body.get("due_date")), as_datetime(ctx.body.get("occurred_at")),
         as_hours(ctx.body.get("spent_hours")), now, now))
    if assignee_id and assignee_id != user["id"]:
        _notify_ticket_assignee(ticket_id, title, assignee_id, user)
    return json_response({"ticket": ticket_or_404(ticket_id)}, 201)


@route("GET", r"/api/tickets/(\d+)")
def get_ticket(ctx, ticket_id):
    user = me(ctx)
    ticket = ticket_or_404(ticket_id)
    linked_tasks = db.query(
        TASK_SELECT + " JOIN ticket_tasks tt ON tt.task_id = t.id WHERE tt.ticket_id=%s "
        "ORDER BY t.id", (ticket_id,))
    linked_issues = db.query(
        ISSUE_SELECT + " JOIN ticket_issues ti ON ti.issue_id = i.id WHERE ti.ticket_id=%s "
        "ORDER BY i.id", (ticket_id,))
    comments = db.query(
        "SELECT c.*, u.name AS user_name, u.avatar_color FROM comments c "
        "LEFT JOIN users u ON u.id = c.user_id WHERE c.ticket_id=%s "
        "ORDER BY c.created_at, c.id", (ticket_id,))
    attachments = db.query(
        "SELECT a.*, u.name AS uploaded_by_name FROM attachments a "
        "LEFT JOIN users u ON u.id = a.uploaded_by WHERE a.ticket_id=%s ORDER BY a.created_at",
        (ticket_id,))
    return json_response({
        "ticket": ticket, "tasks": linked_tasks, "issues": linked_issues,
        "comments": comments, "attachments": attachments,
        "queue_categories": db.query(
            "SELECT id, label, color FROM ticket_categories WHERE queue_id=%s "
            "ORDER BY sort_order, id", (ticket["queue_id"],)),
        "can_delete": can_drop_ticket(user, ticket),
    })


@route("PATCH", r"/api/tickets/(\d+)")
def update_ticket(ctx, ticket_id):
    user = me(ctx)
    current = ticket_or_404(ticket_id)
    body = ctx.body
    fields, params, notes = [], [], []

    if "title" in body:
        fields.append("title=%s")
        params.append(require(body, "title", "件名"))
    for key in ("body", "resolution"):
        if key in body:
            fields.append(key + "=%s")
            params.append(body[key] or "")
    if "on_behalf_of" in body:
        fields.append("on_behalf_of=%s")
        params.append((body["on_behalf_of"] or "")[:120])
    queue_id = current["queue_id"]
    if "queue_id" in body:
        queue_id = as_int(body["queue_id"])
        queue = queue_or_404(queue_id)
        if queue_id != current["queue_id"]:
            notes.append("窓口: {} → {}".format(current["queue_name"], queue["name"]))
            # 分類は窓口ごとなので、移すと前の分類は使えない
            if current["category_id"] and "category_id" not in body:
                fields.append("category_id=%s")
                params.append(None)
        fields.append("queue_id=%s")
        params.append(queue_id)
    if "category_id" in body:
        category_id = ticket_category(queue_id, body["category_id"])
        if category_id != current["category_id"]:
            notes.append("分類: {} → {}".format(
                current["category_label"] or "なし",
                db.scalar("SELECT label AS n FROM ticket_categories WHERE id=%s",
                          (category_id,), default="なし") or "なし"))
        fields.append("category_id=%s")
        params.append(category_id)
    if "kind" in body:
        kind = tickets.kind(body["kind"], current["kind"])
        if kind != current["kind"]:
            notes.append("種別: {} → {}".format(
                tickets.KIND_LABEL.get(current["kind"], "-"),
                tickets.KIND_LABEL.get(kind, "-")))
        fields.append("kind=%s")
        params.append(kind)
    if "priority" in body:
        priority = as_int(body["priority"], current["priority"], 0, 3)
        if priority != current["priority"]:
            notes.append("優先度: {} → {}".format(
                tickets.PRIORITY_LABEL.get(current["priority"], "-"),
                tickets.PRIORITY_LABEL.get(priority, "-")))
        fields.append("priority=%s")
        params.append(priority)
    if "status" in body:
        status = tickets.status(body["status"], current["status"])
        if status != current["status"]:
            notes.append("状態: {} → {}".format(
                tickets.STATUS_LABEL.get(current["status"], "-"),
                tickets.STATUS_LABEL.get(status, "-")))
            # 完了・取り下げにしたら対応日時を入れ、戻したら消す
            if status in tickets.CLOSED_STATUSES and not current["resolved_at"]:
                fields.append("resolved_at=%s")
                params.append(db.now())
            elif tickets.is_open(status) and current["resolved_at"]:
                fields.append("resolved_at=%s")
                params.append(None)
        fields.append("status=%s")
        params.append(status)

    new_assignee = current["assignee_id"]
    if "assignee_id" in body:
        new_assignee = as_int(body["assignee_id"])
        if new_assignee and not db.query_one(
                "SELECT 1 AS x FROM users WHERE id=%s AND is_active=1", (new_assignee,)):
            raise bad_request("担当者が見つかりません")
        if new_assignee != current["assignee_id"]:
            notes.append("担当: {} → {}".format(
                current["assignee_name"] or "未割当",
                db.scalar("SELECT name AS n FROM users WHERE id=%s",
                          (new_assignee,), default="未割当") or "未割当"))
        fields.append("assignee_id=%s")
        params.append(new_assignee)

    if "due_date" in body:
        value = as_date(body["due_date"])
        old = current["due_date"].isoformat() if current["due_date"] else None
        if (value or None) != old:
            notes.append("期限: {} → {}".format(old or "未設定", value or "未設定"))
        fields.append("due_date=%s")
        params.append(value)
    if "occurred_at" in body:
        fields.append("occurred_at=%s")
        params.append(as_datetime(body["occurred_at"]))
    if "spent_hours" in body:
        hours = as_hours(body["spent_hours"])
        old_hours = float(current["spent_hours"]) if current["spent_hours"] else None
        if hours != old_hours:
            notes.append("対応時間: {} → {}".format(
                "未入力" if old_hours is None else "{}h".format(old_hours),
                "未入力" if hours is None else "{}h".format(hours)))
        fields.append("spent_hours=%s")
        params.append(hours)

    if not fields:
        raise bad_request("更新する項目がありません")
    fields.append("updated_at=%s")
    params.append(db.now())
    params.append(ticket_id)
    db.execute("UPDATE tickets SET {} WHERE id=%s".format(", ".join(fields)), params)

    if notes:
        ticket_note(ticket_id, user["id"], " / ".join(notes))
    if new_assignee and new_assignee != current["assignee_id"] and new_assignee != user["id"]:
        _notify_ticket_assignee(ticket_id, current["title"], new_assignee, user)
    return json_response({"ticket": ticket_or_404(ticket_id)})


@route("DELETE", r"/api/tickets/(\d+)")
def delete_ticket(ctx, ticket_id):
    user = me(ctx)
    ticket = ticket_or_404(ticket_id)
    if not can_drop_ticket(user, ticket):
        raise forbidden("削除できるのは起票した本人か管理者だけです")
    stored = db.query(
        "SELECT stored_name FROM attachments WHERE ticket_id=%s AND kind='file'", (ticket_id,))
    db.execute("DELETE FROM tickets WHERE id=%s", (ticket_id,))
    for row in stored:
        _remove_stored_file(row["stored_name"])
    return json_response({"ok": True})


def ticket_note(ticket_id, user_id, text):
    """やりとりの経緯が残るよう、変更はコメント欄に書き足す。"""
    db.execute(
        "INSERT INTO comments(ticket_id, user_id, body, kind, created_at) "
        "VALUES(%s,%s,%s,'system',%s)", (ticket_id, user_id, text, db.now()))


def ticket_url(ticket_id):
    base = db.get_setting("app_base_url", "").rstrip("/")
    return "{}/#/ticket/{}".format(base, ticket_id) if base else ""


def _notify_ticket_assignee(ticket_id, title, assignee_id, actor):
    notify.create(
        assignee_id, "assigned", "チケットの担当になりました: {}".format(title),
        "担当に設定: {}\n{}".format(actor["name"], ticket_url(ticket_id)),
        ticket_id=ticket_id)


@route("POST", r"/api/tickets/(\d+)/comments")
def add_ticket_comment(ctx, ticket_id):
    user = me(ctx)
    ticket = ticket_or_404(ticket_id)
    body = require(ctx.body, "body", "コメント")
    comment_id = db.insert(
        "INSERT INTO comments(ticket_id, user_id, body, kind, created_at) "
        "VALUES(%s,%s,%s,'comment',%s)", (ticket_id, user["id"], body, db.now()))
    db.execute("UPDATE tickets SET updated_at=%s WHERE id=%s", (db.now(), ticket_id))
    notified = _notify_ticket_comment(ticket, user, body)
    row = db.query_one(
        "SELECT c.*, u.name AS user_name, u.avatar_color FROM comments c "
        "LEFT JOIN users u ON u.id = c.user_id WHERE c.id=%s", (comment_id,))
    return json_response({"comment": row, "mentioned": notified}, 201)


def _notify_ticket_comment(ticket, actor, body):
    recipients = set()
    for key in ("assignee_id", "requester_id"):
        if ticket[key]:
            recipients.add(ticket[key])
    for row in db.query("SELECT DISTINCT user_id FROM comments WHERE ticket_id=%s "
                        "AND kind='comment' AND user_id IS NOT NULL", (ticket["id"],)):
        recipients.add(row["user_id"])
    # チケットは全員が見られるので、呼べる相手も全員
    everyone = db.query("SELECT id, name, email FROM users WHERE is_active=1")
    mentioned, _labels = mentions.find(body, everyone)
    mentioned_ids = {m["id"] for m in mentioned} - {actor["id"]}
    recipients.discard(actor["id"])
    recipients -= mentioned_ids
    link = ticket_url(ticket["id"])
    excerpt = body if len(body) <= 300 else body[:300] + "…"
    for user_id in mentioned_ids:
        notify.create(user_id, "mention", "{} さんがあなたを呼んでいます: {}".format(
            actor["name"], ticket["title"]), "{}\n\n{}".format(excerpt, link),
            ticket_id=ticket["id"])
    for user_id in recipients:
        notify.create(user_id, "comment", "チケットのコメント: {}".format(ticket["title"]),
                      "{} さんのコメント\n\n{}\n\n{}".format(actor["name"], excerpt, link),
                      ticket_id=ticket["id"])
    return [m["name"] for m in mentioned if m["id"] in mentioned_ids]


@route("POST", r"/api/tickets/(\d+)/attachments")
def add_ticket_attachment(ctx, ticket_id):
    user = me(ctx)
    ticket_or_404(ticket_id)
    return _store_attachments(ctx, user, {"ticket_id": ticket_id})


@route("PUT", r"/api/tickets/(\d+)/tasks")
def link_ticket_tasks(ctx, ticket_id):
    """すでにあるタスクをチケットに結びつける。"""
    user = me(ctx)
    ticket_or_404(ticket_id)
    wanted = [i for i in (as_int(v) for v in (ctx.body.get("task_ids") or [])) if i]
    allowed = []
    if wanted:
        visible = auth.visible_project_ids(user)
        rows = db.query(
            "SELECT id FROM tasks WHERE id IN %s AND project_id IN %s",
            (tuple(set(wanted)), tuple(visible))) if visible else []
        allowed = [row["id"] for row in rows]
    before = {r["task_id"] for r in db.query(
        "SELECT task_id FROM ticket_tasks WHERE ticket_id=%s", (ticket_id,))}
    db.execute("DELETE FROM ticket_tasks WHERE ticket_id=%s", (ticket_id,))
    for task_id in allowed:
        db.execute("INSERT IGNORE INTO ticket_tasks(ticket_id, task_id) VALUES(%s,%s)",
                   (ticket_id, task_id))
    if set(allowed) != before:
        ticket_note(ticket_id, user["id"],
                    "関連タスク: {} 件 → {} 件".format(len(before), len(allowed)))
        db.execute("UPDATE tickets SET updated_at=%s WHERE id=%s", (db.now(), ticket_id))
    return json_response({"task_ids": allowed})


@route("POST", r"/api/tickets/(\d+)/task")
def create_task_from_ticket(ctx, ticket_id):
    """チケットの内容でタスクを起票し、そのまま結びつける。"""
    user = me(ctx)
    ticket = ticket_or_404(ticket_id)
    project_id = as_int(ctx.body.get("project_id")) or ticket["queue_project_id"]
    if project_id is None:
        raise bad_request("登録先のプロジェクトを選んでください")
    project_or_404(user, project_id, "editor")

    payload = {
        "project_id": project_id,
        "title": (ctx.body.get("title") or ticket["title"])[:300],
        "description": ctx.body.get("description", _task_description_from(ticket)),
        "category": ctx.body.get("category", ""),
        "priority": as_int(ctx.body.get("priority"), ticket["priority"], 0, 3),
        "assignee_id": ctx.body.get("assignee_id", ticket["assignee_id"]),
        "due_date": ctx.body.get("due_date",
                                 ticket["due_date"].isoformat() if ticket["due_date"] else None),
    }
    if "parent_id" in ctx.body:
        payload["parent_id"] = ctx.body["parent_id"]
    task = _create_task(user, payload)
    db.execute("INSERT IGNORE INTO ticket_tasks(ticket_id, task_id) VALUES(%s,%s)",
               (ticket_id, task["id"]))
    # 受け付けたまま放置に見えないよう、対応中へ進めておく
    if ticket["status"] == "new":
        db.execute("UPDATE tickets SET status='doing' WHERE id=%s", (ticket_id,))
    ticket_note(ticket_id, user["id"],
                "タスクを作成: {}（{}）".format(task["title"], task["project_name"]))
    db.execute("UPDATE tickets SET updated_at=%s WHERE id=%s", (db.now(), ticket_id))
    return json_response({"task": task, "ticket": ticket_or_404(ticket_id)}, 201)


def _task_description_from(ticket):
    parts = ["チケット #{} から作成".format(ticket["id"])]
    if ticket["on_behalf_of"]:
        parts.append("依頼元: {}".format(ticket["on_behalf_of"]))
    if ticket["body"]:
        parts.append("")
        parts.append(ticket["body"])
    return "\n".join(parts)


@route("POST", r"/api/tickets/(\d+)/issue")
def create_issue_from_ticket(ctx, ticket_id):
    """作業ではなく論点だったときに、プロジェクトの課題管理表へ移す。"""
    user = me(ctx)
    ticket = ticket_or_404(ticket_id)
    project_id = as_int(ctx.body.get("project_id")) or ticket["queue_project_id"]
    if project_id is None:
        raise bad_request("登録先のプロジェクトを選んでください")
    project_or_404(user, project_id, "editor")
    title = (ctx.body.get("title") or ticket["title"])[:300]
    now = db.now()
    seq = next_issue_seq(project_id)
    issue_id = db.insert(
        "INSERT INTO issues(project_id, seq, title, description, category, status, severity, "
        "owner_id, raised_by, raised_on, due_date, created_at, updated_at) "
        "VALUES(%s,%s,%s,%s,%s,'open',%s,%s,%s,%s,%s,%s,%s)",
        (project_id, seq, title, _task_description_from(ticket),
         normalize_issue_category(ctx.body.get("category"), "other"),
         as_int(ctx.body.get("severity"), min(ticket["priority"], 3), 0, 3),
         ticket["assignee_id"] if ctx.body.get("owner_id") is None
         else as_int(ctx.body.get("owner_id")),
         user["id"], db.today(),
         ticket["due_date"], now, now))
    db.execute("INSERT IGNORE INTO ticket_issues(ticket_id, issue_id) VALUES(%s,%s)",
               (ticket_id, issue_id))
    if ticket["status"] == "new":
        db.execute("UPDATE tickets SET status='doing' WHERE id=%s", (ticket_id,))
    issue = db.query_one(ISSUE_SELECT + " WHERE i.id=%s", (issue_id,))
    ticket_note(ticket_id, user["id"],
                "課題に登録: #{} {}（{}）".format(seq, title, issue["project_name"]))
    db.execute("UPDATE tickets SET updated_at=%s WHERE id=%s", (db.now(), ticket_id))
    return json_response({"issue": issue, "ticket": ticket_or_404(ticket_id)}, 201)


# --------------------------------------------------------------------------
# 自然言語入力とタスク分解
# --------------------------------------------------------------------------

def _nl_context(user):
    """解析に渡す担当者候補とプロジェクト候補。"""
    users = db.query("SELECT id, name FROM users WHERE is_active=1")
    ids = auth.visible_project_ids(user)
    projects = db.query(
        "SELECT id, name FROM projects WHERE id IN %s AND archived=0", (tuple(ids),)
    ) if ids else []
    return users, projects


@route("POST", r"/api/nl/parse")
def nl_parse(ctx):
    """一文からタスクの下書きを作る。DB には書き込まない。"""
    user = me(ctx)
    text = require(ctx.body, "text", "入力")
    users, projects = _nl_context(user)
    default_project = as_int(ctx.body.get("project_id"))

    draft, warning = None, None
    if llm.available() and not as_bool(ctx.body.get("force_rule")):
        try:
            draft = llm.parse(text, users=users, projects=projects)
        except llm.LlmError as error:
            warning = "{}（簡易解析で代替しました）".format(error)
        except Exception as error:  # noqa: BLE001 - 解析失敗で登録を止めない
            warning = "解析に失敗しました（簡易解析で代替しました）"
            log_llm_failure(error)
    if draft is None:
        draft = nlp.parse(text, users=users, projects=projects,
                          default_project_id=default_project)
    if not draft.get("project_id"):
        draft["project_id"] = default_project or _default_project_id(user)
    if not draft.get("title"):
        raise bad_request("タスク名を読み取れませんでした")

    # 権限のないプロジェクトを指してしまった場合は既定に戻す
    if draft["project_id"] and not auth.has_project_access(user, draft["project_id"], "editor"):
        draft["project_id"] = _default_project_id(user)
    return json_response({"draft": draft, "warning": warning, "source": text})


def log_llm_failure(error):
    import logging
    logging.getLogger("tm.api").warning("LLM parse failed: %s", error)


def _default_project_id(user):
    """編集できるプロジェクトのうち、直近に更新されたもの。"""
    ids = auth.visible_project_ids(user)
    for row in db.query(
            "SELECT p.id FROM projects p WHERE p.id IN %s AND p.archived=0 "
            "ORDER BY (SELECT MAX(t.updated_at) FROM tasks t WHERE t.project_id=p.id) DESC, "
            "p.id DESC", (tuple(ids),)) if ids else []:
        if auth.has_project_access(user, row["id"], "editor"):
            return row["id"]
    return None


@route("POST", r"/api/nl/decompose")
def nl_decompose(ctx):
    """大きなタスクを子タスク候補に分解する。DB には書き込まない。"""
    user = me(ctx)
    title = require(ctx.body, "title", "タスク名")
    project_id = as_int(ctx.body.get("project_id"))
    if project_id:
        project_or_404(user, project_id, "editor")
    start_date = as_date(ctx.body.get("start_date"))
    due_date = as_date(ctx.body.get("due_date"))
    description = ctx.body.get("description", "")

    warning, result = None, None
    if llm.available() and not as_bool(ctx.body.get("force_rule")):
        try:
            steps = llm.decompose(title, description, start_date, due_date)
            if steps:
                result = _steps_to_items(steps, start_date, due_date)
                result.update(template="Claude", matched=True, engine="llm")
        except llm.LlmError as error:
            warning = "{}（定型テンプレートで代替しました）".format(error)
        except Exception as error:  # noqa: BLE001
            warning = "分解に失敗しました（定型テンプレートで代替しました）"
            log_llm_failure(error)
    if result is None:
        result = nlp.decompose(title, start_date, due_date,
                               normalize_category(ctx.body.get("category"), ""))
    result["warning"] = warning
    return json_response(result)


def _steps_to_items(steps, start_date, due_date):
    """(名前, 区分, 重み) の並びに日程を按分して子タスク候補にする。"""
    spread = nlp._spread(steps, nlp._parse_iso(start_date), nlp._parse_iso(due_date))
    items = []
    for index, ((title, category, _weight), dates) in enumerate(zip(steps, spread)):
        items.append({
            "title": title,
            "category": category if category in taxonomy.category_values() else "",
            "start_date": dates[0].isoformat() if dates[0] else None,
            "due_date": dates[1].isoformat() if dates[1] else None,
            "sort_order": (index + 1) * 10,
        })
    return {"items": items}


@route("POST", r"/api/tasks/(\d+)/subtasks")
def create_subtasks(ctx, task_id):
    """提案された子タスクをまとめて登録する。"""
    user = me(ctx)
    parent = task_or_404(user, task_id, "editor")
    items = ctx.body.get("items")
    if not isinstance(items, list) or not items:
        raise bad_request("追加する子タスクがありません")
    if task_depth(task_id) >= MAX_TASK_DEPTH:
        raise bad_request("階層が深すぎます（最大 {} 階層）".format(MAX_TASK_DEPTH))

    base_order = (db.scalar(
        "SELECT COALESCE(MAX(sort_order), 0) AS m FROM tasks WHERE parent_id=%s",
        (task_id,), default=0) or parent["sort_order"]) or 0
    now = db.now()
    created = []
    for index, item in enumerate(items):
        title = (item.get("title") or "").strip()
        if not title:
            continue
        created.append(db.insert(
            "INSERT INTO tasks(project_id, parent_id, title, description, category, status, "
            "priority, assignee_id, start_date, due_date, progress, is_milestone, sort_order, "
            "created_by, created_at, updated_at) "
            "VALUES(%s,%s,%s,%s,%s,'todo',%s,%s,%s,%s,0,0,%s,%s,%s,%s)",
            (parent["project_id"], task_id, title, item.get("description", ""),
             normalize_category(item.get("category"), ""),
             as_int(item.get("priority"), parent["priority"], 0, 3),
             as_int(item.get("assignee_id")) or parent["assignee_id"],
             as_date(item.get("start_date")), as_date(item.get("due_date")),
             base_order + (index + 1) * 10, user["id"], now, now)))
    if not created:
        raise bad_request("追加する子タスクがありません")
    system_comment(task_id, user["id"], "子タスクを {} 件追加".format(len(created)))
    touch_task(task_id)
    rows = db.query(TASK_SELECT + " WHERE t.id IN %s ORDER BY t.sort_order", (tuple(created),))
    return json_response({"tasks": rows, "created": len(created)}, 201)


# --------------------------------------------------------------------------
# 会議メモからの一括起票と、進行レビュー（どちらも人が押したときだけ呼ぶ）
# --------------------------------------------------------------------------

MEMO_BULLET = re.compile(r"^\s*(?:[-*・･>＞\u25a0\u25cf\u25c6]|\d+[.)、]|[(\uff08]\d+[)\uff09])\s*")


HONORIFIC = re.compile(r"(さん|サン|氏|様|君|くん|ちゃん|先生|部長|課長|主任)$")


def _match_member(written, members):
    """メモの「鈴木さん」を、一覧の「鈴木 一郎」に結びつける。

    Claude にはメモの呼び方をそのまま書かせて、突き合わせはこちらでやる。
    敬称を外し、完全一致 → 姓または名の一致 → 部分一致 の順に探す。
    候補が 2 人以上いるときは、取り違えるより空欄のままにする。
    """
    name = HONORIFIC.sub("", (written or "").strip()).replace("\u3000", " ").strip()
    if not name:
        return ""
    flat = name.replace(" ", "")
    exact = [m["name"] for m in members if m["name"].replace(" ", "") == flat]
    if exact:
        return exact[0]
    parts = []
    for member in members:
        pieces = [p for p in member["name"].replace("\u3000", " ").split(" ") if p]
        if flat in pieces:
            parts.append(member["name"])
    if len(parts) == 1:
        return parts[0]
    loose = [m["name"] for m in members if flat and flat in m["name"].replace(" ", "")]
    return loose[0] if len(loose) == 1 else ""


def _extract_by_rule(text, users, projects):
    """Claude が使えないときの代替。1 行 1 タスクとみなして拾う。"""
    rows = []
    seen = set()
    for raw in text.splitlines():
        line = MEMO_BULLET.sub("", raw).strip()
        if len(line) < 4 or line.startswith("#"):
            continue
        draft = nlp.parse(line, users=users, projects=projects)
        title = (draft.get("title") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        name = next((u["name"] for u in users if u["id"] == draft.get("assignee_id")), "")
        rows.append({
            "title": title,
            "assignee": name,
            "due_date": draft.get("due_date") or "",
            "category": draft.get("category") or "",
            "priority": int(draft.get("priority") or 1),
            "description": "",
            "source": line,
        })
    return rows


@route("POST", r"/api/nl/extract")
def nl_extract(ctx):
    """会議メモから、登録できる形のタスク候補をまとめて取り出す。DB には書き込まない。"""
    user = me(ctx)
    text = require(ctx.body, "text", "メモ")
    if len(text) > 20000:
        raise bad_request("メモが長すぎます（2 万文字まで）")
    project_id = as_int(ctx.body.get("project_id"))
    if project_id:
        project_or_404(user, project_id, "editor")

    users, projects = _nl_context(user)
    if project_id:
        # 宛先が決まっているなら、そのプロジェクトに関わる人だけを候補にする
        users = assignable_users(project_id) or users

    rows, engine, warning = None, "llm", None
    if llm.available() and not as_bool(ctx.body.get("force_rule")):
        try:
            rows = llm.extract(text, users=users, projects=projects)
        except llm.LlmError as error:
            warning = "{}（簡易読み取りで代替しました）".format(error)
        except Exception as error:  # noqa: BLE001 - 読み取り失敗で操作を止めない
            warning = "読み取りに失敗しました（簡易読み取りで代替しました）"
            log_llm_failure(error)
    if rows is None:
        rows = _extract_by_rule(text, users, projects)
        engine = "rule"
    for row in rows:
        row["assignee"] = _match_member(row.get("assignee"), users)
    return json_response({"rows": rows[:200], "engine": engine, "warning": warning})


REVIEW_STALE_DAYS = 14
# 集計をさかのぼれる範囲。半年より前は見ない。
TICKET_STATS_MAX_DAYS = 186


def _review_context(project, tasks, deps, analysis, load):
    """AI に渡す「今どうなっているか」を、数字のまま文章にまとめる。"""
    today = db.today()
    by_id = {t["id"]: t for t in tasks}
    metrics = analysis["metrics"]

    def line(task, extra=""):
        due = task.get("due_date")
        late = ""
        if due and str(due) < today.isoformat() and task["status"] != "done":
            late = "（{}日超過）".format((today - date.fromisoformat(str(due)[:10])).days)
        return "  #{} {} / 担当:{} / 状態:{} / 期限:{}{} / 進捗:{}%{}".format(
            task["id"], task["title"], task.get("assignee_name") or "未割当",
            status_label(task["status"]), due or "未設定", late,
            task.get("progress") or 0, extra)

    parts = ["プロジェクト: {}".format(project["name"]),
             "今日の日付: {}".format(today.isoformat()),
             "未完了 {} 件 / 全 {} 件".format(
                 sum(1 for t in tasks if t["status"] != "done"), len(tasks))]

    overdue = [t for t in tasks if t["status"] != "done" and t.get("due_date")
               and str(t["due_date"]) < today.isoformat()]
    overdue.sort(key=lambda t: str(t["due_date"]))
    if overdue:
        parts.append("\n[期限を過ぎているもの]")
        parts += [line(t) for t in overdue[:15]]

    if analysis.get("critical_path"):
        parts.append("\n[クリティカルパス（この並びが全体の長さを決めている）]")
        parts += ["  #{} {}".format(i, by_id[i]["title"])
                  for i in analysis["critical_path"] if i in by_id]

    blocking = [t for t in tasks if t["status"] != "done"
                and metrics.get(t["id"], {}).get("blocks_open")]
    blocking.sort(key=lambda t: -metrics[t["id"]]["blocks_open"])
    if blocking:
        parts.append("\n[他の作業を止めているもの]")
        parts += [line(t, " / 後続{}件が待機".format(metrics[t["id"]]["blocks_open"]))
                  for t in blocking[:10]]

    if analysis.get("conflicts"):
        parts.append("\n[前後関係と日程が矛盾しているところ]")
        for c in analysis["conflicts"][:8]:
            parts.append("  「{}」の期限が「{}」の開始より {} 日あと".format(
                c.get("depends_on_title"), c.get("task_title"), c.get("overlap_days")))

    stale_before = (today - timedelta(days=REVIEW_STALE_DAYS)).isoformat()
    stale = [t for t in tasks if t["status"] not in ("done",)
             and str(t.get("updated_at") or "")[:10] < stale_before
             and t.get("progress", 0) < 100]
    if stale:
        parts.append("\n[{} 日以上動いていない未完了]".format(REVIEW_STALE_DAYS))
        parts += [line(t, " / 最終更新:{}".format(str(t.get("updated_at"))[:10]))
                  for t in stale[:10]]

    rows = [r for r in load.get("rows", []) if r.get("user_id")]
    if rows:
        parts.append("\n[担当者ごとの週あたりの負荷（時間 / 使える時間）]")
        for row in rows[:8]:
            cells = ["{}:{}h/{}h".format(w["label"], c["hours"], c["capacity"])
                     for w, c in zip(load["weeks"], row["cells"])][:4]
            parts.append("  {} → {}".format(row["name"], "、".join(cells)))
        if load.get("missing_estimate"):
            parts.append("  ※ 見積が入っていない未完了が {} 件あるため、負荷は実際より軽く出ている".format(
                load["missing_estimate"]))

    milestones = [t for t in tasks if t.get("is_milestone") and t["status"] != "done"]
    milestones.sort(key=lambda t: str(t.get("due_date") or "9999"))
    if milestones:
        parts.append("\n[これから来るマイルストーン]")
        parts += [line(t) for t in milestones[:6]]

    return "\n".join(parts)


@route("POST", r"/api/projects/(\d+)/review")
def project_review(ctx, project_id):
    """いまの進み具合を Claude に見てもらう。ボタンを押したときだけ呼ぶ。"""
    user = me(ctx)
    project = project_or_404(user, project_id)
    tasks = db.query(
        "SELECT t.id, t.title, t.status, t.progress, t.start_date, t.due_date, "
        "t.is_milestone, t.updated_at, t.assignee_id, t.estimate_hours, t.actual_hours, "
        "t.project_id, u.name AS assignee_name "
        "FROM tasks t LEFT JOIN users u ON u.id = t.assignee_id WHERE t.project_id=%s",
        (project_id,))
    if not tasks:
        raise bad_request("タスクがまだないので、見てもらえることがありません")
    if not llm.available():
        raise bad_request(
            "Claude 連携が有効になっていません（システム設定から有効にしてください）")
    deps = project_deps(project_id)
    analysis = graph.bottlenecks(tasks, deps)
    members = db.query("SELECT id, name, avatar_color FROM users WHERE is_active=1")
    hours_per_day = float(db.get_setting("work_hours_per_day", "8") or 8)
    off_days = ()
    if db.get_setting("use_holidays", "1") == "1":
        today = db.today()
        off_days = set(holidays.holidays_between(
            today - timedelta(days=14), today + timedelta(weeks=10)))
    load = workload.build(tasks, members, weeks=6, hours_per_day=hours_per_day,
                          holidays=off_days)

    try:
        data = llm.review(_review_context(project, tasks, deps, analysis, load))
    except llm.LlmError as error:
        raise bad_request(str(error))
    except Exception as error:  # noqa: BLE001 - 画面に出して終わる
        log_llm_failure(error)
        raise bad_request("レビューの生成に失敗しました")

    known = {t["id"]: t["title"] for t in tasks}
    risks = []
    for risk in data.get("risks", []):
        ids = [i for i in risk.get("task_ids", []) if i in known]
        risks.append({"title": risk.get("title", ""), "detail": risk.get("detail", ""),
                      "action": risk.get("action", ""), "level": risk.get("level", "中"),
                      "tasks": [{"id": i, "title": known[i]} for i in ids[:6]]})
    focus = [{"id": f["task_id"], "title": known[f["task_id"]], "why": f.get("why", "")}
             for f in data.get("focus", []) if f.get("task_id") in known]
    return json_response({
        "headline": data.get("headline", ""),
        "risks": risks[:5],
        "focus": focus[:3],
        "generated_at": db.now().isoformat(sep=" ", timespec="seconds"),
        "model": llm.settings()["model"],
    })


@route("POST", r"/api/settings/test-llm")
def test_llm(ctx):
    admin_only(ctx)
    ok, message = llm.check()
    return json_response({"ok": ok, "message": message})


# --------------------------------------------------------------------------
# 負荷ビューと工数
# --------------------------------------------------------------------------

@route("GET", r"/api/workload")
def workload_view(ctx):
    """担当者ごとの週別負荷。project_id を省くと参加中の全プロジェクトが対象。"""
    user = me(ctx)
    project_id = as_int(ctx.query.get("project_id"))
    if project_id:
        project_or_404(user, project_id)
        project_ids = [project_id]
    else:
        project_ids = auth.visible_project_ids(user)
    if not project_ids:
        return json_response({"weeks": [], "rows": [], "unscheduled": [],
                              "effort": workload.effort_summary([]), "projects": []})

    tasks = db.query(
        "SELECT t.id, t.title, t.status, t.assignee_id, t.start_date, t.due_date, t.progress, "
        "t.estimate_hours, t.actual_hours, t.project_id "
        "FROM tasks t JOIN projects p ON p.id = t.project_id "
        "WHERE t.project_id IN %s AND p.archived = 0", (tuple(project_ids),))
    users = db.query("SELECT id, name, avatar_color FROM users WHERE is_active=1")
    weeks = as_int(ctx.query.get("weeks"), 8, 2, 26)
    hours_per_day = float(db.get_setting("work_hours_per_day", "8") or 8)
    off_days = ()
    if db.get_setting("use_holidays", "1") == "1":
        today = db.today()
        off_days = set(holidays.holidays_between(
            today - timedelta(days=14), today + timedelta(weeks=weeks + 2)))

    result = workload.build(tasks, users, weeks=weeks, hours_per_day=hours_per_day,
                            holidays=off_days)
    result["effort"] = workload.effort_summary(tasks)
    result["projects"] = db.query(
        "SELECT id, name FROM projects WHERE id IN %s AND archived=0 ORDER BY name",
        (tuple(project_ids),))
    result["project_id"] = project_id
    return json_response(result)


# --------------------------------------------------------------------------
# 繰り返し（定例タスク）
# --------------------------------------------------------------------------

RECURRENCE_SELECT = """
    SELECT r.*, u.name AS assignee_name, p.name AS project_name,
           pt.title AS parent_title
      FROM recurrences r
      LEFT JOIN users u ON u.id = r.assignee_id
      LEFT JOIN tasks pt ON pt.id = r.parent_id
      JOIN projects p ON p.id = r.project_id
"""


def pick_id(body, current, key):
    """未指定なら今の値を残し、null が来たら「外す」と解釈する。

    as_int(None, 既定値) だと既定値に戻ってしまい、担当者や親タスクを
    外せなくなるため、キーの有無で判断する。
    """
    if key in body:
        return as_int(body[key])
    return current[key] if current else None


def _recurrence_body(ctx, current=None):
    body = ctx.body
    freq = body.get("freq", current["freq"] if current else "weekly")
    if freq not in ("daily", "weekly", "monthly"):
        raise bad_request("繰り返しの種類が不正です")
    weekdays = body.get("weekdays", current["weekdays"] if current else "")
    if isinstance(weekdays, list):
        weekdays = ",".join(str(as_int(d, 0, 0, 6)) for d in weekdays)
    weekdays = ",".join(str(d) for d in recurrence.parse_weekdays(weekdays))
    if freq == "weekly" and not weekdays:
        raise bad_request("曜日を1つ以上選んでください")
    month_day = as_int(body.get("month_day"), current["month_day"] if current else None, 1, 31)
    if freq == "monthly" and not month_day:
        raise bad_request("何日に作るかを指定してください")
    next_on = as_date(body.get("next_on")) or (
        current["next_on"].isoformat() if current else None)
    if not next_on:
        raise bad_request("次回の期限日を指定してください")
    return {
        "title": require(body, "title", "タスク名") if "title" in body or not current
        else current["title"],
        "description": body.get("description", current["description"] if current else "") or "",
        "category": normalize_category(body.get("category"),
                                       current["category"] if current else ""),
        "priority": as_int(body.get("priority"), current["priority"] if current else 1, 0, 3),
        "assignee_id": pick_id(body, current, "assignee_id"),
        "estimate_hours": as_hours(body.get("estimate_hours")) if "estimate_hours" in body
        else (current["estimate_hours"] if current else None),
        "parent_id": pick_id(body, current, "parent_id"),
        "freq": freq,
        "interval_n": as_int(body.get("interval_n"),
                             current["interval_n"] if current else 1, 1, 99),
        "weekdays": weekdays,
        "month_day": month_day,
        "lead_days": as_int(body.get("lead_days"),
                            current["lead_days"] if current else 3, 0, 60),
        "next_on": next_on,
        "active": 1 if as_bool(body.get("active", True)) else 0,
    }


@route("GET", r"/api/projects/(\d+)/recurrences")
def list_recurrences(ctx, project_id):
    user = me(ctx)
    project_or_404(user, project_id)
    rows = db.query(RECURRENCE_SELECT + " WHERE r.project_id=%s ORDER BY r.active DESC, r.next_on",
                    (project_id,))
    for row in rows:
        row["summary"] = recurrence.describe(row)
    return json_response({"recurrences": rows})


@route("POST", r"/api/projects/(\d+)/recurrences")
def create_recurrence(ctx, project_id):
    user = me(ctx)
    project_or_404(user, project_id, "editor")
    values = _recurrence_body(ctx)
    ensure_member(values["assignee_id"], project_id)
    if values["parent_id"] and auth.task_project_id(values["parent_id"]) != project_id:
        raise bad_request("親タスクが同じプロジェクトにありません")
    now = db.now()
    rule_id = db.insert(
        "INSERT INTO recurrences(project_id, title, description, category, priority, assignee_id, "
        "estimate_hours, parent_id, freq, interval_n, weekdays, month_day, lead_days, next_on, "
        "active, created_by, created_at, updated_at) "
        "VALUES(%(project_id)s,%(title)s,%(description)s,%(category)s,%(priority)s,%(assignee_id)s,"
        "%(estimate_hours)s,%(parent_id)s,%(freq)s,%(interval_n)s,%(weekdays)s,%(month_day)s,"
        "%(lead_days)s,%(next_on)s,%(active)s,%(created_by)s,%(now)s,%(now)s)",
        dict(values, project_id=project_id, created_by=user["id"], now=now))
    row = db.query_one(RECURRENCE_SELECT + " WHERE r.id=%s", (rule_id,))
    row["summary"] = recurrence.describe(row)
    return json_response({"recurrence": row}, 201)


@route("PATCH", r"/api/recurrences/(\d+)")
def update_recurrence(ctx, rule_id):
    user = me(ctx)
    current = db.query_one("SELECT * FROM recurrences WHERE id=%s", (rule_id,))
    if not current:
        raise not_found("繰り返し設定が見つかりません")
    project_or_404(user, current["project_id"], "editor")
    values = _recurrence_body(ctx, current)
    if values["parent_id"] and auth.task_project_id(values["parent_id"]) != current["project_id"]:
        raise bad_request("親タスクが同じプロジェクトにありません")
    db.execute(
        "UPDATE recurrences SET title=%(title)s, description=%(description)s, "
        "category=%(category)s, priority=%(priority)s, assignee_id=%(assignee_id)s, "
        "estimate_hours=%(estimate_hours)s, parent_id=%(parent_id)s, freq=%(freq)s, "
        "interval_n=%(interval_n)s, weekdays=%(weekdays)s, month_day=%(month_day)s, "
        "lead_days=%(lead_days)s, next_on=%(next_on)s, active=%(active)s, updated_at=%(now)s "
        "WHERE id=%(id)s", dict(values, id=rule_id, now=db.now()))
    row = db.query_one(RECURRENCE_SELECT + " WHERE r.id=%s", (rule_id,))
    row["summary"] = recurrence.describe(row)
    return json_response({"recurrence": row})


@route("DELETE", r"/api/recurrences/(\d+)")
def delete_recurrence(ctx, rule_id):
    user = me(ctx)
    current = db.query_one("SELECT project_id FROM recurrences WHERE id=%s", (rule_id,))
    if not current:
        raise not_found("繰り返し設定が見つかりません")
    project_or_404(user, current["project_id"], "editor")
    db.execute("DELETE FROM recurrences WHERE id=%s", (rule_id,))
    return json_response({"ok": True})


@route("POST", r"/api/recurrences/(\d+)/run")
def run_recurrence_now(ctx, rule_id):
    """次回分を今すぐ作る。"""
    user = me(ctx)
    rule = db.query_one("SELECT * FROM recurrences WHERE id=%s", (rule_id,))
    if not rule:
        raise not_found("繰り返し設定が見つかりません")
    project_or_404(user, rule["project_id"], "editor")
    today = db.today()
    task_id = recurrence._create_task(rule, today)
    nxt = recurrence.next_date(rule, rule["next_on"])
    db.execute("UPDATE recurrences SET next_on=%s, last_created_on=%s, updated_at=%s WHERE id=%s",
               (nxt, today, db.now(), rule_id))
    return json_response({"task": db.query_one(TASK_SELECT + " WHERE t.id=%s", (task_id,))}, 201)


@route("POST", r"/api/recurrences/(\d+)/skip")
def skip_recurrence(ctx, rule_id):
    """今回は作らずに、次回日だけ先へ進める（「来週は休み」用）。"""
    user = me(ctx)
    rule = db.query_one("SELECT * FROM recurrences WHERE id=%s", (rule_id,))
    if not rule:
        raise not_found("繰り返し設定が見つかりません")
    project_or_404(user, rule["project_id"], "editor")
    times = as_int(ctx.body.get("times"), 1, 1, 12)
    skipped = rule["next_on"]
    nxt = rule["next_on"]
    for _ in range(times):
        nxt = recurrence.next_date(rule, nxt)
    db.execute("UPDATE recurrences SET next_on=%s, updated_at=%s WHERE id=%s",
               (nxt, db.now(), rule_id))
    row = db.query_one(RECURRENCE_SELECT + " WHERE r.id=%s", (rule_id,))
    row["summary"] = recurrence.describe(row)
    return json_response({"recurrence": row, "skipped": skipped, "next_on": nxt})


@route("POST", r"/api/admin/run-recurrences")
def run_recurrences(ctx):
    admin_only(ctx)
    return json_response({"created": recurrence.run()})


# --------------------------------------------------------------------------
# Slack
# --------------------------------------------------------------------------

@route("POST", r"/api/settings/test-slack")
def test_slack(ctx):
    admin_only(ctx)
    project_id = as_int(ctx.body.get("project_id"))
    url = (ctx.body.get("webhook_url") or "").strip()
    if not url and project_id:
        url = slack.webhook_for(project_id)
    ok, message = slack.check(url or None)
    return json_response({"ok": ok, "message": message})
