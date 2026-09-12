"""Notifications: in-app records, e-mail delivery, overdue scan, daily digest."""
import logging
import smtplib
import threading
import time
from datetime import timedelta
from email.message import EmailMessage
from email.utils import formataddr

import pymysql

from . import db, slack

log = logging.getLogger("tm.notify")

STATUS_LABEL = {
    "todo": "未着手", "doing": "進行中", "review": "レビュー中",
    "done": "完了", "blocked": "ブロック中",
}
OPEN_STATUSES = ("todo", "doing", "review", "blocked")


# --------------------------------------------------------------------------
# in-app notifications
# --------------------------------------------------------------------------

def create(user_id, ntype, title, body="", task_id=None, dedupe_key=None, email=True):
    """Insert a notification.  A repeated dedupe_key for the same user is a no-op."""
    try:
        db.insert(
            "INSERT INTO notifications(user_id, task_id, type, title, body, dedupe_key, created_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (user_id, task_id, ntype, title, body, dedupe_key, db.now()),
        )
    except pymysql.err.IntegrityError:
        return False  # already sent
    if email:
        user = db.query_one("SELECT email, name, email_notify FROM users WHERE id=%s", (user_id,))
        if user and user["email_notify"]:
            send_email_async(user["email"], title, body, user["name"])
    return True


def unread_count(user_id):
    return db.scalar(
        "SELECT COUNT(*) AS c FROM notifications WHERE user_id=%s AND is_read=0",
        (user_id,), default=0,
    )


# --------------------------------------------------------------------------
# e-mail
# --------------------------------------------------------------------------

def email_configured():
    s = db.all_settings()
    return s.get("email_enabled") == "1" and bool(s.get("smtp_host"))


def send_email(to_address, subject, body, to_name=""):
    """Send one mail.  Returns (ok, message)."""
    s = db.all_settings()
    if s.get("email_enabled") != "1":
        return False, "メール送信は無効化されています"
    if not s.get("smtp_host"):
        return False, "SMTP ホストが未設定です"

    base = s.get("app_base_url", "").rstrip("/")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = s.get("mail_from") or "task-manager@example.com"
    msg["To"] = formataddr((to_name, to_address)) if to_name else to_address
    footer = "\n\n---\nタスク管理システム\n{}\n".format(base) if base else ""
    msg.set_content((body or subject) + footer)

    try:
        port = int(s.get("smtp_port") or 587)
        if port == 465:
            server = smtplib.SMTP_SSL(s["smtp_host"], port, timeout=20)
        else:
            server = smtplib.SMTP(s["smtp_host"], port, timeout=20)
        with server:
            server.ehlo()
            if port != 465 and s.get("smtp_tls") == "1":
                server.starttls()
                server.ehlo()
            if s.get("smtp_user"):
                server.login(s["smtp_user"], s.get("smtp_password", ""))
            server.send_message(msg)
        return True, "送信しました"
    except Exception as exc:  # noqa: BLE001 - surfaced to the admin UI
        log.warning("mail send failed: %s", exc)
        return False, "送信に失敗しました: {}".format(exc)


def send_email_async(to_address, subject, body, to_name=""):
    if not email_configured():
        return
    threading.Thread(
        target=_send_and_close, args=(to_address, subject, body, to_name), daemon=True
    ).start()


def _send_and_close(to_address, subject, body, to_name):
    try:
        send_email(to_address, subject, body, to_name)
    finally:
        db.close_thread_connection()


# --------------------------------------------------------------------------
# due-date scanning
# --------------------------------------------------------------------------

def task_url(task_id):
    base = db.get_setting("app_base_url", "").rstrip("/")
    return "{}/#/task/{}".format(base, task_id) if base else ""


