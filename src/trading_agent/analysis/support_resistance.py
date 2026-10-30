def levels(bars, values) -> list[dict]:
    atr = values.get("atr_14")
    if atr is None:
        return []
    candidates = [
        (key, values.get(key))
        for key in ("sma_20", "sma_50", "sma_200", "previous_high", "previous_low", "prior_high_20")
    ]
    # A swing needs two completed bars on each side; never use unconfirmed pivots.
    for i in range(max(2, len(bars) - 40), len(bars) - 2):
        window = bars[i - 2 : i + 3]
        if bars[i].high == max(b.high for b in window):
            candidates.append((f"swing_high:{bars[i].timestamp.isoformat()}", bars[i].high))
        if bars[i].low == min(b.low for b in window):
            candidates.append((f"swing_low:{bars[i].timestamp.isoformat()}", bars[i].low))
    return [
        {
            "source": source,
            "zone": [max(0.01, price - atr * 0.25), price + atr * 0.25],
            "timeframe": bars[-1].timeframe,
            "computed_at": bars[-1].timestamp.isoformat(),
            "invalidation": "Completed close beyond zone by one ATR",
        }
        for source, price in candidates
        if price is not None
    ]
