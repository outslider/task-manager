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

from app import auth, config, db, http_util, notify, prefs  # noqa: E402
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

    def test_deleting_a_user_removes_its_project_memberships(self):
        """principal_id は外部キーを張れないので、消し忘れると権限行が残る。"""
        project = self.make_project()
        user, _ = self.make_user("消えるメンバー")
        self.admin.put("/api/projects/{}/members".format(project["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"],
                         "role": "editor"}]})
        self.assertEqual(self.admin.delete("/api/users/{}".format(user["id"]))[0], 200)
        left = db.query(
            "SELECT 1 AS x FROM project_members WHERE principal_type='user' AND principal_id=%s",
            (user["id"],))
        self.assertEqual(list(left), [], "削除したユーザーの権限行が残っている")

    def test_deleting_a_group_removes_its_project_memberships(self):
        project = self.make_project()
        group = self.admin.post("/api/groups", {
            "name": "消えるG-{}".format(uuid.uuid4().hex[:6])})[1]["group"]
        self.admin.put("/api/projects/{}/members".format(project["id"]), {
            "members": [{"principal_type": "group", "principal_id": group["id"],
                         "role": "editor"}]})
        self.admin.delete("/api/groups/{}".format(group["id"]))
        left = db.query(
            "SELECT 1 AS x FROM project_members WHERE principal_type='group' AND principal_id=%s",
            (group["id"],))
        self.assertEqual(list(left), [])

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


class TestEffortAndWorkload(ApiTestCase):
    def test_estimate_is_optional(self):
        project = self.make_project()
        task = self.make_task(project["id"], "工数なし")
        self.assertIsNone(task["estimate_hours"])
        self.assertEqual(float(task["actual_hours"]), 0.0)

    def test_estimate_round_trip(self):
        project = self.make_project()
        task = self.make_task(project["id"], "見積あり", estimate_hours=7.5)
        self.assertEqual(float(task["estimate_hours"]), 7.5)
        data = self.admin.patch("/api/tasks/{}".format(task["id"]),
                                {"estimate_hours": 12, "actual_hours": 3})[1]
        self.assertEqual(float(data["task"]["estimate_hours"]), 12.0)
        self.assertEqual(float(data["task"]["actual_hours"]), 3.0)

    def test_estimate_can_be_cleared(self):
        project = self.make_project()
        task = self.make_task(project["id"], "消す", estimate_hours=5)
        data = self.admin.patch("/api/tasks/{}".format(task["id"]),
                                {"estimate_hours": None})[1]
        self.assertIsNone(data["task"]["estimate_hours"])

    def test_invalid_hours_are_rejected(self):
        project = self.make_project()
        status, _ = self.admin.post("/api/tasks", {
            "project_id": project["id"], "title": "x", "estimate_hours": "たくさん"})
        self.assertEqual(status, 400)
        status, _ = self.admin.post("/api/tasks", {
            "project_id": project["id"], "title": "y", "estimate_hours": -3})
        self.assertEqual(status, 400)

    def test_effort_change_is_recorded_in_the_history(self):
        project = self.make_project()
        task = self.make_task(project["id"], "履歴", estimate_hours=4)
        self.admin.patch("/api/tasks/{}".format(task["id"]), {"estimate_hours": 8})
        detail = self.admin.get("/api/tasks/{}".format(task["id"]))[1]
        self.assertTrue(any(c["kind"] == "system" and "見積工数" in c["body"]
                            for c in detail["comments"]))

    def test_daily_update_accumulates_actual_hours(self):
        project = self.make_project()
        admin_id = self.admin.get("/api/auth/me")[1]["user"]["id"]
        task = self.make_task(project["id"], "実績", assignee_id=admin_id)
        self.admin.post("/api/daily/update",
                        {"updates": [{"task_id": task["id"], "hours": 2.5}]})
        self.admin.post("/api/daily/update",
                        {"updates": [{"task_id": task["id"], "hours": 1.5}]})
        detail = self.admin.get("/api/tasks/{}".format(task["id"]))[1]
        self.assertEqual(float(detail["task"]["actual_hours"]), 4.0)

    def test_workload_reports_hours_per_week(self):
        project = self.make_project()
        user, _ = self.make_user("負荷テスト")
        self.admin.put("/api/projects/{}/members".format(project["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"],
                         "role": "editor"}]})
        self.make_task(project["id"], "来週の作業", assignee_id=user["id"],
                       start_date="2026-09-14", due_date="2026-09-18", estimate_hours=40)
        data = self.admin.get("/api/workload?project_id={}&weeks=12".format(project["id"]))[1]
        self.assertTrue(data["weeks"])
        row = next(r for r in data["rows"] if r["user_id"] == user["id"])
        self.assertEqual(row["total_hours"], 40.0)
        self.assertEqual(data["capacity_per_week"], 40.0)

    def test_workload_requires_project_access(self):
        project = self.make_project()
        _, email = self.make_user("負荷部外者")
        status, _ = self.client_for(email).get(
            "/api/workload?project_id={}".format(project["id"]))
        self.assertEqual(status, 403)

    def test_workload_without_a_project_covers_everything_visible(self):
        project = self.make_project()
        self.make_task(project["id"], "横断", start_date="2026-09-14", due_date="2026-09-18")
        data = self.admin.get("/api/workload")[1]
        self.assertIn("effort", data)
        self.assertTrue(any(p["id"] == project["id"] for p in data["projects"]))


class TestRecurrenceApi(ApiTestCase):
    def make_rule(self, project_id, **kwargs):
        payload = {"title": "週次定例", "freq": "weekly", "weekdays": "0",
                   "next_on": "2026-09-14", "lead_days": 3}
        payload.update(kwargs)
        status, data = self.admin.post(
            "/api/projects/{}/recurrences".format(project_id), payload)
        self.assertEqual(status, 201, data)
        return data["recurrence"]

    def test_create_and_list(self):
        project = self.make_project()
        rule = self.make_rule(project["id"])
        self.assertEqual(rule["summary"], "毎週 月曜")
        rows = self.admin.get(
            "/api/projects/{}/recurrences".format(project["id"]))[1]["recurrences"]
        self.assertEqual([r["id"] for r in rows], [rule["id"]])

    def test_weekly_requires_a_weekday(self):
        project = self.make_project()
        status, data = self.admin.post("/api/projects/{}/recurrences".format(project["id"]), {
            "title": "曜日なし", "freq": "weekly", "weekdays": "", "next_on": "2026-09-14"})
        self.assertEqual(status, 400, data)

    def test_monthly_requires_a_day(self):
        project = self.make_project()
        status, _ = self.admin.post("/api/projects/{}/recurrences".format(project["id"]), {
            "title": "日なし", "freq": "monthly", "next_on": "2026-09-25"})
        self.assertEqual(status, 400)

    def test_run_now_creates_a_task_and_advances(self):
        project = self.make_project()
        rule = self.make_rule(project["id"])
        status, data = self.admin.post("/api/recurrences/{}/run".format(rule["id"]), {})
        self.assertEqual(status, 201, data)
        self.assertEqual(data["task"]["title"], "週次定例")
        self.assertEqual(data["task"]["due_date"], "2026-09-14")
        rows = self.admin.get(
            "/api/projects/{}/recurrences".format(project["id"]))[1]["recurrences"]
        self.assertEqual(rows[0]["next_on"], "2026-09-21")

    def test_generated_task_carries_the_template_values(self):
        project = self.make_project()
        user, _ = self.make_user("定例担当")
        self.admin.put("/api/projects/{}/members".format(project["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"],
                         "role": "editor"}]})
        rule = self.make_rule(project["id"], assignee_id=user["id"], category="meeting",
                              estimate_hours=1.5, priority=2)
        task = self.admin.post("/api/recurrences/{}/run".format(rule["id"]), {})[1]["task"]
        self.assertEqual(task["assignee_id"], user["id"])
        self.assertEqual(task["category"], "meeting")
        self.assertEqual(float(task["estimate_hours"]), 1.5)
        self.assertEqual(task["priority"], 2)

    def test_update_and_deactivate(self):
        project = self.make_project()
        rule = self.make_rule(project["id"])
        data = self.admin.patch("/api/recurrences/{}".format(rule["id"]),
                                {"active": False, "title": "停止した定例"})[1]
        self.assertEqual(data["recurrence"]["active"], 0)
        self.assertEqual(data["recurrence"]["title"], "停止した定例")

    def test_delete(self):
        project = self.make_project()
        rule = self.make_rule(project["id"])
        self.assertEqual(self.admin.delete("/api/recurrences/{}".format(rule["id"]))[0], 200)
        self.assertEqual(self.admin.get(
            "/api/projects/{}/recurrences".format(project["id"]))[1]["recurrences"], [])

    def test_requires_edit_rights(self):
        project = self.make_project()
        user, email = self.make_user("定例閲覧のみ")
        self.admin.put("/api/projects/{}/members".format(project["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"],
                         "role": "viewer"}]})
        status, _ = self.client_for(email).post(
            "/api/projects/{}/recurrences".format(project["id"]),
            {"title": "x", "freq": "daily", "next_on": "2026-09-20"})
        self.assertEqual(status, 403)


