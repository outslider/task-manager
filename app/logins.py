"""ログイン履歴。管理者が「誰が・いつ・どこから」入ったかを確かめるためのもの。

記録するのは、ログインの成功と失敗・ログアウト・パスワードの変更と再発行・
パスワード再設定の依頼と再設定・多要素認証の設定と解除。
失敗は理由（パスワード違い・確認コード違い・停止中のアカウント・登録のないアドレス）も残す。

登録のないアドレスで失敗したときは、入力された文字をそのまま残さない。
メール欄にパスワードを打ってしまうことがあるため、@ の前は 1 文字だけにして伏せる。
"""
import re
from datetime import timedelta

from . import db

# 何日ぶん残すか。過ぎたものは日次バッチで消す
KEEP_DAYS = 365

EVENT_LABEL = {
    "login": "ログイン",
    "failed": "ログイン失敗",
    "logout": "ログアウト",
    "password": "パスワード変更",
    "reset": "パスワード再発行",
    "recovery": "パスワード再設定の依頼",
    "recovered": "パスワード再設定",
    "mfa_on": "多要素認証の設定",
    "mfa_off": "多要素認証の解除",
    "mfa_reset": "多要素認証のリセット",
    "mfa_codes": "予備コードの作り直し",
    "created": "アカウント作成",
}
# ログイン以外の、アカウントの守りに関わる出来事（履歴の「セキュリティ」の絞り込み）
SECURITY_EVENTS = ("password", "reset", "recovery", "recovered",
                   "mfa_on", "mfa_off", "mfa_reset", "mfa_codes", "created")
REASON_LABEL = {
    "bad_password": "パスワード違い",
    "inactive": "停止中のアカウント",
    "unknown": "登録のないメールアドレス",
    "expired": "有効期限切れのアカウント",
    "bad_mfa": "確認コード違い",
    "mfa": "多要素認証",
    "recovery_code": "予備コードを使用",
    "mail": "メールで案内",
    "admin": "管理者へ依頼",
    "limited": "回数の上限で受け付けず",
}


def mask_email(value):
    """登録のないアドレスを伏せて残す。t***@example.co.jp の形。"""
    value = str(value or "").strip()
    if "@" not in value:
        return "（メールアドレスではない入力）"
    local, _, domain = value.rpartition("@")
    if not local or not domain:
        return "（メールアドレスではない入力）"
    return "{}***@{}".format(local[0], domain[:100])


def record(event, user=None, reason="", ip="", user_agent="", label=None):
    """1 件残す。履歴が残せなくても、ログインそのものは止めない。"""
    try:
        db.insert(
            "INSERT INTO login_events(user_id, user_label, event, reason, ip, user_agent, "
            "created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (user["id"] if user else None,
             (label if label is not None else (user or {}).get("name", ""))[:200],
             event, reason, str(ip or "")[:64], str(user_agent or "")[:300], db.now()))
    except Exception:  # noqa: BLE001 - 記録の失敗でログインを妨げない
        pass


def device_label(user_agent):
    """「Chrome / Windows」のような短い表示。細かい版数までは要らない。"""
    ua = str(user_agent or "")
    if not ua:
        return "不明"
    browser = "その他"
    for pattern, name in ((r"Edg/", "Edge"), (r"OPR/|Opera", "Opera"),
                          (r"Firefox/", "Firefox"), (r"Chrome/|CriOS/", "Chrome"),
                          (r"Safari/", "Safari"), (r"curl/", "curl"),
                          (r"python", "Python")):
        if re.search(pattern, ua, re.IGNORECASE):
            browser = name
            break
    system = ""
    for pattern, name in ((r"iPhone|iPad|iPod", "iOS"), (r"Android", "Android"),
                          (r"Windows", "Windows"), (r"Mac OS X|Macintosh", "Mac"),
                          (r"CrOS", "ChromeOS"), (r"Linux", "Linux")):
        if re.search(pattern, ua):
            system = name
            break
    return "{} / {}".format(browser, system) if system else browser


def row_out(row):
    return {
        "id": row["id"],
        "created_at": row["created_at"].isoformat(sep=" ", timespec="seconds"),
        "user_id": row["user_id"],
        "user_label": row["user_label"],
        "event": row["event"],
        "event_label": EVENT_LABEL.get(row["event"], row["event"]),
        "reason": row["reason"],
        "reason_label": REASON_LABEL.get(row["reason"], ""),
        "ip": row["ip"],
        "device": device_label(row["user_agent"]),
        "user_agent": row["user_agent"],
    }


def purge_expired():
    """残す日数を過ぎた履歴を消す。戻り値は件数。"""
    return db.execute("DELETE FROM login_events WHERE created_at < %s",
                      (db.now() - timedelta(days=KEEP_DAYS),))
