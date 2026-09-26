"""ログイン履歴。何が残り、誰が見られ、何を残さないか。"""
import json
import os
import sys
import unittest
import urllib.request
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_api import ADMIN, ApiTestCase, Client  # noqa: E402

from app import db, logins  # noqa: E402


class TestLabels(unittest.TestCase):
    def test_mask_email(self):
        self.assertEqual(logins.mask_email("tanaka@example.co.jp"), "t***@example.co.jp")
        # メール欄にパスワードを打ってしまった場合は、何も残さない
        self.assertEqual(logins.mask_email("hunter2!"), "（メールアドレスではない入力）")
        self.assertEqual(logins.mask_email("@example.com"), "（メールアドレスではない入力）")

    def test_device_label(self):
        chrome_win = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
        edge = chrome_win + " Edg/128.0"
        iphone = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                  "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
        self.assertEqual(logins.device_label(chrome_win), "Chrome / Windows")
        self.assertEqual(logins.device_label(edge), "Edge / Windows")
        self.assertEqual(logins.device_label(iphone), "Safari / iOS")
        self.assertEqual(logins.device_label(""), "不明")


class TestLoginHistory(ApiTestCase):
    def events(self, **query):
        qs = "&".join("{}={}".format(k, v) for k, v in query.items())
        status, data = self.admin.get("/api/admin/logins" + ("?" + qs if qs else ""))
        self.assertEqual(status, 200, data)
        return data

    def latest(self, user_id=None):
        rows = db.query("SELECT * FROM login_events" + (" WHERE user_id=%s" if user_id else "")
                        + " ORDER BY id DESC LIMIT 1", (user_id,) if user_id else ())
        return rows[0] if rows else None

    def test_success_and_failure_are_recorded(self):
        user, email = self.make_user("記録される人")
        self.client_for(email)
        row = self.latest(user["id"])
        self.assertEqual((row["event"], row["ip"]), ("login", "127.0.0.1"))
        status, _ = Client(self.base).post("/api/auth/login",
                                           {"email": email, "password": "wrong-password"})
        self.assertEqual(status, 401)
        row = self.latest(user["id"])
        self.assertEqual((row["event"], row["reason"]), ("failed", "bad_password"))

    def test_unknown_address_is_masked(self):
        Client(self.base).post("/api/auth/login",
                               {"email": "nobody@nowhere.example", "password": "x" * 10})
        row = self.latest()
        self.assertEqual((row["user_id"], row["reason"], row["user_label"]),
                         (None, "unknown", "n***@nowhere.example"))
        Client(self.base).post("/api/auth/login", {"email": "my-secret-pass", "password": "x"})
        row = self.latest()
        self.assertNotIn("secret", row["user_label"])

    def test_inactive_account(self):
        user, email = self.make_user("停止される人")
        self.admin.patch("/api/users/{}".format(user["id"]), {"is_active": False})
        # 正しいパスワードでも入れない。理由は「停止中」として残る
        status, data = Client(self.base).post("/api/auth/login",
                                              {"email": email, "password": "userpassword"})
        self.assertEqual(status, 401)
        self.assertEqual(self.latest(user["id"])["reason"], "inactive")
        # 本人への返事は、パスワード違いと同じ（停止中かどうかを教えない）
        _, wrong = Client(self.base).post("/api/auth/login",
                                          {"email": email, "password": "not-it-at-all"})
        self.assertEqual(data["error"], wrong["error"])
        self.assertEqual(self.latest(user["id"])["reason"], "bad_password")

    def test_logout_password_change_and_reset(self):
        user, email = self.make_user("いろいろする人")
        client = self.client_for(email)
        status, _ = client.post("/api/auth/password", {
            "current_password": "userpassword", "new_password": "new-password-1"})
        self.assertEqual(status, 200)
        self.assertEqual(self.latest(user["id"])["event"], "password")
        client.post("/api/auth/logout", {})
        self.assertEqual(self.latest(user["id"])["event"], "logout")
        self.admin.post("/api/users/{}/password".format(user["id"]), {})
        row = self.latest(user["id"])
        self.assertEqual(row["event"], "reset")
        self.assertIn("管理者", row["user_label"])

    def test_only_admins_can_read(self):
        _user, email = self.make_user("のぞく人")
        self.assertEqual(self.client_for(email).get("/api/admin/logins")[0], 403)
        self.assertEqual(Client(self.base).get("/api/admin/logins")[0], 401)

    def test_filters_and_paging(self):
        user, email = self.make_user("絞られる人")
        for _ in range(3):
            Client(self.base).post("/api/auth/login", {"email": email, "password": "wrong-pw-x"})
        self.client_for(email)
        mine = self.events(user_id=user["id"])
        # いちばん古いのは、管理者がこの人を作ったときの記録
        self.assertEqual([e["event"] for e in mine["events"]],
                         ["login", "failed", "failed", "failed", "created"])
        self.assertEqual(mine["summary"]["failed"], 3)
        self.assertEqual(mine["events"][1]["reason_label"], "パスワード違い")
        failed = self.events(user_id=user["id"], kind="failed")
        self.assertEqual(len(failed["events"]), 3)
        self.assertEqual(self.admin.get("/api/admin/logins?kind=bogus")[0], 400)
        # 続きの取り出し（before より古いものだけ）
        first = self.events(user_id=user["id"])
        last_id = first["events"][1]["id"]
        rest = self.events(user_id=user["id"], before=last_id)
        self.assertEqual([e["event"] for e in rest["events"]], ["failed", "failed", "created"])
        self.assertFalse(rest["has_more"])

    def test_user_list_shows_last_login_to_admins_only(self):
        user, email = self.make_user("一覧の人")
        client = self.client_for(email)
        Client(self.base).post("/api/auth/login", {"email": email, "password": "wrong-pw-y"})
        users = {u["id"]: u for u in self.admin.get("/api/users?include_inactive=1")[1]["users"]}
        self.assertIsNotNone(users[user["id"]]["last_login_at"])
        self.assertEqual(users[user["id"]]["failed_7d"], 1)
        seen = {u["id"]: u for u in client.get("/api/users")[1]["users"]}
        self.assertNotIn("last_login_at", seen[user["id"]])
        self.assertNotIn("failed_7d", seen[user["id"]])

    def test_history_survives_user_deletion(self):
        user, email = self.make_user("消える人")
        self.client_for(email)
        self.assertEqual(self.admin.delete("/api/users/{}".format(user["id"]))[0], 200)
        row = db.query_one("SELECT * FROM login_events WHERE user_label=%s ORDER BY id DESC",
                           ("消える人",))
        self.assertIsNotNone(row)
        self.assertIsNone(row["user_id"])

    def test_old_history_is_purged(self):
        user, email = self.make_user("古い記録の人")
        self.client_for(email)
        db.execute("UPDATE login_events SET created_at=%s WHERE user_id=%s",
                   (db.now() - timedelta(days=logins.KEEP_DAYS + 1), user["id"]))
        self.assertGreaterEqual(logins.purge_expired(), 1)
        self.assertIsNone(self.latest(user["id"]))

    def _login_with_headers(self, headers):
        user, email = self.make_user("中継の人")
        request = urllib.request.Request(
            self.base + "/api/auth/login",
            data=json.dumps({"email": email, "password": "userpassword"}).encode(),
            headers=dict({"Content-Type": "application/json"}, **headers), method="POST")
        with urllib.request.urlopen(request, timeout=20) as response:
            self.assertEqual(response.status, 200)
        return self.latest(user["id"])

    def test_ip_through_a_local_proxy(self):
        # テストのサーバーには同じマシンから繋ぐので、中継を通ってきた扱いになる
        row = self._login_with_headers({"X-Forwarded-For": "203.0.113.9, 198.51.100.7"})
        self.assertEqual(row["ip"], "198.51.100.7")
        row = self._login_with_headers({"X-Real-IP": "192.0.2.44",
                                        "X-Forwarded-For": "203.0.113.9"})
        self.assertEqual(row["ip"], "192.0.2.44")
        row = self._login_with_headers({"User-Agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/1"})
        event = next(e for e in self.events(user_id=row["user_id"])["events"])
        self.assertEqual(event["device"], "Chrome / Windows")


if __name__ == "__main__":
    unittest.main()