class TestSlackSettings(ApiTestCase):
    def test_webhook_url_is_validated(self):
        project = self.make_project()
        status, data = self.admin.patch("/api/projects/{}".format(project["id"]),
                                        {"slack_webhook_url": "http://evil.example.com/hook"})
        self.assertEqual(status, 400, data)
        status, _ = self.admin.patch("/api/projects/{}".format(project["id"]), {
            "slack_webhook_url": "https://hooks.slack.com/services/T/B/x"})
        self.assertEqual(status, 200)

    def test_webhook_can_be_cleared(self):
        project = self.make_project()
        self.admin.patch("/api/projects/{}".format(project["id"]),
                         {"slack_webhook_url": "https://hooks.slack.com/services/T/B/x"})
        self.admin.patch("/api/projects/{}".format(project["id"]), {"slack_webhook_url": ""})
        detail = self.admin.get("/api/projects/{}".format(project["id"]))[1]
        self.assertEqual(detail["project"]["slack_webhook_url"], "")

    def test_slack_is_off_until_configured(self):
        data = self.admin.get("/api/settings")[1]
        self.assertFalse(data["slack_ready"])

    def test_test_send_fails_cleanly_without_a_url(self):
        # テスト自体は実行できているので 200。成否と理由は本文で返す
        status, data = self.admin.post("/api/settings/test-slack", {})
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertIn("設定", data["message"])


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

    def test_connection_tests_report_the_reason_not_a_bare_error(self):
        """設定不足でも 200 で返し、理由を message に載せる（画面で理由を出すため）。"""
        for endpoint in ("test-llm", "test-slack", "test-mail"):
            status, data = self.admin.post("/api/settings/{}".format(endpoint), {})
            self.assertEqual(status, 200, "{} は 200 で返すこと: {}".format(endpoint, data))
            self.assertFalse(data["ok"])
            self.assertTrue(data["message"].strip(),
                            "{} の失敗理由が空になっている".format(endpoint))
            self.assertNotIn("エラー (", data["message"])

    def test_connection_tests_are_admin_only(self):
        _, email = self.make_user("テスト実行者")
        client = self.client_for(email)
        for endpoint in ("test-llm", "test-slack", "test-mail"):
            self.assertEqual(client.post("/api/settings/{}".format(endpoint), {})[0], 403)

    def test_llm_check_explains_a_malformed_key(self):
        self.admin.put("/api/settings", {"settings": {
            "llm_enabled": "1", "llm_api_key": "not-a-real-key"}})
        data = self.admin.post("/api/settings/test-llm", {})[1]
        self.assertFalse(data["ok"])
        self.assertIn("sk-ant-", data["message"])

    def test_meta_endpoint(self):
        data = self.admin.get("/api/meta")[1]
        self.assertEqual(len(data["statuses"]), 5)
        self.assertIn("max_upload_mb", data)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestNotificationPreferences(ApiTestCase):
    """メール / Slack を「どこまで送るか」の制御。"""

    def setUp(self):
        super().setUp()
        db.set_setting("slack_events", "issue,digest")

    def member_of(self, project, name="通知テスト"):
        user, email = self.make_user(name)
        self.admin.put("/api/projects/{}/members".format(project["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"],
                         "role": "editor"}]})
        return user, email

    # -- 個人設定 --------------------------------------------------------
    def test_defaults_are_all_on(self):
        _, email = self.make_user("既定値")
        data = self.client_for(email).get("/api/me/notification-settings")[1]
        self.assertTrue(data["prefs"]["email_notify"])
        for key in prefs.EMAIL_EVENT_KEYS:
            self.assertTrue(data["prefs"][key], "{} は既定で ON".format(key))
        self.assertEqual(data["muted_project_ids"], [])
        self.assertEqual([e["value"] for e in data["events"]], prefs.EMAIL_EVENT_KEYS)

    def test_event_toggles_round_trip(self):
        user, email = self.make_user("イベント切替")
        client = self.client_for(email)
        status, data = client.put("/api/me/notification-settings",
                                  {"assigned": False, "digest": False})
        self.assertEqual(status, 200, data)
        self.assertFalse(data["prefs"]["assigned"])
        self.assertFalse(data["prefs"]["digest"])
        self.assertTrue(data["prefs"]["comment"], "触っていない項目は変わらないこと")
        self.assertFalse(prefs.email_allowed(user["id"], "assigned"))
        self.assertTrue(prefs.email_allowed(user["id"], "comment"))

    def test_due_events_follow_the_due_toggle(self):
        user, email = self.make_user("期限通知")
        self.client_for(email).put("/api/me/notification-settings", {"due": False})
        for ntype in ("overdue", "due_soon"):
            self.assertFalse(prefs.email_allowed(user["id"], ntype),
                             "{} は期限通知の設定に従うこと".format(ntype))

    def test_master_switch_stops_everything(self):
        user, email = self.make_user("全停止")
        self.client_for(email).put("/api/me/notification-settings", {"email_notify": False})
        for key in prefs.EMAIL_EVENT_KEYS:
            self.assertFalse(prefs.email_allowed(user["id"], key))

    def test_settings_require_a_login(self):
        anonymous = Client(self.base)
        self.assertEqual(anonymous.get("/api/me/notification-settings")[0], 401)

    # -- プロジェクト単位のミュート（受け取る人の都合） ------------------
    def test_muting_a_project_silences_only_that_project(self):
        quiet = self.make_project("黙らせるPJ")
        loud = self.make_project("普通のPJ")
        user, email = self.member_of(quiet, "ミュート")
        self.admin.put("/api/projects/{}/members".format(loud["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"],
                         "role": "editor"}]})
        client = self.client_for(email)
        data = client.put("/api/me/notification-settings",
                          {"muted_project_ids": [quiet["id"]]})[1]
        self.assertEqual(data["muted_project_ids"], [quiet["id"]])
        self.assertFalse(prefs.email_allowed(user["id"], "assigned", quiet["id"]))
        self.assertTrue(prefs.email_allowed(user["id"], "assigned", loud["id"]))

    def test_muting_a_project_the_user_cannot_see_is_ignored(self):
        secret = self.make_project("見えないPJ")
        user, email = self.make_user("部外者")
        self.client_for(email).put("/api/me/notification-settings",
                                   {"muted_project_ids": [secret["id"]]})
        self.assertEqual(prefs.muted_projects(user["id"]), [])

    def test_muted_projects_drop_out_of_the_digest(self):
        project = self.make_project("ダイジェスト除外")
        user, email = self.member_of(project, "ダイジェスト")
        self.make_task(project["id"], "超過タスク",
                       assignee_id=user["id"], due_date="2020-01-01")
        self.assertTrue(notify.daily_summary_for(user["id"], exclude_muted=True)["overdue"])
        self.client_for(email).put("/api/me/notification-settings",
                                   {"muted_project_ids": [project["id"]]})
        self.assertFalse(notify.daily_summary_for(user["id"], exclude_muted=True)["overdue"])
        self.assertTrue(notify.daily_summary_for(user["id"])["overdue"],
                        "画面に出す一覧はミュートの影響を受けないこと")

    def test_mutes_disappear_with_the_project(self):
        project = self.make_project("消えるPJ")
        user, email = self.member_of(project, "残骸チェック")
        self.client_for(email).put("/api/me/notification-settings",
                                   {"muted_project_ids": [project["id"]]})
        self.admin.delete("/api/projects/{}".format(project["id"]))
        self.assertEqual(prefs.muted_projects(user["id"]), [])

    # -- プロジェクト単位の停止（送る側の都合） --------------------------
    def test_project_switch_stops_mail_and_in_app_alike(self):
        project = self.make_project("通知停止PJ")
        user, _ = self.member_of(project, "停止対象")
        status, data = self.admin.patch("/api/projects/{}".format(project["id"]),
                                        {"notify_enabled": False})
        self.assertEqual(status, 200, data)
        self.assertFalse(prefs.project_notify_enabled(project["id"]))
        self.assertFalse(prefs.email_allowed(user["id"], "assigned", project["id"]))
        self.assertFalse(notify.create(user["id"], "assigned", "届かないはず",
                                       project_id=project["id"]))

    def test_stopped_projects_are_skipped_by_the_due_scan(self):
        project = self.make_project("スキャン対象外")
        user, _ = self.member_of(project, "期限担当")
        self.make_task(project["id"], "止まっているPJの超過",
                       assignee_id=user["id"], due_date="2020-01-01")
        self.admin.patch("/api/projects/{}".format(project["id"]), {"notify_enabled": False})
        notify.scan_due_tasks()
        overdue = db.query(
            "SELECT title FROM notifications WHERE user_id=%s AND type IN ('overdue','due_soon')",
            (user["id"],))
        self.assertEqual(list(overdue), [], "停止中のプロジェクトからは期限通知を出さないこと")

    def test_project_switch_defaults_to_on(self):
        project = self.make_project("既定ON")
        detail = self.admin.get("/api/projects/{}".format(project["id"]))[1]["project"]
        self.assertTrue(detail["notify_enabled"])

    # -- Slack のイベント選択 --------------------------------------------
    def test_global_slack_events_round_trip(self):
        self.admin.put("/api/settings", {"settings": {"slack_events": ["digest"]}})
        self.assertEqual(db.get_setting("slack_events"), "digest")
        self.assertTrue(prefs.slack_allowed("digest"))
        self.assertFalse(prefs.slack_allowed("issue"))

    def test_slack_events_accept_a_comma_string_and_drop_junk(self):
        self.admin.put("/api/settings", {"settings": {"slack_events": "issue, nonsense"}})
        self.assertEqual(db.get_setting("slack_events"), "issue")

    def test_project_slack_events_override_the_global_choice(self):
        project = self.make_project("Slack個別")
        self.admin.put("/api/settings", {"settings": {"slack_events": ["digest"]}})
        self.admin.patch("/api/projects/{}".format(project["id"]),
                         {"slack_events": ["issue"]})
        self.assertTrue(prefs.slack_allowed("issue", project["id"]))
        self.assertFalse(prefs.slack_allowed("digest", project["id"]))
        # 個別指定を空に戻すと全体設定に従う
        self.admin.patch("/api/projects/{}".format(project["id"]), {"slack_events": []})
        self.assertTrue(prefs.slack_allowed("digest", project["id"]))
        self.assertFalse(prefs.slack_allowed("issue", project["id"]))

    def test_a_stopped_project_never_posts_to_slack(self):
        project = self.make_project("Slack停止")
        self.admin.patch("/api/projects/{}".format(project["id"]),
                         {"notify_enabled": False, "slack_events": ["issue", "digest"]})
        self.assertFalse(prefs.slack_allowed("issue", project["id"]))
        self.assertFalse(prefs.slack_allowed("digest", project["id"]))

    def test_slack_catalogs_are_exposed_to_the_ui(self):
        settings = self.admin.get("/api/settings")[1]
        self.assertEqual([e["value"] for e in settings["slack_events"]],
                         prefs.SLACK_EVENT_KEYS)
        meta = self.admin.get("/api/meta")[1]
        self.assertEqual([e["value"] for e in meta["slack_events"]], prefs.SLACK_EVENT_KEYS)


