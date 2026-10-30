from trading_agent.core.models import Regime
from trading_agent.strategies.base import TradingStrategy


class PullbackSwing(TradingStrategy):
    strategy_id = "S03"
    regimes = {Regime.BULL_TREND}

    def condition(self, technical, config):
        v = technical.indicators
        hit = (
            v["sma_50"] > v["sma_200"]
            and v["close"] >= v["sma_20"]
            and v["close"] - v["sma_20"] <= config["support_atr_distance"] * v["atr_14"]
            and 40 <= v["rsi_14"] <= 60
        )
        return hit, ["UPTREND_PULLBACK_SUPPORT_CONFIRMATION"]
