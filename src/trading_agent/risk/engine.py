from decimal import ROUND_FLOOR, Decimal

from trading_agent.analysis.quality import validate_quote
from trading_agent.core.models import Action, Quality, Regime, RiskAssessment, stable_id
from trading_agent.scheduler.calendar import session_at
from trading_agent.strategies.engine import resolve


def floor_shares(amount: float, price: float) -> int:
    return max(0, int((Decimal(str(amount)) / Decimal(str(price))).to_integral_value(rounding=ROUND_FLOOR)))


def assess(
    quote,
    portfolio,
    technical,
    hourly,
    regime,
    signals,
    config,
    config_version,
    now,
    instrument,
    event,
    correlations=None,
) -> RiskAssessment:
    reasons, warnings, metrics = [], [], {}
    selected, conflict = resolve(signals)
    reasons.extend(conflict)
    if any(
        s.symbol != quote.symbol
        or s.market_snapshot_id != quote.snapshot_id
        or s.technical_snapshot_id != technical.snapshot_id
        for s in signals
    ):
        reasons.append("SIGNAL_EVIDENCE_MISMATCH")
    quality = validate_quote(quote, now, config.stale_quote_seconds)
    reasons.extend(quality.reasons)
    for t in (technical, hourly):
        if t is None or t.quality.status != Quality.VALID:
            reasons.append("TECHNICAL_DATA_INVALID")
        elif t.adjustment != quote.adjustment:
            reasons.append("ADJUSTMENT_MISMATCH")
    if portfolio.reconciliation != "MATCHED":
        reasons.append("RECONCILIATION_REQUIRED")
    if not portfolio.order_coverage_complete:
        reasons.append("ORDER_COVERAGE_UNVERIFIED")
    if len(portfolio.account_refs) != 1:
        reasons.append("MULTI_ACCOUNT_ALLOCATION_REQUIRED")
    if any(p.quantity < 0 or p.currency != "USD" for p in portfolio.positions):
        reasons.append("UNSUPPORTED_PORTFOLIO_EXPOSURE")
    age = (now - portfolio.as_of).total_seconds()
    if config.stale_portfolio_seconds is None or age < -5 or age > config.stale_portfolio_seconds:
        reasons.append("PORTFOLIO_STALE_OR_THRESHOLD_UNCONFIGURED")
    current_session = session_at(now)
    if (
        quote.session != "REGULAR" or current_session != "REGULAR"
    ) and not config.allow_extended_hours_trigger:
        reasons.append("OUTSIDE_REGULAR_SESSION")
    if quote.session in ("UNKNOWN", "CLOSED") or current_session == "CLOSED":
        reasons.append("MARKET_CLOSED_OR_UNKNOWN")
    if regime.state in (Regime.UNKNOWN, Regime.RISK_OFF, Regime.HIGH_VOLATILITY):
        reasons.append("REGIME_BLOCKED")
    if not instrument or not instrument.get("enabled") or instrument.get("leveraged") is not False:
        reasons.append("INSTRUMENT_NOT_APPROVED")
    if instrument and instrument.get("asset_type") not in ("EQUITY", "ETF"):
        reasons.append("UNSUPPORTED_INSTRUMENT")
    if any(quote.symbol in o.get("symbols", []) for o in portfolio.open_orders):
        reasons.append("OPEN_ORDER_CONFLICT")
    if event.get("status") != "KNOWN":
        warnings.append("EVENT_RISK_UNKNOWN")
        if config.earnings_blackout_enabled:
            reasons.append("EVENT_RISK_UNKNOWN")
    elif config.earnings_blackout_enabled and (event.get("blackout") is not False):
        reasons.append("EARNINGS_BLACKOUT")
    missing = config.missing()
    if missing:
        reasons.append("RISK_CONFIG_INCOMPLETE")
        warnings.extend(f"MISSING:{key}" for key in missing)
    if (
        portfolio.equity is None
        or portfolio.cash is None
        or any(p.market_value is None for p in portfolio.positions)
    ):
        reasons.append("PORTFOLIO_VALUATION_UNKNOWN")
    quantity = entry = stop = risk_amount = rr = None
    # Reductions are flagged for review; the POC never invents a liquidation quantity.
    if selected and selected.action in (Action.REDUCE, Action.EXIT):
        reasons.append("REDUCTION_REQUIRES_MANUAL_REVIEW")
    if not reasons and selected:
        equity, cash = portfolio.equity, portfolio.cash
        position = portfolio.position(quote.symbol)
        weight_value = position.market_value if position else 0
        gross = sum(max(0, p.market_value) for p in portfolio.positions)
        metrics.update(
            gross_exposure=gross / equity, cash_weight=cash / equity, position_weight=weight_value / equity
        )
        if portfolio.peak_equity is None:
            reasons.append("DRAWDOWN_BASELINE_UNKNOWN")
        else:
            drawdown = max(0, 1 - equity / portfolio.peak_equity)
            metrics["drawdown"] = drawdown
            if drawdown >= config.max_portfolio_drawdown_pct:
                reasons.append("DRAWDOWN_LIMIT")
        if position and (position.add_count is None or position.add_count >= config.max_add_count):
            reasons.append("ADD_LIMIT_OR_HISTORY_UNKNOWN")
        if quote.bid is None or quote.ask is None or quote.bid <= 0:
            reasons.append("LIQUIDITY_UNKNOWN")
        elif (quote.ask - quote.bid) / quote.price > config.max_spread_pct:
            reasons.append("SPREAD_LIMIT")
        if technical.indicators["volume_mean_20"] < config.min_daily_volume:
            reasons.append("VOLUME_LIMIT")
        sector = instrument.get("sector")
        if sector is None or any(p.sector is None for p in portfolio.positions):
            reasons.append("SECTOR_EXPOSURE_UNKNOWN")
        existing_heat = 0.0
        for p in portfolio.positions:
            if p.protective_stop is None:
                reasons.append("PORTFOLIO_HEAT_UNKNOWN")
            else:
                existing_heat += max(0, p.market_value - p.protective_stop * p.quantity)
        for p in portfolio.positions:
            if p.symbol == quote.symbol or p.quantity <= 0:
                continue
            corr = (correlations or {}).get(p.symbol)
            if corr is None:
                reasons.append("CORRELATION_UNKNOWN")
            elif corr > config.max_correlation:
                reasons.append("CORRELATION_LIMIT")
        if (
            selected.entry_zone is None
            or selected.stop_loss_reference is None
            or selected.target_zone is None
        ):
            reasons.append("PRICE_REFERENCES_MISSING")
        else:
            # Require the current quote to be in the strategy zone; use worst permitted entry and gap buffer.
            if not selected.entry_zone[0] <= quote.price <= selected.entry_zone[1]:
                reasons.append("QUOTE_OUTSIDE_ENTRY_ZONE")
            entry = max(quote.price, selected.entry_zone[1]) * (1 + config.max_slippage_pct)
            stop = selected.stop_loss_reference
            risk_per_share = entry - stop * (1 - config.max_slippage_pct)
            rr = (selected.target_zone[0] - entry) / risk_per_share if risk_per_share > 0 else 0
            if stop >= entry or rr < config.min_risk_reward_ratio:
                reasons.append("RISK_REWARD_OR_STOP_INVALID")
            else:
                sector_value = sum(p.market_value for p in portfolio.positions if p.sector == sector)
                capacities = [
                    floor_shares(equity * config.max_single_trade_risk_pct, risk_per_share),
                    floor_shares(equity * config.max_position_weight - weight_value, entry),
                    floor_shares(cash - equity * config.min_cash_weight, entry),
                    floor_shares(equity * config.max_gross_exposure - gross, entry),
                    floor_shares(equity * config.max_sector_weight - sector_value, entry),
                    floor_shares(equity * config.max_portfolio_heat - existing_heat, risk_per_share),
                ]
                quantity = min(capacities)
                stress = (gross + quantity * entry) * config.stress_shock_pct / equity
                metrics.update(stress_loss_pct=stress, portfolio_heat=existing_heat / equity)
                if stress > config.max_stress_loss_pct:
                    reasons.append("STRESS_LOSS_LIMIT")
                if quantity < 1:
                    reasons.append("POSITION_CASH_OR_RISK_LIMIT")
                risk_amount = quantity * risk_per_share
    approved = not reasons and selected is not None
    action = (
        selected.action
        if approved
        else (Action.REVIEW if conflict == ["STRATEGY_CONFLICT"] else Action.NO_ACTION)
    )
    if quality.status != Quality.VALID or any(
        code in reasons
        for code in ("RECONCILIATION_REQUIRED", "TECHNICAL_DATA_INVALID", "SIGNAL_EVIDENCE_MISMATCH")
    ):
        action = Action.NO_ACTION
    return RiskAssessment(
        assessment_id=stable_id(
            quote.snapshot_id,
            portfolio.snapshot_id,
            config_version,
            [s.signal_id for s in signals],
            now.isoformat(),
            sorted(set(reasons)),
        ),
        symbol=quote.symbol,
        action=action,
        approved=approved,
        quantity=quantity if approved else None,
        entry_reference=entry if approved else None,
        stop_reference=stop if approved else None,
        risk_amount=risk_amount if approved else None,
        risk_reward_ratio=rr if approved else None,
        reason_codes=sorted(set(reasons)),
        warnings=warnings,
        metrics=metrics,
        portfolio_snapshot_id=portfolio.snapshot_id,
        market_snapshot_id=quote.snapshot_id,
        signal_ids=[s.signal_id for s in signals],
        config_version=config_version,
    )