class TestReparenting(ApiTestCase):
    """タスクを別のタスクの子にする・親を付け替える操作。"""

    def chain(self, project_id, depth, prefix="L"):
        """深さ depth の直系チェーンを作り、上から順に返す。"""
        tasks, parent_id = [], None
        for level in range(depth):
            task = self.make_task(project_id, "{}{}".format(prefix, level),
                                  parent_id=parent_id)
            parent_id = task["id"]
            tasks.append(task)
        return tasks

    def task_row(self, task_id):
        return self.admin.get("/api/tasks/{}".format(task_id))[1]["task"]

    def test_a_top_level_task_can_become_a_child(self):
        project = self.make_project()
        parent = self.make_task(project["id"], "親にする")
        task = self.make_task(project["id"], "子になる")
        status, data = self.admin.patch("/api/tasks/{}".format(task["id"]),
                                        {"parent_id": parent["id"]})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["task"]["parent_id"], parent["id"])

    def test_a_child_can_be_moved_back_to_the_top(self):
        project = self.make_project()
        parent = self.make_task(project["id"], "親")
        child = self.make_task(project["id"], "子", parent_id=parent["id"])
        status, data = self.admin.patch("/api/tasks/{}".format(child["id"]),
                                        {"parent_id": None})
        self.assertEqual(status, 200, data)
        self.assertIsNone(data["task"]["parent_id"])

    def test_children_follow_their_parent(self):
        project = self.make_project()
        new_home = self.make_task(project["id"], "移動先")
        moving = self.make_task(project["id"], "動かす")
        kid = self.make_task(project["id"], "ついてくる子", parent_id=moving["id"])
        self.admin.patch("/api/tasks/{}".format(moving["id"]),
                         {"parent_id": new_home["id"]})
        self.assertEqual(self.task_row(kid["id"])["parent_id"], moving["id"])
        self.assertEqual(self.task_row(moving["id"])["parent_id"], new_home["id"])

    def test_moved_task_lands_at_the_end_of_its_new_siblings(self):
        project = self.make_project()
        parent = self.make_task(project["id"], "親")
        first = self.make_task(project["id"], "既存1", parent_id=parent["id"])
        second = self.make_task(project["id"], "既存2", parent_id=parent["id"])
        self.admin.post("/api/tasks/reorder", {
            "project_id": project["id"],
            "items": [{"id": first["id"], "parent_id": parent["id"], "sort_order": 10},
                      {"id": second["id"], "parent_id": parent["id"], "sort_order": 20}]})
        moved = self.make_task(project["id"], "あとから移動")
        self.admin.patch("/api/tasks/{}".format(moved["id"]), {"parent_id": parent["id"]})
        self.assertGreater(self.task_row(moved["id"])["sort_order"],
                           self.task_row(second["id"])["sort_order"])

    def test_an_explicit_sort_order_still_wins(self):
        project = self.make_project()
        parent = self.make_task(project["id"], "親")
        self.make_task(project["id"], "既存", parent_id=parent["id"])
        moved = self.make_task(project["id"], "割り込ませる")
        self.admin.patch("/api/tasks/{}".format(moved["id"]),
                         {"parent_id": parent["id"], "sort_order": 5})
        self.assertEqual(self.task_row(moved["id"])["sort_order"], 5)

    def test_the_change_is_recorded_in_the_history(self):
        project = self.make_project()
        parent = self.make_task(project["id"], "新しい親")
        task = self.make_task(project["id"], "動かす")
        self.admin.patch("/api/tasks/{}".format(task["id"]), {"parent_id": parent["id"]})
        detail = self.admin.get("/api/tasks/{}".format(task["id"]))[1]
        history = [c["body"] for c in detail["comments"] if c["kind"] == "system"]
        self.assertIn("親タスク: トップレベル → 新しい親", history)
        self.admin.patch("/api/tasks/{}".format(task["id"]), {"parent_id": None})
        detail = self.admin.get("/api/tasks/{}".format(task["id"]))[1]
        history = [c["body"] for c in detail["comments"] if c["kind"] == "system"]
        self.assertIn("親タスク: 新しい親 → トップレベル", history)

    def test_setting_the_same_parent_is_not_logged(self):
        project = self.make_project()
        parent = self.make_task(project["id"], "親")
        child = self.make_task(project["id"], "子", parent_id=parent["id"])
        self.admin.patch("/api/tasks/{}".format(child["id"]),
                         {"parent_id": parent["id"], "title": "子（改名）"})
        detail = self.admin.get("/api/tasks/{}".format(child["id"]))[1]
        history = [c["body"] for c in detail["comments"] if c["kind"] == "system"]
        self.assertEqual([h for h in history if "親タスク" in h], [])

    def test_cannot_become_its_own_parent(self):
        project = self.make_project()
        task = self.make_task(project["id"], "自分")
        status, data = self.admin.patch("/api/tasks/{}".format(task["id"]),
                                        {"parent_id": task["id"]})
        self.assertEqual(status, 400)
        self.assertIn("自分自身", data["error"])

    def test_cannot_move_under_another_project(self):
        mine = self.make_project("こちら")
        theirs = self.make_project("あちら")
        task = self.make_task(mine["id"], "動かす")
        outsider = self.make_task(theirs["id"], "別プロジェクト")
        status, data = self.admin.patch("/api/tasks/{}".format(task["id"]),
                                        {"parent_id": outsider["id"]})
        self.assertEqual(status, 400)
        self.assertIn("プロジェクト", data["error"])

    def test_a_subtree_cannot_be_pushed_past_the_depth_limit(self):
        """親だけでなく、ぶら下がる子孫まで数えて深さを判定すること。"""
        project = self.make_project()
        deep = self.chain(project["id"], 7)          # L0..L6（7 階層）
        lone = self.make_task(project["id"], "単体")
        with_kid = self.make_task(project["id"], "子持ち")
        self.make_task(project["id"], "その子", parent_id=with_kid["id"])

        # 7 階層目の下＝8 階層目なので単体なら入る
        status, data = self.admin.patch("/api/tasks/{}".format(lone["id"]),
                                        {"parent_id": deep[-1]["id"]})
        self.assertEqual(status, 200, data)
        # 子持ちだと 9 階層目ができてしまうので弾く
        status, data = self.admin.patch("/api/tasks/{}".format(with_kid["id"]),
                                        {"parent_id": deep[-1]["id"]})
        self.assertEqual(status, 400, data)
        self.assertIn("階層", data["error"])

    def test_reorder_also_checks_the_depth_limit(self):
        project = self.make_project()
        deep = self.chain(project["id"], 8)          # 8 階層ぶん
        task = self.make_task(project["id"], "はみ出す")
        status, data = self.admin.post("/api/tasks/reorder", {
            "project_id": project["id"],
            "items": [{"id": task["id"], "parent_id": deep[-1]["id"], "sort_order": 10}]})
        self.assertEqual(status, 400, data)
        self.assertIn("階層", data["error"])

    def test_reorder_counts_the_moved_subtree(self):
        project = self.make_project()
        deep = self.chain(project["id"], 7)
        with_kid = self.make_task(project["id"], "子持ち")
        self.make_task(project["id"], "その子", parent_id=with_kid["id"])
        status, data = self.admin.post("/api/tasks/reorder", {
            "project_id": project["id"],
            "items": [{"id": with_kid["id"], "parent_id": deep[-1]["id"], "sort_order": 10}]})
        self.assertEqual(status, 400, data)
        self.assertIn("階層", data["error"])

    def test_reparenting_needs_edit_rights(self):
        project = self.make_project()
        parent = self.make_task(project["id"], "親")
        task = self.make_task(project["id"], "子候補")
        user, email = self.make_user("閲覧者")
        self.admin.put("/api/projects/{}/members".format(project["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"],
                         "role": "viewer"}]})
        client = self.client_for(email)
        self.assertEqual(client.patch("/api/tasks/{}".format(task["id"]),
                                      {"parent_id": parent["id"]})[0], 403)

    def test_parent_change_keeps_the_progress_rollup_consistent(self):
        project = self.make_project()
        parent = self.make_task(project["id"], "集計される親")
        task = self.make_task(project["id"], "進捗100", progress=100)
        self.admin.patch("/api/tasks/{}".format(task["id"]), {"parent_id": parent["id"]})
        rows = self.admin.get("/api/projects/{}/tasks".format(project["id"]))[1]["tasks"]
        moved_parent = next(t for t in rows if t["id"] == parent["id"])
        self.assertEqual(moved_parent["leaf_total"], 1)
        self.assertEqual(moved_parent["leaf_done"], 1)


