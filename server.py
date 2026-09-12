#!/usr/bin/env python3
"""Task manager — HTTP server entry point.

    python server.py                 # start on http://0.0.0.0:8080
    python server.py --init-db       # create the schema and the first admin
    python server.py --seed-demo     # add demo data (development only)
    python server.py --run-digest    # send the daily digest once and exit
"""
import argparse
import logging
import mimetypes
import os
import posixpath
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from app import api, auth, db, notify
from app.config import DB, MAX_UPLOAD_BYTES, SECURE_COOKIE, SERVER, STATIC_DIR
from app.http_util import (HttpError, Response, error_response, parse_cookies,
                           parse_json_body, parse_multipart)

log = logging.getLogger("tm")

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".webmanifest": "application/manifest+json",
}


class Context:
    def __init__(self, method, path, query, body, files, user, session_token):
        self.method = method
        self.path = path
        self.query = query
        self.body = body
        self.files = files
        self.user = user
        self.session_token = session_token
        self.secure_cookie = SECURE_COOKIE


class Handler(BaseHTTPRequestHandler):
    server_version = "TaskManager/1.0"
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    def _send(self, response: Response):
        body = response.body if isinstance(response.body, bytes) else str(response.body).encode()
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        for key, value in response.headers:
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        if length > MAX_UPLOAD_BYTES + 1024 * 1024:
            raise HttpError(413, "リクエストが大きすぎます")
        return self.rfile.read(length)

    def _same_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True  # non-browser client (curl, scripts)
        host = self.headers.get("Host", "")
        return urlparse(origin).netloc == host

    # -- verbs ------------------------------------------------------------
    def do_GET(self):
        self._handle()

    def do_HEAD(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_PUT(self):
        self._handle()

    def do_PATCH(self):
        self._handle()

    def do_DELETE(self):
        self._handle()

    def _handle(self):
        try:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if path.startswith("/api/"):
                self._send(self._handle_api(path, parsed.query))
            else:
                self._send(self._handle_static(path))
        except HttpError as exc:
            self._send(error_response(exc.status, exc.message, exc.detail))
        except BrokenPipeError:
            pass
        except Exception:
            log.exception("unhandled error on %s %s", self.command, self.path)
            self._send(error_response(500, "サーバー内部エラーが発生しました"))

    def _handle_api(self, path, raw_query):
        raw = self._read_body()
        content_type = self.headers.get("Content-Type", "")
        body, files = {}, {}
        if raw:
            if content_type.startswith("multipart/form-data"):
                body, files = parse_multipart(raw, content_type)
            elif content_type.startswith("application/x-www-form-urlencoded"):
                body = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
            else:
                body = parse_json_body(raw)
        if self.command not in SAFE_METHODS and not self._same_origin():
            raise HttpError(403, "オリジンが一致しません")

        cookies = parse_cookies(self.headers.get("Cookie"))
        token = cookies.get(auth.SESSION_COOKIE)
        user = auth.user_for_token(token)
        query = {k: v[0] for k, v in parse_qs(raw_query).items()}
        ctx = Context(self.command, path.rstrip("/") or path, query, body, files, user, token)
        return api.dispatch(ctx)

    def _handle_static(self, path):
        if path in ("/", "/index.html") or not posixpath.splitext(path)[1]:
            return self._file_response(os.path.join(STATIC_DIR, "index.html"), cache=False)
        rel = posixpath.normpath(path).lstrip("/")
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(os.path.abspath(STATIC_DIR)):
            raise HttpError(403, "アクセスできません")
        if not os.path.isfile(full):
            raise HttpError(404, "ページが見つかりません")
        return self._file_response(full)

    def _file_response(self, full_path, cache=True):
        if not os.path.isfile(full_path):
            raise HttpError(404, "ページが見つかりません")
        ext = os.path.splitext(full_path)[1].lower()
        ctype = STATIC_TYPES.get(ext) or mimetypes.guess_type(full_path)[0] \
            or "application/octet-stream"
        with open(full_path, "rb") as fh:
            data = fh.read()
        headers = [("Cache-Control", "public, max-age=300" if cache else "no-store")]
        return Response(200, data, ctype, headers)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            db.close_thread_connection()


# --------------------------------------------------------------------------

def seed_demo():
    """Insert a small demo dataset (safe to skip in production)."""
    from datetime import date, timedelta
    if db.scalar("SELECT COUNT(*) AS c FROM projects", default=0):
        print("既にプロジェクトが存在するため、デモデータは投入しません。")
        return
    today = date.today()
    users = [
        ("sato@example.com", "佐藤 花子", "member", "#f6a623"),
        ("suzuki@example.com", "鈴木 一郎", "member", "#2ec4b6"),
        ("takahashi@example.com", "高橋 美咲", "member", "#e56399"),
    ]
    ids = {}
    for email, name, role, color in users:
        row = db.query_one("SELECT id FROM users WHERE email=%s", (email,))
        if row:
            ids[email] = row["id"]
            continue
        ids[email] = db.insert(
            "INSERT INTO users(email, name, password_hash, role, avatar_color, created_at) "
            "VALUES(%s,%s,%s,%s,%s,%s)",
            (email, name, auth.hash_password("password123"), role, color, db.now()))
    admin_id = db.scalar("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1")

    group_id = db.insert(
        "INSERT INTO user_groups(name, description, created_at) VALUES(%s,%s,%s)",
        ("開発チーム", "プロダクト開発メンバー", db.now()))
    for uid in ids.values():
        db.execute("INSERT IGNORE INTO group_members(group_id, user_id) VALUES(%s,%s)",
                   (group_id, uid))

    project_id = db.insert(
        "INSERT INTO projects(name, description, color, owner_id, created_at) "
        "VALUES(%s,%s,%s,%s,%s)",
        ("新製品リリース", "2026年度 新製品の企画から公開まで", "#4f8cff", admin_id, db.now()))
    db.execute("INSERT INTO project_members(project_id, principal_type, principal_id, role) "
               "VALUES(%s,'group',%s,'editor')", (project_id, group_id))

    def add(title, parent, start, due, assignee, status, progress,
            milestone=0, order=0, category="", priority=1):
        return db.insert(
            "INSERT INTO tasks(project_id, parent_id, title, description, category, status, "
            "priority, assignee_id, start_date, due_date, progress, is_milestone, sort_order, "
            "created_by, created_at, updated_at) "
            "VALUES(%s,%s,%s,'',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (project_id, parent, title, category, status, priority, assignee,
             today + timedelta(days=start) if start is not None else None,
             today + timedelta(days=due) if due is not None else None,
             progress, milestone, order, admin_id, db.now(), db.now()))

    def depends(task_id, *predecessors):
        for pred in predecessors:
            db.execute("INSERT IGNORE INTO task_deps(task_id, depends_on_id) VALUES(%s,%s)",
                       (task_id, pred))

    plan = add("企画フェーズ", None, -30, -10, admin_id, "done", 100, 0, 10, "design", 2)
    research = add("市場調査", plan, -30, -22, ids["sato@example.com"], "done", 100, 0, 20,
                   "research")
    spec = add("要件定義書の作成", plan, -21, -10, ids["suzuki@example.com"], "done", 100, 0, 30,
               "docs", 2)
    dev = add("開発フェーズ", None, -9, 20, admin_id, "doing", 45, 0, 40, "build", 2)
    be = add("バックエンド実装", dev, -9, 10, ids["suzuki@example.com"], "doing", 60, 0, 50,
             "build")
    api_design = add("API 設計", be, -9, -3, ids["suzuki@example.com"], "done", 100, 0, 60,
                     "design")
    schema = add("DB スキーマ実装", be, -3, 4, ids["suzuki@example.com"], "doing", 50, 0, 70,
                 "build", 3)
    authz = add("認証・権限まわり", be, 2, 10, ids["takahashi@example.com"], "todo", 0, 0, 80,
                "build", 2)
    fe = add("フロントエンド実装", dev, -5, 18, ids["sato@example.com"], "doing", 30, 0, 90,
             "build")
    design = add("画面デザイン", fe, -5, -1, ids["sato@example.com"], "done", 100, 0, 100,
                 "design")
    gantt = add("ガントチャート画面", fe, 0, 12, ids["sato@example.com"], "doing", 40, 0, 110,
                "build")
    mobile = add("スマホ対応", fe, 8, 18, ids["takahashi@example.com"], "todo", 0, 0, 120, "build")
    test = add("結合テスト", None, 18, 28, ids["takahashi@example.com"], "todo", 0, 0, 130,
               "build", 2)
    review = add("社内レビュー完了", None, None, 5, admin_id, "todo", 0, 1, 135, "meeting", 2)
    release = add("リリース", None, None, 30, admin_id, "todo", 0, 1, 140, "admin", 3)
    overdue = add("旧システムのデータ移行", None, -14, -2, ids["sato@example.com"], "doing", 70,
                  0, 150, "build", 3)
    add("キックオフ会議", None, -32, -32, admin_id, "done", 100, 0, 5, "meeting")
    add("サーバー障害の一次対応", None, -6, -5, ids["suzuki@example.com"], "done", 100, 0, 155,
        "incident", 3)
    add("経費稟議書の提出", None, 3, 9, admin_id, "todo", 0, 0, 160, "admin")

    # 依存関係（先行タスク → 後続タスク）
    depends(spec, research)
    depends(api_design, spec)
    depends(schema, api_design)
    depends(authz, schema)
    depends(gantt, design, schema)
    depends(mobile, gantt)
    depends(test, authz, mobile, overdue)
    depends(review, test)
    depends(release, review)
    db.insert("INSERT INTO comments(task_id, user_id, body, kind, created_at) VALUES(%s,%s,%s,%s,%s)",
              (overdue, ids["sato@example.com"], "移行スクリプトの検証中です。あと2日ほどかかります。",
               "comment", db.now()))
    def add_issue(seq, title, description, category, status, severity, owner, raised, due,
                  resolution="", resolved=None, tasks=()):
        issue_id = db.insert(
            "INSERT INTO issues(project_id, seq, title, description, category, status, severity, "
            "owner_id, raised_by, raised_on, due_date, resolved_on, resolution, "
            "created_at, updated_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (project_id, seq, title, description, category, status, severity, owner, admin_id,
             today + timedelta(days=raised),
             today + timedelta(days=due) if due is not None else None,
             today + timedelta(days=resolved) if resolved is not None else None,
             resolution, db.now(), db.now()))
        for task_id in tasks:
            db.execute("INSERT IGNORE INTO issue_tasks(issue_id, task_id) VALUES(%s,%s)",
                       (issue_id, task_id))
        return issue_id

    blocked = add_issue(
        1, "本番DBの接続情報が未共有", "情報システム部から本番DBの接続情報がまだ届いていない。"
        "データ移行の検証が進められない。", "external", "doing", 3,
        ids["sato@example.com"], -8, 2,
        "情シスに再依頼中。9/15 までに回答がなければ部長からエスカレーション。",
        tasks=[overdue, schema])
    add_issue(
        2, "権限モデルの仕様が未確定", "部署をまたぐ閲覧権限をどこまで許可するか未決。"
        "認証まわりの実装が着手できない。", "spec", "open", 2,
        ids["takahashi@example.com"], -5, 5,
        "次回の定例で決裁を取る。", tasks=[authz])
    add_issue(
        3, "スマホ実機の検証端末が足りない", "iOS の実機が1台しかなく、並行検証ができない。",
        "resource", "pending", 1, ids["takahashi@example.com"], -3, 12,
        "総務に貸出申請中。", tasks=[mobile])
    add_issue(
        4, "ガント出力の解像度が粗いとの指摘", "レビューで、PowerPoint に貼ると文字がつぶれる"
        "との指摘があった。", "quality", "resolved", 2, ids["sato@example.com"], -10, -4,
        "2倍解像度での書き出しオプションを追加して解消。", resolved=-4, tasks=[gantt])
    add_issue(
        5, "追加要望によるスケジュール圧迫", "営業部から追加要望が2件あり、"
        "現行スケジュールでは吸収できない。", "schedule", "open", 3, admin_id, -2, 7,
        "スコープを次期リリースに分割する案で調整中。", tasks=[test, release])

    print("デモデータを投入しました（パスワードはいずれも password123）。")


