"""チケット（受付窓口に届く依頼・問い合わせ・障害）のテスト。

プロジェクト管理とは別建てになっていること、タスクや課題へ渡せること、
渡したあとも互いに残ること、消せる人が限られていることを見る。
"""
import datetime
import os
import sys
import unittest
import uuid
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_api import ADMIN, ApiTestCase, Client  # noqa: E402

ADMIN_EMAIL = ADMIN[0]
ADMIN_NAME = "管理者"
from app import db, tickets  # noqa: E402


class TicketTestCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        # DB はクラス単位でしか初期化されないので、窓口名が衝突しないようにする
        self.queue = self.make_queue("テスト窓口")

    def make_queue(self, name, **kwargs):
        payload = {"name": "{} {}".format(name, uuid.uuid4().hex[:6])}
        payload.update(kwargs)
        status, data = self.admin.post("/api/ticket-queues", payload)
        self.assertEqual(status, 201, data)
        return data["queue"]

    def make_ticket(self, title="困っています", client=None, **kwargs):
        payload = {"queue_id": self.queue["id"], "title": title}
        payload.update(kwargs)
        status, data = (client or self.admin).post("/api/tickets", payload)
        self.assertEqual(status, 201, data)
        return data["ticket"]


class TestQueues(TicketTestCase):
    def test_a_default_queue_exists_out_of_the_box(self):
        names = [q["name"] for q in self.admin.get("/api/ticket-queues")[1]["queues"]]
        self.assertIn(tickets.DEFAULT_QUEUE[0], names)

    def test_only_an_admin_can_add_a_queue(self):
        _user, email = self.make_user()
        status, _ = self.client_for(email).post("/api/ticket-queues", {"name": "勝手に窓口"})
        self.assertEqual(status, 403)

    def test_everyone_can_see_the_queues(self):
        _user, email = self.make_user()
        status, data = self.client_for(email).get("/api/ticket-queues")
        self.assertEqual(status, 200)
        self.assertTrue(data["queues"])

    def test_the_same_name_twice_is_rejected(self):
        status, _ = self.admin.post("/api/ticket-queues", {"name": self.queue["name"]})
        self.assertEqual(status, 400)

    def test_a_queue_with_tickets_cannot_be_deleted(self):
        self.make_ticket()
        status, data = self.admin.delete("/api/ticket-queues/{}".format(self.queue["id"]))
        self.assertEqual(status, 400)
        self.assertIn("残って", data["error"])

    def test_an_empty_queue_can_be_deleted(self):
        queue = self.make_queue("すぐ消す窓口")
        status, _ = self.admin.delete("/api/ticket-queues/{}".format(queue["id"]))
        self.assertEqual(status, 200)

    def test_a_closed_queue_stops_taking_new_tickets(self):
        self.admin.patch("/api/ticket-queues/{}".format(self.queue["id"]),
                         {"is_active": False})
        status, data = self.admin.post("/api/tickets", {
            "queue_id": self.queue["id"], "title": "受け付けてほしい"})
        self.assertEqual(status, 400)
        self.assertIn("受付", data["error"])

    def test_the_count_of_unfinished_tickets_is_reported(self):
        self.make_ticket()
        done = self.make_ticket("終わったもの")
        self.admin.patch("/api/tickets/{}".format(done["id"]), {"status": "done"})
        queue = next(q for q in self.admin.get("/api/ticket-queues")[1]["queues"]
                     if q["id"] == self.queue["id"])
        self.assertEqual(queue["ticket_count"], 2)
        self.assertEqual(queue["open_count"], 1)


class TestProjectQueues(TicketTestCase):
    """プロジェクト専用の窓口。行き先が決まるだけで、見える範囲は変わらない。"""

    def setUp(self):
        super().setUp()
        self.project = self.make_project("窓口つきPJ")

    def test_a_queue_can_be_tied_to_a_project(self):
        queue = self.make_queue("PJ窓口", project_id=self.project["id"])
        self.assertEqual(queue["project_id"], self.project["id"])
        self.assertEqual(queue["project_name"], "窓口つきPJ")

    def test_a_queue_without_a_project_stays_loose(self):
        self.assertIsNone(self.queue["project_id"])

    def test_the_link_can_be_removed_later(self):
        queue = self.make_queue("あとで外す窓口", project_id=self.project["id"])
        _status, data = self.admin.patch("/api/ticket-queues/{}".format(queue["id"]),
                                         {"project_id": None})
        self.assertIsNone(data["queue"]["project_id"])

    def test_an_unknown_project_is_rejected(self):
        status, _ = self.admin.post("/api/ticket-queues",
                                    {"name": "でたらめ窓口", "project_id": 999999})
        self.assertIn(status, (400, 404))

    def test_a_task_goes_to_that_project_without_being_told(self):
        queue = self.make_queue("行き先つき窓口", project_id=self.project["id"])
        _status, made = self.admin.post("/api/tickets",
                                        {"queue_id": queue["id"], "title": "やってほしい"})
        status, data = self.admin.post(
            "/api/tickets/{}/task".format(made["ticket"]["id"]), {})
        self.assertEqual(status, 201, data)
        self.assertEqual(data["task"]["project_name"], "窓口つきPJ")

    def test_an_issue_goes_there_too(self):
        queue = self.make_queue("論点窓口", project_id=self.project["id"])
        _status, made = self.admin.post("/api/tickets",
                                        {"queue_id": queue["id"], "title": "決めたい"})
        status, data = self.admin.post(
            "/api/tickets/{}/issue".format(made["ticket"]["id"]), {})
        self.assertEqual(status, 201, data)
        self.assertEqual(data["issue"]["project_name"], "窓口つきPJ")

    def test_an_explicit_project_still_wins(self):
        other = self.make_project("別のPJ")
        queue = self.make_queue("上書き窓口", project_id=self.project["id"])
        _status, made = self.admin.post("/api/tickets",
                                        {"queue_id": queue["id"], "title": "こっちに出す"})
        _status, data = self.admin.post(
            "/api/tickets/{}/task".format(made["ticket"]["id"]),
            {"project_id": other["id"]})
        self.assertEqual(data["task"]["project_name"], "別のPJ")

    def test_a_loose_queue_still_needs_a_project(self):
        ticket = self.make_ticket()
        status, _ = self.admin.post("/api/tickets/{}/task".format(ticket["id"]), {})
        self.assertEqual(status, 400)

    def test_the_ticket_carries_which_project_its_queue_serves(self):
        queue = self.make_queue("持ち回り窓口", project_id=self.project["id"])
        _status, made = self.admin.post("/api/tickets",
                                        {"queue_id": queue["id"], "title": "確認"})
        self.assertEqual(made["ticket"]["queue_project_name"], "窓口つきPJ")

    def test_tickets_can_be_listed_by_project(self):
        queue = self.make_queue("絞り込み窓口", project_id=self.project["id"])
        self.admin.post("/api/tickets", {"queue_id": queue["id"], "title": "PJ宛て"})
        self.make_ticket("どこでもない宛て")
        titles = [t["title"] for t in self.admin.get(
            "/api/tickets?project_id={}".format(self.project["id"]))[1]["tickets"]]
        self.assertEqual(titles, ["PJ宛て"])

    def test_someone_outside_the_project_can_still_read_it(self):
        queue = self.make_queue("外から見える窓口", project_id=self.project["id"])
        _status, made = self.admin.post("/api/tickets",
                                        {"queue_id": queue["id"], "title": "誰でも読める"})
        _user, email = self.make_user()
        status, _ = self.client_for(email).get(
            "/api/tickets/{}".format(made["ticket"]["id"]))
        self.assertEqual(status, 200)

    def test_but_they_cannot_push_work_into_that_project(self):
        queue = self.make_queue("権限のいる窓口", project_id=self.project["id"])
        _status, made = self.admin.post("/api/tickets",
                                        {"queue_id": queue["id"], "title": "勝手に積めない"})
        _user, email = self.make_user()
        status, _ = self.client_for(email).post(
            "/api/tickets/{}/task".format(made["ticket"]["id"]), {})
        self.assertIn(status, (403, 404))


