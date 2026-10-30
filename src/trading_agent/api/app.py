import secrets
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field

from trading_agent.core.config import Config, Settings
from trading_agent.core.logging import configure_logging
from trading_agent.core.models import Model, Symbol, utcnow
from trading_agent.persistence.store import Store
from trading_agent.scheduler.service import SchedulerService
from trading_agent.workflows.service import Workflow


class Feedback(Model):
    decision_id: str
    response: Literal["ACCEPTED", "REJECTED", "MODIFIED"]
    note: str = Field(default="", max_length=2000)


def create_app(settings=None, store=None, workflow=None):
    settings = settings or Settings()
    configure_logging()
    store = store or Store(settings.database_url)
    workflow = workflow or Workflow(settings, Config(settings), store)
    scheduler = SchedulerService(workflow)

    @asynccontextmanager
    async def lifespan(app):
        store.healthy()  # Migrations must be applied explicitly before startup.
        if settings.scheduler_enabled:
            scheduler.start()
        yield
        if settings.scheduler_enabled:
            scheduler.stop()
        await workflow.aclose()
        store.engine.dispose()

    app = FastAPI(
        title="Mercury Stock Agent",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    bearer = HTTPBearer(auto_error=False)

    def authenticate(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        expected = settings.admin_api_key.get_secret_value()
        if not expected:
            raise HTTPException(503, "ADMIN_API_KEY_NOT_CONFIGURED")
        if not credentials or not secrets.compare_digest(credentials.credentials, expected):
            raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"})

    auth = [Depends(authenticate)]

    @app.get("/health")
    def health():
        try:
            store.healthy()
        except Exception:
            raise HTTPException(503, "Storage unavailable") from None
        return {"status": "ok", "mode": settings.data_mode, "order_execution": False}

    @app.get("/readiness", dependencies=auth)
    def readiness():
        return {
            "mode": settings.data_mode,
            "llm_enabled": settings.llm_enabled,
            "risk_missing": workflow.config.risk.missing(),
            "broker_connected": any(
                p["source"] == "SCHWAB" for p in store.list("portfolio_snapshots", limit=1)
            ),
            "order_execution": False,
            "scheduler_enabled": settings.scheduler_enabled,
        }

    @app.post("/portfolio/sync", dependencies=auth)
    async def sync():
        try:
            async with workflow._mutex:
                with store.singleton("workflow"):
                    return await workflow.sync()
        except Exception as exc:
            raise HTTPException(503, type(exc).__name__) from None

    def latest(table, symbol=None):
        rows = store.list(table, symbol=symbol, limit=1)
        if not rows:
            raise HTTPException(404, "No snapshot available")
        return rows[0]

    @app.get("/portfolio/snapshot", dependencies=auth)
    def snapshot():
        return latest("portfolio_snapshots")

    @app.get("/market/{symbol}", dependencies=auth)
    def market(symbol: Symbol):
        return latest("market_quotes", symbol)

    @app.get("/analysis/{symbol}", dependencies=auth)
    def analysis(symbol: Symbol):
        # Return the latest run's two timeframes, not arbitrary mixed historical rows.
        rows = store.list("technical_snapshots", symbol, 100)
        by_tf = {}
        for row in rows:
            by_tf.setdefault(row["timeframe"], row)
        return list(by_tf.values())

    @app.get("/signals/{symbol}", dependencies=auth)
    def signals(symbol: Symbol):
        rows = store.list("strategy_signals", symbol, 100)
        by_strategy = {}
        for row in rows:
            by_strategy.setdefault(row["strategy_id"], row)
        return list(by_strategy.values())

    @app.get("/decisions/latest", dependencies=auth)
    def decisions():
        return store.list("decisions", limit=100)

    @app.get("/decisions/{decision_id}/audit", dependencies=auth)
    def audit(decision_id: str):
        decision = store.get("decisions", decision_id)
        if not decision:
            raise HTTPException(404, "Unknown decision")
        return {
            "decision": decision,
            "run": store.get("workflow_runs", decision["run_id"]),
            "evidence": store.list("decision_evidence", run_id=decision["run_id"], limit=1000),
            "context": store.list("audit_logs", run_id=decision["run_id"], limit=1000),
        }

    @app.post("/jobs/run-daily", dependencies=auth)
    async def daily():
        try:
            return await workflow.run("daily")
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/jobs/run-events", dependencies=auth)
    async def events():
        return await workflow.run("event")

    @app.post("/feedback", dependencies=auth)
    def feedback(item: Feedback):
        if not store.get("decisions", item.decision_id):
            raise HTTPException(404, "Unknown decision")
        key = str(uuid4())
        store.put(
            "user_feedback",
            key,
            {
                "feedback_id": key,
                "created_at": utcnow().isoformat(),
                "actor": "authenticated-owner",
                **item.model_dump(),
            },
        )
        return {"feedback_id": key}

    return app
