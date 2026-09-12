"""Integration tests for the task manager API.

Runs against a real MariaDB/MySQL database (TM_TEST_DB_NAME, default
``task_manager_test``) and a server started in-process on a free port.

    python -m unittest discover -s tests -v
"""
import json
import os
import socket
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.cookiejar import CookieJar

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Tests talk plain HTTP to a throwaway database, so they must not pick up the
# deployment's config.ini (secure cookies, real DB name, ...).
os.environ.setdefault("TM_CONFIG", os.path.join(os.path.dirname(__file__), "test-config.ini"))
os.environ.setdefault("TM_DB_NAME", os.environ.get("TM_TEST_DB_NAME", "task_manager_test"))
os.environ.setdefault("TM_DB_USER", "tmapp")
os.environ.setdefault("TM_DB_PASSWORD", "tmapp_dev_pw")
os.environ.setdefault("TM_SECURE_COOKIE", "0")
os.environ.setdefault("TM_ADMIN_EMAIL", "admin@test.local")
os.environ.setdefault("TM_ADMIN_PASSWORD", "admin-test-pw")

from app import auth, config, db, http_util, notify  # noqa: E402
import server as server_module  # noqa: E402

ADMIN = ("admin@test.local", "admin-test-pw")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def reset_database():
    conn = db.connect()
    with conn.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS=0")
        cur.execute("SHOW TABLES")
        for row in cur.fetchall():
            cur.execute("DROP TABLE IF EXISTS `{}`".format(next(iter(row.values()))))
        cur.execute("SET FOREIGN_KEY_CHECKS=1")
    conn.commit()
    db.init_db()
    auth.ensure_bootstrap_admin()


class Client:
    """Minimal cookie-aware HTTP client."""

    def __init__(self, base):
        self.base = base
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, method, path, body=None, raw_body=None, content_type=None):
        url = self.base + urllib.parse.quote(path, safe="/?&=%")
        data = None
        headers = {}
        if raw_body is not None:
            data = raw_body
            headers["Content-Type"] = content_type
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=20) as response:
                payload = response.read()
                if response.headers.get("Content-Type", "").startswith("application/json"):
                    return response.status, json.loads(payload or b"{}")
                return response.status, payload
        except urllib.error.HTTPError as error:
            payload = error.read()
            try:
                return error.code, json.loads(payload or b"{}")
            except ValueError:
                return error.code, {"error": payload.decode("utf-8", "replace")}

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, body=None):
        return self.request("POST", path, body if body is not None else {})

    def patch(self, path, body):
        return self.request("PATCH", path, body)

    def put(self, path, body):
        return self.request("PUT", path, body)

    def delete(self, path):
        return self.request("DELETE", path)

    def login(self, email, password):
        status, data = self.post("/api/auth/login", {"email": email, "password": password})
        assert status == 200, data
        return data["user"]


