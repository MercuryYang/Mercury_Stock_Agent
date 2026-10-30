def classify_trend(values: dict) -> str:
    price, fast, medium, slow = (values.get(k) for k in ("close", "sma_20", "sma_50", "sma_200"))
    if any(v is None for v in (price, fast, medium, slow)):
        return "UNKNOWN"
    if price > fast > medium > slow:
        return "UP"
    if price < fast < medium < slow:
        return "DOWN"
    return "SIDEWAYS"
