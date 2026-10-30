from trading_agent.core.models import Regime
from trading_agent.strategies.base import TradingStrategy


class MomentumBreakout(TradingStrategy):
    strategy_id = "S02"
    regimes = {Regime.BULL_TREND}

    def condition(self, technical, config):
        v = technical.indicators
        hit = (
            v["close"] > v["prior_high_20"]
            and (v["volume_ratio"] or 0) >= config["volume_ratio_min"]
            and v["relative_strength_20"] is not None
            and v["relative_strength_20"] > 0
        )
        return hit, ["PRIOR_RANGE_BREAKOUT_VOLUME_AND_RS"]
