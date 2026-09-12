"""繰り返しタスク。定例作業を自動で起票する。

「毎週月曜」「毎月25日」のような規則を持ち、日次バッチが期限の lead_days 日前に
実タスクを作る。作りすぎないよう、1回の実行につき規則ごとに最大1件しか作らない。
"""
import logging
from datetime import date, timedelta

from . import db

log = logging.getLogger("tm.recurrence")

FREQ_LABEL = {"daily": "毎日", "weekly": "毎週", "monthly": "毎月"}
WEEKDAY_LABEL = "月火水木金土日"
# 長期間止まっていた場合に、古い分をまとめて作らないための猶予
MAX_BACKFILL_DAYS = 30


def parse_weekdays(value):
    out = []
    for part in str(value or "").split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 6 and int(part) not in out:
            out.append(int(part))
    return sorted(out)


def _add_months(day, months):
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    last = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(day.day, last))


def next_date(rule, after):
    """after より後の、次の実施日。"""
    freq = rule["freq"]
    interval = max(1, int(rule.get("interval_n") or 1))
    if freq == "daily":
        return after + timedelta(days=interval)
    if freq == "weekly":
        weekdays = parse_weekdays(rule.get("weekdays")) or [after.weekday()]
        anchor = after - timedelta(days=after.weekday())
        for step in range(1, 7 * interval + 8):
            candidate = after + timedelta(days=step)
            if candidate.weekday() not in weekdays:
                continue
            week_offset = ((candidate - timedelta(days=candidate.weekday())) - anchor).days // 7
            if week_offset % interval == 0:
                return candidate
        return after + timedelta(weeks=interval)
    # monthly
    month_day = int(rule.get("month_day") or after.day)
    candidate = _add_months(after.replace(day=1), interval)
    last = _add_months(candidate, 1) - timedelta(days=1)
    return candidate.replace(day=min(month_day, last.day))


def describe(rule):
    """人が読める規則の説明。"""
    interval = max(1, int(rule.get("interval_n") or 1))
    if rule["freq"] == "daily":
        return "毎日" if interval == 1 else "{}日ごと".format(interval)
    if rule["freq"] == "weekly":
        days = "".join(WEEKDAY_LABEL[d] for d in parse_weekdays(rule.get("weekdays")))
        prefix = "毎週" if interval == 1 else "{}週ごと".format(interval)
        return "{} {}曜".format(prefix, days) if days else prefix
    prefix = "毎月" if interval == 1 else "{}ヶ月ごと".format(interval)
    return "{} {}日".format(prefix, rule.get("month_day") or "―")


def due_rules(today=None):
    today = today or db.today()
    return db.query(
        "SELECT r.*, p.archived FROM recurrences r JOIN projects p ON p.id = r.project_id "
        "WHERE r.active = 1 AND p.archived = 0 AND DATE_SUB(r.next_on, INTERVAL r.lead_days DAY) <= %s",
        (today,))


def run(today=None):
    """作るべき定例タスクを生成し、次回日を進める。戻り値は作成件数。"""
    today = today or db.today()
    created = 0
    for rule in due_rules(today):
        try:
            if (today - rule["next_on"]).days <= MAX_BACKFILL_DAYS:
                _create_task(rule, today)
                created += 1
            nxt = next_date(rule, rule["next_on"])
            # 長く止まっていた場合は未来になるまで飛ばす
            guard = 0
            while nxt <= today and guard < 200:
                nxt = next_date(rule, nxt)
                guard += 1
            db.execute(
                "UPDATE recurrences SET next_on=%s, last_created_on=%s, updated_at=%s WHERE id=%s",
                (nxt, today, db.now(), rule["id"]))
        except Exception as error:  # 1件の失敗で全体を止めない
            log.warning("recurrence %s failed: %s", rule["id"], error)
    return created


def _create_task(rule, today):
    now = db.now()
    order = (db.scalar("SELECT COALESCE(MAX(sort_order), 0) AS m FROM tasks WHERE project_id=%s",
                       (rule["project_id"],), default=0) or 0) + 10
    task_id = db.insert(
        "INSERT INTO tasks(project_id, parent_id, title, description, category, status, priority, "
        "assignee_id, start_date, due_date, progress, estimate_hours, is_milestone, sort_order, "
        "created_by, created_at, updated_at) "
        "VALUES(%s,%s,%s,%s,%s,'todo',%s,%s,%s,%s,0,%s,0,%s,%s,%s,%s)",
        (rule["project_id"], rule["parent_id"], rule["title"], rule["description"] or "",
         rule["category"], rule["priority"], rule["assignee_id"], today, rule["next_on"],
         rule["estimate_hours"], order, rule["created_by"], now, now))
    db.insert(
        "INSERT INTO comments(task_id, user_id, body, kind, created_at) VALUES(%s,%s,%s,'system',%s)",
        (task_id, rule["created_by"], "定例タスクとして自動作成（{}）".format(describe(rule)), now))
    if rule["assignee_id"]:
        from . import notify
        notify.create(
            rule["assignee_id"], "assigned", "定例タスク: {}".format(rule["title"]),
            "{}\n期限: {}\n{}".format(describe(rule), rule["next_on"], notify.task_url(task_id)),
            task_id=task_id)
    return task_id
