"""管理者の代理表示（「この人として見る」・閲覧専用）。"""
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_api import ADMIN, ApiTestCase, Client  # noqa: E402

from app import db  # noqa: E402


class ActingCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.project = self.make_project("代理PJ")
        self.other = self.make_project("見えないPJ")
        self.guest_email = "guest-act-{}@test.local".format(self.id()[-8:].replace(".", ""))
        status, data = self.admin.post("/api/users", {
            "name": "社外 太郎", "email": self.guest_email, "role": "guest",
            "organization": "協力会社Z", "password": "userpassword"})
        self.assertEqual(status, 201, data)
        self.guest = data["user"]
        self.admin.put("/api/projects/{}/members".format(self.project["id"]), {"members": [
            {"principal_type": "user", "principal_id": self.guest["id"], "role": "commenter"}]})
        self.task = self.make_task(self.project["id"], "見えるタスク")
        # 代理表示は別のセッションで（ほかのテストの管理者セッションを汚さない）
        self.viewer = Client(self.base)
        self.viewer.login(*ADMIN)

    def tearDown(self):
        db.set_setting("mfa_required", "off")

    def act(self, user_id=None):
        return self.viewer.post("/api/admin/act", {"user_id": user_id or self.guest["id"]})

    def events(self, user_id):
        return [(r["event"], r["reason"], r["user_label"]) for r in db.query(
            "SELECT event, reason, user_label FROM login_events WHERE user_id=%s ORDER BY id",
            (user_id,))]


class TestActingView(ActingCase):
    def test_i_see_what_the_guest_sees(self):
        self.assertEqual(self.act()[0], 200)
        me = self.viewer.get("/api/auth/me")[1]
        self.assertEqual(me["user"]["id"], self.guest["id"])
        self.assertEqual(me["acting"]["by"], "管理者")
        names = [p["name"] for p in self.viewer.get("/api/projects")[1]["projects"]]
        self.assertEqual(names, ["代理PJ"])
        self.assertIn(self.viewer.get("/api/projects/{}".format(self.other["id"]))[0], (403, 404))
        # 社外ユーザーと同じく、ほかの人のメールアドレスは伏せられる
        users = self.viewer.get("/api/users")[1]["users"]
        self.assertTrue(all("email" not in u for u in users if u["id"] != self.guest["id"]))

    def test_nothing_can_be_changed(self):
        self.act()
        path = "/api/tasks/{}".format(self.task["id"])
        for status, _ in (self.viewer.patch(path, {"progress": 50}),
                          self.viewer.post(path + "/comments", {"body": "代理で書く"}),
                          self.viewer.post("/api/tickets", {"queue_id": 1, "title": "x"}),
                          self.viewer.post("/api/auth/password", {"current_password": "a",
                                                                  "new_password": "bbbbbbbb"})):
            self.assertEqual(status, 403)
        status, err = self.viewer.patch(path, {"progress": 50})
        self.assertTrue(err["detail"]["acting"])
        self.assertEqual(db.scalar("SELECT progress FROM tasks WHERE id=%s", (self.task["id"],)), 0)

    def test_personal_things_stay_private(self):
        guest = self.client_for(self.guest_email)
        self.assertEqual(guest.post("/api/todos", {"title": "ないしょのメモ"})[0], 201)
        guest.post("/api/todo-recurrences", {"title": "毎日のメモ", "freq": "daily"})
        before = db.scalar("SELECT COUNT(*) AS c FROM todos WHERE user_id=%s", (self.guest["id"],))
        self.act()
        for path in ("/api/todos", "/api/todo-recurrences", "/api/auth/logins", "/api/auth/mfa"):
            self.assertEqual(self.viewer.get(path)[0], 403, path)
        daily = self.viewer.get("/api/daily")[1]
        self.assertEqual((daily["todos"], daily["totals"]["todos"]), ([], 0))
        found = self.viewer.get("/api/search?q=ないしょ")[1]
        self.assertNotIn("ないしょのメモ", str(found))
        # 見ただけで、相手の定例 ToDo は増えない
        self.assertEqual(db.scalar("SELECT COUNT(*) AS c FROM todos WHERE user_id=%s",
                                   (self.guest["id"],)), before)

    def test_admin_screens_are_closed_while_acting(self):
        self.act()
        self.assertEqual(self.viewer.get("/api/admin/logins")[0], 403)
        self.assertEqual(self.viewer.post("/api/admin/act", {"user_id": self.guest["id"]})[0], 403)

    def test_stop_returns_to_me_and_both_ends_are_recorded(self):
        self.act()
        self.assertEqual(self.viewer.post("/api/auth/act/stop")[0], 200)
        me = self.viewer.get("/api/auth/me")[1]
        self.assertEqual((me["user"]["role"], me["acting"]), ("admin", None))
        self.assertEqual(self.viewer.get("/api/admin/logins")[0], 200)
        admin_id = db.scalar("SELECT id FROM users WHERE email=%s", (ADMIN[0],))
        events = self.events(admin_id)
        self.assertIn(("act_start", "", "管理者 → 社外 太郎 として表示"), events)
        self.assertIn(("act_end", "", "管理者 → 社外 太郎 として表示"), events)
        # 相手の人の履歴には何も残らない
        self.assertEqual([e for e in self.events(self.guest["id"]) if e[0].startswith("act_")], [])
        guest = self.client_for(self.guest_email)
        mine = [e["event"] for e in guest.get("/api/auth/logins")[1]["events"]]
        self.assertNotIn("act_start", mine)

    def test_it_runs_out_after_thirty_minutes(self):
        self.act()
        db.execute("UPDATE sessions SET acting_until=%s WHERE acting_as=%s",
                   (db.now() - timedelta(minutes=1), self.guest["id"]))
        self.assertEqual(self.viewer.get("/api/auth/me")[1]["user"]["role"], "admin")
        admin_id = db.scalar("SELECT id FROM users WHERE email=%s", (ADMIN[0],))
        self.assertIn(("act_end", "expired_act", "管理者 → 社外 太郎 として表示"),
                      self.events(admin_id))

    def test_logging_out_is_recorded_for_the_admin(self):
        self.act()
        admin_id = db.scalar("SELECT id FROM users WHERE email=%s", (ADMIN[0],))
        self.viewer.post("/api/auth/logout")
        last = db.query_one("SELECT user_id, event FROM login_events WHERE event='logout' "
                            "ORDER BY id DESC LIMIT 1")
        self.assertEqual(last["user_id"], admin_id)

    def test_mfa_requirement_does_not_block_the_view(self):
        self.act()
        db.set_setting("mfa_required", "all")
        # 相手が多要素認証を設定していなくても、代理表示は設定の画面にならない
        self.assertEqual(self.viewer.get("/api/projects")[0], 200)
        self.assertFalse(self.viewer.get("/api/auth/me")[1]["user"]["mfa_setup_required"])


