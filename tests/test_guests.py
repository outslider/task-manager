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
        for method, path in (("GET", "/api/tickets/stats"), ("POST", "/api/ticket-queues"),
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
        self.assertNotIn("社内の相談ZZQ", json.dumps(self.g.get("/api/search?q=ZZQ")[1]["groups"],
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


class TestGuestTickets(GuestTestCase):
    """第 2 段階：社外ユーザーは参加プロジェクトの窓口に起票でき、自分の会社のチケットだけ見える。"""

    def setUp(self):
        super().setUp()
        status, data = self.admin.post("/api/ticket-queues", {
            "name": "PJ窓口{}".format(uuid.uuid4().hex[:4]), "project_id": self.project["id"]})
        self.assertEqual(status, 201, data)
        self.queue = data["queue"]
        status, data = self.admin.post("/api/ticket-queues", {
            "name": "社内窓口{}".format(uuid.uuid4().hex[:4])})
        self.inside_queue = data["queue"]
        self.partner, partner_email = self.make_guest("別会社の人", org="B社")
        self.join(self.project, self.partner, "commenter")
        self.b = self.client_for(partner_email, "guestpassword")
        self.org_a = db.scalar("SELECT id FROM organizations WHERE name='A社'")

    def guest_ticket(self, client=None, title="画面が開きません", **extra):
        body = {"queue_id": self.queue["id"], "title": title, "body": "詳しくは…"}
        body.update(extra)
        status, data = (client or self.g).post("/api/tickets", body)
        self.assertEqual(status, 201, data)
        return data["ticket"]

    def test_queues_offered_to_a_guest(self):
        queues = self.g.get("/api/ticket-queues")[1]["queues"]
        self.assertEqual([q["id"] for q in queues], [self.queue["id"]])
        # 社内だけの窓口には起票できない
        status, _ = self.g.post("/api/tickets", {"queue_id": self.inside_queue["id"], "title": "x"})
        self.assertEqual(status, 404)

    def test_a_guest_ticket_belongs_to_the_company(self):
        me_id = self.guest["id"]
        ticket = self.guest_ticket(assignee_id=me_id, status="done", priority=3,
                                   due_date="2026-12-01", spent_hours=5)
        self.assertEqual(ticket["organization_id"], self.org_a)
        self.assertEqual(ticket["organization_name"], "A社")
        # 担当・状態・優先度・期限・工数は社内が決める
        self.assertEqual((ticket["assignee_id"], ticket["status"], ticket["priority"],
                          ticket["due_date"]), (None, "new", 1, None))

    def test_other_companies_cannot_see_it(self):
        ticket = self.guest_ticket(title="A社の問い合わせQQX")
        url = "/api/tickets/{}".format(ticket["id"])
        self.assertEqual(self.b.get(url)[0], 404)
        self.assertNotIn(ticket["id"], [t["id"] for t in self.b.get("/api/tickets")[1]["tickets"]])
        self.assertNotIn("QQX", json.dumps(self.b.get("/api/search?q=QQX")[1]["groups"], ensure_ascii=False))
        self.assertIn("QQX", json.dumps(self.g.get("/api/search?q=QQX")[1]["groups"], ensure_ascii=False))
        # 件数にも、ほかの会社の分を混ぜない
        queue = next(q for q in self.b.get("/api/ticket-queues")[1]["queues"])
        self.assertEqual(queue["open_count"], 0)
        project = next(p for p in self.b.get("/api/projects")[1]["projects"])
        self.assertEqual(project["stats"]["open_tickets"], 0)
        project = next(p for p in self.g.get("/api/projects")[1]["projects"])
        self.assertEqual(project["stats"]["open_tickets"], 1)
        # 社内の人には見える
        self.assertEqual(self.admin.get(url)[0], 200)

    def test_internal_memo_stays_inside(self):
        ticket = self.guest_ticket()
        url = "/api/tickets/{}".format(ticket["id"])
        before = db.scalar("SELECT COUNT(*) AS c FROM notifications WHERE user_id=%s",
                           (self.guest["id"],))
        status, data = self.admin.post(url + "/comments", {"body": "社内だけの相談MEMOX",
                                                           "internal": True})
        self.assertEqual(status, 201, data)
        self.assertEqual(data["comment"]["is_internal"], 1)
        self.assertEqual(db.scalar("SELECT COUNT(*) AS c FROM notifications WHERE user_id=%s",
                                   (self.guest["id"],)), before)
        texts = [c["body"] for c in self.g.get(url)[1]["comments"]]
        self.assertNotIn("社内だけの相談MEMOX", texts)
        # 件数にも数えない（あることも伝えない）
        self.assertEqual(self.g.get(url)[1]["ticket"]["comment_count"], 0)
        listed = next(t for t in self.g.get("/api/tickets")[1]["tickets"] if t["id"] == ticket["id"])
        self.assertEqual(listed["comment_count"], 0)
        self.assertEqual(self.admin.get(url)[1]["ticket"]["comment_count"], 1)
        self.assertNotIn("MEMOX", json.dumps(self.g.get("/api/search?q=MEMOX")[1]["groups"], ensure_ascii=False))
        self.assertIn("社内だけの相談MEMOX", [c["body"] for c in self.admin.get(url)[1]["comments"]])
        # ふつうのコメントは届き、見える
        self.admin.post(url + "/comments", {"body": "お問い合わせありがとうございます"})
        self.assertGreater(db.scalar("SELECT COUNT(*) AS c FROM notifications WHERE user_id=%s",
                                     (self.guest["id"],)), before)
        self.assertIn("お問い合わせありがとうございます",
                      [c["body"] for c in self.g.get(url)[1]["comments"]])
        # 社外ユーザーは社内メモを書けない（印を付けても普通のコメントになる）
        status, data = self.g.post(url + "/comments", {"body": "追記です", "internal": True})
        self.assertEqual(data["comment"]["is_internal"], 0)

    def test_what_a_guest_cannot_do_with_tickets(self):
        ticket = self.guest_ticket()
        url = "/api/tickets/{}".format(ticket["id"])
        for method, path in (("PATCH", url), ("DELETE", url), ("POST", url + "/task"),
                             ("POST", url + "/issue"), ("PUT", url + "/tasks"),
                             ("GET", "/api/tickets/stats"), ("POST", "/api/tickets/import")):
            status, data = self.g.request(method, path, {} if method != "GET" else None)
            self.assertEqual(status, 403, (method, path, data))
        self.assertFalse(self.g.get(url)[1]["can_delete"])

    def test_internal_staff_can_share_a_ticket_with_a_company(self):
        status, data = self.admin.post("/api/tickets", {
            "queue_id": self.queue["id"], "title": "電話で受けた件", "organization_id": self.org_a,
            "spent_hours": 2})
        self.assertEqual(status, 201, data)
        url = "/api/tickets/{}".format(data["ticket"]["id"])
        seen = self.g.get(url)[1]
        self.assertEqual(seen["ticket"]["title"], "電話で受けた件")
        self.assertIsNone(seen["ticket"]["spent_hours"])      # 社内の工数は見せない
        self.assertEqual(self.b.get(url)[0], 404)
        # 会社を付け替えると、見える相手も変わる
        org_b = db.scalar("SELECT id FROM organizations WHERE name='B社'")
        self.admin.patch(url, {"organization_id": org_b})
        self.assertEqual(self.g.get(url)[0], 404)
        self.assertEqual(self.b.get(url)[0], 200)
        orgs = [o["name"] for o in self.admin.get("/api/organizations")[1]["organizations"]]
        self.assertIn("A社", orgs)
        self.assertEqual(self.g.get("/api/organizations")[0], 403)

    def test_guest_cannot_be_assigned_and_untied_queues_stay_closed(self):
        ticket = self.guest_ticket()
        status, _ = self.admin.patch("/api/tickets/{}".format(ticket["id"]),
                                     {"assignee_id": self.guest["id"]})
        self.assertEqual(status, 400)
        # プロジェクトにひもづかない窓口のチケットは、会社が同じでも見えない
        status, data = self.admin.post("/api/tickets", {
            "queue_id": self.inside_queue["id"], "title": "社内窓口の件", "organization_id": self.org_a})
        self.assertEqual(self.g.get("/api/tickets/{}".format(data["ticket"]["id"]))[0], 404)

    def test_guest_can_comment_on_own_company_ticket(self):
        colleague, email = self.make_guest("同じ会社の人", org="A社")
        self.join(self.project, colleague, "viewer")
        ticket = self.guest_ticket()
        other = self.client_for(email, "guestpassword")
        status, data = other.post("/api/tickets/{}/comments".format(ticket["id"]), {"body": "私も困っています"})
        self.assertEqual(status, 201, data)

    def test_guest_can_only_remove_own_attachments(self):
        ticket = self.guest_ticket()
        url = "/api/tickets/{}/attachments".format(ticket["id"])
        status, data = self.admin.post(url, {"kind": "link", "url": "https://intra.example/x",
                                             "name": "社内の資料"})
        self.assertEqual(status, 201, data)
        staff_att = (data.get("attachments") or [data.get("attachment")])[0]["id"]
        status, data = self.g.post(url, {"kind": "link", "url": "https://partner.example/y",
                                         "name": "先方の資料"})
        self.assertEqual(status, 201, data)
        own_att = (data.get("attachments") or [data.get("attachment")])[0]["id"]
        self.assertEqual(self.g.delete("/api/attachments/{}".format(staff_att))[0], 403)
        self.assertEqual(self.g.delete("/api/attachments/{}".format(own_att))[0], 200)


class TestProjectTabs(GuestTestCase):
    """第 3 段階：プロジェクトごとのタブ。社外ユーザーに見せないタブは、サーバーでも閉じる。"""

    def settle(self, **settings):
        status, data = self.admin.patch("/api/projects/{}".format(self.project["id"]), settings)
        self.assertEqual(status, 200, data)

    def my_tabs(self, client):
        return client.get("/api/projects/{}".format(self.project["id"]))[1]["project"]["tabs"]

    def test_defaults(self):
        self.assertEqual(self.my_tabs(self.g), ["tasks", "gantt", "issues", "tickets"])
        # 「決定」は社内には出るが、社外ユーザーには見せると決めるまで出ない
        self.assertEqual(self.my_tabs(self.admin),
                         ["tasks", "gantt", "workload", "bottlenecks", "issues", "decisions", "tickets"])
        project = self.admin.get("/api/projects/{}".format(self.project["id"]))[1]["project"]
        self.assertEqual(project["guest_tabs"], ["tasks", "gantt", "issues", "tickets"])
        # 設定そのものはプロジェクト管理者にだけ
        self.assertNotIn("guest_tabs", self.g.get("/api/projects/{}".format(self.project["id"]))[1]["project"])

    def test_tasks_cannot_be_hidden_and_workload_never_goes_to_guests(self):
        self.settle(tabs_hidden=["tasks", "workload"], guest_tabs=["workload", "gantt"])
        self.assertIn("tasks", self.my_tabs(self.admin))
        self.assertNotIn("workload", self.my_tabs(self.admin))
        self.assertEqual(self.my_tabs(self.g), ["tasks", "gantt"])
        self.assertEqual(self.admin.patch("/api/projects/{}".format(self.project["id"]),
                                          {"guest_tabs": ["bogus"]})[0], 400)

    def test_hidden_from_everyone_hides_from_guests_too(self):
        self.settle(tabs_hidden=["issues"])
        self.assertNotIn("issues", self.my_tabs(self.admin))
        self.assertNotIn("issues", self.my_tabs(self.g))
        # 社内の人に対しては画面から隠すだけ（データは閉じない）
        self.assertEqual(self.admin.get("/api/projects/{}/issues".format(self.project["id"]))[0], 200)

    def test_issues_closed_for_guests(self):
        issue = self.make_issue(self.project["id"], title="社外に見せない課題KKX")
        self.settle(guest_tabs=["tasks", "gantt", "tickets"])
        self.assertEqual(self.g.get("/api/projects/{}/issues".format(self.project["id"]))[0], 403)
        self.assertEqual(self.g.get("/api/issues/{}".format(issue["id"]))[0], 404)
        self.assertEqual(self.g.get("/api/issues")[1]["issues"], [])
        self.assertNotIn("KKX", json.dumps(self.g.get("/api/search?q=KKX")[1]["groups"],
                                           ensure_ascii=False))
        # 通知の関所でも止まる
        self.admin.patch("/api/issues/{}".format(issue["id"]), {"owner_id": self.guest["id"]})
        before = db.scalar("SELECT COUNT(*) AS c FROM notifications WHERE user_id=%s", (self.guest["id"],))
        self.admin.post("/api/issues/{}/comments".format(issue["id"]), {"body": "進めます"})
        self.assertEqual(db.scalar("SELECT COUNT(*) AS c FROM notifications WHERE user_id=%s",
                                   (self.guest["id"],)), before)
        # 戻せば見える
        self.settle(guest_tabs=["tasks", "issues"])
        self.assertEqual(self.g.get("/api/issues/{}".format(issue["id"]))[0], 200)

    def test_bottlenecks_are_off_for_guests_by_default(self):
        url = "/api/projects/{}/bottlenecks".format(self.project["id"])
        self.assertEqual(self.g.get(url)[0], 403)
        self.settle(guest_tabs=["tasks", "bottlenecks"])
        self.assertEqual(self.g.get(url)[0], 200)

    def test_tickets_closed_for_guests(self):
        status, data = self.admin.post("/api/ticket-queues", {
            "name": "PJ窓口{}".format(uuid.uuid4().hex[:4]), "project_id": self.project["id"]})
        queue = data["queue"]
        status, data = self.g.post("/api/tickets", {"queue_id": queue["id"], "title": "質問"})
        self.assertEqual(status, 201, data)
        self.settle(guest_tabs=["tasks", "gantt"])
        self.assertEqual(self.g.get("/api/ticket-queues")[1]["queues"], [])
        self.assertEqual(self.g.get("/api/tickets/{}".format(data["ticket"]["id"]))[0], 404)
        self.assertEqual(self.g.get("/api/tickets")[1]["tickets"], [])

    def test_gantt_closed_for_guests(self):
        self.admin.post("/api/projects/{}/meetings".format(self.project["id"]),
                        {"title": "定例", "freq": "weekly", "weekdays": [1]})
        self.settle(guest_tabs=["tasks", "issues"])
        self.assertEqual(self.g.get("/api/gantt")[1]["tasks"], [])
        self.assertEqual(self.g.get("/api/meetings")[1]["meetings"], [])
        self.assertEqual(len(self.admin.get("/api/meetings?project_ids={}".format(
            self.project["id"]))[1]["meetings"]), 1)


class TestInternalAttachments(GuestTestCase):
    def test_internal_attachment_stays_inside(self):
        status, data = self.admin.post("/api/ticket-queues", {
            "name": "PJ窓口{}".format(uuid.uuid4().hex[:4]), "project_id": self.project["id"]})
        status, data = self.g.post("/api/tickets", {"queue_id": data["queue"]["id"], "title": "見積の件"})
        ticket_id = data["ticket"]["id"]
        url = "/api/tickets/{}/attachments".format(ticket_id)
        status, data = self.admin.post(url, {"url": "https://intra.example/cost", "name": "原価表",
                                             "internal": True})
        self.assertEqual(status, 201, data)
        secret = data["attachments"][0]
        self.assertEqual(secret["is_internal"], 1)
        self.admin.post(url, {"url": "https://example.com/quote", "name": "見積書"})
        seen = self.g.get("/api/tickets/{}".format(ticket_id))[1]
        self.assertEqual([a["name"] for a in seen["attachments"]], ["見積書"])
        self.assertEqual(seen["ticket"]["attachment_count"], 1)
        self.assertEqual(self.g.delete("/api/attachments/{}".format(secret["id"]))[0], 404)
        self.assertEqual(self.g.get("/api/attachments/{}/download".format(secret["id"]))[0], 404)
        staff = self.admin.get("/api/tickets/{}".format(ticket_id))[1]
        self.assertEqual(len(staff["attachments"]), 2)
        # 社外ユーザーは「社内のみ」にできない
        status, data = self.g.post(url, {"url": "https://partner.example/a", "name": "先方", "internal": True})
        self.assertEqual(data["attachments"][0]["is_internal"], 0)


class TestGuestNavigation(ApiTestCase):
    """参加しているどのプロジェクトでも見せていない画面は、社外ユーザーの左メニューに出さない。"""

    def setUp(self):
        super().setUp()
        self.project = self.make_project("社外に一部だけ見せるPJ")
        status, data = self.admin.post("/api/users", {
            "name": "社外の人", "email": "nav-{}@test.local".format(self.project["id"]), "role": "guest",
            "organization": "協力会社N", "password": "userpassword"})
        self.guest = data["user"]
        self.admin.put("/api/projects/{}/members".format(self.project["id"]), {"members": [
            {"principal_type": "user", "principal_id": self.guest["id"], "role": "commenter"}]})
        self.client = self.client_for(self.guest["email"])

    def off(self):
        return sorted(self.client.get("/api/auth/me")[1]["nav_off"])

    def tabs(self, *keys):
        self.admin.patch("/api/projects/{}".format(self.project["id"]), {"guest_tabs": list(keys)})

    def test_only_tasks_shown_hides_the_rest(self):
        self.tabs("tasks")
        self.assertEqual(self.off(), ["gantt", "issues", "tickets"])

    def test_opening_tabs_brings_the_menu_back(self):
        self.tabs("tasks", "issues", "gantt")
        self.assertEqual(self.off(), ["tickets"])   # 窓口がまだ無い
        self.admin.post("/api/ticket-queues", {"name": "窓口N{}".format(self.project["id"]),
                                               "project_id": self.project["id"]})
        self.assertEqual(self.off(), ["tickets"])   # チケットのタブを見せていない
        self.tabs("tasks", "issues", "gantt", "tickets")
        self.assertEqual(self.off(), [])

    def test_insiders_keep_every_menu(self):
        self.tabs("tasks")
        self.assertEqual(self.admin.get("/api/auth/me")[1]["nav_off"], [])