class ApiTestCase(unittest.TestCase):
    server = None
    thread = None
    base = None

    @classmethod
    def setUpClass(cls):
        db.init_db()
        reset_database()
        port = free_port()
        cls.base = "http://127.0.0.1:{}".format(port)
        cls.server = server_module.Server(("127.0.0.1", port), server_module.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.admin = Client(self.base)
        self.admin.login(*ADMIN)

    # -- helpers ---------------------------------------------------------
    def make_user(self, name="テスト太郎", role="member"):
        email = "u{}@test.local".format(uuid.uuid4().hex[:8])
        status, data = self.admin.post("/api/users", {
            "name": name, "email": email, "role": role, "password": "userpassword",
        })
        self.assertEqual(status, 201, data)
        return data["user"], email

    def client_for(self, email, password="userpassword"):
        client = Client(self.base)
        client.login(email, password)
        return client

    def make_project(self, name="テストPJ"):
        status, data = self.admin.post("/api/projects", {"name": name})
        self.assertEqual(status, 201, data)
        return data["project"]

    def make_issue(self, project_id, title="課題", **kwargs):
        payload = {"project_id": project_id, "title": title}
        payload.update(kwargs)
        status, data = self.admin.post("/api/issues", payload)
        self.assertEqual(status, 201, data)
        return data["issue"]

    def make_task(self, project_id, title="タスク", **kwargs):
        payload = {"project_id": project_id, "title": title}
        payload.update(kwargs)
        status, data = self.admin.post("/api/tasks", payload)
        self.assertEqual(status, 201, data)
        return data["task"]


class TestAuth(ApiTestCase):
    def test_login_rejects_bad_password(self):
        client = Client(self.base)
        status, data = client.post("/api/auth/login",
                                   {"email": ADMIN[0], "password": "wrong"})
        self.assertEqual(status, 401)
        self.assertIn("パスワード", data["error"])

    def test_me_requires_no_auth_but_returns_null(self):
        status, data = Client(self.base).get("/api/auth/me")
        self.assertEqual(status, 200)
        self.assertIsNone(data["user"])

    def test_protected_endpoint_requires_login(self):
        status, _ = Client(self.base).get("/api/projects")
        self.assertEqual(status, 401)

    def test_logout_clears_the_session(self):
        client = Client(self.base)
        client.login(*ADMIN)
        self.assertEqual(client.post("/api/auth/logout")[0], 200)
        self.assertEqual(client.get("/api/projects")[0], 401)

    def test_password_change_requires_the_current_one(self):
        user, email = self.make_user()
        client = self.client_for(email)
        status, data = client.post("/api/auth/password", {
            "current_password": "nope", "new_password": "brandnewpassword"})
        self.assertEqual(status, 400, data)
        status, _ = client.post("/api/auth/password", {
            "current_password": "userpassword", "new_password": "brandnewpassword"})
        self.assertEqual(status, 200)
        self.client_for(email, "brandnewpassword")

    def test_password_minimum_length(self):
        user, email = self.make_user()
        client = self.client_for(email)
        status, _ = client.post("/api/auth/password", {
            "current_password": "userpassword", "new_password": "short"})
        self.assertEqual(status, 400)


class TestUsersAndGroups(ApiTestCase):
    def test_only_admins_may_create_users(self):
        _, email = self.make_user()
        status, _ = self.client_for(email).post("/api/users", {
            "name": "x", "email": "x@test.local", "password": "password123"})
        self.assertEqual(status, 403)

    def test_duplicate_email_is_rejected(self):
        user, email = self.make_user()
        status, data = self.admin.post("/api/users", {
            "name": "別人", "email": email, "password": "password123"})
        self.assertEqual(status, 400)
        self.assertIn("既に登録", data["error"])

    def test_cannot_remove_the_last_admin(self):
        admin_id = self.admin.get("/api/auth/me")[1]["user"]["id"]
        status, data = self.admin.patch("/api/users/{}".format(admin_id), {"role": "member"})
        self.assertEqual(status, 400, data)
        status, data = self.admin.patch("/api/users/{}".format(admin_id), {"is_active": False})
        self.assertEqual(status, 400, data)

    def test_group_membership_round_trip(self):
        user, _ = self.make_user("グループ員")
        status, data = self.admin.post("/api/groups", {
            "name": "開発G-{}".format(uuid.uuid4().hex[:6]), "user_ids": [user["id"]]})
        self.assertEqual(status, 201, data)
        group_id = data["group"]["id"]
        groups = self.admin.get("/api/groups")[1]["groups"]
        group = next(g for g in groups if g["id"] == group_id)
        self.assertEqual([m["id"] for m in group["members"]], [user["id"]])
        self.admin.patch("/api/groups/{}".format(group_id), {"user_ids": []})
        groups = self.admin.get("/api/groups")[1]["groups"]
        group = next(g for g in groups if g["id"] == group_id)
        self.assertEqual(group["members"], [])


class TestProjectPermissions(ApiTestCase):
    def test_outsiders_cannot_see_a_project(self):
        project = self.make_project()
        _, email = self.make_user("部外者")
        client = self.client_for(email)
        self.assertEqual(client.get("/api/projects/{}".format(project["id"]))[0], 403)
        self.assertEqual(client.get("/api/projects")[1]["projects"], [])

    def test_viewer_can_read_but_not_write(self):
        project = self.make_project()
        user, email = self.make_user("閲覧者")
        self.admin.put("/api/projects/{}/members".format(project["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"], "role": "viewer"}]})
        client = self.client_for(email)
        self.assertEqual(client.get("/api/projects/{}".format(project["id"]))[0], 200)
        status, _ = client.post("/api/tasks", {"project_id": project["id"], "title": "だめ"})
        self.assertEqual(status, 403)

    def test_commenter_can_comment_but_not_edit(self):
        project = self.make_project()
        task = self.make_task(project["id"])
        user, email = self.make_user("コメント者")
        self.admin.put("/api/projects/{}/members".format(project["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"],
                         "role": "commenter"}]})
        client = self.client_for(email)
        self.assertEqual(
            client.post("/api/tasks/{}/comments".format(task["id"]), {"body": "確認"})[0], 201)
        self.assertEqual(
            client.patch("/api/tasks/{}".format(task["id"]), {"title": "変更"})[0], 403)

    def test_access_can_be_granted_through_a_group(self):
        project = self.make_project()
        user, email = self.make_user("グループ経由")
        group = self.admin.post("/api/groups", {
            "name": "G-{}".format(uuid.uuid4().hex[:6]), "user_ids": [user["id"]]})[1]["group"]
        self.admin.put("/api/projects/{}/members".format(project["id"]), {
            "members": [{"principal_type": "group", "principal_id": group["id"],
                         "role": "editor"}]})
        client = self.client_for(email)
        status, data = client.post("/api/tasks", {"project_id": project["id"], "title": "OK"})
        self.assertEqual(status, 201, data)

    def test_editor_cannot_change_project_settings(self):
        project = self.make_project()
        user, email = self.make_user("編集者")
        self.admin.put("/api/projects/{}/members".format(project["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"], "role": "editor"}]})
        client = self.client_for(email)
        self.assertEqual(
            client.patch("/api/projects/{}".format(project["id"]), {"name": "改名"})[0], 403)
        self.assertEqual(client.delete("/api/projects/{}".format(project["id"]))[0], 403)

    def test_owner_stays_a_member_after_replacing_the_member_list(self):
        project = self.make_project()
        self.admin.put("/api/projects/{}/members".format(project["id"]), {"members": []})
        status, data = self.admin.get("/api/projects/{}".format(project["id"]))
        self.assertEqual(status, 200)
        self.assertTrue(any(m["principal_type"] == "user" and m["role"] == "owner"
                            for m in data["project"]["members"]))


