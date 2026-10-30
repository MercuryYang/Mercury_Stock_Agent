from datetime import timedelta

import numpy as np
import pytest
from pydantic import ValidationError

from trading_agent.analysis.indicators import calculate
from trading_agent.analysis.market_regime import detect
from trading_agent.analysis.quality import validate_bars, validate_quote
from trading_agent.connectors.demo import DEMO_TIME, DemoProvider
from trading_agent.core.models import Quality, Regime


async def series():
    return await DemoProvider().get_history("GOOG", "1d", DEMO_TIME - timedelta(days=500), DEMO_TIME)


async def test_indicators_against_independent_reference():
    bars = await series()
    t = calculate(bars, bars, "bars", DEMO_TIME)
    closes = np.array([b.close for b in bars])
    assert t.quality.status == Quality.VALID
    assert t.indicators["sma_20"] == pytest.approx(np.mean(closes[-20:]))
    assert t.indicators["sma_200"] == pytest.approx(np.mean(closes[-200:]))
    assert t.indicators["bb_upper"] == pytest.approx(closes[-20:].mean() + 2 * closes[-20:].std())
    deltas = np.diff(closes)
    gains, losses = np.maximum(deltas, 0), np.maximum(-deltas, 0)
    g, loss = gains[:14].mean(), losses[:14].mean()
    for new_gain, new_loss in zip(gains[14:], losses[14:], strict=True):
        g, loss = (13 * g + new_gain) / 14, (13 * loss + new_loss) / 14
    assert t.indicators["rsi_14"] == pytest.approx(100 - 100 / (1 + g / loss))
    tr = [
        max(b.high - b.low, abs(b.high - bars[i - 1].close), abs(b.low - bars[i - 1].close))
        for i, b in enumerate(bars)
        if i
    ]
    atr = np.mean(tr[:14])
    for value in tr[14:]:
        atr = (13 * atr + value) / 14
    assert t.indicators["atr_14"] == pytest.approx(atr)
    assert t.indicators["relative_strength_20"] == pytest.approx(0)
    assert t.indicators["session_vwap"] is None


@pytest.mark.parametrize(
    "fault,code",
    [
        ("duplicate", "DUPLICATE_OR_UNORDERED_BARS"),
        ("gap", "MISSING_BARS"),
        ("ohlc", "INVALID_OHLC"),
        ("mixed", "MIXED_BAR_SERIES"),
        ("unknown", "UNKNOWN_ADJUSTMENT_OR_INVALID_BAR"),
        ("short", "INSUFFICIENT_HISTORY"),
    ],
)
async def test_bar_faults(fault, code):
    bars = await series()
    if fault == "duplicate":
        bars.append(bars[-1])
    if fault == "gap":
        bars.pop(-3)
    if fault == "ohlc":
        bars[-1] = bars[-1].model_copy(update={"high": 1})
    if fault == "mixed":
        bars[-1] = bars[-1].model_copy(update={"adjustment": "SPLIT_ADJUSTED"})
    if fault == "unknown":
        bars[-1] = bars[-1].model_copy(update={"adjustment": "UNKNOWN"})
    if fault == "short":
        bars = bars[-5:]
    assert code in validate_bars(bars, DEMO_TIME).reasons


async def test_hourly_vwap_resets_each_session():
    bars = await DemoProvider().get_history("GOOG", "1h", DEMO_TIME - timedelta(days=60), DEMO_TIME)
    t = calculate(bars, bars, "bars", DEMO_TIME)
    assert t.quality.status == Quality.VALID
    current = [b for b in bars if b.timestamp.date() == bars[-1].timestamp.date()]
    expected = sum((b.high + b.low + b.close) / 3 * b.volume for b in current) / sum(
        b.volume for b in current
    )
    assert t.indicators["session_vwap"] == pytest.approx(expected)


def test_nonfinite_and_naive_timestamp_rejected(risk_case):
    raw = risk_case["quote"].model_dump()
    with pytest.raises(ValidationError):
        type(risk_case["quote"])(**{**raw, "price": float("nan")})
    with pytest.raises(ValidationError):
        type(risk_case["quote"])(**{**raw, "timestamp": DEMO_TIME.replace(tzinfo=None)})


def test_quote_age_and_delay(risk_case):
    q = risk_case["quote"]
    assert validate_quote(q, DEMO_TIME + timedelta(seconds=61), 60).status == Quality.DATA_STALE
    assert validate_quote(q, DEMO_TIME, None).status == Quality.DATA_STALE
    assert (
        validate_quote(q.model_copy(update={"is_delayed": None}), DEMO_TIME, 60).status
        == Quality.DATA_INVALID
    )


@pytest.mark.parametrize(
    "atr,trend,state",
    [
        (4, "UP", Regime.RISK_OFF),
        (2.5, "UP", Regime.HIGH_VOLATILITY),
        (2, "UP", Regime.BULL_TREND),
        (2, "DOWN", Regime.BEAR_TREND),
        (2, "SIDEWAYS", Regime.SIDEWAYS),
    ],
)
def test_regime_boundaries(risk_case, atr, trend, state):
    t = risk_case["technical"].model_copy(
        update={"trend": trend, "indicators": {"atr_14": atr, "close": 100}}
    )
    assert detect(t).state == state
    assert detect(None).state == Regime.UNKNOWN
