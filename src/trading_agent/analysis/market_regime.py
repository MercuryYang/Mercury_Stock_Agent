from trading_agent.core.models import MarketRegime, Quality, Regime, Technical, stable_id


def detect(benchmark: Technical | None) -> MarketRegime:
    state, reasons = Regime.UNKNOWN, ["BENCHMARK_DATA_UNAVAILABLE"]
    if benchmark and benchmark.quality.status == Quality.VALID:
        v = benchmark.indicators
        atr_pct = v["atr_14"] / v["close"]
        if atr_pct >= 0.04:
            state, reasons = Regime.RISK_OFF, ["BENCHMARK_ATR_GE_4PCT"]
        elif atr_pct >= 0.025:
            state, reasons = Regime.HIGH_VOLATILITY, ["BENCHMARK_ATR_GE_2_5PCT"]
        elif benchmark.trend == "UP":
            state, reasons = Regime.BULL_TREND, ["BENCHMARK_MA_UPTREND"]
        elif benchmark.trend == "DOWN":
            state, reasons = Regime.BEAR_TREND, ["BENCHMARK_MA_DOWNTREND"]
        else:
            state, reasons = Regime.SIDEWAYS, ["BENCHMARK_MIXED_TREND"]
        reasons += ["BREADTH_UNAVAILABLE", "VOLATILITY_PROXY_ATR"]
    ref = benchmark.snapshot_id if benchmark else None
    return MarketRegime(
        snapshot_id=stable_id(ref, state, "regime-v1"),
        state=state,
        reasons=reasons,
        benchmark_snapshot_id=ref,
    )
