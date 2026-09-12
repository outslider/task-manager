"""負荷集計と繰り返し規則の単体テスト（DB 不要）。"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import recurrence, workload  # noqa: E402

USERS = [{"id": 2, "name": "佐藤 花子", "avatar_color": "#f6a623"},
         {"id": 3, "name": "鈴木 一郎", "avatar_color": "#2ec4b6"}]
SATURDAY = date(2026, 9, 12)


def task(task_id, **kwargs):
    base = {"id": task_id, "title": f"T{task_id}", "status": "todo", "assignee_id": 2,
            "start_date": None, "due_date": None, "estimate_hours": None,
            "actual_hours": 0, "progress": 0}
    base.update(kwargs)
    return base


class TestWorkload(unittest.TestCase):
    def build(self, tasks, **kwargs):
        return workload.build(tasks, USERS, base=SATURDAY, **kwargs)

    def test_estimate_is_spread_over_business_days(self):
        # 9/14(月)〜9/18(金) の5営業日に 40h → その週に 40h
        result = self.build([task(1, start_date="2026-09-14", due_date="2026-09-18",
                                  estimate_hours=40)], weeks=3)
        row = result["rows"][0]
        self.assertEqual(row["cells"][1]["hours"], 40.0)
        self.assertEqual(row["cells"][1]["ratio"], 1.0)

    def test_weekends_are_excluded(self):
        # 土日だけの期間でも 0 除算にならず、1日分として扱う
        result = self.build([task(1, start_date="2026-09-12", due_date="2026-09-13",
                                  estimate_hours=8)], weeks=2)
        self.assertEqual(round(sum(c["hours"] for c in result["rows"][0]["cells"]), 1), 8.0)

    def test_progress_reduces_the_remaining_load(self):
        result = self.build([task(1, start_date="2026-09-14", due_date="2026-09-18",
                                  estimate_hours=40, progress=75)], weeks=3)
        self.assertEqual(result["rows"][0]["cells"][1]["hours"], 10.0)

    def test_overload_is_visible_as_a_ratio_above_one(self):
        tasks = [task(1, start_date="2026-09-14", due_date="2026-09-18", estimate_hours=40),
                 task(2, start_date="2026-09-14", due_date="2026-09-18", estimate_hours=20)]
        result = self.build(tasks, weeks=3)
        self.assertGreater(result["rows"][0]["cells"][1]["ratio"], 1.0)
        self.assertGreater(result["rows"][0]["peak_ratio"], 1.0)

    def test_counts_work_without_any_estimate(self):
        result = self.build([task(1, start_date="2026-09-14", due_date="2026-09-18"),
                             task(2, start_date="2026-09-14", due_date="2026-09-18")], weeks=3)
        self.assertEqual(result["rows"][0]["cells"][1]["count"], 2)
        self.assertEqual(result["rows"][0]["cells"][1]["hours"], 0.0)
        self.assertFalse(result["has_estimates"])
        self.assertEqual(result["missing_estimate"], 2)

    def test_completed_tasks_are_excluded(self):
        result = self.build([task(1, status="done", start_date="2026-09-14",
                                  due_date="2026-09-18", estimate_hours=40)], weeks=3)
        self.assertEqual(result["rows"], [])

    def test_tasks_without_dates_are_listed_as_unscheduled(self):
        result = self.build([task(1, estimate_hours=8)], weeks=3)
        self.assertEqual(result["unscheduled"][0]["count"], 1)
        self.assertEqual(result["unscheduled"][0]["name"], "佐藤 花子")

    def test_unassigned_tasks_get_their_own_row(self):
        result = self.build([task(1, assignee_id=None, start_date="2026-09-14",
                                  due_date="2026-09-18")], weeks=3)
        self.assertEqual(result["rows"][0]["name"], "未割当")

    def test_capacity_follows_the_configured_working_day(self):
        result = self.build([], weeks=2, hours_per_day=6)
        self.assertEqual(result["capacity_per_week"], 30.0)

    def test_a_task_spanning_weeks_is_counted_once_per_week(self):
        result = self.build([task(1, start_date="2026-09-14", due_date="2026-09-25",
                                  estimate_hours=80)], weeks=4)
        cells = result["rows"][0]["cells"]
        self.assertEqual(cells[1]["count"], 1)
        self.assertEqual(cells[2]["count"], 1)
        self.assertEqual(result["rows"][0]["total_count"], 1)


class TestEffortSummary(unittest.TestCase):
    def test_accuracy_uses_completed_tasks_only(self):
        tasks = [
            task(1, status="done", estimate_hours=10, actual_hours=12),
            task(2, status="doing", estimate_hours=20, actual_hours=5),
        ]
        summary = workload.effort_summary(tasks)
        self.assertEqual(summary["estimated"], 30.0)
        self.assertEqual(summary["actual"], 17.0)
        self.assertEqual(summary["done_estimated"], 10.0)
        self.assertEqual(summary["accuracy"], 1.2)

    def test_tasks_without_any_effort_are_ignored(self):
        self.assertEqual(workload.effort_summary([task(1)])["tracked"], 0)

    def test_accuracy_is_none_without_completed_estimates(self):
        self.assertIsNone(workload.effort_summary([task(1, estimate_hours=5)])["accuracy"])


class TestRecurrenceRules(unittest.TestCase):
    def test_weekly_multiple_weekdays(self):
        rule = {"freq": "weekly", "interval_n": 1, "weekdays": "0,2,4"}
        day = date(2026, 9, 14)          # 月
        self.assertEqual(recurrence.next_date(rule, day), date(2026, 9, 16))   # 水
        self.assertEqual(recurrence.next_date(rule, date(2026, 9, 18)), date(2026, 9, 21))

    def test_weekly_with_an_interval(self):
        rule = {"freq": "weekly", "interval_n": 2, "weekdays": "0"}
        self.assertEqual(recurrence.next_date(rule, date(2026, 9, 14)), date(2026, 9, 28))

    def test_monthly_clamps_to_the_last_day(self):
        rule = {"freq": "monthly", "interval_n": 1, "month_day": 31}
        self.assertEqual(recurrence.next_date(rule, date(2026, 1, 31)), date(2026, 2, 28))
        self.assertEqual(recurrence.next_date(rule, date(2028, 1, 31)), date(2028, 2, 29))

    def test_monthly_normal_case(self):
        rule = {"freq": "monthly", "interval_n": 1, "month_day": 25}
        self.assertEqual(recurrence.next_date(rule, date(2026, 9, 25)), date(2026, 10, 25))

    def test_daily_interval(self):
        rule = {"freq": "daily", "interval_n": 3}
        self.assertEqual(recurrence.next_date(rule, date(2026, 9, 12)), date(2026, 9, 15))

    def test_describe_is_readable(self):
        self.assertEqual(recurrence.describe(
            {"freq": "weekly", "interval_n": 1, "weekdays": "0,4"}), "毎週 月金曜")
        self.assertEqual(recurrence.describe(
            {"freq": "monthly", "interval_n": 1, "month_day": 25}), "毎月 25日")
        self.assertEqual(recurrence.describe({"freq": "daily", "interval_n": 2}), "2日ごと")

    def test_weekday_parsing_ignores_rubbish(self):
        self.assertEqual(recurrence.parse_weekdays("0,9,x,3,3"), [0, 3])
        self.assertEqual(recurrence.parse_weekdays(""), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
