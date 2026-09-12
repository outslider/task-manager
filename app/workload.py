"""担当者ごとの負荷（予定工数と件数）を週単位で集計する。

見積工数は任意項目なので、入っていない場合でも件数ベースで負荷が見えるように
両方を返す。期間が入っていないタスクは「未計画」として別に数える。
"""
from datetime import date, timedelta

OPEN_STATUSES = ("todo", "doing", "review", "blocked")


def week_start(day):
    return day - timedelta(days=day.weekday())


def business_days(start, end):
    """土日を除いた日付の一覧。両端を含む。"""
    days, cursor = [], start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days or [start]


def _as_date(value):
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def task_span(task):
    """タスクが占める営業日。期間が決まっていなければ None。"""
    start = _as_date(task.get("start_date"))
    due = _as_date(task.get("due_date"))
    if start and due and due >= start:
        return business_days(start, due)
    if due:
        return business_days(due, due)
    if start:
        return business_days(start, start)
    return None


def build(tasks, users, weeks=8, base=None, hours_per_day=8.0):
    """週ごと・担当者ごとの負荷表を組み立てる。

    tasks: id/assignee_id/status/start_date/due_date/estimate_hours/actual_hours/title
    users: [{"id","name","avatar_color"}]
    """
    base = base or date.today()
    first = week_start(base)
    buckets = [first + timedelta(weeks=i) for i in range(weeks)]
    index = {day: i for i, day in enumerate(buckets)}
    capacity = round(hours_per_day * 5, 1)

    rows = {}
    unscheduled = {}
    missing_estimate = 0
    total_open = 0

    def row_for(user_id):
        if user_id not in rows:
            rows[user_id] = {
                "user_id": user_id,
                "cells": [{"hours": 0.0, "count": 0, "tasks": []} for _ in buckets],
                "total_hours": 0.0, "total_count": 0,
            }
        return rows[user_id]

    for task in tasks:
        if task.get("status") not in OPEN_STATUSES:
            continue
        total_open += 1
        assignee = task.get("assignee_id")
        estimate = task.get("estimate_hours")
        estimate = float(estimate) if estimate not in (None, "") else None
        if estimate is None:
            missing_estimate += 1

        span = task_span(task)
        if span is None:
            entry = unscheduled.setdefault(assignee, {"user_id": assignee, "count": 0,
                                                      "hours": 0.0})
            entry["count"] += 1
            entry["hours"] += estimate or 0.0
            continue

        remaining = 1.0 - min(100, max(0, task.get("progress") or 0)) / 100.0
        per_day = (estimate * remaining / len(span)) if estimate else 0.0
        row = row_for(assignee)
        counted_weeks = set()
        for day in span:
            bucket = index.get(week_start(day))
            if bucket is None:
                continue
            row["cells"][bucket]["hours"] += per_day
            row["total_hours"] += per_day
            if bucket not in counted_weeks:
                counted_weeks.add(bucket)
                row["cells"][bucket]["count"] += 1
                row["cells"][bucket]["tasks"].append(
                    {"id": task["id"], "title": task["title"]})
        if counted_weeks:
            row["total_count"] += 1

    by_id = {u["id"]: u for u in users}
    result = []
    for user_id, row in rows.items():
        person = by_id.get(user_id)
        for cell in row["cells"]:
            cell["hours"] = round(cell["hours"], 1)
            cell["ratio"] = round(cell["hours"] / capacity, 2) if capacity else 0
            cell["tasks"] = cell["tasks"][:12]
        result.append({
            **row,
            "name": person["name"] if person else "未割当",
            "avatar_color": person["avatar_color"] if person else "#98a2b3",
            "total_hours": round(row["total_hours"], 1),
            "peak_ratio": max((c["ratio"] for c in row["cells"]), default=0),
        })
    # 未割当を最後に、それ以外は負荷の高い順
    result.sort(key=lambda r: (r["user_id"] is None, -r["total_hours"], -r["total_count"]))

    return {
        "weeks": [{"start": day.isoformat(),
                   "label": "{}/{}".format(day.month, day.day),
                   "is_current": day == first} for day in buckets],
        "rows": result,
        "unscheduled": [
            {**entry, "hours": round(entry["hours"], 1),
             "name": by_id[entry["user_id"]]["name"] if entry["user_id"] in by_id else "未割当"}
            for entry in sorted(unscheduled.values(), key=lambda e: -e["count"])],
        "capacity_per_week": capacity,
        "hours_per_day": hours_per_day,
        "missing_estimate": missing_estimate,
        "open_tasks": total_open,
        "has_estimates": missing_estimate < total_open,
    }


def effort_summary(tasks):
    """見積と実績の突き合わせ。工数が入っているタスクだけを対象にする。"""
    estimated = actual = 0.0
    done_estimated = done_actual = 0.0
    tracked = 0
    for task in tasks:
        estimate = task.get("estimate_hours")
        actual_hours = float(task.get("actual_hours") or 0)
        if estimate in (None, "") and not actual_hours:
            continue
        tracked += 1
        estimate = float(estimate or 0)
        estimated += estimate
        actual += actual_hours
        if task.get("status") == "done":
            done_estimated += estimate
            done_actual += actual_hours
    ratio = round(done_actual / done_estimated, 2) if done_estimated else None
    return {
        "tracked": tracked,
        "estimated": round(estimated, 1),
        "actual": round(actual, 1),
        "done_estimated": round(done_estimated, 1),
        "done_actual": round(done_actual, 1),
        "accuracy": ratio,
    }
