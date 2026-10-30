from trading_agent.core.models import Regime
from trading_agent.strategies.base import TradingStrategy


class MeanReversion(TradingStrategy):
    strategy_id = "S04"
    regimes = {Regime.SIDEWAYS}

    def condition(self, technical, config):
        v = technical.indicators
        return (v["close"] <= v["bb_lower"] and v["rsi_14"] <= config["rsi_low"]), [
            "SIDEWAYS_OVERSOLD_BOLLINGER"
        ]
