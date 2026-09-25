"""多要素認証（認証アプリの 6 桁コード）と、ログイン・パスワード再設定の合言葉。

認証アプリは Google Authenticator・Microsoft Authenticator・1Password など、
TOTP（RFC 6238：30 秒ごとに変わる 6 桁）に対応したものなら何でもよい。

- 鍵はサーバーと認証アプリだけが持つ。画面に出すのは設定のときの一度だけ
- 同じコードは二度通さない（last_step より前の区切りは受け付けない）
- スマホをなくしたときのために、使い切りの予備コードを 10 個渡す（ハッシュだけを持つ）
- パスワードの次のコード待ちや、パスワード再設定のリンクは「合言葉」で結ぶ。
  合言葉はハッシュだけを DB に置き、期限と試行回数で締める
"""
import base64
import hashlib
import hmac
import io
import secrets
import struct
import time
from datetime import timedelta
from urllib.parse import quote

from . import db

STEP = 30            # 秒
DIGITS = 6
WINDOW = 1           # 前後 1 区切り（±30 秒）までの時計のずれは許す
RECOVERY_COUNT = 10

CHALLENGE_LIFE = {"mfa": timedelta(minutes=5), "reset": timedelta(minutes=60)}
CHALLENGE_TRIES = {"mfa": 5, "reset": 5}

REQUIRED_LABEL = {"off": "必須にしない", "admin": "管理者だけ必須", "all": "全員必須"}


# --------------------------------------------------------------------------
# TOTP
# --------------------------------------------------------------------------

def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret):
    padded = secret.upper() + "=" * (-len(secret) % 8)
    return base64.b32decode(padded)


def code_at(secret, step):
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10 ** DIGITS).zfill(DIGITS)


def current_step(now=None):
    return int((now if now is not None else time.time()) // STEP)


def match_step(secret, code, after_step=0, now=None):
    """合うコードの区切りを返す。合わない・使用済みなら None。"""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    base = current_step(now)
    for step in range(base - WINDOW, base + WINDOW + 1):
        if step > after_step and hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def otpauth_uri(secret, account, issuer):
    label = quote("{}:{}".format(issuer, account))
    return "otpauth://totp/{}?secret={}&issuer={}&algorithm=SHA1&digits={}&period={}".format(
        label, secret, quote(issuer), DIGITS, STEP)


def qr_svg(text):
    """認証アプリで読み取る QR コード（SVG の文字列）。segno が無ければ空（鍵の手入力で設定できる）。"""
    try:
        import segno
    except ImportError:
        return ""
    out = io.BytesIO()
    segno.make(text, error="m").save(out, kind="svg", scale=5, border=2, xmldecl=False,
                                     svgns=True, dark="#111111", light="#ffffff")
    return out.getvalue().decode("utf-8")


# --------------------------------------------------------------------------
# 利用者ごとの状態
# --------------------------------------------------------------------------

def status(user_id):
    row = db.query_one("SELECT enabled_at FROM user_mfa WHERE user_id=%s", (user_id,))
    return bool(row and row["enabled_at"])


def required_for(user):
    """このユーザーに多要素認証が必須か。社外ユーザーも「全員」に含める。"""
    setting = db.get_setting("mfa_required", "off")
    if setting == "all":
        return True
    return setting == "admin" and bool(user) and user.get("role") == "admin"


def start_setup(user_id):
    """新しい鍵を作って「設定の途中」として置く。前の途中のものは捨てる。
    すでに有効なら None（スマホを替えるときは、いったん解除してから設定し直す）。"""
    secret = new_secret()
    db.execute(
        "INSERT INTO user_mfa(user_id, secret, enabled_at, last_step, created_at) "
        "VALUES(%s,%s,NULL,0,%s) ON DUPLICATE KEY UPDATE "
        "secret=IF(enabled_at IS NULL, VALUES(secret), secret), created_at=VALUES(created_at)",
        (user_id, secret, db.now()))
    row = db.query_one("SELECT secret, enabled_at FROM user_mfa WHERE user_id=%s", (user_id,))
    if row["enabled_at"]:
        return None
    return row["secret"]


def confirm_setup(user_id, code):
    """設定の途中の鍵でコードが合えば有効にする。予備コードを返す（合わなければ None）。"""
    row = db.query_one("SELECT secret, enabled_at FROM user_mfa WHERE user_id=%s", (user_id,))
    if not row or row["enabled_at"]:
        return None
    step = match_step(row["secret"], code)
    if step is None:
        return None
    db.execute("UPDATE user_mfa SET enabled_at=%s, last_step=%s WHERE user_id=%s",
               (db.now(), step, user_id))
    return new_recovery_codes(user_id)


def disable(user_id):
    db.execute("DELETE FROM user_mfa WHERE user_id=%s", (user_id,))
    db.execute("DELETE FROM mfa_recovery_codes WHERE user_id=%s", (user_id,))


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normal_recovery(code):
    return "".join(ch for ch in str(code or "").lower() if ch.isalnum())


def new_recovery_codes(user_id):
    """予備コードを作り直す。前のものは使えなくなる。xxxx-xxxx の形で返す。"""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"   # 見間違えやすい文字（0 o 1 l i）は使わない
    codes = []
    for _ in range(RECOVERY_COUNT):
        raw = "".join(secrets.choice(alphabet) for _ in range(8))
        codes.append("{}-{}".format(raw[:4], raw[4:]))
    db.execute("DELETE FROM mfa_recovery_codes WHERE user_id=%s", (user_id,))
    db.executemany("INSERT INTO mfa_recovery_codes(user_id, code_hash) VALUES(%s,%s)",
                   [(user_id, _hash(_normal_recovery(c))) for c in codes])
    return codes


def remaining_codes(user_id):
    return db.scalar("SELECT COUNT(*) AS c FROM mfa_recovery_codes "
                     "WHERE user_id=%s AND used_at IS NULL", (user_id,), default=0)


def verify(user_id, code):
    """ログインのときの確認。'totp'・'recovery'（予備コードを 1 つ使った）・None を返す。"""
    row = db.query_one("SELECT secret, last_step FROM user_mfa "
                       "WHERE user_id=%s AND enabled_at IS NOT NULL", (user_id,))
    if not row:
        return None
    text = str(code or "").strip()
    if any(ch.isalpha() for ch in text):
        normal = _normal_recovery(text)
        if len(normal) != 8:
            return None
        used = db.execute("UPDATE mfa_recovery_codes SET used_at=%s WHERE user_id=%s "
                          "AND code_hash=%s AND used_at IS NULL LIMIT 1",
                          (db.now(), user_id, _hash(normal)))
        return "recovery" if used else None
    step = match_step(row["secret"], text, after_step=row["last_step"])
    if step is None:
        return None
    # 同時に 2 回通されても、区切りを進められるのは 1 回だけ
    if not db.execute("UPDATE user_mfa SET last_step=%s WHERE user_id=%s AND last_step<%s",
                      (step, user_id, step)):
        return None
    return "totp"


# --------------------------------------------------------------------------
# 合言葉（パスワードの次のコード待ち／パスワード再設定）
# --------------------------------------------------------------------------

def issue(user_id, purpose):
    token = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO auth_challenges(token_hash, user_id, purpose, created_at, expires_at) "
        "VALUES(%s,%s,%s,%s,%s)",
        (_hash(token), user_id, purpose, db.now(), db.now() + CHALLENGE_LIFE[purpose]))
    return token