def scan_due_tasks():
    """Create overdue / due-soon notifications for assignees.  Idempotent per day."""
    today = db.today()
    soon_days = int(db.get_setting("due_soon_days", "3") or 3)
    horizon = today + timedelta(days=soon_days)
    created = 0

    rows = db.query(
        """
        SELECT t.id, t.title, t.due_date, t.assignee_id, t.status, p.name AS project_name
          FROM tasks t JOIN projects p ON p.id = t.project_id
         WHERE t.assignee_id IS NOT NULL
           AND t.due_date IS NOT NULL
           AND t.status IN %s
           AND p.archived = 0
           AND t.due_date <= %s
        """,
        (OPEN_STATUSES, horizon),
    )
    for t in rows:
        due = t["due_date"]
        overdue = due < today
        days = (today - due).days if overdue else (due - today).days
        if overdue:
            title = "【期限超過】{}（{}日超過）".format(t["title"], days)
            ntype, key = "overdue", "overdue:{}:{}".format(t["id"], today.isoformat())
        elif days == 0:
            title = "【本日期限】{}".format(t["title"])
            ntype, key = "due_soon", "due0:{}:{}".format(t["id"], today.isoformat())
        else:
            title = "【期限まで{}日】{}".format(days, t["title"])
            ntype, key = "due_soon", "due{}:{}:{}".format(days, t["id"], today.isoformat())
        body = "プロジェクト: {}\n期限: {}\n状態: {}\n{}".format(
            t["project_name"], due.isoformat(),
            STATUS_LABEL.get(t["status"], t["status"]), task_url(t["id"]),
        )
        if create(t["assignee_id"], ntype, title, body, task_id=t["id"], dedupe_key=key):
            created += 1
    return created


# --------------------------------------------------------------------------
# daily digest
# --------------------------------------------------------------------------

def daily_summary_for(user_id):
    """Tasks the user should look at today, grouped by urgency."""
    today = db.today()
    soon_days = int(db.get_setting("due_soon_days", "3") or 3)
    rows = db.query(
        """
        SELECT t.id, t.title, t.status, t.progress, t.due_date, t.priority,
               p.name AS project_name, p.id AS project_id
          FROM tasks t JOIN projects p ON p.id = t.project_id
         WHERE t.assignee_id = %s AND t.status IN %s AND p.archived = 0
         ORDER BY (t.due_date IS NULL), t.due_date, t.priority DESC
        """,
        (user_id, OPEN_STATUSES),
    )
    buckets = {"overdue": [], "today": [], "soon": [], "later": [], "no_due": []}
    for t in rows:
        due = t["due_date"]
        if due is None:
            buckets["no_due"].append(t)
        elif due < today:
            buckets["overdue"].append(t)
        elif due == today:
            buckets["today"].append(t)
        elif (due - today).days <= soon_days:
            buckets["soon"].append(t)
        else:
            buckets["later"].append(t)
    return buckets


def digest_text(user, buckets):
    base = db.get_setting("app_base_url", "").rstrip("/")
    lines = ["{} さん、本日のタスク状況です。".format(user["name"]), ""]
    labels = [
        ("overdue", "■ 期限超過"), ("today", "■ 本日期限"),
        ("soon", "■ まもなく期限"), ("no_due", "■ 期限未設定"),
    ]
    for key, label in labels:
        items = buckets.get(key) or []
        if not items:
            continue
        lines.append("{} ({}件)".format(label, len(items)))
        for t in items[:20]:
            due = t["due_date"].isoformat() if t["due_date"] else "-"
            lines.append("  - [{}] {} / 期限 {} / 進捗 {}%".format(
                t["project_name"], t["title"], due, t["progress"]))
        if len(items) > 20:
            lines.append("  ... 他 {} 件".format(len(items) - 20))
        lines.append("")
    if base:
        lines.append("今日の進捗を更新する: {}/#/daily".format(base))
    return "\n".join(lines)


