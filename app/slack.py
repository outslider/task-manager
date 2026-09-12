"""Slack への通知（Incoming Webhook）。

こちらから送るだけなので、インターネットに公開されたエンドポイントは不要。
社内ネットワーク内に閉じた構成のままで使える。
"""
import json
import logging
import threading
import urllib.error
import urllib.request

from . import db

log = logging.getLogger("tm.slack")
TIMEOUT = 10


def settings():
    values = db.all_settings()
    return {
        "enabled": values.get("slack_enabled") == "1",
        "webhook_url": values.get("slack_webhook_url", "").strip(),
    }


def available():
    config = settings()
    return bool(config["enabled"] and config["webhook_url"])


def webhook_for(project_id=None):
    """プロジェクト個別の宛先があればそちらへ、なければ全体設定へ。"""
    config = settings()
    if not config["enabled"]:
        return ""
    if project_id:
        url = db.scalar("SELECT slack_webhook_url AS u FROM projects WHERE id=%s",
                        (project_id,), default="") or ""
        if url.strip():
            return url.strip()
    return config["webhook_url"]


def post(text, webhook_url=None, project_id=None):
    """1件送る。(成否, メッセージ) を返す。"""
    url = (webhook_url or webhook_for(project_id)).strip()
    if not url:
        return False, "Slack の送信先が設定されていません"
    payload = json.dumps({"text": text}).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read().decode("utf-8", "replace").strip()
        if body and body != "ok":
            return False, "Slack から予期しない応答: {}".format(body[:120])
        return True, "送信しました"
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:120]
        return False, "Slack エラー ({}): {}".format(error.code, detail)
    except urllib.error.URLError as error:
        return False, "Slack に接続できません: {}".format(error.reason)
    except Exception as error:  # noqa: BLE001 - 通知失敗で本処理を止めない
        return False, "送信に失敗しました: {}".format(error)


def post_async(text, project_id=None):
    if not available():
        return
    threading.Thread(target=_send, args=(text, project_id), daemon=True).start()


def _send(text, project_id):
    try:
        ok, message = post(text, project_id=project_id)
        if not ok:
            log.warning("slack post failed: %s", message)
    finally:
        db.close_thread_connection()


def check(webhook_url=None):
    return post("✅ タスク管理システムからのテスト通知です。", webhook_url=webhook_url)