class TestRaisingTickets(TicketTestCase):
    def test_anyone_logged_in_can_raise_one(self):
        _user, email = self.make_user()
        ticket = self.make_ticket("メンバーからの依頼", client=self.client_for(email))
        self.assertEqual(ticket["status"], "new")
        self.assertEqual(ticket["requester_name"], "テスト太郎")

    def test_everyone_can_read_it(self):
        ticket = self.make_ticket()
        _user, email = self.make_user()
        status, data = self.client_for(email).get("/api/tickets/{}".format(ticket["id"]))
        self.assertEqual(status, 200)
        self.assertEqual(data["ticket"]["id"], ticket["id"])

    def test_a_stranger_without_a_login_cannot(self):
        status, _ = Client(self.base).get("/api/tickets")
        self.assertEqual(status, 401)

    def test_the_title_is_required(self):
        status, _ = self.admin.post("/api/tickets",
                                    {"queue_id": self.queue["id"], "title": "  "})
        self.assertEqual(status, 400)

    def test_an_unknown_kind_falls_back_to_a_request(self):
        ticket = self.make_ticket(kind="なんだこれ")
        self.assertEqual(ticket["kind"], "request")

    def test_an_incident_keeps_the_time_it_happened(self):
        ticket = self.make_ticket("止まりました", kind="incident",
                                  occurred_at="2026-09-20T09:30")
        self.assertTrue(str(ticket["occurred_at"]).startswith("2026-09-20 09:30"))

    def test_it_records_who_the_request_came_from(self):
        ticket = self.make_ticket(on_behalf_of="営業部 田中")
        self.assertEqual(ticket["on_behalf_of"], "営業部 田中")


class TestTicketProgress(TicketTestCase):
    def test_finishing_it_stamps_the_time(self):
        ticket = self.make_ticket()
        _status, data = self.admin.patch("/api/tickets/{}".format(ticket["id"]),
                                         {"status": "done"})
        self.assertIsNotNone(data["ticket"]["resolved_at"])

    def test_reopening_it_clears_the_time(self):
        ticket = self.make_ticket()
        self.admin.patch("/api/tickets/{}".format(ticket["id"]), {"status": "done"})
        _status, data = self.admin.patch("/api/tickets/{}".format(ticket["id"]),
                                         {"status": "doing"})
        self.assertIsNone(data["ticket"]["resolved_at"])

    def test_changes_are_written_into_the_history(self):
        ticket = self.make_ticket()
        self.admin.patch("/api/tickets/{}".format(ticket["id"]), {"status": "doing"})
        _status, data = self.admin.get("/api/tickets/{}".format(ticket["id"]))
        notes = [c["body"] for c in data["comments"] if c["kind"] == "system"]
        self.assertTrue(any("状態" in n for n in notes), notes)

    def test_an_unknown_assignee_is_rejected(self):
        ticket = self.make_ticket()
        status, _ = self.admin.patch("/api/tickets/{}".format(ticket["id"]),
                                     {"assignee_id": 999999})
        self.assertEqual(status, 400)

    def test_the_assignee_is_told(self):
        user, _email = self.make_user(name="受ける人")
        ticket = self.make_ticket()
        self.admin.patch("/api/tickets/{}".format(ticket["id"]),
                         {"assignee_id": user["id"]})
        rows = db.query("SELECT type, title FROM notifications WHERE user_id=%s",
                        (user["id"],))
        self.assertTrue(any(r["type"] == "assigned" for r in rows), list(rows))

    def test_an_empty_patch_is_rejected(self):
        ticket = self.make_ticket()
        status, _ = self.admin.patch("/api/tickets/{}".format(ticket["id"]), {})
        self.assertEqual(status, 400)


class TestHandingOver(TicketTestCase):
    """チケットからタスク・課題へ渡す。"""

    def setUp(self):
        super().setUp()
        self.project = self.make_project("受け皿PJ")

    def test_a_task_is_created_and_linked(self):
        ticket = self.make_ticket("プリンタを直してほしい", due_date="2026-10-01",
                                  on_behalf_of="総務 佐藤")
        status, data = self.admin.post("/api/tickets/{}/task".format(ticket["id"]),
                                       {"project_id": self.project["id"]})
        self.assertEqual(status, 201, data)
        self.assertEqual(data["task"]["title"], "プリンタを直してほしい")
        self.assertEqual(data["task"]["due_date"], "2026-10-01")
        self.assertIn("チケット #{}".format(ticket["id"]), data["task"]["description"])
        self.assertIn("総務 佐藤", data["task"]["description"])
        self.assertEqual(data["ticket"]["task_count"], 1)

    def test_handing_over_moves_it_off_the_waiting_pile(self):
        ticket = self.make_ticket()
        self.assertEqual(ticket["status"], "new")
        _status, data = self.admin.post("/api/tickets/{}/task".format(ticket["id"]),
                                        {"project_id": self.project["id"]})
        self.assertEqual(data["ticket"]["status"], "doing")

    def test_it_does_not_push_back_a_ticket_already_in_hand(self):
        ticket = self.make_ticket()
        self.admin.patch("/api/tickets/{}".format(ticket["id"]), {"status": "pending"})
        _status, data = self.admin.post("/api/tickets/{}/task".format(ticket["id"]),
                                        {"project_id": self.project["id"]})
        self.assertEqual(data["ticket"]["status"], "pending")

    def test_you_cannot_push_a_task_into_a_project_you_cannot_edit(self):
        ticket = self.make_ticket()
        _user, email = self.make_user()
        status, _ = self.client_for(email).post(
            "/api/tickets/{}/task".format(ticket["id"]),
            {"project_id": self.project["id"]})
        self.assertIn(status, (403, 404))

    def test_the_project_is_required(self):
        ticket = self.make_ticket()
        status, _ = self.admin.post("/api/tickets/{}/task".format(ticket["id"]), {})
        self.assertEqual(status, 400)

    def test_an_issue_is_created_and_linked(self):
        ticket = self.make_ticket("権限の考え方を決めたい")
        status, data = self.admin.post("/api/tickets/{}/issue".format(ticket["id"]),
                                       {"project_id": self.project["id"]})
        self.assertEqual(status, 201, data)
        self.assertEqual(data["issue"]["seq"], 1)
        self.assertEqual(data["ticket"]["issue_count"], 1)

    def test_an_existing_task_can_be_linked(self):
        ticket = self.make_ticket()
        task = self.make_task(self.project["id"], title="すでにやっている作業")
        status, data = self.admin.put("/api/tickets/{}/tasks".format(ticket["id"]),
                                      {"task_ids": [task["id"]]})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["task_ids"], [task["id"]])

    def test_linking_ignores_tasks_you_cannot_see(self):
        ticket = self.make_ticket()
        task = self.make_task(self.project["id"], title="見えないところの作業")
        _user, email = self.make_user()
        other = self.client_for(email)
        _status, data = other.put("/api/tickets/{}/tasks".format(ticket["id"]),
                                  {"task_ids": [task["id"]]})
        self.assertEqual(data["task_ids"], [])

    def test_deleting_the_ticket_leaves_the_task_alone(self):
        ticket = self.make_ticket()
        _status, made = self.admin.post("/api/tickets/{}/task".format(ticket["id"]),
                                        {"project_id": self.project["id"]})
        task_id = made["task"]["id"]
        self.admin.delete("/api/tickets/{}".format(ticket["id"]))
        status, _ = self.admin.get("/api/tasks/{}".format(task_id))
        self.assertEqual(status, 200)

    def test_deleting_the_task_only_drops_the_link(self):
        ticket = self.make_ticket()
        _status, made = self.admin.post("/api/tickets/{}/task".format(ticket["id"]),
                                        {"project_id": self.project["id"]})
        self.admin.delete("/api/tasks/{}".format(made["task"]["id"]))
        status, data = self.admin.get("/api/tickets/{}".format(ticket["id"]))
        self.assertEqual(status, 200)
        self.assertEqual(data["tasks"], [])