class TestProjectScoping(ApiTestCase):
    """所属していないプロジェクトの中身が、どの入口からも見えないこと。"""

    def setUp(self):
        super().setUp()
        self.secret = self.make_project("見えてはいけないPJ")
        self.secret_task = self.make_task(self.secret["id"], "秘密のタスク")
        self.secret_issue = self.make_issue(self.secret["id"], "秘密の課題")
        self.mine = self.make_project("参加しているPJ")
        self.user, email = self.make_user("一般メンバー")
        self.admin.put("/api/projects/{}/members".format(self.mine["id"]), {
            "members": [{"principal_type": "user", "principal_id": self.user["id"],
                         "role": "editor"}]})
        self.my_task = self.make_task(self.mine["id"], "見えてよいタスク")
        self.client = self.client_for(email)

    def test_project_list_shows_only_my_projects(self):
        names = [p["name"] for p in self.client.get("/api/projects")[1]["projects"]]
        self.assertEqual(names, ["参加しているPJ"])

    def test_cross_project_task_search_is_scoped(self):
        titles = [t["title"] for t in self.client.get("/api/tasks")[1]["tasks"]]
        self.assertIn("見えてよいタスク", titles)
        self.assertNotIn("秘密のタスク", titles)

    def test_a_single_task_from_another_project_is_refused(self):
        self.assertEqual(
            self.client.get("/api/tasks/{}".format(self.secret_task["id"]))[0], 403)

    def test_cross_project_issue_list_is_scoped(self):
        titles = [i["title"] for i in self.client.get("/api/issues")[1]["issues"]]
        self.assertNotIn("秘密の課題", titles)

    def test_a_single_issue_from_another_project_is_refused(self):
        self.assertEqual(
            self.client.get("/api/issues/{}".format(self.secret_issue["id"]))[0], 403)

    def test_project_scoped_endpoints_are_refused(self):
        for path in ("/api/projects/{}", "/api/projects/{}/tasks",
                     "/api/projects/{}/issues", "/api/projects/{}/bottlenecks",
                     "/api/projects/{}/recurrences"):
            status, _ = self.client.get(path.format(self.secret["id"]))
            self.assertEqual(status, 403, "{} が漏れています".format(path))

    def test_workload_only_counts_my_projects(self):
        self.admin.patch("/api/tasks/{}".format(self.secret_task["id"]),
                         {"assignee_id": self.user["id"], "due_date": "2026-04-10",
                          "estimate_hours": 8})
        self.admin.patch("/api/tasks/{}".format(self.my_task["id"]),
                         {"assignee_id": self.user["id"], "due_date": "2026-04-10",
                          "estimate_hours": 8})
        data = self.client.get("/api/workload")[1]
        blob = json.dumps(data, ensure_ascii=False)
        self.assertIn("参加しているPJ", blob)
        self.assertNotIn("見えてはいけないPJ", blob)

    def test_the_daily_screen_does_not_leak_other_projects(self):
        self.admin.patch("/api/tasks/{}".format(self.secret_task["id"]),
                         {"assignee_id": self.user["id"], "due_date": "2020-01-01"})
        blob = json.dumps(self.client.get("/api/daily")[1], ensure_ascii=False)
        self.assertNotIn("秘密のタスク", blob)

    def test_cannot_create_a_task_in_another_project(self):
        status, _ = self.client.post("/api/tasks", {
            "project_id": self.secret["id"], "title": "割り込み"})
        self.assertEqual(status, 403)

    def test_notification_settings_list_only_my_projects(self):
        data = self.client.get("/api/me/notification-settings")[1]
        self.assertEqual([p["name"] for p in data["projects"]], ["参加しているPJ"])

    def join_secret(self):
        self.admin.put("/api/projects/{}/members".format(self.secret["id"]), {
            "members": [{"principal_type": "user", "principal_id": self.user["id"],
                         "role": "viewer"}]})

    def leave_secret(self):
        self.admin.put("/api/projects/{}/members".format(self.secret["id"]), {"members": []})

    def test_daily_drops_tasks_left_behind_after_leaving_a_project(self):
        """在籍中に割り当てられ、その後メンバーから外れた場合も見えないこと。"""
        self.join_secret()
        self.admin.patch("/api/tasks/{}".format(self.secret_task["id"]),
                         {"assignee_id": self.user["id"], "due_date": "2020-01-01"})
        self.assertIn("秘密のタスク",
                      json.dumps(self.client.get("/api/daily")[1], ensure_ascii=False))
        self.leave_secret()
        self.assertNotIn("秘密のタスク",
                         json.dumps(self.client.get("/api/daily")[1], ensure_ascii=False))

    def test_due_notifications_skip_projects_i_cannot_see(self):
        self.join_secret()
        self.admin.patch("/api/tasks/{}".format(self.secret_task["id"]),
                         {"assignee_id": self.user["id"], "due_date": "2020-01-01"})
        self.leave_secret()
        notify.scan_due_tasks()
        overdue = db.query(
            "SELECT title FROM notifications WHERE user_id=%s AND type IN ('overdue','due_soon')",
            (self.user["id"],))
        self.assertEqual(list(overdue), [])

    def test_cannot_assign_a_task_to_a_non_member(self):
        outsider, _ = self.make_user("よその人")
        status, data = self.admin.patch("/api/tasks/{}".format(self.my_task["id"]),
                                        {"assignee_id": outsider["id"]})
        self.assertEqual(status, 400, data)
        self.assertIn("メンバー", data["error"])
        status, data = self.admin.post("/api/tasks", {
            "project_id": self.mine["id"], "title": "新規", "assignee_id": outsider["id"]})
        self.assertEqual(status, 400, data)

    def test_cannot_make_a_non_member_the_issue_owner(self):
        outsider, _ = self.make_user("よその人2")
        status, data = self.admin.post("/api/issues", {
            "project_id": self.mine["id"], "title": "課題", "owner_id": outsider["id"]})
        self.assertEqual(status, 400, data)
        self.assertIn("メンバー", data["error"])

    def test_a_member_can_still_be_assigned(self):
        status, data = self.admin.patch("/api/tasks/{}".format(self.my_task["id"]),
                                        {"assignee_id": self.user["id"]})
        self.assertEqual(status, 200, data)


class TestTodos(ApiTestCase):
    """個人 ToDo（プロジェクトに属さない、本人だけの覚え書き）。"""

    def add(self, client, title="牛乳を買う", **kwargs):
        payload = {"title": title}
        payload.update(kwargs)
        status, data = client.post("/api/todos", payload)
        self.assertEqual(status, 201, data)
        return data["todo"]

    def test_create_and_list(self):
        todo = self.add(self.admin, "経費精算を出す", due_date="2026-10-01")
        self.assertEqual(todo["title"], "経費精算を出す")
        self.assertEqual(todo["due_date"], "2026-10-01")
        data = self.admin.get("/api/todos")[1]
        self.assertIn("経費精算を出す", [t["title"] for t in data["todos"]])
        self.assertGreaterEqual(data["open_count"], 1)

    def test_only_the_owner_can_see_it(self):
        self.add(self.admin, "管理者の秘密のメモ")
        _, email = self.make_user("別の人")
        other = self.client_for(email)
        self.assertEqual(other.get("/api/todos")[1]["todos"], [])

    def test_even_an_admin_cannot_see_someone_elses(self):
        user, email = self.make_user("持ち主")
        mine = self.add(self.client_for(email), "本人だけのメモ")
        self.assertEqual(self.admin.get("/api/todos/{}".format(mine["id"]))[0], 404)
        self.assertEqual(
            self.admin.patch("/api/todos/{}".format(mine["id"]), {"title": "書き換え"})[0], 404)
        self.assertEqual(self.admin.delete("/api/todos/{}".format(mine["id"]))[0], 404)
        blob = json.dumps(self.admin.get("/api/todos")[1], ensure_ascii=False)
        self.assertNotIn("本人だけのメモ", blob)

    def test_completing_and_reopening(self):
        todo = self.add(self.admin)
        data = self.admin.patch("/api/todos/{}".format(todo["id"]), {"is_done": True})[1]
        self.assertTrue(data["todo"]["is_done"])
        self.assertIsNotNone(data["todo"]["done_at"])
        # 既定では未完了だけが返る
        self.assertNotIn(todo["id"], [t["id"] for t in self.admin.get("/api/todos")[1]["todos"]])
        self.assertIn(todo["id"],
                      [t["id"] for t in self.admin.get("/api/todos?include_done=1")[1]["todos"]])
        data = self.admin.patch("/api/todos/{}".format(todo["id"]), {"is_done": False})[1]
        self.assertFalse(data["todo"]["is_done"])
        self.assertIsNone(data["todo"]["done_at"])

    def test_editing_and_deleting(self):
        todo = self.add(self.admin)
        data = self.admin.patch("/api/todos/{}".format(todo["id"]),
                                {"title": "牛乳と卵を買う", "due_date": "2026-11-05"})[1]
        self.assertEqual(data["todo"]["title"], "牛乳と卵を買う")
        self.assertEqual(data["todo"]["due_date"], "2026-11-05")
        self.assertEqual(self.admin.delete("/api/todos/{}".format(todo["id"]))[0], 200)
        self.assertEqual(self.admin.get("/api/todos/{}".format(todo["id"]))[0], 404)

    def test_a_title_is_required(self):
        self.assertEqual(self.admin.post("/api/todos", {"title": "  "})[0], 400)

    def test_reorder_is_limited_to_my_own(self):
        first = self.add(self.admin, "A")
        second = self.add(self.admin, "B")
        status, _ = self.admin.post("/api/todos/reorder",
                                    {"ids": [second["id"], first["id"]]})
        self.assertEqual(status, 200)
        titles = [t["title"] for t in self.admin.get("/api/todos")[1]["todos"]]
        self.assertLess(titles.index("B"), titles.index("A"))

        _, email = self.make_user("よその人")
        theirs = self.add(self.client_for(email), "他人の ToDo")
        status, data = self.admin.post("/api/todos/reorder", {"ids": [theirs["id"]]})
        self.assertEqual(status, 400, data)

    def test_promoting_to_a_project_task(self):
        project = self.make_project()
        todo = self.add(self.admin, "設計方針をまとめる", due_date="2026-12-01")
        status, data = self.admin.post("/api/todos/{}/promote".format(todo["id"]),
                                       {"project_id": project["id"]})
        self.assertEqual(status, 201, data)
        task = data["task"]
        self.assertEqual(task["title"], "設計方針をまとめる")
        self.assertEqual(task["due_date"], "2026-12-01")
        self.assertEqual(task["project_id"], project["id"])
        # 引き上げた ToDo は残さない
        self.assertEqual(self.admin.get("/api/todos/{}".format(todo["id"]))[0], 404)

    def test_cannot_promote_into_a_project_i_cannot_edit(self):
        project = self.make_project()
        user, email = self.make_user("閲覧者")
        self.admin.put("/api/projects/{}/members".format(project["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"],
                         "role": "viewer"}]})
        client = self.client_for(email)
        todo = self.add(client, "上げられない")
        self.assertEqual(client.post("/api/todos/{}/promote".format(todo["id"]),
                                     {"project_id": project["id"]})[0], 403)

    def test_todos_show_up_on_the_daily_screen(self):
        self.add(self.admin, "今日の確認に出る ToDo")
        blob = json.dumps(self.admin.get("/api/daily")[1], ensure_ascii=False)
        self.assertIn("今日の確認に出る ToDo", blob)

    def test_todos_are_removed_with_the_account(self):
        user, email = self.make_user("退職者")
        self.add(self.client_for(email), "消える ToDo")
        self.admin.delete("/api/users/{}".format(user["id"]))
        self.assertEqual(
            db.scalar("SELECT COUNT(*) AS c FROM todos WHERE user_id=%s", (user["id"],),
                      default=0), 0)

    def test_todos_need_a_login(self):
        self.assertEqual(Client(self.base).get("/api/todos")[0], 401)


