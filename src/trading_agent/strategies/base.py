from abc import ABC, abstractmethod

from trading_agent.core.models import Action, Quality, Regime, Signal, stable_id


class TradingStrategy(ABC):
    strategy_id: str
    regimes: set[Regime]

    @abstractmethod
    def condition(self, technical, config) -> tuple[bool, list[str]]: ...

    def evaluate(self, market_data, technical_state, portfolio_state, market_regime, config) -> Signal:
        t, v = technical_state, technical_state.indicators
        position = portfolio_state.position(t.symbol)
        action, strength, reasons = Action.WAIT, 0.0, ["CONDITIONS_NOT_MET"]
        entry = stop = target = rr = None
        if t.quality.status != Quality.VALID:
            action, reasons = Action.NO_ACTION, t.quality.reasons
        elif market_regime.state not in self.regimes:
            reasons = ["REGIME_NOT_APPLICABLE"]
        else:
            hit, reasons = self.condition(t, config)
            if hit:
                action = Action.ADD if position and position.quantity > 0 else Action.BUY
                strength = 0.7  # Evidence rank; not a calibrated probability.
                price, atr = v["close"], v["atr_14"]
                if atr > 0 and price - 2 * atr > 0:
                    entry, stop, target, rr = (
                        (price, price + 0.25 * atr),
                        price - 2 * atr,
                        (price + 4.75 * atr, price + 5 * atr),
                        2.0,
                    )
                else:
                    action, reasons = Action.NO_ACTION, ["INVALID_STOP_DISTANCE"]
            elif position and position.quantity > 0 and t.trend == "DOWN":
                action, strength = Action.REDUCE, 0.6
                reasons = ["DOWNTREND_REQUIRES_REVIEW"]
        return Signal(
            signal_id=stable_id(self.strategy_id, config["version"], t.snapshot_id, action),
            strategy_id=self.strategy_id,
            strategy_version=config["version"],
            symbol=t.symbol,
            direction="LONG" if action in (Action.BUY, Action.ADD) else "NEUTRAL",
            action=action,
            signal_strength=strength,
            entry_zone=entry,
            stop_loss_reference=stop,
            target_zone=target,
            risk_reward_ratio=rr,
            invalidation_condition="Completed close below the stop reference or regime change",
            reason_codes=reasons,
            market_snapshot_id=market_data.snapshot_id,
            technical_snapshot_id=t.snapshot_id,
        )