class TestTasks(ApiTestCase):
    def test_hierarchy_and_progress_rollup(self):
        project = self.make_project()
        parent = self.make_task(project["id"], "親")
        self.make_task(project["id"], "子1", parent_id=parent["id"], progress=100)
        self.make_task(project["id"], "子2", parent_id=parent["id"], progress=0)
        tasks = self.admin.get("/api/projects/{}/tasks".format(project["id"]))[1]["tasks"]
        parent_row = next(t for t in tasks if t["id"] == parent["id"])
        self.assertEqual(parent_row["child_count"], 2)
        self.assertEqual(parent_row["rollup_progress"], 50)
        self.assertEqual(parent_row["leaf_total"], 2)
        self.assertEqual(parent_row["leaf_done"], 1)

    def test_rollup_dates_span_the_children(self):
        project = self.make_project()
        parent = self.make_task(project["id"], "親")
        self.make_task(project["id"], "子1", parent_id=parent["id"],
                       start_date="2026-01-05", due_date="2026-01-10")
        self.make_task(project["id"], "子2", parent_id=parent["id"],
                       start_date="2026-01-08", due_date="2026-01-20")
        tasks = self.admin.get("/api/projects/{}/tasks".format(project["id"]))[1]["tasks"]
        parent_row = next(t for t in tasks if t["id"] == parent["id"])
        self.assertEqual(parent_row["rollup_start"], "2026-01-05")
        self.assertEqual(parent_row["rollup_due"], "2026-01-20")

    def test_progress_100_marks_the_task_done(self):
        project = self.make_project()
        task = self.make_task(project["id"])
        status, data = self.admin.patch("/api/tasks/{}".format(task["id"]), {"progress": 100})
        self.assertEqual(status, 200)
        self.assertEqual(data["task"]["status"], "done")
        self.assertIsNotNone(data["task"]["completed_at"])

    def test_marking_done_sets_progress_to_100(self):
        project = self.make_project()
        task = self.make_task(project["id"])
        data = self.admin.patch("/api/tasks/{}".format(task["id"]), {"status": "done"})[1]
        self.assertEqual(data["task"]["progress"], 100)

    def test_cannot_make_a_descendant_the_parent(self):
        project = self.make_project()
        parent = self.make_task(project["id"], "親")
        child = self.make_task(project["id"], "子", parent_id=parent["id"])
        status, data = self.admin.patch("/api/tasks/{}".format(parent["id"]),
                                        {"parent_id": child["id"]})
        self.assertEqual(status, 400)
        self.assertIn("子孫", data["error"])

    def test_depth_limit_is_enforced(self):
        project = self.make_project()
        parent_id = None
        for level in range(8):
            parent_id = self.make_task(project["id"], "L{}".format(level),
                                       parent_id=parent_id)["id"]
        status, data = self.admin.post("/api/tasks", {
            "project_id": project["id"], "title": "深すぎ", "parent_id": parent_id})
        self.assertEqual(status, 400, data)
        self.assertIn("階層", data["error"])

    def test_start_after_due_is_rejected(self):
        project = self.make_project()
        status, _ = self.admin.post("/api/tasks", {
            "project_id": project["id"], "title": "逆転",
            "start_date": "2026-03-10", "due_date": "2026-03-01"})
        self.assertEqual(status, 400)

    def test_invalid_date_format_is_rejected(self):
        project = self.make_project()
        status, data = self.admin.post("/api/tasks", {
            "project_id": project["id"], "title": "日付", "due_date": "2026/03/01"})
        self.assertEqual(status, 400, data)

    def test_deleting_a_parent_removes_descendants(self):
        project = self.make_project()
        parent = self.make_task(project["id"], "親")
        child = self.make_task(project["id"], "子", parent_id=parent["id"])
        grandchild = self.make_task(project["id"], "孫", parent_id=child["id"])
        status, data = self.admin.delete("/api/tasks/{}".format(parent["id"]))
        self.assertEqual(status, 200)
        self.assertEqual(data["deleted"], 3)
        self.assertEqual(self.admin.get("/api/tasks/{}".format(grandchild["id"]))[0], 404)

    def test_reorder_moves_and_reparents(self):
        project = self.make_project()
        first = self.make_task(project["id"], "1つ目")
        second = self.make_task(project["id"], "2つ目")
        status, _ = self.admin.post("/api/tasks/reorder", {
            "project_id": project["id"],
            "items": [{"id": second["id"], "parent_id": first["id"], "sort_order": 10}]})
        self.assertEqual(status, 200)
        tasks = self.admin.get("/api/projects/{}/tasks".format(project["id"]))[1]["tasks"]
        moved = next(t for t in tasks if t["id"] == second["id"])
        self.assertEqual(moved["parent_id"], first["id"])

    def test_reorder_rejects_a_cycle(self):
        project = self.make_project()
        a = self.make_task(project["id"], "A")
        b = self.make_task(project["id"], "B", parent_id=a["id"])
        status, data = self.admin.post("/api/tasks/reorder", {
            "project_id": project["id"],
            "items": [{"id": a["id"], "parent_id": b["id"], "sort_order": 10}]})
        self.assertEqual(status, 400, data)

    def test_dependency_cycles_are_rejected(self):
        project = self.make_project()
        a = self.make_task(project["id"], "A")
        b = self.make_task(project["id"], "B")
        self.assertEqual(
            self.admin.post("/api/tasks/{}/deps".format(b["id"]),
                            {"depends_on_id": a["id"]})[0], 200)
        status, data = self.admin.post("/api/tasks/{}/deps".format(a["id"]),
                                       {"depends_on_id": b["id"]})
        self.assertEqual(status, 400, data)
        self.assertIn("循環", data["error"])

    def test_status_change_writes_a_history_entry(self):
        project = self.make_project()
        task = self.make_task(project["id"])
        self.admin.patch("/api/tasks/{}".format(task["id"]), {"status": "doing"})
        detail = self.admin.get("/api/tasks/{}".format(task["id"]))[1]
        system = [c for c in detail["comments"] if c["kind"] == "system"]
        self.assertTrue(any("未着手" in c["body"] and "進行中" in c["body"] for c in system))

    def test_search_filters(self):
        project = self.make_project()
        self.make_task(project["id"], "検索対象のタスク")
        self.make_task(project["id"], "別のもの")
        tasks = self.admin.get("/api/tasks?q=検索対象")[1]["tasks"]
        self.assertEqual([t["title"] for t in tasks], ["検索対象のタスク"])

    def test_milestone_flag_round_trip(self):
        project = self.make_project()
        task = self.make_task(project["id"], "M", is_milestone=True, due_date="2026-05-01")
        self.assertEqual(task["is_milestone"], 1)
        tasks = self.admin.get("/api/tasks?milestone=1")[1]["tasks"]
        self.assertIn(task["id"], [t["id"] for t in tasks])


class TestCommentsAndAttachments(ApiTestCase):
    def test_comment_lifecycle(self):
        project = self.make_project()
        task = self.make_task(project["id"])
        status, data = self.admin.post("/api/tasks/{}/comments".format(task["id"]),
                                       {"body": "進捗は順調です"})
        self.assertEqual(status, 201)
        comment_id = data["comment"]["id"]
        detail = self.admin.get("/api/tasks/{}".format(task["id"]))[1]
        self.assertTrue(any(c["body"] == "進捗は順調です" for c in detail["comments"]))
        self.assertEqual(self.admin.delete("/api/comments/{}".format(comment_id))[0], 200)

    def test_users_cannot_delete_other_peoples_comments(self):
        project = self.make_project()
        task = self.make_task(project["id"])
        user, email = self.make_user("他人")
        self.admin.put("/api/projects/{}/members".format(project["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"],
                         "role": "commenter"}]})
        other = self.client_for(email)
        comment = other.post("/api/tasks/{}/comments".format(task["id"]),
                             {"body": "私のコメント"})[1]["comment"]
        second_user, second_email = self.make_user("第三者")
        self.admin.put("/api/projects/{}/members".format(project["id"]), {
            "members": [
                {"principal_type": "user", "principal_id": user["id"], "role": "commenter"},
                {"principal_type": "user", "principal_id": second_user["id"],
                 "role": "commenter"}]})
        status, _ = self.client_for(second_email).delete(
            "/api/comments/{}".format(comment["id"]))
        self.assertEqual(status, 403)

    def test_link_attachment_requires_a_scheme(self):
        project = self.make_project()
        task = self.make_task(project["id"])
        status, _ = self.admin.post("/api/tasks/{}/attachments".format(task["id"]),
                                    {"url": "example.com"})
        self.assertEqual(status, 400)
        status, data = self.admin.post("/api/tasks/{}/attachments".format(task["id"]),
                                       {"url": "https://example.com/doc", "name": "仕様書"})
        self.assertEqual(status, 201, data)
        self.assertEqual(data["attachments"][0]["kind"], "link")

    def test_file_upload_and_download(self):
        project = self.make_project()
        task = self.make_task(project["id"])
        boundary = "----tmtest"
        content = "テストの中身\n".encode("utf-8")
        body = (
            "--{b}\r\n"
            'Content-Disposition: form-data; name="file"; filename="資料 (1).txt"\r\n'
            "Content-Type: text/plain\r\n\r\n"
        ).format(b=boundary).encode("utf-8") + content + "\r\n--{b}--\r\n".format(
            b=boundary).encode("utf-8")
        status, data = self.admin.request(
            "POST", "/api/tasks/{}/attachments".format(task["id"]), raw_body=body,
            content_type="multipart/form-data; boundary={}".format(boundary))
        self.assertEqual(status, 201, data)
        attachment = data["attachments"][0]
        self.assertEqual(attachment["name"], "資料 (1).txt")
        self.assertEqual(attachment["size"], len(content))
        status, payload = self.admin.get(
            "/api/attachments/{}/download".format(attachment["id"]))
        self.assertEqual(status, 200)
        self.assertEqual(payload, content)
        self.assertEqual(self.admin.delete(
            "/api/attachments/{}".format(attachment["id"]))[0], 200)

    def test_outsiders_cannot_download_attachments(self):
        project = self.make_project()
        task = self.make_task(project["id"])
        attachment = self.admin.post("/api/tasks/{}/attachments".format(task["id"]),
                                     {"url": "https://example.com/x"})[1]["attachments"][0]
        _, email = self.make_user("部外者2")
        status, _ = self.client_for(email).get(
            "/api/attachments/{}/download".format(attachment["id"]))
        self.assertEqual(status, 403)


