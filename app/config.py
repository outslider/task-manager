"""Application configuration.

Values come from environment variables first, then config.ini next to the
project root, then built-in defaults.  Nothing here is required to run with a
local development MariaDB.
"""
import configparser
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
STATIC_DIR = os.path.join(BASE_DIR, "static")
CONFIG_FILE = os.environ.get("TM_CONFIG", os.path.join(BASE_DIR, "config.ini"))

_ini = configparser.ConfigParser()
if os.path.exists(CONFIG_FILE):
    _ini.read(CONFIG_FILE, encoding="utf-8")


def _get(section, key, env, default):
    if env in os.environ:
        return os.environ[env]
    if _ini.has_option(section, key):
        return _ini.get(section, key)
    return default


DB = {
    "host": _get("database", "host", "TM_DB_HOST", "127.0.0.1"),
    "port": int(_get("database", "port", "TM_DB_PORT", "3306")),
    "user": _get("database", "user", "TM_DB_USER", "tmapp"),
    "password": _get("database", "password", "TM_DB_PASSWORD", ""),
    "database": _get("database", "name", "TM_DB_NAME", "task_manager"),
    "charset": "utf8mb4",
}

SERVER = {
    "host": _get("server", "host", "TM_HOST", "0.0.0.0"),
    "port": int(_get("server", "port", "TM_PORT", "8080")),
}

def _normalize_base_path(value):
    """'' | '/tasks' — leading slash, no trailing slash."""
    value = (value or "").strip().strip("/")
    return "/" + value if value else ""


# サブディレクトリ配下で公開する場合のパス（例 /tasks）。
# リバースプロキシがプレフィックスを削らない構成のときに指定します。
BASE_PATH = _normalize_base_path(_get("server", "base_path", "TM_BASE_PATH", ""))

# 通常は base_path と同じ。プロキシ側がプレフィックスを削る構成（アプリはルートで動くが、
# ブラウザからはサブディレクトリに見える）のときだけ、ここでクッキーの範囲を絞れます。
COOKIE_PATH = (_normalize_base_path(_get("server", "cookie_path", "TM_COOKIE_PATH", ""))
               or BASE_PATH or "/")

# 25 MB per uploaded file by default.
MAX_UPLOAD_BYTES = int(_get("server", "max_upload_bytes", "TM_MAX_UPLOAD", str(25 * 1024 * 1024)))
SECURE_COOKIE = _get("server", "secure_cookie", "TM_SECURE_COOKIE", "0") == "1"
