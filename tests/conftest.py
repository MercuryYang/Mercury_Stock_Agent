from datetime import UTC, datetime

import pytest

from trading_agent.core.config import Config, RiskConfig, Settings
from trading_agent.core.models import (
    Action,
    MarketRegime,
    Portfolio,
    QualityReport,
    Quote,
    Regime,
    Signal,
    Technical,
)
from trading_agent.persistence.schema import metadata
from trading_agent.persistence.store import Store

NOW = datetime(2026, 10, 29, 19, 59, tzinfo=UTC)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path,
        admin_api_key="test-administration-key",
    )


@pytest.fixture
def config(settings):
    return Config(settings)


@pytest.fixture
def store(settings):
    db = Store(settings.database_url)
    metadata.create_all(db.engine)
    yield db
    db.engine.dispose()


@pytest.fixture
def risk_config():
    # Synthetic test risk limits, never loaded by the application.
    return RiskConfig(
        max_position_weight=0.2,
        max_single_trade_risk_pct=0.01,
        max_portfolio_drawdown_pct=0.2,
        max_sector_weight=0.4,
        max_add_count=2,
        max_gross_exposure=0.8,
        min_cash_weight=0.2,
        max_portfolio_heat=0.05,
        min_risk_reward_ratio=1.5,
        max_spread_pct=0.01,
        min_daily_volume=10000,
        max_slippage_pct=0.001,
        stress_shock_pct=0.1,
        max_stress_loss_pct=0.1,
        max_correlation=0.9,
        earnings_blackout_days=3,
        stale_quote_seconds=60,
        stale_portfolio_seconds=600,
    )


@pytest.fixture
def risk_case():
    quote = Quote(
        snapshot_id="quote",
        symbol="GOOG",
        timestamp=NOW,
        price=100,
        bid=99.99,
        ask=100.01,
        source="TEST",
        is_delayed=False,
        session="REGULAR",
    )
    portfolio = Portfolio(
        snapshot_id="portfolio",
        as_of=NOW,
        source="TEST",
        account_refs=["test"],
        positions=[],
        cash=10000,
        equity=10000,
        peak_equity=10000,
        reconciliation="MATCHED",
        order_coverage_complete=True,
    )
    technical = Technical(
        snapshot_id="technical",
        market_snapshot_id="bars",
        symbol="GOOG",
        timeframe="1d",
        as_of=NOW,
        version="test",
        adjustment="UNADJUSTED",
        trend="UP",
        levels=[],
        quality=QualityReport(status="VALID", reasons=[]),
        indicators={"volume_mean_20": 100000, "close": 100, "atr_14": 2},
    )
    signal = Signal(
        signal_id="signal",
        strategy_id="S01",
        strategy_version="s01-v1",
        symbol="GOOG",
        action=Action.BUY,
        signal_strength=0.7,
        entry_zone=(100, 101),
        stop_loss_reference=95,
        target_zone=(115, 120),
        risk_reward_ratio=2,
        invalidation_condition="Below stop",
        reason_codes=["UPTREND"],
        market_snapshot_id="quote",
        technical_snapshot_id="technical",
    )
    return dict(
        quote=quote,
        portfolio=portfolio,
        technical=technical,
        hourly=technical,
        regime=MarketRegime(
            snapshot_id="regime", state=Regime.BULL_TREND, reasons=[], benchmark_snapshot_id="benchmark"
        ),
        signals=[signal],
        config_version="test",
        now=NOW,
        instrument={"enabled": True, "leveraged": False, "asset_type": "EQUITY", "sector": "Tech"},
        event={"status": "KNOWN", "blackout": False},
    )
