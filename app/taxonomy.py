"""状態とカテゴリの定義。画面から編集できるようにデータベースに置く。

状態は「未完了か完了か」の判定に使っているため、**キーは固定**して名前と色だけ
変えられるようにしている（勝手に増やせると、期限超過の集計や進捗のロールアップが
崩れるため）。カテゴリは見出しにすぎないので、追加も削除も自由にできる。
"""
import threading

from . import db

# キー, 既定の表示名, 既定の色。キーはコードが判定に使うので変えられない。
DEFAULT_STATUSES = [
    ("todo", "未着手", "#98a2b3"),
    ("doing", "進行中", "#3b6ef5"),
    ("review", "レビュー中", "#9061f9"),
    ("done", "完了", "#17a673"),
    ("blocked", "ブロック中", "#e14c4c"),
]
STATUS_KEYS = [key for key, _l, _c in DEFAULT_STATUSES]
DONE_STATUS = "done"
OPEN_STATUS_KEYS = tuple(k for k in STATUS_KEYS if k != DONE_STATUS)

DEFAULT_CATEGORIES = [
    ("research", "調査・リサーチ", "#6366f1", "🔍"),
    ("design", "設計・企画", "#8b5cf6", "✏️"),
    ("build", "実装・構築", "#3b6ef5", "🔧"),
    ("docs", "ドキュメント作成", "#0ea5e9", "📄"),
    ("meeting", "会議・打ち合わせ", "#14b8a6", "👥"),
    ("admin", "事務・申請系", "#a1a1aa", "📋"),
    ("incident", "トラブル対応・障害対応", "#ef4444", "🚨"),
]

# 状態の配色。ばらばらに選ぶより、まとまりで選べたほうが揃いやすい。
STATUS_PALETTES = [
    {"key": "default", "label": "標準",
     "colors": {"todo": "#98a2b3", "doing": "#3b6ef5", "review": "#9061f9",
                "done": "#17a673", "blocked": "#e14c4c"}},
    {"key": "calm", "label": "落ち着いた色",
     "colors": {"todo": "#9aa5b1", "doing": "#4c7ea8", "review": "#7c8aa5",
                "done": "#5c9c7f", "blocked": "#b5726e"}},
    {"key": "vivid", "label": "はっきりした色",
     "colors": {"todo": "#94a3b8", "doing": "#2563eb", "review": "#a855f7",
                "done": "#16a34a", "blocked": "#dc2626"}},
    {"key": "warm", "label": "暖色より",
     "colors": {"todo": "#a8a29e", "doing": "#ea7317", "review": "#c2410c",
                "done": "#65a30d", "blocked": "#be123c"}},
    {"key": "mono", "label": "単色（濃淡）",
     "colors": {"todo": "#cbd5e1", "doing": "#64748b", "review": "#475569",
                "done": "#1e293b", "blocked": "#0f172a"}},
]

# カテゴリの記号。自由入力にすると見た目がばらつくので、ここから選んでもらう。
ICON_CHOICES = [
    "🔍", "✏️", "🔧", "📄", "👥", "📋", "🚨", "💡", "📊", "🎯",
    "🧪", "🛠", "📦", "🚀", "🔐", "🗂", "📝", "💬", "📞", "🧭",
    "🏗", "🧹", "💰", "📈", "🤝", "⚖️", "🔁", "🎓", "🖥", "📮",
]

_lock = threading.Lock()
_cache = {"statuses": None, "categories": None}


def seed():
    """初回起動時に既定値を入れる。すでにあれば何もしない。"""
    if db.scalar("SELECT COUNT(*) AS c FROM task_statuses", default=0) == 0:
        for order, (key, label, color) in enumerate(DEFAULT_STATUSES):
            db.execute(
                "INSERT IGNORE INTO task_statuses(status_key, label, color, sort_order) "
                "VALUES(%s,%s,%s,%s)", (key, label, color, (order + 1) * 10))
    if db.scalar("SELECT COUNT(*) AS c FROM task_categories", default=0) == 0:
        for order, (value, label, color, icon) in enumerate(DEFAULT_CATEGORIES):
            db.execute(
                "INSERT IGNORE INTO task_categories(value, label, color, icon, sort_order) "
                "VALUES(%s,%s,%s,%s,%s)", (value, label, color, icon, (order + 1) * 10))
    invalidate()