def main():
    parser = argparse.ArgumentParser(description="タスク管理システム")
    parser.add_argument("--host", default=SERVER["host"])
    parser.add_argument("--port", type=int, default=SERVER["port"])
    parser.add_argument("--init-db", action="store_true", help="スキーマ作成のみ行う")
    parser.add_argument("--seed-demo", action="store_true", help="デモデータを投入する")
    parser.add_argument("--run-digest", action="store_true", help="日次サマリを一度送って終了")
    parser.add_argument("--no-scheduler", action="store_true", help="日次バッチを起動しない")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s: %(message)s")

    try:
        db.init_db()
    except Exception as exc:
        print("データベースに接続できません: {}".format(exc), file=sys.stderr)
        print("config.ini または TM_DB_* 環境変数を確認してください。", file=sys.stderr)
        return 1

    created = auth.ensure_bootstrap_admin()
    if created:
        print("=" * 62)
        print(" 初期管理者アカウントを作成しました")
        print("   メール    : {}".format(created["email"]))
        print("   パスワード: {}".format(created["password"]))
        print(" ログイン後に必ずパスワードを変更してください。")
        print("=" * 62)

    if args.seed_demo:
        seed_demo()
    if args.init_db:
        print("スキーマを初期化しました（MariaDB/MySQL: {}）".format(db.server_version()))
        return 0
    if args.run_digest:
        print(notify.run_daily_digest(force=True))
        return 0

    auth.purge_expired_sessions()
    if not args.no_scheduler:
        notify.start_scheduler()

    httpd = Server((args.host, args.port), Handler)
    print("タスク管理システムを起動しました: http://{}:{}/".format(
        "localhost" if args.host in ("0.0.0.0", "") else args.host, args.port))
    print("DB: {user}@{host}:{port}/{database} ({version})".format(
        version=db.server_version(), **DB))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
