"""意思決定ログ。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_api import ApiTestCase  # noqa: E402

from app import db  # noqa: E402


class DecisionCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.project = self.make_project("決めるPJ")
        self.pid = self.project["id"]
        self.admin_id = db.scalar("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1")
        self.task = self.make_task(self.pid, "認証の実装")
        self.issue = self.make_issue(self.pid, "認証方式が未定")

    def create(self, client=None, **body):
        payload = {
            "title": "認証は社内の SSO に寄せる", "status": "decided",
            "what": "ログインは社内 SSO（SAML）を使う", "why": "アカウント管理を一本化するため",
            "people": [self.admin_id],
            "options": [{"title": "社内 SSO", "adopted": True, "reason": "既存の仕組みを使える"},
                        {"title": "独自のパスワード", "adopted": False, "reason": "管理が二重になる"}],
            "premises": [{"text": "全社員が SSO のアカウントを持っている", "review_on": "2027-03-31"}],
            "links": {"tasks": [self.task["id"]], "issues": [self.issue["id"]]},
        }
        payload.update(body)
        status, data = (client or self.admin).post("/api/projects/{}/decisions".format(self.pid), payload)
        self.assertEqual(status, 201, data)
        return data["decision"]

    def detail(self, decision_id, client=None):
        return (client or self.admin).get("/api/decisions/{}".format(decision_id))


class TestRecording(DecisionCase):
    def test_everything_is_kept(self):
        d = self.create()
        self.assertEqual((d["seq"], d["status"], d["version"]), (1, "decided", 1))
        self.assertIsNotNone(d["decided_on"])   # 決定なら、日付を入れなくても今日になる
        status, data = self.detail(d["id"])
        self.assertEqual(status, 200)
        dec = data["decision"]
        self.assertEqual([o["adopted"] for o in dec["options"]], [True, False])
        self.assertEqual(dec["options"][1]["reason"], "管理が二重になる")
        self.assertEqual(dec["premises"][0]["review_on"], "2027-03-31")
        self.assertEqual([t["title"] for t in data["tasks"]], ["認証の実装"])
        self.assertEqual([i["title"] for i in data["issues"]], ["認証方式が未定"])
        self.assertEqual(data["versions"][0]["changes"], "作成")
        rows = self.admin.get("/api/projects/{}/decisions".format(self.pid))[1]["decisions"]
        self.assertEqual((rows[0]["rejected_count"], rows[0]["premise_count"]), (1, 1))
        self.assertEqual(self.create(title="二つ目", status="draft")["seq"], 2)

    def test_changing_after_the_decision_needs_a_reason_and_leaves_a_version(self):
        d = self.create()
        path = "/api/decisions/{}".format(d["id"])
        status, err = self.admin.patch(path, {"why": "理由を書き直す"})
        self.assertEqual(status, 400)
        self.assertIn("理由", err["error"])
        status, data = self.admin.patch(path, {
            "why": "理由を書き直す", "premises": [{"text": "全社員が SSO のアカウントを持っている",
                                                "review_on": "2027-03-31", "broken": True}],
            "status": "review", "reason": "協力会社の人は SSO を持っていないと分かった"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["decision"]["version"], 2)
        versions = self.detail(d["id"])[1]["versions"]
        self.assertEqual(versions[0]["changes"], "状態・理由・前提条件")
        self.assertEqual(versions[0]["reason"], "協力会社の人は SSO を持っていないと分かった")
        # 前の版の中身も読める
        old = self.admin.get(path + "/versions/1")[1]
        self.assertEqual(old["snapshot"]["why"], "アカウント管理を一本化するため")
        self.assertEqual(old["snapshot"]["status_label"], "決定")
        # 何も変えなければ版は増えない
        self.assertEqual(self.admin.patch(path, {"why": "理由を書き直す", "reason": "x"})[0], 400)

    def test_drafts_can_change_freely(self):
        d = self.create(status="draft")
        self.assertEqual(self.admin.patch("/api/decisions/{}".format(d["id"]),
                                          {"what": "まだ考え中"})[0], 200)

    def test_superseding_marks_the_old_one(self):
        old = self.create()
        new = self.create(title="認証は IDaaS に切り替える", supersedes_id=old["id"])
        data = self.detail(old["id"])[1]
        self.assertEqual(data["decision"]["status"], "superseded")
        self.assertEqual([r["seq"] for r in data["superseded_by"]], [new["seq"]])
        self.assertIn("置き換えられた", data["versions"][0]["reason"])
        self.assertEqual(self.detail(new["id"])[1]["supersedes"]["seq"], old["seq"])
        # 輪にはできない
        self.assertEqual(self.admin.patch("/api/decisions/{}".format(old["id"]), {
            "supersedes_id": new["id"], "reason": "x"})[0], 400)

    def test_links_and_people_stay_inside_the_project(self):
        other = self.make_project("よそ")
        other_task = self.make_task(other["id"], "よそのタスク")
        status, _ = self.admin.post("/api/projects/{}/decisions".format(self.pid), {
            "title": "x", "links": {"tasks": [other_task["id"]]}})
        self.assertEqual(status, 400)
        outsider, _ = self.make_user("部外者")
        status, _ = self.admin.post("/api/projects/{}/decisions".format(self.pid), {
            "title": "x", "people": [outsider["id"]]})
        self.assertEqual(status, 400)

    def test_search_finds_decisions(self):
        self.create()
        groups = self.admin.get("/api/search?q=SSO")[1]["groups"]
        self.assertIn("decision", [g["kind"] for g in groups])


class TestWhoMaySee(DecisionCase):
    def setUp(self):
        super().setUp()
        self.editor, editor_email = self.make_user("編集者")
        self.viewer, viewer_email = self.make_user("閲覧者")
        status, data = self.admin.post("/api/users", {
            "name": "社外", "email": "g-dec-{}@test.local".format(self.pid), "role": "guest",
            "organization": "協力会社D", "password": "userpassword"})
        self.guest = data["user"]
        self.admin.put("/api/projects/{}/members".format(self.pid), {"members": [
            {"principal_type": "user", "principal_id": self.editor["id"], "role": "editor"},
            {"principal_type": "user", "principal_id": self.viewer["id"], "role": "viewer"},
            {"principal_type": "user", "principal_id": self.guest["id"], "role": "commenter"}]})
        self.editor_client = self.client_for(editor_email)
        self.viewer_client = self.client_for(viewer_email)
        self.guest_client = self.client_for(self.guest["email"])

    def open_to_guests(self):
        self.admin.patch("/api/projects/{}".format(self.pid),
                         {"guest_tabs": ["tasks", "decisions"]})

    def test_editors_record_viewers_read(self):
        d = self.create(self.editor_client)
        self.assertEqual(self.detail(d["id"], self.viewer_client)[0], 200)
        self.assertEqual(self.viewer_client.post("/api/projects/{}/decisions".format(self.pid),
                                                 {"title": "x"})[0], 403)
        self.assertEqual(self.viewer_client.patch("/api/decisions/{}".format(d["id"]),
                                                  {"what": "x", "reason": "x"})[0], 403)
        # 消せるのはプロジェクト管理者だけ
        self.assertEqual(self.editor_client.delete("/api/decisions/{}".format(d["id"]))[0], 403)
        self.assertEqual(self.admin.delete("/api/decisions/{}".format(d["id"]))[0], 200)

    def test_guests_see_only_what_is_opened_to_them(self):
        shown = self.create(title="社外にも見せる決定", guest_visible=True)
        hidden = self.create(title="社内だけの決定")
        draft = self.create(title="検討中の決定", status="draft", guest_visible=True)
        # タブを見せていないうちは、何も見えない
        self.assertEqual(self.guest_client.get("/api/projects/{}/decisions".format(self.pid))[0], 403)
        self.open_to_guests()
        rows = self.guest_client.get("/api/projects/{}/decisions".format(self.pid))[1]["decisions"]
        self.assertEqual([r["title"] for r in rows], ["社外にも見せる決定"])
        for d in (hidden, draft):
            self.assertEqual(self.detail(d["id"], self.guest_client)[0], 404)
        status, data = self.detail(shown["id"], self.guest_client)
        self.assertEqual(status, 200)
        self.assertNotIn("versions", data)          # 変更の理由は社内だけ
        self.assertEqual(data["issues"], [])        # 課題を見せていないので、関連の課題も出さない
        self.assertEqual([t["title"] for t in data["tasks"]], ["認証の実装"])
        self.assertEqual(self.guest_client.get(
            "/api/decisions/{}/versions/1".format(shown["id"]))[0], 403)
        self.assertEqual(self.guest_client.post("/api/projects/{}/decisions".format(self.pid),
                                                {"title": "x"})[0], 403)
        found = str(self.guest_client.get("/api/search?q=決定")[1])
        self.assertIn("社外にも見せる決定", found)
        self.assertNotIn("社内だけの決定", found)

    def test_guests_do_not_see_hidden_neighbours_in_the_chain(self):
        self.open_to_guests()
        old = self.create(title="前の決定（社内だけ）")
        new = self.create(title="新しい決定", guest_visible=True, supersedes_id=old["id"])
        data = self.detail(new["id"], self.guest_client)[1]
        self.assertIsNone(data["supersedes"])
        self.assertNotIn("supersedes_id", data["decision"])
