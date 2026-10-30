import math
from datetime import datetime

import numpy as np
import talib

from trading_agent.analysis.quality import validate_bars
from trading_agent.analysis.support_resistance import levels
from trading_agent.analysis.trend import classify_trend
from trading_agent.core.models import Bar, Quality, Technical, stable_id
from trading_agent.scheduler.calendar import NY

PARAMETERS = {
    "sma": [20, 50, 200],
    "ema": [9, 20, 50],
    "rsi": 14,
    "macd": [12, 26, 9],
    "atr": 14,
    "bollinger": [20, 2],
    "relative_strength": 20,
}


def calculate(
    bars: list[Bar], benchmark: list[Bar], market_id: str, now: datetime, version: str = "indicators-v1"
) -> Technical:
    quality = validate_bars(bars, now)
    if not bars:
        raise ValueError("No bars to analyze")
    values = {}
    if quality.status == Quality.VALID:
        close, high, low, volume = (
            np.array([getattr(b, key) for b in bars], dtype=float)
            for key in ("close", "high", "low", "volume")
        )

        def last(series):
            return float(series[-1]) if math.isfinite(series[-1]) else None

        values = {f"sma_{n}": last(talib.SMA(close, n)) for n in PARAMETERS["sma"]}
        values.update({f"ema_{n}": last(talib.EMA(close, n)) for n in PARAMETERS["ema"]})
        macd, signal, hist = talib.MACD(close, 12, 26, 9)
        upper, mid, lower = talib.BBANDS(close, 20, 2, 2)
        values.update(
            close=float(close[-1]),
            rsi_14=last(talib.RSI(close, 14)),
            atr_14=last(talib.ATR(high, low, close, 14)),
            macd=last(macd),
            macd_signal=last(signal),
            macd_hist=last(hist),
            bb_upper=last(upper),
            bb_middle=last(mid),
            bb_lower=last(lower),
            volume=float(volume[-1]),
            # Exclude the trigger bar from breakout / volume reference windows.
            volume_mean_20=float(volume[-21:-1].mean()),
            volume_ratio=float(volume[-1] / volume[-21:-1].mean()) if volume[-21:-1].mean() > 0 else None,
            prior_high_20=float(high[-21:-1].max()),
            prior_low_20=float(low[-21:-1].min()),
            previous_high=float(high[-2]),
            previous_low=float(low[-2]),
            relative_strength_20=None,
            session_vwap=None,
        )
        ref = {b.timestamp: b.close for b in benchmark if b.adjustment == bars[-1].adjustment}
        if bars[-1].timestamp in ref and bars[-21].timestamp in ref:
            values["relative_strength_20"] = float(
                (close[-1] / close[-21]) / (ref[bars[-1].timestamp] / ref[bars[-21].timestamp]) - 1
            )
        if bars[-1].timeframe != "1d":
            day = bars[-1].timestamp.astimezone(NY).date()
            current = [b for b in bars if b.timestamp.astimezone(NY).date() == day]
            total = sum(b.volume for b in current)
            values["session_vwap"] = (
                sum((b.high + b.low + b.close) / 3 * b.volume for b in current) / total if total else None
            )
    return Technical(
        snapshot_id=stable_id(market_id, version, PARAMETERS, values),
        market_snapshot_id=market_id,
        symbol=bars[-1].symbol,
        timeframe=bars[-1].timeframe,
        as_of=bars[-1].timestamp,
        version=version,
        adjustment=bars[-1].adjustment,
        indicators=values,
        trend=classify_trend(values),
        levels=levels(bars, values),
        quality=quality,
    )