class TestTicketTalk(TicketTestCase):
    def test_a_comment_can_be_added_by_anyone(self):
        ticket = self.make_ticket()
        _user, email = self.make_user()
        status, data = self.client_for(email).post(
            "/api/tickets/{}/comments".format(ticket["id"]), {"body": "確認します"})
        self.assertEqual(status, 201, data)

    def test_a_mention_reaches_the_person(self):
        user, _email = self.make_user(name="呼ばれる人")
        ticket = self.make_ticket()
        _status, data = self.admin.post(
            "/api/tickets/{}/comments".format(ticket["id"]),
            {"body": "@呼ばれる人 これお願いできますか"})
        self.assertIn("呼ばれる人", data["mentioned"])
        rows = db.query("SELECT type FROM notifications WHERE user_id=%s", (user["id"],))
        self.assertTrue(any(r["type"] == "mention" for r in rows))

    def test_you_can_only_delete_your_own_comment(self):
        ticket = self.make_ticket()
        _user, email = self.make_user()
        other = self.client_for(email)
        _status, made = other.post("/api/tickets/{}/comments".format(ticket["id"]),
                                   {"body": "わたしの書き込み"})
        _user2, email2 = self.make_user(name="別の人")
        status, _ = self.client_for(email2).delete(
            "/api/comments/{}".format(made["comment"]["id"]))
        self.assertEqual(status, 403)

    def test_an_admin_can_delete_any_comment(self):
        ticket = self.make_ticket()
        _user, email = self.make_user()
        _status, made = self.client_for(email).post(
            "/api/tickets/{}/comments".format(ticket["id"]), {"body": "消される書き込み"})
        status, _ = self.admin.delete("/api/comments/{}".format(made["comment"]["id"]))
        self.assertEqual(status, 200)

    def test_a_link_can_be_attached(self):
        ticket = self.make_ticket()
        status, data = self.admin.post("/api/tickets/{}/attachments".format(ticket["id"]),
                                       {"url": "https://example.com/manual", "name": "手順"})
        self.assertEqual(status, 201, data)
        _status, detail = self.admin.get("/api/tickets/{}".format(ticket["id"]))
        self.assertEqual(len(detail["attachments"]), 1)


class TestWhoCanDelete(TicketTestCase):
    def test_the_person_who_raised_it_can(self):
        _user, email = self.make_user()
        client = self.client_for(email)
        ticket = self.make_ticket(client=client)
        status, _ = client.delete("/api/tickets/{}".format(ticket["id"]))
        self.assertEqual(status, 200)

    def test_someone_else_cannot(self):
        _user, email = self.make_user()
        ticket = self.make_ticket(client=self.client_for(email))
        _user2, email2 = self.make_user(name="関係ない人")
        status, _ = self.client_for(email2).delete("/api/tickets/{}".format(ticket["id"]))
        self.assertEqual(status, 403)

    def test_an_admin_can(self):
        _user, email = self.make_user()
        ticket = self.make_ticket(client=self.client_for(email))
        status, _ = self.admin.delete("/api/tickets/{}".format(ticket["id"]))
        self.assertEqual(status, 200)


class TestTicketList(TicketTestCase):
    def test_unfinished_only_by_default(self):
        self.make_ticket("残っているもの")
        done = self.make_ticket("終わったもの")
        self.admin.patch("/api/tickets/{}".format(done["id"]), {"status": "done"})
        titles = [t["title"] for t in self.admin.get("/api/tickets")[1]["tickets"]]
        self.assertIn("残っているもの", titles)
        self.assertNotIn("終わったもの", titles)

    def test_everything_can_be_listed(self):
        done = self.make_ticket("終わったもの")
        self.admin.patch("/api/tickets/{}".format(done["id"]), {"status": "done"})
        titles = [t["title"] for t in
                  self.admin.get("/api/tickets?status=all")[1]["tickets"]]
        self.assertIn("終わったもの", titles)

    def test_it_can_be_narrowed_to_what_i_am_handling(self):
        user, email = self.make_user()
        mine = self.make_ticket("自分の担当", assignee_id=user["id"])
        self.make_ticket("他人の担当")
        titles = [t["title"] for t in
                  self.client_for(email).get("/api/tickets?scope=mine")[1]["tickets"]]
        self.assertEqual(titles, [mine["title"]])

    def test_it_can_be_narrowed_to_what_has_no_owner(self):
        user, _email = self.make_user()
        self.make_ticket("担当あり", assignee_id=user["id"])
        self.make_ticket("担当なし")
        # 同じクラスの他のテストが作ったものが残るので、この窓口だけを見る
        titles = [t["title"] for t in self.admin.get(
            "/api/tickets?scope=unassigned&queue_id={}".format(self.queue["id"])
        )[1]["tickets"]]
        self.assertEqual(titles, ["担当なし"])

    def test_it_can_be_narrowed_by_kind(self):
        self.make_ticket("障害です", kind="incident")
        self.make_ticket("依頼です", kind="request")
        titles = [t["title"] for t in self.admin.get(
            "/api/tickets?kind=incident&queue_id={}".format(self.queue["id"])
        )[1]["tickets"]]
        self.assertEqual(titles, ["障害です"])

    def test_the_search_looks_at_the_body_too(self):
        self.make_ticket("件名はふつう", body="キーワードは本文にある")
        titles = [t["title"] for t in
                  self.admin.get("/api/tickets?q=キーワード")[1]["tickets"]]
        self.assertEqual(titles, ["件名はふつう"])

    def test_the_summary_counts_what_needs_attention(self):
        # 集計は全窓口が対象なので、増えぶんで確かめる
        before = self.admin.get("/api/tickets")[1]["summary"]
        user, _email = self.make_user()
        self.make_ticket("受付待ち")
        self.make_ticket("期限切れ", assignee_id=user["id"], due_date="2020-01-01")
        after = self.admin.get("/api/tickets")[1]["summary"]
        self.assertEqual(after["open"] - before["open"], 2)
        self.assertEqual(after["waiting"] - before["waiting"], 2)
        self.assertEqual(after["unassigned"] - before["unassigned"], 1)
        self.assertEqual(after["overdue"] - before["overdue"], 1)


