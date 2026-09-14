"""日本の祝日と、会社ごとの休業日。

祝日は外部サービスに問い合わせず、その場で計算する（社内に閉じた構成のままにするため）。
「国民の祝日に関する法律」に沿って、振替休日と国民の休日まで含めて出す。
会社独自の休み（年末年始・夏季休暇など）は company_holidays テーブルで足せる。
"""
from datetime import date, timedelta
from functools import lru_cache

from . import db

# 春分・秋分は年によって動くので、近似式（1980〜2099 年の範囲で一致する）を使う。
_EQUINOX = {"vernal": 20.8431, "autumnal": 23.2488}
_EQUINOX_ORIGIN = 1980


def _equinox_day(year, kind):
    elapsed = year - _EQUINOX_ORIGIN
    return int(_EQUINOX[kind] + 0.242194 * elapsed - elapsed // 4)


def _nth_monday(year, month, nth):
    first = date(year, month, 1)
    offset = (7 - first.weekday()) % 7          # 最初の月曜まで
    return first + timedelta(days=offset + 7 * (nth - 1))


def national_holidays(year):
    """その年の祝日を {date: 名前} で返す（振替休日・国民の休日を含む）。"""
    days = {
        date(year, 1, 1): "元日",
        _nth_monday(year, 1, 2): "成人の日",
        date(year, 2, 11): "建国記念の日",
        date(year, 3, _equinox_day(year, "vernal")): "春分の日",
        date(year, 4, 29): "昭和の日",
        date(year, 5, 3): "憲法記念日",
        date(year, 5, 4): "みどりの日",
        date(year, 5, 5): "こどもの日",
        _nth_monday(year, 7, 3): "海の日",
        date(year, 8, 11): "山の日",
        _nth_monday(year, 9, 3): "敬老の日",
        date(year, 9, _equinox_day(year, "autumnal")): "秋分の日",
        _nth_monday(year, 10, 2): "スポーツの日" if year >= 2020 else "体育の日",
        date(year, 11, 3): "文化の日",
        date(year, 11, 23): "勤労感謝の日",
    }
    if year >= 2020:
        days[date(year, 2, 23)] = "天皇誕生日"
    else:
        days[date(year, 12, 23)] = "天皇誕生日"

    # 振替休日: 祝日が日曜なら、その後の最初の平日
    for day in sorted(days):
        if day.weekday() != 6:
            continue
        moved = day + timedelta(days=1)
        while moved in days:
            moved += timedelta(days=1)
        days[moved] = "振替休日"

    # 国民の休日: 祝日に挟まれた平日（敬老の日と秋分の日の間など）
    for day in sorted(list(days)):
        candidate = day + timedelta(days=1)
        if candidate in days or candidate.weekday() == 6:
            continue
        if candidate + timedelta(days=1) in days:
            days[candidate] = "国民の休日"
    return days


@lru_cache(maxsize=32)
def _national_cached(year):
    return national_holidays(year)


def company_holidays(start=None, end=None):
    """会社独自の休業日。{date: 名前}。"""
    sql = "SELECT day, name FROM company_holidays"
    params = []
    if start and end:
        sql += " WHERE day BETWEEN %s AND %s"
        params = [start, end]
    return {r["day"]: r["name"] for r in db.query(sql, params)}


def holidays_between(start, end):
    """期間内の休日をまとめて返す。{date: 名前}。"""
    if end < start:
        start, end = end, start
    out = {}
    for year in range(start.year, end.year + 1):
        for day, name in _national_cached(year).items():
            if start <= day <= end:
                out[day] = name
    out.update({d: n for d, n in company_holidays(start, end).items() if start <= d <= end})
    return out


def is_holiday(day, extra=None):
    """土日または祝日・会社休業日か。extra を渡すと問い合わせを省ける。"""
    if day.weekday() >= 5:
        return True
    if extra is not None:
        return day in extra
    return day in _national_cached(day.year) or day in company_holidays(day, day)


def business_days(start, end):
    """start〜end（両端含む）の営業日数。"""
    if end < start:
        return 0
    extra = holidays_between(start, end)
    count, cursor = 0, start
    while cursor <= end:
        if not is_holiday(cursor, extra):
            count += 1
        cursor += timedelta(days=1)
    return count


def add_business_days(start, days):
    """営業日で数えて days 日後（days が負なら前）。"""
    if days == 0:
        return start
    step = 1 if days > 0 else -1
    remaining = abs(days)
    cursor = start
    guard = 0
    while remaining and guard < 2000:
        cursor += timedelta(days=step)
        guard += 1
        if not is_holiday(cursor):
            remaining -= 1
    return cursor


def clear_cache():
    _national_cached.cache_clear()
