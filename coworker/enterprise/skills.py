"""Enterprise distribution-grant refresh (BND-008, legacy release chain removed).

WORK-RESOURCES-001 / TASK-023: the old publish_id installer, the release
bundle fetch, and the automatic catalog sync are DELETED — installing a
skill is an EXPLICIT user operation through the new resources family (see
``coworker.enterprise.installer`` / ``coworker.enterprise.resources``), and
no code path may call the platform's retired release routes (the test fake
trips a violation on any such call).

What stays here: the SESSION-level grant refresh (re-query the platform
session before enterprise features may start; failure fails closed). It is
the coarse gate; per-resource authorization (≤30s, catalog/KB level) lives
in ``coworker.enterprise.resources.ResourceAuthz``.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from .auth import EnterpriseContext

logger = logging.getLogger("coworker.enterprise.skills")

DEFAULT_PLATFORM_TIMEOUT = 15.0

# Session-grant freshness (BND-008): the resource-level cache is shorter (30s)
# and never bypassed by this one.
GRANT_TTL_SECONDS = 60.0


# -- session grant ------------------------------------------------------------------


@dataclass
class _GrantEntry:
    ok: bool
    checked_at: float
    detail: str = ""


_grants: dict[str, _GrantEntry] = {}
_grants_lock = threading.Lock()
_refresh_in_flight: set[str] = set()


async def refresh_distribution_grant(
    context: EnterpriseContext,
    *,
    platform_base: str = "",
    http_client: Optional[httpx.AsyncClient] = None,
) -> bool:
    """Re-query the platform session (INT-101f) before enterprise features may
    start. Failure — unreachable, unconfigured, revoked — fails closed."""
    base = (platform_base or "").rstrip("/")
    if http_client is None and not base:
        entry = _GrantEntry(ok=False, checked_at=time.time(), detail="no platform_base")
        with _grants_lock:
            _grants[context.key] = entry
        return False
    try:
        if http_client is not None:
            resp = await http_client.get(
                "/integration/v1/session",
                headers={"Authorization": f"Bearer {context.access_token}"},
            )
        else:
            async with httpx.AsyncClient(
                base_url=base, timeout=DEFAULT_PLATFORM_TIMEOUT
            ) as client:
                resp = await client.get(
                    "/integration/v1/session",
                    headers={"Authorization": f"Bearer {context.access_token}"},
                )
        ok = resp.status_code == 200
        detail = "" if ok else f"platform session check returned {resp.status_code}"
    except httpx.HTTPError as exc:
        ok, detail = False, f"platform unreachable: {exc}"
    with _grants_lock:
        _grants[context.key] = _GrantEntry(ok=ok, checked_at=time.time(), detail=detail)
    return ok


def grant_allows(context: EnterpriseContext) -> bool:
    """The cached verdict: may enterprise features START right now? Absent or
    stale-past-TTL means no (fail closed); the manager schedules the refresh."""
    with _grants_lock:
        entry = _grants.get(context.key)
    if entry is None:
        return False
    if time.time() - entry.checked_at > GRANT_TTL_SECONDS:
        return False
    return entry.ok


def grant_is_stale(context: EnterpriseContext) -> bool:
    with _grants_lock:
        entry = _grants.get(context.key)
    return entry is None or time.time() - entry.checked_at > GRANT_TTL_SECONDS


def grant_detail(context: EnterpriseContext) -> str:
    """Why the grant currently fails ("" when it allows) — for audit rows."""
    with _grants_lock:
        entry = _grants.get(context.key)
    if entry is None:
        return "distribution grant not yet verified"
    if time.time() - entry.checked_at > GRANT_TTL_SECONDS:
        return "distribution grant stale — re-verification pending"
    return entry.detail or "distribution grant refused"


def refresh_in_flight(context_key: str) -> bool:
    with _grants_lock:
        return context_key in _refresh_in_flight


def set_refresh_in_flight(context_key: str, value: bool) -> None:
    with _grants_lock:
        if value:
            _refresh_in_flight.add(context_key)
        else:
            _refresh_in_flight.discard(context_key)


def drop_grant(context_key: str) -> None:
    with _grants_lock:
        _grants.pop(context_key, None)
