"""運用管理者：管理者とプロジェクト管理者のあいだの権限。

グループ・ユーザー（管理者と運用管理者以外）・状態とカテゴリ・休業日・チケット窓口を設定できる。
システム設定・ログイン履歴・代理表示・パスワード再発行・停止と削除は管理者だけ。
プロジェクトは、ふつうの社内ユーザーと同じく参加しているものだけ。
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_api import ApiTestCase  # noqa: E402

from app import db  # noqa: E402


class ManagerCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.manager, email = self.make_user("運用 花子", role="manager")
        self.m = self.client_for(email)
        self.member, member_email = self.make_user("社内 太郎")
        self.member_client = self.client_for(member_email)

    def events(self, event, user_id=None):
        sql, params = "SELECT * FROM login_events WHERE event=%s", [event]
        if user_id:
            sql += " AND user_id=%s"
            params.append(user_id)
        return db.query(sql + " ORDER BY id", tuple(params))


class TestManagerCanRunOperations(ManagerCase):
    def test_groups_are_managed_and_recorded(self):
        status, data = self.m.post("/api/groups", {"name": "営業G" + uuid.uuid4().hex[:4],
                                                   "user_ids": [self.member["id"]]})
        self.assertEqual(status, 201, data)
        gid = data["group"]["id"]
        # 自分を入れることもできる（信頼できる人に付ける前提。記録には残る）
        status, _ = self.m.patch("/api/groups/{}".format(gid),
                                 {"user_ids": [self.member["id"], self.manager["id"]]})
        self.assertEqual(status, 200)
        status, _ = self.m.patch("/api/groups/{}".format(gid), {"user_ids": [self.manager["id"]]})
        self.assertEqual(status, 200)
        self.assertEqual(self.m.delete("/api/groups/{}".format(gid))[0], 200)
        labels = [e["user_label"] for e in self.events("group", self.manager["id"])]
        self.assertEqual(len(labels), 4, labels)
        self.assertIn("作成", labels[0])
        self.assertIn("社内 太郎 を追加", labels[0])
        self.assertIn("運用 花子 を追加", labels[1])
        self.assertIn("社内 太郎 を外した", labels[2])
        self.assertIn("削除", labels[3])
        self.assertTrue(all("運用 花子 が操作" in label for label in labels))

    def test_members_and_guests_can_be_created_and_edited(self):
        status, data = self.m.post("/api/users", {"name": "新人", "email": "new-{}@test.local".format(
            uuid.uuid4().hex[:6]), "role": "member", "password": "userpassword"})
        self.assertEqual(status, 201, data)
        status, data = self.m.post("/api/users", {"name": "協力会社の人", "email": "g-{}@test.local".format(
            uuid.uuid4().hex[:6]), "role": "guest", "organization": "協力会社M"})
        self.assertEqual(status, 201, data)
        status, data = self.m.patch("/api/users/{}".format(self.member["id"]), {
            "name": "社内 次郎", "role": "guest", "organization": "協力会社M", "expires_on": "2027-03-31"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["user"]["role"], "guest")
        # 相手の履歴に、だれが何を変えたかが残る
        label = self.events("account", self.member["id"])[-1]["user_label"]
        self.assertIn("社内ユーザー → 社外ユーザー", label)
        self.assertIn("有効期限 なし → 2027-03-31", label)
        self.assertIn("運用 花子 が変更", label)

    def test_settings_for_daily_operations(self):
        status, data = self.m.get("/api/admin/taxonomy")
        self.assertEqual(status, 200)
        self.assertEqual(self.m.put("/api/admin/taxonomy", {"categories": data["categories"]})[0], 200)
        self.assertEqual(self.m.post("/api/holidays", {"day": "2026-12-29", "name": "年末休業"})[0], 201)
        self.assertEqual(self.m.delete("/api/holidays/2026-12-29")[0], 200)
        status, data = self.m.post("/api/ticket-queues", {"name": "総務窓口" + uuid.uuid4().hex[:4]})
        self.assertEqual(status, 201, data)
        qid = data["queue"]["id"]
        self.assertEqual(self.m.patch("/api/ticket-queues/{}".format(qid), {"description": "備品"})[0], 200)
        self.assertEqual(self.m.delete("/api/ticket-queues/{}".format(qid))[0], 200)

    def test_user_list_has_organizations_but_not_login_details(self):
        data = self.m.get("/api/users?include_inactive=1")[1]
        self.assertIn("organizations", data)
        self.assertNotIn("last_login_at", data["users"][0])
        self.assertNotIn("mfa_enabled", data["users"][0])


class TestManagerLimits(ManagerCase):
    def test_cannot_create_or_promote_to_admin_or_manager(self):
        for role in ("admin", "manager"):
            status, _ = self.m.post("/api/users", {"name": "x", "email": "x-{}@test.local".format(
                uuid.uuid4().hex[:6]), "role": role})
            self.assertEqual(status, 403, role)
            status, _ = self.m.patch("/api/users/{}".format(self.member["id"]), {"role": role})
            self.assertEqual(status, 403, role)
        self.assertEqual(db.scalar("SELECT role AS r FROM users WHERE id=%s", (self.member["id"],)), "member")

    def test_cannot_touch_admin_or_manager_accounts(self):
        admin_id = db.scalar("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1")
        other, _ = self.make_user("別の運用管理者", role="manager")
        for target in (admin_id, other["id"]):
            self.assertEqual(self.m.patch("/api/users/{}".format(target), {"name": "書き換え"})[0], 403)
            self.assertEqual(self.m.patch("/api/users/{}".format(target), {"email": "evil@test.local"})[0], 403)

    def test_can_still_edit_own_profile_but_not_own_role(self):
        status, data = self.m.patch("/api/users/{}".format(self.manager["id"]), {"name": "運用 花子2"})
        self.assertEqual(status, 200, data)
        self.m.patch("/api/users/{}".format(self.manager["id"]), {"role": "admin"})
        self.assertEqual(db.scalar("SELECT role AS r FROM users WHERE id=%s", (self.manager["id"],)), "manager")

    def test_admin_only_actions_stay_closed(self):
        uid = self.member["id"]
        self.assertEqual(self.m.patch("/api/users/{}".format(uid), {"is_active": False})[0], 403)
        self.assertEqual(self.m.post("/api/users/{}/password".format(uid), {})[0], 403)
        self.assertEqual(self.m.delete("/api/users/{}/mfa".format(uid))[0], 403)
        self.assertEqual(self.m.delete("/api/users/{}".format(uid))[0], 403)
        self.assertEqual(self.m.get("/api/settings")[0], 403)
        self.assertEqual(self.m.put("/api/settings", {"llm_enabled": False})[0], 403)
        self.assertEqual(self.m.get("/api/admin/logins")[0], 403)
        self.assertEqual(self.m.post("/api/admin/act", {"user_id": uid})[0], 403)
        self.assertEqual(db.scalar("SELECT is_active AS a FROM users WHERE id=%s", (uid,)), 1)

    def test_projects_are_only_the_ones_joined(self):
        project = self.make_project("運用管理者は入っていないPJ")
        # 参加していない社内ユーザーと同じ扱い（管理者のように全プロジェクトには入れない）
        member_status = self.member_client.get("/api/projects/{}".format(project["id"]))[0]
        self.assertIn(member_status, (403, 404))
        self.assertEqual(self.m.get("/api/projects/{}".format(project["id"]))[0], member_status)
        self.assertEqual(self.m.get("/api/projects/{}/tasks".format(project["id"]))[0], member_status)
        self.assertNotIn(project["id"], [p["id"] for p in self.m.get("/api/projects")[1]["projects"]])

    def test_members_still_cannot_run_operations(self):
        c = self.member_client
        self.assertEqual(c.post("/api/groups", {"name": "勝手G"})[0], 403)
        self.assertEqual(c.get("/api/admin/taxonomy")[0], 403)
        self.assertEqual(c.post("/api/holidays", {"day": "2026-12-30"})[0], 403)
        self.assertEqual(c.post("/api/ticket-queues", {"name": "勝手窓口"})[0], 403)
        self.assertEqual(c.post("/api/users", {"name": "x", "email": "y@test.local"})[0], 403)


class TestAdminEditsAreRecordedToo(ManagerCase):
    def test_admin_changes_leave_a_record_but_self_edits_do_not(self):
        self.admin.patch("/api/users/{}".format(self.member["id"]), {"role": "manager"})
        label = self.events("account", self.member["id"])[-1]["user_label"]
        self.assertIn("社内ユーザー → 運用管理者", label)
        before = len(self.events("account"))
        self.m.patch("/api/users/{}".format(self.manager["id"]), {"name": "自分で変更"})
        self.assertEqual(len(self.events("account")), before)
