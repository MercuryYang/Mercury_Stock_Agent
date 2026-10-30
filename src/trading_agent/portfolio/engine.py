from trading_agent.core.models import Portfolio, Position, stable_id


def normalize_accounts(accounts, refs, orders, transactions, now, order_history=None):
    positions, issues, cash, equity = [], [], 0.0, 0.0
    seen = set()
    for item in accounts:
        account = item["securitiesAccount"]
        ref = refs[account["accountNumber"]]
        seen.add(ref)
        balances = account.get("currentBalances", {})
        if not {"cashBalance", "liquidationValue"} <= balances.keys():
            issues.append("BALANCES_MISSING")
        cash += balances.get("cashBalance", 0)
        equity += balances.get("liquidationValue", 0)
        if "positions" not in account:
            issues.append("POSITIONS_FIELD_MISSING")
        for p in account.get("positions", []):
            instrument = p["instrument"]
            if instrument.get("assetType") != "EQUITY":
                issues.append("UNSUPPORTED_ASSET_IN_ACCOUNT")
                continue
            qty = p.get("longQuantity", 0) - p.get("shortQuantity", 0)
            cost, value = p.get("averagePrice"), p.get("marketValue")
            if cost is None or value is None:
                issues.append("COST_OR_VALUATION_MISSING")
            positions.append(
                Position(
                    account_ref=ref,
                    symbol=instrument["symbol"],
                    quantity=qty,
                    average_cost=cost,
                    market_value=value,
                    unrealized_pnl=(value - cost * qty) if cost is not None and value is not None else None,
                    source="SCHWAB",
                    cost_basis_source="BROKER_AVERAGE_PRICE",
                    as_of=now,
                )
            )
    if seen != set(refs.values()):
        issues.append("PARTIAL_ACCOUNT_RESPONSE")
    valuation = sum(p.market_value or 0 for p in positions) + cash
    if abs(valuation - equity) > max(0.01, abs(equity) * 0.0001):
        issues.append("BALANCE_RECONCILIATION_MISMATCH")
    if equity <= 0:
        issues.append("NON_POSITIVE_EQUITY")
    body = dict(
        as_of=now,
        source="SCHWAB",
        account_refs=sorted(seen),
        positions=positions,
        cash=cash,
        equity=equity if equity > 0 else None,
        reconciliation="RECONCILIATION_REQUIRED" if issues else "MATCHED",
        issues=sorted(set(issues)),
        open_orders=orders,
        transactions=transactions,
        order_history=order_history or orders,
    )
    return Portfolio(snapshot_id=stable_id(body), **body)
