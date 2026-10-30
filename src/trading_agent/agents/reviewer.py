from trading_agent.core.models import Action, Decision, Evidence, Quality


def validate_draft(draft, context):
    if draft.action != context["risk"]["action"]:
        raise ValueError("AGENT_RISK_CONFLICT")
    if not set(draft.evidence_ids) <= set(context["evidence_ids"]):
        raise ValueError("AGENT_UNKNOWN_EVIDENCE")
    if not set(draft.explanation_codes) <= set(context["allowed_codes"]):
        raise ValueError("AGENT_UNSUPPORTED_EXPLANATION")
    if not context["risk"]["approved"] and draft.confidence_label != "LOW":
        raise ValueError("AGENT_CONFIDENCE_CONFLICT")


def build_decision(
    key,
    run_id,
    now,
    mode,
    portfolio,
    quote,
    technical,
    signals,
    risk,
    quality,
    draft=None,
    rejected=None,
    regime=None,
):
    action = risk.action if not rejected else Action.NO_ACTION
    valid = risk.approved and not rejected
    evidence = [
        Evidence(
            source_type="portfolio",
            source_id=portfolio.snapshot_id,
            statement="Authoritative portfolio input",
        ),
        Evidence(source_type="market", source_id=quote.snapshot_id, statement="Timestamped market input"),
        Evidence(source_type="risk", source_id=risk.assessment_id, statement="Deterministic risk gate"),
    ]
    evidence += [
        Evidence(source_type="technical", source_id=t.snapshot_id, statement=f"{t.timeframe} analysis")
        for t in technical
    ]
    evidence += [
        Evidence(source_type="strategy", source_id=s.signal_id, statement=s.strategy_version) for s in signals
    ]
    if regime:
        evidence.append(Evidence(source_type="regime", source_id=regime.snapshot_id, statement=regime.state))
    codes = draft.explanation_codes if draft else (risk.reason_codes or ["RISK_APPROVED"])
    summary = risk.reason_codes + risk.warnings + ([rejected] if rejected else [])
    decision = Decision(
        decision_id=key,
        run_id=run_id,
        created_at=now,
        symbol=quote.symbol,
        action=action,
        confidence_label=draft.confidence_label if draft else "LOW",
        market_view="；".join(codes),
        evidence=evidence,
        trigger_conditions=["Current quote within approved entry zone"] if valid else [],
        invalidation_conditions=[s.invalidation_condition for s in signals if s.action == action]
        if valid
        else [],
        risk_summary=summary,
        quantity=risk.quantity if valid else None,
        entry_reference=risk.entry_reference if valid else None,
        stop_reference=risk.stop_reference if valid else None,
        risk_reward_ratio=risk.risk_reward_ratio if valid else None,
        user_action_required=action in (Action.BUY, Action.ADD, Action.REDUCE, Action.EXIT, Action.REVIEW),
        data_quality=quality,
        portfolio_snapshot_id=portfolio.snapshot_id,
        market_snapshot_id=quote.snapshot_id,
        strategy_versions={s.strategy_id: s.strategy_version for s in signals},
        generator="agents_sdk" if draft else "deterministic",
        mode=mode,
        market_regime=regime.state if regime else "UNKNOWN",
        market_regime_snapshot_id=regime.snapshot_id if regime else None,
    )
    validate_decision(decision, risk)
    return decision


def validate_decision(decision, risk):
    if decision.action in (Action.BUY, Action.ADD):
        if not risk.approved or decision.action != risk.action or decision.data_quality != Quality.VALID:
            raise ValueError("RISK_VETO")
        for field in ("quantity", "entry_reference", "stop_reference", "risk_reward_ratio"):
            if getattr(decision, field) != getattr(risk, field):
                raise ValueError("UNSOURCED_TRADE_TERMS")
    elif any(getattr(decision, f) is not None for f in ("quantity", "entry_reference", "stop_reference")):
        raise ValueError("UNAPPROVED_TRADE_TERMS")
    if (
        decision.market_snapshot_id != risk.market_snapshot_id
        or decision.portfolio_snapshot_id != risk.portfolio_snapshot_id
    ):
        raise ValueError("SNAPSHOT_MISMATCH")
