from sqlalchemy import JSON, Column, DateTime, MetaData, String, Table

metadata = MetaData()
NAMES = (
    "accounts",
    "positions",
    "portfolio_snapshots",
    "orders",
    "transactions",
    "market_bars",
    "market_quotes",
    "technical_snapshots",
    "market_regimes",
    "strategy_definitions",
    "strategy_signals",
    "risk_assessments",
    "decisions",
    "decision_evidence",
    "notification_events",
    "user_feedback",
    "audit_logs",
    "workflow_runs",
    "event_states",
    "paper_outcomes",
)
TABLES = {
    name: Table(
        name,
        metadata,
        Column("id", String(64), primary_key=True),
        Column("created_at", DateTime(timezone=True), nullable=False, index=True),
        Column("symbol", String(16), index=True),
        Column("run_id", String(64), index=True),
        Column("source", String(64), nullable=False),
        Column("version", String(64), nullable=False),
        Column("payload", JSON, nullable=False),
    )
    for name in NAMES
}
