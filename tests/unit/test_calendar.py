from datetime import UTC, date, datetime

from trading_agent.scheduler.calendar import session_bounds
from trading_agent.scheduler.service import due_tasks


def test_dst_holiday_half_day():
    assert session_bounds(date(2026, 3, 6))[0].hour == 14
    assert session_bounds(date(2026, 3, 9))[0].hour == 13
    assert session_bounds(date(2026, 11, 26)) is None
    opening, closing = session_bounds(date(2026, 11, 27))
    assert closing.hour == 18 and (closing - opening).total_seconds() == 3.5 * 3600


def test_dynamic_tasks_and_holiday_week(config):
    assert due_tasks(datetime(2026, 11, 26, 15, tzinfo=UTC), config.scheduler) == []
    tasks = due_tasks(datetime(2026, 11, 27, 18, 16, tzinfo=UTC), config.scheduler)
    assert ("postmarket", "2026-11-27") in tasks and ("weekly", "2026-11-27") in tasks
    # Good Friday: Thursday is the last trading day of this week.
    tasks = due_tasks(datetime(2026, 4, 2, 20, 16, tzinfo=UTC), config.scheduler)
    assert ("weekly", "2026-04-02") in tasks
