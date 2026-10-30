from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

NY = ZoneInfo("America/New_York")


@lru_cache(maxsize=8)
def calendar(year: int):
    return xcals.get_calendar("XNYS", start=f"{year - 3}-01-01", end=f"{year + 2}-12-31")


def session_bounds(day: date) -> tuple[datetime, datetime] | None:
    cal = calendar(day.year)
    label = pd.Timestamp(day)
    if not cal.is_session(label):
        return None
    return cal.session_open(label).to_pydatetime(), cal.session_close(label).to_pydatetime()


def session_at(now: datetime) -> str:
    local = now.astimezone(NY)
    bounds = session_bounds(local.date())
    if bounds is None:
        return "CLOSED"
    opening, closing = bounds
    if opening <= now < closing:
        return "REGULAR"
    if time(4) <= local.time() and now < opening:
        return "PRE"
    if closing <= now and local.time() < time(20):
        return "POST"
    return "CLOSED"


def completed_sessions(now: datetime, count: int = 260) -> list[date]:
    cal = calendar(now.year)
    labels = cal.sessions_in_range(
        pd.Timestamp(now.date() - timedelta(days=count * 2 + 20)), pd.Timestamp(now.date())
    )
    return [d.date() for d in labels if cal.session_close(d).to_pydatetime() <= now][-count:]


def expected_bar_times(start: datetime, end: datetime, timeframe: str) -> list[datetime]:
    """Bar timestamps identify period end, including the final 30-minute hourly bar."""
    cal = calendar(start.year)
    labels = cal.sessions_in_range(
        pd.Timestamp(start.astimezone(NY).date()), pd.Timestamp(end.astimezone(NY).date())
    )
    result = []
    for label in labels:
        opening = cal.session_open(label).to_pydatetime()
        closing = cal.session_close(label).to_pydatetime()
        if timeframe == "1d":
            times = [closing]
        else:
            step = timedelta(minutes=60 if timeframe == "1h" else 15)
            times, cursor = [], opening
            while cursor < closing:
                cursor = min(cursor + step, closing)
                times.append(cursor)
        result.extend(t.astimezone(UTC) for t in times if start <= t <= end)
    return result
