"""`kimi-account` provider — Kimi Code models through a Kimi subscription.

Wraps `OpenAIProvider` with automatic OAuth access token refresh via `KimiTokenStore`.
When an access token approaches expiry, it refreshes silently. If a 401 AuthenticationError
occurs mid-flight, it refreshes the token and transparently retries the turn once.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .base import AssistantTurn
from .kimi_auth import CODING_BASE_URL, KimiTokenStore
from .openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)


def _status_code(exc: Exception) -> Optional[int]:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


class KimiAccountProvider(OpenAIProvider):
    def __init__(
        self,
        client: Any = None,
        *,
        api_key: Optional[str] = None,
        secrets: Any = None,
        default_model: str = "k3.1-pro",
        base_url: str = CODING_BASE_URL,
    ):
        super().__init__(
            client=client,
            default_model=default_model,
            api_key=api_key,
            base_url=base_url,
            secrets=secrets,
        )
        self._store = KimiTokenStore(secrets)
        self._client_token: Optional[str] = None
        self._injected = client is not None

    def _ensure_client(self) -> Any:
        if self._injected:
            return self._client

        # The OAuth bearer is short-lived: fetch via store (auto-refreshes near expiry)
        token = ""
        if self._store.signed_in():
            try:
                token = self._store.access_token()
            except Exception as exc:
                logger.warning("Failed to resolve Kimi access token from store: %s", exc)
        if not token:
            token = self._api_key or ""
        if not token:
            raise RuntimeError("Kimi 账号未登录或未设置 Token —— 请在模型与密钥设置中登录。")

        if self._client is None or token != self._client_token:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=token,
                base_url=self._base_url or CODING_BASE_URL,
            )
            self._client_token = token
        return self._client

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        **settings: Any,
    ) -> AssistantTurn:
        try:
            return super().complete(model=model, messages=messages, tools=tools, **settings)
        except Exception as exc:
            if _status_code(exc) == 401 and not self._injected and self._store.signed_in():
                logger.info("Kimi access token returned 401, refreshing token and retrying complete...")
                try:
                    self._store.refresh()
                    self._client = None
                    self._client_token = None
                    return super().complete(model=model, messages=messages, tools=tools, **settings)
                except Exception as refresh_exc:
                    logger.warning("Kimi token refresh after 401 failed: %s", refresh_exc)
            raise

    def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        **settings: Any,
    ):
        try:
            gen = super().stream(model=model, messages=messages, tools=tools, **settings)
            first = next(gen)
        except StopIteration:
            return
        except Exception as exc:
            if _status_code(exc) == 401 and not self._injected and self._store.signed_in():
                logger.info("Kimi access token returned 401, refreshing token and retrying stream...")
                try:
                    self._store.refresh()
                    self._client = None
                    self._client_token = None
                    yield from super().stream(model=model, messages=messages, tools=tools, **settings)
                    return
                except Exception as refresh_exc:
                    logger.warning("Kimi token refresh after 401 failed: %s", refresh_exc)
            raise

        yield first
        yield from gen
