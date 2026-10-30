import asyncio
import hashlib
import hmac
from datetime import UTC, datetime, timedelta

import httpx

from trading_agent.connectors.schwab.auth import AuthorizationRequired, EncryptedVault, SchwabAuth
from trading_agent.core.models import Bar, Quote, stable_id, utcnow
from trading_agent.portfolio.engine import normalize_accounts
from trading_agent.scheduler.calendar import NY, expected_bar_times, session_at, session_bounds


class SchwabReadOnly:
    """Only explicit GET endpoints. OAuth token exchange is the only HTTP POST in this connector."""

    def __init__(self, settings, client=None, auth=None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            base_url="https://api.schwabapi.com", timeout=30, follow_redirects=False
        )
        self.auth = auth or SchwabAuth(settings)
        self.account_vault = EncryptedVault(
            settings.data_dir / "schwab-accounts.enc", settings.schwab_token_encryption_key.get_secret_value()
        )

    async def _get(self, path, params=None):
        force_refresh = False
        for attempt in range(3):
            token = await self.auth.access_token(force=force_refresh)
            force_refresh = False
            try:
                response = await self.client.get(
                    path, params=params, headers={"Authorization": f"Bearer {token}"}
                )
            except httpx.HTTPError:
                if attempt == 2:
                    raise RuntimeError("SCHWAB_NETWORK_ERROR") from None
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code == 401 and attempt < 2:
                force_refresh = True
                continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                retry = response.headers.get("Retry-After", "1")
                await asyncio.sleep(min(float(retry) if retry.isdigit() else 1, 10))
                continue
            if response.status_code in (401, 403):
                raise AuthorizationRequired("SCHWAB_AUTHORIZATION_OR_PERMISSION_REQUIRED")
            if response.status_code != 200:
                raise RuntimeError(f"SCHWAB_READ_HTTP_{response.status_code}")
            return response.json()
        raise RuntimeError("SCHWAB_READ_FAILED")

    async def sync(self):
        now = utcnow()
        mapping = await self._get("/trader/v1/accounts/accountNumbers")
        if not mapping:
            raise RuntimeError("SCHWAB_NO_ACCOUNTS")
        key = self.settings.schwab_token_encryption_key.get_secret_value().encode()
        refs = {
            m["accountNumber"]: hmac.new(key, m["accountNumber"].encode(), hashlib.sha256).hexdigest()[:24]
            for m in mapping
        }
        self.account_vault.write({refs[m["accountNumber"]]: m for m in mapping})
        accounts = await self._get("/trader/v1/accounts", {"fields": "positions"})
        orders, transactions, order_history = [], [], []
        for m in mapping:
            ref, account_hash = refs[m["accountNumber"]], m["hashValue"]
            raw_orders = await self._get(
                f"/trader/v1/accounts/{account_hash}/orders",
                {
                    "fromEnteredTime": (now - timedelta(days=60)).isoformat(),
                    "toEnteredTime": now.isoformat(),
                    "maxResults": 3000,
                },
            )
            if len(raw_orders) >= 3000:
                raise RuntimeError("ORDERS_POSSIBLY_TRUNCATED")
            pending = list(raw_orders)
            while pending:
                o = pending.pop()
                pending.extend(o.get("childOrderStrategies", []))
                normalized = {
                    "order_ref": stable_id(ref, o["orderId"]),
                    "account_ref": ref,
                    "status": o.get("status"),
                    "as_of": now.isoformat(),
                    "stop_price": o.get("stopPrice"),
                    "order_type": o.get("orderType"),
                    "symbols": sorted(
                        {leg["instrument"]["symbol"] for leg in o.get("orderLegCollection", [])}
                    ),
                }
                order_history.append(normalized)
                if o.get("status") not in ("FILLED", "CANCELED", "REJECTED", "EXPIRED", "REPLACED"):
                    orders.append(normalized)
            raw_transactions = await self._get(
                f"/trader/v1/accounts/{account_hash}/transactions",
                {
                    "startDate": (now - timedelta(days=60)).isoformat(),
                    "endDate": now.isoformat(),
                    "types": "TRADE",
                },
            )
            for t in raw_transactions:
                transactions.append(
                    {
                        "transaction_ref": stable_id(ref, t["activityId"]),
                        "account_ref": ref,
                        "time": t.get("time"),
                        "type": t.get("type"),
                        "net_amount": t.get("netAmount"),
                        "legs": [
                            {
                                "symbol": leg.get("instrument", {}).get("symbol"),
                                "quantity": leg.get("amount"),
                                "price": leg.get("price"),
                            }
                            for leg in t.get("transferItems", [])
                        ],
                    }
                )
        return normalize_accounts(accounts, refs, orders, transactions, now, order_history)

    async def get_quote(self, symbol):
        data = await self._get("/marketdata/v1/quotes", {"symbols": symbol})
        item = data[symbol]
        quote = item["quote"]
        timestamp = datetime.fromtimestamp(quote["tradeTime"] / 1000, UTC)
        return Quote(
            snapshot_id=stable_id("SCHWAB", symbol, quote),
            symbol=symbol,
            timestamp=timestamp,
            price=quote["lastPrice"],
            bid=quote.get("bidPrice"),
            ask=quote.get("askPrice"),
            source="SCHWAB",
            is_delayed=item.get("realtime") is False if "realtime" in item else None,
            session=session_at(timestamp),
        )

    async def get_history(self, symbol, timeframe, start_time, end_time):
        if timeframe not in ("1d", "1h", "15m"):
            raise ValueError("Unsupported timeframe")
        params = {
            "symbol": symbol,
            "frequencyType": "daily" if timeframe == "1d" else "minute",
            "frequency": 1 if timeframe == "1d" else (30 if timeframe == "1h" else 15),
            "startDate": int(start_time.timestamp() * 1000),
            "endDate": int(end_time.timestamp() * 1000),
            "needExtendedHoursData": "false",
        }
        raw = await self._get("/marketdata/v1/pricehistory", params)
        grouped = {}
        for candle in raw.get("candles", []):
            timestamp = datetime.fromtimestamp(candle["datetime"] / 1000, UTC)
            bounds = session_bounds(timestamp.astimezone(NY).date())
            if not bounds:
                raise ValueError("OFF_CALENDAR_CANDLE")
            opening, closing = bounds
            if timeframe == "1d":
                end = closing
            else:
                if not opening <= timestamp < closing:
                    raise ValueError("OUT_OF_SESSION_CANDLE")
                steps = int((timestamp - opening).total_seconds() // (3600 if timeframe == "1h" else 900)) + 1
                end = min(opening + timedelta(minutes=steps * (60 if timeframe == "1h" else 15)), closing)
            if end <= end_time:  # Exclude incomplete bars explicitly.
                grouped.setdefault(end, []).append((timestamp, candle))
        bars = []
        for end, entries in sorted(grouped.items()):
            entries.sort(key=lambda x: x[0])
            times = [t for t, _ in entries]
            if len(times) != len(set(times)):
                raise ValueError("DUPLICATE_PROVIDER_CANDLES")
            if timeframe != "1d":
                opening, _ = session_bounds(end.astimezone(NY).date())
                ends = expected_bar_times(opening, end, timeframe)
                start = ends[-2] if len(ends) > 1 else opening
                step = timedelta(minutes=30 if timeframe == "1h" else 15)
                expected = [start + i * step for i in range(int((end - start) / step))]
                if times != expected:
                    raise ValueError("INCOMPLETE_INTRADAY_AGGREGATION")
            cs = [c for _, c in entries]
            bars.append(
                Bar(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=end,
                    open=cs[0]["open"],
                    close=cs[-1]["close"],
                    high=max(c["high"] for c in cs),
                    low=min(c["low"] for c in cs),
                    volume=sum(c["volume"] for c in cs),
                    source="SCHWAB",
                    adjustment=self.settings.schwab_history_adjustment,
                )
            )
        return bars

    async def get_market_status(self):
        return session_at(utcnow())

    async def aclose(self):
        await self.client.aclose()
        await self.auth.client.aclose()
