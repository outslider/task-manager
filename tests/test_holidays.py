"""祝日の計算（外部サービスに問い合わせずに求める）。"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TM_CONFIG", os.path.join(os.path.dirname(__file__), "test-config.ini"))
os.environ.setdefault("TM_DB_NAME", os.environ.get("TM_TEST_DB_NAME", "task_manager_test"))
os.environ.setdefault("TM_DB_USER", "tmapp")
os.environ.setdefault("TM_DB_PASSWORD", "tmapp_dev_pw")

from app import holidays, workload  # noqa: E402


class TestNationalHolidays(unittest.TestCase):
    def names(self, year):
        return {day: name for day, name in holidays.national_holidays(year).items()}

    def test_fixed_days(self):
        days = self.names(2026)
        self.assertEqual(days[date(2026, 1, 1)], "元日")
        self.assertEqual(days[date(2026, 2, 11)], "建国記念の日")
        self.assertEqual(days[date(2026, 4, 29)], "昭和の日")
        self.assertEqual(days[date(2026, 8, 11)], "山の日")
        self.assertEqual(days[date(2026, 11, 3)], "文化の日")

    def test_happy_monday(self):
        """成人の日は1月第2月曜、海の日は7月第3月曜。"""
        days = self.names(2026)
        self.assertEqual(days[date(2026, 1, 12)], "成人の日")
        self.assertEqual(days[date(2026, 7, 20)], "海の日")
        self.assertEqual(days[date(2026, 9, 21)], "敬老の日")
        self.assertEqual(days[date(2026, 10, 12)], "スポーツの日")

    def test_equinox_matches_the_published_dates(self):
        """春分・秋分は官報で公表された日と一致すること。"""
        expected = {
            2024: (date(2024, 3, 20), date(2024, 9, 22)),
            2025: (date(2025, 3, 20), date(2025, 9, 23)),
            2026: (date(2026, 3, 20), date(2026, 9, 23)),
            2027: (date(2027, 3, 21), date(2027, 9, 23)),
            2028: (date(2028, 3, 20), date(2028, 9, 22)),
            2029: (date(2029, 3, 20), date(2029, 9, 23)),
        }
        for year, (vernal, autumnal) in expected.items():
            days = self.names(year)
            self.assertEqual(days.get(vernal), "春分の日", "{}年の春分".format(year))
            self.assertEqual(days.get(autumnal), "秋分の日", "{}年の秋分".format(year))

    def test_substitute_holiday(self):
        """日曜と重なったら翌日以降の最初の平日が振替休日になる。"""
        days = self.names(2026)
        self.assertEqual(days[date(2026, 5, 3)], "憲法記念日")   # 日曜
        self.assertEqual(days[date(2026, 5, 6)], "振替休日")     # 4日・5日が祝日なので6日
        days = self.names(2027)
        self.assertEqual(days[date(2027, 3, 22)], "振替休日")    # 春分(日)の翌日

    def test_citizens_holiday(self):
        """祝日に挟まれた平日は国民の休日（2026年9月22日）。"""
        days = self.names(2026)
        self.assertEqual(days[date(2026, 9, 22)], "国民の休日")

    def test_emperors_birthday_moved_in_2020(self):
        self.assertIn(date(2019, 12, 23), self.names(2019))
        self.assertIn(date(2020, 2, 23), self.names(2020))
        self.assertNotIn(date(2020, 12, 23), self.names(2020))

    def test_is_holiday_covers_weekends(self):
        self.assertTrue(holidays.is_holiday(date(2026, 9, 19)))   # 土
        self.assertTrue(holidays.is_holiday(date(2026, 9, 20)))   # 日
        self.assertFalse(holidays.is_holiday(date(2026, 9, 18)))  # 金

    def test_business_days_skip_holidays(self):
        # 2026/9/18(金) 〜 9/25(金): 土日 + 敬老 + 国民 + 秋分 を除くと 18,24,25 の 3 日
        self.assertEqual(holidays.business_days(date(2026, 9, 18), date(2026, 9, 25)), 3)

    def test_add_business_days(self):
        # 金曜から1営業日後は月曜。ただし月曜が祝日ならその次
        self.assertEqual(holidays.add_business_days(date(2026, 9, 18), 1), date(2026, 9, 24))
        self.assertEqual(holidays.add_business_days(date(2026, 9, 24), -1), date(2026, 9, 18))
        self.assertEqual(holidays.add_business_days(date(2026, 9, 18), 0), date(2026, 9, 18))

    def test_holidays_between_spans_years(self):
        found = holidays.holidays_between(date(2026, 12, 20), date(2027, 1, 15))
        self.assertIn(date(2027, 1, 1), found)
        self.assertIn(date(2027, 1, 11), found)


class TestWorkloadWithHolidays(unittest.TestCase):
    def test_capacity_drops_on_a_week_with_holidays(self):
        monday = date(2026, 9, 21)     # 敬老の日・国民の休日・秋分の日で 3 日休み
        off = set(holidays.holidays_between(monday, monday + __import__("datetime").timedelta(days=6)))
        result = workload.build([], [], weeks=2, base=monday, hours_per_day=8.0, holidays=off)
        self.assertEqual(result["weeks"][0]["capacity"], 16.0)
        self.assertEqual(len(result["weeks"][0]["holidays"]), 3)
        self.assertEqual(result["weeks"][1]["capacity"], 40.0)

    def test_ratio_uses_that_week_capacity(self):
        monday = date(2026, 9, 21)
        off = set(holidays.holidays_between(monday, monday + __import__("datetime").timedelta(days=6)))
        task = {"id": 1, "title": "詰め込み", "status": "todo", "assignee_id": 1,
                "start_date": "2026-09-24", "due_date": "2026-09-25",
                "estimate_hours": 16, "progress": 0}
        result = workload.build([task], [{"id": 1, "name": "A", "avatar_color": "#000"}],
                                weeks=1, base=monday, hours_per_day=8.0, holidays=off)
        # 16h を 16h の枠でこなす = ちょうど 100%
        self.assertEqual(result["rows"][0]["cells"][0]["ratio"], 1.0)

    def test_task_span_skips_holidays(self):
        span = workload.task_span(
            {"start_date": "2026-09-21", "due_date": "2026-09-25"},
            holidays=holidays.holidays_between(date(2026, 9, 21), date(2026, 9, 25)))
        self.assertEqual([d.isoformat() for d in span], ["2026-09-24", "2026-09-25"])


if __name__ == "__main__":
    unittest.main()
