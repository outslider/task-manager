"""Unit tests for the dependency graph analysis (no database required)."""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import graph  # noqa: E402


def task(task_id, title="T", status="todo", start=None, due=None, milestone=0):
    return {"id": task_id, "title": title, "status": status, "start_date": start,
            "due_date": due, "is_milestone": milestone, "priority": 1, "progress": 0,
            "category": ""}


def dep(task_id, depends_on_id):
    return {"task_id": task_id, "depends_on_id": depends_on_id}


class TestBlockingImpact(unittest.TestCase):
    def test_counts_direct_and_transitive_successors(self):
        # 1 -> 2 -> 3,  1 -> 4
        tasks = [task(i) for i in (1, 2, 3, 4)]
        deps = [dep(2, 1), dep(3, 2), dep(4, 1)]
        m = graph.analyze(tasks, deps)["metrics"]
        self.assertEqual(m[1]["blocks_direct"], 2)
        self.assertEqual(m[1]["blocks_total"], 3)
        self.assertEqual(m[2]["blocks_total"], 1)
        self.assertEqual(m[3]["blocks_total"], 0)

    def test_completed_successors_are_excluded_from_the_open_count(self):
        tasks = [task(1), task(2, status="done"), task(3)]
        m = graph.analyze(tasks, [dep(2, 1), dep(3, 1)])["metrics"]
        self.assertEqual(m[1]["blocks_total"], 2)
        self.assertEqual(m[1]["blocks_open"], 1)

    def test_a_task_is_blocked_only_by_unfinished_predecessors(self):
        tasks = [task(1, status="done"), task(2), task(3)]
        m = graph.analyze(tasks, [dep(3, 1), dep(3, 2)])["metrics"]
        self.assertEqual(m[3]["blocked_by"], 2)
        self.assertEqual(m[3]["blocked_by_open"], 1)
        self.assertTrue(m[3]["is_blocked"])

    def test_finished_tasks_are_never_reported_as_blocked(self):
        tasks = [task(1), task(2, status="done")]
        m = graph.analyze(tasks, [dep(2, 1)])["metrics"]
        self.assertFalse(m[2]["is_blocked"])

    def test_ignores_dependencies_pointing_outside_the_task_set(self):
        m = graph.analyze([task(1)], [dep(1, 99), dep(99, 1)])["metrics"]
        self.assertEqual(m[1]["blocked_by"], 0)
        self.assertEqual(m[1]["blocks_direct"], 0)

    def test_survives_a_dependency_cycle(self):
        tasks = [task(1), task(2)]
        result = graph.analyze(tasks, [dep(1, 2), dep(2, 1)])
        self.assertEqual(set(result["metrics"]), {1, 2})


class TestCriticalPath(unittest.TestCase):
    def test_longest_chain_has_no_slack(self):
        # long: 1(5d) -> 2(15d) -> 4(16d);  short branch: 1 -> 3(3d)
        tasks = [
            task(1, start="2026-01-01", due="2026-01-05"),
            task(2, start="2026-01-06", due="2026-01-20"),
            task(3, start="2026-01-06", due="2026-01-08"),
            task(4, start="2026-01-10", due="2026-01-25"),
        ]
        deps = [dep(2, 1), dep(3, 1), dep(4, 2)]
        result = graph.analyze(tasks, deps)
        self.assertEqual(result["critical_path"], [1, 2, 4])
        self.assertEqual(result["metrics"][1]["slack_days"], 0)
        self.assertGreater(result["metrics"][3]["slack_days"], 0)
        self.assertFalse(result["metrics"][3]["is_critical"])

    def test_isolated_tasks_are_not_on_the_critical_path(self):
        tasks = [task(1, start="2026-01-01", due="2026-01-02"), task(2)]
        result = graph.analyze(tasks, [])
        self.assertEqual(result["critical_path"], [])
        self.assertFalse(result["metrics"][1]["is_critical"])

    def test_milestones_have_no_duration(self):
        self.assertEqual(graph.duration_days(task(1, due="2026-01-05", milestone=1)), 0)
        self.assertEqual(graph.duration_days(task(1, start="2026-01-01", due="2026-01-05")), 5)
        self.assertEqual(graph.duration_days(task(1)), 1)


class TestConflicts(unittest.TestCase):
    def test_flags_a_successor_starting_before_its_predecessor_ends(self):
        tasks = [
            task(1, "先行", start="2026-01-01", due="2026-01-20"),
            task(2, "後続", start="2026-01-10", due="2026-01-30"),
        ]
        conflicts = graph.analyze(tasks, [dep(2, 1)])["conflicts"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["overlap_days"], 10)
        self.assertEqual(conflicts[0]["depends_on_id"], 1)

    def test_no_conflict_when_the_order_is_consistent(self):
        tasks = [
            task(1, start="2026-01-01", due="2026-01-05"),
            task(2, start="2026-01-06", due="2026-01-10"),
        ]
        self.assertEqual(graph.analyze(tasks, [dep(2, 1)])["conflicts"], [])

    def test_no_conflict_when_dates_are_missing(self):
        self.assertEqual(graph.analyze([task(1), task(2)], [dep(2, 1)])["conflicts"], [])


class TestBottleneckRanking(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 1, 15)

    def test_the_task_holding_up_the_most_work_ranks_first(self):
        tasks = [task(1, "根っこ"), task(2), task(3), task(4, "枝")]
        deps = [dep(2, 1), dep(3, 2), dep(4, 1)]
        ranked = graph.bottlenecks(tasks, deps, today=self.today)["bottlenecks"]
        self.assertEqual(ranked[0]["title"], "根っこ")
        self.assertEqual(ranked[0]["blocks_open"], 3)

    def test_completed_tasks_are_excluded(self):
        tasks = [task(1, "完了済み", status="done"), task(2)]
        ranked = graph.bottlenecks(tasks, [dep(2, 1)], today=self.today)["bottlenecks"]
        self.assertNotIn("完了済み", [r["title"] for r in ranked])

    def test_quiet_tasks_are_not_listed(self):
        ranked = graph.bottlenecks([task(1, "普通のタスク")], [], today=self.today)["bottlenecks"]
        self.assertEqual(ranked, [])

    def test_reasons_explain_the_ranking(self):
        tasks = [task(1, "遅れている", due="2026-01-10"), task(2)]
        ranked = graph.bottlenecks(tasks, [dep(2, 1)], today=self.today)["bottlenecks"]
        reasons = ranked[0]["reasons"]
        self.assertIn("後続 1 件が待機", reasons)
        self.assertIn("期限 5 日超過", reasons)
        self.assertEqual(ranked[0]["overdue_days"], 5)

    def test_explicitly_blocked_status_is_surfaced(self):
        ranked = graph.bottlenecks([task(1, "止まっている", status="blocked")], [],
                                   today=self.today)["bottlenecks"]
        self.assertEqual(len(ranked), 1)
        self.assertIn("ブロック中として登録", ranked[0]["reasons"])

    def test_limit_is_respected(self):
        tasks = [task(i, "t{}".format(i), status="blocked") for i in range(1, 11)]
        ranked = graph.bottlenecks(tasks, [], today=self.today, limit=3)["bottlenecks"]
        self.assertEqual(len(ranked), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
