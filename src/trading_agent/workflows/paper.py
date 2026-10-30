from datetime import datetime
from statistics import mean

from trading_agent.core.models import Action, Bar, stable_id
from trading_agent.scheduler.calendar import completed_sessions, session_bounds

HORIZONS = (1, 3, 5, 10)


def forward_outcomes(decision, bars, cost_bps):
    """Next-session open to forward close; no same-bar execution or actual fill assumption."""
    as_of = datetime.fromisoformat(decision["created_at"])
    eligible = [b for b in bars if b.timeframe == "1d" and session_bounds(b.timestamp.date())[0] > as_of]
    eligible.sort(key=lambda b: b.timestamp)
    if not eligible:
        return []
    if len({b.adjustment for b in eligible}) != 1 or eligible[0].adjustment == "UNKNOWN":
        raise ValueError("PAPER_ADJUSTMENT_INVALID")
    entry = eligible[0].open
    result = []
    for horizon in HORIZONS:
        if len(eligible) < horizon:
            continue
        window = eligible[:horizon]
        expected = completed_sessions(window[-1].timestamp, horizon)
        if [b.timestamp.date() for b in window] != expected:
            raise ValueError("PAPER_MISSING_SESSIONS")
        # A stated round-trip cost; no claim that these proxy returns are realized trades.
        net_return = window[-1].close / entry - 1 - cost_bps / 10000
        result.append(
            {
                "decision_id": decision["decision_id"],
                "symbol": decision["symbol"],
                "horizon": horizon,
                "entry_time": session_bounds(window[0].timestamp.date())[0].isoformat(),
                "exit_time": window[-1].timestamp.isoformat(),
                "entry_reference": entry,
                "close_reference": window[-1].close,
                "gross_return": window[-1].close / entry - 1,
                "net_return": net_return,
                "mae": min(b.low for b in window) / entry - 1,
                "mfe": max(b.high for b in window) / entry - 1,
                "cost_bps": cost_bps,
                "regime": decision.get("market_regime", "UNKNOWN"),
                "strategy_versions": decision.get("strategy_versions", {}),
                "evaluation": "FORWARD_MARKOUT_NOT_EXECUTED_TRADE",
            }
        )
    return result


def evaluate_saved(store, cost_bps):
    outcomes = []
    for d in store.list("decisions", limit=100000):
        if d["action"] not in (Action.BUY, Action.ADD) or d.get("mode") != "live":
            continue
        snapshots = store.list("market_bars", symbol=d["symbol"], limit=10000)
        bars = {}
        for snapshot in reversed(snapshots):
            for raw in snapshot["bars"]:
                if raw["timeframe"] == "1d":
                    bar = Bar.model_validate(raw)
                    bars[bar.timestamp] = bar
        for cost in cost_bps:
            for item in forward_outcomes(d, list(bars.values()), cost):
                key = stable_id(d["decision_id"], item["horizon"], cost)
                store.put("paper_outcomes", key, item, symbol=d["symbol"])
                outcomes.append(item)
    groups = {}
    for item in outcomes:
        key = f"{item['regime']}:h{item['horizon']}:cost{item['cost_bps']}"
        groups.setdefault(key, []).append(item["net_return"])
    summary = {}
    for key, returns in groups.items():
        wins, losses = [x for x in returns if x > 0], [x for x in returns if x < 0]
        summary[key] = {
            "observations": len(returns),
            "mean_return": mean(returns),
            "positive_markout_rate": len(wins) / len(returns),
            "payoff_ratio": mean(wins) / abs(mean(losses)) if wins and losses else None,
        }
    return {
        "outcomes": len(outcomes),
        "groups": summary,
        "status": "INSUFFICIENT_FORWARD_DATA" if not outcomes else "RESEARCH_ONLY",
        "limitations": [
            "Overlapping horizons are not independent trades",
            "No portfolio equity curve or realized win-rate inferred",
            "Out-of-sample validation and live forward observation remain required",
        ],
    }