class TestTaskImport(ApiTestCase):
    """表計算ソフトからの一括取り込み。"""

    def rows(self, *items):
        return [dict(zip(("title", "assignee", "start_date", "due_date", "category",
                          "priority", "status", "progress", "estimate_hours",
                          "description", "is_milestone", "level", "parent"),
                         list(row) + [""] * (13 - len(row))))
                for row in items]

    def import_rows(self, project_id, rows, dry_run=False):
        return self.admin.post("/api/projects/{}/tasks/import".format(project_id),
                               {"rows": rows, "dry_run": dry_run})

    def tasks_of(self, project_id):
        return self.admin.get("/api/projects/{}/tasks".format(project_id))[1]["tasks"]

    def test_basic_import(self):
        project = self.make_project()
        status, data = self.import_rows(project["id"], self.rows(
            ("要件定義", "", "2026/10/1", "2026/10/15", "設計・企画", "高", "進行中", "40", "20"),
            ("設計", "", "2026-10-16", "2026-10-31")))
        self.assertEqual(status, 201, data)
        self.assertEqual(data["created"], 2)
        tasks = {t["title"]: t for t in self.tasks_of(project["id"])}
        first = tasks["要件定義"]
        self.assertEqual(first["start_date"], "2026-10-01")
        self.assertEqual(first["due_date"], "2026-10-15")
        self.assertEqual(first["category"], "design")
        self.assertEqual(first["priority"], 2)
        self.assertEqual(first["status"], "doing")
        self.assertEqual(first["progress"], 40)
        self.assertEqual(float(first["estimate_hours"]), 20.0)

    def test_indentation_becomes_a_hierarchy(self):
        project = self.make_project()
        self.import_rows(project["id"], self.rows(
            ("親A",), ("  子A1",), ("    孫A",), ("  子A2",), ("親B",)))
        tasks = {t["title"]: t for t in self.tasks_of(project["id"])}
        self.assertIsNone(tasks["親A"]["parent_id"])
        self.assertEqual(tasks["子A1"]["parent_id"], tasks["親A"]["id"])
        self.assertEqual(tasks["孫A"]["parent_id"], tasks["子A1"]["id"])
        self.assertEqual(tasks["子A2"]["parent_id"], tasks["親A"]["id"])
        self.assertIsNone(tasks["親B"]["parent_id"])

    def test_a_level_column_wins_over_indentation(self):
        project = self.make_project()
        rows = [{"title": "親", "level": "1"}, {"title": "子", "level": "2"}]
        self.import_rows(project["id"], rows)
        tasks = {t["title"]: t for t in self.tasks_of(project["id"])}
        self.assertEqual(tasks["子"]["parent_id"], tasks["親"]["id"])

    def test_a_parent_column_links_by_name(self):
        project = self.make_project()
        rows = [{"title": "土台"}, {"title": "上物", "parent": "土台"}]
        self.import_rows(project["id"], rows)
        tasks = {t["title"]: t for t in self.tasks_of(project["id"])}
        self.assertEqual(tasks["上物"]["parent_id"], tasks["土台"]["id"])

    def test_various_date_formats(self):
        project = self.make_project()
        self.import_rows(project["id"], [
            {"title": "スラッシュ", "due_date": "2026/12/24"},
            {"title": "和風", "due_date": "2026年12月25日"},
            {"title": "曜日つき", "due_date": "2026/12/26(土)"},
            {"title": "ハイフン", "due_date": "2026-12-27"},
            {"title": "読めない", "due_date": "来週あたり"},
        ])
        tasks = {t["title"]: t for t in self.tasks_of(project["id"])}
        self.assertEqual(tasks["スラッシュ"]["due_date"], "2026-12-24")
        self.assertEqual(tasks["和風"]["due_date"], "2026-12-25")
        self.assertEqual(tasks["曜日つき"]["due_date"], "2026-12-26")
        self.assertEqual(tasks["ハイフン"]["due_date"], "2026-12-27")
        self.assertIsNone(tasks["読めない"]["due_date"], "読めない日付は空にする")

    def test_assignee_is_matched_by_name_or_email(self):
        project = self.make_project()
        user, email = self.make_user("担当 太郎")
        self.admin.put("/api/projects/{}/members".format(project["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"],
                         "role": "editor"}]})
        self.import_rows(project["id"], [
            {"title": "氏名で", "assignee": "担当 太郎"},
            {"title": "メールで", "assignee": email},
        ])
        tasks = {t["title"]: t for t in self.tasks_of(project["id"])}
        self.assertEqual(tasks["氏名で"]["assignee_id"], user["id"])
        self.assertEqual(tasks["メールで"]["assignee_id"], user["id"])

    def test_an_unknown_assignee_is_reported_but_does_not_stop_the_import(self):
        project = self.make_project()
        status, data = self.import_rows(project["id"], [
            {"title": "宛先不明", "assignee": "居ない 人"}])
        self.assertEqual(status, 201, data)
        self.assertEqual(data["created"], 1)
        self.assertEqual(len(data["problems"]), 1)
        self.assertIn("メンバーに見つかりません", data["problems"][0]["message"])
        self.assertIsNone(self.tasks_of(project["id"])[0]["assignee_id"])

    def test_dry_run_changes_nothing(self):
        project = self.make_project()
        status, data = self.import_rows(project["id"],
                                        [{"title": "下見"}], dry_run=True)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["would_create"], 1)
        self.assertEqual(self.tasks_of(project["id"]), [])

    def test_rows_without_a_title_are_reported(self):
        project = self.make_project()
        data = self.import_rows(project["id"],
                                [{"title": "  "}, {"title": "ちゃんとある"}], dry_run=True)[1]
        self.assertEqual(data["would_create"], 1)
        self.assertIn("タスク名が空", data["problems"][0]["message"])

    def test_import_needs_edit_rights(self):
        project = self.make_project()
        user, email = self.make_user("閲覧のみ")
        self.admin.put("/api/projects/{}/members".format(project["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"],
                         "role": "viewer"}]})
        client = self.client_for(email)
        self.assertEqual(client.post(
            "/api/projects/{}/tasks/import".format(project["id"]),
            {"rows": [{"title": "だめ"}]})[0], 403)

    def test_too_many_rows_are_refused(self):
        project = self.make_project()
        status, _ = self.import_rows(project["id"],
                                     [{"title": "x"} for _ in range(1001)])
        self.assertEqual(status, 400)

    def test_import_keeps_the_existing_order(self):
        project = self.make_project()
        self.make_task(project["id"], "先にあったタスク")
        self.import_rows(project["id"], [{"title": "あとから1"}, {"title": "あとから2"}])
        titles = [t["title"] for t in sorted(self.tasks_of(project["id"]),
                                             key=lambda t: t["sort_order"])]
        self.assertEqual(titles, ["先にあったタスク", "あとから1", "あとから2"])


class TestBulkEdit(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.project = self.make_project()
        self.tasks = [self.make_task(self.project["id"], name,
                                     start_date="2026-10-0{}".format(i + 1),
                                     due_date="2026-10-0{}".format(i + 2))
                      for i, name in enumerate(["甲", "乙", "丙"])]
        self.ids = [t["id"] for t in self.tasks]

    def reload(self):
        return {t["title"]: t for t in
                self.admin.get("/api/projects/{}/tasks".format(self.project["id"]))[1]["tasks"]}

    def test_status_is_applied_to_all(self):
        status, data = self.admin.post("/api/tasks/bulk",
                                       {"ids": self.ids[:2], "status": "doing"})
        self.assertEqual(status, 200, data)
        rows = self.reload()
        self.assertEqual(rows["甲"]["status"], "doing")
        self.assertEqual(rows["乙"]["status"], "doing")
        self.assertEqual(rows["丙"]["status"], "todo", "選んでいないタスクは変えない")

    def test_marking_done_also_fills_the_progress(self):
        self.admin.post("/api/tasks/bulk", {"ids": self.ids, "status": "done"})
        for row in self.reload().values():
            self.assertEqual(row["progress"], 100)

    def test_shifting_the_schedule(self):
        status, data = self.admin.post("/api/tasks/bulk",
                                       {"ids": self.ids, "action": "shift", "days": 7})
        self.assertEqual(status, 200, data)
        rows = self.reload()
        self.assertEqual(rows["甲"]["start_date"], "2026-10-08")
        self.assertEqual(rows["甲"]["due_date"], "2026-10-09")
        self.admin.post("/api/tasks/bulk", {"ids": self.ids, "action": "shift", "days": -7})
        self.assertEqual(self.reload()["甲"]["start_date"], "2026-10-01")

    def test_shifting_needs_a_number_of_days(self):
        self.assertEqual(self.admin.post(
            "/api/tasks/bulk", {"ids": self.ids, "action": "shift", "days": 0})[0], 400)

    def test_bulk_delete_takes_the_children_too(self):
        child = self.make_task(self.project["id"], "子", parent_id=self.ids[0])
        status, data = self.admin.post("/api/tasks/bulk",
                                       {"ids": [self.ids[0]], "action": "delete"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["deleted"], 2)
        self.assertEqual(self.admin.get("/api/tasks/{}".format(child["id"]))[0], 404)

    def test_the_change_is_recorded_on_every_task(self):
        self.admin.post("/api/tasks/bulk", {"ids": self.ids, "priority": 3})
        detail = self.admin.get("/api/tasks/{}".format(self.ids[0]))[1]
        history = [c["body"] for c in detail["comments"] if c["kind"] == "system"]
        self.assertTrue(any("一括更新" in h and "最重要" in h for h in history))

    def test_a_non_member_cannot_be_assigned_in_bulk(self):
        outsider, _ = self.make_user("よその人")
        status, data = self.admin.post("/api/tasks/bulk",
                                       {"ids": self.ids, "assignee_id": outsider["id"]})
        self.assertEqual(status, 400, data)
        self.assertIn("メンバー", data["error"])

    def test_tasks_from_a_project_i_cannot_edit_are_refused(self):
        user, email = self.make_user("編集不可")
        self.admin.put("/api/projects/{}/members".format(self.project["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"],
                         "role": "viewer"}]})
        client = self.client_for(email)
        self.assertEqual(client.post("/api/tasks/bulk",
                                     {"ids": self.ids, "status": "done"})[0], 403)

    def test_unknown_ids_are_refused(self):
        self.assertEqual(self.admin.post("/api/tasks/bulk",
                                         {"ids": [999999], "status": "done"})[0], 404)

    def test_an_empty_selection_is_refused(self):
        self.assertEqual(self.admin.post("/api/tasks/bulk", {"ids": []})[0], 400)


