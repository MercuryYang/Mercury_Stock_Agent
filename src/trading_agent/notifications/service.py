from datetime import datetime, timedelta
from typing import Protocol

import structlog

from trading_agent.core.models import stable_id, utcnow
from trading_agent.scheduler.calendar import NY

log = structlog.get_logger()


class NotificationProvider(Protocol):
    async def send(self, message, severity, metadata=None): ...


class ConsoleNotificationProvider:
    async def send(self, message, severity, metadata=None):
        # Do not print account holdings, quantities or raw provider/model text.
        log.info(
            "notification",
            severity=severity,
            notification_id=metadata["notification_id"],
            event_type=metadata.get("event_type"),
        )


class DatabaseNotificationProvider:
    def __init__(self, store):
        self.store = store

    async def send(self, message, severity, metadata=None):
        self.store.put(
            "audit_logs",
            stable_id("notification", metadata["notification_id"]),
            {
                "operation": "NOTIFICATION_STORED",
                "notification_id": metadata["notification_id"],
                "severity": severity,
            },
        )


class NotificationRouter:
    def __init__(self, store, providers=None):
        self.store = store
        self.providers = providers or [DatabaseNotificationProvider(store), ConsoleNotificationProvider()]

    def enqueue(self, dedup_key, message, severity="INFO", metadata=None, connection=None):
        key = stable_id(dedup_key)
        event = {
            "notification_id": key,
            "dedup_key": dedup_key,
            "message": message,
            "severity": severity,
            "metadata": metadata or {},
            "status": "PENDING",
            "attempts": 0,
            "delivered_providers": [],
            "next_attempt": utcnow().isoformat(),
        }
        self.store.put("notification_events", key, event, connection=connection)
        return key

    async def flush(self, now=None):
        now = now or utcnow()
        for event in self.store.list("notification_events", limit=10000, status_not="SENT"):
            if event["status"] == "SENT" or datetime.fromisoformat(event["next_attempt"]) > now:
                continue
            for index, provider in enumerate(self.providers):
                name = f"{index}:{type(provider).__name__}"
                if name in event["delivered_providers"]:
                    continue
                try:
                    await provider.send(
                        event["message"],
                        event["severity"],
                        {**event["metadata"], "notification_id": event["notification_id"]},
                    )
                    event["delivered_providers"].append(name)
                    self.store.put("notification_events", event["notification_id"], event, replace=True)
                except Exception as exc:
                    event["attempts"] += 1
                    event["status"] = "FAILED"
                    event["last_error_type"] = type(exc).__name__
                    event["next_attempt"] = (
                        now + timedelta(seconds=min(3600, 2 ** min(event["attempts"], 11)))
                    ).isoformat()
                    break
            else:
                event["status"] = "SENT"
            self.store.put("notification_events", event["notification_id"], event, replace=True)


class EventDetector:
    def __init__(self, store, cooldown_minutes):
        self.store, self.cooldown = store, timedelta(minutes=cooldown_minutes)

    def changed(self, symbol, quote, technical, signals, risk, now, commit=True, connection=None):
        states = {
            "risk": "|".join(risk.reason_codes) or "CLEAR",
            "trend": technical.trend,
            "signal": "|".join(f"{s.strategy_id}:{s.action}" for s in signals),
        }
        supports = [level["zone"][0] for level in technical.levels if level["zone"][0] < quote.price]
        atr = technical.indicators.get("atr_14") or 0
        states["support"] = "NEAR" if supports and quote.price - max(supports) <= atr * 0.5 else "CLEAR"
        key = stable_id(symbol, "event-state", now.astimezone(NY).date())
        old = self.store.get("event_states", key)
        severity = "WARNING" if risk.reason_codes else "INFO"
        if any(
            code in risk.reason_codes
            for code in ("STALE_QUOTE", "TECHNICAL_DATA_INVALID", "RECONCILIATION_REQUIRED")
        ):
            severity = "ERROR"
        if any(code in risk.reason_codes for code in ("DRAWDOWN_LIMIT", "STRESS_LOSS_LIMIT")):
            severity = "CRITICAL"
        rank = {"INFO": 0, "WARNING": 1, "ERROR": 2, "CRITICAL": 3}
        changed = old is None or old["states"] != states
        escalated = old is not None and rank[severity] > rank[old["severity"]]
        resolved = old is not None and (
            rank[severity] < rank[old["severity"]]
            or (old["states"]["support"] == "NEAR" and states["support"] == "CLEAR")
        )
        due = old is None or now - datetime.fromisoformat(old["sent_at"]) >= self.cooldown
        should_send = changed and (due or escalated or resolved)
        if should_send and commit:
            self.store.put(
                "event_states",
                key,
                {"states": states, "severity": severity, "sent_at": now.isoformat()},
                symbol=symbol,
                replace=True,
                connection=connection,
            )
        return should_send