def lookup(token, purpose):
    """有効な合言葉なら行を返す（期限切れ・使用済み・回数切れは None）。"""
    if not token:
        return None
    row = db.query_one("SELECT * FROM auth_challenges WHERE token_hash=%s AND purpose=%s",
                       (_hash(str(token)), purpose))
    if (not row or row["used_at"] or row["expires_at"] <= db.now()
            or row["attempts"] >= CHALLENGE_TRIES[purpose]):
        return None
    return row


def fail(row):
    """外れを 1 回数える。残りの回数を返す。"""
    db.execute("UPDATE auth_challenges SET attempts=attempts+1 WHERE token_hash=%s",
               (row["token_hash"],))
    return max(0, CHALLENGE_TRIES[row["purpose"]] - row["attempts"] - 1)


def consume(row):
    """使い切る。二重に使われたら False。"""
    return bool(db.execute("UPDATE auth_challenges SET used_at=%s "
                           "WHERE token_hash=%s AND used_at IS NULL",
                           (db.now(), row["token_hash"])))


def recent_count(user_id, purpose, within=timedelta(hours=1)):
    return db.scalar("SELECT COUNT(*) AS c FROM auth_challenges WHERE user_id=%s AND purpose=%s "
                     "AND created_at >= %s", (user_id, purpose, db.now() - within), default=0)


def forget_resets(user_id):
    """パスワードが変わったら、出ている再設定リンクはすべて使えなくする。"""
    db.execute("UPDATE auth_challenges SET used_at=%s WHERE user_id=%s AND purpose='reset' "
               "AND used_at IS NULL", (db.now(), user_id))


def purge_expired():
    return db.execute("DELETE FROM auth_challenges WHERE expires_at < %s",
                      (db.now() - timedelta(days=1),))