class TestSearch(ApiTestCase):
    """横断検索。見えないプロジェクトのものは絶対に返さない。"""

    def setUp(self):
        super().setUp()
        self.mine = self.make_project("見えるPJ")
        self.secret = self.make_project("見えないPJ")
        self.task = self.make_task(self.mine["id"], "移行手順書の作成")
        self.hidden = self.make_task(self.secret["id"], "移行手順書の裏レシピ")
        self.issue = self.make_issue(self.mine["id"], "移行時の停止時間が読めない")
        self.admin.post("/api/tasks/{}/comments".format(self.task["id"]),
                        {"body": "移行のリハーサルは来週やります"})
        self.user, self.email = self.make_user("検索する人")
        self.admin.put("/api/projects/{}/members".format(self.mine["id"]), {
            "members": [{"principal_type": "user", "principal_id": self.user["id"],
                         "role": "editor"}]})
        self.client = self.client_for(self.email)

    def find(self, client, keyword):
        data = client.get("/api/search?q={}".format(urllib.parse.quote(keyword)))[1]
        return {g["kind"]: [i.get("title") or i.get("name") or i.get("excerpt")
                            for i in g["items"]]
                for g in data["groups"]}

    def test_finds_tasks_issues_and_comments(self):
        found = self.find(self.admin, "移行")
        self.assertIn("移行手順書の作成", found.get("task", []))
        self.assertIn("移行時の停止時間が読めない", found.get("issue", []))
        self.assertTrue(found.get("comment"), "コメントも検索できること")

    def test_projects_are_searchable(self):
        self.assertIn("見えるPJ", self.find(self.admin, "見えるPJ").get("project", []))

    def test_results_are_scoped_to_my_projects(self):
        found = self.find(self.client, "移行")
        self.assertIn("移行手順書の作成", found.get("task", []))
        self.assertNotIn("移行手順書の裏レシピ", found.get("task", []))
        self.assertNotIn("見えないPJ", found.get("project", []))

    def test_comments_from_invisible_projects_are_excluded(self):
        self.admin.post("/api/tasks/{}/comments".format(self.hidden["id"]),
                        {"body": "これは部外者に見えてはいけない移行メモ"})
        excerpts = " ".join(self.find(self.client, "移行").get("comment", []))
        self.assertNotIn("部外者に見えてはいけない", excerpts)

    def test_my_todos_are_searchable_but_only_mine(self):
        self.admin.post("/api/todos", {"title": "移行の書類を出す"})
        self.assertIn("移行の書類を出す", self.find(self.admin, "移行").get("todo", []))
        self.assertNotIn("移行の書類を出す", self.find(self.client, "移行").get("todo", []))

    def test_a_short_keyword_returns_nothing(self):
        data = self.admin.get("/api/search?q=%E7%A7%BB")[1]
        self.assertEqual(data["total"], 0)
        self.assertIn("2 文字以上", data["message"])

    def test_search_needs_a_login(self):
        self.assertEqual(Client(self.base).get("/api/search?q=test")[0], 401)

    def test_comment_excerpt_shows_the_surrounding_text(self):
        found = self.admin.get("/api/search?q=%E3%83%AA%E3%83%8F%E3%83%BC%E3%82%B5%E3%83%AB")[1]
        comments = next((g for g in found["groups"] if g["kind"] == "comment"), None)
        self.assertIsNotNone(comments)
        self.assertIn("リハーサル", comments["items"][0]["excerpt"])


class TestHolidayApi(ApiTestCase):
    def test_national_holidays_are_returned(self):
        data = self.admin.get("/api/holidays?from=2026-04-25&to=2026-05-10")[1]
        days = {h["day"]: h["name"] for h in data["holidays"]}
        self.assertEqual(days.get("2026-04-29"), "昭和の日")
        self.assertEqual(days.get("2026-05-06"), "振替休日")
        self.assertTrue(data["enabled"])

    def test_company_holidays_can_be_added_and_removed(self):
        status, _ = self.admin.post("/api/holidays",
                                    {"day": "2026-12-30", "name": "年末年始休業"})
        self.assertEqual(status, 201)
        data = self.admin.get("/api/holidays?from=2026-12-01&to=2026-12-31")[1]
        entry = next(h for h in data["holidays"] if h["day"] == "2026-12-30")
        self.assertEqual(entry["name"], "年末年始休業")
        self.assertTrue(entry["company"])
        self.admin.delete("/api/holidays/2026-12-30")
        data = self.admin.get("/api/holidays?from=2026-12-01&to=2026-12-31")[1]
        self.assertNotIn("2026-12-30", [h["day"] for h in data["holidays"]])

    def test_only_admins_can_change_company_holidays(self):
        _, email = self.make_user("一般利用者")
        client = self.client_for(email)
        self.assertEqual(client.post("/api/holidays", {"day": "2026-12-31"})[0], 403)
        self.assertEqual(client.delete("/api/holidays/2026-12-31")[0], 403)
        self.assertEqual(client.get("/api/holidays")[0], 200, "閲覧は誰でもできる")

    def test_the_feature_can_be_switched_off(self):
        db.set_setting("use_holidays", "0")
        try:
            data = self.admin.get("/api/holidays?from=2026-04-25&to=2026-05-10")[1]
            self.assertFalse(data["enabled"])
            self.assertEqual(data["holidays"], [])
        finally:
            db.set_setting("use_holidays", "1")

    def test_workload_capacity_drops_on_holiday_weeks(self):
        data = self.admin.get("/api/workload?weeks=8")[1]
        weeks = {w["start"]: w for w in data["weeks"]}
        self.assertTrue(any(w["capacity"] < 40 for w in weeks.values())
                        or all(not w["holidays"] for w in weeks.values()),
                        "祝日のある週は使える時間が減ること")
        for week in weeks.values():
            self.assertEqual(week["capacity"], 8.0 * (5 - len(week["holidays"])))


class TestCommentMentions(ApiTestCase):
    """コメントで名前を呼ばれた人への通知。"""

    def setUp(self):
        super().setUp()
        self.project = self.make_project()
        self.task = self.make_task(self.project["id"], "メンション対象")
        self.hanako, self.hanako_mail = self.make_user("佐藤 花子")
        self.ichiro, self.ichiro_mail = self.make_user("鈴木 一郎")
        self.admin.put("/api/projects/{}/members".format(self.project["id"]), {
            "members": [{"principal_type": "user", "principal_id": self.hanako["id"],
                         "role": "editor"},
                        {"principal_type": "user", "principal_id": self.ichiro["id"],
                         "role": "editor"}]})

    def notifications_for(self, user_id, ntype=None):
        sql = "SELECT type, title, body FROM notifications WHERE user_id=%s"
        params = [user_id]
        if ntype:
            sql += " AND type=%s"
            params.append(ntype)
        return list(db.query(sql, params))      # db.query はタプルを返す

    def comment(self, body, client=None):
        status, data = (client or self.admin).post(
            "/api/tasks/{}/comments".format(self.task["id"]), {"body": body})
        self.assertEqual(status, 201, data)
        return data

    def test_a_mentioned_member_is_notified(self):
        data = self.comment("@佐藤 花子 確認おねがいします")
        self.assertEqual(data["mentioned"], ["佐藤 花子"])
        rows = self.notifications_for(self.hanako["id"], "mention")
        self.assertEqual(len(rows), 1)
        self.assertIn("あなたを呼んでいます", rows[0]["title"])
        self.assertIn("確認おねがいします", rows[0]["body"])

    def test_someone_not_mentioned_gets_nothing(self):
        self.comment("@佐藤 花子 おねがいします")
        self.assertEqual(self.notifications_for(self.ichiro["id"]), [])

    def test_mentioning_works_even_without_any_other_involvement(self):
        """担当でも作成者でもコメント済みでもない人を、名前だけで呼べること。"""
        self.assertEqual(self.notifications_for(self.ichiro["id"]), [])
        self.comment("@鈴木 一郎 も見ておいてください")
        self.assertEqual(len(self.notifications_for(self.ichiro["id"], "mention")), 1)

    def test_a_mentioned_person_does_not_also_get_the_plain_comment_notice(self):
        """担当者を呼んだときに、通知が二重に飛ばないこと。"""
        self.admin.patch("/api/tasks/{}".format(self.task["id"]),
                         {"assignee_id": self.hanako["id"]})
        self.comment("@佐藤 花子 おねがいします")
        types = [r["type"] for r in self.notifications_for(self.hanako["id"])]
        self.assertEqual(types.count("mention"), 1)
        self.assertEqual(types.count("comment"), 0)

    def test_mentioning_yourself_does_nothing(self):
        client = self.client_for(self.hanako_mail)
        data = self.comment("@佐藤 花子 メモ", client=client)
        self.assertEqual(data["mentioned"], [])
        self.assertEqual(self.notifications_for(self.hanako["id"], "mention"), [])

    def test_a_non_member_cannot_be_mentioned(self):
        outsider, _ = self.make_user("部外者 太郎")
        data = self.comment("@部外者 太郎 見て")
        self.assertEqual(data["mentioned"], [])
        self.assertEqual(self.notifications_for(outsider["id"]), [])

    def test_mentions_follow_the_notification_preferences(self):
        client = self.client_for(self.hanako_mail)
        client.put("/api/me/notification-settings", {"mention": False})
        self.assertFalse(prefs.email_allowed(self.hanako["id"], "mention",
                                             self.project["id"]))
        # 画面の通知には残す
        self.comment("@佐藤 花子 おねがい")
        self.assertEqual(len(self.notifications_for(self.hanako["id"], "mention")), 1)

    def test_mention_is_offered_as_a_preference(self):
        data = self.admin.get("/api/me/notification-settings")[1]
        self.assertIn("mention", [e["value"] for e in data["events"]])
        self.assertTrue(data["prefs"]["mention"])

    def test_issue_comments_support_mentions(self):
        issue = self.make_issue(self.project["id"], "メンションする課題")
        status, data = self.admin.post("/api/issues/{}/comments".format(issue["id"]),
                                       {"body": "@鈴木 一郎 対応おねがいします"})
        self.assertEqual(status, 201, data)
        self.assertEqual(data["mentioned"], ["鈴木 一郎"])
        rows = self.notifications_for(self.ichiro["id"], "mention")
        self.assertEqual(len(rows), 1)
        self.assertIn("メンションする課題", rows[0]["title"])

    def test_a_muted_project_stays_quiet(self):
        client = self.client_for(self.hanako_mail)
        client.put("/api/me/notification-settings",
                   {"muted_project_ids": [self.project["id"]]})
        self.assertFalse(prefs.email_allowed(self.hanako["id"], "mention",
                                             self.project["id"]))


class TestGanttTaskCreation(ApiTestCase):
    """ガント画面からの追加は、通常のタスク作成 API をそのまま使う。
    期間つきで作れることと、ガントが返すデータで画面を組めることを押さえる。"""

    def test_a_task_can_be_created_with_a_span(self):
        project = self.make_project()
        status, data = self.admin.post("/api/tasks", {
            "project_id": project["id"], "title": "ドラッグで作った",
            "start_date": "2026-11-02", "due_date": "2026-11-06"})
        self.assertEqual(status, 201, data)
        self.assertEqual(data["task"]["start_date"], "2026-11-02")
        self.assertEqual(data["task"]["due_date"], "2026-11-06")

    def test_a_single_day_span_is_allowed(self):
        project = self.make_project()
        status, data = self.admin.post("/api/tasks", {
            "project_id": project["id"], "title": "1日だけ",
            "start_date": "2026-11-02", "due_date": "2026-11-02"})
        self.assertEqual(status, 201, data)

    def test_the_gantt_payload_has_what_the_form_needs(self):
        project = self.make_project()
        self.make_task(project["id"], "既存")
        data = self.admin.get("/api/projects/{}/tasks".format(project["id"]))[1]
        for key in ("tasks", "deps", "project", "members"):
            self.assertIn(key, data, "{} が返っていません".format(key))

    def test_a_viewer_cannot_create_from_the_gantt(self):
        project = self.make_project()
        user, email = self.make_user("閲覧のみ")
        self.admin.put("/api/projects/{}/members".format(project["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"],
                         "role": "viewer"}]})
        client = self.client_for(email)
        self.assertEqual(client.post("/api/tasks", {
            "project_id": project["id"], "title": "だめ",
            "start_date": "2026-11-02", "due_date": "2026-11-06"})[0], 403)


