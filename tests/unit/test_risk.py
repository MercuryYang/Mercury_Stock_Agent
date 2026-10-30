from datetime import timedelta

import pytest

from trading_agent.core.config import RiskConfig
from trading_agent.core.models import Action, Position, Regime
from trading_agent.risk.engine import assess, floor_shares


def test_exact_size_worst_case_and_cash_caps(risk_case, risk_config):
    risk = assess(**risk_case, config=risk_config)
    assert risk.approved
    assert risk.action == Action.BUY
    assert risk.quantity == 16
    assert risk.risk_amount <= 100
    assert risk.quantity * risk.entry_reference <= 2000
    assert risk.risk_reward_ratio > 1.5
    assert floor_shares(0.3, 0.1) == 3
    assert floor_shares(-1, 100) == 0


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("is_delayed", True, "DELAYED_OR_UNKNOWN_QUOTE"),
        ("session", "POST", "OUTSIDE_REGULAR_SESSION"),
        ("bid", None, "LIQUIDITY_UNKNOWN"),
        ("ask", 110, "SPREAD_LIMIT"),
        ("price", 200, "QUOTE_OUTSIDE_ENTRY_ZONE"),
    ],
)
def test_execution_gates(risk_case, risk_config, field, value, reason):
    risk_case["quote"] = risk_case["quote"].model_copy(update={field: value})
    r = assess(**risk_case, config=risk_config)
    assert not r.approved and r.quantity is None and reason in r.reason_codes


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("reconciliation", "UNVERIFIED", "RECONCILIATION_REQUIRED"),
        ("cash", 0, "POSITION_CASH_OR_RISK_LIMIT"),
        ("peak_equity", 20000, "DRAWDOWN_LIMIT"),
        ("peak_equity", None, "DRAWDOWN_BASELINE_UNKNOWN"),
        ("open_orders", [{"symbols": ["GOOG"]}], "OPEN_ORDER_CONFLICT"),
        ("account_refs", ["a", "b"], "MULTI_ACCOUNT_ALLOCATION_REQUIRED"),
    ],
)
def test_portfolio_gates(risk_case, risk_config, field, value, reason):
    risk_case["portfolio"] = risk_case["portfolio"].model_copy(update={field: value})
    r = assess(**risk_case, config=risk_config)
    assert not r.approved and reason in r.reason_codes


def test_missing_config_event_regime_and_stale(risk_case, risk_config):
    r = assess(**risk_case, config=RiskConfig())
    assert not r.approved and r.quantity is None and "RISK_CONFIG_INCOMPLETE" in r.reason_codes
    risk_case["now"] += timedelta(seconds=61)
    assert "STALE_QUOTE" in assess(**risk_case, config=risk_config).reason_codes
    risk_case["event"] = {"status": "UNKNOWN"}
    assert "EVENT_RISK_UNKNOWN" in assess(**risk_case, config=risk_config).reason_codes
    risk_case["regime"] = risk_case["regime"].model_copy(update={"state": Regime.UNKNOWN})
    assert "REGIME_BLOCKED" in assess(**risk_case, config=risk_config).reason_codes


def test_overweight_sector_heat_correlation(risk_case, risk_config):
    pos = Position(
        account_ref="test",
        symbol="QQQ",
        quantity=10,
        market_value=5000,
        source="TEST",
        as_of=risk_case["now"],
        sector="Tech",
        protective_stop=490,
    )
    risk_case["portfolio"] = risk_case["portfolio"].model_copy(update={"positions": [pos], "cash": 5000})
    r = assess(**risk_case, config=risk_config, correlations={"QQQ": 0.99})
    assert "CORRELATION_LIMIT" in r.reason_codes
    assert "POSITION_CASH_OR_RISK_LIMIT" in r.reason_codes


def test_conflict_not_majority(risk_case, risk_config):
    sell = risk_case["signals"][0].model_copy(update={"signal_id": "sell", "action": Action.REDUCE})
    risk_case["signals"] = risk_case["signals"] * 3 + [sell]
    r = assess(**risk_case, config=risk_config)
    assert r.action == Action.REVIEW and "STRATEGY_CONFLICT" in r.reason_codes


def test_quote_from_regular_session_cannot_authorize_after_close(risk_case, risk_config):
    risk_case["now"] += timedelta(minutes=2)
    config = risk_config.model_copy(update={"stale_quote_seconds": 300})
    r = assess(**risk_case, config=config)
    assert not r.approved and "OUTSIDE_REGULAR_SESSION" in r.reason_codes
