"""定例会議。ガントの 1 行に、開催日を点で並べるためのもの。

会議を 1 回ずつタスクにすると、週次の定例だけで 1 年に 52 行になり、
ガントが会議で埋まってしまう。ここでは「毎週火曜」のような決まりだけを持ち、
開催日は見る期間のぶんだけその場で数える。先の予定まで出るのはそのため。

1 回ごとの中止・日にち変更は、例外として別に持つ。例外の鍵は「その回の
予定日」（休日でずらしたあとの日）で、画面で点を押した日と一致する。
決まりを後から変えて予定日が消えた例外は、ただ効かなくなるだけにしてある。
"""
from datetime import date, timedelta

from . import db, holidays

FREQ_LABEL = {"weekly": "毎週", "monthly": "毎月"}
WEEKDAY_LABEL = "月火水木金土日"
HOLIDAY_RULES = {
    "skip": "その回は休み",
    "next": "翌営業日にずらす",
    "prev": "前営業日にずらす",
    "keep": "そのまま",
}
# 休日でずらすときに、どこまで先（前）を探すか
SHIFT_LIMIT = 14
# 一度に数える期間の上限。全体ガントで数年分を並べても重くならない程度
MAX_SPAN_DAYS = 1500


def parse_weekdays(value):
    out = set()
    for part in str(value or "").split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 6:
            out.add(int(part))
    return sorted(out)


def _month_index(day):
    return day.year * 12 + day.month - 1


def _last_day(year, month):
    nxt = date(year + month // 12, month % 12 + 1, 1)
    return (nxt - timedelta(days=1)).day


def _nth_weekday(year, month, nth, weekday):
    """その月の第 nth weekday。nth が -1 なら最終。無い月（第5など）は None。"""
    if nth == -1:
        last = date(year, month, _last_day(year, month))
        return last - timedelta(days=(last.weekday() - weekday) % 7)
    first = date(year, month, 1)
    day = first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (nth - 1))
    return day if day.month == month else None


def raw_dates(m, lo, hi):
    """休日を考えない、決まりどおりの開催日。lo〜hi と開始日〜終了日の両方に入るもの。"""
    start = m["start_on"]
    lo = max(lo, start)
    if m.get("end_on"):
        hi = min(hi, m["end_on"])
    if hi < lo:
        return []
    interval = max(1, int(m.get("interval_n") or 1))
    out = []
    if m["freq"] == "weekly":
        weekdays = parse_weekdays(m.get("weekdays")) or [start.weekday()]
        anchor = start - timedelta(days=start.weekday())
        day = lo
        while day <= hi:
            if day.weekday() in weekdays:
                weeks = ((day - timedelta(days=day.weekday())) - anchor).days // 7
                if weeks % interval == 0:
                    out.append(day)
            day += timedelta(days=1)
        return out
    # monthly
    base = _month_index(start)
    for index in range(_month_index(lo), _month_index(hi) + 1):
        if (index - base) % interval:
            continue
        year, month = divmod(index, 12)
        month += 1
        if m.get("month_mode") == "nth":
            day = _nth_weekday(year, month, int(m.get("nth") or 1),
                               int(m.get("nth_weekday") or 0))
        else:
            wanted = int(m.get("month_day") or start.day)
            day = date(year, month, min(wanted, _last_day(year, month)))
        if day and lo <= day <= hi:
            out.append(day)
    return out


def _shift(day, rule, is_off):
    """休日に当たった回の扱い。休みにするなら None。"""
    if rule == "keep" or not is_off(day):
        return day
    if rule == "skip":
        return None
    step = timedelta(days=1 if rule == "next" else -1)
    for _ in range(SHIFT_LIMIT):
        day += step
        if not is_off(day):
            return day
    return None


def scheduled(m, lo, hi, is_off):
    """休日の扱いまで済ませた予定日。[(決まりどおりの日, 予定日)] を日付順で。

    ずらした結果が期間に入ってくる回もあるので、少し広めに数えてから絞る。
    ずらした先が別の回と重なったら 1 回にまとめる。
    """
    pad = timedelta(days=SHIFT_LIMIT + 1)
    seen = {}
    for planned in raw_dates(m, lo - pad, hi + pad):
        day = _shift(planned, m.get("holiday_rule") or "next", is_off)
        if day and lo <= day <= hi and day not in seen:
            seen[day] = planned
    return sorted(((planned, day) for day, planned in seen.items()), key=lambda p: p[1])


def is_scheduled(m, day, is_off):
    return any(d == day for _, d in scheduled(m, day, day, is_off))


def occurrences(m, lo, hi, is_off, exceptions):
    """lo〜hi に並べる回。中止した回も「中止」として返す（消えると気づけないため）。

    exceptions は {予定日: 例外の行}。日にちを変えた回は、変えた先の日に出す。
    """
    out = []
    for planned, day in scheduled(m, lo, hi, is_off):
        ex = exceptions.get(day)
        if ex and ex["action"] == "move":
            continue                      # 変えた先のほうで出す
        out.append({
            "date": day.isoformat(), "planned": day.isoformat(),
            "shifted_from": planned.isoformat() if planned != day else None,
            "status": "cancelled" if ex else "normal",
            "note": ex["note"] if ex else "",
        })
    for day, ex in exceptions.items():
        moved = ex.get("moved_to")
        if ex["action"] != "move" or not moved or not lo <= moved <= hi:
            continue
        # 決まりを変えて元の回が無くなった例外は、もう効かせない
        if not is_scheduled(m, day, is_off):
            continue
        out.append({
            "date": moved.isoformat(), "planned": day.isoformat(),
            "shifted_from": None, "status": "moved", "note": ex["note"],
        })
    out.sort(key=lambda o: (o["date"], o["planned"]))
    return out


def off_days(lo, hi):
    """lo〜hi（前後に余裕を持たせて）の休日判定。問い合わせは 1 回で済ませる。"""
    pad = timedelta(days=SHIFT_LIMIT + 2)
    # 祝日を使わない設定なら、土日だけを休みとして扱う（ガントの網掛けと揃える）
    extra = (holidays.holidays_between(lo - pad, hi + pad)
             if db.get_setting("use_holidays", "1") == "1" else {})
    return lambda day: holidays.is_holiday(day, extra)


def load_exceptions(meeting_ids):
    """{会議 id: {予定日: 例外}}。"""
    out = {}
    if not meeting_ids:
        return out
    for row in db.query(
            "SELECT meeting_id, on_date, action, moved_to, note FROM meeting_exceptions "
            "WHERE meeting_id IN %s", (tuple(meeting_ids),)):
        out.setdefault(row["meeting_id"], {})[row["on_date"]] = row
    return out


def describe(m):
    """「隔週 火・木 10:00」のような短い説明。"""
    interval = max(1, int(m.get("interval_n") or 1))
    if m["freq"] == "weekly":
        head = "毎週" if interval == 1 else ("隔週" if interval == 2 else
                                            "{}週ごと".format(interval))
        days = "・".join(WEEKDAY_LABEL[d] for d in parse_weekdays(m.get("weekdays")))
        text = "{} {}".format(head, days).strip()
    else:
        head = "毎月" if interval == 1 else "{}か月ごと".format(interval)
        if m.get("month_mode") == "nth":
            nth = int(m.get("nth") or 1)
            which = "最終" if nth == -1 else "第{}".format(nth)
            text = "{} {}{}曜".format(head, which, WEEKDAY_LABEL[int(m.get("nth_weekday") or 0)])
        else:
            text = "{} {}日".format(head, int(m.get("month_day") or 1))
    if m.get("time_text"):
        text += " " + m["time_text"]
    return text
