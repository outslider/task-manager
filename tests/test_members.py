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
