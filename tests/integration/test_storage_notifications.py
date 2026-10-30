import os
from datetime import timedelta

import pytest
from sqlalchemy import inspect

from trading_agent.cli import migrate
from trading_agent.core.models import utcnow
from trading_agent.notifications.service import NotificationRouter
from trading_agent.persistence.schema import NAMES
from trading_agent.persistence.store import Store
from trading_agent.workflows.service import Workflow


def test_migrations_reopen_and_schema(settings):
    migrate(settings)
    store = Store(settings.database_url)
    assert set(NAMES) <= set(inspect(store.engine).get_table_names())
    store.put("audit_logs", "one", {"test": True})
    store.engine.dispose()
    reopened = Store(settings.database_url)
    assert reopened.get("audit_logs", "one") == {"test": True}
    migrate(settings)
    reopened.engine.dispose()


@pytest.mark.postgres
async def test_postgres_migration_and_unique_upsert(settings, config):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    s = settings.model_copy(update={"database_url": url})
    migrate(s)
    store = Store(url)
    assert store.put("audit_logs", "postgres-check", {"ok": True}, replace=True)
    assert not store.put("audit_logs", "postgres-check", {"ok": False})
    assert store.get("audit_logs", "postgres-check")["ok"]
    with store.singleton("test-lock"):
        with pytest.raises(RuntimeError, match="WORKFLOW_BUSY"):
            with store.singleton("test-lock"):
                pass
    workflow = Workflow(s, config, store)
    result = await workflow.run()
    assert result["status"] == "SUCCESS"
    assert all(store.get("decisions", key)["action"] == "NO_ACTION" for key in result["decision_ids"])
    assert all(n["status"] == "SENT" for n in store.list("notification_events"))
    await workflow.aclose()
    store.engine.dispose()


def test_local_singleton_lock(store):
    with store.singleton("test-lock"):
        with pytest.raises(RuntimeError, match="WORKFLOW_BUSY"):
            with store.singleton("test-lock"):
                pass


async def test_notification_failure_retry_without_resending_successful_provider(store):
    class Provider:
        def __init__(self, fail=False):
            self.calls, self.fail = 0, fail

        async def send(self, *args):
            self.calls += 1
            if self.fail:
                self.fail = False
                raise ConnectionError("simulated failure")

    first, second = Provider(), Provider(True)
    router = NotificationRouter(store, [first, second])
    key = router.enqueue("same-event", {"sample": True})
    assert key == router.enqueue("same-event", {"sample": True})
    await router.flush()
    assert store.get("notification_events", key)["status"] == "FAILED"
    await router.flush(utcnow() + timedelta(seconds=10))
    assert store.get("notification_events", key)["status"] == "SENT"
    assert first.calls == 1 and second.calls == 2
