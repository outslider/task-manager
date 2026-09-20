"""MySQL / MariaDB storage layer (PyMySQL driver).

One connection per thread, reconnecting automatically.  All SQL in the code
base uses %s placeholders.
"""
import logging
import threading
from datetime import date, datetime
from decimal import Decimal

import pymysql
from pymysql.cursors import DictCursor

from .config import DB

_local = threading.local()

DDL = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        email         VARCHAR(190) NOT NULL UNIQUE,
        name          VARCHAR(120) NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        role          VARCHAR(20)  NOT NULL DEFAULT 'member',
        is_active     TINYINT(1)   NOT NULL DEFAULT 1,
        email_notify  TINYINT(1)   NOT NULL DEFAULT 1,   -- メール通知の親スイッチ
        notify_assigned TINYINT(1) NOT NULL DEFAULT 1,   -- 自分が担当になったとき
        notify_comment  TINYINT(1) NOT NULL DEFAULT 1,   -- 自分が関わるものへのコメント
        notify_due      TINYINT(1) NOT NULL DEFAULT 1,   -- 期限が近い / 超過
        notify_digest   TINYINT(1) NOT NULL DEFAULT 1,   -- 日次レポート
        notify_mention  TINYINT(1) NOT NULL DEFAULT 1,   -- コメントで名前を呼ばれたとき
        avatar_color  VARCHAR(20)  NOT NULL DEFAULT '#4f8cff',
        ui_theme      VARCHAR(10)  NOT NULL DEFAULT 'auto',   -- auto|light|dark
        ui_accent     VARCHAR(20)  NOT NULL DEFAULT '',       -- 空なら組織の既定色
        created_at    DATETIME     NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS user_groups (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        name        VARCHAR(150) NOT NULL UNIQUE,
        description VARCHAR(500) NOT NULL DEFAULT '',
        created_at  DATETIME     NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS group_members (
        group_id INT NOT NULL,
        user_id  INT NOT NULL,
        PRIMARY KEY (group_id, user_id),
        CONSTRAINT fk_gm_group FOREIGN KEY (group_id) REFERENCES user_groups(id) ON DELETE CASCADE,
        CONSTRAINT fk_gm_user  FOREIGN KEY (user_id)  REFERENCES users(id)       ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS projects (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        name        VARCHAR(200)  NOT NULL,
        description TEXT,
        color       VARCHAR(20)   NOT NULL DEFAULT '#4f8cff',
        owner_id    INT NULL,
        archived    TINYINT(1)    NOT NULL DEFAULT 0,
        slack_webhook_url VARCHAR(300) NOT NULL DEFAULT '',  -- 空なら全体設定を使う
        notify_enabled TINYINT(1)  NOT NULL DEFAULT 1,       -- 0 ならこのプロジェクトの通知を止める
        slack_events VARCHAR(120)  NOT NULL DEFAULT '',      -- 空なら全体設定を使う
        created_at  DATETIME      NOT NULL,
        CONSTRAINT fk_proj_owner FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS project_members (
        project_id     INT NOT NULL,
        principal_type VARCHAR(10) NOT NULL,          -- user | group
        principal_id   INT NOT NULL,
        role           VARCHAR(20) NOT NULL DEFAULT 'editor',
        PRIMARY KEY (project_id, principal_type, principal_id),
        CONSTRAINT fk_pm_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id           INT AUTO_INCREMENT PRIMARY KEY,
        project_id   INT NOT NULL,
        parent_id    INT NULL,
        title        VARCHAR(300) NOT NULL,
        description  TEXT,
        category     VARCHAR(20)  NOT NULL DEFAULT '',       -- research|design|build|docs|meeting|admin|incident
        status       VARCHAR(20)  NOT NULL DEFAULT 'todo',   -- todo|doing|review|done|blocked
        priority     TINYINT      NOT NULL DEFAULT 1,        -- 重要度 0 低 .. 3 最重要
        assignee_id  INT NULL,
        start_date   DATE NULL,
        due_date     DATE NULL,
        progress     INT          NOT NULL DEFAULT 0,
        estimate_hours DECIMAL(6,1) NULL,              -- 見積工数（任意）
        actual_hours   DECIMAL(6,1) NOT NULL DEFAULT 0, -- 実績工数（日次更新で積み上がる）
        is_milestone TINYINT(1)   NOT NULL DEFAULT 0,
        marker      VARCHAR(10)   NOT NULL DEFAULT '',   -- ガントで使う記号（空なら既定）
        sort_order   INT          NOT NULL DEFAULT 0,
        created_by   INT NULL,
        created_at   DATETIME     NOT NULL,
        updated_at   DATETIME     NOT NULL,
        completed_at DATETIME NULL,
        KEY idx_tasks_project (project_id),
        KEY idx_tasks_parent (parent_id),
        KEY idx_tasks_assignee (assignee_id),
        KEY idx_tasks_due (due_date),
        KEY idx_tasks_category (category),
        CONSTRAINT fk_task_project  FOREIGN KEY (project_id)  REFERENCES projects(id) ON DELETE CASCADE,
        CONSTRAINT fk_task_parent   FOREIGN KEY (parent_id)   REFERENCES tasks(id)    ON DELETE CASCADE,
        CONSTRAINT fk_task_assignee FOREIGN KEY (assignee_id) REFERENCES users(id)    ON DELETE SET NULL,
        CONSTRAINT fk_task_creator  FOREIGN KEY (created_by)  REFERENCES users(id)    ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS task_deps (
        task_id       INT NOT NULL,
        depends_on_id INT NOT NULL,
        PRIMARY KEY (task_id, depends_on_id),
        KEY idx_dep_on (depends_on_id),
        CONSTRAINT fk_dep_task FOREIGN KEY (task_id)       REFERENCES tasks(id) ON DELETE CASCADE,
        CONSTRAINT fk_dep_on   FOREIGN KEY (depends_on_id) REFERENCES tasks(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS issues (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        project_id  INT NOT NULL,
        seq         INT NOT NULL,                       -- プロジェクト内の課題No.
        title       VARCHAR(300) NOT NULL,
        description TEXT,                               -- 課題の内容・背景
        category    VARCHAR(20)  NOT NULL DEFAULT '',   -- spec|tech|schedule|...
        status      VARCHAR(20)  NOT NULL DEFAULT 'open', -- open|doing|pending|resolved|closed
        severity    TINYINT      NOT NULL DEFAULT 1,    -- 影響度 0 低 .. 3 重大
        owner_id    INT NULL,                           -- 対応者
        raised_by   INT NULL,                           -- 起票者
        raised_on   DATE NOT NULL,                      -- 発生日
        due_date    DATE NULL,                          -- 対応期限
        resolved_on DATE NULL,                          -- 解決日
        resolution  TEXT,                               -- 対応方針・対応結果
        created_at  DATETIME NOT NULL,
        updated_at  DATETIME NOT NULL,
        UNIQUE KEY uq_issue_seq (project_id, seq),
        KEY idx_issues_status (project_id, status),
        KEY idx_issues_owner (owner_id),
        KEY idx_issues_due (due_date),
        CONSTRAINT fk_issue_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
        CONSTRAINT fk_issue_owner   FOREIGN KEY (owner_id)   REFERENCES users(id)    ON DELETE SET NULL,
        CONSTRAINT fk_issue_raiser  FOREIGN KEY (raised_by)  REFERENCES users(id)    ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS issue_tasks (
        issue_id INT NOT NULL,
        task_id  INT NOT NULL,
        PRIMARY KEY (issue_id, task_id),
        KEY idx_issue_tasks_task (task_id),
        CONSTRAINT fk_it_issue FOREIGN KEY (issue_id) REFERENCES issues(id) ON DELETE CASCADE,
        CONSTRAINT fk_it_task  FOREIGN KEY (task_id)  REFERENCES tasks(id)  ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ticket_queues (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        name        VARCHAR(80)  NOT NULL,             -- 受付窓口の名前（情シス窓口 など）
        description VARCHAR(300) NOT NULL DEFAULT '',
        color       VARCHAR(20)  NOT NULL DEFAULT '#3b6ef5',
        icon        VARCHAR(8)   NOT NULL DEFAULT '',
        project_id  INT NULL,                          -- 特定プロジェクト専用の窓口にする場合
        default_kind VARCHAR(20) NOT NULL DEFAULT 'request',  -- 起票時に最初から選ばれる種別
        sort_order  INT          NOT NULL DEFAULT 0,
        is_active   TINYINT(1)   NOT NULL DEFAULT 1,
        created_at  DATETIME     NOT NULL,
        UNIQUE KEY uq_queue_name (name),
        KEY idx_queue_project (project_id),
        CONSTRAINT fk_queue_project FOREIGN KEY (project_id) REFERENCES projects(id)
            ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ticket_categories (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        queue_id   INT NOT NULL,
        label      VARCHAR(60) NOT NULL,
        color      VARCHAR(20) NOT NULL DEFAULT '#98a2b3',
        sort_order INT         NOT NULL DEFAULT 0,
        KEY idx_ticket_cat_queue (queue_id, sort_order),
        CONSTRAINT fk_ticket_cat_queue FOREIGN KEY (queue_id)
            REFERENCES ticket_queues(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS tickets (
        id           INT AUTO_INCREMENT PRIMARY KEY,
        queue_id     INT NOT NULL,
        kind         VARCHAR(20)  NOT NULL DEFAULT 'request',  -- request|question|incident
        category_id  INT NULL,                            -- 窓口ごとに決めた分類
        title        VARCHAR(300) NOT NULL,
        body         TEXT,                               -- 依頼・問い合わせの内容
        status       VARCHAR(20)  NOT NULL DEFAULT 'new', -- new|doing|pending|done|canceled
        priority     TINYINT      NOT NULL DEFAULT 1,     -- 0 低 .. 3 緊急
        requester_id INT NULL,                            -- 登録した人
        on_behalf_of VARCHAR(120) NOT NULL DEFAULT '',    -- 代理で出したときの依頼元
        assignee_id  INT NULL,                            -- 対応する人
        due_date     DATE NULL,                           -- 回答・対応の期限
        occurred_at  DATETIME NULL,                       -- 障害の発生日時
        resolved_at  DATETIME NULL,
        spent_hours  DECIMAL(6,1) NULL,                   -- かかった時間（任意）
        resolution   TEXT,                                -- 対応結果
        created_at   DATETIME NOT NULL,
        updated_at   DATETIME NOT NULL,
        KEY idx_tickets_queue (queue_id, status),
        KEY idx_tickets_assignee (assignee_id, status),
        KEY idx_tickets_requester (requester_id),
        KEY idx_tickets_due (due_date),
        KEY idx_tickets_category (category_id),
        KEY idx_tickets_created (created_at),
        KEY idx_tickets_resolved (resolved_at),
        CONSTRAINT fk_ticket_queue     FOREIGN KEY (queue_id)     REFERENCES ticket_queues(id),
        CONSTRAINT fk_ticket_requester FOREIGN KEY (requester_id) REFERENCES users(id) ON DELETE SET NULL,
        CONSTRAINT fk_ticket_assignee  FOREIGN KEY (assignee_id)  REFERENCES users(id) ON DELETE SET NULL,
        CONSTRAINT fk_ticket_category  FOREIGN KEY (category_id)
            REFERENCES ticket_categories(id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ticket_tasks (
        ticket_id INT NOT NULL,
        task_id   INT NOT NULL,
        PRIMARY KEY (ticket_id, task_id),
        KEY idx_ticket_tasks_task (task_id),
        CONSTRAINT fk_tt_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
        CONSTRAINT fk_tt_task   FOREIGN KEY (task_id)   REFERENCES tasks(id)   ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ticket_issues (
        ticket_id INT NOT NULL,
        issue_id  INT NOT NULL,
        PRIMARY KEY (ticket_id, issue_id),
        KEY idx_ticket_issues_issue (issue_id),
        CONSTRAINT fk_ti_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
        CONSTRAINT fk_ti_issue  FOREIGN KEY (issue_id)  REFERENCES issues(id)  ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS recurrences (
        id             INT AUTO_INCREMENT PRIMARY KEY,
        project_id     INT NOT NULL,
        title          VARCHAR(300) NOT NULL,
        description    TEXT,
        category       VARCHAR(20)  NOT NULL DEFAULT '',
        priority       TINYINT      NOT NULL DEFAULT 1,
        assignee_id    INT NULL,
        estimate_hours DECIMAL(6,1) NULL,
        parent_id      INT NULL,                        -- 生成先の親タスク（任意）
        freq           VARCHAR(10)  NOT NULL,           -- daily | weekly | monthly
        interval_n     INT          NOT NULL DEFAULT 1,
        weekdays       VARCHAR(20)  NOT NULL DEFAULT '', -- weekly のとき '0,2,4'（月=0）
        month_day      TINYINT      NULL,               -- monthly のとき 1-31
        lead_days      INT          NOT NULL DEFAULT 3, -- 期限の何日前に作るか
        next_on        DATE         NOT NULL,           -- 次に作るタスクの期限
        last_created_on DATE        NULL,
        active         TINYINT(1)   NOT NULL DEFAULT 1,
        created_by     INT NULL,
        created_at     DATETIME     NOT NULL,
        updated_at     DATETIME     NOT NULL,
        KEY idx_recurrence_project (project_id),
        KEY idx_recurrence_next (active, next_on),
        CONSTRAINT fk_rec_project  FOREIGN KEY (project_id)  REFERENCES projects(id) ON DELETE CASCADE,
        CONSTRAINT fk_rec_assignee FOREIGN KEY (assignee_id) REFERENCES users(id)    ON DELETE SET NULL,
        CONSTRAINT fk_rec_parent   FOREIGN KEY (parent_id)   REFERENCES tasks(id)    ON DELETE SET NULL,
        CONSTRAINT fk_rec_creator  FOREIGN KEY (created_by)  REFERENCES users(id)    ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS comments (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        task_id    INT NULL,
        issue_id   INT NULL,
        ticket_id  INT NULL,
        user_id    INT NULL,
        body       TEXT NOT NULL,
        kind       VARCHAR(20) NOT NULL DEFAULT 'comment',   -- comment | system | checkin
        created_at DATETIME NOT NULL,
        KEY idx_comments_task (task_id),
        KEY idx_comments_issue (issue_id),
        KEY idx_comments_ticket (ticket_id),
        CONSTRAINT fk_comment_task  FOREIGN KEY (task_id)  REFERENCES tasks(id)  ON DELETE CASCADE,
        CONSTRAINT fk_comment_issue FOREIGN KEY (issue_id) REFERENCES issues(id) ON DELETE CASCADE,
        CONSTRAINT fk_comment_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
        CONSTRAINT fk_comment_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS attachments (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        task_id     INT NULL,
        issue_id    INT NULL,
        ticket_id   INT NULL,
        kind        VARCHAR(10)  NOT NULL,            -- file | link
        name        VARCHAR(300) NOT NULL,
        url         VARCHAR(2000) NOT NULL DEFAULT '',
        stored_name VARCHAR(200) NOT NULL DEFAULT '',
        size        BIGINT       NOT NULL DEFAULT 0,
        mime        VARCHAR(150) NOT NULL DEFAULT '',
        uploaded_by INT NULL,
        created_at  DATETIME     NOT NULL,
        KEY idx_attachments_task (task_id),
        KEY idx_attachments_issue (issue_id),
        KEY idx_attachments_ticket (ticket_id),
        CONSTRAINT fk_att_task  FOREIGN KEY (task_id)  REFERENCES tasks(id)  ON DELETE CASCADE,
        CONSTRAINT fk_att_issue FOREIGN KEY (issue_id) REFERENCES issues(id) ON DELETE CASCADE,
        CONSTRAINT fk_att_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
        CONSTRAINT fk_att_user FOREIGN KEY (uploaded_by) REFERENCES users(id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        token      VARCHAR(64) PRIMARY KEY,
        user_id    INT NOT NULL,
        created_at DATETIME NOT NULL,
        expires_at DATETIME NOT NULL,
        KEY idx_sessions_user (user_id),
        CONSTRAINT fk_session_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS notifications (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        user_id    INT NOT NULL,
        task_id    INT NULL,
        type       VARCHAR(30)  NOT NULL,
        title      VARCHAR(300) NOT NULL,
        body       TEXT,
        is_read    TINYINT(1)   NOT NULL DEFAULT 0,
        dedupe_key VARCHAR(190) NULL,
        created_at DATETIME     NOT NULL,
        KEY idx_notif_user (user_id, is_read),
        UNIQUE KEY uq_notif_dedupe (user_id, dedupe_key),
        CONSTRAINT fk_notif_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        CONSTRAINT fk_notif_task FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        setting_key   VARCHAR(100) PRIMARY KEY,
        setting_value TEXT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS checkins (
        id           INT AUTO_INCREMENT PRIMARY KEY,
        user_id      INT NOT NULL,
        checkin_date DATE NOT NULL,
        note         TEXT,
        created_at   DATETIME NOT NULL,
        UNIQUE KEY uq_checkin (user_id, checkin_date),
        CONSTRAINT fk_checkin_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS todos (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        user_id    INT NOT NULL,
        title      VARCHAR(300) NOT NULL,
        note       VARCHAR(1000) NOT NULL DEFAULT '',
        due_date   DATE NULL,
        is_done    TINYINT(1) NOT NULL DEFAULT 0,
        sort_order INT NOT NULL DEFAULT 0,
        done_at    DATETIME NULL,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        KEY idx_todos_user (user_id, is_done, sort_order),
        CONSTRAINT fk_todo_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS task_statuses (
        status_key VARCHAR(20)  NOT NULL PRIMARY KEY,
        label      VARCHAR(40)  NOT NULL,
        color      VARCHAR(20)  NOT NULL,
        sort_order INT          NOT NULL DEFAULT 0
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS task_categories (
        value      VARCHAR(30)  NOT NULL PRIMARY KEY,
        label      VARCHAR(60)  NOT NULL,
        color      VARCHAR(20)  NOT NULL,
        icon       VARCHAR(8)   NOT NULL DEFAULT '',
        sort_order INT          NOT NULL DEFAULT 0
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS shared_links (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        project_id INT NULL,                      -- NULL なら全体で共有
        title      VARCHAR(200)  NOT NULL,
        url        VARCHAR(2000) NOT NULL,
        note       VARCHAR(500)  NOT NULL DEFAULT '',
        category   VARCHAR(40)   NOT NULL DEFAULT '',   -- 手順書・共有フォルダ など
        sort_order INT           NOT NULL DEFAULT 0,
        created_by INT NULL,
        created_at DATETIME      NOT NULL,
        updated_at DATETIME      NOT NULL,
        KEY idx_links_project (project_id, sort_order),
        CONSTRAINT fk_link_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
        CONSTRAINT fk_link_user FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS company_holidays (
        day        DATE NOT NULL PRIMARY KEY,
        name       VARCHAR(100) NOT NULL DEFAULT '休業日',
        created_at DATETIME NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS notification_mutes (
        user_id    INT NOT NULL,
        project_id INT NOT NULL,
        PRIMARY KEY (user_id, project_id),
        CONSTRAINT fk_mute_user    FOREIGN KEY (user_id)    REFERENCES users(id)    ON DELETE CASCADE,
        CONSTRAINT fk_mute_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]

log = logging.getLogger("tm.db")

DEFAULT_SETTINGS = {
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_user": "",
    "smtp_password": "",
    "smtp_tls": "1",
    "mail_from": "task-manager@example.com",
    "app_base_url": "http://localhost:8080",
    "daily_digest_time": "09:00",
    "daily_digest_enabled": "1",
    "due_soon_days": "3",
    "email_enabled": "0",
    "ui_accent_default": "#3b6ef5",
    "llm_enabled": "0",
    "llm_api_key": "",
    "llm_model": "claude-opus-5",
    "slack_enabled": "0",
    "slack_webhook_url": "",
    "slack_events": "issue,digest",
    "use_holidays": "1",
    "work_hours_per_day": "8",
    "app_name": "タスク管理",
}


def now():
    return datetime.now().replace(microsecond=0)


def today():
    return date.today()


def connect():
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = pymysql.connect(
            host=DB["host"], port=DB["port"], user=DB["user"],
            password=DB["password"], database=DB["database"],
            charset=DB["charset"], cursorclass=DictCursor, autocommit=True,
        )
        _local.conn = conn
    else:
        try:
            conn.ping()          # raises when the server dropped the connection
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            _local.conn = None
            return connect()     # reconnect with a fresh connection
    return conn


def close_thread_connection():
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        finally:
            _local.conn = None


def query(sql, params=()):
    with connect().cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def query_one(sql, params=()):
    with connect().cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def scalar(sql, params=(), default=None):
    row = query_one(sql, params)
    if not row:
        return default
    return next(iter(row.values()))


def execute(sql, params=()):
    with connect().cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def insert(sql, params=()):
    with connect().cursor() as cur:
        cur.execute(sql, params)
        return cur.lastrowid


def executemany(sql, seq):
    if not seq:
        return 0
    with connect().cursor() as cur:
        cur.executemany(sql, seq)
        return cur.rowcount


class transaction:
    """`with db.transaction():` — commits on success, rolls back on error."""

    def __enter__(self):
        self.conn = connect()
        self.conn.begin()
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        return False


def jsonable(value):
    """Convert driver types (datetime/date/Decimal/bytes) into JSON values."""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def get_setting(key, default=""):
    row = query_one("SELECT setting_value FROM settings WHERE setting_key=%s", (key,))
    if row:
        return row["setting_value"]
    return DEFAULT_SETTINGS.get(key, default)


def all_settings():
    out = dict(DEFAULT_SETTINGS)
    for r in query("SELECT setting_key, setting_value FROM settings"):
        out[r["setting_key"]] = r["setting_value"]
    return out


def set_setting(key, value):
    execute(
        "INSERT INTO settings(setting_key, setting_value) VALUES(%s,%s) "
        "ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value)",
        (key, str(value)),
    )


# Columns added after the first release.  Applied on every start-up.
MIGRATIONS = [
    ("tasks", "category",
     "ALTER TABLE tasks ADD COLUMN category VARCHAR(20) NOT NULL DEFAULT '' AFTER description"),
    ("users", "ui_theme",
     "ALTER TABLE users ADD COLUMN ui_theme VARCHAR(10) NOT NULL DEFAULT 'auto'"),
    ("users", "ui_accent",
     "ALTER TABLE users ADD COLUMN ui_accent VARCHAR(20) NOT NULL DEFAULT ''"),
    ("tasks", "estimate_hours",
     "ALTER TABLE tasks ADD COLUMN estimate_hours DECIMAL(6,1) NULL AFTER progress"),
    ("tasks", "actual_hours",
     "ALTER TABLE tasks ADD COLUMN actual_hours DECIMAL(6,1) NOT NULL DEFAULT 0 AFTER estimate_hours"),
    ("projects", "slack_webhook_url",
     "ALTER TABLE projects ADD COLUMN slack_webhook_url VARCHAR(300) NOT NULL DEFAULT ''"),
    ("comments", "issue_id", "ALTER TABLE comments ADD COLUMN issue_id INT NULL AFTER task_id"),
    ("users", "notify_assigned",
     "ALTER TABLE users ADD COLUMN notify_assigned TINYINT(1) NOT NULL DEFAULT 1"),
    ("users", "notify_comment",
     "ALTER TABLE users ADD COLUMN notify_comment TINYINT(1) NOT NULL DEFAULT 1"),
    ("users", "notify_due",
     "ALTER TABLE users ADD COLUMN notify_due TINYINT(1) NOT NULL DEFAULT 1"),
    ("users", "notify_digest",
     "ALTER TABLE users ADD COLUMN notify_digest TINYINT(1) NOT NULL DEFAULT 1"),
    ("users", "notify_mention",
     "ALTER TABLE users ADD COLUMN notify_mention TINYINT(1) NOT NULL DEFAULT 1"),
    ("tasks", "marker",
     "ALTER TABLE tasks ADD COLUMN marker VARCHAR(10) NOT NULL DEFAULT ''"),
    ("shared_links", "category",
     "ALTER TABLE shared_links ADD COLUMN category VARCHAR(40) NOT NULL DEFAULT ''"),
    ("projects", "notify_enabled",
     "ALTER TABLE projects ADD COLUMN notify_enabled TINYINT(1) NOT NULL DEFAULT 1"),
    ("projects", "slack_events",
     "ALTER TABLE projects ADD COLUMN slack_events VARCHAR(120) NOT NULL DEFAULT ''"),
    ("attachments", "issue_id",
     "ALTER TABLE attachments ADD COLUMN issue_id INT NULL AFTER task_id"),
    # チケットはあとから足した機能なので、既存 DB にも列を足す
    ("comments", "ticket_id",
     "ALTER TABLE comments ADD COLUMN ticket_id INT NULL AFTER issue_id"),
    ("attachments", "ticket_id",
     "ALTER TABLE attachments ADD COLUMN ticket_id INT NULL AFTER issue_id"),
    ("ticket_queues", "project_id",
     "ALTER TABLE ticket_queues ADD COLUMN project_id INT NULL AFTER icon"),
    ("ticket_queues", "default_kind",
     "ALTER TABLE ticket_queues ADD COLUMN default_kind VARCHAR(20) NOT NULL "
     "DEFAULT 'request' AFTER project_id"),
    ("tickets", "category_id",
     "ALTER TABLE tickets ADD COLUMN category_id INT NULL AFTER kind"),
    ("tickets", "spent_hours",
     "ALTER TABLE tickets ADD COLUMN spent_hours DECIMAL(6,1) NULL AFTER resolved_at"),
]

MIGRATION_INDEXES = [
    ("tasks", "idx_tasks_category", "ALTER TABLE tasks ADD KEY idx_tasks_category (category)"),
    ("comments", "idx_comments_issue", "ALTER TABLE comments ADD KEY idx_comments_issue (issue_id)"),
    ("attachments", "idx_attachments_issue",
     "ALTER TABLE attachments ADD KEY idx_attachments_issue (issue_id)"),
    ("comments", "idx_comments_ticket",
     "ALTER TABLE comments ADD KEY idx_comments_ticket (ticket_id)"),
    ("attachments", "idx_attachments_ticket",
     "ALTER TABLE attachments ADD KEY idx_attachments_ticket (ticket_id)"),
    ("ticket_queues", "idx_queue_project",
     "ALTER TABLE ticket_queues ADD KEY idx_queue_project (project_id)"),
    ("tickets", "idx_tickets_category",
     "ALTER TABLE tickets ADD KEY idx_tickets_category (category_id)"),
    ("tickets", "idx_tickets_created",
     "ALTER TABLE tickets ADD KEY idx_tickets_created (created_at)"),
    ("tickets", "idx_tickets_resolved",
     "ALTER TABLE tickets ADD KEY idx_tickets_resolved (resolved_at)"),
]

# Comments and attachments originally belonged to a task only; issues reuse them.
NULLABLE_COLUMNS = [
    ("comments", "task_id", "ALTER TABLE comments MODIFY COLUMN task_id INT NULL"),
    ("attachments", "task_id", "ALTER TABLE attachments MODIFY COLUMN task_id INT NULL"),
]

MIGRATION_FKS = [
    ("comments", "fk_comment_issue",
     "ALTER TABLE comments ADD CONSTRAINT fk_comment_issue "
     "FOREIGN KEY (issue_id) REFERENCES issues(id) ON DELETE CASCADE"),
    ("attachments", "fk_att_issue",
     "ALTER TABLE attachments ADD CONSTRAINT fk_att_issue "
     "FOREIGN KEY (issue_id) REFERENCES issues(id) ON DELETE CASCADE"),
    ("comments", "fk_comment_ticket",
     "ALTER TABLE comments ADD CONSTRAINT fk_comment_ticket "
     "FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE"),
    ("attachments", "fk_att_ticket",
     "ALTER TABLE attachments ADD CONSTRAINT fk_att_ticket "
     "FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE"),
    ("ticket_queues", "fk_queue_project",
     "ALTER TABLE ticket_queues ADD CONSTRAINT fk_queue_project "
     "FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL"),
    ("tickets", "fk_ticket_category",
     "ALTER TABLE tickets ADD CONSTRAINT fk_ticket_category "
     "FOREIGN KEY (category_id) REFERENCES ticket_categories(id) ON DELETE SET NULL"),
]


def column_exists(table, column):
    return query_one(
        "SELECT 1 AS x FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (table, column),
    ) is not None


def column_is_nullable(table, column):
    row = query_one(
        "SELECT IS_NULLABLE AS n FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (table, column),
    )
    return bool(row) and row["n"] == "YES"


def constraint_exists(table, name):
    return query_one(
        "SELECT 1 AS x FROM information_schema.TABLE_CONSTRAINTS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND CONSTRAINT_NAME = %s",
        (table, name),
    ) is not None


def index_exists(table, index):
    return query_one(
        "SELECT 1 AS x FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s",
        (table, index),
    ) is not None


def run_migrations():
    """Bring an existing database up to the current schema.  Idempotent."""
    applied = []
    for table, column, ddl in MIGRATIONS:
        if not column_exists(table, column):
            execute(ddl)
            applied.append("{}.{}".format(table, column))
    for table, column, ddl in NULLABLE_COLUMNS:
        if column_exists(table, column) and not column_is_nullable(table, column):
            execute(ddl)
            applied.append("{}.{} -> NULL".format(table, column))
    for table, index, ddl in MIGRATION_INDEXES:
        if not index_exists(table, index):
            execute(ddl)
            applied.append("{}:{}".format(table, index))
    for table, name, ddl in MIGRATION_FKS:
        if not constraint_exists(table, name):
            execute(ddl)
            applied.append("{}:{}".format(table, name))
    return applied


def init_db():
    """Create the schema (idempotent), migrate it and seed default settings.

    起動のたびに走る。既存のデータベースはここで最新のスキーマへ追いつくので、
    更新の手順は「git pull して再起動」だけで済む。
    """
    conn = connect()
    before = set(existing_tables())
    with conn.cursor() as cur:
        for stmt in DDL:
            cur.execute(stmt)
    conn.commit()
    created = sorted(set(existing_tables()) - before)
    applied = run_migrations()
    added_settings = []
    for k, v in DEFAULT_SETTINGS.items():
        if query_one("SELECT 1 FROM settings WHERE setting_key=%s", (k,)) is None:
            set_setting(k, v)
            added_settings.append(k)
    from . import taxonomy, tickets  # 循環 import を避けるため、ここで取り込む
    taxonomy.seed()
    tickets.seed()
    # 何が変わったのかは残しておく（黙って直っていると、後で追えなくなる）
    if created:
        log.info("テーブルを作成しました: %s", ", ".join(created))
    if applied:
        log.info("スキーマを移行しました (%d 件): %s", len(applied), ", ".join(applied))
    if added_settings:
        log.info("設定の既定値を追加しました: %s", ", ".join(added_settings))
    return {"created_tables": created, "migrations": applied, "settings": added_settings}


def existing_tables():
    return [r["t"] for r in query(
        "SELECT TABLE_NAME AS t FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE()")]


def server_version():
    return scalar("SELECT VERSION() AS v", default="unknown")
