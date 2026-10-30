import asyncio
import json
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken

from trading_agent.core.models import utcnow


class AuthorizationRequired(RuntimeError):
    pass


class EncryptedVault:
    def __init__(self, path: Path, key: str):
        self.path = path
        self.cipher = Fernet(key.encode())

    def read(self):
        if not self.path.exists():
            raise AuthorizationRequired("SCHWAB_AUTHORIZATION_REQUIRED")
        try:
            return json.loads(self.cipher.decrypt(self.path.read_bytes()))
        except (InvalidToken, ValueError) as exc:
            raise AuthorizationRequired("SCHWAB_TOKEN_STORE_INVALID") from exc

    def write(self, payload):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(self.cipher.encrypt(json.dumps(payload).encode()))
        os.replace(temporary, self.path)
        if os.name != "nt":
            self.path.chmod(0o600)


class SchwabAuth:
    def __init__(self, settings, client=None):
        self.settings = settings
        if not all(
            (
                settings.schwab_client_id.get_secret_value(),
                settings.schwab_client_secret.get_secret_value(),
                settings.schwab_token_encryption_key.get_secret_value(),
                settings.schwab_redirect_uri,
            )
        ):
            raise AuthorizationRequired("SCHWAB_CONFIGURATION_REQUIRED")
        key = settings.schwab_token_encryption_key.get_secret_value()
        self.vault = EncryptedVault(settings.data_dir / "schwab-token.enc", key)
        self.state = EncryptedVault(settings.data_dir / "schwab-state.enc", key)
        self.client = client or httpx.AsyncClient(timeout=30, follow_redirects=False)
        self.lock = asyncio.Lock()

    def authorization_url(self):
        state = secrets.token_urlsafe(32)
        self.state.write({"state": state, "created_at": utcnow().isoformat(), "used": False})
        return "https://api.schwabapi.com/v1/oauth/authorize?" + urlencode(
            {
                "client_id": self.settings.schwab_client_id.get_secret_value(),
                "response_type": "code",
                "redirect_uri": self.settings.schwab_redirect_uri,
                "state": state,
            }
        )

    async def exchange_redirect(self, redirect_url):
        parsed, expected = urlparse(redirect_url), urlparse(self.settings.schwab_redirect_uri)
        # Browsers serialize an empty root path as '/'. Non-root paths must still match exactly.
        if (parsed.scheme, parsed.netloc, parsed.path or "/") != (
            expected.scheme,
            expected.netloc,
            expected.path or "/",
        ):
            raise AuthorizationRequired("OAUTH_REDIRECT_MISMATCH")
        params, saved = parse_qs(parsed.query), self.state.read()
        if (
            saved["used"]
            or (utcnow() - datetime.fromisoformat(saved["created_at"])).total_seconds() > 600
            or not secrets.compare_digest(params.get("state", [""])[0], saved["state"])
        ):
            raise AuthorizationRequired("OAUTH_STATE_INVALID")
        code = params.get("code", [None])[0]
        if not code:
            raise AuthorizationRequired("OAUTH_CODE_MISSING")
        self.state.write({**saved, "used": True})
        await self._token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.settings.schwab_redirect_uri,
            }
        )

    async def _token(self, data, previous=None):
        try:
            response = await self.client.post(
                "https://api.schwabapi.com/v1/oauth/token",
                data=data,
                auth=(
                    self.settings.schwab_client_id.get_secret_value(),
                    self.settings.schwab_client_secret.get_secret_value(),
                ),
            )
        except httpx.HTTPError as exc:
            raise AuthorizationRequired("SCHWAB_AUTH_NETWORK_ERROR") from exc
        if response.status_code != 200:
            raise AuthorizationRequired(f"SCHWAB_REAUTHORIZE_HTTP_{response.status_code}")
        token, now = response.json(), utcnow()
        if (
            not token.get("access_token")
            or not isinstance(token.get("expires_in"), (float, int))
            or token["expires_in"] <= 0
            or not (token.get("refresh_token") or (previous or {}).get("refresh_token"))
        ):
            raise AuthorizationRequired("SCHWAB_TOKEN_RESPONSE_INVALID")
        max_age = token.get("refresh_token_expires_in") or self.settings.schwab_refresh_token_max_age_seconds
        if not previous and not max_age:
            raise AuthorizationRequired("VERIFY_REFRESH_TOKEN_LIFETIME_IN_PORTAL")
        # Refresh rotation must never extend the original authorization deadline by assumption.
        deadline = (
            previous["refresh_deadline"] if previous else (now + timedelta(seconds=max_age)).isoformat()
        )
        self.vault.write(
            {
                "access_token": token["access_token"],
                "refresh_token": token.get("refresh_token") or (previous or {}).get("refresh_token"),
                "access_deadline": (now + timedelta(seconds=token["expires_in"])).isoformat(),
                "refresh_deadline": deadline,
            }
        )

    async def access_token(self, force=False):
        async with self.lock:
            token = self.vault.read()
            if utcnow() >= datetime.fromisoformat(token["refresh_deadline"]):
                raise AuthorizationRequired("SCHWAB_REAUTHORIZATION_REQUIRED")
            if force or utcnow() + timedelta(seconds=60) >= datetime.fromisoformat(token["access_deadline"]):
                await self._token(
                    {"grant_type": "refresh_token", "refresh_token": token["refresh_token"]}, token
                )
                token = self.vault.read()
            return token["access_token"]
