"""通知を「送るか送らないか」を一箇所で決める。

判定は 3 段重ね。上から順に 1 つでも「送らない」なら送らない。

  1. プロジェクト        projects.notify_enabled … そのプロジェクト発の通知を丸ごと止める
  2. 受け取る人          users.email_notify + イベント別スイッチ
  3. 受け取る人×プロジェクト  notification_mutes … 興味のないプロジェクトだけ黙らせる

Slack はチャンネル宛なので個人設定は関係なく、1 とイベント選択だけを見る。
"""
from . import db

# メール（＝個人宛）のイベント。UI のチェックボックスもこの順に並ぶ。
EMAIL_EVENTS = [
    ("assigned", "担当に設定されたとき", "タスクや課題の担当者に指名されたとき"),
    ("comment", "コメントがついたとき", "自分が担当・作成・コメントしたものへの書き込み"),
    ("due", "期限が近い / 超過したとき", "期限前の予告と、超過したタスクのお知らせ"),
    ("digest", "日次レポート", "毎朝その日のタスク一覧をまとめて受け取る"),
]
EMAIL_EVENT_KEYS = [key for key, _, _ in EMAIL_EVENTS]
EMAIL_COLUMNS = {key: "notify_{}".format(key) for key in EMAIL_EVENT_KEYS}

# Slack（＝チャンネル宛）のイベント。
SLACK_EVENTS = [
    ("issue", "課題の起票", "影響度「大」以上の課題が登録されたとき"),
    ("digest", "日次サマリ", "毎朝の期限超過・本日期限のまとめ"),
]
SLACK_EVENT_KEYS = [key for key, _, _ in SLACK_EVENTS]

# notifications.type から判定用のイベント名へ。
EVENT_ALIASES = {"overdue": "due", "due_soon": "due"}


def event_of(ntype):
    return EVENT_ALIASES.get(ntype, ntype)


def parse_events(raw, allowed):
    """カンマ区切りの文字列を、許可されたイベント名だけの集合にする。"""
    if not raw:
        return set()
    return {part.strip() for part in str(raw).split(",") if part.strip() in allowed}


def format_events(values, allowed):
    """保存用のカンマ区切り文字列。並び順は定義順に揃える。"""
    chosen = set()
    for value in values or []:
        text = str(value).strip()
        if text in allowed:
            chosen.add(text)
    return ",".join(key for key in allowed if key in chosen)


# --------------------------------------------------------------------------
# プロジェクト側
# --------------------------------------------------------------------------

def project_notify_enabled(project_id):
    if not project_id:
        return True
    value = db.scalar("SELECT notify_enabled AS v FROM projects WHERE id=%s",
                      (project_id,), default=1)
    return bool(value)


def project_muted_by(user_id, project_id):
    if not (user_id and project_id):
        return False
    return db.query_one(
        "SELECT 1 AS x FROM notification_mutes WHERE user_id=%s AND project_id=%s",
        (user_id, project_id)) is not None


def muted_projects(user_id):
    return [r["project_id"] for r in db.query(
        "SELECT project_id FROM notification_mutes WHERE user_id=%s ORDER BY project_id",
        (user_id,))]


def set_muted_projects(user_id, project_ids):
    """ミュートするプロジェクトを丸ごと置き換える。存在しない ID は無視。"""
    wanted = set()
    for value in project_ids or []:
        try:
            wanted.add(int(value))
        except (TypeError, ValueError):
            continue
    db.execute("DELETE FROM notification_mutes WHERE user_id=%s", (user_id,))
    for project_id in sorted(wanted):
        if db.query_one("SELECT 1 AS x FROM projects WHERE id=%s", (project_id,)):
            db.execute(
                "INSERT INTO notification_mutes(user_id, project_id) VALUES(%s,%s)",
                (user_id, project_id))
    return muted_projects(user_id)


# --------------------------------------------------------------------------
# 受け取る人側（メール）
# --------------------------------------------------------------------------

def email_prefs(user_id):
    columns = ", ".join(EMAIL_COLUMNS[key] for key in EMAIL_EVENT_KEYS)
    row = db.query_one(
        "SELECT email_notify, {} FROM users WHERE id=%s".format(columns), (user_id,))
    if not row:
        return None
    prefs = {"email_notify": bool(row["email_notify"])}
    for key in EMAIL_EVENT_KEYS:
        prefs[key] = bool(row[EMAIL_COLUMNS[key]])
    return prefs


def save_email_prefs(user_id, values):
    """渡されたキーだけを更新する。"""
    sets, params = [], []
    if "email_notify" in values:
        sets.append("email_notify=%s")
        params.append(1 if values["email_notify"] else 0)
    for key in EMAIL_EVENT_KEYS:
        if key in values:
            sets.append("{}=%s".format(EMAIL_COLUMNS[key]))
            params.append(1 if values[key] else 0)
    if sets:
        params.append(user_id)
        db.execute("UPDATE users SET {} WHERE id=%s".format(", ".join(sets)), params)
    return email_prefs(user_id)


def email_allowed(user_id, ntype, project_id=None):
    """この人にこの通知メールを送ってよいか。"""
    if not project_notify_enabled(project_id):
        return False
    prefs = email_prefs(user_id)
    if not prefs or not prefs["email_notify"]:
        return False
    event = event_of(ntype)
    if event in prefs and not prefs[event]:
        return False
    return not project_muted_by(user_id, project_id)


# --------------------------------------------------------------------------
# Slack
# --------------------------------------------------------------------------

def global_slack_events():
    return parse_events(db.get_setting("slack_events", "issue,digest"), SLACK_EVENT_KEYS)


def slack_events_for(project_id=None):
    """プロジェクト個別の選択があればそちら、なければ全体設定。"""
    if project_id:
        raw = db.scalar("SELECT slack_events AS v FROM projects WHERE id=%s",
                        (project_id,), default="") or ""
        if raw.strip():
            return parse_events(raw, SLACK_EVENT_KEYS)
    return global_slack_events()


def slack_allowed(event, project_id=None):
    if not project_notify_enabled(project_id):
        return False
    return event in slack_events_for(project_id)