class TestStaticCaching(ApiTestCase):
    """更新したソースがブラウザに届くこと（キャッシュで古いままにならないこと）。"""

    def raw(self, path, headers=None):
        request = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def test_scripts_are_revalidated_every_time(self):
        """期限で寝かせず、毎回サーバーに確認させること。"""
        status, headers, _body = self.raw("/js/app.js")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "no-cache")
        self.assertNotIn("max-age", headers.get("Cache-Control", ""))
        self.assertTrue(headers.get("ETag"))
        self.assertTrue(headers.get("Last-Modified"))

    def test_an_unchanged_file_comes_back_as_304(self):
        _status, headers, body = self.raw("/js/app.js")
        etag = headers["ETag"]
        status, _headers, second = self.raw("/js/app.js", {"If-None-Match": etag})
        self.assertEqual(status, 304)
        self.assertEqual(second, b"", "304 に本文を付けないこと")
        self.assertTrue(body, "初回は本文が返ること")

    def test_a_stale_etag_gets_the_new_file(self):
        status, _headers, body = self.raw("/js/app.js", {"If-None-Match": '"nonsense"'})
        self.assertEqual(status, 200)
        self.assertTrue(body)

    def test_a_weak_etag_is_accepted(self):
        _status, headers, _body = self.raw("/css/style.css")
        status, _h, _b = self.raw("/css/style.css",
                                  {"If-None-Match": "W/" + headers["ETag"]})
        self.assertEqual(status, 304)

    def test_if_modified_since_is_honoured(self):
        _status, headers, _body = self.raw("/css/style.css")
        status, _h, _b = self.raw("/css/style.css",
                                  {"If-Modified-Since": headers["Last-Modified"]})
        self.assertEqual(status, 304)

    def test_different_files_have_different_etags(self):
        _s1, h1, _b1 = self.raw("/js/app.js")
        _s2, h2, _b2 = self.raw("/js/api.js")
        self.assertNotEqual(h1["ETag"], h2["ETag"])

    def test_the_page_itself_is_never_stored(self):
        _status, headers, _body = self.raw("/")
        self.assertEqual(headers.get("Cache-Control"), "no-store")

    def test_api_responses_are_never_stored(self):
        status, headers, _body = self.raw("/api/meta")
        self.assertIn(status, (200, 401))
        self.assertEqual(headers.get("Cache-Control"), "no-store")


class TestRecurrenceGrouping(ApiTestCase):
    """週1の打ち合わせを、1つの親タスクにまとめて扱う。"""

    def setUp(self):
        super().setUp()
        self.project = self.make_project("定例のあるPJ")
        self.parent = self.make_task(self.project["id"], "週次定例（9月〜12月）",
                                     start_date="2026-09-01", due_date="2026-12-25")

    def make_rule(self, **kwargs):
        payload = {
            "title": "週次定例", "freq": "weekly", "weekdays": "0", "interval_n": 1,
            "lead_days": 0, "next_on": "2026-09-07", "category": "meeting",
        }
        payload.update(kwargs)
        status, data = self.admin.post(
            "/api/projects/{}/recurrences".format(self.project["id"]), payload)
        self.assertEqual(status, 201, data)
        return data["recurrence"]

    def tasks(self):
        return self.admin.get(
            "/api/projects/{}/tasks".format(self.project["id"]))[1]["tasks"]

    def test_occurrences_are_created_under_the_parent(self):
        rule = self.make_rule(parent_id=self.parent["id"])
        self.assertEqual(rule["parent_id"], self.parent["id"])
        self.assertEqual(rule["parent_title"], "週次定例（9月〜12月）")
        status, data = self.admin.post("/api/recurrences/{}/run".format(rule["id"]), {})
        self.assertEqual(status, 201, data)
        self.assertEqual(data["task"]["parent_id"], self.parent["id"])

    def test_the_parent_keeps_its_own_span_and_rolls_up(self):
        rule = self.make_rule(parent_id=self.parent["id"])
        for _ in range(3):
            self.admin.post("/api/recurrences/{}/run".format(rule["id"]), {})
        parent = next(t for t in self.tasks() if t["id"] == self.parent["id"])
        self.assertEqual(parent["child_count"], 3)
        self.assertEqual(parent["leaf_total"], 3)
        # 親自身の期間は保たれる（先の予定まで引いたバーが縮まないこと）
        self.assertEqual(parent["rollup_start"], "2026-09-01")
        self.assertEqual(parent["rollup_due"], "2026-12-25")

    def test_completing_occurrences_moves_the_parent_progress(self):
        rule = self.make_rule(parent_id=self.parent["id"])
        made = [self.admin.post("/api/recurrences/{}/run".format(rule["id"]), {})[1]["task"]
                for _ in range(4)]
        self.admin.post("/api/tasks/bulk", {"ids": [made[0]["id"], made[1]["id"]],
                                            "status": "done"})
        parent = next(t for t in self.tasks() if t["id"] == self.parent["id"])
        self.assertEqual(parent["leaf_done"], 2)
        self.assertEqual(parent["rollup_progress"], 50)

    def test_an_occurrence_can_be_moved_without_touching_the_rule(self):
        """今週だけ日程がずれても、翌週以降は元の規則どおりに出ること。"""
        rule = self.make_rule(parent_id=self.parent["id"])
        task = self.admin.post("/api/recurrences/{}/run".format(rule["id"]), {})[1]["task"]
        status, data = self.admin.patch("/api/tasks/{}".format(task["id"]),
                                        {"due_date": "2026-09-09"})
        self.assertEqual(status, 200, data)
        moved = self.admin.get("/api/tasks/{}".format(task["id"]))[1]["task"]
        self.assertEqual(moved["due_date"], "2026-09-09")
        self.assertEqual(moved["parent_id"], self.parent["id"], "親からは外れない")
        rules = self.admin.get(
            "/api/projects/{}/recurrences".format(self.project["id"]))[1]["recurrences"]
        self.assertEqual(rules[0]["next_on"], "2026-09-14", "規則の次回は動かない")

    def test_a_backfilled_occurrence_does_not_get_inverted_dates(self):
        """予定日が過ぎている回を作っても、開始日が期限より後にならないこと。"""
        rule = self.make_rule(next_on="2026-01-05")     # 過去の日付
        task = self.admin.post("/api/recurrences/{}/run".format(rule["id"]), {})[1]["task"]
        self.assertEqual(task["due_date"], "2026-01-05")
        self.assertLessEqual(task["start_date"], task["due_date"],
                             "開始日が期限より後になっている")
        # 逆転していると、その後の日程変更が弾かれてしまう
        status, data = self.admin.patch("/api/tasks/{}".format(task["id"]),
                                        {"due_date": "2026-01-07"})
        self.assertEqual(status, 200, data)

    def test_deleting_an_occurrence_does_not_bring_it_back(self):
        rule = self.make_rule(parent_id=self.parent["id"])
        task = self.admin.post("/api/recurrences/{}/run".format(rule["id"]), {})[1]["task"]
        gone = task["due_date"]
        self.admin.delete("/api/tasks/{}".format(task["id"]))
        self.assertEqual(
            [t for t in self.tasks() if t["parent_id"] == self.parent["id"]], [])
        notify.run_daily_digest(force=True)      # 定例の自動起票もここで走る
        children = [t for t in self.tasks() if t["parent_id"] == self.parent["id"]]
        # 次の回が作られるのは正しい。消した回そのものが戻らないことを見る
        self.assertNotIn(gone, [t["due_date"] for t in children],
                         "消した回が復活しないこと")

    def test_an_ad_hoc_meeting_can_be_added_by_hand(self):
        rule = self.make_rule(parent_id=self.parent["id"])
        self.admin.post("/api/recurrences/{}/run".format(rule["id"]), {})
        extra = self.make_task(self.project["id"], "臨時打ち合わせ",
                               parent_id=self.parent["id"], due_date="2026-09-10")
        parent = next(t for t in self.tasks() if t["id"] == self.parent["id"])
        self.assertEqual(parent["child_count"], 2)
        self.assertEqual(extra["parent_id"], self.parent["id"])

    # -- 次回を飛ばす ----------------------------------------------------
    def test_skip_moves_the_next_date_without_creating_a_task(self):
        rule = self.make_rule(parent_id=self.parent["id"])
        before = len(self.tasks())
        status, data = self.admin.post("/api/recurrences/{}/skip".format(rule["id"]), {})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["skipped"], "2026-09-07")
        self.assertEqual(data["next_on"], "2026-09-14")
        self.assertEqual(len(self.tasks()), before, "タスクは作られないこと")

    def test_skip_can_jump_several_times(self):
        rule = self.make_rule()
        data = self.admin.post("/api/recurrences/{}/skip".format(rule["id"]),
                               {"times": 3})[1]
        self.assertEqual(data["next_on"], "2026-09-28")

    def test_skip_needs_edit_rights(self):
        rule = self.make_rule()
        user, email = self.make_user("閲覧のみ")
        self.admin.put("/api/projects/{}/members".format(self.project["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"],
                         "role": "viewer"}]})
        client = self.client_for(email)
        self.assertEqual(
            client.post("/api/recurrences/{}/skip".format(rule["id"]), {})[0], 403)

    def test_a_parent_from_another_project_is_refused(self):
        other = self.make_project("よそのPJ")
        outsider = self.make_task(other["id"], "よそのタスク")
        status, data = self.admin.post(
            "/api/projects/{}/recurrences".format(self.project["id"]),
            {"title": "だめ", "freq": "weekly", "weekdays": "0", "next_on": "2026-09-07",
             "parent_id": outsider["id"]})
        self.assertEqual(status, 400, data)
        self.assertIn("プロジェクト", data["error"])

    def test_the_parent_can_be_changed_later(self):
        rule = self.make_rule()
        self.assertIsNone(rule["parent_id"])
        data = self.admin.patch("/api/recurrences/{}".format(rule["id"]),
                                {"parent_id": self.parent["id"]})[1]
        self.assertEqual(data["recurrence"]["parent_id"], self.parent["id"])
        data = self.admin.patch("/api/recurrences/{}".format(rule["id"]),
                                {"parent_id": None})[1]
        self.assertIsNone(data["recurrence"]["parent_id"])