class TestDoneCounts(TicketTestCase):
    """残りだけでなく、片付いたぶんも数える。"""

    def test_finishing_one_moves_it_into_the_done_counts(self):
        before = self.admin.get("/api/tickets")[1]["summary"]
        ticket = self.make_ticket("片付けるもの")
        self.admin.patch("/api/tickets/{}".format(ticket["id"]), {"status": "done"})
        after = self.admin.get("/api/tickets")[1]["summary"]
        self.assertEqual(after["done_total"] - before["done_total"], 1)
        self.assertEqual(after["done_week"] - before["done_week"], 1)
        self.assertEqual(after["done_month"] - before["done_month"], 1)

    def test_a_withdrawn_ticket_is_not_counted_as_done(self):
        before = self.admin.get("/api/tickets")[1]["summary"]
        ticket = self.make_ticket("取り下げるもの")
        self.admin.patch("/api/tickets/{}".format(ticket["id"]), {"status": "canceled"})
        after = self.admin.get("/api/tickets")[1]["summary"]
        self.assertEqual(after["done_total"], before["done_total"])
        self.assertEqual(after["open"], before["open"])

    def test_the_turnaround_is_reported(self):
        ticket = self.make_ticket()
        self.admin.patch("/api/tickets/{}".format(ticket["id"]), {"status": "done"})
        data = self.admin.get("/api/tickets")[1]
        self.assertIsNotNone(data["turnaround_days"])


class TestTicketStats(TicketTestCase):
    def test_a_new_ticket_lands_on_today(self):
        self.make_ticket("今日うけた")
        _status, data = self.admin.get(
            "/api/tickets/stats?unit=day&span=7&queue_id={}".format(self.queue["id"]))
        self.assertEqual(data["unit"], "day")
        self.assertEqual(len(data["buckets"]), 7)
        self.assertEqual(data["buckets"][-1]["created"], 1)
        self.assertEqual(data["totals"]["created"], 1)

    def test_finishing_it_counts_on_the_day_it_closed(self):
        ticket = self.make_ticket()
        self.admin.patch("/api/tickets/{}".format(ticket["id"]), {"status": "done"})
        _status, data = self.admin.get(
            "/api/tickets/stats?unit=day&span=7&queue_id={}".format(self.queue["id"]))
        self.assertEqual(data["buckets"][-1]["resolved"], 1)
        self.assertEqual(data["totals"]["resolved"], 1)

    def test_it_can_be_counted_by_week(self):
        self.make_ticket()
        _status, data = self.admin.get(
            "/api/tickets/stats?unit=week&span=4&queue_id={}".format(self.queue["id"]))
        self.assertEqual(data["unit"], "week")
        self.assertEqual(len(data["buckets"]), 4)
        self.assertEqual(data["buckets"][-1]["created"], 1)

    def test_it_breaks_down_by_queue_and_kind(self):
        self.make_ticket("障害のほう", kind="incident")
        self.make_ticket("依頼のほう", kind="request")
        _status, data = self.admin.get(
            "/api/tickets/stats?queue_id={}".format(self.queue["id"]))
        self.assertEqual([q["queue_id"] for q in data["by_queue"]], [self.queue["id"]])
        kinds = {k["kind"]: k["created"] for k in data["by_kind"]}
        self.assertEqual(kinds, {"incident": 1, "request": 1})

    def test_another_queue_is_left_out(self):
        other = self.make_queue("よその窓口")
        self.admin.post("/api/tickets", {"queue_id": other["id"], "title": "よそのぶん"})
        self.make_ticket("うちのぶん")
        _status, data = self.admin.get(
            "/api/tickets/stats?queue_id={}".format(self.queue["id"]))
        self.assertEqual(data["totals"]["created"], 1)

    def test_the_span_is_capped(self):
        _status, data = self.admin.get("/api/tickets/stats?unit=day&span=9999")
        self.assertLessEqual(len(data["buckets"]), 60)

    def test_login_is_required(self):
        status, _ = Client(self.base).get("/api/tickets/stats")
        self.assertEqual(status, 401)


