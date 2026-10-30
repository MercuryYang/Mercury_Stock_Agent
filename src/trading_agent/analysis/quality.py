from datetime import datetime

from trading_agent.core.models import Bar, Quality, QualityReport, Quote
from trading_agent.scheduler.calendar import expected_bar_times


def validate_bars(bars: list[Bar], now: datetime, minimum: int = 200) -> QualityReport:
    reasons = []
    if len(bars) < minimum:
        reasons.append("INSUFFICIENT_HISTORY")
    if not bars:
        return QualityReport(status=Quality.DATA_INVALID, reasons=reasons)
    times = [b.timestamp for b in bars]
    if times != sorted(set(times)):
        reasons.append("DUPLICATE_OR_UNORDERED_BARS")
    if any(b.timestamp > now for b in bars):
        reasons.append("FUTURE_OR_INCOMPLETE_BAR")
    if any(b.high < max(b.open, b.close, b.low) or b.low > min(b.open, b.close) for b in bars):
        reasons.append("INVALID_OHLC")
    if len({(b.symbol, b.timeframe, b.adjustment, b.source) for b in bars}) != 1:
        reasons.append("MIXED_BAR_SERIES")
    if any(b.adjustment == "UNKNOWN" or b.quality_status != Quality.VALID for b in bars):
        reasons.append("UNKNOWN_ADJUSTMENT_OR_INVALID_BAR")
    if any(b.session != "REGULAR" for b in bars):
        reasons.append("NON_REGULAR_HISTORY")
    expected = expected_bar_times(min(times), now, bars[0].timeframe)
    if set(expected) - set(times):
        reasons.append("MISSING_BARS")
    if set(times) - set(expected):
        reasons.append("OFF_CALENDAR_BARS")
    return QualityReport(status=Quality.DATA_INVALID if reasons else Quality.VALID, reasons=reasons)


def validate_quote(quote: Quote, now: datetime, stale_seconds: float | None) -> QualityReport:
    reasons = []
    age = (now - quote.timestamp).total_seconds()
    if age < -5:
        reasons.append("FUTURE_QUOTE")
    if quote.quality_status != Quality.VALID:
        reasons.append(quote.quality_status.value)
    if quote.is_delayed is not False:
        reasons.append("DELAYED_OR_UNKNOWN_QUOTE")
    if quote.bid is not None and quote.ask is not None and quote.bid > quote.ask:
        reasons.append("CROSSED_QUOTE")
    if quote.adjustment != "UNADJUSTED":
        reasons.append("UNKNOWN_QUOTE_ADJUSTMENT")
    if reasons:
        return QualityReport(status=Quality.DATA_INVALID, reasons=reasons)
    if stale_seconds is None or age > stale_seconds:
        return QualityReport(
            status=Quality.DATA_STALE,
            reasons=["STALE_THRESHOLD_UNCONFIGURED" if stale_seconds is None else "STALE_QUOTE"],
        )
    return QualityReport(status=Quality.VALID, reasons=[])
