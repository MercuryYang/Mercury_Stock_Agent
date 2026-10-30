import math
from datetime import UTC, datetime

from trading_agent.core.models import Bar, Portfolio, Position, Quote, stable_id
from trading_agent.scheduler.calendar import completed_sessions, expected_bar_times, session_bounds

DEMO_TIME = datetime(2026, 10, 29, 19, 59, tzinfo=UTC)


class DemoProvider:
    """Synthetic test data only. Prices and cash are not user account facts."""

    def __init__(self, now=DEMO_TIME):
        self.now = now

    async def get_quote(self, symbol):
        price = {"GOOG": 300.0, "QQQ": 500.0, "SPY": 600.0}.get(symbol, 100.0)
        return Quote(
            snapshot_id=stable_id("DEMO", symbol, self.now),
            symbol=symbol,
            timestamp=self.now,
            price=price,
            bid=price - 0.01,
            ask=price + 0.01,
            source="SYNTHETIC_FIXTURE",
            is_delayed=False,
            session="REGULAR",
        )

    async def get_history(self, symbol, timeframe, start_time, end_time):
        days = completed_sessions(self.now, 260)
        start = session_bounds(days[0])[0]
        times = expected_bar_times(start, self.now, timeframe)
        if timeframe != "1d":
            times = times[-260:]
        target = {"GOOG": 299.0, "QQQ": 499.0, "SPY": 599.0}.get(symbol, 99.0)
        bars = []
        for i, timestamp in enumerate(times):
            close = target - (len(times) - 1 - i) * 0.2 + math.sin(i / 8) * 2
            bars.append(
                Bar(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=timestamp,
                    open=close - 0.1,
                    high=close + 1,
                    low=close - 1,
                    close=close,
                    volume=1_000_000 + i * 100,
                    source="SYNTHETIC_FIXTURE",
                    is_delayed=False,
                    adjustment="UNADJUSTED",
                )
            )
        return bars

    async def get_market_status(self):
        return "REGULAR"

    async def sync(self):
        positions = [
            Position(
                account_ref="demo-account",
                symbol=symbol,
                quantity=quantity,
                average_cost=cost,
                market_value=quantity * price,
                source="SYNTHETIC_FIXTURE",
                cost_basis_source="TEST_FIXTURE" if cost else "UNKNOWN",
                as_of=self.now,
            )
            for symbol, quantity, cost, price in [("GOOG", 10, 289.60, 300), ("QQQ", 20, None, 500)]
        ]
        return Portfolio(
            snapshot_id=stable_id("demo-portfolio", self.now),
            as_of=self.now,
            source="SYNTHETIC_FIXTURE",
            account_refs=["demo-account"],
            positions=positions,
            cash=10000,
            equity=23000,
            reconciliation="UNVERIFIED",
            issues=["SYNTHETIC_DATA_ONLY", "QQQ_COST_UNKNOWN"],
        )

    async def aclose(self):
        pass
