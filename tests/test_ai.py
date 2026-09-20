"""会議メモからの一括起票と、進行レビューのテスト。

Claude API は呼ばない。テスト用の DB では連携が無効なので、
メモの読み取りは簡易読み取りに落ち、レビューは断られるのが正しい動き。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_api import ApiTestCase, Client  # noqa: E402
from app.api import _extract_by_rule, _match_member, assignable_users  # noqa: E402


MEMBERS = [
    {"id": 1, "name": "鈴木 一郎"},
    {"id": 2, "name": "佐藤 花子"},
    {"id": 3, "name": "高橋 美咲"},
    {"id": 4, "name": "管理者"},
]


class TestMemberMatching(unittest.TestCase):
    """メモの呼び方と、登録されている氏名を結びつける。"""

    def test_matches_the_exact_name(self):
        self.assertEqual(_match_member("鈴木 一郎", MEMBERS), "鈴木 一郎")

    def test_ignores_the_space_in_the_name(self):
        self.assertEqual(_match_member("鈴木一郎", MEMBERS), "鈴木 一郎")

    def test_strips_honorifics(self):
        self.assertEqual(_match_member("鈴木さん", MEMBERS), "鈴木 一郎")
        self.assertEqual(_match_member("高橋部長", MEMBERS), "高橋 美咲")

    def test_matches_the_given_name(self):
        self.assertEqual(_match_member("花子", MEMBERS), "佐藤 花子")

    def test_gives_up_when_two_people_could_match(self):
        people = MEMBERS + [{"id": 5, "name": "鈴木 二郎"}]
        self.assertEqual(_match_member("鈴木さん", people), "")

    def test_gives_up_on_an_unknown_name(self):
        self.assertEqual(_match_member("だれか", MEMBERS), "")

    def test_empty_stays_empty(self):
        self.assertEqual(_match_member("", MEMBERS), "")
        self.assertEqual(_match_member(None, MEMBERS), "")


class TestRuleExtraction(unittest.TestCase):
    """Claude が使えないときの、1 行 1 件の読み取り。"""

    def test_reads_one_task_per_line(self):
        rows = _extract_by_rule("・移行手順書をまとめる\n・検証環境を用意する", MEMBERS, [])
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["source"] for row in rows))

    def test_drops_bullets_and_numbering(self):
        rows = _extract_by_rule("1. 移行手順書をまとめる", MEMBERS, [])
        self.assertNotIn("1.", rows[0]["title"])

    def test_skips_short_lines_and_headings(self):
        rows = _extract_by_rule("# 定例\n出席\n\n・移行手順書をまとめる", MEMBERS, [])
        self.assertEqual(len(rows), 1)

    def test_does_not_repeat_the_same_task(self):
        rows = _extract_by_rule("・移行手順書をまとめる\n・移行手順書をまとめる", MEMBERS, [])
        self.assertEqual(len(rows), 1)


class TestExtractEndpoint(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.project = self.make_project("メモ取り込みPJ")

    def test_falls_back_to_the_rule_reader(self):
        status, data = self.admin.post("/api/nl/extract", {
            "project_id": self.project["id"],
            "text": "・移行手順書をまとめる\n・検証環境を用意する",
        })
        self.assertEqual(status, 200, data)
        self.assertEqual(data["engine"], "rule")
        self.assertEqual(len(data["rows"]), 2)

    def test_the_rows_can_be_imported_as_they_are(self):
        _status, data = self.admin.post("/api/nl/extract", {
            "project_id": self.project["id"], "text": "・移行手順書をまとめる",
        })
        status, result = self.admin.post(
            "/api/projects/{}/tasks/import".format(self.project["id"]),
            {"rows": data["rows"]})
        self.assertIn(status, (200, 201), result)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["problems"], [])

    def test_empty_text_is_rejected(self):
        status, _ = self.admin.post("/api/nl/extract", {"text": ""})
        self.assertEqual(status, 400)

    def test_a_very_long_memo_is_rejected(self):
        status, _ = self.admin.post("/api/nl/extract", {"text": "あ" * 20001})
        self.assertEqual(status, 400)

    def test_a_stranger_cannot_read_into_someone_elses_project(self):
        _user, email = self.make_user()
        other = self.client_for(email)
        status, _ = other.post("/api/nl/extract", {
            "project_id": self.project["id"], "text": "・なにかする",
        })
        self.assertIn(status, (403, 404))

    def test_login_is_required(self):
        status, _ = Client(self.base).post("/api/nl/extract", {"text": "・なにかする"})
        self.assertEqual(status, 401)


class TestAssignableUsers(ApiTestCase):
    """メンバーから外れても担当のまま、という状態でも取り込みを止めない。

    いまの API は担当に指定するときメンバーかどうかを見るが、
    あとからメンバーを外した場合や、DB へ直接入れた古いデータでは
    「担当だがメンバーではない」人が残りうる。
    """

    def leftover_assignee(self, project, name):
        from app import db as database
        user, _email = self.make_user(name=name)
        task = self.make_task(project["id"], title="割り当て済み")
        database.execute("UPDATE tasks SET assignee_id=%s WHERE id=%s",
                         (user["id"], task["id"]))
        return user

    def test_includes_someone_who_is_already_assigned(self):
        project = self.make_project("担当PJ")
        names = [row["name"] for row in assignable_users(project["id"])]
        self.assertNotIn("担当 一郎", names)

        self.leftover_assignee(project, "担当 一郎")
        names = [row["name"] for row in assignable_users(project["id"])]
        self.assertIn("担当 一郎", names)

    def test_the_importer_accepts_that_person(self):
        project = self.make_project("取り込みPJ")
        self.leftover_assignee(project, "担当 二郎")
        status, result = self.admin.post(
            "/api/projects/{}/tasks/import".format(project["id"]),
            {"rows": [{"title": "メモから", "assignee": "担当 二郎"}]})
        self.assertIn(status, (200, 201), result)
        self.assertEqual(result["problems"], [])


class TestReviewEndpoint(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.project = self.make_project("レビューPJ")

    def test_says_so_when_claude_is_not_configured(self):
        self.make_task(self.project["id"], title="なにかする")
        status, data = self.admin.post(
            "/api/projects/{}/review".format(self.project["id"]), {})
        self.assertEqual(status, 400)
        self.assertIn("Claude", data["error"])

    def test_an_empty_project_is_rejected_before_calling_out(self):
        status, data = self.admin.post(
            "/api/projects/{}/review".format(self.project["id"]), {})
        self.assertEqual(status, 400)
        self.assertIn("タスク", data["error"])

    def test_a_stranger_cannot_ask_about_someone_elses_project(self):
        _user, email = self.make_user()
        other = self.client_for(email)
        status, _ = other.post("/api/projects/{}/review".format(self.project["id"]), {})
        self.assertIn(status, (403, 404))


class TestReviewContext(ApiTestCase):
    """AI に渡す文章に、判断のもとになる数字が入っているか。"""

    def test_the_context_lists_what_is_overdue_and_what_blocks(self):
        from app import graph, workload
        from app.api import _review_context, project_deps
        from app import db as database

        project = self.make_project("文面PJ")
        first = self.make_task(project["id"], title="先にやる", due_date="2020-01-01")
        second = self.make_task(project["id"], title="あとでやる")
        status, body = self.admin.post(
            "/api/tasks/{}/deps".format(second["id"]),
            {"depends_on_id": first["id"]})
        self.assertIn(status, (200, 201), body)

        tasks = database.query(
            "SELECT t.id, t.title, t.status, t.progress, t.start_date, t.due_date, "
            "t.is_milestone, t.updated_at, t.assignee_id, t.estimate_hours, "
            "t.actual_hours, t.project_id, u.name AS assignee_name "
            "FROM tasks t LEFT JOIN users u ON u.id = t.assignee_id "
            "WHERE t.project_id=%s", (project["id"],))
        deps = project_deps(project["id"])
        analysis = graph.bottlenecks(tasks, deps)
        load = workload.build(tasks, [], weeks=4)
        text = _review_context({"name": "文面PJ"}, tasks, deps, analysis, load)

        self.assertIn("文面PJ", text)
        self.assertIn("期限を過ぎているもの", text)
        self.assertIn("先にやる", text)
        self.assertIn("他の作業を止めているもの", text)
        self.assertIn("#{}".format(first["id"]), text)


if __name__ == "__main__":
    unittest.main()
