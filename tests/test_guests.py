"""社外ユーザー（第 1 段階）。

社外ユーザーは、参加しているプロジェクトの中だけを見られ、コメントでき、
自分が担当のタスクの進捗・状態・実績を更新できる。それ以外は閉じる。
「閉じる」は許可リスト方式なので、ここでは代表的な機能が閉じていることと、
中身（メールアドレス・全体のリンク・チケット）が脇道から漏れないことを確かめる。
"""
import json
import os
import sys
import uuid
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_api import ADMIN, ApiTestCase, Client  # noqa: E402

from app import db  # noqa: E402


class GuestTestCase(ApiTestCase):
    def make_guest(self, name="協力会社の人", org="A社", **extra):
        email = "g{}@partner.example".format(uuid.uuid4().hex[:8])
        body = {"name": name, "email": email, "role": "guest", "organization": org,
                "password": "guestpassword"}
        body.update(extra)
        status, data = self.admin.post("/api/users", body)
        self.assertEqual(status, 201, data)
        return data["user"], email

    def join(self, project, user, role):
        current = self.admin.get("/api/projects/{}".format(project["id"]))[1]["project"]["members"]
        members = [{"principal_type": m["principal_type"], "principal_id": m["id"],
                    "role": m["role"]} for m in current]
        members.append({"principal_type": "user", "principal_id": user["id"], "role": role})
        status, data = self.admin.put("/api/projects/{}/members".format(project["id"]),
                                      {"members": members})
        self.assertEqual(status, 200, data)

    def setUp(self):
        super().setUp()
        self.project = self.make_project("社外と組むPJ")
        self.secret = self.make_project("社内だけのPJ")
        self.guest, self.guest_email = self.make_guest()
        self.join(self.project, self.guest, "editor")   # 編集者で入れても、コメント可に抑える
        self.g = self.client_for(self.guest_email, "guestpassword")


class TestGuestAccount(GuestTestCase):
    def test_a_guest_needs_a_company(self):
        status, data = self.admin.post("/api/users", {
            "name": "会社なし", "email": "nocompany@partner.example", "role": "guest"})
        self.assertEqual(status, 400, data)
        users = {u["id"]: u for u in self.admin.get("/api/users?include_inactive=1")[1]["users"]}
        self.assertEqual(users[self.guest["id"]]["organization_name"], "A社")
        self.assertIn("A社", self.admin.get("/api/users")[1]["organizations"])

    def test_role_is_capped_at_commenter(self):
        role = db.scalar("SELECT role AS r FROM project_members WHERE project_id=%s "
                         "AND principal_type='user' AND principal_id=%s",
                         (self.project["id"], self.guest["id"]))
        self.assertEqual(role, "commenter")
        mine = next(p for p in self.g.get("/api/projects")[1]["projects"])
        self.assertEqual(mine["my_role"], "commenter")

    def test_group_membership_is_capped_too(self):
        status, data = self.admin.post("/api/groups", {"name": "混成チーム{}".format(uuid.uuid4().hex[:4]),
                                                       "user_ids": [self.guest["id"]]})
        self.assertEqual(status, 201, data)
        self.admin.put("/api/projects/{}/members".format(self.secret["id"]), {"members": [
            {"principal_type": "group", "principal_id": data["group"]["id"], "role": "owner"}]})
        mine = {p["id"]: p for p in self.g.get("/api/projects")[1]["projects"]}
        self.assertEqual(mine[self.secret["id"]]["my_role"], "commenter")

    def test_expired_account_cannot_get_in(self):
        yesterday = (db.today() - timedelta(days=1)).isoformat()
        self.admin.patch("/api/users/{}".format(self.guest["id"]), {"expires_on": yesterday})
        # 入っていた人も、次の要求から締め出される
        self.assertEqual(self.g.get("/api/projects")[0], 401)
        status, _ = Client(self.base).post("/api/auth/login",
                                           {"email": self.guest_email, "password": "guestpassword"})
        self.assertEqual(status, 401)
        reason = db.scalar("SELECT reason AS r FROM login_events WHERE user_id=%s "
                           "ORDER BY id DESC LIMIT 1", (self.guest["id"],))
        self.assertEqual(reason, "expired")

    def test_a_guest_cannot_be_the_project_admin(self):
        status, data = self.admin.patch("/api/projects/{}".format(self.project["id"]),
                                        {"owner_id": self.guest["id"]})
        self.assertEqual(status, 400, data)

    def test_turning_a_member_into_a_guest_lowers_roles(self):
        user, email = self.make_user("社外になる人")
        self.join(self.secret, user, "owner")
        self.admin.patch("/api/users/{}".format(user["id"]),
                         {"role": "guest", "organization": "B社"})
        role = db.scalar("SELECT role AS r FROM project_members WHERE project_id=%s "
                         "AND principal_type='user' AND principal_id=%s", (self.secret["id"], user["id"]))
        self.assertEqual(role, "commenter")


