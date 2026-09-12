"""自然言語解析とタスク分解の単体テスト（DB 不要）。"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import nlp  # noqa: E402

USERS = [{"id": 2, "name": "佐藤 花子"}, {"id": 3, "name": "鈴木 一郎"}]
PROJECTS = [{"id": 7, "name": "新製品リリース"}]
SATURDAY = date(2026, 9, 12)


def parse(text, base=SATURDAY):
    return nlp.parse(text, users=USERS, projects=PROJECTS, base=base)


class TestDateParsing(unittest.TestCase):
    def test_today_and_tomorrow(self):
        self.assertEqual(parse("明日までに提出")["due_date"], "2026-09-13")
        self.assertEqual(parse("今日中に確認")["due_date"], "2026-09-12")
        self.assertEqual(parse("明後日レビュー")["due_date"], "2026-09-14")

    def test_relative_weekday(self):
        # 2026-09-12 は土曜。来週金曜は 9/18。
        self.assertEqual(parse("来週金曜までに提出")["due_date"], "2026-09-18")
        self.assertEqual(parse("再来週月曜に開始")["start_date"], "2026-09-21")

    def test_bare_weekday_picks_the_next_one(self):
        self.assertEqual(parse("水曜までに提出")["due_date"], "2026-09-16")

    def test_relative_expressions_never_land_in_the_past(self):
        # 土曜に「今週中」と言われたら、金曜ではなく当日に丸める
        self.assertEqual(parse("今週中にレビュー")["due_date"], "2026-09-12")

    def test_month_end(self):
        self.assertEqual(parse("今月末までに請求書を送る")["due_date"], "2026-09-30")
        self.assertEqual(parse("来月末までに契約更新")["due_date"], "2026-10-31")

    def test_explicit_dates(self):
        self.assertEqual(parse("9/30 締め切り")["due_date"], "2026-09-30")
        self.assertEqual(parse("2027-01-15 に納品")["due_date"], "2027-01-15")

    def test_month_day_rolls_over_to_next_year(self):
        self.assertEqual(parse("1/5 に提出", base=date(2026, 12, 20))["due_date"], "2027-01-05")

    def test_offsets(self):
        self.assertEqual(parse("3日後に確認")["due_date"], "2026-09-15")
        self.assertEqual(parse("2週間後にレビュー")["due_date"], "2026-09-26")

    def test_range_becomes_start_and_due(self):
        draft = parse("10/1から10/20まで結合テスト")
        self.assertEqual(draft["start_date"], "2026-10-01")
        self.assertEqual(draft["due_date"], "2026-10-20")

    def test_invalid_date_is_ignored(self):
        self.assertIsNone(parse("13/45 に提出")["due_date"])


class TestFieldExtraction(unittest.TestCase):
    def test_assignee_by_name_with_honorific(self):
        self.assertEqual(parse("鈴木さんが手順書を作成")["assignee_id"], 3)
        self.assertEqual(parse("@佐藤 花子 に依頼")["assignee_id"], 2)

    def test_unknown_name_is_not_assigned(self):
        self.assertIsNone(parse("山田さんが対応")["assignee_id"])

    def test_importance_keywords(self):
        self.assertEqual(parse("至急 障害対応")["priority"], 3)
        self.assertEqual(parse("重要 契約の確認")["priority"], 2)
        self.assertEqual(parse("急がないので資料整理")["priority"], 0)
        self.assertEqual(parse("資料整理")["priority"], 1)

    def test_category_inference(self):
        self.assertEqual(parse("競合の市場調査")["category"], "research")
        self.assertEqual(parse("週次定例の打ち合わせ")["category"], "meeting")
        self.assertEqual(parse("経費精算の申請")["category"], "admin")
        self.assertEqual(parse("本番障害の復旧")["category"], "incident")

    def test_project_is_matched_by_name(self):
        self.assertEqual(parse("新製品リリースのテスト")["project_id"], 7)

    def test_milestone_keyword(self):
        self.assertTrue(parse("10/1 納期")["is_milestone"])
        self.assertFalse(parse("10/1 打ち合わせ")["is_milestone"])

    def test_title_drops_recognised_parts(self):
        draft = parse("来週金曜までに鈴木さんが移行手順書を作成")
        self.assertNotIn("来週金曜", draft["title"])
        self.assertNotIn("鈴木", draft["title"])
        self.assertIn("移行手順書", draft["title"])

    def test_second_line_becomes_the_description(self):
        draft = nlp.parse("手順書の作成\n過去の手順は共有ドライブにあります", base=SATURDAY)
        self.assertEqual(draft["title"], "手順書の作成")
        self.assertIn("共有ドライブ", draft["description"])

    def test_empty_input(self):
        self.assertEqual(nlp.parse("")["title"], "")

    def test_confidence_rises_with_detail(self):
        vague = parse("あれ")
        detailed = parse("来週金曜までに鈴木さんが移行手順書を作成")
        self.assertLess(vague["confidence"], detailed["confidence"])


class TestDecomposition(unittest.TestCase):
    def test_server_migration_matches_the_expected_steps(self):
        result = nlp.decompose("基幹システムのサーバ移行")
        self.assertTrue(result["matched"])
        titles = " ".join(item["title"] for item in result["items"])
        for expected in ("バックアップ", "リハーサル", "切替", "疎通確認", "旧環境の停止"):
            self.assertIn(expected, titles)

    def test_templates_are_ordered_and_categorised(self):
        items = nlp.decompose("リリース作業")["items"]
        self.assertTrue(all(item["category"] for item in items))
        orders = [item["sort_order"] for item in items]
        self.assertEqual(orders, sorted(orders))

    def test_unknown_work_falls_back_to_a_generic_plan(self):
        result = nlp.decompose("よくわからない何か")
        self.assertFalse(result["matched"])
        self.assertTrue(result["items"])

    def test_dates_are_spread_across_the_parent_period(self):
        items = nlp.decompose("サーバ移行", "2026-10-01", "2026-10-20")["items"]
        self.assertEqual(items[0]["start_date"], "2026-10-01")
        self.assertEqual(items[-1]["due_date"], "2026-10-20")
        for earlier, later in zip(items, items[1:]):
            self.assertLessEqual(earlier["due_date"], later["start_date"])

    def test_without_a_period_no_dates_are_invented(self):
        items = nlp.decompose("サーバ移行")["items"]
        self.assertTrue(all(item["due_date"] is None for item in items))

    def test_each_template_is_well_formed(self):
        for template in nlp.TEMPLATES:
            self.assertTrue(template["keywords"], template["name"])
            self.assertGreaterEqual(len(template["steps"]), 4, template["name"])
            for title, category, weight in template["steps"]:
                self.assertTrue(title.strip())
                self.assertGreaterEqual(weight, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
