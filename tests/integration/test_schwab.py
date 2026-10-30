from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet

from trading_agent.connectors.schwab.auth import AuthorizationRequired, EncryptedVault, SchwabAuth
from trading_agent.connectors.schwab.client import SchwabReadOnly
from trading_agent.core.models import utcnow
from trading_agent.portfolio.engine import normalize_accounts


def auth_settings(settings):
    return settings.model_copy(
        update={
            "schwab_client_id": settings.admin_api_key,
            "schwab_client_secret": settings.admin_api_key,
            "schwab_token_encryption_key": type(settings.admin_api_key)(Fernet.generate_key().decode()),
            "schwab_redirect_uri": "https://127.0.0.1/callback",
            "schwab_refresh_token_max_age_seconds": 600,
        }
    )


def test_encrypted_storage_and_tamper_detection(tmp_path):
    path = tmp_path / "token.enc"
    vault = EncryptedVault(path, Fernet.generate_key().decode())
    vault.write({"access_token": "super-secret"})
    assert b"super-secret" not in path.read_bytes()
    assert vault.read()["access_token"] == "super-secret"
    path.write_bytes(b"tampered")
    with pytest.raises(AuthorizationRequired):
        vault.read()


async def test_oauth_state_rotation_and_fixed_refresh_expiry(settings):
    s = auth_settings(settings)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "test-access",
                "refresh_token": f"refresh-{len(calls)}",
                "expires_in": 1800,
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    auth = SchwabAuth(s, client)
    url = auth.authorization_url()
    state = parse_qs(urlparse(url).query)["state"][0]
    with pytest.raises(AuthorizationRequired):
        await auth.exchange_redirect(f"https://127.0.0.1/callback?code=test&state=wrong-{state}")
    await auth.exchange_redirect(f"https://127.0.0.1/callback?code=test&state={state}")
    old = auth.vault.read()
    await auth.access_token(force=True)
    new = auth.vault.read()
    assert new["refresh_token"] != old["refresh_token"]
    assert new["refresh_deadline"] == old["refresh_deadline"]
    assert all(r.method == "POST" and r.url.path == "/v1/oauth/token" for r in calls)
    with pytest.raises(AuthorizationRequired):
        await auth.exchange_redirect(f"https://127.0.0.1/callback?code=test&state={state}")
    auth.vault.write({**new, "refresh_deadline": (utcnow() - timedelta(seconds=1)).isoformat()})
    with pytest.raises(AuthorizationRequired):
        await auth.access_token()
    await client.aclose()


async def test_token_failure_does_not_leak_body(settings):
    s = auth_settings(settings)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(401, json={"secret": "never-log-this"}))
    )
    auth = SchwabAuth(s, client)
    with pytest.raises(AuthorizationRequired) as exc:
        await auth._token({"grant_type": "authorization_code", "code": "secret"})
    assert "never-log-this" not in str(exc.value)
    await client.aclose()


async def test_schwab_quote_hour_aggregation_and_retry(settings):
    s = auth_settings(settings).model_copy(update={"schwab_history_adjustment": "UNADJUSTED"})
    calls, failures = [], []

    async def token(force=False):
        return "mock-token"

    now = datetime(2026, 10, 29, 15, 30, tzinfo=UTC)
    candles = [
        {
            "datetime": int((now - timedelta(hours=2) + timedelta(minutes=30 * i)).timestamp() * 1000),
            "open": 100 + i,
            "high": 102 + i,
            "low": 99 + i,
            "close": 101 + i,
            "volume": 100,
        }
        for i in range(4)
    ]

    def handle(request):
        calls.append(request)
        if request.url.path.endswith("quotes"):
            if not failures:
                failures.append(True)
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(
                200,
                json={
                    "GOOG": {
                        "realtime": True,
                        "quote": {
                            "tradeTime": int(now.timestamp() * 1000),
                            "lastPrice": 100,
                            "bidPrice": 99.9,
                            "askPrice": 100.1,
                        },
                    }
                },
            )
        return httpx.Response(200, json={"candles": candles})

    http = httpx.AsyncClient(base_url="https://api.schwabapi.com", transport=httpx.MockTransport(handle))
    auth = SchwabAuth(s, http)
    auth.access_token = token
    connector = SchwabReadOnly(s, http, auth)
    quote = await connector.get_quote("GOOG")
    assert quote.is_delayed is False and quote.timestamp == now
    bars = await connector.get_history("GOOG", "1h", now - timedelta(days=1), now)
    assert len(bars) == 2 and bars[0].volume == 200 and bars[0].close == 102
    assert bars[0].timestamp.hour == 14 and bars[0].timestamp.minute == 30
    assert all(r.method == "GET" for r in calls)
    candles.pop(0)
    with pytest.raises(ValueError, match="INCOMPLETE_INTRADAY_AGGREGATION"):
        await connector.get_history("GOOG", "1h", now - timedelta(days=1), now)
    await connector.aclose()


def test_portfolio_reconciliation_and_cost_separation():
    now = utcnow()
    account = {
        "securitiesAccount": {
            "accountNumber": "private-account",
            "positions": [
                {
                    "instrument": {"symbol": "GOOG", "assetType": "EQUITY"},
                    "longQuantity": 10,
                    "shortQuantity": 0,
                    "averagePrice": 100,
                    "marketValue": 1100,
                }
            ],
            "currentBalances": {"cashBalance": 900, "liquidationValue": 2000},
        }
    }
    p = normalize_accounts([account], {"private-account": "internal"}, [], [], now)
    assert p.reconciliation == "MATCHED" and p.positions[0].unrealized_pnl == 100
    assert p.positions[0].tax_cost_basis is None and p.positions[0].manual_average_cost is None
    assert "private-account" not in p.model_dump_json()
    account["securitiesAccount"]["currentBalances"]["liquidationValue"] = 3000
    assert (
        normalize_accounts([account], {"private-account": "internal"}, [], [], now).reconciliation
        == "RECONCILIATION_REQUIRED"
    )
    assert (
        "PARTIAL_ACCOUNT_RESPONSE"
        in normalize_accounts([], {"private-account": "internal"}, [], [], now).issues
    )