class TestTicketImport(TicketTestCase):
    def rows_for(self, *rows):
        return {"queue_id": self.queue["id"], "rows": list(rows)}

    def test_a_dry_run_does_not_write(self):
        before = self.admin.get("/api/tickets?status=all")[1]["summary"]["total"]
        payload = self.rows_for({"title": "取り込むもの"})
        payload["dry_run"] = True
        status, data = self.admin.post("/api/tickets/import", payload)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["would_create"], 1)
        after = self.admin.get("/api/tickets?status=all")[1]["summary"]["total"]
        self.assertEqual(after, before)

    def test_it_reads_the_japanese_labels(self):
        status, data = self.admin.post("/api/tickets/import", self.rows_for(
            {"title": "止まった", "kind": "障害", "status": "完了", "priority": "緊急"}))
        self.assertEqual(status, 201, data)
        ticket = self.admin.get("/api/tickets/{}".format(data["ticket_ids"][0]))[1]["ticket"]
        self.assertEqual(ticket["kind"], "incident")
        self.assertEqual(ticket["status"], "done")
        self.assertEqual(ticket["priority"], 3)

    def test_a_finished_row_gets_a_resolved_time(self):
        _status, data = self.admin.post("/api/tickets/import", self.rows_for(
            {"title": "もう終わってる", "status": "完了", "created_at": "2026/9/1"}))
        ticket = self.admin.get("/api/tickets/{}".format(data["ticket_ids"][0]))[1]["ticket"]
        self.assertIsNotNone(ticket["resolved_at"])
        self.assertTrue(str(ticket["created_at"]).startswith("2026-09-01"))

    def test_it_reads_the_dates_people_actually_write(self):
        _status, data = self.admin.post("/api/tickets/import", self.rows_for(
            {"title": "日付いろいろ", "due_date": "2026/10/5",
             "occurred_at": "2026/10/1 9:30"}))
        ticket = self.admin.get("/api/tickets/{}".format(data["ticket_ids"][0]))[1]["ticket"]
        self.assertEqual(str(ticket["due_date"]), "2026-10-05")
        self.assertTrue(str(ticket["occurred_at"]).startswith("2026-10-01 09:30"))

    def test_a_queue_can_be_named_per_row(self):
        other = self.make_queue("行き先指定の窓口")
        _status, data = self.admin.post("/api/tickets/import", self.rows_for(
            {"title": "こっちの窓口へ", "queue": other["name"]}))
        ticket = self.admin.get("/api/tickets/{}".format(data["ticket_ids"][0]))[1]["ticket"]
        self.assertEqual(ticket["queue_id"], other["id"])

    def test_an_unknown_queue_is_reported_and_skipped(self):
        payload = self.rows_for({"title": "行き先不明", "queue": "存在しない窓口"})
        payload["dry_run"] = True
        _status, data = self.admin.post("/api/tickets/import", payload)
        self.assertEqual(data["would_create"], 0)
        self.assertIn("窓口", data["problems"][0]["message"])

    def test_a_missing_title_is_reported_and_skipped(self):
        payload = self.rows_for({"title": "  ", "kind": "依頼"})
        payload["dry_run"] = True
        _status, data = self.admin.post("/api/tickets/import", payload)
        self.assertEqual(data["would_create"], 0)
        self.assertIn("件名", data["problems"][0]["message"])

    def test_an_unknown_person_is_reported_but_the_row_still_goes_in(self):
        payload = self.rows_for({"title": "担当あやしい", "assignee": "いない人"})
        payload["dry_run"] = True
        _status, data = self.admin.post("/api/tickets/import", payload)
        self.assertEqual(data["would_create"], 1)
        self.assertTrue(data["problems"])

    def test_a_known_person_is_matched(self):
        user, _email = self.make_user(name="取り込み担当")
        _status, data = self.admin.post("/api/tickets/import", self.rows_for(
            {"title": "担当つき", "assignee": "取り込み担当"}))
        ticket = self.admin.get("/api/tickets/{}".format(data["ticket_ids"][0]))[1]["ticket"]
        self.assertEqual(ticket["assignee_id"], user["id"])

    def test_an_empty_list_is_rejected(self):
        status, _ = self.admin.post("/api/tickets/import", {"rows": []})
        self.assertEqual(status, 400)

    def test_too_many_rows_are_rejected(self):
        status, _ = self.admin.post("/api/tickets/import", self.rows_for(
            *[{"title": "行 {}".format(i)} for i in range(1001)]))
        self.assertEqual(status, 400)

    def test_login_is_required(self):
        status, _ = Client(self.base).post("/api/tickets/import", {"rows": [{"title": "x"}]})
        self.assertEqual(status, 401)

    def test_the_field_list_is_available(self):
        status, data = self.admin.get("/api/tickets/import-fields")
        self.assertEqual(status, 200)
        values = [f["value"] for f in data["fields"]]
        self.assertIn("title", values)
        self.assertIn("queue", values)


class TestQueueDefaults(TicketTestCase):
    """窓口ごとに、起票したときの種別を決めておける。"""

    def test_the_default_is_a_request(self):
        self.assertEqual(self.queue["default_kind"], "request")

    def test_it_can_be_set_per_queue(self):
        queue = self.make_queue("問い合わせ窓口", default_kind="question")
        self.assertEqual(queue["default_kind"], "question")

    def test_a_new_ticket_picks_it_up(self):
        queue = self.make_queue("障害窓口", default_kind="incident")
        _status, data = self.admin.post("/api/tickets",
                                        {"queue_id": queue["id"], "title": "落ちました"})
        self.assertEqual(data["ticket"]["kind"], "incident")

    def test_an_explicit_kind_still_wins(self):
        queue = self.make_queue("障害寄りの窓口", default_kind="incident")
        _status, data = self.admin.post(
            "/api/tickets", {"queue_id": queue["id"], "title": "これは依頼", "kind": "request"})
        self.assertEqual(data["ticket"]["kind"], "request")

    def test_an_unknown_default_falls_back(self):
        queue = self.make_queue("でたらめ既定", default_kind="なんだこれ")
        self.assertEqual(queue["default_kind"], "request")


