"""Turn-scoped enterprise knowledge access (WORK-RESOURCES-001, BND-004/005).

One turn = one identity + one knowledge base + one fixed skill version. The
scope is bound at message acceptance, travels explicitly through the turn's
tool calls (never through a process-global variable that concurrent sessions
could overwrite — BND-005), and is released when the turn ends/cancels; the
next scope-less message returns to the default behavior.

Enforcement is at the TOOL layer, not the prompt layer: with a scope active,
``knowledge_search`` fills the fixed tenant/KB when the model omits the
parameters and REFUSES (SCOPE_MISMATCH, no upstream request) when the model
tries different values; ``knowledge_read`` only serves documents from this
turn's hit set, and the platform re-verifies the authoritative document's
actual tenant/KB before any content is returned.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from .auth import EnterpriseContext
from .resources import (
    INVALID_REQUEST,
    RESOURCE_NOT_ACCESSIBLE,
    _err,
    authorize_knowledge_base,
    check_context_id,
    validate_resource_id,
)
from .resources import PlatformResourceClient

SCOPE_MISMATCH = "SCOPE_MISMATCH"


@dataclass
class TurnScope:
    """The one-KB authorization window pinned to a single turn."""

    turn_id: str
    context_key: str
    tenant_id: str
    knowledge_base_id: str
    labels: dict[str, str] = field(default_factory=dict)
    hits: set[str] = field(default_factory=set)
    created_at: float = field(default_factory=time.time)

    def record_hits(self, results: list[dict[str, Any]]) -> None:
        for hit in results:
            if not isinstance(hit, dict):
                continue
            kid = str(hit.get("knowledge_id") or "").strip()
            if kid:
                self.hits.add(kid)

    def allows_read(self, knowledge_id: str) -> bool:
        return knowledge_id in self.hits

    def to_meta(self) -> dict[str, Any]:
        """The persisted message metadata (DATA-102)."""
        return {
            "turn_id": self.turn_id,
            "tenant_id": self.tenant_id,
            "knowledge_base_id": self.knowledge_base_id,
            "labels": dict(self.labels),
        }


async def build_turn_scope(
    data: dict[str, Any],
    context: EnterpriseContext,
    client: PlatformResourceClient,
) -> tuple[Optional[TurnScope], Optional[dict[str, Any]]]:
    """Validate the GUI-supplied knowledge_scope and bind it to a new turn id.

    Server-side only truths: the KB must be visible to the CURRENT identity
    (resource-level re-check), the context_id must match exactly, and the
    display labels are generated HERE — never adopted from the client."""
    if not isinstance(data, dict):
        return None, _err(INVALID_REQUEST, "knowledge_scope must be an object")
    bad = check_context_id(context, data.get("context_id"))
    if bad is not None:
        return None, bad
    try:
        tenant_id = validate_resource_id(data.get("tenant_id"), "tenant_id")
        kb_id = validate_resource_id(data.get("knowledge_base_id"), "knowledge_base_id")
    except ValueError as exc:
        return None, _err(INVALID_REQUEST, str(exc))

    allowed, item = await authorize_knowledge_base(client, context, tenant_id, kb_id)
    if not allowed:
        err = (item or {}).get("error") or _err(RESOURCE_NOT_ACCESSIBLE, "knowledge base is not accessible")
        code = err.get("code", RESOURCE_NOT_ACCESSIBLE)
        status_err = dict(err)
        return None, {"error": status_err, "_http_hint": 403 if code == "SPACE_FORBIDDEN" else 404}

    item = item or {}
    labels = {
        "tenant_name": str(item.get("tenant_name") or tenant_id),
        "knowledge_base_name": str(item.get("name") or kb_id),
    }
    return (
        TurnScope(
            turn_id=str(uuid.uuid4()),
            context_key=context.key,
            tenant_id=tenant_id,
            knowledge_base_id=kb_id,
            labels=labels,
        ),
        None,
    )


def validate_scope_object(data: Any) -> Optional[dict[str, Any]]:
    """Shape/length/mutual-exclusion checks BEFORE any authorization work
    (BND-004.4: 校验失败拒绝该消息，保留草稿)。"""
    if data is None:
        return None
    if not isinstance(data, dict):
        return _err(INVALID_REQUEST, "knowledge_scope must be an object")
    # 规格字段固定（DATA-102）：未知额外字段一律拒绝，防止扩展字段被
    # 当作授权语义误传。
    extra = set(data) - {"tenant_id", "knowledge_base_id", "context_id"}
    if extra:
        return _err(
            INVALID_REQUEST,
            "knowledge_scope has unknown fields: " + ", ".join(sorted(extra)),
        )
    for key in ("tenant_id", "knowledge_base_id", "context_id"):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            return _err(INVALID_REQUEST, f"knowledge_scope.{key} is required")
        if len(value) > 128 + 64:  # context ids are 32-hex; ids ≤128
            return _err(INVALID_REQUEST, f"knowledge_scope.{key} is too long")
    return None