def invalidate():
    with _lock:
        _cache["statuses"] = None
        _cache["categories"] = None


def statuses():
    """[{value,label,color}] を並び順で返す。未登録のキーは既定値で補う。"""
    if _cache["statuses"] is None:
        rows = {r["status_key"]: r for r in db.query(
            "SELECT status_key, label, color, sort_order FROM task_statuses")}
        out = []
        for order, (key, label, color) in enumerate(DEFAULT_STATUSES):
            row = rows.get(key)
            out.append({
                "value": key,
                "label": (row or {}).get("label") or label,
                "color": (row or {}).get("color") or color,
                "sort_order": (row or {}).get("sort_order", (order + 1) * 10),
            })
        out.sort(key=lambda s: s["sort_order"])
        with _lock:
            _cache["statuses"] = out
    return _cache["statuses"]


def categories():
    if _cache["categories"] is None:
        rows = db.query(
            "SELECT value, label, color, icon FROM task_categories ORDER BY sort_order, value")
        with _lock:
            _cache["categories"] = [dict(r) for r in rows]
    return _cache["categories"]


def status_label(key):
    for row in statuses():
        if row["value"] == key:
            return row["label"]
    return key


def status_labels():
    return {row["value"]: row["label"] for row in statuses()}


def category_values():
    return {row["value"] for row in categories()}


def category_label(value):
    for row in categories():
        if row["value"] == value:
            return row["label"]
    return value or "未分類"


def save_statuses(items):
    """名前・色・並び順だけを更新する。キーの追加や削除は受け付けない。"""
    order = 0
    for item in items or []:
        key = str(item.get("value") or "").strip()
        if key not in STATUS_KEYS:
            continue
        order += 10
        db.execute(
            "INSERT INTO task_statuses(status_key, label, color, sort_order) "
            "VALUES(%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
            "label=VALUES(label), color=VALUES(color), sort_order=VALUES(sort_order)",
            (key, (str(item.get("label") or "").strip() or key)[:40],
             normalize_color(item.get("color")), order))
    invalidate()
    return statuses()


def save_categories(items):
    """一覧をまるごと置き換える。消えたカテゴリのタスクは未分類に戻す。"""
    seen, order = [], 0
    for item in items or []:
        value = slugify(item.get("value"), item.get("label"))
        if not value or value in seen:
            continue
        order += 10
        seen.append(value)
        db.execute(
            "INSERT INTO task_categories(value, label, color, icon, sort_order) "
            "VALUES(%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
            "label=VALUES(label), color=VALUES(color), icon=VALUES(icon), "
            "sort_order=VALUES(sort_order)",
            (value, (str(item.get("label") or "").strip() or value)[:60],
             normalize_color(item.get("color")),
             normalize_icon(item.get("icon")), order))
    removed = [r["value"] for r in db.query("SELECT value FROM task_categories")
               if r["value"] not in seen]
    if removed:
        db.execute("DELETE FROM task_categories WHERE value IN %s", (tuple(removed),))
        # 使われていたタスクは未分類に戻す（存在しない分類が残らないように）
        db.execute("UPDATE tasks SET category='' WHERE category IN %s", (tuple(removed),))
    invalidate()
    return {"categories": categories(), "removed": removed}


def slugify(value, label=None):
    """英数字のキーを作る。日本語しか無い場合は連番で代用する。"""
    import re
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_-]+", "-", text).strip("-")
    if text:
        return text[:30]
    base = re.sub(r"[^a-z0-9_-]+", "-", str(label or "").strip().lower()).strip("-")
    if base:
        return base[:30]
    return "cat-{}".format(abs(hash(str(label))) % 100000)


def normalize_color(value):
    import re
    text = str(value or "").strip()
    return text if re.match(r"^#[0-9a-fA-F]{6}$", text) else "#98a2b3"


def normalize_icon(value):
    text = str(value or "").strip()
    return text[:8] if text in ICON_CHOICES else (ICON_CHOICES[0] if not text else text[:8])
