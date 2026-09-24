"""Authentication, sessions and the permission model."""
import base64
import hashlib
import hmac
import os
import secrets
from datetime import timedelta

from . import db

SESSION_COOKIE = "tm_session"
SESSION_DAYS = 14
PBKDF2_ROUNDS = 200_000

# Project roles, weakest to strongest.
ROLE_ORDER = {"viewer": 1, "commenter": 2, "editor": 3, "owner": 4}
PROJECT_ROLES = list(ROLE_ORDER)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ROUNDS,
        base64.b64encode(salt).decode(),
        base64.b64encode(dk).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt_b64), int(rounds)
        )
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except Exception:
        return False


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO sessions(token, user_id, created_at, expires_at) VALUES(%s,%s,%s,%s)",
        (token, user_id, db.now(), db.now() + timedelta(days=SESSION_DAYS)),
    )
    return token


def destroy_session(token: str):
    db.execute("DELETE FROM sessions WHERE token=%s", (token,))


def user_for_token(token: str):
    if not token:
        return None
    row = db.query_one(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token=%s AND s.expires_at > %s AND u.is_active = 1",
        (token, db.now()),
    )
    if row:
        row.pop("password_hash", None)
    return row


def purge_expired_sessions():
    db.execute("DELETE FROM sessions WHERE expires_at <= %s", (db.now(),))


_DUMMY = []


def _dummy_hash():
    if not _DUMMY:
        _DUMMY.append(hash_password(secrets.token_urlsafe(12)))
    return _DUMMY[0]


def check_login(email: str, password: str):
    """(ユーザー, 失敗の理由) を返す。成功なら理由は空、失敗ならユーザーは
    分かる範囲で返す（ログイン履歴に「誰のアカウントで失敗したか」を残すため）。"""
    row = db.query_one("SELECT * FROM users WHERE email=%s", (email.strip(),))
    if not row:
        # 登録の有無で応答の速さが変わると、アドレスの存在を探られる。同じだけ計算しておく
        verify_password(password, _dummy_hash())
        return None, "unknown"
    stored = row.pop("password_hash", None)
    if not verify_password(password, stored or ""):
        return row, "bad_password"
    if not row["is_active"]:
        return row, "inactive"
    return row, ""


def authenticate(email: str, password: str):
    user, reason = check_login(email, password)
    return None if reason else user


# --------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------

def is_admin(user) -> bool:
    return bool(user) and user.get("role") == "admin"


def project_role(user, project_id):
    """Strongest role the user holds on a project, directly or via a group."""
    if not user or project_id is None:
        return None
    if is_admin(user):
        return "owner"
    proj = db.query_one("SELECT owner_id FROM projects WHERE id=%s", (project_id,))
    if proj is None:
        return None
    roles = [
        r["role"]
        for r in db.query(
            """
            SELECT pm.role FROM project_members pm
             WHERE pm.project_id = %s
               AND ( (pm.principal_type='user'  AND pm.principal_id = %s)
                  OR (pm.principal_type='group' AND pm.principal_id IN
                        (SELECT group_id FROM group_members WHERE user_id = %s)) )
            """,
            (project_id, user["id"], user["id"]),
        )
    ]
    if proj["owner_id"] == user["id"]:
        roles.append("owner")
    if not roles:
        return None
    return max(roles, key=lambda r: ROLE_ORDER.get(r, 0))


def project_roles(user, project_ids):
    """複数プロジェクトぶんの権限をまとめて返す。一覧画面で 1 件ずつ引かないため。"""
    ids = [i for i in project_ids if i]
    if not user or not ids:
        return {}
    if is_admin(user):
        return {i: "owner" for i in ids}
    scope = tuple(ids)
    best = {}
    for row in db.query(
            """
            SELECT pm.project_id, pm.role FROM project_members pm
             WHERE pm.project_id IN %s
               AND ( (pm.principal_type='user'  AND pm.principal_id = %s)
                  OR (pm.principal_type='group' AND pm.principal_id IN
                        (SELECT group_id FROM group_members WHERE user_id = %s)) )
            """, (scope, user["id"], user["id"])):
        current = best.get(row["project_id"])
        if current is None or ROLE_ORDER.get(row["role"], 0) > ROLE_ORDER.get(current, 0):
            best[row["project_id"]] = row["role"]
    for row in db.query("SELECT id FROM projects WHERE id IN %s AND owner_id=%s",
                        (scope, user["id"])):
        best[row["id"]] = "owner"
    return best


def has_project_access(user, project_id, minimum="viewer") -> bool:
    role = project_role(user, project_id)
    if role is None:
        return False
    return ROLE_ORDER.get(role, 0) >= ROLE_ORDER.get(minimum, 0)


def visible_project_ids(user):
    """Project ids the user may at least view."""
    if is_admin(user):
        return [r["id"] for r in db.query("SELECT id FROM projects")]
    rows = db.query(
        """
        SELECT DISTINCT p.id FROM projects p
          LEFT JOIN project_members pm ON pm.project_id = p.id
         WHERE p.owner_id = %s
            OR (pm.principal_type='user'  AND pm.principal_id = %s)
            OR (pm.principal_type='group' AND pm.principal_id IN
                  (SELECT group_id FROM group_members WHERE user_id = %s))
        """,
        (user["id"], user["id"], user["id"]),
    )
    return [r["id"] for r in rows]


def task_project_id(task_id):
    return db.scalar("SELECT project_id FROM tasks WHERE id=%s", (task_id,))


def ensure_bootstrap_admin():
    """Create the first admin account when the database has no users."""
    if db.query_one("SELECT 1 FROM users LIMIT 1"):
        return None
    email = os.environ.get("TM_ADMIN_EMAIL", "admin@example.com")
    password = os.environ.get("TM_ADMIN_PASSWORD") or secrets.token_urlsafe(9)
    db.insert(
        "INSERT INTO users(email, name, password_hash, role, created_at) "
        "VALUES(%s,%s,%s,%s,%s)",
        (email, "管理者", hash_password(password), "admin", db.now()),
    )
    return {"email": email, "password": password}