class TestGuestSees(GuestTestCase):
    def test_only_joined_projects(self):
        ids = [p["id"] for p in self.g.get("/api/projects")[1]["projects"]]
        self.assertEqual(ids, [self.project["id"]])
        self.assertEqual(self.g.get("/api/projects/{}/tasks".format(self.secret["id"]))[0], 403)

    def test_closed_features(self):
        for method, path in (("GET", "/api/tickets"), ("GET", "/api/ticket-queues"),
                             ("GET", "/api/templates"), ("GET", "/api/trash"),
                             ("GET", "/api/workload"), ("GET", "/api/groups"),
                             ("POST", "/api/nl/parse"), ("POST", "/api/projects"),
                             ("POST", "/api/tasks"), ("POST", "/api/tasks/bulk"),
                             ("GET", "/api/settings"), ("POST", "/api/links")):
            status, data = self.g.request(method, path, {} if method != "GET" else None)
            self.assertEqual(status, 403, (method, path, data))

    def test_ai_is_off_for_guests(self):
        self.assertFalse(self.g.get("/api/meta")[1]["llm_available"])

    def test_people_are_limited_to_the_same_projects_and_emails_are_hidden(self):
        partner, _ = self.make_guest("同じPJの別会社", org="B社")
        self.join(self.project, partner, "viewer")
        stranger, _ = self.make_user("関係ない社内の人")
        users = {u["id"]: u for u in self.g.get("/api/users")[1]["users"]}
        self.assertIn(partner["id"], users)        # 同じプロジェクトなら別会社でも名前は見える
        self.assertNotIn(stranger["id"], users)
        self.assertEqual(users[partner["id"]]["organization_name"], "B社")
        for uid, user in users.items():
            if uid != self.guest["id"]:          # 自分のアドレスは見えてよい
                self.assertNotIn("email", user)
        me = self.g.get("/api/auth/me")[1]["user"]
        self.assertEqual(me["email"], self.guest_email)   # 自分のものは見える
        text = json.dumps(self.g.get("/api/projects/{}".format(self.project["id"]))[1],
                          ensure_ascii=False)
        self.assertNotIn("@test.local", text)
        self.assertNotIn(ADMIN[0], text)

    def test_shared_links_are_hidden(self):
        self.admin.post("/api/links", {"title": "全社の共有フォルダ", "url": "https://intra.example/share"})
        self.admin.post("/api/links", {"title": "PJの資料", "url": "https://example.com/pj",
                                       "project_id": self.project["id"]})
        data = self.g.get("/api/links")[1]
        titles = [l["title"] for l in data["links"]]
        self.assertEqual(titles, ["PJの資料"])
        self.assertFalse(data["can_add_shared"])
        self.assertEqual(data["projects"], [])

    def test_tickets_do_not_leak_through_search_or_daily(self):
        status, queue = self.admin.post("/api/ticket-queues", {"name": "全員の窓口{}".format(
            uuid.uuid4().hex[:4])})
        self.admin.post("/api/tickets", {"queue_id": queue["queue"]["id"], "title": "社内の相談ZZQ"})
        self.assertNotIn("社内の相談ZZQ", json.dumps(self.g.get("/api/search?q=ZZQ")[1],
                                                ensure_ascii=False))
        daily = self.g.get("/api/daily")[1]
        self.assertEqual(daily.get("my_tickets", []), [])
        self.assertIn("社内の相談ZZQ", json.dumps(self.admin.get("/api/search?q=ZZQ")[1],
                                             ensure_ascii=False))


