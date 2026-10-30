from trading_agent.core.models import Decision, Evidence, Portfolio, RiskAssessment, stable_id


def record_abstention(store, router, symbol, task, now, run_id, mode, config_version, code, portfolio=None):
    if portfolio is None:
        portfolio = Portfolio(
            snapshot_id=stable_id("unavailable-portfolio", now.date()),
            as_of=now,
            source="UNAVAILABLE",
            account_refs=[],
            positions=[],
            cash=None,
            equity=None,
            reconciliation="UNVERIFIED",
            issues=[code],
        )
        store.put(
            "portfolio_snapshots", portfolio.snapshot_id, portfolio.model_dump(mode="json"), run_id=run_id
        )
    market_id = stable_id("unavailable-market", symbol, now.date(), code)
    diagnostic = {
        "snapshot_id": market_id,
        "symbol": symbol,
        "timestamp": now.isoformat(),
        "source": "UNAVAILABLE",
        "quality_status": "DATA_INVALID",
        "price": None,
        "reason": code,
    }
    risk = RiskAssessment(
        assessment_id=stable_id(market_id, portfolio.snapshot_id, config_version),
        symbol=symbol,
        action="NO_ACTION",
        approved=False,
        reason_codes=[code],
        warnings=[],
        metrics={},
        portfolio_snapshot_id=portfolio.snapshot_id,
        market_snapshot_id=market_id,
        signal_ids=[],
        config_version=config_version,
    )
    key = stable_id(task, market_id, portfolio.snapshot_id, config_version)
    d = Decision(
        decision_id=key,
        run_id=run_id,
        created_at=now,
        symbol=symbol,
        action="NO_ACTION",
        confidence_label="LOW",
        market_view="DATA_INVALID",
        trigger_conditions=[],
        invalidation_conditions=[],
        risk_summary=[code],
        user_action_required=False,
        data_quality="DATA_INVALID",
        portfolio_snapshot_id=portfolio.snapshot_id,
        market_snapshot_id=market_id,
        strategy_versions={},
        generator="deterministic",
        mode=mode,
        evidence=[
            Evidence(source_type=kind, source_id=ref, statement="Unavailable data requires abstention")
            for kind, ref in [
                ("market", market_id),
                ("portfolio", portfolio.snapshot_id),
                ("risk", risk.assessment_id),
            ]
        ],
    )
    payload = d.model_dump(mode="json")
    with store.engine.begin() as conn:
        store.put("market_quotes", market_id, diagnostic, symbol=symbol, run_id=run_id, connection=conn)
        store.put(
            "risk_assessments",
            risk.assessment_id,
            risk.model_dump(mode="json"),
            run_id=run_id,
            connection=conn,
        )
        store.put("decisions", key, payload, symbol=symbol, run_id=run_id, connection=conn)
        for evidence in d.evidence:
            store.put(
                "decision_evidence",
                stable_id(key, evidence.source_id),
                {"decision_id": key, **evidence.model_dump()},
                run_id=run_id,
                connection=conn,
            )
        router.enqueue(key, payload, "ERROR", {"event_type": "DATA_INVALID"}, connection=conn)
    return key