class TestQueueCategories(TicketTestCase):
    """分類は窓口ごと。置かない窓口があってもよい。"""

    def setUp(self):
        super().setUp()
        self.shop = self.make_queue("分類つき窓口", categories=[
            {"label": "PC・端末", "color": "#3b6ef5"},
            {"label": "ネットワーク", "color": "#17a673"},
        ])

    def cats(self, queue_id):
        queues = self.admin.get("/api/ticket-queues")[1]["queues"]
        return next(q for q in queues if q["id"] == queue_id)["categories"]

    def test_a_queue_can_have_none(self):
        self.assertEqual(self.queue["categories"], [])

    def test_they_come_back_in_order(self):
        self.assertEqual([c["label"] for c in self.shop["categories"]],
                         ["PC・端末", "ネットワーク"])

    def test_they_can_be_renamed_without_losing_the_link(self):
        first = self.shop["categories"][0]
        _status, made = self.admin.post("/api/tickets", {
            "queue_id": self.shop["id"], "title": "端末の話", "category_id": first["id"]})
        self.admin.patch("/api/ticket-queues/{}".format(self.shop["id"]), {"categories": [
            {"id": first["id"], "label": "パソコン", "color": first["color"]},
            self.shop["categories"][1],
        ]})
        ticket = self.admin.get("/api/tickets/{}".format(made["ticket"]["id"]))[1]["ticket"]
        self.assertEqual(ticket["category_label"], "パソコン")

    def test_removing_one_leaves_the_ticket_without_a_category(self):
        first = self.shop["categories"][0]
        _status, made = self.admin.post("/api/tickets", {
            "queue_id": self.shop["id"], "title": "消える分類", "category_id": first["id"]})
        self.admin.patch("/api/ticket-queues/{}".format(self.shop["id"]),
                         {"categories": [self.shop["categories"][1]]})
        ticket = self.admin.get("/api/tickets/{}".format(made["ticket"]["id"]))[1]["ticket"]
        self.assertIsNone(ticket["category_id"])
        self.assertIsNone(ticket["category_label"])

    def test_all_of_them_can_be_cleared(self):
        self.admin.patch("/api/ticket-queues/{}".format(self.shop["id"]), {"categories": []})
        self.assertEqual(self.cats(self.shop["id"]), [])

    def test_a_category_from_another_queue_is_refused(self):
        other = self.shop["categories"][0]
        status, data = self.admin.post("/api/tickets", {
            "queue_id": self.queue["id"], "title": "よその分類", "category_id": other["id"]})
        self.assertEqual(status, 400)
        self.assertIn("窓口", data["error"])

    def test_moving_a_ticket_to_another_queue_drops_the_category(self):
        first = self.shop["categories"][0]
        _status, made = self.admin.post("/api/tickets", {
            "queue_id": self.shop["id"], "title": "引っ越すもの", "category_id": first["id"]})
        _status, data = self.admin.patch("/api/tickets/{}".format(made["ticket"]["id"]),
                                         {"queue_id": self.queue["id"]})
        self.assertIsNone(data["ticket"]["category_id"])

    def test_the_change_is_written_into_the_history(self):
        first = self.shop["categories"][0]
        _status, made = self.admin.post("/api/tickets",
                                        {"queue_id": self.shop["id"], "title": "分類をつける"})
        self.admin.patch("/api/tickets/{}".format(made["ticket"]["id"]),
                         {"category_id": first["id"]})
        _status, detail = self.admin.get("/api/tickets/{}".format(made["ticket"]["id"]))
        notes = [c["body"] for c in detail["comments"] if c["kind"] == "system"]
        self.assertTrue(any("分類" in n for n in notes), notes)

    def test_the_detail_carries_the_queues_categories(self):
        _status, made = self.admin.post("/api/tickets",
                                        {"queue_id": self.shop["id"], "title": "選択肢の確認"})
        _status, detail = self.admin.get("/api/tickets/{}".format(made["ticket"]["id"]))
        self.assertEqual([c["label"] for c in detail["queue_categories"]],
                         ["PC・端末", "ネットワーク"])

    def test_tickets_can_be_narrowed_by_category(self):
        first, second = self.shop["categories"]
        self.admin.post("/api/tickets", {"queue_id": self.shop["id"],
                                         "title": "端末のほう", "category_id": first["id"]})
        self.admin.post("/api/tickets", {"queue_id": self.shop["id"],
                                         "title": "回線のほう", "category_id": second["id"]})
        titles = [t["title"] for t in self.admin.get(
            "/api/tickets?category_id={}".format(first["id"]))[1]["tickets"]]
        self.assertEqual(titles, ["端末のほう"])

    def test_import_can_name_the_category(self):
        status, data = self.admin.post("/api/tickets/import", {
            "queue_id": self.shop["id"],
            "rows": [{"title": "取り込みの分類", "category": "ネットワーク"}]})
        self.assertEqual(status, 201, data)
        ticket = self.admin.get("/api/tickets/{}".format(data["ticket_ids"][0]))[1]["ticket"]
        self.assertEqual(ticket["category_label"], "ネットワーク")

    def test_import_reports_an_unknown_category(self):
        _status, data = self.admin.post("/api/tickets/import", {
            "queue_id": self.shop["id"], "dry_run": True,
            "rows": [{"title": "あやしい分類", "category": "ないやつ"}]})
        self.assertEqual(data["would_create"], 1)
        self.assertIn("分類", data["problems"][0]["message"])

    def test_import_uses_the_queues_default_kind(self):
        queue = self.make_queue("取り込み用の障害窓口", default_kind="incident")
        _status, data = self.admin.post("/api/tickets/import", {
            "queue_id": queue["id"], "rows": [{"title": "種別を省く"}]})
        ticket = self.admin.get("/api/tickets/{}".format(data["ticket_ids"][0]))[1]["ticket"]
        self.assertEqual(ticket["kind"], "incident")

    def test_only_an_admin_can_change_the_categories(self):
        _user, email = self.make_user()
        status, _ = self.client_for(email).patch(
            "/api/ticket-queues/{}".format(self.shop["id"]), {"categories": []})
        self.assertEqual(status, 403)


class TestStatsByPerson(TicketTestCase):
    def setUp(self):
        super().setUp()
        self.shop = self.make_queue("集計用の窓口", categories=[
            {"label": "甲"}, {"label": "乙"},
        ])

    def stats(self):
        return self.admin.get(
            "/api/tickets/stats?queue_id={}".format(self.shop["id"]))[1]

    def test_it_counts_per_person(self):
        user, _email = self.make_user(name="対応する人")
        _status, made = self.admin.post("/api/tickets", {
            "queue_id": self.shop["id"], "title": "この人がやる",
            "assignee_id": user["id"]})
        self.admin.patch("/api/tickets/{}".format(made["ticket"]["id"]), {"status": "done"})
        rows = {p["name"]: p for p in self.stats()["by_assignee"]}
        self.assertEqual(rows["対応する人"]["created"], 1)
        self.assertEqual(rows["対応する人"]["resolved"], 1)
        self.assertIsNotNone(rows["対応する人"]["turnaround_days"])

    def test_tickets_with_no_owner_are_gathered_together(self):
        self.admin.post("/api/tickets", {"queue_id": self.shop["id"], "title": "誰もいない"})
        rows = {p["name"]: p for p in self.stats()["by_assignee"]}
        self.assertIn("未割当", rows)
        self.assertIsNone(rows["未割当"]["user_id"])

    def test_the_average_is_empty_until_something_is_finished(self):
        user, _email = self.make_user(name="まだ終わってない人")
        self.admin.post("/api/tickets", {"queue_id": self.shop["id"], "title": "進行中",
                                         "assignee_id": user["id"]})
        rows = {p["name"]: p for p in self.stats()["by_assignee"]}
        self.assertIsNone(rows["まだ終わってない人"]["turnaround_days"])

    def test_it_counts_per_category(self):
        first = self.shop["categories"][0]
        self.admin.post("/api/tickets", {"queue_id": self.shop["id"], "title": "甲のほう",
                                         "category_id": first["id"]})
        self.admin.post("/api/tickets", {"queue_id": self.shop["id"], "title": "分類なし"})
        data = self.stats()
        rows = {c["label"]: c["created"] for c in data["by_category"]}
        self.assertEqual(rows.get("甲"), 1)
        self.assertEqual(rows.get("分類なし"), 1)
        self.assertTrue(data["has_categories"])

    def test_a_queue_without_categories_says_so(self):
        self.make_ticket("分類のない窓口のもの")
        data = self.admin.get(
            "/api/tickets/stats?queue_id={}".format(self.queue["id"]))[1]
        self.assertFalse(data["has_categories"])


