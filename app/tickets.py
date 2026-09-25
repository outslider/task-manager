"""チケット（受付窓口に届く依頼・問い合わせ・障害報告）の定義。

プロジェクト管理とは別建てにしている。プロジェクトに属さないところへ
飛んでくる話を受けて、作業が要るものだけをタスクへ渡すのが役目。

タスクの状態やカテゴリと違い、こちらはキーも表示名もコード側で固定する。
受付の流れは部署ごとに変わるものではないうえ、ここを可変にすると
「未対応のまま何日」といった集計が崩れるため。
"""
from . import auth, db

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


def ticket_visible_to(user, visibility, project_id, organization_id):
    """この人がこのチケットを読めるか。窓口の判定に、社外ユーザーだけ会社の判定を足す。

    社外ユーザーに見えるのは自分の会社のチケットだけ。同じプロジェクトに別の会社の
    人がいても、互いの問い合わせは見えない。
    """
    if not queue_visible_to(user, visibility, project_id):
        return False
    if auth.is_guest(user):
        return bool(organization_id) and organization_id == user.get("organization_id")
    return True


def queue_visible_to(user, visibility, project_id):
    """この人がこの窓口のチケットを読めるか。判断はここ 1 か所だけに置く。

    画面の問い合わせ（api.visible_queue_clause）と通知の関所（notify）が
    ここを使う。別々に書くと、片方だけ直って食い違うため。
    紐づけ先が消えた「メンバーだけ」の窓口は、管理者以外には閉じたままにする。
    """
    # 社外ユーザーは、参加しているプロジェクトにひもづいた窓口だけ（公開範囲の設定によらない）
    if auth.is_guest(user):
        return bool(project_id) and auth.has_project_access(user, project_id)
    if (visibility or "all") != "project":
        return True
    if auth.is_admin(user):
        return True
    return bool(project_id) and auth.has_project_access(user, project_id)
