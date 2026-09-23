"""見出し。タスク一覧とガントで区切りの帯として描くためだけの行。

作業ではないので、数えるところ・並べるところ・分析するところのどこにも
混ざってはいけない。ここでは、その「どこにも」を一か所ずつ確かめる。
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_api import ADMIN, ApiTestCase  # noqa: E402

from app import db  # noqa: E402

HEAD = "◇見出しXYZ"


class TestHeadings(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.project = self.make_project("見出しPJ")
        self.pid = self.project["id"]
        self.task = self.make_task(self.pid, title="ふつうの作業XYZ")
        self.heading = self.make_heading()

    def make_heading(self, title=HEAD, **kwargs):
        body = {"project_id": self.pid, "title": title, "is_heading": True}
        body.update(kwargs)
        status, data = self.admin.post("/api/tasks", body)
        self.assertEqual(status, 201, data)
        return data["task"]

    def admin_id(self):
        return db.scalar("SELECT id FROM users WHERE email=%s", (ADMIN[0],))

    def dumped(self, path):
        status, data = self.admin.get(path)
        self.assertEqual(status, 200, data)
        return json.dumps(data, ensure_ascii=False)

    # --- 作る・変える ----------------------------------------------------

    def test_a_heading_has_only_a_name(self):
        heading = self.make_heading("見出し2", status="done", due_date="2026-10-01",
                                    start_date="2026-09-01", assignee_id=self.admin_id(),
                                    is_milestone=True, estimate_hours=5, priority=3,
                                    depends_on=[self.task["id"]])
        self.assertEqual(heading["is_heading"], 1)
        self.assertEqual(heading["status"], "todo")
        self.assertIsNone(heading["due_date"])
        self.assertIsNone(heading["start_date"])
        self.assertIsNone(heading["assignee_id"])
        self.assertEqual(heading["is_milestone"], 0)
        self.assertIsNone(heading["estimate_hours"])
        self.assertEqual(db.scalar("SELECT COUNT(*) AS c FROM task_deps WHERE task_id=%s",
                                   (heading["id"],)), 0)

    def test_only_name_and_place_can_change(self):
        url = "/api/tasks/{}".format(self.heading["id"])
        status, data = self.admin.patch(url, {"title": "改名"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["task"]["title"], "改名")
        for patch in ({"status": "done"}, {"due_date": "2026-10-01"},
                      {"assignee_id": self.admin_id()}, {"progress": 50},
                      {"is_milestone": True}, {"depends_on": [self.task["id"]]},
                      {"title": "x", "status": "doing"}):
            self.assertEqual(self.admin.patch(url, patch)[0], 400, patch)
        parent = self.make_task(self.pid, title="親")
        status, data = self.admin.patch(url, {"parent_id": parent["id"]})
        self.assertEqual(status, 200, data)

    def test_nothing_can_be_placed_under_a_heading(self):
        hid = self.heading["id"]
        status, _ = self.admin.post("/api/tasks", {"project_id": self.pid, "title": "子",
                                                   "parent_id": hid})
        self.assertEqual(status, 400)
        self.assertEqual(self.admin.patch("/api/tasks/{}".format(self.task["id"]),
                                          {"parent_id": hid})[0], 400)
        self.assertEqual(self.admin.post("/api/tasks/reorder", {
            "project_id": self.pid,
            "items": [{"id": self.task["id"], "parent_id": hid, "sort_order": 1}]})[0], 400)
        self.assertEqual(self.admin.post("/api/projects/{}/recurrences".format(self.pid), {
            "title": "定例", "freq": "weekly", "weekdays": [1], "next_on": "2026-10-06",
            "parent_id": hid})[0], 400)
        status, data = self.admin.post("/api/templates", {
            "name": "雛形", "scope": "tasks", "task_id": self.task["id"]})
        self.assertEqual(status, 201, data)
        self.assertEqual(self.admin.post("/api/templates/{}/apply".format(data["template"]["id"]), {
            "project_id": self.pid, "parent_id": hid})[0], 400)

    def test_a_meeting_can_sit_in_a_heading_section(self):
        # 定例会議はタスクではないので、見出しの区切りに置ける
        status, data = self.admin.post("/api/projects/{}/meetings".format(self.pid), {
            "title": "区切りの定例", "freq": "weekly", "weekdays": [1],
            "parent_id": self.heading["id"]})
        self.assertEqual(status, 201, data)
        self.assertEqual(data["meeting"]["parent_id"], self.heading["id"])
        # 見出しを消すと、定例は先頭の「定例」へ移り、戻すと元の区切りに戻る
        result = self.admin.delete("/api/tasks/{}".format(self.heading["id"]))[1]
        self.assertIsNone(db.scalar("SELECT parent_id AS p FROM meetings WHERE id=%s",
                                    (data["meeting"]["id"],)))
        self.admin.post("/api/trash/{}/restore".format(result["trash_id"]), {})
        self.assertEqual(db.scalar("SELECT parent_id AS p FROM meetings WHERE id=%s",
                                   (data["meeting"]["id"],)), self.heading["id"])

    def test_no_dependencies_and_no_links(self):
        hid = self.heading["id"]
        tid = self.task["id"]
        self.assertEqual(self.admin.post("/api/tasks/{}/deps".format(tid),
                                         {"depends_on_id": hid})[0], 400)
        self.assertEqual(self.admin.post("/api/tasks/{}/deps".format(hid),
                                         {"depends_on_id": tid})[0], 400)
        self.assertEqual(self.admin.patch("/api/tasks/{}".format(tid),
                                          {"depends_on": [hid]})[0], 400)
        self.assertEqual(self.admin.post("/api/issues", {
            "project_id": self.pid, "title": "課題", "task_ids": [hid]})[0], 400)

    # --- 一覧に出るところ（区切りとして要る画面） --------------------------

    def test_project_list_and_gantt_include_headings(self):
        tasks = self.admin.get("/api/projects/{}/tasks".format(self.pid))[1]["tasks"]
        heading = next(t for t in tasks if t["id"] == self.heading["id"])
        self.assertEqual(heading["is_heading"], 1)
        rows = self.admin.get("/api/gantt?project_ids={}".format(self.pid))[1]["tasks"]
        self.assertEqual(next(t for t in rows if t["id"] == self.heading["id"])["is_heading"], 1)

    # --- 混ざってはいけないところ --------------------------------------------

    def test_not_counted_in_project_stats(self):
        projects = self.admin.get("/api/projects")[1]["projects"]
        mine = next(p for p in projects if p["id"] == self.pid)
        self.assertEqual(mine["stats"]["total"], 1)
        self.admin.patch("/api/tasks/{}".format(self.task["id"]), {"status": "done"})
        projects = self.admin.get("/api/projects")[1]["projects"]
        mine = next(p for p in projects if p["id"] == self.pid)
        self.assertEqual((mine["stats"]["done"], mine["stats"]["total"]), (1, 1))

    def test_parent_rollup_ignores_headings(self):
        parent = self.make_task(self.pid, title="親")
        self.make_task(self.pid, title="子", parent_id=parent["id"], progress=100,
                       status="done", start_date="2026-10-01", due_date="2026-10-05")
        self.make_heading("子の見出し", parent_id=parent["id"])
        tasks = self.admin.get("/api/projects/{}/tasks".format(self.pid))[1]["tasks"]
        row = next(t for t in tasks if t["id"] == parent["id"])
        self.assertEqual(row["child_count"], 1)
        self.assertEqual(row["rollup_progress"], 100)
        # 子が見出しだけのタスクは、まとめ役ではない（日程をそのまま動かせる）
        alone = self.make_task(self.pid, title="見出しだけ持つ")
        self.make_heading("その下の見出し", parent_id=alone["id"])
        tasks = self.admin.get("/api/projects/{}/tasks".format(self.pid))[1]["tasks"]
        self.assertEqual(next(t for t in tasks if t["id"] == alone["id"])["child_count"], 0)

    def test_not_in_task_search(self):
        for path in ("/api/tasks?q=XYZ", "/api/tasks?project_id={}".format(self.pid),
                     "/api/tasks?status=open", "/api/search?q=XYZ"):
            text = self.dumped(path)
            self.assertIn("ふつうの作業XYZ", text, path)
            self.assertNotIn(HEAD, text, path)

    def test_not_in_task_detail_children(self):
        parent = self.make_task(self.pid, title="親")
        self.make_task(self.pid, title="子", parent_id=parent["id"])
        self.make_heading("詳細に出ない見出し", parent_id=parent["id"])
        children = self.admin.get("/api/tasks/{}".format(parent["id"]))[1]["children"]
        self.assertEqual([c["title"] for c in children], ["子"])

    def test_not_in_bottlenecks_or_workload(self):
        self.assertNotIn(HEAD, self.dumped("/api/projects/{}/bottlenecks".format(self.pid)))
        data = self.admin.get("/api/workload?project_id={}".format(self.pid))[1]
        self.assertEqual(data["open_tasks"], 1)
        self.assertEqual(sum(e["count"] for e in data["unscheduled"]), 1)

    def test_bulk_edit_skips_headings_but_delete_works(self):
        ids = [self.task["id"], self.heading["id"]]
        status, data = self.admin.post("/api/tasks/bulk", {"ids": ids, "status": "done"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["updated"], 1)
        row = db.query_one("SELECT status, completed_at FROM tasks WHERE id=%s",
                           (self.heading["id"],))
        self.assertEqual((row["status"], row["completed_at"]), ("todo", None))
        status, data = self.admin.post("/api/tasks/bulk", {
            "ids": ids, "action": "shift", "days": 3})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.admin.post("/api/tasks/bulk", {
            "ids": [self.heading["id"]], "due_date": "2026-10-01"})[1]["updated"], 0)
        self.assertIsNone(db.scalar("SELECT due_date AS d FROM tasks WHERE id=%s",
                                    (self.heading["id"],)))
        status, data = self.admin.post("/api/tasks/bulk", {"ids": ids, "action": "delete"})
        self.assertEqual((status, data["deleted"]), (200, 2))

    def test_duplicate_and_templates_keep_headings(self):
        parent = self.make_task(self.pid, title="まとまり")
        self.make_heading("まとまりの見出し", parent_id=parent["id"])
        self.make_task(self.pid, title="まとまりの作業", parent_id=parent["id"])
        status, data = self.admin.post("/api/tasks/{}/duplicate".format(parent["id"]), {})
        self.assertEqual(status, 201, data)
        copies = db.query("SELECT title, is_heading FROM tasks WHERE parent_id=%s "
                          "ORDER BY sort_order", (data["task"]["id"],))
        self.assertEqual([(c["title"], c["is_heading"]) for c in copies],
                         [("まとまりの見出し", 1), ("まとまりの作業", 0)])


if __name__ == "__main__":
    unittest.main()
