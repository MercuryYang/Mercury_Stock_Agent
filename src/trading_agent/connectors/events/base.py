class UnknownEventProvider:
    async def recent(self, symbol, now, blackout_days=None):
        # No event feed has been authorized. Absence of a feed is not absence of risk.
        return {
            "symbol": symbol,
            "status": "UNKNOWN",
            "blackout": None,
            "as_of": now.isoformat(),
            "source": "UNCONFIGURED",
            "events": [],
        }
