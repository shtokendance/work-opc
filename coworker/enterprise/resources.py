"""Enterprise resource service (WORK-RESOURCES-001, INT-101/103/105, BND-002/008).

The sidecar's resource-facing half: list/inspect knowledge bases and skill
catalogs through the platform's NEW resources family, authorize individual
resources with a ≤30s fail-closed cache (TASK-020), and run bounded install
operations (TASK-015) on top of the atomic installer.

Failure semantics (INT-105):
* a STRUCTURAL 404 from the upstream resources family (an old platform that
  never heard of the new protocol) maps to 501 RESOURCE_API_UNSUPPORTED —
  never a fallback to the retired release protocol;
* a JSON 404 RESOURCE_NOT_ACCESSIBLE stays a per-resource denial;
* 202 on install is never success — the operation must reach a terminal
  state and only then reports ``installed_sha256``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import secrets as pysecrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx

from .auth import (
    ENTERPRISE_NOT_CONFIGURED,
    UPSTREAM_UNAVAILABLE,
    EnterpriseContext,
)
from . import installer
from .registry import SKILL_VERSION_CHANGED, SkillRef

logger = logging.getLogger("coworker.enterprise.resources")

# Stable local codes (INT-105 family).
INVALID_REQUEST = "INVALID_REQUEST"
SESSION_REVOKED = "SESSION_REVOKED"
CONTEXT_CHANGED = "CONTEXT_CHANGED"
SPACE_FORBIDDEN = "SPACE_FORBIDDEN"
RESOURCE_NOT_ACCESSIBLE = "RESOURCE_NOT_ACCESSIBLE"
SKILL_HASH_MISMATCH = "SKILL_HASH_MISMATCH"
ARCHIVE_NOT_READY = "ARCHIVE_NOT_READY"
OPERATION_IN_PROGRESS = "OPERATION_IN_PROGRESS"
SKILL_PACKAGE_TOO_LARGE = "SKILL_PACKAGE_TOO_LARGE"
SKILL_PACKAGE_UNSAFE = "SKILL_PACKAGE_UNSAFE"
INSTALL_FAILED = "INSTALL_FAILED"
RESOURCE_API_UNSUPPORTED = "RESOURCE_API_UNSUPPORTED"
SKILL_NOT_AVAILABLE = "SKILL_NOT_AVAILABLE"

LIST_TIMEOUT = 15.0
ARCHIVE_TOTAL_BUDGET = 120.0
ARCHIVE_CONNECT_TIMEOUT = 15.0
OPERATION_BUDGET_SECONDS = 300.0
GRANT_TTL_SECONDS = 30.0
MAX_DOWNLOAD_CONCURRENCY = 2

RESOURCE_ID_MAX_LEN = 128
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _err(code: str, message: str, request_id: str = "") -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id or str(uuid.uuid4()),
        }
    }


AuditFn = Callable[[dict[str, Any]], None]


def validate_resource_id(value: Any, what: str) -> str:
    """DATA-101: non-empty, ≤128 chars, single ID — no separators, control
    characters, or `..`. Raises ValueError."""
    if not isinstance(value, str):
        raise ValueError(f"{what} must be a string")
    value = value.strip()
    if not value or len(value) > RESOURCE_ID_MAX_LEN:
        raise ValueError(f"{what} must be 1..{RESOURCE_ID_MAX_LEN} chars")
    if (
        any(c in value for c in ("/", "\\", "\0"))
        or ".." in value
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError(f"{what} contains forbidden characters")
    return value


def normalize_sha256(value: Any) -> str:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value.strip().lower()):
        raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
    return value.strip().lower()


# -- platform client -----------------------------------------------------------------


class PlatformResourceClient:
    """Thin async client for the platform resources family (per-context token)."""

    def __init__(
        self,
        platform_base: str,
        context: EnterpriseContext,
        *,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._base = platform_base.rstrip("/")
        self._context = context
        self._client = http_client

    async def _get(self, path: str, *, timeout: float = LIST_TIMEOUT) -> tuple[int, dict[str, Any]]:
        base = self._base
        if not base:
            return 400, _err(ENTERPRISE_NOT_CONFIGURED, "enterprise platform is not configured")
        headers = {"Authorization": f"Bearer {self._context.access_token}"}
        try:
            if self._client is not None:
                resp = await self._client.get(f"{base}{path}", headers=headers)
            else:
                async with httpx.AsyncClient(
                    base_url=base,
                    timeout=httpx.Timeout(timeout, connect=timeout),
                ) as client:
                    resp = await client.get(path, headers=headers)
        except httpx.HTTPError as exc:
            return 502, _err(UPSTREAM_UNAVAILABLE, f"platform unreachable: {exc}")
        try:
            body = resp.json()
        except ValueError:
            # Structural (non-envelope) failure — an old platform that never
            # implemented the resources family surfaces here.
            return resp.status_code, _err(
                RESOURCE_API_UNSUPPORTED if resp.status_code == 404 else UPSTREAM_UNAVAILABLE,
                f"platform returned non-JSON ({resp.status_code})",
            )
        if not isinstance(body, dict):
            return resp.status_code, _err(UPSTREAM_UNAVAILABLE, "platform returned a non-object body")
        return resp.status_code, body

    @staticmethod
    def _map(status: int, body: dict[str, Any]) -> dict[str, Any]:
        """Platform envelope → local error view. Structural 404 on the
        resources family is UNKNOWN-PROTOCOL, never RESOURCE_NOT_ACCESSIBLE."""
        err = body.get("error") if isinstance(body, dict) else None
        code = (err or {}).get("code", "") if isinstance(err, dict) else ""
        if status == 404 and code != RESOURCE_NOT_ACCESSIBLE:
            return _err(
                RESOURCE_API_UNSUPPORTED,
                "the platform does not support the resources API — upgrade the server",
            )
        if status >= 400:
            if code:
                return {"error": {"code": code, "message": (err or {}).get("message", "platform error"),
                                  "request_id": (err or {}).get("request_id", "")}}
            return _err(UPSTREAM_UNAVAILABLE, f"platform error {status}")
        return body

    async def list_resources(
        self,
        kind: str,  # "knowledge-bases" | "skills"
        *,
        q: str = "",
        tenant_id: str = "",
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        params: list[tuple[str, str]] = [
            ("offset", str(offset)),
            ("limit", str(limit)),
        ]
        if q:
            params.append(("q", q))
        if tenant_id:
            params.append(("tenant_id", tenant_id))
        from urllib.parse import urlencode

        status, body = await self._get(
            f"/integration/v1/resources/{kind}?{urlencode(params)}"
        )
        return self._map(status, body)

    async def get_skill_catalog(self, tenant_id: str, catalog_id: str) -> dict[str, Any]:
        from urllib.parse import urlencode

        status, body = await self._get(
            f"/integration/v1/resources/skills/{httpx.URL(path=catalog_id).path}?"
            + urlencode({"tenant_id": tenant_id})
        )
        return self._map(status, body)

    async def get_knowledge_base_item(self, tenant_id: str, kb_id: str) -> Optional[dict[str, Any]]:
        """KB 单项没有专用接口：以 tenant 过滤的列表行作为授权复核依据。"""
        out = await self.list_resources(
            "knowledge-bases", tenant_id=tenant_id, offset=0, limit=100
        )
        if "error" in out:
            return None
        for item in out.get("items") or []:
            if isinstance(item, dict) and item.get("knowledge_base_id") == kb_id:
                return item
        return None

    async def download_archive(
        self, tenant_id: str, catalog_id: str, expected_sha256: str
    ) -> tuple[Optional[bytes], Optional[dict[str, Any]]]:
        """Download the raw archive bytes with the independent download budget
        (120s total, 15s connect). Three-way hash check: expected vs the
        response X-SHA256 header vs the actual bytes."""
        base = self._base
        if not base:
            return None, _err(ENTERPRISE_NOT_CONFIGURED, "enterprise platform is not configured")
        from urllib.parse import urlencode

        path = (
            f"/integration/v1/resources/skills/{httpx.URL(path=catalog_id).path}/archive?"
            + urlencode({"tenant_id": tenant_id, "expected_sha256": expected_sha256})
        )
        headers = {"Authorization": f"Bearer {self._context.access_token}"}

        async def _do() -> httpx.Response:
            if self._client is not None:
                return await self._client.get(
                    f"{base}{path}",
                    headers=headers,
                    timeout=httpx.Timeout(ARCHIVE_CONNECT_TIMEOUT, connect=ARCHIVE_CONNECT_TIMEOUT),
                )
            async with httpx.AsyncClient(
                base_url=base,
                timeout=httpx.Timeout(ARCHIVE_CONNECT_TIMEOUT, connect=ARCHIVE_CONNECT_TIMEOUT),
            ) as client:
                return await client.get(path, headers=headers)

        try:
            resp = await asyncio.wait_for(_do(), timeout=ARCHIVE_TOTAL_BUDGET)
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            return None, _err(UPSTREAM_UNAVAILABLE, f"archive download failed: {exc}")
        if resp.status_code != 200:
            try:
                body = resp.json()
            except ValueError:
                body = {}
            return None, self._map(resp.status_code, body)
        content: bytes = resp.content
        got = hashlib.sha256(content).hexdigest()
        header_sha = (resp.headers.get("X-SHA256") or "").strip().lower()
        if got != expected_sha256 or (header_sha and header_sha != expected_sha256):
            return None, _err(
                SKILL_HASH_MISMATCH,
                f"archive bytes do not match the expected digest ({got[:16]}…)",
            )
        return content, None


# -- resource authorization cache (TASK-020, BND-008) ----------------------------------


@dataclass
class _Grant:
    allowed: bool
    checked_at: float
    detail: str = ""


class ResourceAuthz:
    """Catalog/KB-level re-check cache. Session liveness NEVER substitutes for
    resource authorization (BND-008): every cache entry is bound to
    (context key, tenant, resource, version) and expires in ≤30s; a failed
    refresh denies (fail closed); a context switch clears the shard."""

    def __init__(self, ttl: float = GRANT_TTL_SECONDS) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._grants: dict[tuple[str, str, str, str], _Grant] = {}

    def _key(self, context: EnterpriseContext, tenant: str, resource: str, version: str) -> tuple[str, str, str, str]:
        return (context.key, tenant, resource, version or "*")

    def check(
        self,
        context: EnterpriseContext,
        tenant: str,
        resource: str,
        version: str = "",
        *,
        now: Optional[float] = None,
    ) -> bool:
        at = time.time() if now is None else now
        with self._lock:
            # B2：先按精确版本查， missed 再回退通配条目——授权记录以复核时
            # 看到的具体版本落键，而门（如 registry）以 ref 的版本查询，两者
            # 必须能在同一 TTL 窗口内命中，否则生产 load 永远拒绝。
            for key in (
                self._key(context, tenant, resource, version),
                self._key(context, tenant, resource, ""),
            ):
                grant = self._grants.get(key)
                if grant is None:
                    continue
                if at - grant.checked_at > self._ttl:
                    return False
                return grant.allowed
        return False

    def record(
        self,
        context: EnterpriseContext,
        tenant: str,
        resource: str,
        allowed: bool,
        version: str = "",
        detail: str = "",
    ) -> None:
        with self._lock:
            self._grants[self._key(context, tenant, resource, version)] = _Grant(
                allowed=allowed, checked_at=time.time(), detail=detail
            )

    def drop_context(self, context_key: str) -> None:
        """Identity/space switch: immediate cleanup (BND-002.7/BND-008)."""
        with self._lock:
            dead = [k for k in self._grants if k[0] == context_key]
            for key in dead:
                self._grants.pop(key, None)

    def drop_all(self) -> None:
        with self._lock:
            self._grants.clear()


AUTHZ = ResourceAuthz()


async def authorize_catalog(
    client: PlatformResourceClient,
    context: EnterpriseContext,
    tenant_id: str,
    catalog_id: str,
    bundle_sha256: str = "",
) -> tuple[bool, Optional[dict[str, Any]]]:
    """Catalog-level re-check with the ≤30s cache (fresh entries short-circuit;
    stale/missing entries re-query the single-item endpoint). Returns
    (allowed, item|error)."""
    if AUTHZ.check(context, tenant_id, catalog_id, bundle_sha256):
        return True, None
    out = await client.get_skill_catalog(tenant_id, catalog_id)
    if "error" in out:
        err_code = (out.get("error") or {}).get("code", "")
        AUTHZ.record(context, tenant_id, catalog_id, False, bundle_sha256, err_code)
        if err_code == RESOURCE_NOT_ACCESSIBLE:
            from .registry import REGISTRY
            REGISTRY.schedule_uninstall_or_now(context, tenant_id, catalog_id)
        return False, out
    item = out.get("item") if isinstance(out.get("item"), dict) else {}
    current_sha = str(item.get("bundle_sha256") or "").lower()
    allowed = bool(item) and item.get("availability") == "available"
    # 成功授权同时落精确版本键与通配键（B2：让按 ref 版本查询的门可命中）。
    AUTHZ.record(context, tenant_id, catalog_id, allowed, current_sha)
    AUTHZ.record(context, tenant_id, catalog_id, allowed, "")
    if bundle_sha256 and current_sha and current_sha != bundle_sha256:
        # Version drift is a denial for a PINNED ref; the caller maps it.
        return False, _err(SKILL_VERSION_CHANGED, "the catalog version changed since selection")
    return allowed, None


async def authorize_knowledge_base(
    client: PlatformResourceClient,
    context: EnterpriseContext,
    tenant_id: str,
    kb_id: str,
) -> tuple[bool, Optional[dict[str, Any]]]:
    if AUTHZ.check(context, tenant_id, kb_id, ""):
        return True, None
    item = await client.get_knowledge_base_item(tenant_id, kb_id)
    allowed = item is not None
    AUTHZ.record(context, tenant_id, kb_id, allowed)
    if not allowed:
        return False, _err(RESOURCE_NOT_ACCESSIBLE, "knowledge base is not accessible")
    return True, item


# -- install operations (TASK-015, INT-103) --------------------------------------------


@dataclass
class Operation:
    operation_id: str
    context_key: str
    identity_hash: str
    tenant_id: str
    catalog_id: str
    expected_sha256: str
    status: str = "downloading"  # downloading|verifying|activating|succeeded|failed
    error: Optional[dict[str, Any]] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    name: str = ""
    version: str = ""

    def view(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "operation_id": self.operation_id,
            "status": self.status,
            "context_id": _context_id_from_key(self.context_key),
            "tenant_id": self.tenant_id,
            "catalog_id": self.catalog_id,
            "expected_sha256": self.expected_sha256,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.status == "succeeded":
            out["installed_sha256"] = self.expected_sha256
        if self.error is not None:
            out["error"] = dict(self.error)
        return out


def _context_id_from_key(context_key: str) -> str:
    return hashlib.sha256(context_key.encode("utf-8")).hexdigest()[:32]


class OperationService:
    """Per-identity serialized install/update operations with bounded download
    concurrency, a 5-minute budget, and no cross-identity landing."""

    def __init__(
        self,
        *,
        audit: Optional[AuditFn] = None,
        runner: Optional[Callable[[Any, str], None]] = None,
    ) -> None:
        self._audit = audit
        # The operation coroutine must OUTLIVE the request that accepted it
        # (TestClient's per-request portal teardown kills create_task'd work;
        # the same reason the manager runs enterprise side-effects on a
        # dedicated daemon thread). Injected by the manager.
        self._runner = runner
        self._lock = threading.Lock()
        self._ops: dict[str, Operation] = {}
        self._inflight: dict[tuple[str, str, str], str] = {}  # ident+tenant+cat → op id
        self._semaphore = asyncio.Semaphore(MAX_DOWNLOAD_CONCURRENCY)

    def _base_row(self, op: Operation) -> dict[str, Any]:
        return {
            "session_id": "",
            "tool": "enterprise_skill_install",
            "arguments": {
                "tenant": op.tenant_id,
                "catalog_id": op.catalog_id,
                "expected_sha256": op.expected_sha256,
            },
        }

    def get(self, operation_id: str, context: EnterpriseContext) -> Optional[Operation]:
        op = self._ops.get(operation_id)
        if op is None or op.context_key != context.key:
            return None  # 其他身份不可枚举 (DATA-104/INT-103)
        return op

    def get_for(
        self, context: EnterpriseContext, tenant_id: str, catalog_id: str
    ) -> Optional[Operation]:
        key = (installer.identity_hash(context), tenant_id, catalog_id)
        op_id = self._inflight.get(key)
        return self._ops.get(op_id) if op_id else None

    async def request_install(
        self,
        context: EnterpriseContext,
        client: PlatformResourceClient,
        *,
        tenant_id: str,
        catalog_id: str,
        expected_sha256: str,
        name: str = "",
        version: str = "",
        current_context_getter: Callable[[], Optional[EnterpriseContext]],
    ) -> tuple[int, dict[str, Any]]:
        """Validate + enqueue. Returns (http_status, body): 202 accepted,
        200 already-installed-idempotent, or an error status."""
        try:
            tenant_id = validate_resource_id(tenant_id, "tenant_id")
            catalog_id = validate_resource_id(catalog_id, "catalog_id")
            expected_sha256 = normalize_sha256(expected_sha256)
        except ValueError as exc:
            return 400, _err(INVALID_REQUEST, str(exc))

        # Fresh single-item authorization pinned to the EXPECTED sha (also
        # refreshes the authz cache): if the catalog moved on, this is a
        # version conflict, never a silent same-SHA idempotent hit.
        allowed, problem = await authorize_catalog(
            client, context, tenant_id, catalog_id, expected_sha256
        )
        if not allowed:
            err = (problem or {}).get("error") or _err(RESOURCE_NOT_ACCESSIBLE, "catalog not accessible")
            code = err.get("code", RESOURCE_NOT_ACCESSIBLE)
            # Minor 修复：按 INT-105 语义映射，不把 401/502 压成 404。
            status = {
                "INVALID_REQUEST": 400,
                "SESSION_REVOKED": 401,
                "SPACE_FORBIDDEN": 403,
                RESOURCE_NOT_ACCESSIBLE: 404,
                "SKILL_VERSION_CHANGED": 409,
                "ARCHIVE_NOT_READY": 409,
                "UPSTREAM_UNAVAILABLE": 502,
            }.get(code, 404)
            return status, {"error": err}

        ident = installer.identity_hash(context)
        okey = (ident, tenant_id, catalog_id)
        with self._lock:
            existing_id = self._inflight.get(okey)
            existing = self._ops.get(existing_id) if existing_id else None
            if existing is not None and existing.status not in ("succeeded", "failed"):
                if existing.expected_sha256 == expected_sha256:
                    return 202, {"operation_id": existing.operation_id,
                                 "status": existing.status, "context_id": _context_id_from_key(context.key)}
                return 409, _err(
                    OPERATION_IN_PROGRESS, "another write operation is in progress for this skill"
                )

        # Idempotent no-op: same SHA already active and valid.
        record = installer.get_install(context, tenant_id, catalog_id)
        if (
            record is not None
            and record.active_sha256 == expected_sha256
            and installer.find_skill_root(
                installer.catalog_dir(context, tenant_id, catalog_id)
                / installer.VERSIONS_DIR
                / expected_sha256
            )
            is not None
        ):
            return 200, {
                "operation_id": "",
                "status": "installed",
                "installed_sha256": record.active_sha256,
                "enabled": record.enabled,
                "context_id": _context_id_from_key(context.key),
            }

        op = Operation(
            operation_id=str(uuid.uuid4()),
            context_key=context.key,
            identity_hash=ident,
            tenant_id=tenant_id,
            catalog_id=catalog_id,
            expected_sha256=expected_sha256,
            name=name,
            version=version,
        )
        with self._lock:
            self._ops[op.operation_id] = op
            self._inflight[okey] = op.operation_id
        if self._runner is not None:
            self._runner(
                self._run(op, client, current_context_getter), "opc-install-operation"
            )
        else:
            asyncio.get_running_loop().create_task(
                self._run(op, client, current_context_getter)
            )
        return 202, {"operation_id": op.operation_id, "status": op.status,
                     "context_id": _context_id_from_key(context.key)}

    async def _run(
        self,
        op: Operation,
        client: PlatformResourceClient,
        current_context_getter: Callable[[], Optional[EnterpriseContext]],
    ) -> None:
        try:
            async with self._semaphore:
                op.status = "downloading"
                op.updated_at = time.time()
                bundle, error = await client.download_archive(
                    op.tenant_id, op.catalog_id, op.expected_sha256
                )
            if bundle is None:
                raise _OpFailed(error or _err(UPSTREAM_UNAVAILABLE, "download failed"))
            op.status = "verifying"
            op.updated_at = time.time()
            # Late-response guard: the download belonged to the identity that
            # STARTED it — a switch mid-flight must not land it (VAL-214).
            current = current_context_getter()
            if current is None or current.key != op.context_key:
                raise _OpFailed(_err(CONTEXT_CHANGED, "identity switched during the operation"))
            op.status = "activating"
            op.updated_at = time.time()
            result = await asyncio.to_thread(
                installer.install,
                current,
                op.tenant_id,
                op.catalog_id,
                bundle,
                expected_sha256=op.expected_sha256,
                name=op.name,
                version=op.version,
                audit=self._audit,
            )
            if not result.get("ok"):
                err = result.get("error") or {}
                raise _OpFailed(_err(err.get("code", INSTALL_FAILED), err.get("message", "install failed")))
            op.status = "succeeded"
            op.updated_at = time.time()
            if self._audit:
                self._audit({**self._base_row(op), "stage": "operation_succeeded",
                             "status": "granted", "reason": op.expected_sha256[:16]})
        except _OpFailed as exc:
            op.status = "failed"
            op.error = exc.payload["error"]
            op.updated_at = time.time()
            if self._audit:
                self._audit({**self._base_row(op), "stage": "operation_failed",
                             "status": "refused", "reason": op.error.get("code", "")})
        except Exception as exc:  # noqa: BLE001 - the operation must always land
            logger.exception("install operation %s crashed", op.operation_id)
            op.status = "failed"
            op.error = _err(INSTALL_FAILED, str(exc)[:300])["error"]
            op.updated_at = time.time()
        finally:
            with self._lock:
                if self._inflight.get((op.identity_hash, op.tenant_id, op.catalog_id)) == op.operation_id:
                    self._inflight.pop((op.identity_hash, op.tenant_id, op.catalog_id), None)
            # Terminal-state retention is bounded in prune(); the record stays
            # queryable for GUI recovery after a page refresh.
            self.prune()

    def prune(self, keep_terminal: int = 50) -> None:
        """Bound memory: keep the most recent terminal ops per identity shard."""
        with self._lock:
            terminal = [
                (op_id, op)
                for op_id, op in self._ops.items()
                if op.status in ("succeeded", "failed")
            ]
            if len(terminal) <= keep_terminal:
                return
            terminal.sort(key=lambda pair: pair[1].updated_at, reverse=True)
            for op_id, _op in terminal[keep_terminal:]:
                self._ops.pop(op_id, None)


class _OpFailed(Exception):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__((payload.get("error") or {}).get("message", "operation failed"))
        self.payload = payload


# -- sidecar-facing facade -------------------------------------------------------------


def context_id_of(context: EnterpriseContext) -> str:
    """The opaque context marker the GUI sends back with every resource
    selection (DATA-101); derived from the context key so a switch
    invalidates it automatically."""
    return _context_id_from_key(context.key)


def check_context_id(context: EnterpriseContext, provided: Any) -> Optional[dict[str, Any]]:
    """DATA-101/BND-004.5: the provided context_id must EXACTLY match the live
    one — a stale selection is rejected, never re-bound."""
    if not isinstance(provided, str) or not provided:
        return _err(INVALID_REQUEST, "context_id is required")
    if not pysecrets.compare_digest(provided, context_id_of(context)):
        return _err(CONTEXT_CHANGED, "the selection belongs to a previous identity")
    return None


def merge_local_skill_state(
    context: EnterpriseContext, items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Attach the current identity's local install state to catalog rows
    (INT-101: local_status / installed_sha256 / enabled)."""
    records = {
        (r.tenant_id, r.catalog_id): r for r in installer.list_installs(context)
    }
    out = []
    for item in items:
        row = dict(item)
        record = records.get((str(item.get("tenant_id") or ""), str(item.get("catalog_id") or "")))
        if record is None:
            row["local_status"] = "not_installed"
        elif not record.enabled:
            row["local_status"] = "installed_disabled"
        else:
            row["local_status"] = "installed_enabled"
        row["installed_sha256"] = record.active_sha256 if record else ""
        row["enabled"] = record.enabled if record else False
        out.append(row)
    return out


def reconcile_local_installs(
    context: EnterpriseContext,
    tenant_id: str,
    remote_catalog_ids: set[str],
) -> list[str]:
    """Prune locally installed enterprise skills that no longer exist on the platform.
    Called when a complete, unfiltered catalog listing has been retrieved.
    Returns list of uninstalled or pending-uninstalled catalog_ids."""
    from .registry import REGISTRY

    local_records = installer.list_installs(context)
    pruned: list[str] = []
    for record in local_records:
        if tenant_id and record.tenant_id != tenant_id:
            continue
        if record.catalog_id not in remote_catalog_ids:
            logger.info(
                "reconcile_local_installs: catalog %s (tenant %s) not in remote catalog; removing",
                record.catalog_id,
                record.tenant_id,
            )
            REGISTRY.schedule_uninstall_or_now(context, record.tenant_id, record.catalog_id)
            pruned.append(record.catalog_id)
    return pruned
