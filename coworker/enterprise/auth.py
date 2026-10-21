"""Enterprise login against the OPC platform (INT-101a-f, BND-101/102/160).

State machine (spec §2):

    start_login --浏览器完成企微授权--> callback(code, state)
    callback --兑换成功--> context 建立（issuer|subject|tenant|generation 键隔离）
    callback --兑换失败--> 错误码透传（BND-180 形状）+ 事务 failed（终态）
    refresh --轮换--> 新 access（内存）+ 新 refresh（一次性交还 renderer 入 Keychain）
    refresh --401 REFRESH_REUSE_DETECTED--> 本地企业上下文清除
    logout --撤销--> 平台 logout + 本地上下文清除

Refresh tokens are NEVER persisted here: the one minted at login is handed to
the renderer once (one-time read from `login_result`), and each refresh call
returns the rotated refresh the same way. The sidecar keeps only the in-memory
access token (``store``).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets as pysecrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from .store import EnterpriseCredentialStore, EnterpriseProfileMeta, KeychainStore

logger = logging.getLogger("coworker.enterprise.auth")

PLATFORM_TIMEOUT = 15.0  # seconds, per TASK-W02 (all platform calls)
CLIENT_ID = "work-desktop"  # INT-101a pre-registered id

# BND-180 stable codes reused/passed through (platform is the source of truth
# for its own codes; these two are produced locally).
AUTH_STATE_MISMATCH = "AUTH_STATE_MISMATCH"
UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
ENTERPRISE_NOT_CONFIGURED = "ENTERPRISE_NOT_CONFIGURED"
REFRESH_REUSE_DETECTED = "REFRESH_REUSE_DETECTED"

_TXN_TTL_SECONDS = 600.0  # login transaction window (spec: 回调超时 10min)
REFRESH_LEEWAY_SECONDS = 120.0  # refresh when this close to access expiry


def _err(code: str, message: str, request_id: str = "") -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id or str(uuid.uuid4()),
        }
    }


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@dataclass
class EnterpriseContext:
    """The live enterprise session, keyed for BND-120 isolation."""

    issuer: str
    subject: str
    tenant: str
    generation: int
    authz_version: int
    spaces: list[str]
    access_token: str = field(repr=False, default="")  # memory-only, never logged
    obtained_at: float = field(default_factory=time.time)
    expires_in: int = 900
    epoch: int = 0

    @property
    def key(self) -> str:
        return f"{self.issuer}|{self.subject}|{self.tenant}|{self.generation}"

    def seconds_until_expiry(self, *, now: Optional[float] = None) -> float:
        """Lifetime left on the in-memory access token (≤ 0 = expired)."""
        at = time.time() if now is None else now
        return (self.obtained_at + self.expires_in) - at

    def access_expired(self, *, now: Optional[float] = None) -> bool:
        return self.seconds_until_expiry(now=now) <= 0

    def refresh_recommended(self, *, now: Optional[float] = None) -> bool:
        """True inside the leeway window before access expiry — the signal the
        GUI consumes to refresh BEFORE enterprise calls start 401ing."""
        return self.seconds_until_expiry(now=now) <= REFRESH_LEEWAY_SECONDS

    def meta(self) -> EnterpriseProfileMeta:
        return EnterpriseProfileMeta(
            issuer=self.issuer,
            subject=self.subject,
            tenant=self.tenant,
            generation=self.generation,
        )

    def public_view(self) -> dict[str, Any]:
        """What the API exposes: identity + versions + expiry hints, NEVER the
        access token. `seconds_until_expiry` / `refresh_recommended` let the
        GUI refresh before the 15-minute access dies (BND-102/160)."""
        return {
            "issuer": self.issuer,
            "subject": self.subject,
            "tenant": self.tenant,
            "generation": self.generation,
            "authz_version": self.authz_version,
            "spaces": list(self.spaces),
            "epoch": self.epoch,
            "obtained_at": self.obtained_at,
            "expires_in": self.expires_in,
            "seconds_until_expiry": int(self.seconds_until_expiry()),
            "refresh_recommended": self.refresh_recommended(),
        }


@dataclass
class LoginTransaction:
    login_txn_id: str
    state: str
    code_verifier: str
    created_at: float = field(default_factory=time.time)
    status: str = "pending"  # pending | succeeded | failed (terminal states stick)
    error: Optional[dict[str, Any]] = None
    context_key: str = ""
    _refresh_token: Optional[str] = field(repr=False, default=None)
    _refresh_handed_off: bool = False

    def expired(self) -> bool:
        return self.status == "pending" and (
            time.time() - self.created_at > _TXN_TTL_SECONDS
        )

    def handoff_refresh(self) -> Optional[str]:
        """One-time presentation of the refresh token to the renderer. The copy
        held here is cleared in the same breath — the sidecar must not keep a
        refresh resident in memory after the handoff (BND-160 hygiene)."""
        if self._refresh_handed_off:
            return None
        self._refresh_handed_off = True
        token = self._refresh_token
        self._refresh_token = None
        return token


class EnterpriseAuthService:
    """Drives INT-101 against one OPC platform deployment."""

    def __init__(
        self,
        platform_base: str = "",
        *,
        client_id: str = CLIENT_ID,
        store: Optional[EnterpriseCredentialStore] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.platform_base = platform_base.rstrip("/")
        self.client_id = client_id
        self.store: EnterpriseCredentialStore = store or KeychainStore()
        # Tests inject a client; production builds one per call from platform_base.
        self._client = http_client
        self._txns: dict[str, LoginTransaction] = {}  # by login_txn_id
        self._by_state: dict[str, str] = {}  # one-time state -> login_txn_id
        self._context: Optional[EnterpriseContext] = None
        self._epoch = 0
        # Context keys invalidated by a switch (BND-120) — observable for tests/audit.
        self.orphaned_context_keys: list[str] = []

    @classmethod
    def from_config(cls, config: Any) -> "EnterpriseAuthService":
        return cls(getattr(config, "opc_platform_base", "") or "")

    @property
    def configured(self) -> bool:
        return bool(self.platform_base)

    @property
    def context(self) -> Optional[EnterpriseContext]:
        return self._context

    # -- platform plumbing ------------------------------------------------------

    async def _post(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """POST to the platform; transport/malformed failures become BND-180 errors."""
        if self._client is not None:
            try:
                resp = await self._client.post(path, json=payload)
            except httpx.HTTPError as exc:
                return 502, _err(UPSTREAM_UNAVAILABLE, f"platform unreachable: {exc}")
            return resp.status_code, self._decode(resp)
        if not self.platform_base:
            return 400, _err(
                ENTERPRISE_NOT_CONFIGURED, "enterprise login is not configured"
            )
        try:
            async with httpx.AsyncClient(
                base_url=self.platform_base, timeout=PLATFORM_TIMEOUT
            ) as client:
                resp = await client.post(path, json=payload)
        except httpx.HTTPError as exc:
            return 502, _err(UPSTREAM_UNAVAILABLE, f"platform unreachable: {exc}")
        return resp.status_code, self._decode(resp)

    @staticmethod
    def _decode(resp: httpx.Response) -> dict[str, Any]:
        try:
            body = resp.json()
        except ValueError:
            return _err(UPSTREAM_UNAVAILABLE, f"platform returned non-JSON ({resp.status_code})")
        if resp.status_code >= 400 and "error" not in body:
            return _err(UPSTREAM_UNAVAILABLE, f"platform error {resp.status_code}")
        return body

    def _device_fingerprint(self) -> str:
        # Stable per machine, not a secret — INT-101a binds device to the txn.
        node = uuid.getnode()
        return hashlib.sha256(str(node).encode()).hexdigest()[:32]

    # -- INT-101a: start ---------------------------------------------------------

    def _sweep_txns(self) -> None:
        """Drop terminal (succeeded/failed) and expired login transactions —
        they are only needed until the GUI reads their outcome once, and a
        terminal txn must not linger (its refresh copy is already cleared).
        Runs at each new login start; the GUI polls the CURRENT txn before it
        ever starts another."""
        dead = [
            txn_id
            for txn_id, txn in self._txns.items()
            if txn.status != "pending" or txn.expired()
        ]
        for txn_id in dead:
            txn = self._txns.pop(txn_id)
            txn._refresh_token = None
            self._by_state.pop(txn.state, None)

    async def start_login(self, redirect_uri: str) -> dict[str, Any]:
        """Create a login transaction and ask the platform for the authorize URL."""
        if not self.configured and self._client is None:
            return {"ok": False, **_err(ENTERPRISE_NOT_CONFIGURED, "enterprise login is not configured")}
        self._sweep_txns()
        verifier = pysecrets.token_urlsafe(48)
        txn = LoginTransaction(
            login_txn_id=str(uuid.uuid4()),
            state=pysecrets.token_urlsafe(24),
            code_verifier=verifier,
        )
        status, body = await self._post(
            "/integration/v1/auth/authorize",
            {
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "state": txn.state,
                "code_challenge": _s256(verifier),
                "code_challenge_method": "S256",
                "device": self._device_fingerprint(),
            },
        )
        if status >= 400:
            logger.info("enterprise authorize failed: %s", body.get("error", {}).get("code"))
            return {"ok": False, **body}
        authorize_url = body.get("authorize_url")
        if not authorize_url:
            return {
                "ok": False,
                **_err(UPSTREAM_UNAVAILABLE, "platform authorize response missing authorize_url"),
            }
        # The platform's own login_txn_id identifies the browser-side flow; ours
        # tracks the local exchange. Keep both.
        txn_id = str(body.get("login_txn_id") or txn.login_txn_id)
        txn.login_txn_id = txn_id
        self._txns[txn_id] = txn
        self._by_state[txn.state] = txn_id
        return {"ok": True, "authorize_url": authorize_url, "login_txn_id": txn_id}

    # -- INT-101b/c: loopback callback + code exchange ---------------------------

    async def complete_callback(
        self, code: str = "", state: str = "", error: str = ""
    ) -> dict[str, Any]:
        """One-time state consumption + platform token exchange.

        Returns {"ok": True, "login_txn_id", ...} or {"ok": False, "error": {...}}.
        """
        txn_id = self._by_state.pop(state, None)  # consume the state (重放 → 400)
        if txn_id is None:
            return {"ok": False, **_err(AUTH_STATE_MISMATCH, "unknown or already-used state")}
        txn = self._txns.get(txn_id)
        if txn is None or txn.expired():
            if txn is not None:
                txn.status = "failed"
                txn.error = _err(AUTH_STATE_MISMATCH, "login transaction expired")
            return {
                "ok": False,
                **(txn.error if txn else _err(AUTH_STATE_MISMATCH, "unknown login transaction")),
            }
        if error:
            txn.status = "failed"
            txn.error = _err(AUTH_STATE_MISMATCH, f"platform returned error: {error}")
            return {"ok": False, "login_txn_id": txn_id, **txn.error}
        if not code:
            txn.status = "failed"
            txn.error = _err(AUTH_STATE_MISMATCH, "callback missing code")
            return {"ok": False, "login_txn_id": txn_id, **txn.error}
        status, body = await self._post(
            "/integration/v1/auth/token",
            {
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "code": code,
                "code_verifier": txn.code_verifier,
                "device": self._device_fingerprint(),
            },
        )
        if status >= 400:
            # BND-180: platform codes pass through untouched (可观察状态).
            txn.status = "failed"
            txn.error = body if "error" in body else _err(UPSTREAM_UNAVAILABLE, str(body))
            logger.info(
                "enterprise token exchange failed: %s",
                txn.error.get("error", {}).get("code"),
            )
            return {"ok": False, "login_txn_id": txn_id, **txn.error}
        access = body.get("access_token")
        if not access:
            txn.status = "failed"
            txn.error = _err(UPSTREAM_UNAVAILABLE, "token response missing access_token")
            return {"ok": False, "login_txn_id": txn_id, **txn.error}
        ctx = self._context_from_token(body, access)
        self._establish_context(ctx)
        txn.status = "succeeded"
        txn.context_key = ctx.key
        txn._refresh_token = body.get("refresh_token")
        logger.info(
            "enterprise login established: subject=%s tenant=%s generation=%s",
            ctx.subject,
            ctx.tenant,
            ctx.generation,
        )
        return {"ok": True, "login_txn_id": txn_id, "context": ctx.public_view()}

    def _context_from_token(self, body: dict[str, Any], access: str) -> EnterpriseContext:
        self._epoch += 1
        return EnterpriseContext(
            issuer=str(body.get("issuer") or self.platform_base or "opc"),
            subject=str(body.get("subject") or body.get("immutable_subject") or ""),
            tenant=str(body.get("tenant") or body.get("tenant_id") or ""),
            generation=int(body.get("generation") or 0),
            authz_version=int(body.get("authz_version") or 0),
            spaces=[str(s) for s in body.get("spaces") or []],
            access_token=access,
            expires_in=int(body.get("expires_in") or 900),
            epoch=self._epoch,
        )

    def _establish_context(self, ctx: EnterpriseContext) -> None:
        """BND-120: a different key orphans the previous context immediately."""
        if self._context is not None and self._context.key != ctx.key:
            self.orphaned_context_keys.append(self._context.key)
            logger.info(
                "enterprise context switched: %s -> %s (old approvals/sessions orphaned)",
                self._context.key,
                ctx.key,
            )
        self._context = ctx
        profile = ctx.meta().context_key()
        self.store.set_access_token(profile, ctx.access_token)
        self.store.set_meta(profile, ctx.meta())

    # -- observable login state (GUI polls this) ---------------------------------

    def login_result(self, login_txn_id: str) -> dict[str, Any]:
        txn = self._txns.get(login_txn_id)
        if txn is None:
            return {"ok": False, **_err(AUTH_STATE_MISMATCH, "unknown login_txn_id")}
        if txn.expired():
            txn.status = "failed"
            txn.error = txn.error or _err(AUTH_STATE_MISMATCH, "login transaction expired")
        out: dict[str, Any] = {
            "ok": txn.status != "failed",
            "login_txn_id": login_txn_id,
            "state": txn.status,
        }
        if txn.error:
            out["error"] = txn.error["error"]
        if txn.status == "succeeded":
            out["context"] = self._context.public_view() if self._context else None
            refresh = txn.handoff_refresh()
            if refresh is not None:
                out["refresh_token"] = refresh  # one-time handoff to the Keychain writer
        return out

    # -- INT-101d: refresh --------------------------------------------------------

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        """Rotate via the platform. New refresh is one-time handed back (Keychain)."""
        if not refresh_token:
            return {"ok": False, **_err(AUTH_STATE_MISMATCH, "refresh_token required")}
        status, body = await self._post(
            "/integration/v1/auth/refresh",
            {
                "client_id": self.client_id,
                "refresh_token": refresh_token,
                "device": self._device_fingerprint(),
            },
        )
        if status >= 400:
            code = (body.get("error") or {}).get("code", "")
            if code == REFRESH_REUSE_DETECTED:
                # BND-102: the whole family is revoked — clear local context too.
                self._clear_local_context(reason="refresh_reuse_detected")
            logger.info("enterprise refresh failed: %s", code)
            return {"ok": False, **body}
        access = body.get("access_token")
        if not access:
            return {
                "ok": False,
                **_err(UPSTREAM_UNAVAILABLE, "refresh response missing access_token"),
            }
        if self._context is not None:
            self._context.access_token = access
            self._context.obtained_at = time.time()
            self._context.expires_in = int(body.get("expires_in") or 900)
            profile = self._context.meta().context_key()
            self.store.set_access_token(profile, access)
        return {"ok": True, "refresh_token": body.get("refresh_token") or ""}

    # -- INT-101e: logout ----------------------------------------------------------

    async def logout(self, refresh_token: str = "") -> dict[str, Any]:
        """Revoke at the platform (best-effort) and clear everything local."""
        access = self._context.access_token if self._context is not None else ""
        headers = {"Authorization": f"Bearer {access}"} if access else {}
        if self._client is not None:
            try:
                await self._client.post(
                    "/integration/v1/auth/logout",
                    json={"client_id": self.client_id, "refresh_token": refresh_token},
                    headers=headers,
                )
            except httpx.HTTPError:
                pass  # local clear proceeds regardless
        elif self.platform_base:
            try:
                async with httpx.AsyncClient(
                    base_url=self.platform_base, timeout=PLATFORM_TIMEOUT
                ) as client:
                    await client.post(
                        "/integration/v1/auth/logout",
                        json={"client_id": self.client_id, "refresh_token": refresh_token},
                        headers=headers,
                    )
            except httpx.HTTPError:
                pass
        self._clear_local_context(reason="logout")
        return {"ok": True}

    def _clear_local_context(self, reason: str) -> None:
        if self._context is not None:
            profile = self._context.meta().context_key()
            self.store.clear(profile)
            self.orphaned_context_keys.append(self._context.key)
        self._context = None
        logger.info("enterprise context cleared (%s)", reason)

    # -- INT-101f: session -----------------------------------------------------------

    def session_view(self) -> Optional[dict[str, Any]]:
        return self._context.public_view() if self._context is not None else None