class TestSpentHours(TicketTestCase):
    """対応時間は任意。入れておくと、あとで集計に出る。"""

    def test_it_is_empty_by_default(self):
        self.assertIsNone(self.make_ticket()["spent_hours"])

    def test_it_can_be_set_when_raising(self):
        ticket = self.make_ticket("時間つき", spent_hours=1.5)
        self.assertEqual(float(ticket["spent_hours"]), 1.5)

    def test_it_can_be_set_later(self):
        ticket = self.make_ticket()
        _status, data = self.admin.patch("/api/tickets/{}".format(ticket["id"]),
                                         {"spent_hours": 2.5})
        self.assertEqual(float(data["ticket"]["spent_hours"]), 2.5)

    def test_it_can_be_cleared(self):
        ticket = self.make_ticket("あとで消す", spent_hours=3)
        _status, data = self.admin.patch("/api/tickets/{}".format(ticket["id"]),
                                         {"spent_hours": None})
        self.assertIsNone(data["ticket"]["spent_hours"])

    def test_a_negative_value_is_refused(self):
        status, _ = self.admin.post("/api/tickets", {
            "queue_id": self.queue["id"], "title": "マイナス", "spent_hours": -1})
        self.assertEqual(status, 400)

    def test_the_change_is_written_into_the_history(self):
        ticket = self.make_ticket()
        self.admin.patch("/api/tickets/{}".format(ticket["id"]), {"spent_hours": 4})
        _status, detail = self.admin.get("/api/tickets/{}".format(ticket["id"]))
        notes = [c["body"] for c in detail["comments"] if c["kind"] == "system"]
        self.assertTrue(any("対応時間" in n for n in notes), notes)

    def test_import_reads_hours_and_minutes(self):
        _status, data = self.admin.post("/api/tickets/import", {
            "queue_id": self.queue["id"],
            "rows": [{"title": "時間", "spent_hours": "1.5"},
                     {"title": "分", "spent_hours": "90分"},
                     {"title": "空", "spent_hours": ""}]})
        got = [self.admin.get("/api/tickets/{}".format(i))[1]["ticket"]["spent_hours"]
               for i in data["ticket_ids"]]
        self.assertEqual([float(got[0]), float(got[1])], [1.5, 1.5])
        self.assertIsNone(got[2])

    def test_finished_tickets_add_up_in_the_stats(self):
        first = self.make_ticket("2時間かかった", spent_hours=2)
        second = self.make_ticket("3時間かかった", spent_hours=3)
        for ticket in (first, second):
            self.admin.patch("/api/tickets/{}".format(ticket["id"]), {"status": "done"})
        data = self.admin.get(
            "/api/tickets/stats?queue_id={}".format(self.queue["id"]))[1]
        self.assertEqual(data["totals"]["spent_hours"], 5.0)
        self.assertEqual(data["totals"]["spent_rows"], 2)

    def test_unfinished_hours_are_not_counted(self):
        self.make_ticket("まだ途中", spent_hours=8)
        data = self.admin.get(
            "/api/tickets/stats?queue_id={}".format(self.queue["id"]))[1]
        self.assertIsNone(data["totals"]["spent_hours"])

    def test_it_shows_up_per_person(self):
        user, _email = self.make_user(name="時間を使う人")
        ticket = self.make_ticket("その人の作業", assignee_id=user["id"], spent_hours=6)
        self.admin.patch("/api/tickets/{}".format(ticket["id"]), {"status": "done"})
        rows = {p["name"]: p for p in self.admin.get(
            "/api/tickets/stats?queue_id={}".format(self.queue["id"]))[1]["by_assignee"]}
        self.assertEqual(rows["時間を使う人"]["spent_hours"], 6.0)


class TestStatsPeriod(TicketTestCase):
    """集計は半年ぶんまでさかのぼれる。"""

    def stats(self, **params):
        query = "&".join("{}={}".format(k, v) for k, v in params.items())
        return self.admin.get("/api/tickets/stats?" + query)[1]

    def test_it_ends_today_by_default(self):
        data = self.stats(unit="day", span=7)
        self.assertEqual(data["end"], str(db.today()))
        self.assertFalse(data["can_go_forward"])
        self.assertTrue(data["can_go_back"])

    def test_it_can_be_moved_back(self):
        end = (db.today() - timedelta(days=30)).isoformat()
        data = self.stats(unit="day", span=7, end=end)
        self.assertEqual(data["end"], end)
        self.assertTrue(data["can_go_forward"])

    def test_it_stops_at_half_a_year(self):
        data = self.stats(unit="day", span=7, end="2000-01-01")
        self.assertFalse(data["can_go_back"])
        earliest = db.today() - timedelta(days=186)
        self.assertGreaterEqual(datetime.date.fromisoformat(data["from"]), earliest)

    def test_the_future_is_pulled_back_to_today(self):
        end = (db.today() + timedelta(days=90)).isoformat()
        data = self.stats(unit="day", span=7, end=end)
        self.assertEqual(data["end"], str(db.today()))
        self.assertFalse(data["can_go_forward"])

    def test_a_week_lands_on_a_monday(self):
        data = self.stats(unit="week", span=4, end="2026-07-15")
        self.assertEqual(datetime.date.fromisoformat(data["end"]).weekday(), 0)
        self.assertEqual(len(data["buckets"]), 4)

    def test_only_that_window_is_counted(self):
        ticket = self.make_ticket("今日のもの")
        void = ticket
        del void
        old = self.stats(unit="day", span=7, end=(db.today() - timedelta(days=60)).isoformat())
        self.assertEqual(old["totals"]["created"], 0)
        now = self.stats(unit="day", span=7)
        self.assertGreaterEqual(now["totals"]["created"], 1)

    def test_a_bad_date_is_ignored(self):
        status, _ = self.admin.get("/api/tickets/stats?end=めちゃくちゃ")
        self.assertEqual(status, 400)


class TestDailyTickets(TicketTestCase):
    """「今日の確認」に、自分が担当のチケットを出す。"""

    def daily(self, client=None):
        return (client or self.admin).get("/api/daily")[1]

    def test_my_open_tickets_are_listed(self):
        ticket = self.make_ticket("自分の担当", assignee_id=self.admin_id())
        titles = [t["title"] for t in self.daily()["tickets"]]
        self.assertIn(ticket["title"], titles)

    def admin_id(self):
        return db.query_one("SELECT id FROM users WHERE email=%s", (ADMIN_EMAIL,))["id"]

    def test_someone_elses_ticket_is_not_listed(self):
        user, _email = self.make_user(name="よその担当")
        self.make_ticket("よその人のもの", assignee_id=user["id"])
        titles = [t["title"] for t in self.daily()["tickets"]]
        self.assertNotIn("よその人のもの", titles)

    def test_a_finished_ticket_drops_off(self):
        ticket = self.make_ticket("終わったもの", assignee_id=self.admin_id())
        self.admin.patch("/api/tickets/{}".format(ticket["id"]), {"status": "done"})
        titles = [t["title"] for t in self.daily()["tickets"]]
        self.assertNotIn("終わったもの", titles)

    def test_overdue_comes_first(self):
        self.make_ticket("あとの期限", assignee_id=self.admin_id(), due_date="2099-01-01")
        self.make_ticket("切れている", assignee_id=self.admin_id(), due_date="2020-01-01")
        titles = [t["title"] for t in self.daily()["tickets"]]
        self.assertLess(titles.index("切れている"), titles.index("あとの期限"))
        self.assertEqual(titles[0], "切れている")

    def test_unassigned_ones_are_counted(self):
        before = self.daily()["unclaimed_tickets"]
        self.make_ticket("誰も受けていない")
        self.assertEqual(self.daily()["unclaimed_tickets"], before + 1)

    def test_taking_one_removes_it_from_the_unclaimed_count(self):
        ticket = self.make_ticket("これから受ける")
        before = self.daily()["unclaimed_tickets"]
        self.admin.patch("/api/tickets/{}".format(ticket["id"]),
                         {"assignee_id": self.admin_id()})
        self.assertEqual(self.daily()["unclaimed_tickets"], before - 1)

    def test_a_member_sees_their_own(self):
        user, email = self.make_user(name="メンバー担当")
        self.make_ticket("その人のもの", assignee_id=user["id"])
        titles = [t["title"] for t in self.daily(self.client_for(email))["tickets"]]
        self.assertEqual(titles, ["その人のもの"])

    def test_the_queue_is_carried_along(self):
        # 同じクラスの他のテストが作ったものも並ぶので、名前で探す
        self.make_ticket("窓口つき", assignee_id=self.admin_id())
        row = next(t for t in self.daily()["tickets"] if t["title"] == "窓口つき")
        self.assertEqual(row["queue_name"], self.queue["name"])