class TestWhoCanAct(ActingCase):
    def test_not_admins_not_myself_not_stopped_people(self):
        admin_id = db.scalar("SELECT id FROM users WHERE email=%s", (ADMIN[0],))
        self.assertEqual(self.act(admin_id)[0], 400)
        other_admin, _ = self.make_user("もう一人の管理者", role="admin")
        self.assertEqual(self.act(other_admin["id"])[0], 400)
        stopped, _ = self.make_user("停止中の人")
        self.admin.patch("/api/users/{}".format(stopped["id"]), {"is_active": False})
        self.assertEqual(self.act(stopped["id"])[0], 400)

    def test_members_cannot_act(self):
        member, email = self.make_user("一般の人")
        client = self.client_for(email)
        self.assertEqual(client.post("/api/admin/act", {"user_id": self.guest["id"]})[0], 403)

    def test_acting_ends_if_the_person_is_stopped(self):
        self.act()
        self.admin.patch("/api/users/{}".format(self.guest["id"]), {"is_active": False})
        self.assertEqual(self.viewer.get("/api/auth/me")[1]["user"]["role"], "admin")


class TestOwnerPreview(ApiTestCase):
    """プロジェクト管理者の「社外ユーザー（〇〇社）として、このプロジェクトを見る」。"""

    def setUp(self):
        super().setUp()
        self.owner, email = self.make_user("PJ管理者")
        self.owner_client = self.client_for(email)
        self.pid = self.owner_client.post("/api/projects", {"name": "見せるPJ"})[1]["project"]["id"]
        self.mine_too = self.owner_client.post("/api/projects", {"name": "自分の別PJ"})[1]["project"]["id"]
        self.task = self.owner_client.post("/api/tasks", {"project_id": self.pid, "title": "見えるタスク"})[1]["task"]
        self.org_a = db.insert("INSERT INTO organizations(name, created_at) VALUES(%s,%s)",
                               ("A社{}".format(self.pid), db.now()))
        self.org_b = db.insert("INSERT INTO organizations(name, created_at) VALUES(%s,%s)",
                               ("B社{}".format(self.pid), db.now()))

    def preview(self, client=None, pid=None, org=None):
        return (client or self.owner_client).post("/api/projects/{}/preview".format(pid or self.pid),
                                                  {"organization_id": org or self.org_a})

    def test_i_see_the_project_as_a_guest_of_that_company(self):
        self.assertEqual(self.preview()[0], 200)
        me = self.owner_client.get("/api/auth/me")[1]
        self.assertEqual(me["user"]["role"], "guest")
        self.assertEqual(me["acting"]["preview"]["project"], "見せるPJ")
        names = [p["name"] for p in self.owner_client.get("/api/projects")[1]["projects"]]
        self.assertEqual(names, ["見せるPJ"])   # 自分の別のプロジェクトも見えない
        users = self.owner_client.get("/api/users")[1]["users"]
        self.assertTrue(users)
        self.assertTrue(all("email" not in u for u in users))

    def test_tabs_and_company_tickets_follow_the_guest_rules(self):
        self.owner_client.patch("/api/projects/{}".format(self.pid), {"guest_tabs": ["tasks", "tickets"]})
        queue = self.admin.post("/api/ticket-queues", {"name": "窓口{}".format(self.pid),
                                                       "project_id": self.pid})[1]["queue"]
        for org, title in ((self.org_a, "A社の問い合わせ"), (self.org_b, "B社の問い合わせ")):
            status, data = self.admin.post("/api/tickets", {"queue_id": queue["id"], "title": title,
                                                            "organization_id": org})
            self.assertEqual(status, 201, data)
        self.make_issue(self.pid, "社内の課題")
        self.preview()
        self.assertIn(self.owner_client.get("/api/projects/{}/issues".format(self.pid))[0], (403, 404))
        # 課題を見せていないので、件数も出さない（プロジェクト一覧・詳細とも）
        listed = self.owner_client.get("/api/projects")[1]["projects"][0]["stats"]
        detail = self.owner_client.get("/api/projects/{}".format(self.pid))[1]["project"]["stats"]
        self.assertEqual((listed["open_issues"], detail["open_issues"]), (0, 0))
        titles = [t["title"] for t in self.owner_client.get("/api/tickets")[1]["tickets"]]
        self.assertEqual(titles, ["A社の問い合わせ"])

    def test_it_is_read_only(self):
        self.preview()
        self.assertEqual(self.owner_client.post("/api/tasks/{}/comments".format(self.task["id"]),
                                                {"body": "x"})[0], 403)
        self.assertEqual(self.owner_client.patch("/api/projects/{}".format(self.pid),
                                                 {"name": "変える"})[0], 403)

    def test_only_the_project_owner_and_only_their_project(self):
        editor, editor_email = self.make_user("編集者")
        self.owner_client.put("/api/projects/{}/members".format(self.pid), {"members": [
            {"principal_type": "user", "principal_id": editor["id"], "role": "editor"}]})
        self.assertIn(self.preview(self.client_for(editor_email))[0], (403, 404))
        other = self.make_project("よそのPJ")
        self.assertIn(self.preview(pid=other["id"])[0], (403, 404))

    def test_stop_and_the_record_stays_with_the_owner(self):
        self.preview()
        self.assertEqual(self.owner_client.post("/api/auth/act/stop")[0], 200)
        me = self.owner_client.get("/api/auth/me")[1]
        self.assertEqual((me["user"]["id"], me["acting"]), (self.owner["id"], None))
        labels = [(r["event"], r["user_label"]) for r in db.query(
            "SELECT event, user_label FROM login_events WHERE user_id=%s AND event LIKE 'act%%' ORDER BY id",
            (self.owner["id"],))]
        self.assertEqual(labels[0][0], "act_start")
        self.assertIn("「見せるPJ」を社外ユーザー（A社", labels[0][1])
        self.assertEqual(labels[-1], ("act_end", "PJ管理者 → 「見せるPJ」を社外ユーザーとして表示"))

    def test_it_ends_when_the_owner_role_goes(self):
        self.preview()
        self.admin.patch("/api/projects/{}".format(self.pid), {"owner_id": db.scalar(
            "SELECT id FROM users WHERE email=%s", (ADMIN[0],))})
        self.admin.put("/api/projects/{}/members".format(self.pid), {"members": [
            {"principal_type": "user", "principal_id": self.owner["id"], "role": "editor"}]})
        self.assertEqual(self.owner_client.get("/api/auth/me")[1]["user"]["id"], self.owner["id"])

    def test_company_choices_put_this_projects_companies_first(self):
        status, data = self.admin.post("/api/users", {
            "name": "B社の人", "email": "b{}@test.local".format(self.pid), "role": "guest",
            "organization": db.scalar("SELECT name FROM organizations WHERE id=%s", (self.org_b,)),
            "password": "userpassword"})
        self.owner_client.put("/api/projects/{}/members".format(self.pid), {"members": [
            {"principal_type": "user", "principal_id": data["user"]["id"], "role": "commenter"}]})
        orgs = self.owner_client.get("/api/projects/{}/preview".format(self.pid))[1]["organizations"]
        self.assertEqual((orgs[0]["id"], orgs[0]["in_project"]), (self.org_b, True))