class TestNotificationsAndDaily(ApiTestCase):
    def test_assignment_creates_a_notification(self):
        project = self.make_project()
        user, email = self.make_user("担当者")
        self.admin.put("/api/projects/{}/members".format(project["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"],
                         "role": "editor"}]})
        self.make_task(project["id"], "割当タスク", assignee_id=user["id"])
        client = self.client_for(email)
        data = client.get("/api/notifications")[1]
        self.assertTrue(any(n["type"] == "assigned" for n in data["notifications"]))
        self.assertGreaterEqual(data["unread"], 1)

    def test_marking_notifications_read(self):
        project = self.make_project()
        user, email = self.make_user("既読テスト")
        self.admin.put("/api/projects/{}/members".format(project["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"],
                         "role": "editor"}]})
        self.make_task(project["id"], "通知タスク", assignee_id=user["id"])
        client = self.client_for(email)
        self.assertEqual(client.post("/api/notifications/read", {"all": True})[1]["unread"], 0)

    def test_overdue_scan_is_idempotent(self):
        project = self.make_project()
        user, _ = self.make_user("超過担当")
        self.admin.put("/api/projects/{}/members".format(project["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"],
                         "role": "editor"}]})
        self.make_task(project["id"], "遅れているタスク",
                       assignee_id=user["id"], due_date="2020-01-01")
        first = notify.scan_due_tasks()
        second = notify.scan_due_tasks()
        self.assertGreaterEqual(first, 1)
        self.assertEqual(second, 0, "同じ日に同じ通知を重複作成しないこと")

    def test_daily_buckets_and_batch_update(self):
        project = self.make_project()
        admin_id = self.admin.get("/api/auth/me")[1]["user"]["id"]
        overdue = self.make_task(project["id"], "超過", assignee_id=admin_id,
                                 due_date="2020-01-01")
        daily = self.admin.get("/api/daily")[1]
        self.assertIn(overdue["id"], [t["id"] for t in daily["buckets"]["overdue"]])

        status, data = self.admin.post("/api/daily/update", {
            "updates": [{"task_id": overdue["id"], "progress": 100,
                         "note": "本日完了しました"}],
            "note": "今日のチェックイン"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["streak"], 1)
        detail = self.admin.get("/api/tasks/{}".format(overdue["id"]))[1]
        self.assertEqual(detail["task"]["status"], "done")
        self.assertTrue(any(c["kind"] == "checkin" and "本日完了" in c["body"]
                            for c in detail["comments"]))
        daily = self.admin.get("/api/daily")[1]
        self.assertEqual(daily["checkin"]["note"], "今日のチェックイン")

    def test_daily_update_respects_permissions(self):
        project = self.make_project()
        task = self.make_task(project["id"])
        _, email = self.make_user("無権限")
        status, _ = self.client_for(email).post("/api/daily/update", {
            "updates": [{"task_id": task["id"], "progress": 50}]})
        self.assertEqual(status, 403)

    def test_digest_run_reports_counts(self):
        project = self.make_project()
        admin_id = self.admin.get("/api/auth/me")[1]["user"]["id"]
        self.make_task(project["id"], "ダイジェスト対象", assignee_id=admin_id,
                       due_date="2020-02-02")
        status, data = self.admin.post("/api/admin/run-digest", {})
        self.assertEqual(status, 200, data)
        self.assertIn("sent", data)


class TestCategories(ApiTestCase):
    def test_category_round_trip(self):
        project = self.make_project()
        task = self.make_task(project["id"], "調べもの", category="research")
        self.assertEqual(task["category"], "research")
        data = self.admin.patch("/api/tasks/{}".format(task["id"]), {"category": "docs"})[1]
        self.assertEqual(data["task"]["category"], "docs")

    def test_unknown_category_is_rejected(self):
        project = self.make_project()
        status, data = self.admin.post("/api/tasks", {
            "project_id": project["id"], "title": "x", "category": "nonsense"})
        self.assertEqual(status, 400, data)
        self.assertIn("カテゴリ", data["error"])

    def test_category_can_be_cleared(self):
        project = self.make_project()
        task = self.make_task(project["id"], "分類なし", category="meeting")
        data = self.admin.patch("/api/tasks/{}".format(task["id"]), {"category": ""})[1]
        self.assertEqual(data["task"]["category"], "")

    def test_category_change_is_recorded_in_the_history(self):
        project = self.make_project()
        task = self.make_task(project["id"], "履歴", category="build")
        self.admin.patch("/api/tasks/{}".format(task["id"]), {"category": "incident"})
        detail = self.admin.get("/api/tasks/{}".format(task["id"]))[1]
        self.assertTrue(any(c["kind"] == "system" and "カテゴリ" in c["body"]
                            for c in detail["comments"]))

    def test_filtering_by_category(self):
        project = self.make_project()
        wanted = self.make_task(project["id"], "会議の準備", category="meeting")
        self.make_task(project["id"], "実装", category="build")
        tasks = self.admin.get("/api/tasks?category=meeting&project_id={}".format(
            project["id"]))[1]["tasks"]
        self.assertEqual([t["id"] for t in tasks], [wanted["id"]])

    def test_meta_exposes_the_seven_categories(self):
        data = self.admin.get("/api/meta")[1]
        self.assertEqual(len(data["categories"]), 7)
        self.assertIn("incident", [c["value"] for c in data["categories"]])
        self.assertEqual([i["label"] for i in data["importance"]][0], "最重要")


class TestDependenciesApi(ApiTestCase):
    def test_dependencies_can_be_set_when_creating_a_task(self):
        project = self.make_project()
        first = self.make_task(project["id"], "先行")
        second = self.make_task(project["id"], "後続", depends_on=[first["id"]])
        detail = self.admin.get("/api/tasks/{}".format(second["id"]))[1]
        self.assertEqual([d["id"] for d in detail["deps"]], [first["id"]])

    def test_dependencies_can_be_replaced_on_update(self):
        project = self.make_project()
        a = self.make_task(project["id"], "A")
        b = self.make_task(project["id"], "B")
        c = self.make_task(project["id"], "C", depends_on=[a["id"]])
        status, _ = self.admin.patch("/api/tasks/{}".format(c["id"]),
                                     {"depends_on": [b["id"]]})
        self.assertEqual(status, 200)
        detail = self.admin.get("/api/tasks/{}".format(c["id"]))[1]
        self.assertEqual([d["id"] for d in detail["deps"]], [b["id"]])

    def test_dependencies_can_be_cleared(self):
        project = self.make_project()
        a = self.make_task(project["id"], "A")
        b = self.make_task(project["id"], "B", depends_on=[a["id"]])
        self.admin.patch("/api/tasks/{}".format(b["id"]), {"depends_on": []})
        detail = self.admin.get("/api/tasks/{}".format(b["id"]))[1]
        self.assertEqual(detail["deps"], [])

    def test_cross_project_dependencies_are_rejected(self):
        first = self.make_project("PJ1")
        second = self.make_project("PJ2")
        outsider = self.make_task(second["id"], "別PJ")
        status, data = self.admin.post("/api/tasks", {
            "project_id": first["id"], "title": "x", "depends_on": [outsider["id"]]})
        self.assertEqual(status, 400, data)

    def test_cycles_are_rejected_when_set_in_bulk(self):
        project = self.make_project()
        a = self.make_task(project["id"], "A")
        b = self.make_task(project["id"], "B", depends_on=[a["id"]])
        status, data = self.admin.patch("/api/tasks/{}".format(a["id"]),
                                        {"depends_on": [b["id"]]})
        self.assertEqual(status, 400, data)
        self.assertIn("循環", data["error"])

    def test_task_list_reports_blocking_state(self):
        project = self.make_project()
        a = self.make_task(project["id"], "先行")
        b = self.make_task(project["id"], "後続", depends_on=[a["id"]])
        tasks = self.admin.get("/api/projects/{}/tasks".format(project["id"]))[1]["tasks"]
        rows = {t["id"]: t for t in tasks}
        self.assertEqual(rows[a["id"]]["blocks_total"], 1)
        self.assertTrue(rows[b["id"]]["is_blocked"])
        self.assertFalse(rows[a["id"]]["is_blocked"])

    def test_task_detail_lists_the_downstream_impact(self):
        project = self.make_project()
        a = self.make_task(project["id"], "根っこ")
        b = self.make_task(project["id"], "中間", depends_on=[a["id"]])
        c = self.make_task(project["id"], "末端", depends_on=[b["id"]])
        detail = self.admin.get("/api/tasks/{}".format(a["id"]))[1]
        self.assertEqual(detail["metrics"]["blocks_total"], 2)
        self.assertEqual(sorted(i["id"] for i in detail["impact"]), sorted([b["id"], c["id"]]))


class TestBottleneckApi(ApiTestCase):
    def test_ranks_the_task_that_blocks_the_most(self):
        project = self.make_project()
        root = self.make_task(project["id"], "ボトルネック候補")
        mid = self.make_task(project["id"], "中間", depends_on=[root["id"]])
        self.make_task(project["id"], "末端", depends_on=[mid["id"]])
        data = self.admin.get("/api/projects/{}/bottlenecks".format(project["id"]))[1]
        self.assertEqual(data["bottlenecks"][0]["title"], "ボトルネック候補")
        self.assertEqual(data["bottlenecks"][0]["blocks_open"], 2)
        self.assertTrue(data["bottlenecks"][0]["reasons"])

    def test_reports_the_critical_path_with_titles(self):
        project = self.make_project()
        a = self.make_task(project["id"], "A", start_date="2026-04-01", due_date="2026-04-05")
        b = self.make_task(project["id"], "B", start_date="2026-04-06", due_date="2026-04-20",
                           depends_on=[a["id"]])
        data = self.admin.get("/api/projects/{}/bottlenecks".format(project["id"]))[1]
        self.assertEqual([n["title"] for n in data["critical_path"]], ["A", "B"])
        self.assertEqual([n["id"] for n in data["critical_path"]], [a["id"], b["id"]])

    def test_reports_date_conflicts(self):
        project = self.make_project()
        a = self.make_task(project["id"], "先行", start_date="2026-04-01", due_date="2026-04-20")
        self.make_task(project["id"], "後続", start_date="2026-04-10", due_date="2026-04-30",
                       depends_on=[a["id"]])
        data = self.admin.get("/api/projects/{}/bottlenecks".format(project["id"]))[1]
        self.assertEqual(len(data["conflicts"]), 1)
        self.assertEqual(data["conflicts"][0]["overlap_days"], 10)

    def test_requires_project_access(self):
        project = self.make_project()
        _, email = self.make_user("部外者3")
        status, _ = self.client_for(email).get(
            "/api/projects/{}/bottlenecks".format(project["id"]))
        self.assertEqual(status, 403)

    def test_blocked_filter_in_search(self):
        project = self.make_project()
        a = self.make_task(project["id"], "先行")
        b = self.make_task(project["id"], "待ち", depends_on=[a["id"]])
        tasks = self.admin.get("/api/tasks?blocked=1&project_id={}".format(
            project["id"]))[1]["tasks"]
        self.assertEqual([t["id"] for t in tasks], [b["id"]])

    def test_project_stats_count_blocked_tasks(self):
        project = self.make_project()
        a = self.make_task(project["id"], "先行")
        self.make_task(project["id"], "待ち", depends_on=[a["id"]])
        projects = self.admin.get("/api/projects")[1]["projects"]
        row = next(p for p in projects if p["id"] == project["id"])
        self.assertEqual(row["stats"]["blocked"], 1)


class TestIssues(ApiTestCase):
    def test_issue_numbers_are_sequential_per_project(self):
        first = self.make_project("PJ-A")
        second = self.make_project("PJ-B")
        self.assertEqual(self.make_issue(first["id"], "A1")["seq"], 1)
        self.assertEqual(self.make_issue(first["id"], "A2")["seq"], 2)
        self.assertEqual(self.make_issue(second["id"], "B1")["seq"], 1)

    def test_defaults_are_sensible(self):
        project = self.make_project()
        issue = self.make_issue(project["id"], "既定値")
        self.assertEqual(issue["status"], "open")
        self.assertEqual(issue["category"], "other")
        self.assertEqual(issue["severity"], 1)
        self.assertIsNotNone(issue["raised_on"])
        self.assertEqual(issue["raised_by_name"], "管理者")

    def test_unknown_category_and_status_are_rejected(self):
        project = self.make_project()
        status, data = self.admin.post("/api/issues", {
            "project_id": project["id"], "title": "x", "category": "nope"})
        self.assertEqual(status, 400, data)
        status, data = self.admin.post("/api/issues", {
            "project_id": project["id"], "title": "x", "status": "nope"})
        self.assertEqual(status, 400, data)

    def test_resolving_fills_the_resolution_date(self):
        project = self.make_project()
        issue = self.make_issue(project["id"], "解決する課題")
        data = self.admin.patch("/api/issues/{}".format(issue["id"]),
                                {"status": "resolved"})[1]
        self.assertIsNotNone(data["issue"]["resolved_on"])
        data = self.admin.patch("/api/issues/{}".format(issue["id"]), {"status": "doing"})[1]
        self.assertIsNone(data["issue"]["resolved_on"])

    def test_changes_are_recorded_as_history(self):
        project = self.make_project()
        issue = self.make_issue(project["id"], "履歴の課題")
        self.admin.patch("/api/issues/{}".format(issue["id"]),
                         {"status": "doing", "severity": 3})
        detail = self.admin.get("/api/issues/{}".format(issue["id"]))[1]
        history = " ".join(c["body"] for c in detail["comments"] if c["kind"] == "system")
        self.assertIn("状態", history)
        self.assertIn("影響度", history)

    def test_comments_and_deletion(self):
        project = self.make_project()
        issue = self.make_issue(project["id"])
        status, data = self.admin.post("/api/issues/{}/comments".format(issue["id"]),
                                       {"body": "情シスに再依頼した"})
        self.assertEqual(status, 201, data)
        detail = self.admin.get("/api/issues/{}".format(issue["id"]))[1]
        self.assertTrue(any(c["body"] == "情シスに再依頼した" for c in detail["comments"]))
        self.assertEqual(
            self.admin.delete("/api/comments/{}".format(data["comment"]["id"]))[0], 200)

    def test_attachments_can_be_added_to_an_issue(self):
        project = self.make_project()
        issue = self.make_issue(project["id"])
        status, data = self.admin.post("/api/issues/{}/attachments".format(issue["id"]),
                                       {"url": "https://example.com/evidence", "name": "証跡"})
        self.assertEqual(status, 201, data)
        detail = self.admin.get("/api/issues/{}".format(issue["id"]))[1]
        self.assertEqual(len(detail["attachments"]), 1)

    def test_summary_covers_the_whole_project_not_the_filter(self):
        project = self.make_project()
        self.make_issue(project["id"], "未解決", severity=3)
        resolved = self.make_issue(project["id"], "解決済")
        self.admin.patch("/api/issues/{}".format(resolved["id"]), {"status": "resolved"})
        data = self.admin.get("/api/projects/{}/issues?status=open".format(project["id"]))[1]
        self.assertEqual(len(data["issues"]), 1)
        self.assertEqual(data["summary"]["total"], 2)
        self.assertEqual(data["summary"]["resolved"], 1)
        self.assertEqual(data["summary"]["high"], 1)

    def test_filters(self):
        project = self.make_project()
        self.make_issue(project["id"], "仕様の課題", category="spec", severity=3)
        self.make_issue(project["id"], "コストの課題", category="cost", severity=0)
        base = "/api/projects/{}/issues".format(project["id"])
        self.assertEqual(
            [i["title"] for i in self.admin.get(base + "?category=spec")[1]["issues"]],
            ["仕様の課題"])
        self.assertEqual(
            [i["title"] for i in self.admin.get(base + "?min_severity=3")[1]["issues"]],
            ["仕様の課題"])
        self.assertEqual(
            [i["title"] for i in self.admin.get(base + "?q=コスト")[1]["issues"]],
            ["コストの課題"])

    def test_permissions(self):
        project = self.make_project()
        issue = self.make_issue(project["id"])
        _, outsider = self.make_user("課題部外者")
        self.assertEqual(
            self.client_for(outsider).get("/api/issues/{}".format(issue["id"]))[0], 403)

        viewer, viewer_email = self.make_user("課題閲覧者")
        self.admin.put("/api/projects/{}/members".format(project["id"]), {
            "members": [{"principal_type": "user", "principal_id": viewer["id"],
                         "role": "viewer"}]})
        client = self.client_for(viewer_email)
        self.assertEqual(client.get("/api/issues/{}".format(issue["id"]))[0], 200)
        self.assertEqual(
            client.patch("/api/issues/{}".format(issue["id"]), {"title": "変更"})[0], 403)

    def test_cross_project_listing(self):
        first = self.make_project("横断A")
        second = self.make_project("横断B")
        self.make_issue(first["id"], "課題A")
        self.make_issue(second["id"], "課題B")
        titles = [i["title"] for i in self.admin.get("/api/issues")[1]["issues"]]
        self.assertIn("課題A", titles)
        self.assertIn("課題B", titles)

    def test_deleting_a_project_removes_its_issues(self):
        project = self.make_project()
        issue = self.make_issue(project["id"])
        self.admin.delete("/api/projects/{}".format(project["id"]))
        self.assertEqual(self.admin.get("/api/issues/{}".format(issue["id"]))[0], 404)


class TestIssueTaskLinks(ApiTestCase):
    def test_tasks_can_be_linked_at_creation(self):
        project = self.make_project()
        task = self.make_task(project["id"], "対応タスク")
        issue = self.make_issue(project["id"], "紐づく課題", task_ids=[task["id"]])
        self.assertEqual(issue["task_count"], 1)
        detail = self.admin.get("/api/issues/{}".format(issue["id"]))[1]
        self.assertEqual([t["id"] for t in detail["tasks"]], [task["id"]])

    def test_links_can_be_replaced(self):
        project = self.make_project()
        first = self.make_task(project["id"], "T1")
        second = self.make_task(project["id"], "T2")
        issue = self.make_issue(project["id"], "課題", task_ids=[first["id"]])
        self.admin.put("/api/issues/{}/tasks".format(issue["id"]),
                       {"task_ids": [second["id"]]})
        detail = self.admin.get("/api/issues/{}".format(issue["id"]))[1]
        self.assertEqual([t["id"] for t in detail["tasks"]], [second["id"]])

    def test_cross_project_links_are_rejected(self):
        first = self.make_project("L1")
        second = self.make_project("L2")
        outsider = self.make_task(second["id"], "別PJのタスク")
        issue = self.make_issue(first["id"])
        status, data = self.admin.put("/api/issues/{}/tasks".format(issue["id"]),
                                      {"task_ids": [outsider["id"]]})
        self.assertEqual(status, 400, data)

    def test_open_task_count_tracks_progress(self):
        project = self.make_project()
        task = self.make_task(project["id"], "未完了タスク")
        issue = self.make_issue(project["id"], "課題", task_ids=[task["id"]])
        self.assertEqual(
            self.admin.get("/api/issues/{}".format(issue["id"]))[1]["issue"]["open_task_count"], 1)
        self.admin.patch("/api/tasks/{}".format(task["id"]), {"status": "done"})
        self.assertEqual(
            self.admin.get("/api/issues/{}".format(issue["id"]))[1]["issue"]["open_task_count"], 0)

    def test_task_detail_shows_the_linked_issues(self):
        project = self.make_project()
        task = self.make_task(project["id"], "タスク")
        issue = self.make_issue(project["id"], "この課題", task_ids=[task["id"]])
        detail = self.admin.get("/api/tasks/{}".format(task["id"]))[1]
        self.assertEqual([i["id"] for i in detail["issues"]], [issue["id"]])

    def test_deleting_a_task_removes_the_link_only(self):
        project = self.make_project()
        task = self.make_task(project["id"], "消えるタスク")
        issue = self.make_issue(project["id"], "残る課題", task_ids=[task["id"]])
        self.admin.delete("/api/tasks/{}".format(task["id"]))
        detail = self.admin.get("/api/issues/{}".format(issue["id"]))
        self.assertEqual(detail[0], 200)
        self.assertEqual(detail[1]["tasks"], [])

    def test_project_stats_count_open_issues(self):
        project = self.make_project()
        self.make_issue(project["id"], "未解決の課題")
        resolved = self.make_issue(project["id"], "解決済の課題")
        self.admin.patch("/api/issues/{}".format(resolved["id"]), {"status": "closed"})
        projects = self.admin.get("/api/projects")[1]["projects"]
        row = next(p for p in projects if p["id"] == project["id"])
        self.assertEqual(row["stats"]["open_issues"], 1)


class TestAppearance(ApiTestCase):
    def test_theme_and_accent_round_trip(self):
        data = self.admin.patch("/api/auth/profile",
                                {"ui_theme": "dark", "ui_accent": "#16A34A"})[1]
        self.assertEqual(data["user"]["ui_theme"], "dark")
        self.assertEqual(data["user"]["ui_accent"], "#16a34a")

    def test_invalid_colour_is_rejected(self):
        status, data = self.admin.patch("/api/auth/profile", {"ui_accent": "green"})
        self.assertEqual(status, 400, data)

    def test_accent_can_be_cleared_to_follow_the_org_default(self):
        self.admin.patch("/api/auth/profile", {"ui_accent": "#111111"})
        data = self.admin.patch("/api/auth/profile", {"ui_accent": ""})[1]
        self.assertEqual(data["user"]["ui_accent"], "")

    def test_org_defaults_are_exposed_to_the_client(self):
        self.admin.put("/api/settings", {"settings": {
            "ui_accent_default": "#8b5cf6", "app_name": "社内タスク"}})
        data = self.admin.get("/api/auth/me")[1]
        self.assertEqual(data["ui"]["accent_default"], "#8b5cf6")
        self.assertEqual(data["ui"]["app_name"], "社内タスク")

    def test_defaults_reach_the_login_screen(self):
        self.admin.put("/api/settings", {"settings": {"app_name": "課題管理"}})
        data = Client(self.base).get("/api/auth/me")[1]
        self.assertIsNone(data["user"])
        self.assertEqual(data["ui"]["app_name"], "課題管理")


class TestNaturalLanguage(ApiTestCase):
    def test_parse_returns_a_draft_without_writing(self):
        project = self.make_project()
        before = self.admin.get("/api/projects/{}/tasks".format(project["id"]))[1]["tasks"]
        status, data = self.admin.post("/api/nl/parse", {
            "text": "明日までに至急 移行手順書を作成", "project_id": project["id"]})
        self.assertEqual(status, 200, data)
        draft = data["draft"]
        self.assertIn("移行手順書", draft["title"])
        self.assertEqual(draft["priority"], 3)
        self.assertIsNotNone(draft["due_date"])
        after = self.admin.get("/api/projects/{}/tasks".format(project["id"]))[1]["tasks"]
        self.assertEqual(len(before), len(after), "解析だけでタスクを作ってはいけない")

    def test_parse_assigns_a_known_user(self):
        project = self.make_project()
        user, _ = self.make_user("田中 太郎")
        self.admin.put("/api/projects/{}/members".format(project["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"],
                         "role": "editor"}]})
        data = self.admin.post("/api/nl/parse", {
            "text": "田中 太郎さんが週次レポートを作成", "project_id": project["id"]})[1]
        self.assertEqual(data["draft"]["assignee_id"], user["id"])

    def test_parse_requires_text(self):
        self.assertEqual(self.admin.post("/api/nl/parse", {"text": "  "})[0], 400)

    def test_parse_falls_back_to_an_editable_project(self):
        project = self.make_project()
        data = self.admin.post("/api/nl/parse", {"text": "資料をまとめる"})[1]
        self.assertIsNotNone(data["draft"]["project_id"])

    def test_decompose_suggests_ordered_steps(self):
        project = self.make_project()
        status, data = self.admin.post("/api/nl/decompose", {
            "title": "サーバ移行", "project_id": project["id"],
            "start_date": "2026-10-01", "due_date": "2026-10-20"})
        self.assertEqual(status, 200, data)
        titles = " ".join(item["title"] for item in data["items"])
        self.assertIn("バックアップ", titles)
        self.assertIn("疎通確認", titles)
        self.assertEqual(data["items"][0]["start_date"], "2026-10-01")
        self.assertEqual(data["items"][-1]["due_date"], "2026-10-20")

    def test_decompose_requires_project_edit_rights(self):
        project = self.make_project()
        _, email = self.make_user("分解部外者")
        status, _ = self.client_for(email).post("/api/nl/decompose", {
            "title": "サーバ移行", "project_id": project["id"]})
        self.assertEqual(status, 403)

    def test_subtasks_are_created_in_order(self):
        project = self.make_project()
        parent = self.make_task(project["id"], "親タスク", priority=2)
        status, data = self.admin.post("/api/tasks/{}/subtasks".format(parent["id"]), {
            "items": [
                {"title": "事前バックアップ", "category": "build", "due_date": "2026-10-05"},
                {"title": "疎通確認", "category": "build"},
            ]})
        self.assertEqual(status, 201, data)
        self.assertEqual(data["created"], 2)
        detail = self.admin.get("/api/tasks/{}".format(parent["id"]))[1]
        self.assertEqual([c["title"] for c in detail["children"]],
                         ["事前バックアップ", "疎通確認"])
        # 重要度は親から引き継ぐ
        self.assertEqual(detail["children"][0]["priority"], 2)

    def test_subtasks_record_history(self):
        project = self.make_project()
        parent = self.make_task(project["id"], "親")
        self.admin.post("/api/tasks/{}/subtasks".format(parent["id"]),
                        {"items": [{"title": "子1"}]})
        detail = self.admin.get("/api/tasks/{}".format(parent["id"]))[1]
        self.assertTrue(any(c["kind"] == "system" and "子タスク" in c["body"]
                            for c in detail["comments"]))

    def test_subtasks_reject_empty_input(self):
        project = self.make_project()
        parent = self.make_task(project["id"])
        self.assertEqual(self.admin.post(
            "/api/tasks/{}/subtasks".format(parent["id"]), {"items": []})[0], 400)
        self.assertEqual(self.admin.post(
            "/api/tasks/{}/subtasks".format(parent["id"]),
            {"items": [{"title": "   "}]})[0], 400)

    def test_subtasks_require_edit_rights(self):
        project = self.make_project()
        parent = self.make_task(project["id"])
        user, email = self.make_user("閲覧だけ")
        self.admin.put("/api/projects/{}/members".format(project["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"],
                         "role": "viewer"}]})
        status, _ = self.client_for(email).post(
            "/api/tasks/{}/subtasks".format(parent["id"]), {"items": [{"title": "x"}]})
        self.assertEqual(status, 403)

    def test_llm_key_is_masked_in_settings(self):
        self.admin.put("/api/settings", {"settings": {"llm_api_key": "sk-ant-secret"}})
        data = self.admin.get("/api/settings")[1]
        self.assertEqual(data["settings"]["llm_api_key"], "********")
        self.assertIn("llm_models", data)
        self.admin.put("/api/settings", {"settings": {"llm_api_key": "********"}})
        self.assertEqual(db.get_setting("llm_api_key"), "sk-ant-secret")


