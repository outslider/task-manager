"""親タスクの進捗は子から集計する。どの画面に渡す値も集計値で、親では入力できない。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_api import ADMIN, ApiTestCase  # noqa: E402

from app import db  # noqa: E402


class TestRollupProgress(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.project = self.make_project("集計PJ")
        pid = self.project["id"]
        self.admin_id = db.scalar("SELECT id FROM users WHERE email=%s", (ADMIN[0],))
        # 親に古い進捗 10% が保存されていても、子（100% と 0%）から 50% になる
        self.parent = self.make_task(pid, title="親タスク", progress=10,
                                     assignee_id=self.admin_id, due_date="2026-12-01")
        self.done = self.make_task(pid, title="終わった子", parent_id=self.parent["id"],
                                   progress=100, status="done")
        self.todo = self.make_task(pid, title="まだの子", parent_id=self.parent["id"], progress=0)

    def test_task_detail_carries_the_rollup(self):
        data = self.admin.get("/api/tasks/{}".format(self.parent["id"]))[1]
        self.assertEqual(data["task"]["child_count"], 2)
        self.assertEqual(data["task"]["rollup_progress"], 50)
        self.assertEqual((data["task"]["leaf_done"], data["task"]["leaf_total"]), (1, 2))
        # 孫がいる子は、子の行でも集計値
        self.make_task(self.project["id"], title="孫", parent_id=self.todo["id"],
                       progress=40)
        data = self.admin.get("/api/tasks/{}".format(self.parent["id"]))[1]
        child = next(c for c in data["children"] if c["id"] == self.todo["id"])
        self.assertEqual((child["child_count"], child["rollup_progress"]), (1, 40))
        self.assertEqual(data["task"]["rollup_progress"], 70)

    def test_search_and_my_tasks_carry_the_rollup(self):
        for path in ("/api/tasks?q=親タスク", "/api/tasks?scope=mine"):
            rows = self.admin.get(path)[1]["tasks"]
            row = next(t for t in rows if t["id"] == self.parent["id"])
            self.assertEqual(row["rollup_progress"], 50, path)

    def test_daily_carries_the_rollup(self):
        data = self.admin.get("/api/daily")[1]
        rows = [t for bucket in data["buckets"].values() for t in bucket]
        row = next(t for t in rows if t["id"] == self.parent["id"])
        self.assertEqual((row["child_count"], row["rollup_progress"]), (2, 50))

    def test_progress_of_a_parent_cannot_be_typed_in(self):
        status, data = self.admin.patch("/api/tasks/{}".format(self.parent["id"]), {"progress": 80})
        self.assertEqual(status, 400, data)
        self.assertIn("子タスク", data["error"])
        # 状態は変えられる
        status, data = self.admin.patch("/api/tasks/{}".format(self.parent["id"]),
                                        {"status": "doing"})
        self.assertEqual(status, 200, data)
        # 子は今までどおり入力できる
        self.assertEqual(self.admin.patch("/api/tasks/{}".format(self.todo["id"]),
                                          {"progress": 50})[0], 200)
        data = self.admin.get("/api/tasks/{}".format(self.parent["id"]))[1]
        self.assertEqual(data["task"]["rollup_progress"], 75)

    def test_daily_update_leaves_a_parent_progress_alone(self):
        status, data = self.admin.post("/api/daily/update", {"updates": [
            {"task_id": self.parent["id"], "progress": 90, "status": "review"}]})
        self.assertEqual(status, 200, data)
        row = db.query_one("SELECT progress, status FROM tasks WHERE id=%s", (self.parent["id"],))
        self.assertEqual((row["progress"], row["status"]), (10, "review"))

    def test_a_heading_child_does_not_make_a_parent(self):
        alone = self.make_task(self.project["id"], title="見出しだけ持つ")
        self.admin.post("/api/tasks", {"project_id": self.project["id"], "title": "見出し",
                                       "is_heading": True, "parent_id": alone["id"]})
        self.assertEqual(self.admin.patch("/api/tasks/{}".format(alone["id"]),
                                          {"progress": 30})[0], 200)

    def test_when_the_last_child_goes_the_parent_is_typed_in_again(self):
        for child in (self.done, self.todo):
            self.admin.delete("/api/tasks/{}".format(child["id"]))
        self.assertEqual(self.admin.patch("/api/tasks/{}".format(self.parent["id"]),
                                          {"progress": 30})[0], 200)
        data = self.admin.get("/api/tasks/{}".format(self.parent["id"]))[1]
        self.assertEqual((data["task"]["child_count"], data["task"]["progress"]), (0, 30))
