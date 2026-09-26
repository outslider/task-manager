"""プロジェクト管理者（管理者アカウントでない人）が、自分のプロジェクトのメンバーを扱える。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_api import ApiTestCase  # noqa: E402


class TestProjectOwnerManagesMembers(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.owner, owner_email = self.make_user("PJ管理者")
        self.owner_client = self.client_for(owner_email)
        status, data = self.owner_client.post("/api/projects", {"name": "自分のプロジェクト"})
        self.assertEqual(status, 201, data)
        self.pid = data["project"]["id"]
        self.alice, self.alice_email = self.make_user("アリス")
        self.bob, _ = self.make_user("ボブ")

    def put_members(self, client, members):
        return client.put("/api/projects/{}/members".format(self.pid), {"members": [
            {"principal_type": "user", "principal_id": uid, "role": role} for uid, role in members]})

    def roles(self):
        rows = self.owner_client.get("/api/projects/{}".format(self.pid))[1]["project"]["members"]
        return {m["id"]: m["role"] for m in rows if m["principal_type"] == "user"}

    def test_add_change_and_remove(self):
        status, data = self.put_members(self.owner_client, [
            (self.owner["id"], "owner"), (self.alice["id"], "editor"), (self.bob["id"], "viewer")])
        self.assertEqual(status, 200, data)
        self.assertEqual(self.roles(), {self.owner["id"]: "owner", self.alice["id"]: "editor",
                                        self.bob["id"]: "viewer"})
        # アリスは入れる
        alice = self.client_for(self.alice_email)
        self.assertEqual(alice.get("/api/projects/{}".format(self.pid))[0], 200)
        # 権限を変えて、ボブを外す
        self.assertEqual(self.put_members(self.owner_client, [
            (self.owner["id"], "owner"), (self.alice["id"], "commenter")])[0], 200)
        self.assertEqual(self.roles(), {self.owner["id"]: "owner", self.alice["id"]: "commenter"})

    def test_the_owner_cannot_be_dropped(self):
        self.assertEqual(self.put_members(self.owner_client, [(self.alice["id"], "editor")])[0], 200)
        self.assertEqual(self.roles()[self.owner["id"]], "owner")

    def test_editors_cannot_change_members(self):
        self.put_members(self.owner_client, [(self.owner["id"], "owner"), (self.alice["id"], "editor")])
        alice = self.client_for(self.alice_email)
        status, _ = self.put_members(alice, [(self.alice["id"], "owner")])
        self.assertIn(status, (403, 404))
        self.assertEqual(self.roles()[self.alice["id"]], "editor")


class TestProjectOwnerCreatesAccounts(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.owner, owner_email = self.make_user("作る人")
        self.owner_client = self.client_for(owner_email)
        self.pid = self.owner_client.post("/api/projects", {"name": "作るPJ"})[1]["project"]["id"]
        self.path = "/api/projects/{}/accounts".format(self.pid)

    def tearDown(self):
        from app import db
        db.set_setting("owner_account_creation", "all")

    def email(self):
        import uuid
        return "new{}@test.local".format(uuid.uuid4().hex[:8])

    def test_a_new_account_joins_the_project(self):
        from app import db
        email = self.email()
        status, data = self.owner_client.post(self.path, {"name": "新人", "email": email})
        self.assertEqual(status, 201, data)
        self.assertEqual((data["user"]["role"], data["project_role"]), ("member", "editor"))
        self.assertGreaterEqual(len(data["initial_password"]), 8)
        # 本人はそのパスワードで入れて、プロジェクトが見える
        newbie = self.client_for(email, data["initial_password"])
        self.assertEqual(newbie.get("/api/projects/{}".format(self.pid))[0], 200)
        row = db.query_one("SELECT user_label FROM login_events WHERE event='created' "
                           "AND user_id=%s", (data["user"]["id"],))
        self.assertEqual(row["user_label"], "新人（作る人 が作成）")

    def test_guests_need_a_company_and_are_capped(self):
        email = self.email()
        self.assertEqual(self.owner_client.post(self.path, {
            "name": "社外", "email": email, "role": "guest"})[0], 400)
        status, data = self.owner_client.post(self.path, {
            "name": "社外", "email": email, "role": "guest", "organization": "協力会社X",
            "project_role": "editor", "expires_on": "2027-03-31"})
        self.assertEqual(status, 201, data)
        self.assertEqual(data["project_role"], "commenter")
        self.assertEqual(data["user"]["organization_name"], "協力会社X")
        self.assertEqual(data["user"]["expires_on"], "2027-03-31")

    def test_no_admins_and_no_owner_role(self):
        self.assertEqual(self.owner_client.post(self.path, {
            "name": "x", "email": self.email(), "role": "admin"})[0], 403)
        self.assertEqual(self.owner_client.post(self.path, {
            "name": "x", "email": self.email(), "project_role": "owner"})[0], 400)

    def test_duplicates_point_to_adding_the_existing_person(self):
        _, email = self.make_user("既にいる人")
        status, data = self.owner_client.post(self.path, {"name": "既にいる人", "email": email})
        self.assertEqual(status, 400)
        self.assertIn("メンバーを追加", data["error"])

    def test_only_the_project_owner(self):
        editor, editor_email = self.make_user("編集者")
        self.owner_client.put("/api/projects/{}/members".format(self.pid), {"members": [
            {"principal_type": "user", "principal_id": editor["id"], "role": "editor"}]})
        editor_client = self.client_for(editor_email)
        self.assertIn(editor_client.post(self.path, {"name": "x", "email": self.email()})[0], (403, 404))
        other = self.make_project("よそのPJ")
        self.assertIn(self.owner_client.post("/api/projects/{}/accounts".format(other["id"]), {
            "name": "x", "email": self.email()})[0], (403, 404))

    def test_the_admin_setting_limits_it(self):
        from app import db
        db.set_setting("owner_account_creation", "guest")
        self.assertEqual(self.owner_client.post(self.path, {"name": "x", "email": self.email()})[0], 403)
        self.assertEqual(self.owner_client.post(self.path, {
            "name": "x", "email": self.email(), "role": "guest", "organization": "Y社"})[0], 201)
        self.assertEqual(self.owner_client.get("/api/meta")[1]["creatable_account_roles"], ["guest"])
        db.set_setting("owner_account_creation", "off")
        self.assertEqual(self.owner_client.post(self.path, {
            "name": "x", "email": self.email(), "role": "guest", "organization": "Y社"})[0], 403)
        self.assertEqual(self.admin.put("/api/settings", {"settings": {"owner_account_creation": "x"}})[0], 400)
