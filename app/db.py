"""MySQL / MariaDB storage layer (PyMySQL driver).

One connection per thread, reconnecting automatically.  All SQL in the code
base uses %s placeholders.
"""
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
        email_notify  TINYINT(1)   NOT NULL DEFAULT 1,
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
        user_id    INT NULL,
        body       TEXT NOT NULL,
        kind       VARCHAR(20) NOT NULL DEFAULT 'comment',   -- comment | system | checkin
        created_at DATETIME NOT NULL,
        KEY idx_comments_task (task_id),
        KEY idx_comments_issue (issue_id),
        CONSTRAINT fk_comment_task  FOREIGN KEY (task_id)  REFERENCES tasks(id)  ON DELETE CASCADE,
        CONSTRAINT fk_comment_issue FOREIGN KEY (issue_id) REFERENCES issues(id) ON DELETE CASCADE,
        CONSTRAINT fk_comment_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS attachments (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        task_id     INT NULL,
        issue_id    INT NULL,
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
        CONSTRAINT fk_att_task  FOREIGN KEY (task_id)  REFERENCES tasks(id)  ON DELETE CASCADE,
        CONSTRAINT fk_att_issue FOREIGN KEY (issue_id) REFERENCES issues(id) ON DELETE CASCADE,
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
]

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
    ("attachments", "issue_id",
     "ALTER TABLE attachments ADD COLUMN issue_id INT NULL AFTER task_id"),
]

MIGRATION_INDEXES = [
    ("tasks", "idx_tasks_category", "ALTER TABLE tasks ADD KEY idx_tasks_category (category)"),
    ("comments", "idx_comments_issue", "ALTER TABLE comments ADD KEY idx_comments_issue (issue_id)"),
    ("attachments", "idx_attachments_issue",
     "ALTER TABLE attachments ADD KEY idx_attachments_issue (issue_id)"),
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
    """Create the schema (idempotent), migrate it and seed default settings."""
    conn = connect()
    with conn.cursor() as cur:
        for stmt in DDL:
            cur.execute(stmt)
    conn.commit()
    run_migrations()
    for k, v in DEFAULT_SETTINGS.items():
        if query_one("SELECT 1 FROM settings WHERE setting_key=%s", (k,)) is None:
            set_setting(k, v)


def server_version():
    return scalar("SELECT VERSION() AS v", default="unknown")
