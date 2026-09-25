"""多要素認証・パスワード再設定・その履歴。"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_api import ADMIN, ApiTestCase, Client  # noqa: E402

from app import db, mfa, notify  # noqa: E402


def now_code(secret, shift=0):
    return mfa.code_at(secret, mfa.current_step() + shift)


class TestTotp(ApiTestCase):
    def test_rfc6238_vector(self):
        # RFC 6238 の試験値（SHA1、鍵 "12345678901234567890"、59 秒 → 94287082 の下 6 桁）
        import base64
        secret = base64.b32encode(b"12345678901234567890").decode()
        self.assertEqual(mfa.code_at(secret, 59 // 30), "287082")
        self.assertEqual(mfa.match_step(secret, "287 082", now=59), 1)
        self.assertIsNone(mfa.match_step(secret, "287082", after_step=1, now=59))


class SecurityCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.user, self.email = self.make_user("多要素 花子")
        self.client = self.client_for(self.email)

    def tearDown(self):
        db.set_setting("mfa_required", "off")
        db.set_setting("email_enabled", "0")
        db.set_setting("smtp_host", "")
        db.execute("DELETE FROM login_events WHERE event='recovery'")

    def enable_mfa(self, client=None):
        client = client or self.client
        status, setup = client.post("/api/auth/mfa/setup")
        self.assertEqual(status, 200, setup)
        self.assertIn("<svg", setup["qr_svg"])
        self.assertTrue(setup["uri"].startswith("otpauth://totp/"))
        status, data = client.post("/api/auth/mfa/enable", {"code": now_code(setup["secret"])})
        self.assertEqual(status, 200, data)
        # 設定で使ったコードは、ログインでは通らない。次の区切りまで進めておく
        db.execute("UPDATE user_mfa SET last_step=last_step-2 WHERE secret=%s", (setup["secret"],))
        return setup["secret"], data["recovery_codes"]

    def events(self, user_id=None):
        return [(r["event"], r["reason"]) for r in db.query(
            "SELECT event, reason FROM login_events WHERE user_id=%s ORDER BY id",
            (user_id or self.user["id"],))]


class TestMfaLogin(SecurityCase):
    def test_setup_needs_a_right_code_and_gives_recovery_codes(self):
        status, setup = self.client.post("/api/auth/mfa/setup")
        self.assertEqual(status, 200)
        self.assertEqual(self.client.post("/api/auth/mfa/enable", {"code": "000000"})[0], 400)
        self.assertFalse(self.client.get("/api/auth/mfa")[1]["enabled"])
        status, data = self.client.post("/api/auth/mfa/enable", {"code": now_code(setup["secret"])})
        self.assertEqual(status, 200, data)
        self.assertEqual(len(data["recovery_codes"]), 10)
        state = self.client.get("/api/auth/mfa")[1]
        self.assertEqual((state["enabled"], state["recovery_left"]), (True, 10))
        self.assertEqual(self.client.post("/api/auth/mfa/setup")[0], 400)
        self.assertIn(("mfa_on", ""), self.events())

    def test_password_alone_no_longer_logs_in(self):
        secret, _ = self.enable_mfa()
        fresh = Client(self.base)
        status, data = fresh.post("/api/auth/login", {"email": self.email, "password": "userpassword"})
        self.assertEqual(status, 200, data)
        self.assertTrue(data["mfa_required"])
        self.assertNotIn("user", data)
        self.assertIsNone(fresh.get("/api/auth/me")[1]["user"])
        self.assertEqual(fresh.get("/api/projects")[0], 401)
        # 違うコード → 残りの回数
        status, err = fresh.post("/api/auth/mfa/verify",
                                 {"challenge": data["challenge"], "code": "000000"})
        self.assertEqual(status, 401)
        self.assertIn("あと 4 回", err["error"])
        status, ok = fresh.post("/api/auth/mfa/verify",
                                {"challenge": data["challenge"], "code": now_code(secret)})
        self.assertEqual(status, 200, ok)
        self.assertTrue(ok["user"]["mfa_enabled"])
        self.assertEqual(fresh.get("/api/auth/me")[1]["user"]["id"], self.user["id"])
        self.assertIn(("failed", "bad_mfa"), self.events())
        self.assertEqual(self.events()[-1], ("login", "mfa"))
        # 合言葉は使い切り
        self.assertEqual(fresh.post("/api/auth/mfa/verify", {
            "challenge": data["challenge"], "code": now_code(secret)})[0], 401)

    def test_the_same_code_is_not_accepted_twice(self):
        secret, _ = self.enable_mfa()
        code = now_code(secret)
        first = Client(self.base)
        ch = first.post("/api/auth/login", {"email": self.email, "password": "userpassword"})[1]
        self.assertEqual(first.post("/api/auth/mfa/verify",
                                    {"challenge": ch["challenge"], "code": code})[0], 200)
        second = Client(self.base)
        ch = second.post("/api/auth/login", {"email": self.email, "password": "userpassword"})[1]
        self.assertEqual(second.post("/api/auth/mfa/verify",
                                     {"challenge": ch["challenge"], "code": code})[0], 401)

    def test_five_misses_end_the_attempt(self):
        secret, _ = self.enable_mfa()
        fresh = Client(self.base)
        ch = fresh.post("/api/auth/login", {"email": self.email, "password": "userpassword"})[1]
        for _ in range(5):
            status, err = fresh.post("/api/auth/mfa/verify",
                                     {"challenge": ch["challenge"], "code": "000000"})
        self.assertEqual(status, 401)
        self.assertTrue(err["detail"]["restart"])
        status, err = fresh.post("/api/auth/mfa/verify",
                                 {"challenge": ch["challenge"], "code": now_code(secret)})
        self.assertEqual(status, 401)
        self.assertTrue(err["detail"]["restart"])

    def test_a_recovery_code_works_once(self):
        _, codes = self.enable_mfa()
        fresh = Client(self.base)
        ch = fresh.post("/api/auth/login", {"email": self.email, "password": "userpassword"})[1]
        status, data = fresh.post("/api/auth/mfa/verify",
                                  {"challenge": ch["challenge"], "code": codes[0].upper()})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["recovery_left"], 9)
        self.assertEqual(self.events()[-1], ("login", "recovery_code"))
        again = Client(self.base)
        ch = again.post("/api/auth/login", {"email": self.email, "password": "userpassword"})[1]
        self.assertEqual(again.post("/api/auth/mfa/verify",
                                    {"challenge": ch["challenge"], "code": codes[0]})[0], 401)

    def test_disable_and_new_codes_need_the_password(self):
        _, codes = self.enable_mfa()
        self.assertEqual(self.client.post("/api/auth/mfa/recovery-codes", {"password": "x"})[0], 400)
        status, data = self.client.post("/api/auth/mfa/recovery-codes", {"password": "userpassword"})
        self.assertEqual(status, 200)
        self.assertNotEqual(set(data["recovery_codes"]), set(codes))
        self.assertEqual(self.client.post("/api/auth/mfa/disable", {"password": "x"})[0], 400)
        self.assertEqual(self.client.post("/api/auth/mfa/disable",
                                          {"password": "userpassword"})[0], 200)
        self.assertFalse(self.client.get("/api/auth/mfa")[1]["enabled"])
        self.assertEqual(db.scalar("SELECT COUNT(*) AS c FROM mfa_recovery_codes WHERE user_id=%s",
                                   (self.user["id"],)), 0)
        kinds = [e for e, _ in self.events()]
        self.assertIn("mfa_codes", kinds)
        self.assertIn("mfa_off", kinds)
        # 次はパスワードだけで入れる
        self.client_for(self.email)

    def test_the_secret_never_leaves_the_server_after_setup(self):
        secret, _ = self.enable_mfa()
        for client, path in ((self.client, "/api/auth/me"), (self.client, "/api/auth/mfa"),
                             (self.admin, "/api/users"), (self.admin, "/api/users?include_inactive=1")):
            body = str(client.get(path)[1])
            self.assertNotIn(secret, body, path)

    def test_admin_can_reset_a_lost_phone(self):
        self.enable_mfa()
        other = self.make_user("一般")[1]
        self.assertEqual(self.client_for(other).delete(
            "/api/users/{}/mfa".format(self.user["id"]))[0], 403)
        self.assertEqual(self.admin.delete("/api/users/{}/mfa".format(self.user["id"]))[0], 200)
        # 今のセッションも切れる
        self.assertEqual(self.client.get("/api/projects")[0], 401)
        self.client_for(self.email)
        row = db.query_one("SELECT user_label FROM login_events WHERE event='mfa_reset' "
                           "ORDER BY id DESC LIMIT 1")
        self.assertIn("がリセット", row["user_label"])

    def test_admin_list_shows_who_uses_mfa(self):
        self.enable_mfa()
        rows = {u["id"]: u for u in self.admin.get("/api/users")[1]["users"]}
        self.assertTrue(rows[self.user["id"]]["mfa_enabled"])
        rows = {u["id"]: u for u in self.client.get("/api/users")[1]["users"]}
        self.assertNotIn("mfa_enabled", rows[self.user["id"]])


class TestMfaRequired(SecurityCase):
    def test_required_for_everyone_locks_everything_but_setup(self):
        db.set_setting("mfa_required", "all")
        status, err = self.client.get("/api/projects")
        self.assertEqual(status, 403)
        self.assertTrue(err["detail"]["mfa_setup_required"])
        self.assertEqual(self.client.post("/api/tasks", {"title": "x"})[0], 403)
        me = self.client.get("/api/auth/me")[1]["user"]
        self.assertTrue(me["mfa_setup_required"])
        self.enable_mfa()
        self.assertEqual(self.client.get("/api/projects")[0], 200)
        self.assertFalse(self.client.get("/api/auth/me")[1]["user"]["mfa_setup_required"])

    def test_admin_only_setting_leaves_members_alone(self):
        db.set_setting("mfa_required", "admin")
        self.assertEqual(self.client.get("/api/projects")[0], 200)
        self.assertEqual(self.admin.get("/api/projects")[0], 403)
        self.assertTrue(self.admin.get("/api/auth/me")[1]["user"]["mfa_setup_required"])

    def test_guests_can_set_it_up(self):
        email = "guest-mfa@test.local"
        status, data = self.admin.post("/api/users", {
            "name": "社外", "email": email, "role": "guest", "organization": "協力会社",
            "password": "userpassword"})
        self.assertEqual(status, 201, data)
        client = self.client_for(email)
        db.set_setting("mfa_required", "all")
        self.assertEqual(client.get("/api/projects")[0], 403)
        self.enable_mfa(client)
        self.assertEqual(client.get("/api/projects")[0], 200)


class TestRecovery(SecurityCase):
    def test_without_mail_the_admins_are_asked(self):
        status, data = Client(self.base).post("/api/auth/recovery", {"email": self.email})
        self.assertEqual(status, 200)
        self.assertEqual(data["via"], "admin")
        admin_id = db.scalar("SELECT id FROM users WHERE email=%s", (ADMIN[0],))
        note = db.query_one("SELECT title, body FROM notifications WHERE user_id=%s "
                            "ORDER BY id DESC LIMIT 1", (admin_id,))
        self.assertIn("多要素 花子", note["title"])
        self.assertIn(("recovery", "admin"), self.events())
        # パスワードは変わっていない
        self.client_for(self.email)

    def test_unknown_addresses_get_the_same_answer(self):
        known = Client(self.base).post("/api/auth/recovery", {"email": self.email})
        unknown = Client(self.base).post("/api/auth/recovery", {"email": "nobody@test.local"})
        self.assertEqual(known, unknown)
        row = db.query_one("SELECT user_id, user_label, reason FROM login_events "
                           "WHERE event='recovery' ORDER BY id DESC LIMIT 1")
        self.assertEqual((row["user_id"], row["reason"]), (None, "unknown"))
        self.assertEqual(row["user_label"], "n***@test.local")

    def mail_on(self):
        db.set_setting("email_enabled", "1")
        db.set_setting("smtp_host", "smtp.invalid")
        sent = []
        patcher = mock.patch.object(notify, "send_email_async",
                                    side_effect=lambda *a, **k: sent.append(a))
        patcher.start()
        self.addCleanup(patcher.stop)
        return sent

    def token_from(self, sent):
        body = sent[-1][2]
        return body.split("#/reset/")[1].split()[0]

    def test_by_mail_the_link_sets_a_new_password(self):
        sent = self.mail_on()
        status, data = Client(self.base).post("/api/auth/recovery", {"email": self.email})
        self.assertEqual((status, data["via"]), (200, "mail"))
        self.assertEqual(sent[-1][0], self.email)
        token = self.token_from(sent)
        anon = Client(self.base)
        self.assertTrue(anon.get("/api/auth/reset/" + token)[1]["ok"])
        self.assertEqual(anon.post("/api/auth/reset", {"token": token, "password": "short"})[0], 400)
        self.assertEqual(anon.post("/api/auth/reset",
                                   {"token": token, "password": "brand-new-pw"})[0], 200)
        # 使い切り、今のセッションは切れる、新しいパスワードで入れる
        self.assertFalse(anon.get("/api/auth/reset/" + token)[1]["ok"])
        self.assertEqual(anon.post("/api/auth/reset",
                                   {"token": token, "password": "another-pw"})[0], 400)
        self.assertEqual(self.client.get("/api/projects")[0], 401)
        self.assertEqual(Client(self.base).post("/api/auth/login", {
            "email": self.email, "password": "userpassword"})[0], 401)
        self.client_for(self.email, "brand-new-pw")
        kinds = self.events()
        self.assertIn(("recovery", "mail"), kinds)
        self.assertIn(("recovered", "mail"), kinds)

    def test_mfa_still_guards_after_a_reset(self):
        secret, _ = self.enable_mfa()
        sent = self.mail_on()
        Client(self.base).post("/api/auth/recovery", {"email": self.email})
        Client(self.base).post("/api/auth/reset", {"token": self.token_from(sent),
                                                   "password": "brand-new-pw"})
        status, data = Client(self.base).post("/api/auth/login",
                                              {"email": self.email, "password": "brand-new-pw"})
        self.assertTrue(data["mfa_required"])

    def test_changing_the_password_kills_open_links(self):
        sent = self.mail_on()
        Client(self.base).post("/api/auth/recovery", {"email": self.email})
        token = self.token_from(sent)
        self.client.post("/api/auth/password", {"current_password": "userpassword",
                                                "new_password": "changed-pw-1"})
        self.assertFalse(Client(self.base).get("/api/auth/reset/" + token)[1]["ok"])

    def test_requests_are_limited(self):
        sent = self.mail_on()
        for _ in range(5):
            Client(self.base).post("/api/auth/recovery", {"email": self.email})
        self.assertEqual(len(sent), 3)
        self.assertIn(("recovery", "limited"), self.events())

    def test_a_stopped_account_gets_nothing(self):
        sent = self.mail_on()
        self.admin.patch("/api/users/{}".format(self.user["id"]), {"is_active": False})
        Client(self.base).post("/api/auth/recovery", {"email": self.email})
        self.assertEqual(sent, [])


class TestOwnHistory(SecurityCase):
    def test_i_see_only_my_own_events(self):
        self.enable_mfa()
        events = self.client.get("/api/auth/logins")[1]["events"]
        self.assertTrue(events)
        self.assertEqual({e["user_id"] for e in events}, {self.user["id"]})
        self.assertIn("多要素認証の設定", [e["event_label"] for e in events])

    def test_admin_can_filter_security_events(self):
        self.enable_mfa()
        rows = self.admin.get("/api/admin/logins?kind=security")[1]["events"]
        self.assertTrue(rows)
        self.assertTrue(all(r["event"] not in ("login", "failed", "logout") for r in rows))
