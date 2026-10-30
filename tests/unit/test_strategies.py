import pytest

from trading_agent.core.models import Action, Regime
from trading_agent.strategies.engine import STRATEGIES


@pytest.mark.parametrize("strategy_id", ["S01", "S02", "S03", "S04"])
def test_four_independent_strategies(strategy_id, risk_case, config):
    values = {
        "close": 100,
        "atr_14": 2,
        "macd_hist": 1,
        "prior_high_20": 99,
        "volume_ratio": 2,
        "relative_strength_20": 0.1,
        "sma_50": 95,
        "sma_200": 90,
        "sma_20": 99,
        "rsi_14": 50,
        "bb_lower": 101,
    }
    if strategy_id == "S04":
        values["rsi_14"] = 20
    technical = risk_case["technical"].model_copy(update={"indicators": values})
    regime = risk_case["regime"].model_copy(
        update={"state": Regime.SIDEWAYS if strategy_id == "S04" else Regime.BULL_TREND}
    )
    strategy = next(s for s in STRATEGIES if s.strategy_id == strategy_id)
    signal = strategy.evaluate(
        risk_case["quote"],
        technical,
        risk_case["portfolio"],
        regime,
        config.strategy["strategies"][strategy_id],
    )
    assert signal.action == Action.BUY
    assert signal.entry_zone[1] < signal.target_zone[0] and signal.stop_loss_reference < signal.entry_zone[0]
    assert signal.strategy_version and signal.invalidation_condition
    unknown = strategy.evaluate(
        risk_case["quote"],
        technical,
        risk_case["portfolio"],
        regime.model_copy(update={"state": Regime.UNKNOWN}),
        config.strategy["strategies"][strategy_id],
    )
    assert unknown.action == Action.WAIT