class TestGuestDoes(GuestTestCase):
    def setUp(self):
        super().setUp()
        self.mine = self.make_task(self.project["id"], title="協力会社の作業",
                                   assignee_id=self.guest["id"], due_date="2026-12-01")
        self.other = self.make_task(self.project["id"], title="社内の作業")

    def test_can_comment(self):
        status, data = self.g.post("/api/tasks/{}/comments".format(self.other["id"]),
                                   {"body": "確認しました"})
        self.assertEqual(status, 201, data)

    def test_cannot_edit_tasks(self):
        self.assertEqual(self.g.patch("/api/tasks/{}".format(self.other["id"]),
                                      {"title": "書き換え"})[0], 403)
        self.assertEqual(self.g.patch("/api/tasks/{}".format(self.other["id"]),
                                      {"progress": 50})[0], 403)

    def test_can_update_progress_of_own_task(self):
        url = "/api/tasks/{}".format(self.mine["id"])
        status, data = self.g.patch(url, {"progress": 60, "status": "doing"})
        self.assertEqual(status, 200, data)
        self.assertEqual((data["task"]["progress"], data["task"]["status"]), (60, "doing"))
        self.assertEqual(self.g.patch(url, {"actual_hours": 3})[0], 200)
        # 期限や名前は変えられない
        self.assertEqual(self.g.patch(url, {"due_date": "2027-01-01"})[0], 403)
        self.assertEqual(self.g.patch(url, {"title": "改名"})[0], 403)

    def test_daily_update_for_own_task(self):
        status, data = self.g.post("/api/daily/update", {"updates": [
            {"task_id": self.mine["id"], "progress": 80, "note": "順調です"}]})
        self.assertEqual(status, 200, data)
        self.assertEqual(db.scalar("SELECT progress AS p FROM tasks WHERE id=%s",
                                   (self.mine["id"],)), 80)
        status, _ = self.g.post("/api/daily/update", {"updates": [
            {"task_id": self.mine["id"], "due_date": "2027-01-01"}]})
        self.assertEqual(status, 403)

    def test_internal_commenter_can_update_own_task_too(self):
        user, email = self.make_user("コメント可の社内の人")
        self.join(self.project, user, "commenter")
        task = self.make_task(self.project["id"], title="社内の担当作業", assignee_id=user["id"])
        client = self.client_for(email)
        self.assertEqual(client.patch("/api/tasks/{}".format(task["id"]), {"progress": 40})[0], 200)


class TestProjectSecrets(GuestTestCase):
    def test_slack_webhook_is_only_for_project_admins(self):
        hook = "https://hooks.slack.com/services/T000/B000/SECRET"
        self.admin.patch("/api/projects/{}".format(self.project["id"]), {"slack_webhook_url": hook})
        viewer, email = self.make_user("閲覧の社内の人")
        self.join(self.project, viewer, "viewer")
        for client in (self.g, self.client_for(email)):
            for path in ("/api/projects", "/api/projects/{}".format(self.project["id"]),
                         "/api/projects/{}/tasks".format(self.project["id"])):
                text = json.dumps(client.get(path)[1], ensure_ascii=False)
                self.assertNotIn("SECRET", text, path)
        project = self.admin.get("/api/projects/{}".format(self.project["id"]))[1]["project"]
        self.assertEqual(project["slack_webhook_url"], hook)
        mine = self.g.get("/api/projects/{}".format(self.project["id"]))[1]["project"]
        self.assertTrue(mine["has_slack_webhook"])
