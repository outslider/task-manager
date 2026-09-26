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


class TestSecondStage(DecisionCase):
    """第 2 段階：メンバー以外の決めた人・決めた場・見直し日の知らせ・関連・Slack・メモからの下書き。"""

    def test_people_outside_the_project(self):
        d = self.create(people_extra="山田社長、佐々木取締役")
        data = self.detail(d["id"])[1]["decision"]
        self.assertEqual(data["people_extra"], ["山田社長", "佐々木取締役"])
        rows = self.admin.get("/api/projects/{}/decisions".format(self.pid))[1]["decisions"]
        self.assertEqual(rows[0]["people_extra"], ["山田社長", "佐々木取締役"])
        # 名前だけを変えても、決めた人の変更として版に残る
        status, data = self.admin.patch("/api/decisions/{}".format(d["id"]), {
            "people_extra": ["山田社長"], "reason": "取締役は出席していなかった"})
        self.assertEqual(status, 200, data)
        versions = self.detail(d["id"])[1]["versions"]
        self.assertEqual(versions[0]["changes"], "決めた人")

    def test_the_meeting_where_it_was_decided(self):
        status, data = self.admin.post("/api/projects/{}/meetings".format(self.pid), {
            "title": "週次定例", "freq": "weekly", "weekdays": [1], "start_on": "2026-09-01",
            "holiday_rule": "skip"})
        meeting = data["meeting"]
        d = self.create(meeting_id=meeting["id"], meeting_on="2026-09-21")
        got = self.detail(d["id"])[1]["decision"]["meeting"]
        self.assertEqual((got["title"], got["on"]), ("週次定例", "2026-09-21"))
        other = self.make_project("よその定例")
        status, data = self.admin.post("/api/projects/{}/meetings".format(other["id"]), {
            "title": "よその会", "freq": "weekly", "weekdays": [1], "start_on": "2026-09-01",
            "holiday_rule": "skip"})
        status, _ = self.admin.post("/api/projects/{}/decisions".format(self.pid), {
            "title": "x", "meeting_id": data["meeting"]["id"]})
        self.assertEqual(status, 400)

    def test_review_dates_notify_once(self):
        from app import notify
        editor, email = self.make_user("決めた編集者")
        self.admin.put("/api/projects/{}/members".format(self.pid), {"members": [
            {"principal_type": "user", "principal_id": editor["id"], "role": "editor"}]})
        d = self.create(people=[editor["id"]], premises=[
            {"text": "予算は 500 万円以内", "review_on": "2020-01-01"},
            {"text": "まだ先の前提", "review_on": "2999-01-01"},
            {"text": "もう崩れた前提", "review_on": "2020-01-01", "broken": True}])
        self.assertGreaterEqual(notify.scan_decision_reviews(), 1)
        self.assertEqual(notify.scan_decision_reviews(), 0)   # 同じ日にもう一度走っても増えない
        client = self.client_for(email)
        notes = client.get("/api/notifications")[1]["notifications"]
        note = next(n for n in notes if n["type"] == "decision_review")
        self.assertEqual(note["decision_id"], d["id"])
        self.assertIn("予算は 500 万円以内", note["body"])
        self.assertNotIn("まだ先の前提", note["body"])
        self.assertNotIn("もう崩れた前提", note["body"])
        # 今日の確認にも出る
        reviews = client.get("/api/daily")[1]["decision_reviews"]
        self.assertEqual([r["id"] for r in reviews], [d["id"]])
        # 見直し日を延ばすと、今日の確認から消える
        self.admin.patch("/api/decisions/{}".format(d["id"]), {
            "premises": [{"text": "予算は 500 万円以内", "review_on": "2999-01-01"}],
            "reason": "来期も予算は変わらないと確認した"})
        self.assertEqual(client.get("/api/daily")[1]["decision_reviews"], [])

    def test_drafts_and_superseded_ones_are_not_nagged(self):
        from app import notify
        self.create(status="draft", premises=[{"text": "検討中の前提", "review_on": "2020-01-01"}])
        before = db.scalar("SELECT COUNT(*) AS c FROM notifications WHERE type='decision_review'")
        notify.scan_decision_reviews()
        after = db.scalar("SELECT COUNT(*) AS c FROM notifications WHERE type='decision_review'")
        self.assertEqual(before, after)

    def test_tasks_and_issues_show_their_decisions(self):
        d = self.create()
        task = self.admin.get("/api/tasks/{}".format(self.task["id"]))[1]
        issue = self.admin.get("/api/issues/{}".format(self.issue["id"]))[1]
        self.assertEqual([x["seq"] for x in task["decisions"]], [d["seq"]])
        self.assertEqual([x["seq"] for x in issue["decisions"]], [d["seq"]])

    def test_slack_on_decided_and_review(self):
        from unittest import mock
        from app import slack
        sent = []
        with mock.patch.object(slack, "post_async", side_effect=lambda text, **k: sent.append((text, k))):
            d = self.create(status="draft")
            self.assertEqual(sent, [])
            self.admin.patch("/api/decisions/{}".format(d["id"]), {"status": "decided"})
            self.admin.patch("/api/decisions/{}".format(d["id"]), {
                "status": "review", "reason": "前提が崩れた"})
        self.assertEqual([k["event"] for _, k in sent], ["decision", "decision"])
        self.assertIn("決定しました：D-", sent[0][0])
        self.assertIn("見直し中", sent[1][0])
        self.assertIn("前提が崩れた", sent[1][0])

    def test_extract_from_notes_by_rule_and_by_claude(self):
        from unittest import mock
        from app import llm
        memo = "・次回の打ち合わせは来週\n・決定：認証は社内 SSO で進める\n・宿題：見積もりを取る"
        path = "/api/projects/{}/decisions/extract".format(self.pid)
        status, data = self.admin.post(path, {"text": memo, "force_rule": True})
        self.assertEqual((status, data["engine"]), (200, "rule"))
        self.assertEqual([r["title"] for r in data["rows"]], ["認証は社内 SSO で進める"])
        admin_name = db.scalar("SELECT name FROM users WHERE id=%s", (self.admin_id,))
        fake = [{"title": "SSO を採用", "what": "社内 SSO", "why": "一本化", "source": "決定",
                 "decided_by": [admin_name, "山田社長"],
                 "options": [{"title": "独自", "adopted": False, "reason": "二重管理"}], "premises": []}]
        with mock.patch.object(llm, "available", return_value=True), \
                mock.patch.object(llm, "extract_decisions", return_value=fake):
            status, data = self.admin.post(path, {"text": memo})
        self.assertEqual((status, data["engine"]), (200, "llm"))
        row = data["rows"][0]
        self.assertEqual(row["people"], [self.admin_id])
        self.assertEqual(row["people_extra"], ["山田社長"])
        # 下書きを返すだけで、記録はしない
        self.assertEqual(db.scalar("SELECT COUNT(*) AS c FROM decisions WHERE project_id=%s",
                                   (self.pid,)), 0)
