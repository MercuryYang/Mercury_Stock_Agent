import json
from pathlib import Path

from pydantic import AwareDatetime, Field

from trading_agent.core.models import Model, Positive, stable_id


class HoldingContext(Model):
    quantity: float
    sector: str | None = None
    protective_stop: Positive | None = None
    add_count: int | None = Field(default=None, ge=0)
    manual_average_cost: Positive | None = None


class VerifiedRiskContext(Model):
    """Explicit user metadata, never inferred or written by the model."""

    account_ref: str
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    as_of: AwareDatetime
    valid_until: AwareDatetime
    peak_equity: Positive
    order_coverage_complete: bool
    holdings: dict[str, HoldingContext]


def enrich(portfolio, path: Path, store):
    if not path.exists():
        return portfolio
    context = VerifiedRiskContext.model_validate_json(path.read_text(encoding="utf-8"))
    if (
        portfolio.account_refs != [context.account_ref]
        or not context.as_of <= portfolio.as_of <= context.valid_until
    ):
        return portfolio.model_copy(
            update={
                "reconciliation": "RECONCILIATION_REQUIRED",
                "issues": portfolio.issues + ["RISK_CONTEXT_EXPIRED_OR_ACCOUNT_MISMATCH"],
            }
        )
    actual = {p.symbol: p.quantity for p in portfolio.positions}
    expected = {symbol: meta.quantity for symbol, meta in context.holdings.items()}
    if actual != expected:
        return portfolio.model_copy(
            update={
                "reconciliation": "RECONCILIATION_REQUIRED",
                "issues": portfolio.issues + ["RISK_CONTEXT_POSITION_MISMATCH"],
            }
        )
    payload = context.model_dump(mode="json")
    key = stable_id("user-risk-context", payload)
    previous = store.list("audit_logs", symbol="__CONTEXT__", limit=1)
    store.put(
        "audit_logs",
        key,
        {
            "operation": "USER_RISK_CONTEXT",
            "actor": context.actor,
            "reason": context.reason,
            "as_of": context.as_of.isoformat(),
            "before": previous[0].get("after") if previous else None,
            "after": payload,
        },
        symbol="__CONTEXT__",
    )
    positions = [
        p.model_copy(update=context.holdings[p.symbol].model_dump(exclude={"quantity"}))
        for p in portfolio.positions
    ]
    updates = {
        "positions": positions,
        "peak_equity": context.peak_equity,
        "order_coverage_complete": context.order_coverage_complete,
        "risk_context_id": key,
    }
    enriched = portfolio.model_copy(update=updates)
    body = json.loads(enriched.model_dump_json(exclude={"snapshot_id"}))
    return enriched.model_copy(update={"snapshot_id": stable_id(body, key)})
