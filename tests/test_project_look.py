"""プロジェクトごとの Slack の宛先と、プロジェクトの見た目（色・模様・アイコン）。"""
import os
import sys
import uuid
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_api import ApiTestCase  # noqa: E402

from app import db, slack  # noqa: E402

PROJECT_HOOK = "https://hooks.slack.com/services/T000/B000/project"
GLOBAL_HOOK = "https://hooks.slack.com/services/T000/B000/global"


class SyncThread:
    """送信のスレッドをその場で走らせる（テストで結果を待たなくてよいように）。"""

    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class SlackCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.sent = []
        for patcher in (
                mock.patch.object(slack.threading, "Thread", SyncThread),
                mock.patch.object(slack, "post", side_effect=self.record)):
            patcher.start()
            self.addCleanup(patcher.stop)
        db.set_setting("slack_enabled", "1")
        db.set_setting("slack_webhook_url", "")
        db.set_setting("slack_events", "issue,digest")
        self.project = self.make_project("通知PJ{}".format(uuid.uuid4().hex[:4]))

    def tearDown(self):
        db.set_setting("slack_enabled", "0")
        db.set_setting("slack_webhook_url", "")
        db.set_setting("slack_events", "issue,digest")

    def record(self, text, webhook_url=None, project_id=None):
        self.sent.append((text, webhook_url or slack.webhook_for(project_id)))
        return True, "送信しました"

    def set_project(self, **body):
        status, data = self.admin.patch("/api/projects/{}".format(self.project["id"]), body)
        self.assertEqual(status, 200, data)

    def raise_issue(self, severity=2):
        status, data = self.admin.post("/api/issues", {
            "project_id": self.project["id"], "title": "重大な課題", "severity": severity})
        self.assertEqual(status, 201, data)


class TestProjectDestination(SlackCase):
    def test_a_project_hook_works_without_a_global_one(self):
        self.set_project(slack_webhook_url=PROJECT_HOOK)
        self.raise_issue()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][1], PROJECT_HOOK)
        self.assertIn("重大な課題", self.sent[0][0])

    def test_projects_without_a_hook_use_the_global_one(self):
        db.set_setting("slack_webhook_url", GLOBAL_HOOK)
        self.raise_issue()
        self.assertEqual([url for _, url in self.sent], [GLOBAL_HOOK])

    def test_nothing_goes_out_when_there_is_no_destination(self):
        self.raise_issue()
        self.assertEqual(self.sent, [])

    def test_the_master_switch_still_rules(self):
        db.set_setting("slack_enabled", "0")
        self.set_project(slack_webhook_url=PROJECT_HOOK)
        self.raise_issue()
        self.assertEqual(self.sent, [])

    def test_a_muted_project_sends_nothing(self):
        self.set_project(slack_webhook_url=PROJECT_HOOK, notify_enabled=False)
        self.raise_issue()
        self.assertEqual(self.sent, [])

    def test_the_daily_summary_goes_to_the_project_hook(self):
        from app import notify
        self.set_project(slack_webhook_url=PROJECT_HOOK)
        self.make_task(self.project["id"], "期限切れ", due_date="2020-01-01")
        self.assertGreaterEqual(notify.slack_daily_summary(), 1)
        self.assertIn(PROJECT_HOOK, [url for _, url in self.sent])


