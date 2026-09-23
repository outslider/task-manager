"""定例会議。開催日の数え方と、API の権限・入力チェック。"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_api import ApiTestCase  # noqa: E402

from app import db, meetings  # noqa: E402


def rule(**kwargs):
    base = {"freq": "weekly", "interval_n": 1, "weekdays": "1", "month_mode": "day",
            "month_day": None, "nth": None, "nth_weekday": None, "time_text": "",
            "holiday_rule": "next", "start_on": date(2026, 9, 1), "end_on": None}
    base.update(kwargs)
    return base


# 2026 年 9 月は 21 日（敬老の日）・22 日（国民の休日）・23 日（秋分の日）が続く
SILVER_WEEK = {date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)}


def off(day):
    return day.weekday() >= 5 or day in SILVER_WEEK


def never_off(day):
    return False


def days(m, lo, hi, is_off=never_off):
    return [d for _, d in meetings.scheduled(m, lo, hi, is_off)]


SEP1, SEP30 = date(2026, 9, 1), date(2026, 9, 30)


class TestRawDates(unittest.TestCase):
    def test_every_tuesday(self):
        self.assertEqual(days(rule(), SEP1, SEP30),
                         [date(2026, 9, d) for d in (1, 8, 15, 22, 29)])

    def test_every_other_week_counts_from_the_start_week(self):
        m = rule(interval_n=2)
        self.assertEqual(days(m, SEP1, SEP30), [date(2026, 9, d) for d in (1, 15, 29)])
        # 見る期間の始まりをずらしても、何週目かは開始日から数える
        self.assertEqual(days(m, date(2026, 9, 7), SEP30),
                         [date(2026, 9, 15), date(2026, 9, 29)])

    def test_two_weekdays(self):
        m = rule(weekdays="1,3")
        self.assertEqual(days(m, SEP1, date(2026, 9, 10)),
                         [date(2026, 9, d) for d in (1, 3, 8, 10)])

    def test_month_day_31_falls_back_to_the_last_day(self):
        m = rule(freq="monthly", month_day=31, start_on=date(2026, 1, 1))
        self.assertEqual(days(m, date(2026, 1, 1), date(2026, 4, 30)),
                         [date(2026, 1, 31), date(2026, 2, 28),
                          date(2026, 3, 31), date(2026, 4, 30)])

    def test_nth_weekday(self):
        first_friday = rule(freq="monthly", month_mode="nth", nth=1, nth_weekday=4)
        self.assertEqual(days(first_friday, date(2026, 10, 1), date(2026, 11, 30)),
                         [date(2026, 10, 2), date(2026, 11, 6)])
        last_friday = rule(freq="monthly", month_mode="nth", nth=-1, nth_weekday=4)
        self.assertEqual(days(last_friday, date(2026, 10, 1), date(2026, 11, 30)),
                         [date(2026, 10, 30), date(2026, 11, 27)])

    def test_every_other_month(self):
        m = rule(freq="monthly", month_day=10, interval_n=2, start_on=date(2026, 1, 5))
        self.assertEqual(days(m, date(2026, 1, 1), date(2026, 6, 30)),
                         [date(2026, 1, 10), date(2026, 3, 10), date(2026, 5, 10)])

    def test_start_and_end_are_respected(self):
        m = rule(start_on=date(2026, 9, 10), end_on=date(2026, 9, 25))
        self.assertEqual(days(m, SEP1, SEP30), [date(2026, 9, 15), date(2026, 9, 22)])


class TestHolidayRules(unittest.TestCase):
    MONDAY = "0"

    def test_skip(self):
        m = rule(weekdays=self.MONDAY, holiday_rule="skip")
        self.assertNotIn(date(2026, 9, 21), days(m, SEP1, SEP30, off))
        self.assertIn(date(2026, 9, 14), days(m, SEP1, SEP30, off))

    def test_next_jumps_over_consecutive_holidays(self):
        m = rule(weekdays=self.MONDAY, holiday_rule="next")
        got = days(m, SEP1, SEP30, off)
        self.assertIn(date(2026, 9, 24), got)
        self.assertNotIn(date(2026, 9, 21), got)
        pairs = dict((d, p) for p, d in meetings.scheduled(m, SEP1, SEP30, off))
        self.assertEqual(pairs[date(2026, 9, 24)], date(2026, 9, 21))

    def test_prev(self):
        m = rule(weekdays=self.MONDAY, holiday_rule="prev")
        self.assertIn(date(2026, 9, 18), days(m, SEP1, SEP30, off))

    def test_keep(self):
        m = rule(weekdays=self.MONDAY, holiday_rule="keep")
        self.assertIn(date(2026, 9, 21), days(m, SEP1, SEP30, off))

    def test_shifted_meeting_that_lands_on_another_is_counted_once(self):
        m = rule(weekdays="0,1", holiday_rule="next")
        is_off = lambda d: d == date(2026, 10, 12)   # noqa: E731
        got = days(m, date(2026, 10, 12), date(2026, 10, 13), is_off)
        self.assertEqual(got, [date(2026, 10, 13)])

    def test_a_meeting_shifted_into_the_range_is_shown(self):
        m = rule(weekdays=self.MONDAY, holiday_rule="next")
        self.assertEqual(days(m, date(2026, 9, 24), date(2026, 9, 27), off),
                         [date(2026, 9, 24)])


class TestOccurrences(unittest.TestCase):
    def ex(self, action, moved_to=None, note=""):
        return {"action": action, "moved_to": moved_to, "note": note}

    def test_cancelled_is_kept_but_marked(self):
        got = meetings.occurrences(rule(), SEP1, SEP30, never_off,
                                   {date(2026, 9, 8): self.ex("cancel", note="出張")})
        item = next(o for o in got if o["date"] == "2026-09-08")
        self.assertEqual(item["status"], "cancelled")
        self.assertEqual(item["note"], "出張")

    def test_moved_shows_on_the_new_day_only(self):
        got = meetings.occurrences(rule(), SEP1, SEP30, never_off,
                                   {date(2026, 9, 8): self.ex("move", date(2026, 9, 9))})
        dates = [o["date"] for o in got]
        self.assertNotIn("2026-09-08", dates)
        moved = next(o for o in got if o["date"] == "2026-09-09")
        self.assertEqual((moved["status"], moved["planned"]), ("moved", "2026-09-08"))

    def test_moved_in_from_outside_the_range(self):
        got = meetings.occurrences(rule(), date(2026, 9, 10), SEP30, never_off,
                                   {date(2026, 9, 8): self.ex("move", date(2026, 9, 11))})
        self.assertIn("2026-09-11", [o["date"] for o in got])

    def test_moved_out_of_the_range_disappears(self):
        got = meetings.occurrences(rule(), SEP1, date(2026, 9, 10), never_off,
                                   {date(2026, 9, 8): self.ex("move", date(2026, 9, 20))})
        self.assertEqual([o["date"] for o in got], ["2026-09-01"])

    def test_stale_exception_after_the_rule_changed_is_ignored(self):
        # 火曜の回を動かしたあと、決まりを水曜に変えた
        wednesday = rule(weekdays="2")
        got = meetings.occurrences(wednesday, SEP1, SEP30, never_off,
                                   {date(2026, 9, 8): self.ex("move", date(2026, 9, 11))})
        self.assertNotIn("2026-09-11", [o["date"] for o in got])

    def test_shift_is_reported(self):
        m = rule(weekdays="0", holiday_rule="next")
        got = meetings.occurrences(m, SEP1, SEP30, off, {})
        item = next(o for o in got if o["date"] == "2026-09-24")
        self.assertEqual(item["shifted_from"], "2026-09-21")


class TestDescribe(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(meetings.describe(rule(time_text="10:00")), "毎週 火 10:00")
        self.assertEqual(meetings.describe(rule(interval_n=2, weekdays="1,3")), "隔週 火・木")
        self.assertEqual(meetings.describe(rule(freq="monthly", month_day=1)), "毎月 1日")
        self.assertEqual(meetings.describe(
            rule(freq="monthly", month_mode="nth", nth=1, nth_weekday=4)), "毎月 第1金曜")
        self.assertEqual(meetings.describe(
            rule(freq="monthly", month_mode="nth", nth=-1, nth_weekday=4, interval_n=3)),
            "3か月ごと 最終金曜")


class TestMeetingApi(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.project = self.make_project("定例PJ")
        self.url = "/api/projects/{}/meetings".format(self.project["id"])

    def create(self, **kwargs):
        body = {"title": "週次定例", "freq": "weekly", "weekdays": [1],
                "start_on": "2026-09-01", "holiday_rule": "skip"}
        body.update(kwargs)
        status, data = self.admin.post(self.url, body)
        self.assertEqual(status, 201, data)
        return data["meeting"]

    def listing(self, client=None, **query):
        query.setdefault("project_ids", self.project["id"])
        query.setdefault("from", "2026-09-01")
        query.setdefault("to", "2026-09-30")
        qs = "&".join("{}={}".format(k, v) for k, v in query.items())
        status, data = (client or self.admin).get("/api/meetings?" + qs)
        self.assertEqual(status, 200, data)
        return data["meetings"]

    def member(self, role):
        user, email = self.make_user("定例の{}".format(role))
        self.admin.put("/api/projects/{}/members".format(self.project["id"]), {
            "members": [{"principal_type": "user", "principal_id": user["id"],
                         "role": role}]})
        return self.client_for(email)

    def test_create_and_list_with_occurrences(self):
        made = self.create(time_text="10:00", holiday_rule="keep")
        self.assertEqual(made["summary"], "毎週 火 10:00")
        found = self.listing()
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["can_edit"])
        self.assertEqual([o["date"] for o in found[0]["occurrences"]],
                         ["2026-09-01", "2026-09-08", "2026-09-15", "2026-09-22",
                          "2026-09-29"])

    def test_real_holidays_are_used(self):
        # 月曜の定例。9/21（敬老の日）は祝日の暦から判定されて休みになる
        self.create(weekdays=[0])
        dates = [o["date"] for o in self.listing()[0]["occurrences"]]
        self.assertIn("2026-09-14", dates)
        self.assertNotIn("2026-09-21", dates)

    def test_national_holiday_between_holidays_counts(self):
        # 火曜の定例。9/22 は敬老の日と秋分の日に挟まれた国民の休日
        self.create(weekdays=[1], holiday_rule="next")
        dates = [o["date"] for o in self.listing()[0]["occurrences"]]
        self.assertNotIn("2026-09-22", dates)
        self.assertIn("2026-09-24", dates)

    def test_viewer_sees_but_cannot_change(self):
        made = self.create()
        viewer = self.member("viewer")
        found = self.listing(viewer)
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0]["can_edit"])
        self.assertEqual(viewer.post(self.url, {"title": "x", "weekdays": [1]})[0], 403)
        self.assertEqual(viewer.patch("/api/meetings/{}".format(made["id"]),
                                      {"title": "x"})[0], 403)
        self.assertEqual(viewer.delete("/api/meetings/{}".format(made["id"]))[0], 403)
        self.assertEqual(viewer.put("/api/meetings/{}/exceptions/2026-09-08".format(made["id"]),
                                    {"action": "cancel"})[0], 403)

    def test_editor_can_change(self):
        made = self.create()
        editor = self.member("editor")
        status, data = editor.patch("/api/meetings/{}".format(made["id"]), {"title": "改名"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["meeting"]["title"], "改名")

    def test_outsider_sees_nothing(self):
        made = self.create()
        _, email = self.make_user("部外の人")
        outsider = self.client_for(email)
        self.assertEqual(self.listing(outsider), [])
        self.assertEqual(self.listing(outsider, project_ids=""), [])
        self.assertEqual(outsider.patch("/api/meetings/{}".format(made["id"]),
                                        {"title": "x"})[0], 403)

    def test_listing_without_projects_covers_visible_ones(self):
        self.create()
        titles = [m["title"] for m in self.listing(project_ids="")]
        self.assertIn("週次定例", titles)

    def test_validation(self):
        bad = [
            {"title": ""},
            {"weekdays": []},
            {"freq": "daily"},
            {"freq": "monthly", "month_mode": "day"},
            {"freq": "monthly", "month_mode": "nth", "nth": 1},
            {"holiday_rule": "maybe"},
            {"start_on": "2026-09-10", "end_on": "2026-09-01"},
            {"start_on": "2026-02-30"},
        ]
        for patch in bad:
            body = {"title": "定例", "freq": "weekly", "weekdays": [1]}
            body.update(patch)
            status, data = self.admin.post(self.url, body)
            self.assertEqual(status, 400, (patch, data))

    def test_patch_keeps_unspecified_fields(self):
        made = self.create(time_text="15:00", end_on="2026-12-31")
        status, data = self.admin.patch("/api/meetings/{}".format(made["id"]), {"title": "新"})
        self.assertEqual(status, 200, data)
        self.assertEqual((data["meeting"]["time_text"], data["meeting"]["end_on"],
                          data["meeting"]["weekdays"]), ("15:00", "2026-12-31", [1]))
        # null を送れば終了日を外せる
        status, data = self.admin.patch("/api/meetings/{}".format(made["id"]), {"end_on": None})
        self.assertIsNone(data["meeting"]["end_on"])

    def test_cancel_move_and_restore(self):
        made = self.create()
        base = "/api/meetings/{}/exceptions/".format(made["id"])
        self.assertEqual(self.admin.put(base + "2026-09-08", {"action": "cancel",
                                                              "note": "出張"})[0], 200)
        self.assertEqual(self.admin.put(base + "2026-09-15", {"action": "move",
                                                              "moved_to": "2026-09-17"})[0], 200)
        occ = {o["date"]: o for o in self.listing()[0]["occurrences"]}
        self.assertEqual(occ["2026-09-08"]["status"], "cancelled")
        self.assertNotIn("2026-09-15", occ)
        self.assertEqual(occ["2026-09-17"]["status"], "moved")
        # 取り消すと決まりどおりに戻る
        self.assertEqual(self.admin.delete(base + "2026-09-15")[0], 200)
        occ = {o["date"]: o for o in self.listing()[0]["occurrences"]}
        self.assertEqual(occ["2026-09-15"]["status"], "normal")
        self.assertNotIn("2026-09-17", occ)

    def test_exception_checks(self):
        made = self.create()
        base = "/api/meetings/{}/exceptions/".format(made["id"])
        # 開催日ではない日
        self.assertEqual(self.admin.put(base + "2026-09-09", {"action": "cancel"})[0], 400)
        self.assertEqual(self.admin.put(base + "2026-09-08", {"action": "later"})[0], 400)
        self.assertEqual(self.admin.put(base + "2026-09-08", {"action": "move"})[0], 400)
        self.assertEqual(self.admin.put(base + "2026-09-08", {
            "action": "move", "moved_to": "2026-09-08"})[0], 400)
        self.assertEqual(self.admin.put(base + "2026-02-30", {"action": "cancel"})[0], 400)
        self.assertEqual(self.admin.put(base + "2026-09-08", {
            "action": "move", "moved_to": "2026-09-31"})[0], 400)

    def test_range_checks(self):
        self.assertEqual(self.admin.get(
            "/api/meetings?from=2026-09-30&to=2026-09-01")[0], 400)
        self.assertEqual(self.admin.get(
            "/api/meetings?from=2020-01-01&to=2026-12-31")[0], 400)

    def test_deleting_the_project_removes_meetings(self):
        made = self.create()
        self.admin.put("/api/meetings/{}/exceptions/2026-09-08".format(made["id"]),
                       {"action": "cancel"})
        self.assertEqual(self.admin.delete("/api/projects/{}".format(self.project["id"]))[0], 200)
        self.assertIsNone(db.query_one("SELECT id FROM meetings WHERE id=%s", (made["id"],)))
        self.assertIsNone(db.query_one(
            "SELECT meeting_id FROM meeting_exceptions WHERE meeting_id=%s", (made["id"],)))

    def test_preview(self):
        status, data = self.admin.post("/api/meetings/preview", {
            "freq": "monthly", "month_mode": "nth", "nth": 1, "nth_weekday": 4,
            "start_on": "2030-01-01"})
        self.assertEqual(status, 200, data)
        self.assertEqual([n["date"] for n in data["next"]][:2], ["2030-01-04", "2030-02-01"])
        status, _ = self.admin.post("/api/meetings/preview", {"freq": "weekly", "weekdays": []})
        self.assertEqual(status, 400)

    def test_place_under_a_task(self):
        phase = self.make_task(self.project["id"], title="開発フェーズ")
        made = self.create(parent_id=phase["id"])
        self.assertEqual(made["parent_id"], phase["id"])
        self.assertEqual(self.listing()[0]["parent_id"], phase["id"])
        # 別の項目だけ変えても、置き場所はそのまま
        status, data = self.admin.patch("/api/meetings/{}".format(made["id"]), {"title": "新"})
        self.assertEqual(data["meeting"]["parent_id"], phase["id"])
        # null で先頭に戻せる
        status, data = self.admin.patch("/api/meetings/{}".format(made["id"]), {"parent_id": None})
        self.assertIsNone(data["meeting"]["parent_id"])

    def test_parent_must_be_in_the_same_project(self):
        other = self.make_project("別PJ")
        stranger = self.make_task(other["id"], title="よそのタスク")
        body = {"title": "定例", "weekdays": [1], "parent_id": stranger["id"]}
        self.assertEqual(self.admin.post(self.url, body)[0], 400)
        made = self.create()
        self.assertEqual(self.admin.patch("/api/meetings/{}".format(made["id"]),
                                          {"parent_id": stranger["id"]})[0], 400)
        self.assertEqual(self.admin.patch("/api/meetings/{}".format(made["id"]),
                                          {"parent_id": 99999999})[0], 400)

    def test_deleting_the_parent_moves_it_to_the_top_and_restore_puts_it_back(self):
        phase = self.make_task(self.project["id"], title="消えるフェーズ")
        child = self.make_task(self.project["id"], title="孫", parent_id=phase["id"])
        on_phase = self.create(title="フェーズ定例", parent_id=phase["id"])
        on_child = self.create(title="孫の定例", parent_id=child["id"])
        self.assertEqual(self.admin.delete("/api/tasks/{}".format(phase["id"]))[0], 200)
        # 定例そのものは消えず、置き場所だけ外れる
        parents = {m["title"]: m["parent_id"] for m in self.listing()}
        self.assertEqual(parents, {"フェーズ定例": None, "孫の定例": None})
        # 消したあとで置き直したものは、戻しても動かさない
        self.admin.patch("/api/meetings/{}".format(on_child["id"]), {"parent_id": None})
        keep = self.make_task(self.project["id"], title="置き直し先")
        self.admin.patch("/api/meetings/{}".format(on_child["id"]), {"parent_id": keep["id"]})
        entry = next(t for t in self.admin.get("/api/trash")[1]["items"]
                     if t["kind"] == "task" and t["item_id"] == phase["id"])
        status, data = self.admin.post("/api/trash/{}/restore".format(entry["id"]), {})
        self.assertEqual(status, 200, data)
        parents = {m["id"]: m["parent_id"] for m in self.listing()}
        self.assertEqual(parents[on_phase["id"]], phase["id"])
        self.assertEqual(parents[on_child["id"]], keep["id"])

    def test_restore_when_the_meeting_was_deleted_meanwhile(self):
        phase = self.make_task(self.project["id"], title="フェーズ")
        made = self.create(parent_id=phase["id"])
        self.admin.delete("/api/tasks/{}".format(phase["id"]))
        self.admin.delete("/api/meetings/{}".format(made["id"]))
        entry = next(t for t in self.admin.get("/api/trash")[1]["items"]
                     if t["kind"] == "task" and t["item_id"] == phase["id"])
        status, data = self.admin.post("/api/trash/{}/restore".format(entry["id"]), {})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.admin.get("/api/tasks/{}".format(phase["id"]))[0], 200)
        # 定例の控えは件数に入れない（戻せなかった扱いにもしない）
        self.assertEqual(data.get("skipped", 0), 0, data)

    def test_delete_meeting(self):
        made = self.create()
        self.assertEqual(self.admin.delete("/api/meetings/{}".format(made["id"]))[0], 200)
        self.assertEqual(self.listing(), [])
        self.assertEqual(self.admin.delete("/api/meetings/{}".format(made["id"]))[0], 404)


if __name__ == "__main__":
    unittest.main()
