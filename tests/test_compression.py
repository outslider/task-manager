"""応答の圧縮（gzip）と JSON の書き出し。

大きな一覧（数千件のタスク）は JSON が数 MB になるので、ブラウザが受け取れるときは圧縮して送る。
圧縮しても中身が変わらないこと、送ってはいけないもの（小さい応答・304・添付のダウンロード）は
そのまま送ることを確かめる。
"""
import gzip
import json
import os
import sys
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_api import ADMIN, ApiTestCase, Client  # noqa: E402
from app import db, http_util  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fetch(url, headers=None, cookie_client=None):
    """生の応答（本文は受け取ったまま）とヘッダーを返す。"""
    req = urllib.request.Request(url, headers=headers or {})
    opener = cookie_client.opener if cookie_client else urllib.request.build_opener()
    try:
        with opener.open(req, timeout=20) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


class TestCompression(ApiTestCase):
    def setUp(self):
        self.admin = Client(self.base)
        self.admin.login(*ADMIN)

    def test_static_js_is_gzipped_when_accepted(self):
        status, headers, body = fetch(self.base + "/js/views/gantt.js", {"Accept-Encoding": "gzip, br"})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Encoding"), "gzip")
        self.assertEqual(headers.get("Vary"), "Accept-Encoding")
        self.assertEqual(int(headers.get("Content-Length")), len(body))
        with open(os.path.join(ROOT, "static", "js", "views", "gantt.js"), "rb") as fh:
            original = fh.read()
        self.assertEqual(gzip.decompress(body), original)
        self.assertLess(len(body), len(original) / 3)

    def test_not_gzipped_without_accept_encoding(self):
        status, headers, body = fetch(self.base + "/js/views/gantt.js")
        self.assertEqual(status, 200)
        self.assertIsNone(headers.get("Content-Encoding"))
        with open(os.path.join(ROOT, "static", "js", "views", "gantt.js"), "rb") as fh:
            self.assertEqual(body, fh.read())

    def test_revalidation_still_returns_304(self):
        _s, headers, _b = fetch(self.base + "/css/style.css", {"Accept-Encoding": "gzip"})
        status, headers2, body = fetch(self.base + "/css/style.css",
                                       {"Accept-Encoding": "gzip", "If-None-Match": headers["ETag"]})
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")
        self.assertIsNone(headers2.get("Content-Encoding"))

    def test_small_json_is_sent_as_is(self):
        status, headers, body = fetch(self.base + "/api/auth/whoami", {"Accept-Encoding": "gzip"},
                                      cookie_client=Client(self.base))
        self.assertIsNone(headers.get("Content-Encoding"))
        json.loads(body)

    def test_large_api_json_is_gzipped_and_identical(self):
        status, data = self.admin.post("/api/projects", {"name": "圧縮の確認"})
        self.assertEqual(status, 201, data)
        pid = data["project"]["id"]
        now = db.now()
        db.executemany(
            "INSERT INTO tasks(project_id, title, description, status, created_at, updated_at) "
            "VALUES(%s,%s,%s,'todo',%s,%s)",
            [(pid, "タスク {}".format(i), "説明" * 20, now, now) for i in range(80)])
        path = "/api/projects/{}/tasks".format(pid)
        status, headers, packed = fetch(self.base + path, {"Accept-Encoding": "gzip"}, cookie_client=self.admin)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Encoding"), "gzip")
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        _s, _h, plain = fetch(self.base + path, cookie_client=self.admin)
        self.assertEqual(json.loads(gzip.decompress(packed)), json.loads(plain))
        self.assertEqual(len(json.loads(plain)["tasks"]), 80)

    def test_downloads_are_not_compressed(self):
        """添付のダウンロードは、テキストでも受け取ったままのバイト列で返す。"""
        status, data = self.admin.post("/api/projects", {"name": "添付の確認"})
        pid = data["project"]["id"]
        task = self.make_task(pid, "添付先")
        boundary = "----tmgz"
        content = ("圧縮してはいけない中身\n" * 300).encode("utf-8")
        body = (
            "--{b}\r\n"
            'Content-Disposition: form-data; name="file"; filename="memo.txt"\r\n'
            "Content-Type: text/plain\r\n\r\n"
        ).format(b=boundary).encode("utf-8") + content + "\r\n--{b}--\r\n".format(b=boundary).encode("utf-8")
        status, data = self.admin.request(
            "POST", "/api/tasks/{}/attachments".format(task["id"]), raw_body=body,
            content_type="multipart/form-data; boundary={}".format(boundary))
        self.assertEqual(status, 201, data)
        att = data["attachments"][0]
        status, headers, payload = fetch(self.base + "/api/attachments/{}/download".format(att["id"]),
                                         {"Accept-Encoding": "gzip"}, cookie_client=self.admin)
        self.assertEqual(status, 200)
        self.assertIn("text/plain", headers.get("Content-Type"))
        self.assertIsNone(headers.get("Content-Encoding"))
        self.assertEqual(payload, content)


class TestJsonDefault(unittest.TestCase):
    def test_same_output_as_before(self):
        value = {
            "at": datetime(2026, 9, 27, 13, 5, 9), "on": date(2026, 9, 27),
            "hours": Decimal("1.50"), "raw": b"abc", "list": [date(2026, 1, 2), None, 3],
            "nested": {"d": Decimal("2")},
        }
        response = http_util.json_response(value)
        self.assertEqual(json.loads(response.body), json.loads(json.dumps(db.jsonable(value), default=str)))
        self.assertEqual(json.loads(response.body)["at"], "2026-09-27 13:05:09")
        self.assertEqual(json.loads(response.body)["hours"], 1.5)


if __name__ == "__main__":
    unittest.main()
