from fastapi.testclient import TestClient

from trading_agent.api.app import create_app
from trading_agent.core.models import Decision
from trading_agent.workflows.service import Workflow


async def test_workflow_audit_idempotency_and_notifications(settings, config, store):
    workflow = Workflow(settings, config, store)
    a, b = await workflow.run(), await workflow.run()
    assert a["status"] == b["status"] == "SUCCESS"
    assert a["decision_ids"] == b["decision_ids"]
    assert len(store.list("decisions")) == 2
    assert len(store.list("notification_events")) == 2
    assert all(n["status"] == "SENT" for n in store.list("notification_events"))
    for raw in store.list("decisions"):
        d = Decision.model_validate(raw)
        assert d.action == "NO_ACTION" and d.quantity is None
        assert store.get("portfolio_snapshots", d.portfolio_snapshot_id)
        assert store.get("market_quotes", d.market_snapshot_id)
        for e in d.evidence:
            table = {
                "risk": "risk_assessments",
                "technical": "technical_snapshots",
                "strategy": "strategy_signals",
                "portfolio": "portfolio_snapshots",
                "market": "market_quotes",
                "regime": "market_regimes",
            }[e.source_type]
            assert store.get(table, e.source_id)
    await workflow.aclose()


async def test_events_do_not_repeatedly_call_llm(settings, config, store):
    workflow = Workflow(settings, config, store)
    first, second = await workflow.run("event"), await workflow.run("event")
    assert first["status"] == second["status"] == "SUCCESS"
    assert len(store.list("notification_events")) == 2
    await workflow.aclose()


def test_api_auth_feedback_and_all_read_routes(settings, config, store):
    app = create_app(settings, store, Workflow(settings, config, store))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        for path in ("/portfolio/snapshot", "/decisions/latest", "/market/GOOG", "/signals/GOOG"):
            assert client.get(path).status_code == 401
        headers = {"Authorization": "Bearer " + settings.admin_api_key.get_secret_value()}
        result = client.post("/jobs/run-daily", headers=headers)
        assert result.status_code == 200 and result.json()["status"] == "SUCCESS"
        for path in (
            "/portfolio/snapshot",
            "/decisions/latest",
            "/market/GOOG",
            "/signals/GOOG",
            "/analysis/GOOG",
        ):
            assert client.get(path, headers=headers).status_code == 200
        decision_id = result.json()["decision_ids"][0]
        assert client.get(f"/decisions/{decision_id}/audit", headers=headers).status_code == 200
        body = {"decision_id": decision_id, "response": "REJECTED", "note": "Test only"}
        assert client.post("/feedback", json=body, headers=headers).status_code == 200
        assert client.post("/feedback", json=body).status_code == 401
        assert (
            client.post("/feedback", json={**body, "decision_id": "bad"}, headers=headers).status_code == 404
        )
        assert client.post("/orders", headers=headers).status_code == 404