class TestDuplicateTitles(ApiTestCase):
    """同じ名前のタスクがあっても、取り違えが起きないこと。"""

    def setUp(self):
        super().setUp()
        self.project = self.make_project("同名のあるPJ")
        self.first = self.make_task(self.project["id"], "週次定例")
        self.second = self.make_task(self.project["id"], "週次定例")

    def tasks(self):
        return {t["id"]: t for t in self.admin.get(
            "/api/projects/{}/tasks".format(self.project["id"]))[1]["tasks"]}

    def test_children_stay_with_the_right_parent(self):
        a = self.make_task(self.project["id"], "議事録", parent_id=self.first["id"])
        b = self.make_task(self.project["id"], "議事録", parent_id=self.second["id"])
        rows = self.tasks()
        self.assertEqual(rows[a["id"]]["parent_id"], self.first["id"])
        self.assertEqual(rows[b["id"]]["parent_id"], self.second["id"])

    def test_moving_a_child_between_same_named_parents(self):
        child = self.make_task(self.project["id"], "議事録", parent_id=self.first["id"])
        status, data = self.admin.patch("/api/tasks/{}".format(child["id"]),
                                        {"parent_id": self.second["id"]})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.tasks()[child["id"]]["parent_id"], self.second["id"])
        self.assertEqual(self.tasks()[self.first["id"]]["child_count"], 0)
        self.assertEqual(self.tasks()[self.second["id"]]["child_count"], 1)

    def test_the_history_records_which_one(self):
        """履歴が「週次定例 → 週次定例」では区別がつかないので、名前は残しつつ
        どちらへ動いたかは parent_id で追える状態であること。"""
        child = self.make_task(self.project["id"], "議事録", parent_id=self.first["id"])
        self.admin.patch("/api/tasks/{}".format(child["id"]),
                         {"parent_id": self.second["id"]})
        detail = self.admin.get("/api/tasks/{}".format(child["id"]))[1]
        self.assertEqual(detail["task"]["parent_id"], self.second["id"])
        self.assertEqual([p["id"] for p in detail["path"]], [self.second["id"]])

    def test_reordering_same_named_siblings(self):
        third = self.make_task(self.project["id"], "週次定例")
        self.admin.post("/api/tasks/reorder", {
            "project_id": self.project["id"],
            "items": [{"id": third["id"], "parent_id": None, "sort_order": 10},
                      {"id": self.first["id"], "parent_id": None, "sort_order": 20},
                      {"id": self.second["id"], "parent_id": None, "sort_order": 30}]})
        rows = self.tasks()
        self.assertEqual(rows[third["id"]]["sort_order"], 10)
        self.assertEqual(rows[self.first["id"]]["sort_order"], 20)
        self.assertEqual(rows[self.second["id"]]["sort_order"], 30)

    def test_import_warns_when_a_parent_name_is_ambiguous(self):
        status, data = self.admin.post(
            "/api/projects/{}/tasks/import".format(self.project["id"]),
            {"rows": [{"title": "打合せ"}, {"title": "打合せ"},
                      {"title": "議事録", "parent": "打合せ"}]})
        self.assertEqual(status, 201, data)
        self.assertTrue(any("複数ある" in p["message"] for p in data["problems"]),
                        "曖昧な親名を知らせること")

    def test_import_without_duplicates_is_quiet(self):
        data = self.admin.post(
            "/api/projects/{}/tasks/import".format(self.project["id"]),
            {"rows": [{"title": "打合せA"}, {"title": "議事録", "parent": "打合せA"}]})[1]
        self.assertEqual([p for p in data["problems"] if "複数ある" in p["message"]], [])


class TestGanttMarkers(ApiTestCase):
    """ガントで使う記号（◆ / ● / ★ など）。"""

    def test_marker_round_trip(self):
        project = self.make_project()
        status, data = self.admin.post("/api/tasks", {
            "project_id": project["id"], "title": "星にする", "marker": "star"})
        self.assertEqual(status, 201, data)
        self.assertEqual(data["task"]["marker"], "star")
        updated = self.admin.patch("/api/tasks/{}".format(data["task"]["id"]),
                                   {"marker": "circle"})[1]
        self.assertEqual(updated["task"]["marker"], "circle")

    def test_an_unknown_marker_falls_back_to_the_default(self):
        project = self.make_project()
        data = self.admin.post("/api/tasks", {
            "project_id": project["id"], "title": "でたらめ", "marker": "<script>"})[1]
        self.assertEqual(data["task"]["marker"], "")

    def test_the_marker_can_be_cleared(self):
        project = self.make_project()
        task = self.make_task(project["id"], "記号つき", marker="square")
        self.assertEqual(task["marker"], "square")
        data = self.admin.patch("/api/tasks/{}".format(task["id"]), {"marker": ""})[1]
        self.assertEqual(data["task"]["marker"], "")

    def test_markers_are_listed_in_meta(self):
        meta = self.admin.get("/api/meta")[1]
        values = [m["value"] for m in meta["markers"]]
        self.assertIn("", values)
        for expected in ("circle", "square", "triangle", "down", "star"):
            self.assertIn(expected, values)

    def test_default_is_empty(self):
        project = self.make_project()
        self.assertEqual(self.make_task(project["id"], "既定")["marker"], "")


class TestRecurrenceSingleDay(ApiTestCase):
    """定例タスクは会議が多いので、開始日と期限を同じ日にする。"""

    def make_rule(self, **kwargs):
        project = kwargs.pop("project", None) or self.make_project()
        payload = {"title": "定例", "freq": "weekly", "weekdays": "0",
                   "lead_days": 7, "next_on": "2026-12-07"}
        payload.update(kwargs)
        status, data = self.admin.post(
            "/api/projects/{}/recurrences".format(project["id"]), payload)
        self.assertEqual(status, 201, data)
        return project, data["recurrence"]

    def test_the_occurrence_starts_and_ends_on_the_same_day(self):
        _project, rule = self.make_rule()
        task = self.admin.post("/api/recurrences/{}/run".format(rule["id"]), {})[1]["task"]
        self.assertEqual(task["start_date"], "2026-12-07")
        self.assertEqual(task["due_date"], "2026-12-07")

    def test_occurrences_do_not_share_one_start_date(self):
        """まとめて作っても、回ごとに違う日付になること。"""
        project, rule = self.make_rule()
        made = [self.admin.post("/api/recurrences/{}/run".format(rule["id"]), {})[1]["task"]
                for _ in range(4)]
        starts = [t["start_date"] for t in made]
        self.assertEqual(len(set(starts)), 4, "開始日が全部同じになっている: {}".format(starts))
        for task in made:
            self.assertEqual(task["start_date"], task["due_date"])

    def test_a_past_occurrence_is_still_consistent(self):
        _project, rule = self.make_rule(next_on="2026-01-05")
        task = self.admin.post("/api/recurrences/{}/run".format(rule["id"]), {})[1]["task"]
        self.assertEqual(task["start_date"], "2026-01-05")
        self.assertEqual(task["due_date"], "2026-01-05")


class TestGanttOverview(ApiTestCase):
    """全プロジェクトを 1 枚のガントにまとめるためのデータ。"""

    def setUp(self):
        super().setUp()
        self.a = self.make_project("俯瞰A")
        self.b = self.make_project("俯瞰B")
        self.ta = self.make_task(self.a["id"], "A のタスク", due_date="2026-10-10")
        self.tb = self.make_task(self.b["id"], "B のタスク", due_date="2026-10-20")

    def test_tasks_from_every_project_are_returned(self):
        data = self.admin.get("/api/gantt")[1]
        titles = [t["title"] for t in data["tasks"]]
        self.assertIn("A のタスク", titles)
        self.assertIn("B のタスク", titles)
        names = [p["name"] for p in data["projects"]]
        self.assertIn("俯瞰A", names)
        self.assertIn("俯瞰B", names)

    def test_each_task_carries_its_project(self):
        data = self.admin.get("/api/gantt")[1]
        task = next(t for t in data["tasks"] if t["title"] == "A のタスク")
        self.assertEqual(task["project_name"], "俯瞰A")
        self.assertTrue(task["project_color"])

    def test_rollup_is_applied(self):
        child = self.make_task(self.a["id"], "子", parent_id=self.ta["id"],
                               due_date="2026-11-30", progress=100)
        data = self.admin.get("/api/gantt")[1]
        parent = next(t for t in data["tasks"] if t["id"] == self.ta["id"])
        self.assertEqual(parent["child_count"], 1)
        self.assertEqual(parent["rollup_due"], "2026-11-30")
        self.assertIn(child["id"], [t["id"] for t in data["tasks"]])

    def test_only_my_projects_are_included(self):
        user, email = self.make_user("片方だけの人")
        self.admin.put("/api/projects/{}/members".format(self.a["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"],
                         "role": "viewer"}]})
        data = self.client_for(email).get("/api/gantt")[1]
        self.assertEqual([p["name"] for p in data["projects"]], ["俯瞰A"])
        self.assertEqual([t["title"] for t in data["tasks"]], ["A のタスク"])

    def test_it_can_be_narrowed_to_some_projects(self):
        data = self.admin.get("/api/gantt?project_ids={}".format(self.b["id"]))[1]
        self.assertEqual([p["name"] for p in data["projects"]], ["俯瞰B"])

    def test_archived_projects_are_left_out(self):
        self.admin.patch("/api/projects/{}".format(self.b["id"]), {"archived": True})
        data = self.admin.get("/api/gantt")[1]
        self.assertNotIn("俯瞰B", [p["name"] for p in data["projects"]])

    def test_my_role_is_included_so_the_screen_knows_what_is_editable(self):
        data = self.admin.get("/api/gantt")[1]
        for project in data["projects"]:
            self.assertIn(project["my_role"], ("owner", "editor", "commenter", "viewer"))

    def test_someone_with_no_projects_gets_an_empty_chart(self):
        _user, email = self.make_user("どこにも属さない人")
        data = self.client_for(email).get("/api/gantt")[1]
        self.assertEqual(data["tasks"], [])
        self.assertEqual(data["projects"], [])

    def test_it_needs_a_login(self):
        self.assertEqual(Client(self.base).get("/api/gantt")[0], 401)
