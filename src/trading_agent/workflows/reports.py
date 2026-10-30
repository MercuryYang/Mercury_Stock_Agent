def render_report(store, decision_id):
    decision = store.get("decisions", decision_id)
    if decision is None:
        raise ValueError("DECISION_NOT_FOUND")
    rows = store.list("audit_logs", run_id=decision["run_id"], limit=1000)
    record = next(
        (
            row
            for row in rows
            if row.get("operation") == "DECISION_CONTEXT" and row.get("decision_id") == decision_id
        ),
        None,
    )
    lines = [
        f"# {decision['symbol']} 持仓分析",
        "",
        f"模式：{decision['mode']}；时间：{decision['created_at']}；动作：**{decision['action']}**",
        "",
        "合成测试数据，仅用于验证系统。" if decision["mode"] == "demo" else "账户与行情以所列快照时间为准。",
        "",
    ]
    if record:
        c = record["context"]
        positions = [p for p in c["portfolio"]["positions"] if p["symbol"] == decision["symbol"]]
        for p in positions:
            cost = p["average_cost"] if p["average_cost"] is not None else "未知"
            lines += [f"- 持仓：{p['quantity']}；平均成本：{cost} USD；成本来源：{p['cost_basis_source']}"]
        q = c["quote"]
        lines += [
            f"- 行情：{q['price']} USD；{q['timestamp']}；来源 {q['source']}；时段 {q['session']}；延迟 {q['is_delayed']}",
            f"- 市场状态：{c['regime']['state']}；依据：{', '.join(c['regime']['reasons'])}",
            "",
            "| 周期 | 趋势 | RSI 14 | ATR 14 | 数据质量 |",
            "|---|---|---|---|---|",
        ]
        for t in c["technical"]:
            v = t["indicators"]
            lines.append(
                f"| {t['timeframe']} | {t['trend']} | {v.get('rsi_14')} | {v.get('atr_14')} | {t['quality']['status']} |"
            )
        lines += ["", "| 策略版本 | 动作 | 条件依据 | 失效条件 |", "|---|---|---|---|"]
        for signal in c["signals"]:
            lines.append(
                f"| {signal['strategy_version']} | {signal['action']} | {', '.join(signal['reason_codes'])} | {signal['invalidation_condition']} |"
            )
        # Price triggers and support levels are suppressed when the current data gate fails.
        if decision["data_quality"] == "VALID":
            lines += ["", "关键价位区间（技术证据，不代表保证成交价）：", ""]
            for t in c["technical"]:
                for level in t["levels"][:6]:
                    lines.append(
                        f"- {t['timeframe']} {level['source']}: {level['zone']}; {level['invalidation']}"
                    )
        lines += ["", f"事件数据：{c['events']['status']}；来源：{c['events']['source']}"]
    lines += ["", f"分析解释：{decision['market_view']}", "", "风险检查：", ""]
    lines += [f"- {reason}" for reason in decision["risk_summary"]] or ["- 已通过风控检查"]
    lines += [
        "",
        f"建议数量：{decision['quantity'] if decision['quantity'] is not None else '未生成'}",
        f"触发条件：{decision['trigger_conditions']}",
        f"失效条件：{decision['invalidation_conditions']}",
        f"需要用户处理：{decision['user_action_required']}",
        f"生成器：{decision['generator']}；置信等级：{decision['confidence_label']}（非胜率）",
        "",
        f"决策 ID：{decision_id}；运行 ID：{decision['run_id']}",
        f"持仓快照：{decision['portfolio_snapshot_id']}；行情快照：{decision['market_snapshot_id']}",
        "",
    ]
    return "\n".join(lines)
