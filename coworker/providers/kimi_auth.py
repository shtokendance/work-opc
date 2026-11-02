"""Kimi Code subscription sign-in for the `kimi-account` provider (OAuth 2.0 Device Flow).

Instead of a manual API key, the user signs in with their Kimi account:
a Device Authorization Grant flow opens the browser to Kimi's authorization page.
Once the user confirms authorization in the browser, tokens land in the SecretStore
profile `provider:kimi-account`.
"""

from __future__ import annotations

import asyncio
import logging
import time
import webbrowser
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

CLIENT_ID = "17e5f671-d194-4dfb-9706-5516cb48c098"
DEVICE_AUTH_URL = "https://auth.kimi.com/api/oauth/device_authorization"
TOKEN_URL = "https://auth.kimi.com/api/oauth/token"
CODING_BASE_URL = "https://api.kimi.com/coding/v1"
PROFILE = "provider:kimi-account"
FLOW_TIMEOUT_SECONDS = 300

last_authorize_url: Optional[str] = None


class KimiTokenStore:
    """Token set + metadata in the `provider:kimi-account` SecretStore profile."""

    def __init__(self, secrets: Any) -> None:
        self._secrets = secrets

    def _data(self) -> dict[str, Any]:
        if self._secrets is None:
            return {}
        return self._secrets.get(PROFILE) or {}

    def _merge(self, patch: dict[str, Any]) -> None:
        if self._secrets is not None:
            self._secrets.put(PROFILE, {**self._data(), **patch})

    def signed_in(self) -> bool:
        data = self._data()
        return bool(data.get("tokens") or data.get("api_key"))

    def account_label(self) -> Optional[str]:
        data = self._data()
        return data.get("account") or data.get("account_email") or ("Kimi 账号" if self.signed_in() else None)

    def save(self, tokens: dict[str, Any], account: Optional[str] = None) -> None:
        existing = self._data().get("tokens") or {}
        merged = {
            k: (tokens.get(k) or existing.get(k))
            for k in ("access_token", "refresh_token", "token_type", "expires_in")
        }
        merged = {k: v for k, v in merged.items() if v}
        access_token = merged.get("access_token", "")
        patch: dict[str, Any] = {
            "tokens": merged,
            "api_key": access_token,
            "base_url": CODING_BASE_URL,
            "tokens_issued_at": int(time.time()),
        }
        if account:
            patch["account"] = account
        self._merge(patch)

    def clear(self) -> bool:
        if self._secrets is None:
            return False
        had = self.signed_in()
        self._secrets.delete(PROFILE)
        return had


async def sign_in(
    secrets: Any,
    *,
    timeout: float = FLOW_TIMEOUT_SECONDS,
    open_browser: bool = True,
) -> dict[str, Any]:
    """Run interactive Kimi Code device flow: request code -> open browser -> poll token."""
    global last_authorize_url

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            DEVICE_AUTH_URL,
            data={"client_id": CLIENT_ID},
            headers={"User-Agent": "Work-OPC/0.3.4"},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Kimi 授权服务响应异常 (HTTP {resp.status_code}): {resp.text}")
        data = resp.json()

    device_code = data.get("device_code")
    verification_uri = data.get("verification_uri_complete") or data.get("verification_uri")
    if not device_code or not verification_uri:
        raise RuntimeError("Kimi 授权初始化失败：未返回有效的授权地址。")

    last_authorize_url = verification_uri
    interval = max(int(data.get("interval") or 4), 2)

    if open_browser:
        logger.info("Opening browser for Kimi authorization: %s", verification_uri)
        await asyncio.get_running_loop().run_in_executor(None, webbrowser.open, verification_uri)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(interval)
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                poll_resp = await client.post(
                    TOKEN_URL,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "client_id": CLIENT_ID,
                        "device_code": device_code,
                    },
                    headers={"User-Agent": "Work-OPC/0.3.4"},
                )
        except Exception:
            continue

        if poll_resp.status_code == 200:
            tokens = poll_resp.json()
            store = KimiTokenStore(secrets)
            store.save(tokens)
            return {"ok": True, "signed_in": True}

        err_body = {}
        try:
            err_body = poll_resp.json()
        except Exception:
            pass

        err = err_body.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 2
            continue
        if err in ("expired_token", "access_denied"):
            desc = err_body.get("error_description") or err
            raise RuntimeError(f"Kimi 授权取消或已过期 ({desc})")

    raise RuntimeError("Kimi 授权超时，请重试。")