class TestSubdirectory(unittest.TestCase):
    """サブディレクトリ配下（/tasks）で公開したときの挙動。"""

    BASE_PATH = "/tasks"

    @classmethod
    def setUpClass(cls):
        db.init_db()
        reset_database()
        cls._saved = (server_module.BASE_PATH, http_util.COOKIE_PATH)
        server_module.BASE_PATH = cls.BASE_PATH
        http_util.COOKIE_PATH = cls.BASE_PATH
        port = free_port()
        cls.base = "http://127.0.0.1:{}".format(port)
        cls.server = server_module.Server(("127.0.0.1", port), server_module.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        server_module.BASE_PATH, http_util.COOKIE_PATH = cls._saved

    def setUp(self):
        self.client = Client(self.base)

    def test_index_is_served_under_the_prefix(self):
        status, body = self.client.get("/tasks/")
        self.assertEqual(status, 200)
        self.assertIn(b"<!doctype html>", body.lower())

    def test_assets_are_served_under_the_prefix(self):
        self.assertEqual(self.client.get("/tasks/css/style.css")[0], 200)
        self.assertEqual(self.client.get("/tasks/js/app.js")[0], 200)

    def test_html_references_assets_relatively(self):
        body = self.client.get("/tasks/")[1].decode()
        self.assertIn('href="css/style.css"', body)
        self.assertNotIn('href="/css/style.css"', body)

    def test_missing_trailing_slash_redirects(self):
        request = urllib.request.Request(self.base + "/tasks", method="GET")
        opener = urllib.request.build_opener(NoRedirect())
        try:
            with opener.open(request, timeout=10) as response:
                status, location = response.status, response.headers.get("Location")
        except urllib.error.HTTPError as error:
            status, location = error.code, error.headers.get("Location")
        self.assertEqual(status, 302)
        self.assertEqual(location, "/tasks/")

    def test_api_works_under_the_prefix(self):
        status, data = self.client.get("/tasks/api/meta")
        self.assertEqual(status, 200)
        self.assertIn("statuses", data)

    def test_paths_outside_the_prefix_are_not_served(self):
        self.assertEqual(self.client.get("/")[0], 404)
        self.assertEqual(self.client.get("/api/meta")[0], 404)
        self.assertEqual(self.client.get("/css/style.css")[0], 404)

    def test_login_cookie_is_scoped_to_the_prefix(self):
        status, _ = self.client.post("/tasks/api/auth/login",
                                     {"email": ADMIN[0], "password": ADMIN[1]})
        self.assertEqual(status, 200)
        cookie = next(iter(self.client.jar))
        self.assertEqual(cookie.path, self.BASE_PATH)
        # 同じセッションでそのまま API を呼べること
        self.assertEqual(self.client.get("/tasks/api/projects")[0], 200)

    def test_base_path_normalisation(self):
        normalize = config._normalize_base_path
        self.assertEqual(normalize(""), "")
        self.assertEqual(normalize("/"), "")
        self.assertEqual(normalize("tasks"), "/tasks")
        self.assertEqual(normalize("/tasks"), "/tasks")
        self.assertEqual(normalize("/tasks/"), "/tasks")
        self.assertEqual(normalize("  /tools/tasks/  "), "/tools/tasks")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class TestSettings(ApiTestCase):
    def test_settings_are_admin_only(self):
        _, email = self.make_user("一般")
        self.assertEqual(self.client_for(email).get("/api/settings")[0], 403)

    def test_secrets_are_masked_and_preserved(self):
        self.admin.put("/api/settings", {"settings": {"smtp_password": "s3cret",
                                                      "smtp_host": "smtp.example.com"}})
        data = self.admin.get("/api/settings")[1]["settings"]
        self.assertEqual(data["smtp_password"], "********")
        self.admin.put("/api/settings", {"settings": {"smtp_password": "********"}})
        self.assertEqual(db.get_setting("smtp_password"), "s3cret")

    def test_unknown_settings_are_ignored(self):
        self.admin.put("/api/settings", {"settings": {"evil_key": "x"}})
        self.assertNotIn("evil_key", self.admin.get("/api/settings")[1]["settings"])

    def test_meta_endpoint(self):
        data = self.admin.get("/api/meta")[1]
        self.assertEqual(len(data["statuses"]), 5)
        self.assertIn("max_upload_mb", data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
