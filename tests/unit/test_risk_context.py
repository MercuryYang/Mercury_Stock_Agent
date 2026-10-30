import json
from datetime import timedelta

from trading_agent.portfolio.risk_context import enrich


def test_verified_metadata_is_bound_to_account_quantity_and_time(store, risk_case, tmp_path):
    p = risk_case["portfolio"]
    payload = {
        "account_ref": "test",
        "actor": "owner",
        "reason": "verified with brokerage",
        "as_of": (p.as_of - timedelta(minutes=1)).isoformat(),
        "valid_until": (p.as_of + timedelta(minutes=30)).isoformat(),
        "peak_equity": 11000,
        "order_coverage_complete": True,
        "holdings": {},
    }
    path = tmp_path / "risk-context.json"
    path.write_text(json.dumps(payload))
    enriched = enrich(p, path, store)
    assert enriched.peak_equity == 11000 and enriched.snapshot_id != p.snapshot_id
    assert enriched.risk_context_id and enriched.source == p.source
    assert store.list("audit_logs")[0]["actor"] == "owner"
    path.write_text(json.dumps({**payload, "holdings": {"GOOG": {"quantity": 10}}}))
    assert enrich(p, path, store).reconciliation == "RECONCILIATION_REQUIRED"
    path.write_text(json.dumps({**payload, "valid_until": (p.as_of - timedelta(seconds=1)).isoformat()}))
    assert enrich(p, path, store).reconciliation == "RECONCILIATION_REQUIRED"
