"""Immutable snapshot storage, mirrors, audit, outbox and paper outcomes."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None
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


def upgrade():
    for name in NAMES:
        op.create_table(
            name,
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("symbol", sa.String(16)),
            sa.Column("run_id", sa.String(64)),
            sa.Column("source", sa.String(64), nullable=False),
            sa.Column("version", sa.String(64), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
        )
        for field in ("created_at", "symbol", "run_id"):
            op.create_index(f"ix_{name}_{field}", name, [field])


def downgrade():
    for name in reversed(NAMES):
        op.drop_table(name)
