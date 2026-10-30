from trading_agent.core.models import Regime
from trading_agent.strategies.base import TradingStrategy


class TrendFollowing(TradingStrategy):
    strategy_id = "S01"
    regimes = {Regime.BULL_TREND, Regime.BEAR_TREND}

    def condition(self, technical, config):
        return technical.trend == "UP" and technical.indicators["macd_hist"] > 0, ["MA_STRUCTURE_AND_MACD"]