class TestNewEvents(SlackCase):
    def test_task_done_only_when_chosen(self):
        self.set_project(slack_webhook_url=PROJECT_HOOK)
        task = self.make_task(self.project["id"], "仕上げ")
        self.admin.patch("/api/tasks/{}".format(task["id"]), {"status": "done"})
        self.assertEqual(self.sent, [])
        self.set_project(slack_events=["done"])
        other = self.make_task(self.project["id"], "もう一つ")
        self.admin.patch("/api/tasks/{}".format(other["id"]), {"status": "done"})
        self.assertEqual(len(self.sent), 1)
        self.assertIn("もう一つ", self.sent[0][0])
        # 完了のまま別の項目を変えても、もう一度は送らない
        self.admin.patch("/api/tasks/{}".format(other["id"]), {"title": "もう一つ（改）"})
        self.assertEqual(len(self.sent), 1)

    def test_bulk_done_is_one_message(self):
        self.set_project(slack_webhook_url=PROJECT_HOOK, slack_events=["done"])
        ids = [self.make_task(self.project["id"], "まとめ{}".format(i))["id"] for i in range(3)]
        status, data = self.admin.post("/api/tasks/bulk", {"ids": ids, "status": "done"})
        self.assertEqual(status, 200, data)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("3 件完了", self.sent[0][0])

    def test_daily_update_done(self):
        self.set_project(slack_webhook_url=PROJECT_HOOK, slack_events=["done"])
        admin_id = db.scalar("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1")
        task = self.make_task(self.project["id"], "日次で完了", assignee_id=admin_id)
        self.admin.post("/api/daily/update", {"updates": [{"task_id": task["id"], "status": "done"}]})
        self.assertEqual(len(self.sent), 1)

    def test_ticket_raised_goes_to_the_queue_project(self):
        self.set_project(slack_webhook_url=PROJECT_HOOK, slack_events=["ticket"])
        status, data = self.admin.post("/api/ticket-queues", {
            "name": "窓口{}".format(uuid.uuid4().hex[:4]), "project_id": self.project["id"]})
        self.assertEqual(status, 201, data)
        status, data = self.admin.post("/api/tickets", {"queue_id": data["queue"]["id"],
                                                        "title": "ログインできない"})
        self.assertEqual(status, 201, data)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("ログインできない", self.sent[0][0])
        self.assertEqual(self.sent[0][1], PROJECT_HOOK)


class TestSlackTestButton(SlackCase):
    def test_project_owner_can_try_their_hook(self):
        user, email = self.make_user("PJ管理者")
        owner = self.client_for(email)
        status, data = owner.post("/api/projects", {"name": "自分のPJ"})
        pid = data["project"]["id"]
        status, data = owner.post("/api/settings/test-slack",
                                  {"project_id": pid, "webhook_url": PROJECT_HOOK})
        self.assertEqual((status, data["ok"]), (200, True), data)
        # Slack 以外の宛先へは送れない
        self.assertEqual(owner.post("/api/settings/test-slack", {
            "project_id": pid, "webhook_url": "http://169.254.169.254/latest"})[0], 400)
        # 全体の宛先や、ほかのプロジェクトは試せない
        self.assertEqual(owner.post("/api/settings/test-slack", {})[0], 403)
        self.assertIn(owner.post("/api/settings/test-slack",
                                 {"project_id": self.project["id"]})[0], (403, 404))


class TestProjectLook(ApiTestCase):
    def test_new_projects_get_different_colors(self):
        colors = {self.make_project("色{}".format(i))["color"] for i in range(3)}
        self.assertEqual(len(colors), 3)

    def test_theme_and_icon_are_checked(self):
        project = self.make_project("見た目")
        path = "/api/projects/{}".format(project["id"])
        self.assertEqual(self.admin.patch(path, {"theme": "sparkles"})[0], 400)
        self.assertEqual(self.admin.patch(path, {"icon": "とても長いアイコンの文字"})[0], 400)
        self.assertEqual(self.admin.patch(path, {"icon": "ab"})[0], 400)
        self.assertEqual(self.admin.patch(path, {"color": "red"})[0], 400)
        status, data = self.admin.patch(path, {"theme": "waves", "icon": "🚀", "color": "#0EA5A4"})
        self.assertEqual(status, 200, data)
        row = db.query_one("SELECT color, theme, icon FROM projects WHERE id=%s", (project["id"],))
        self.assertEqual((row["color"], row["theme"], row["icon"]), ("#0ea5a4", "waves", "🚀"))
        self.assertIn("waves", self.admin.get("/api/meta")[1]["project_themes"])

    def test_tinting_is_a_personal_choice(self):
        status, data = self.admin.patch("/api/auth/profile", {"ui_project_tint": False})
        self.assertEqual(status, 200, data)
        self.assertFalse(data["user"]["ui_project_tint"])
        self.assertFalse(self.admin.get("/api/auth/me")[1]["user"]["ui_project_tint"])
        self.admin.patch("/api/auth/profile", {"ui_project_tint": True})