def run_daily_digest(force=False):
    """Send each active user their daily summary.  Safe to call repeatedly."""
    if not force and db.get_setting("daily_digest_enabled", "1") != "1":
        return {"sent": 0, "skipped": "digest disabled"}
    today = db.today().isoformat()
    from . import recurrence          # 循環 import を避けるため遅延読み込み
    recurring = recurrence.run()
    scanned = scan_due_tasks()
    slack_posts = slack_daily_summary()
    sent = 0
    for user in db.query("SELECT id, name, email, email_notify FROM users WHERE is_active=1"):
        buckets = daily_summary_for(user["id"])
        actionable = sum(len(buckets[k]) for k in ("overdue", "today", "soon", "no_due"))
        if actionable == 0:
            continue
        title = "本日のタスク確認（超過 {} / 本日 {} / 直近 {}）".format(
            len(buckets["overdue"]), len(buckets["today"]), len(buckets["soon"]))
        if create(user["id"], "digest", title, digest_text(user, buckets),
                  dedupe_key="digest:{}".format(today), email=True):
            sent += 1
    return {"sent": sent, "due_notifications": scanned,
            "recurring_tasks": recurring, "slack_posts": slack_posts}


def slack_daily_summary():
    """プロジェクトごとの状況を Slack に流す。宛先がなければ何もしない。"""
    if not slack.available():
        return 0
    today = db.today()
    rows = db.query(
        """
        SELECT p.id, p.name, p.slack_webhook_url,
               SUM(t.status IN %s AND t.due_date IS NOT NULL AND t.due_date < %s) AS overdue,
               SUM(t.status IN %s AND t.due_date = %s) AS due_today,
               SUM(t.status IN %s) AS open_tasks
          FROM projects p LEFT JOIN tasks t ON t.project_id = p.id
         WHERE p.archived = 0
         GROUP BY p.id
        """,
        (OPEN_STATUSES, today, OPEN_STATUSES, today, OPEN_STATUSES))
    base = db.get_setting("app_base_url", "").rstrip("/")
    posted = 0
    combined = []
    for row in rows:
        overdue, due_today = int(row["overdue"] or 0), int(row["due_today"] or 0)
        issues = db.scalar(
            "SELECT COUNT(*) AS c FROM issues WHERE project_id=%s AND status IN %s "
            "AND severity >= 2", (row["id"], ("open", "doing", "pending")), default=0)
        if not (overdue or due_today or issues):
            continue
        line = "*{}* — 期限超過 {} / 本日期限 {} / 重要な未解決課題 {}".format(
            row["name"], overdue, due_today, issues)
        link = "{}/#/p/{}/tasks".format(base, row["id"]) if base else ""
        if row["slack_webhook_url"].strip():
            ok, _ = slack.post("📋 本日の状況\n{}\n{}".format(line, link),
                               webhook_url=row["slack_webhook_url"].strip())
            posted += 1 if ok else 0
        else:
            combined.append(line + ("\n{}".format(link) if link else ""))
    if combined:
        ok, _ = slack.post("📋 本日の状況\n" + "\n".join(combined))
        posted += 1 if ok else 0
    return posted


# --------------------------------------------------------------------------
# background scheduler
# --------------------------------------------------------------------------

class Scheduler(threading.Thread):
    """Runs the digest once a day at the configured local time."""

    def __init__(self):
        super().__init__(daemon=True, name="tm-scheduler")
        self._stop = threading.Event()
        self._last_run_date = None

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.wait(30):
            try:
                self._tick()
            except Exception as exc:  # keep the thread alive
                log.warning("scheduler tick failed: %s", exc)
                db.close_thread_connection()

    def _tick(self):
        if db.get_setting("daily_digest_enabled", "1") != "1":
            return
        now = db.now()
        target = db.get_setting("daily_digest_time", "09:00")
        try:
            hh, mm = [int(x) for x in target.split(":")[:2]]
        except ValueError:
            hh, mm = 9, 0
        today = now.date()
        if self._last_run_date == today:
            return
        if (now.hour, now.minute) >= (hh, mm):
            self._last_run_date = today
            result = run_daily_digest()
            log.info("daily digest: %s", result)


def start_scheduler():
    sched = Scheduler()
    sched.start()
    return sched
