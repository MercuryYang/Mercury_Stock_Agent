from datetime import UTC, datetime

import pytest

from trading_agent.core.models import Bar
from trading_agent.scheduler.calendar import session_bounds
from trading_agent.workflows.paper import forward_outcomes


def test_no_same_bar_execution_and_costs():
    decision = {"created_at": "2026-10-26T19:59:00+00:00", "symbol": "GOOG", "decision_id": "test"}
    bars = []
    for day, price in [(26, 100), (27, 110), (28, 120), (29, 130), (30, 140)]:
        timestamp = session_bounds(datetime(2026, 10, day, tzinfo=UTC).date())[1]
        bars.append(
            Bar(
                symbol="GOOG",
                timeframe="1d",
                timestamp=timestamp,
                open=price,
                high=price + 2,
                low=price - 1,
                close=price + 1,
                volume=1000,
                source="TEST",
                adjustment="UNADJUSTED",
            )
        )
    results = forward_outcomes(decision, bars, 10)
    assert results[0]["entry_reference"] == 110
    assert results[0]["net_return"] == pytest.approx(111 / 110 - 1 - 0.001)
    assert {r["horizon"] for r in results} == {1, 3}
    assert results[1]["close_reference"] == 131
