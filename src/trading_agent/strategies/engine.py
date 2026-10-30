from trading_agent.core.models import Action
from trading_agent.strategies.mean_reversion import MeanReversion
from trading_agent.strategies.momentum_breakout import MomentumBreakout
from trading_agent.strategies.pullback_swing import PullbackSwing
from trading_agent.strategies.trend_following import TrendFollowing

STRATEGIES = (TrendFollowing(), MomentumBreakout(), PullbackSwing(), MeanReversion())


def evaluate_all(quote, technical, portfolio, regime, config):
    return [
        s.evaluate(quote, technical, portfolio, regime, config[s.strategy_id])
        for s in STRATEGIES
        if config[s.strategy_id]["enabled"]
    ]


def resolve(signals):
    active = [s for s in signals if s.action in (Action.BUY, Action.ADD, Action.REDUCE, Action.EXIT)]
    if len({s.action for s in active}) > 1:
        return None, ["STRATEGY_CONFLICT"]
    # Fixed priority by ID, after applicability filtering; never majority voting.
    return (sorted(active, key=lambda s: s.strategy_id)[0], []) if active else (None, ["NO_ACTIVE_SIGNAL"])
