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


_USER_BY = (
    "SELECT u.*, (SELECT m.enabled_at IS NOT NULL FROM user_mfa m WHERE m.user_id = u.id) AS mfa_on "
    "FROM users u WHERE u.id=%s AND u.is_active = 1 AND (u.expires_on IS NULL OR u.expires_on >= %s)")

ACT_MINUTES = 30   # 代理表示（閲覧専用）を続けられる時間


def user_for_token(token: str):
    """セッションの利用者。管理者が代理表示をしているときは、見ている相手の行を返し、
    本人（管理者）の行を "_actor" に入れる。"""
    if not token:
        return None
    session = db.query_one("SELECT user_id, acting_as, acting_until FROM sessions "
                           "WHERE token=%s AND expires_at > %s", (token, db.now()))
    if not session:
        return None
    row = _clean(db.query_one(_USER_BY, (session["user_id"], db.today())))
    if not row or not session["acting_as"]:
        return row
    target = _clean(db.query_one(_USER_BY, (session["acting_as"], db.today())))
    if (not is_admin(row) or not target or is_admin(target)
            or not session["acting_until"] or session["acting_until"] <= db.now()):
        # 期限切れ・相手が停止された・管理者でなくなった → 自分に戻す
        stop_acting(token, row, target, reason="expired_act")
        return row
    target["_actor"] = row
    target["_acting_until"] = session["acting_until"]
    return target


def _clean(row):
    if row:
        row.pop("password_hash", None)
        row["mfa_on"] = bool(row.get("mfa_on"))
    return row


def start_acting(token, actor, target):
    until = db.now() + timedelta(minutes=ACT_MINUTES)
    db.execute("UPDATE sessions SET acting_as=%s, acting_until=%s WHERE token=%s",
               (target["id"], until, token))
    return until


def stop_acting(token, actor, target, reason="", ip="", user_agent=""):
    """代理表示を終える。終わったことも履歴に残す。"""
    from . import logins
    session = db.query_one("SELECT acting_as FROM sessions WHERE token=%s", (token,))
    if not session or not session["acting_as"]:
        return
    if not db.execute("UPDATE sessions SET acting_as=NULL, acting_until=NULL "
                      "WHERE token=%s AND acting_as IS NOT NULL", (token,)):
        return
    target = target or db.query_one("SELECT id, name FROM users WHERE id=%s", (session["acting_as"],))
    if target:
        logins.record("act_end", target, reason, ip, user_agent,
                      label="{}（{} の代理表示）".format(target["name"], actor["name"]))


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
    if row.get("expires_on") and row["expires_on"] < db.today():
        return row, "expired"
    return row, ""


def authenticate(email: str, password: str):
    user, reason = check_login(email, password)
    return None if reason else user


# --------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------

def is_admin(user) -> bool:
    return bool(user) and user.get("role") == "admin"


# アカウントの種類。社外ユーザーは、参加しているプロジェクトの中だけを扱う。
ACCOUNT_ROLES = ("admin", "member", "guest")
ACCOUNT_LABEL = {"admin": "管理者", "member": "社内ユーザー", "guest": "社外ユーザー"}
# 社外ユーザーがプロジェクトで持てるいちばん強い役割。タスクの追加・編集はさせず、
# 起票は（第 2 段階で開ける）チケットから。担当タスクの進捗更新は別に許す。
GUEST_MAX_ROLE = "commenter"


def is_guest(user) -> bool:
    return bool(user) and user.get("role") == "guest"


# プロジェクトの中のタブ。タスクはプロジェクトの入口なので、いつも出す。
PROJECT_TABS = ("tasks", "gantt", "workload", "bottlenecks", "issues", "tickets")
TAB_LABEL = {"tasks": "タスク", "gantt": "ガント", "workload": "負荷",
             "bottlenecks": "ボトルネック", "issues": "課題", "tickets": "チケット"}
# 社外ユーザーに見せるタブの初期値。「見せるものを並べる」形なので、あとからタブを
# 足しても社外ユーザーには勝手に見えない。負荷（担当者ごとの工数）はどうしても見せない。
GUEST_TABS_DEFAULT = "tasks,gantt,issues,tickets"
GUEST_TABS_NEVER = frozenset({"workload"})


def parse_tabs(value):
    return {t for t in str(value or "").split(",") if t in PROJECT_TABS}


def tab_settings(project_ids):
    """{project_id: (隠すタブ, 社外ユーザーに見せるタブ)}。"""
    ids = [i for i in project_ids if i]
    if not ids:
        return {}
    return {r["id"]: (parse_tabs(r["tabs_hidden"]), parse_tabs(r["guest_tabs"]))
            for r in db.query("SELECT id, tabs_hidden, guest_tabs FROM projects WHERE id IN %s",
                              (tuple(ids),))}


def tab_open_for(user, key, hidden, guest_tabs):
    """このタブを、この人に開いてよいか（社外ユーザー向けの判定）。

    社内の人に対する「隠す」は画面をすっきりさせるためのもので、データは閉じない。
    社外ユーザーに対しては、ここで閉じたものはサーバーでも閉じる。"""
    if key == "tasks" or not is_guest(user):
        return True
    return key not in hidden and key in guest_tabs and key not in GUEST_TABS_NEVER


def guest_tab_open(user, project_id, key):
    if not is_guest(user) or key == "tasks":
        return True
    hidden, guest_tabs = tab_settings([project_id]).get(project_id, (set(), set()))
    return tab_open_for(user, key, hidden, guest_tabs)


def tab_project_ids(user, key):
    """このタブを開いてよいプロジェクト（社外ユーザーは設定で絞る。社内の人はそのまま）。"""
    ids = visible_project_ids(user)
    if not is_guest(user):
        return ids
    settings = tab_settings(ids)
    return [i for i in ids if tab_open_for(user, key, *settings.get(i, (set(), set())))]


def _cap(user, role):
    """社外ユーザーは、どんな付け方をされても GUEST_MAX_ROLE までにする。
    グループ経由で編集者になっていても、ここで抑える。"""
    if role and is_guest(user) and ROLE_ORDER.get(role, 0) > ROLE_ORDER[GUEST_MAX_ROLE]:
        return GUEST_MAX_ROLE
    return role


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
    return _cap(user, max(roles, key=lambda r: ROLE_ORDER.get(r, 0)))


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
    return {pid: _cap(user, role) for pid, role in best.items()}


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