class TestCrossLinks(TicketTestCase):
    """チケットとタスク・課題・通知のあいだを行き来できる。"""

    def setUp(self):
        super().setUp()
        self.project = self.make_project("行き来PJ")

    def test_the_task_knows_which_ticket_it_came_from(self):
        ticket = self.make_ticket("もとの依頼")
        _status, made = self.admin.post("/api/tickets/{}/task".format(ticket["id"]),
                                        {"project_id": self.project["id"]})
        _status, detail = self.admin.get("/api/tasks/{}".format(made["task"]["id"]))
        self.assertEqual([t["id"] for t in detail["tickets"]], [ticket["id"]])
        self.assertEqual(detail["tickets"][0]["queue_name"], self.queue["name"])

    def test_the_issue_knows_too(self):
        ticket = self.make_ticket("論点になった依頼")
        _status, made = self.admin.post("/api/tickets/{}/issue".format(ticket["id"]),
                                        {"project_id": self.project["id"]})
        _status, detail = self.admin.get("/api/issues/{}".format(made["issue"]["id"]))
        self.assertEqual([t["id"] for t in detail["tickets"]], [ticket["id"]])

    def test_a_task_with_no_ticket_says_so(self):
        task = self.make_task(self.project["id"], title="ふつうのタスク")
        _status, detail = self.admin.get("/api/tasks/{}".format(task["id"]))
        self.assertEqual(detail["tickets"], [])

    def test_the_mention_notification_points_at_the_ticket(self):
        user, email = self.make_user(name="呼ぶ人")
        ticket = self.make_ticket("呼ばれるチケット")
        self.client_for(email).post("/api/tickets/{}/comments".format(ticket["id"]),
                                    {"body": "@{} お願いします".format(ADMIN_NAME)})
        rows = db.query(
            "SELECT type, ticket_id FROM notifications WHERE user_id=%s "
            "ORDER BY id DESC LIMIT 1", (self.admin_user_id(),))
        void = user
        del void
        self.assertEqual(rows[0]["ticket_id"], ticket["id"])

    def admin_user_id(self):
        return db.query_one("SELECT id FROM users WHERE email=%s", (ADMIN_EMAIL,))["id"]

    def test_the_assignment_notification_points_at_the_ticket(self):
        user, _email = self.make_user(name="担当にされる人")
        ticket = self.make_ticket("担当をつける")
        self.admin.patch("/api/tickets/{}".format(ticket["id"]),
                         {"assignee_id": user["id"]})
        row = db.query_one(
            "SELECT ticket_id FROM notifications WHERE user_id=%s ORDER BY id DESC LIMIT 1",
            (user["id"],))
        self.assertEqual(row["ticket_id"], ticket["id"])

    def test_deleting_the_ticket_takes_its_notifications_with_it(self):
        user, _email = self.make_user(name="消える通知の人")
        ticket = self.make_ticket("消すチケット")
        self.admin.patch("/api/tickets/{}".format(ticket["id"]),
                         {"assignee_id": user["id"]})
        self.admin.delete("/api/tickets/{}".format(ticket["id"]))
        left = db.scalar("SELECT COUNT(*) AS c FROM notifications WHERE ticket_id=%s",
                         (ticket["id"],), default=0)
        self.assertEqual(left, 0)

    def test_a_project_queue_shows_up_in_the_project_stats(self):
        queue = self.make_queue("PJ専用窓口", project_id=self.project["id"])
        self.admin.post("/api/tickets", {"queue_id": queue["id"], "title": "PJ宛て"})
        row = next(p for p in self.admin.get("/api/projects")[1]["projects"]
                   if p["id"] == self.project["id"])
        self.assertEqual(row["stats"]["open_tickets"], 1)

    def test_a_loose_queue_is_not_counted_for_any_project(self):
        self.make_ticket("どこ宛てでもない")
        row = next(p for p in self.admin.get("/api/projects")[1]["projects"]
                   if p["id"] == self.project["id"])
        self.assertEqual(row["stats"]["open_tickets"], 0)


class TestTicketPaging(TicketTestCase):
    """件数が増えても全部を一度に返さない。切れていることは画面に伝える。"""

    def page(self, **params):
        query = "&".join("{}={}".format(k, v) for k, v in params.items())
        return self.admin.get("/api/tickets?" + query)[1]

    def test_it_reports_how_many_matched(self):
        for i in range(3):
            self.make_ticket("数える {}".format(i))
        data = self.page(queue_id=self.queue["id"])
        self.assertEqual(data["matched"], 3)
        self.assertFalse(data["has_more"])

    def test_it_stops_at_one_page(self):
        for i in range(12):
            self.make_ticket("たくさん {}".format(i))
        data = self.page(queue_id=self.queue["id"], limit=5)
        self.assertEqual(len(data["tickets"]), 5)
        self.assertEqual(data["matched"], 12)
        self.assertTrue(data["has_more"])

    def test_the_next_page_continues_where_it_left_off(self):
        for i in range(12):
            self.make_ticket("続き {}".format(i))
        first = self.page(queue_id=self.queue["id"], limit=5)
        second = self.page(queue_id=self.queue["id"], limit=5, offset=5)
        ids = [t["id"] for t in first["tickets"]] + [t["id"] for t in second["tickets"]]
        self.assertEqual(len(set(ids)), 10)
        self.assertTrue(second["has_more"])

    def test_the_last_page_says_there_is_no_more(self):
        for i in range(7):
            self.make_ticket("終わり {}".format(i))
        last = self.page(queue_id=self.queue["id"], limit=5, offset=5)
        self.assertEqual(len(last["tickets"]), 2)
        self.assertFalse(last["has_more"])

    def test_narrowing_changes_how_many_matched(self):
        self.make_ticket("あたり")
        self.make_ticket("はずれ")
        data = self.page(queue_id=self.queue["id"], q="あたり")
        self.assertEqual(data["matched"], 1)

    def test_a_silly_offset_is_harmless(self):
        self.make_ticket("ひとつだけ")
        data = self.page(queue_id=self.queue["id"], offset=99999)
        self.assertEqual(data["tickets"], [])
        self.assertFalse(data["has_more"])


class TestTicketSearch(TicketTestCase):
    def test_a_ticket_turns_up_in_the_global_search(self):
        self.make_ticket("ぷりんたの調子がわるい")
        _status, data = self.admin.get("/api/search?q=ぷりんた")
        kinds = {g["kind"] for g in data["groups"]}
        self.assertIn("ticket", kinds)

    def test_a_member_outside_every_project_still_finds_it(self):
        self.make_ticket("だれでも見えるはず")
        _user, email = self.make_user()
        _status, data = self.client_for(email).get("/api/search?q=だれでも")
        kinds = {g["kind"] for g in data["groups"]}
        self.assertIn("ticket", kinds)


if __name__ == "__main__":
    unittest.main()
