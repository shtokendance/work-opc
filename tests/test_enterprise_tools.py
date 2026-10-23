"""TASK-W04 — enterprise tools + the MCP approval bridge (INT-120/150, BND-151/163).

Fake-platform round trips for knowledge_search/knowledge_read (including 4xx
passthrough), mcp_invoke success/policy-denied, the full 202 → Inbox approval
→ platform decision → result chain (allow / deny / unknown vocabulary denied /
orphaned / timeout), offline structured failures, per-context connection
eviction, and the sync tool surface the engine registry sees.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from coworker.enterprise.auth import EnterpriseContext
from coworker.enterprise.tools import (
    ENTERPRISE_NOT_CONFIGURED,
    INVOCATION_UNKNOWN_OUTCOME,
    MCP_INVOKE_TOOL,
    EnterpriseBridge,
)
from coworker.inbox import STATE_PENDING, InboxStore
from coworker.testing.fake_opc import FakeOpc


def _ctx(tenant: str = "tenant-a", generation: int = 1) -> EnterpriseContext:
    return EnterpriseContext(
        issuer="https://opc.example.com",
        subject="sub-1",
        tenant=tenant,
        generation=generation,
        authz_version=1,
        spaces=[],
        access_token="opc-access-test",
    )


class _FakeClient:
    """Records aclose() so connection eviction is observable."""

    def __init__(self, key: str, log: list[str]) -> None:
        self.key = key
        self._log = log
        self._real = httpx.AsyncClient(timeout=5.0)

    async def request(self, method: str, url: str, **kwargs):
        return await self._real.request(method, url, **kwargs)

    async def aclose(self) -> None:
        self._log.append(self.key)
        await self._real.aclose()


def _bridge(
    context: EnterpriseContext | None,
    *,
    base_url: str,
    inbox: InboxStore,
    approval_timeout: float = 5.0,
    closed_log: list[str] | None = None,
    audit=None,
) -> EnterpriseBridge:
    log = closed_log if closed_log is not None else []
    return EnterpriseBridge(
        context_getter=lambda: context,
        platform_base_getter=lambda: base_url,
        prompt_router=lambda sid: {"inbox": "default", "visibility": "inbox"},
        inbox=inbox,
        audit=audit,
        approval_timeout=approval_timeout,
        poll_interval=0.01,
        client_factory=lambda key: _FakeClient(key, log),
    )


@pytest.fixture
def fake_opc():
    fake = FakeOpc().start()
    try:
        yield fake
    finally:
        fake.stop()


@pytest.fixture
def inbox():
    return InboxStore()


# -- INT-120 knowledge tools -------------------------------------------------------


async def test_knowledge_search_round_trip(fake_opc, inbox):
    bridge = _bridge(_ctx(), base_url=fake_opc.base_url, inbox=inbox)
    out = await bridge.knowledge_search("离职", space="space-技术交付中心")
    assert out["results"][0]["knowledge_id"] == "kn-1"
    assert fake_opc.knowledge_search_calls[0]["query"] == "离职"
    # space 覆盖上下文租户，统一以 tenant_id 发出（INT-120a 必需字段）。
    assert fake_opc.knowledge_search_calls[0]["tenant_id"] == "space-技术交付中心"
    assert "opc-access-test" in fake_opc.seen_tokens  # context token injected


async def test_knowledge_search_defaults_context_tenant(fake_opc, inbox):
    bridge = _bridge(_ctx(), base_url=fake_opc.base_url, inbox=inbox)
    await bridge.knowledge_search("离职")
    # 未显式给 space 时从企业上下文租户缺省填充（真平台拒绝无 tenant 请求）。
    assert fake_opc.knowledge_search_calls[0]["tenant_id"] == "tenant-a"


async def test_knowledge_search_4xx_passes_through(fake_opc, inbox):
    fake_opc.fail_next_knowledge_search("SPACE_FORBIDDEN", 403)
    bridge = _bridge(_ctx(), base_url=fake_opc.base_url, inbox=inbox)
    out = await bridge.knowledge_search("secret space")
    assert not out["ok"]
    assert out["error"]["code"] == "SPACE_FORBIDDEN"


async def test_knowledge_read_round_trip_and_403(fake_opc, inbox):
    bridge = _bridge(_ctx(), base_url=fake_opc.base_url, inbox=inbox)
    doc = await bridge.knowledge_read("kn-1")
    assert doc["data"]["title"] == "离职流程"
    denied = await bridge.knowledge_read("kn-unknown")
    # 新契约（INT-105）：不存在/不可见统一 404 RESOURCE_NOT_ACCESSIBLE。
    assert not denied["ok"] and denied["error"]["code"] == "RESOURCE_NOT_ACCESSIBLE"
    bad = await bridge.knowledge_read("../etc/passwd")
    assert bad["error"]["code"] == "INVALID_KNOWLEDGE_ID"


async def test_offline_and_no_context_fail_structured(fake_opc, inbox):
    offline = _bridge(_ctx(), base_url="", inbox=inbox)
    out = await offline.knowledge_search("q")
    assert not out["ok"]
    assert out["error"]["code"] == ENTERPRISE_NOT_CONFIGURED

    no_ctx = _bridge(None, base_url=fake_opc.base_url, inbox=inbox)
    out = await no_ctx.mcp_invoke(
        session_id="s1", service_id="svc", tool_name="t", arguments={}
    )
    assert not out["ok"]
    assert out["error"]["code"] == ENTERPRISE_NOT_CONFIGURED

    dead = FakeOpc().start()
    dead.stop()  # port closed → connection refused
    unreachable = _bridge(_ctx(), base_url=dead.base_url, inbox=inbox)
    out = await unreachable.knowledge_search("q")
    assert not out["ok"]
    assert out["error"]["code"] == "UPSTREAM_UNAVAILABLE"


# -- INT-150 mcp_invoke -------------------------------------------------------------


async def test_mcp_invoke_success(fake_opc, inbox):
    bridge = _bridge(_ctx(), base_url=fake_opc.base_url, inbox=inbox)
    out = await bridge.mcp_invoke(
        session_id="s1", service_id="svc-hr", tool_name="list", arguments={"n": 1}
    )
    assert out["ok"] and out["status"] == "succeeded"
    call = fake_opc.mcp_invoke_calls[0]
    assert call["service_id"] == "svc-hr"
    assert call["idempotency_key"]


async def test_mcp_invoke_policy_denied_passthrough(fake_opc, inbox):
    fake_opc.mcp_invoke_behaviors = [{"kind": "policy_denied"}]
    bridge = _bridge(_ctx(), base_url=fake_opc.base_url, inbox=inbox)
    out = await bridge.mcp_invoke(
        session_id="s1", service_id="svc", tool_name="delete", arguments={}
    )
    assert not out["ok"]
    assert out["error"]["code"] == "MCP_POLICY_DENIED"


async def _wait_for_pending(inbox: InboxStore) -> list:
    for _ in range(500):
        pending = inbox.list(state=STATE_PENDING)
        if pending:
            return pending
        await asyncio.sleep(0.01)
    raise AssertionError("no pending inbox item appeared")


async def test_mcp_invoke_202_approval_allow_full_chain(fake_opc, inbox):
    fake_opc.mcp_invoke_behaviors = [{"kind": "approval_required"}]
    bridge = _bridge(_ctx(), base_url=fake_opc.base_url, inbox=inbox)
    task = asyncio.create_task(
        bridge.mcp_invoke(
            session_id="s1",
            service_id="svc-hr",
            tool_name="fire",
            arguments={"who": "bob", "note": "x" * 200},
        )
    )
    (item,) = await _wait_for_pending(inbox)
    # The parked card is enterprise-marked and binds the invocation + summary.
    assert item.data["enterprise"] is True
    assert item.data["tool"] == MCP_INVOKE_TOOL
    assert item.data["invocation_id"] in fake_opc.invocations
    assert item.data["context_key"]
    assert "fire" in item.title and "svc-hr" in item.title
    assert "who: bob" in item.body  # final-argument summary on the card

    inbox.resolve(item.id, "allow")
    out = await asyncio.wait_for(task, timeout=5)
    assert out["ok"] and out["status"] == "succeeded"
    # The decision reached the platform bound to the invocation.
    (decision,) = fake_opc.mcp_decisions
    assert decision["invocation_id"] == item.data["invocation_id"]
    assert decision["decision"] == "approve"
    assert decision["service_id"] == "svc-hr"
    assert "who: bob" in decision["arguments_summary"]


async def test_mcp_invoke_202_approval_deny(fake_opc, inbox):
    fake_opc.mcp_invoke_behaviors = [{"kind": "approval_required"}]
    bridge = _bridge(_ctx(), base_url=fake_opc.base_url, inbox=inbox)
    task = asyncio.create_task(
        bridge.mcp_invoke(session_id="s1", service_id="svc", tool_name="fire", arguments={})
    )
    (item,) = await _wait_for_pending(inbox)
    inbox.resolve(item.id, "deny")
    out = await asyncio.wait_for(task, timeout=5)
    assert not out["ok"] and out["status"] == "denied"
    assert fake_opc.mcp_decisions[0]["decision"] == "deny"
    invocation = fake_opc.invocations[item.data["invocation_id"]]
    assert invocation["status"] == "failed"


async def test_mcp_invoke_grant_vocabulary_not_honored(fake_opc, inbox):
    """BND-151 local re-check: grant-style resolutions are not in the offered
    vocabulary and fail closed as a denial (no silent widening)."""
    fake_opc.mcp_invoke_behaviors = [{"kind": "approval_required"}]
    bridge = _bridge(_ctx(), base_url=fake_opc.base_url, inbox=inbox)
    task = asyncio.create_task(
        bridge.mcp_invoke(session_id="s1", service_id="svc", tool_name="fire", arguments={})
    )
    (item,) = await _wait_for_pending(inbox)
    inbox.resolve(item.id, "always")
    out = await asyncio.wait_for(task, timeout=5)
    assert not out["ok"]
    assert fake_opc.mcp_decisions[0]["decision"] == "deny"


async def test_mcp_invoke_orphaned_returns_unknown_without_decision(fake_opc, inbox):
    """Context switch semantics: an orphaned approval never posts a decision
    under a dead context (BND-152)."""
    fake_opc.mcp_invoke_behaviors = [{"kind": "approval_required"}]
    bridge = _bridge(_ctx(), base_url=fake_opc.base_url, inbox=inbox)
    task = asyncio.create_task(
        bridge.mcp_invoke(session_id="s1", service_id="svc", tool_name="fire", arguments={})
    )
    (item,) = await _wait_for_pending(inbox)
    inbox.resolve(item.id, "orphaned", by="system:enterprise-context-switch")
    out = await asyncio.wait_for(task, timeout=5)
    assert not out["ok"]
    assert out["status"] == "unknown_outcome"
    assert out["error"]["code"] == INVOCATION_UNKNOWN_OUTCOME
    assert fake_opc.mcp_decisions == []  # nothing posted


async def test_mcp_invoke_approval_timeout_returns_unknown(fake_opc, inbox):
    fake_opc.mcp_invoke_behaviors = [{"kind": "approval_required"}]
    bridge = _bridge(_ctx(), base_url=fake_opc.base_url, inbox=inbox, approval_timeout=0.2)
    out = await bridge.mcp_invoke(
        session_id="s1", service_id="svc", tool_name="fire", arguments={}
    )
    assert not out["ok"]
    assert out["status"] == "unknown_outcome"
    assert out["error"]["code"] == INVOCATION_UNKNOWN_OUTCOME
    (item,) = inbox.list()
    assert item.state == "resolved" and item.resolution == "expired"
    assert fake_opc.mcp_decisions == []


async def test_mcp_invoke_202_without_invocation_id_fails(fake_opc, inbox):
    """A 202 approval response without an invocation id is refused — there is
    nothing to bind a decision to (BND-151)."""
    bridge = EnterpriseBridge(
        context_getter=lambda: _ctx(),
        platform_base_getter=lambda: fake_opc.base_url,
        prompt_router=lambda sid: {"inbox": "default", "visibility": "inbox"},
        inbox=inbox,
        approval_timeout=0.2,
        poll_interval=0.01,
    )
    original = bridge._request

    async def fake_request(context, method, path, *, json_body=None):
        if path == "/integration/v1/mcp/invoke":
            return 202, {
                "error": {"code": "MCP_APPROVAL_REQUIRED", "message": "m"},
                "approval": {},
            }
        return await original(context, method, path, json_body=json_body)

    bridge._request = fake_request  # type: ignore[assignment]
    out = await bridge.mcp_invoke(
        session_id="s1", service_id="svc", tool_name="fire", arguments={}
    )
    assert not out["ok"]
    assert "invocation_id" in out["error"]["message"]
    assert inbox.list() == []  # nothing was parked


# -- connections sharded by context key (BND-120) -------------------------------------


async def test_connections_are_per_context_and_closed_on_switch(fake_opc, inbox):
    closed: list[str] = []
    ctx_a = _ctx(tenant="tenant-a", generation=1)
    bridge = _bridge(_ctx(), base_url=fake_opc.base_url, inbox=inbox, closed_log=closed)
    bridge._context_getter = lambda: ctx_a
    await bridge.knowledge_search("q")
    assert bridge.context_keys() == [ctx_a.key]

    await bridge.aclose_context(ctx_a.key)
    assert bridge.context_keys() == []
    assert closed == [ctx_a.key]

    # A new context (generation+1) gets a FRESH connection, never the old one.
    ctx_b = _ctx(tenant="tenant-a", generation=2)
    bridge._context_getter = lambda: ctx_b
    await bridge.knowledge_search("q2")
    assert bridge.context_keys() == [ctx_b.key]
    assert ctx_a.key != ctx_b.key
    await bridge.aclose()


# -- the sync tool surface the registry sees -------------------------------------------


def test_build_tools_sync_wrappers_and_metadata(fake_opc, inbox):
    bridge = _bridge(_ctx(), base_url=fake_opc.base_url, inbox=inbox)
    tools = bridge.build_tools("sess-1")
    assert [fn.__name__ for fn in tools] == [
        "knowledge_search", "knowledge_read", "mcp_invoke", "enterprise_load_skill",
    ]
    for fn in tools:
        assert fn.__coworker_schema__["function"]["name"] == fn.__name__
        assert fn.__aisuite_tool_metadata__.category == "enterprise"
        assert fn.__aisuite_tool_metadata__.requires_approval is False
    # Sync wrappers drive the real async implementation (engine runs tools on
    # worker threads; each call gets its own event loop).
    search = next(fn for fn in tools if fn.__name__ == "knowledge_search")
    out = search("离职")
    assert out["results"][0]["knowledge_id"] == "kn-1"
