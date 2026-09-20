"""Small HTTP helpers: JSON bodies, multipart uploads, cookies, responses."""
import json
import re
from http import cookies as http_cookies

from . import db
from .config import COOKIE_PATH


class HttpError(Exception):
    def __init__(self, status, message, detail=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail


def bad_request(msg="不正なリクエストです", detail=None):
    return HttpError(400, msg, detail)


def unauthorized(msg="ログインが必要です"):
    return HttpError(401, msg)


def forbidden(msg="権限がありません"):
    return HttpError(403, msg)


def not_found(msg="見つかりません"):
    return HttpError(404, msg)


class Response:
    def __init__(self, status=200, body=b"", content_type="application/json; charset=utf-8",
                 headers=None):
        self.status = status
        self.body = body
        self.content_type = content_type
        self.headers = headers or []

    def add_cookie(self, name, value, max_age=None, secure=False, http_only=True,
                   same_site="Lax", path=None):
        path = path or COOKIE_PATH
        parts = ["{}={}".format(name, value), "Path=" + path]
        if max_age is not None:
            parts.append("Max-Age={}".format(max_age))
        if http_only:
            parts.append("HttpOnly")
        if secure:
            parts.append("Secure")
        parts.append("SameSite=" + same_site)
        self.headers.append(("Set-Cookie", "; ".join(parts)))
        return self


def redirect(location, status=302):
    return Response(status, b"", "text/plain; charset=utf-8", [("Location", location)])


def json_response(data, status=200):
    payload = json.dumps(db.jsonable(data), ensure_ascii=False, default=str)
    return Response(status, payload.encode("utf-8"))


def error_response(status, message, detail=None):
    body = {"error": message}
    if detail:
        body["detail"] = detail
    return json_response(body, status)


def parse_cookies(header):
    jar = http_cookies.SimpleCookie()
    if header:
        try:
            jar.load(header)
        except http_cookies.CookieError:
            return {}
    return {k: v.value for k, v in jar.items()}


def parse_json_body(raw):
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise bad_request("JSON の解析に失敗しました")
    if not isinstance(data, dict):
        raise bad_request("JSON オブジェクトを指定してください")
    return data


# --------------------------------------------------------------------------
# multipart/form-data
# --------------------------------------------------------------------------

_DISPOSITION_RE = re.compile(r'(\w+)="([^"]*)"')


class UploadedFile:
    def __init__(self, filename, content_type, data):
        self.filename = filename
        self.content_type = content_type or "application/octet-stream"
        self.data = data

    @property
    def size(self):
        return len(self.data)


def parse_multipart(raw, content_type):
    """Return (fields, files) from a multipart/form-data body."""
    match = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not match:
        raise bad_request("multipart の boundary がありません")
    boundary = ("--" + match.group(1)).encode("latin-1")

    fields, files = {}, {}
    for chunk in raw.split(boundary):
        if chunk in (b"", b"--", b"--\r\n", b"\r\n"):
            continue
        chunk = chunk.lstrip(b"\r\n")
        if chunk.startswith(b"--"):
            continue
        head, _, body = chunk.partition(b"\r\n\r\n")
        if not _:
            continue
        body = body[:-2] if body.endswith(b"\r\n") else body

        name = filename = None
        part_type = ""
        for line in head.decode("utf-8", "replace").split("\r\n"):
            lower = line.lower()
            if lower.startswith("content-disposition:"):
                for key, value in _DISPOSITION_RE.findall(line):
                    if key == "name":
                        name = value
                    elif key == "filename":
                        filename = value
            elif lower.startswith("content-type:"):
                part_type = line.split(":", 1)[1].strip()
        if name is None:
            continue
        if filename is not None:
            files[name] = UploadedFile(filename, part_type, body)
        else:
            fields[name] = body.decode("utf-8", "replace")
    return fields, files


# --------------------------------------------------------------------------
# value coercion
# --------------------------------------------------------------------------

def as_int(value, default=None, minimum=None, maximum=None):
    if value in (None, "", "null"):
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        n = max(minimum, n)
    if maximum is not None:
        n = min(maximum, n)
    return n


def as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "yes", "on")


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?$")


def as_date(value):
    """Accept 'YYYY-MM-DD' or empty; returns the string or None."""
    if value in (None, "", "null"):
        return None
    value = str(value)[:10]
    if not _DATE_RE.match(value):
        raise bad_request("日付は YYYY-MM-DD 形式で指定してください: " + value)
    return value


def as_datetime(value):
    """'YYYY-MM-DDTHH:MM' / 'YYYY-MM-DD HH:MM(:SS)' / 空 を受ける。戻り値は文字列か None。

    障害の発生日時のように、日付だけでは足りない項目に使う。
    """
    if value in (None, "", "null"):
        return None
    text = str(value).strip().replace("T", " ")
    if _DATE_RE.match(text[:10]) and len(text) == 10:
        return text + " 00:00:00"
    if not _DATETIME_RE.match(text):
        raise bad_request("日時は YYYY-MM-DD HH:MM 形式で指定してください: " + str(value))
    return text if len(text) > 16 else text + ":00"


def require(data, key, label=None):
    value = data.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise bad_request("{} は必須です".format(label or key))
    return value.strip() if isinstance(value, str) else value
