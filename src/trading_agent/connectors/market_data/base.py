from datetime import datetime
from typing import Protocol

from trading_agent.core.models import Bar, Quote


class MarketDataProvider(Protocol):
    async def get_quote(self, symbol: str) -> Quote: ...
    async def get_history(
        self, symbol: str, timeframe: str, start_time: datetime, end_time: datetime
    ) -> list[Bar]: ...
    async def get_market_status(self) -> str: ...
