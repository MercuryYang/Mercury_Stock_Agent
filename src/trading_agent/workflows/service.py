import asyncio
from datetime import timedelta
from uuid import uuid4

import pandas as pd

from trading_agent.agents.analyst import analyze
from trading_agent.agents.model_provider import ModelProvider
from trading_agent.agents.reviewer import build_decision, validate_draft
from trading_agent.analysis.indicators import PARAMETERS, calculate
from trading_agent.analysis.market_regime import detect
from trading_agent.analysis.quality import validate_quote
from trading_agent.connectors.demo import DEMO_TIME, DemoProvider
from trading_agent.connectors.events.base import UnknownEventProvider
from trading_agent.connectors.schwab.client import SchwabReadOnly
from trading_agent.core.models import Quality, stable_id, utcnow
from trading_agent.core.version import runtime_manifest
from trading_agent.notifications.service import EventDetector, NotificationRouter
from trading_agent.portfolio.risk_context import enrich
from trading_agent.risk.engine import assess
from trading_agent.scheduler.calendar import NY
from trading_agent.strategies.engine import evaluate_all
from trading_agent.workflows.failures import record_abstention


class Workflow:
    def __init__(self, settings, config, store, provider=None, llm=None, events=None, notifications=None):
        self.settings, self.config, self.store = settings, config, store
        self.provider = provider
        self.llm = llm
        self.events = events or UnknownEventProvider()
        self.notifications = notifications or NotificationRouter(store)
        self.detector = EventDetector(store, config.scheduler["notification_cooldown_minutes"])
        self._mutex = asyncio.Lock()
        self.runtime = runtime_manifest(settings)

    def get_provider(self):
        if self.provider is None:
            self.provider = (
                DemoProvider() if self.settings.data_mode == "demo" else SchwabReadOnly(self.settings)
            )
        return self.provider

    async def sync(self):
        portfolio = await self.get_provider().sync()
        if self.settings.data_mode == "live":
            portfolio = enrich(portfolio, self.settings.data_dir / "risk-context.json", self.store)
        with self.store.engine.begin() as conn:
            self.store.put(
                "portfolio_snapshots",
                portfolio.snapshot_id,
                portfolio.model_dump(mode="json"),
                source=portfolio.source,
                connection=conn,
            )
            self.store.replace_positions(portfolio, conn)
            for ref in portfolio.account_refs:
                self.store.put(
                    "accounts", ref, {"account_ref": ref, "source": portfolio.source}, connection=conn
                )
            for o in portfolio.order_history or portfolio.open_orders:
                self.store.put("orders", o["order_ref"], o, replace=True, connection=conn)
            for t in portfolio.transactions:
                self.store.put("transactions", t["transaction_ref"], t, connection=conn)
        return portfolio

    async def series(self, symbol, timeframe, now, run_id):
        history = await self.get_provider().get_history(
            symbol,
            timeframe,
            now - timedelta(days=self.config.app["history_days"] if timeframe == "1d" else 60),
            now,
        )
        # One immutable series snapshot preserves order, duplicates, provenance and exact calculation input.
        key = stable_id([b.model_dump(mode="json") for b in history])
        self.store.put(
            "market_bars",
            key,
            {
                "snapshot_id": key,
                "symbol": symbol,
                "timeframe": timeframe,
                "bars": [b.model_dump(mode="json") for b in history],
            },
            symbol=symbol,
            run_id=run_id,
        )
        return history, key

    async def run(self, task="daily", now=None):
        if self._mutex.locked():
            raise RuntimeError("WORKFLOW_BUSY")
        async with self._mutex:
            with self.store.singleton("workflow"):
                return await self._run(task, now)

    async def _run(self, task, now=None):
        now = now or (DEMO_TIME if self.settings.data_mode == "demo" else utcnow())
        run_id = str(uuid4())
        record = {
            "run_id": run_id,
            "task": task,
            "started_at": utcnow().isoformat(),
            "as_of": now.isoformat(),
            "status": "RUNNING",
            "mode": self.settings.data_mode,
            "config_hash": self.config.version,
            "config": self.config.documents,
            "indicator_parameters": PARAMETERS,
            "runtime": self.runtime,
            "decision_ids": [],
            "issues": [],
        }
        self.store.put("workflow_runs", run_id, record, run_id=run_id)
        try:
            portfolio = await self.sync()
            benchmark_symbol = self.config.app["benchmark"]
            benchmark = {}
            benchmark_technical = None
            for tf in self.config.app["timeframes"]:
                try:
                    bars, key = await self.series(benchmark_symbol, tf, now, run_id)
                    benchmark[tf] = bars
                    if tf == "1d":
                        benchmark_technical = calculate(bars, bars, key, now)
                        self.store.put(
                            "technical_snapshots",
                            benchmark_technical.snapshot_id,
                            benchmark_technical.model_dump(mode="json"),
                            symbol=benchmark_symbol,
                            run_id=run_id,
                        )
                except Exception as exc:
                    record["issues"].append(f"BENCHMARK_{tf}_{type(exc).__name__}")
            regime = detect(benchmark_technical)
            self.store.put(
                "market_regimes", regime.snapshot_id, regime.model_dump(mode="json"), run_id=run_id
            )
            symbols = sorted(
                set(self.config.portfolio["watchlist"]) | {p.symbol for p in portfolio.positions}
            )
            daily = {}
            for symbol in symbols:
                try:
                    daily[symbol] = await self.series(symbol, "1d", now, run_id)
                except Exception as exc:
                    record["issues"].append(f"{symbol}_HISTORY_{type(exc).__name__}")
            for symbol in symbols:
                try:
                    decision = await self._symbol(
                        symbol, task, now, run_id, portfolio, benchmark, regime, daily
                    )
                    if decision:
                        record["decision_ids"].append(decision["decision_id"])
                except Exception as exc:
                    code = f"{symbol}_DATA_INVALID_{type(exc).__name__}"
                    record["issues"].append(code)
                    key = record_abstention(
                        self.store,
                        self.notifications,
                        symbol,
                        task,
                        now,
                        run_id,
                        self.settings.data_mode,
                        self.config.version,
                        code,
                        portfolio,
                    )
                    record["decision_ids"].append(key)
            record["status"] = "PARTIAL" if record["issues"] else "SUCCESS"
        except Exception as exc:
            record["status"] = "FAILED"
            record["issues"].append(type(exc).__name__)
            for symbol in self.config.portfolio["watchlist"]:
                key = record_abstention(
                    self.store,
                    self.notifications,
                    symbol,
                    task,
                    now,
                    run_id,
                    self.settings.data_mode,
                    self.config.version,
                    "SOURCE_UNAVAILABLE:" + type(exc).__name__,
                )
                record["decision_ids"].append(key)
            self.notifications.enqueue(
                stable_id("source-failure", type(exc).__name__, now.date()),
                {"error_type": type(exc).__name__, "run_id": run_id},
                "ERROR",
                {"event_type": "SOURCE_OR_AUTH_FAILURE"},
            )
        finally:
            record["finished_at"] = utcnow().isoformat()
            self.store.put("workflow_runs", run_id, record, run_id=run_id, replace=True)
            await self.notifications.flush()
        return record

    async def _symbol(self, symbol, task, now, run_id, portfolio, benchmark, regime, daily):
        quote = await self.get_provider().get_quote(symbol)
        bars, bars_id = daily[symbol]
        hourly_bars, hourly_id = await self.series(symbol, "1h", now, run_id)
        technical = calculate(bars, benchmark.get("1d", []), bars_id, now)
        hourly = calculate(hourly_bars, benchmark.get("1h", []), hourly_id, now)
        signals = evaluate_all(quote, technical, portfolio, regime, self.config.strategy["strategies"])
        correlations = {}
        target = pd.Series({b.timestamp: b.close for b in bars}).pct_change().dropna()
        for p in portfolio.positions:
            other = daily.get(p.symbol)
            if other:
                series = pd.Series({b.timestamp: b.close for b in other[0]}).pct_change().dropna()
                joined = pd.concat([target, series], axis=1).dropna().tail(60)
                if len(joined) >= 30 and all(joined.std() > 0):
                    correlations[p.symbol] = float(joined.corr().iloc[0, 1])
        event = await self.events.recent(symbol, now, self.config.risk.earnings_blackout_days)
        risk = assess(
            quote,
            portfolio,
            technical,
            hourly,
            regime,
            signals,
            self.config.risk,
            self.config.version,
            now,
            self.config.portfolio["instruments"].get(symbol),
            event,
            correlations,
        )
        key = stable_id(
            task,
            symbol,
            now.astimezone(NY).date(),
            quote.snapshot_id,
            portfolio.snapshot_id,
            technical.snapshot_id,
            hourly.snapshot_id,
            regime.snapshot_id,
            self.config.version,
            self.runtime,
            risk.action,
            risk.reason_codes,
            event,
        )
        if existing := self.store.get("decisions", key):
            return existing
        if task == "event" and not self.detector.changed(
            symbol, quote, technical, signals, risk, now, commit=False
        ):
            return None
        self.store.put(
            "market_quotes", quote.snapshot_id, quote.model_dump(mode="json"), symbol=symbol, run_id=run_id
        )
        for t in (technical, hourly):
            self.store.put(
                "technical_snapshots", t.snapshot_id, t.model_dump(mode="json"), symbol=symbol, run_id=run_id
            )
        for s in signals:
            self.store.put(
                "strategy_definitions", s.strategy_version, self.config.strategy["strategies"][s.strategy_id]
            )
            self.store.put(
                "strategy_signals", s.signal_id, s.model_dump(mode="json"), symbol=symbol, run_id=run_id
            )
        self.store.put(
            "risk_assessments", risk.assessment_id, risk.model_dump(mode="json"), symbol=symbol, run_id=run_id
        )
        context = {
            "portfolio": portfolio.model_dump(mode="json"),
            "quote": quote.model_dump(mode="json"),
            "technical": [t.model_dump(mode="json") for t in (technical, hourly)],
            "signals": [s.model_dump(mode="json") for s in signals],
            "risk": risk.model_dump(mode="json"),
            "regime": regime.model_dump(mode="json"),
            "events": event,
            "allowed_codes": sorted(
                set(
                    risk.reason_codes
                    + risk.warnings
                    + regime.reasons
                    + [r for s in signals for r in s.reason_codes]
                    + ["RISK_APPROVED"]
                )
            ),
            "evidence_ids": [
                quote.snapshot_id,
                portfolio.snapshot_id,
                risk.assessment_id,
                technical.snapshot_id,
                hourly.snapshot_id,
            ]
            + [s.signal_id for s in signals],
        }
        if not risk.approved:
            context["allowed_codes"].remove("RISK_APPROVED")
        self.store.put(
            "audit_logs",
            stable_id(key, "context"),
            {
                "operation": "DECISION_CONTEXT",
                "context": context,
                "decision_id": key,
                "history_ids": [bars_id, hourly_id],
            },
            symbol=symbol,
            run_id=run_id,
        )
        draft = rejected = None
        if self.settings.llm_enabled:
            try:
                if self.llm is None:
                    self.llm = ModelProvider(self.settings, self.config.app["llm"])
                draft = await analyze(self.llm, context, self.config.app["llm"])
                validate_draft(draft, context)
            except Exception as exc:
                rejected = f"LLM_OUTPUT_REJECTED:{type(exc).__name__}"
                draft = None
        publication_time = utcnow() if self.settings.data_mode == "live" else now
        if self.settings.data_mode == "live":
            risk = assess(
                quote,
                portfolio,
                technical,
                hourly,
                regime,
                signals,
                self.config.risk,
                self.config.version,
                publication_time,
                self.config.portfolio["instruments"].get(symbol),
                event,
                correlations,
            )
            self.store.put(
                "risk_assessments",
                risk.assessment_id,
                risk.model_dump(mode="json"),
                symbol=symbol,
                run_id=run_id,
            )
            if draft and draft.action != risk.action:
                rejected, draft = "RISK_RECHECK_REJECTED_MODEL_ACTION", None
        quality = validate_quote(quote, publication_time, self.config.risk.stale_quote_seconds).status
        if any(t.quality.status != Quality.VALID for t in (technical, hourly)):
            quality = Quality.DATA_INVALID
        decision = build_decision(
            key,
            run_id,
            publication_time,
            self.settings.data_mode,
            portfolio,
            quote,
            [technical, hourly],
            signals,
            risk,
            quality,
            draft,
            rejected,
            regime=regime,
        )
        payload = decision.model_dump(mode="json")
        with self.store.engine.begin() as conn:
            self.store.put("decisions", key, payload, symbol=symbol, run_id=run_id, connection=conn)
            if task == "event":
                self.detector.changed(symbol, quote, technical, signals, risk, now, connection=conn)
            for e in decision.evidence:
                self.store.put(
                    "decision_evidence",
                    stable_id(key, e.source_id),
                    {"decision_id": key, **e.model_dump()},
                    run_id=run_id,
                    connection=conn,
                )
            self.notifications.enqueue(
                key,
                payload,
                "WARNING" if risk.reason_codes else "INFO",
                {"event_type": task},
                connection=conn,
            )
        return payload

    async def aclose(self):
        if self.provider:
            await self.provider.aclose()
        if self.llm:
            await self.llm.aclose()
