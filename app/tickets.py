"""チケット（受付窓口に届く依頼・問い合わせ・障害報告）の定義。

プロジェクト管理とは別建てにしている。プロジェクトに属さないところへ
飛んでくる話を受けて、作業が要るものだけをタスクへ渡すのが役目。

タスクの状態やカテゴリと違い、こちらはキーも表示名もコード側で固定する。
受付の流れは部署ごとに変わるものではないうえ、ここを可変にすると
「未対応のまま何日」といった集計が崩れるため。
"""
from . import db

# value, 表示名, 記号
KINDS = [
    ("request", "依頼", "📋"),
    ("question", "問い合わせ", "💬"),
    ("incident", "障害", "🚨"),
]
KIND_VALUES = {k for k, _l, _i in KINDS}
KIND_LABEL = {k: l for k, l, _i in KINDS}
KIND_ICON = {k: i for k, _l, i in KINDS}
DEFAULT_KIND = "request"

# value, 表示名, 色
STATUSES = [
    ("new", "受付待ち", "#e8912b"),
    ("doing", "対応中", "#3b6ef5"),
    ("pending", "保留", "#98a2b3"),
    ("done", "完了", "#17a673"),
    ("canceled", "取り下げ", "#a1a1aa"),
]
STATUS_VALUES = {s for s, _l, _c in STATUSES}
STATUS_LABEL = {s: l for s, l, _c in STATUSES}
STATUS_COLOR = {s: c for s, _l, c in STATUSES}
DEFAULT_STATUS = "new"
# まだ手が離れていない状態。一覧の既定の絞り込みと、滞留の集計に使う。
OPEN_STATUSES = ("new", "doing", "pending")
CLOSED_STATUSES = ("done", "canceled")

PRIORITY_LABEL = {0: "低", 1: "中", 2: "高", 3: "緊急"}

# 窓口の記号。直接入力だと環境によって打てないので、この中から選んでもらう。
QUEUE_ICONS = [
    "📮", "📥", "🖥", "🛠", "🏢", "💼", "💰", "📞", "🤝", "🧾",
    "🔐", "🚚", "🧪", "🚀", "📚", "🩺", "🧹", "⚙️", "🗂", "🌐",
    "👥", "📊", "🎓", "🏗", "🔧", "📋", "💬", "🚨", "🎫", "🧭",
]

DEFAULT_QUEUE = ("総合受付", "どこに出すか迷うものはここへ。窓口は管理画面で増やせます。",
                 "#3b6ef5", "📮")


def seed():
    """初回起動時に窓口をひとつ作る。すでにあれば何もしない。"""
    if db.scalar("SELECT COUNT(*) AS c FROM ticket_queues", default=0):
        return
    name, description, color, icon = DEFAULT_QUEUE
    db.execute(
        "INSERT IGNORE INTO ticket_queues(name, description, color, icon, sort_order, "
        "is_active, created_at) VALUES(%s,%s,%s,%s,10,1,%s)",
        (name, description, color, icon, db.now()))


def kind(value, fallback=DEFAULT_KIND):
    value = (value or "").strip()
    return value if value in KIND_VALUES else fallback


def status(value, fallback=DEFAULT_STATUS):
    value = (value or "").strip()
    return value if value in STATUS_VALUES else fallback


def is_open(value):
    return value in OPEN_STATUSES


def meta():
    """画面に渡す選択肢。"""
    return {
        "kinds": [{"value": v, "label": l, "icon": i} for v, l, i in KINDS],
        "statuses": [{"value": v, "label": l, "color": c} for v, l, c in STATUSES],
        "priorities": [{"value": v, "label": l}
                       for v, l in sorted(PRIORITY_LABEL.items(), reverse=True)],
        "open_statuses": list(OPEN_STATUSES),
        "queue_icons": list(QUEUE_ICONS),
    }
