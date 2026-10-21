"""Enterprise tools + the MCP approval bridge (INT-120/150, BND-151/163).

Three tools are injected into a session's engine (``extra_tools``) while an
enterprise context is active:

* ``knowledge_search(query, space?)`` — INT-120a against the platform's
  knowledge adapter (the platform re-authorizes server-side and reads
  knowledge with the bridge identity; the client never talks to knowledge).
* ``knowledge_read(knowledge_id)`` — INT-120b fragment/content read.
* ``mcp_invoke(service_id, tool_name, arguments)`` — INT-150 precise
  invocation through the platform adapter.

Failure shape is always structured (BND-180): ``{"error": {"code", "message",
"request_id"?}}`` — a dead platform is ``UPSTREAM_UNAVAILABLE`` /
``ENTERPRISE_NOT_CONFIGURED``, never a silent degrade to personal behavior.

The approval bridge (BND-151): when the platform answers a 202
``MCP_APPROVAL_REQUIRED``, the bridge parks the decision request as an
enterprise-marked Inbox approval (service, tool, final-argument summary,
bound ``invocation_id`` + ``context_key``) and blocks the tool call. The GUI's
existing ApprovalCard / InboxItemCard renders it like any other approval.
On ``allow`` the decision is posted to the platform (which re-validates the
decision server-side — binding ``args_digest + policy_version`` — and only
then executes); on ``deny`` the platform records the refusal; on
``orphaned`` (enterprise context switched away mid-wait) or a wait timeout,
NO decision is posted and the tool returns an unknown status — the caller
must not retry a write blind (BND-152).

Connections (the enterprise "connection cache") are held per context key and
closed when that context is orphaned (BND-120): an existing connection is
never re-labeled for a new context. Retrieval calls are stateless — each
``knowledge_search``/``knowledge_read`` is an independent platform request
carrying the current context token, so there is no client-side retrieval
session to shard; the platform owns session state.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Callable, Optional, Protocol

import aisuite as ai
import httpx

from .auth import (
    ENTERPRISE_NOT_CONFIGURED,
    UPSTREAM_UNAVAILABLE,
    EnterpriseContext,
)

logger = logging.getLogger("coworker.enterprise.tools")

DEFAULT_PLATFORM_TIMEOUT = 15.0  # every platform call (TASK-W02 budget)
DEFAULT_APPROVAL_TIMEOUT = 300.0  # how long a tool waits for the human
MCP_APPROVAL_REQUIRED = "MCP_APPROVAL_REQUIRED"
MCP_POLICY_DENIED = "MCP_POLICY_DENIED"
INVOCATION_UNKNOWN_OUTCOME = "INVOCATION_UNKNOWN_OUTCOME"

KNOWLEDGE_SEARCH_TOOL = "knowledge_search"
KNOWLEDGE_READ_TOOL = "knowledge_read"
MCP_INVOKE_TOOL = "mcp_invoke"


class _InboxLike(Protocol):
    """The slice of InboxStore the bridge needs (duck-typed; tests may pass a
    bare InboxStore without a manager)."""

    def add_approval(self, session_id, title, *, body="", inbox="default",
                     visibility="inbox", data=None, tool_call_id=None): ...

    async def wait(self, item_id: str) -> str: ...

    def resolve(self, item_id: str, resolution: str, by: str = "") -> bool: ...


ContextGetter = Callable[[], Optional[EnterpriseContext]]
PromptRouter = Callable[[str], dict[str, Any]]
AuditFn = Callable[[dict[str, Any]], None]
ClientFactory = Callable[[str], httpx.AsyncClient]


def _err(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


def _audit(audit: Optional[AuditFn], row: dict[str, Any]) -> None:
    if audit is None:
        logger.info("enterprise tools audit: %s", row)
        return
    try:
        audit(row)
    except Exception:  # noqa: BLE001 - audit must never break a call
        logger.warning("enterprise tools audit sink failed: %s", row)


class EnterpriseBridge:
    """The enterprise side of the engine: platform calls + the approval bridge.

    ``prompt_router(session_id)`` supplies the Inbox routing + visibility the
    manager uses for every other parked prompt, so an enterprise approval
    lands exactly where a local one would.
    """

    def __init__(
        self,
        *,
        context_getter: ContextGetter,
        platform_base_getter: Callable[[], str],
        prompt_router: PromptRouter,
        inbox: _InboxLike,
        audit: Optional[AuditFn] = None,
        approval_timeout: float = DEFAULT_APPROVAL_TIMEOUT,
        client_factory: Optional[ClientFactory] = None,
        poll_interval: float = 1.0,
    ) -> None:
        self._context_getter = context_getter
        self._platform_base_getter = platform_base_getter
        self._prompt_router = prompt_router
        self._inbox = inbox
        self._audit = audit
        self._approval_timeout = approval_timeout
        self._client_factory = client_factory or (
            lambda key: httpx.AsyncClient(timeout=DEFAULT_PLATFORM_TIMEOUT)
        )
        self._poll_interval = poll_interval
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._client_loops: dict[str, asyncio.AbstractEventLoop] = {}
        self._pending: dict[str, set[str]] = {}  # context_key -> invocation ids

    # -- connections, sharded by context key (BND-120) -------------------------
    def _client_for(self, context: EnterpriseContext) -> httpx.AsyncClient:
        key = context.key
        loop = asyncio.get_running_loop()
        client = self._clients.get(key)
        if client is not None and self._client_loops.get(key) is not loop:
            # Created on another (now foreign) loop: close it there, re-create
            # here so no connection crosses contexts or loops.
            old_loop = self._client_loops.get(key)
            if old_loop is not None and not old_loop.is_closed():
                old_loop.call_soon_threadsafe(
                    lambda c=client: asyncio.ensure_future(c.aclose())
                )
            client = None
        if client is None:
            client = self._client_factory(key)
            self._clients[key] = client
            self._client_loops[key] = loop
        return client

    def _track_pending(self, context: EnterpriseContext, invocation_id: str) -> None:
        self._pending.setdefault(context.key, set()).add(invocation_id)

    def _untrack_pending(self, context: EnterpriseContext, invocation_id: str) -> None:
        ids = self._pending.get(context.key)
        if ids is not None:
            ids.discard(invocation_id)

    def pending_invocations(self, context_key: str) -> set[str]:
        return set(self._pending.get(context_key) or set())

    def context_keys(self) -> list[str]:
        return sorted(self._clients)

    async def aclose_context(self, context_key: str) -> None:
        """Terminate every enterprise connection held for an orphaned context."""
        client = self._clients.pop(context_key, None)
        self._client_loops.pop(context_key, None)
        self._pending.pop(context_key, None)
        if client is not None:
            await client.aclose()

    async def aclose(self) -> None:
        for key in list(self._clients):
            await self.aclose_context(key)

    # -- the platform call itself ------------------------------------------------
    async def _request(
        self,
        context: EnterpriseContext,
        method: str,
        path: str,
        *,
        json_body: Optional[dict[str, Any]] = None,
    ) -> tuple[int, dict[str, Any]]:
        base = (self._platform_base_getter() or "").rstrip("/")
        if not base:
            return 400, _err(ENTERPRISE_NOT_CONFIGURED, "no platform_base configured")
        client = self._client_for(context)
        headers = {"Authorization": f"Bearer {context.access_token}"}
        try:
            resp = await client.request(
                method, f"{base}{path}", json=json_body, headers=headers
            )
        except httpx.HTTPError as exc:
            return 502, _err(UPSTREAM_UNAVAILABLE, f"platform unreachable: {exc}")
        try:
            body = resp.json()
        except ValueError:
            return resp.status_code, {
                "error": {
                    "code": UPSTREAM_UNAVAILABLE,
                    "message": f"platform returned non-JSON ({resp.status_code})",
                }
            }
        return resp.status_code, body if isinstance(body, dict) else {"value": body}

    def _require_context(
        self,
    ) -> tuple[Optional[EnterpriseContext], Optional[dict[str, Any]]]:
        context = self._context_getter()
        if context is None:
            return None, _err(ENTERPRISE_NOT_CONFIGURED, "no active enterprise context")
        if not (self._platform_base_getter() or "").strip():
            return None, _err(
                ENTERPRISE_NOT_CONFIGURED, "enterprise platform is not configured"
            )
        return context, None

    # -- INT-120 -------------------------------------------------------------------
    async def knowledge_search(
        self, query: str, space: Optional[str] = None
    ) -> dict[str, Any]:
        context, failure = self._require_context()
        if failure is not None:
            return failure
        payload: dict[str, Any] = {"query": str(query or "")}
        if space:
            payload["space"] = str(space)
        status, body = await self._request(
            context, "POST", "/integration/v1/knowledge/search", json_body=payload
        )
        if status >= 400:
            return (
                {"ok": False, **body}
                if "error" in body
                else _err(UPSTREAM_UNAVAILABLE, f"knowledge search failed ({status})")
            )
        return body

    async def knowledge_read(self, knowledge_id: str) -> dict[str, Any]:
        context, failure = self._require_context()
        if failure is not None:
            return failure
        kid = str(knowledge_id or "").strip().strip("/")
        if not kid or ".." in kid or "/" in kid:
            return _err("INVALID_KNOWLEDGE_ID", "knowledge_id must be a single id")
        status, body = await self._request(
            context, "GET", f"/integration/v1/knowledge/{kid}"
        )
        if status >= 400:
            return (
                {"ok": False, **body}
                if "error" in body
                else _err(UPSTREAM_UNAVAILABLE, f"knowledge read failed ({status})")
            )
        return body

    # -- INT-150 + the approval bridge ----------------------------------------------
    async def mcp_invoke(
        self,
        *,
        session_id: str,
        service_id: str,
        tool_name: str,
        arguments: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        context, failure = self._require_context()
        if failure is not None:
            return failure
        service_id = str(service_id or "").strip()
        tool_name = str(tool_name or "").strip()
        if not service_id or not tool_name:
            return _err("INVALID_INVOCATION", "service_id and tool_name are required")
        args = arguments if isinstance(arguments, dict) else {}
        status, body = await self._request(
            context,
            "POST",
            "/integration/v1/mcp/invoke",
            json_body={
                "service_id": service_id,
                "tool_name": tool_name,
                "arguments": args,
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        if status == 200:
            return {"ok": True, **body} if "result" not in body else {"ok": True, **body}
        if (
            status == 202
            and isinstance(body, dict)
            and (body.get("error") or {}).get("code") == MCP_APPROVAL_REQUIRED
        ):
            return await self._await_approval(
                context,
                session_id=session_id,
                service_id=service_id,
                tool_name=tool_name,
                arguments=args,
                approval=body.get("approval") if isinstance(body.get("approval"), dict) else {},
                body=body,
            )
        if status >= 400:
            return (
                {"ok": False, **body}
                if "error" in body
                else _err(UPSTREAM_UNAVAILABLE, f"mcp invoke failed ({status})")
            )
        return _err(UPSTREAM_UNAVAILABLE, f"unexpected platform status {status}")

    async def _await_approval(
        self,
        context: EnterpriseContext,
        *,
        session_id: str,
        service_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        approval: dict[str, Any],
        body: dict[str, Any],
    ) -> dict[str, Any]:
        invocation_id = str(
            approval.get("invocation_id") or body.get("invocation_id") or ""
        )
        if not invocation_id:
            _audit(
                self._audit,
                {
                    "session_id": session_id,
                    "tool": MCP_INVOKE_TOOL,
                    "arguments": {"service_id": service_id, "tool_name": tool_name},
                    "stage": "approval_missing_invocation",
                    "status": "refused",
                    "reason": "202 without invocation_id",
                },
            )
            return _err(UPSTREAM_UNAVAILABLE, "approval request carried no invocation_id")
        self._track_pending(context, invocation_id)
        try:
            return await self._approval_loop(
                context,
                session_id=session_id,
                service_id=service_id,
                tool_name=tool_name,
                arguments=arguments,
                invocation_id=invocation_id,
            )
        finally:
            self._untrack_pending(context, invocation_id)

    async def _approval_loop(
        self,
        context: EnterpriseContext,
        *,
        session_id: str,
        service_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        invocation_id: str,
    ) -> dict[str, Any]:
        from ..inbox import args_preview

        summary = args_preview(arguments)
        routing = self._prompt_router(session_id) or {}
        item = self._inbox.add_approval(
            session_id,
            f"Allow `{tool_name}` on `{service_id}`?",
            body=(
                f"service: {service_id} · tool: {tool_name}\n{summary}"
                if summary
                else f"service: {service_id} · tool: {tool_name}"
            ),
            inbox=str(routing.get("inbox") or "default"),
            visibility=str(routing.get("visibility") or "inbox"),
            data={
                "enterprise": True,
                "tool": MCP_INVOKE_TOOL,
                "category": "enterprise",
                "arguments": {
                    "service_id": service_id,
                    "tool_name": tool_name,
                    "arguments": arguments,
                },
                "invocation_id": invocation_id,
                "context_key": context.key,
            },
            tool_call_id=None,
        )
        _audit(
            self._audit,
            {
                "session_id": session_id,
                "tool": MCP_INVOKE_TOOL,
                "arguments": {"service_id": service_id, "tool_name": tool_name},
                "stage": "approval_parked",
                "status": "pending",
                "reason": f"item={item.id} invocation={invocation_id}",
            },
        )
        try:
            resolution = await asyncio.wait_for(
                self._inbox.wait(item.id), timeout=self._approval_timeout
            )
        except TimeoutError:
            # The local card expires; no decision is posted for a stale ask.
            self._inbox.resolve(item.id, "expired", by="system:bridge-timeout")
            _audit(
                self._audit,
                {
                    "session_id": session_id,
                    "tool": MCP_INVOKE_TOOL,
                    "arguments": {"invocation_id": invocation_id},
                    "stage": "approval_timeout",
                    "status": "unknown",
                    "reason": f"item={item.id} waited {self._approval_timeout}s",
                },
            )
            return {
                "ok": False,
                "status": "unknown_outcome",
                "invocation_id": invocation_id,
                "error": {
                    "code": INVOCATION_UNKNOWN_OUTCOME,
                    "message": "approval wait timed out; the platform invocation is left for status query",
                },
            }
        if resolution in ("orphaned", "superseded", "expired"):
            # Context switched away (or a later answer superseded this card):
            # the old context is dead — never post a decision under it.
            _audit(
                self._audit,
                {
                    "session_id": session_id,
                    "tool": MCP_INVOKE_TOOL,
                    "arguments": {"invocation_id": invocation_id},
                    "stage": "approval_orphaned",
                    "status": "unknown",
                    "reason": f"item={item.id} resolution={resolution}",
                },
            )
            return {
                "ok": False,
                "status": "unknown_outcome",
                "invocation_id": invocation_id,
                "error": {
                    "code": INVOCATION_UNKNOWN_OUTCOME,
                    "message": f"approval {resolution}; no decision was posted",
                },
            }
        # BND-151 re-check, local half: only the approve/deny vocabulary is
        # honoured — anything else (including grant-style words like "always")
        # fails closed as a denial, mirroring _grant_offered's "validate, don't
        # trust the card". The platform re-validates the decision itself
        # (args_digest + policy_version binding, single-use).
        decision = "approve" if resolution in ("allow", "approve") else "deny"
        status, body = await self._request(
            context,
            "POST",
            f"/integration/v1/mcp/invocations/{invocation_id}/decision",
            json_body={
                "decision": decision,
                "service_id": service_id,
                "tool_name": tool_name,
                "arguments_summary": args_preview(arguments, limit=500),
            },
        )
        if status >= 400:
            return (
                {"ok": False, **body}
                if "error" in body
                else _err(UPSTREAM_UNAVAILABLE, f"decision post failed ({status})")
            )
        if decision == "deny":
            _audit(
                self._audit,
                {
                    "session_id": session_id,
                    "tool": MCP_INVOKE_TOOL,
                    "arguments": {"invocation_id": invocation_id},
                    "stage": "approval_denied",
                    "status": "denied",
                    "reason": f"item={item.id}",
                },
            )
            return {
                "ok": False,
                "status": "denied",
                "invocation_id": invocation_id,
                "error": {
                    "code": "MCP_INVOCATION_DENIED",
                    "message": "the user denied this invocation",
                },
            }
        return await self._poll_result(context, session_id, invocation_id)

    async def _poll_result(
        self,
        context: EnterpriseContext,
        session_id: str,
        invocation_id: str,
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + self._approval_timeout
        while True:
            status, body = await self._request(
                context, "GET", f"/integration/v1/mcp/invocations/{invocation_id}"
            )
            if status == 200:
                state = str(body.get("status") or "")
                if state == "succeeded":
                    return {"ok": True, **body}
                if state in ("failed", "cancelled"):
                    return {"ok": False, **body}
                if state == "unknown_outcome":
                    return {
                        "ok": False,
                        **body,
                        "error": body.get("error")
                        or {
                            "code": INVOCATION_UNKNOWN_OUTCOME,
                            "message": "the platform reports an unknown outcome; do not auto-retry writes",
                        },
                    }
                # still executing — keep polling
            elif status >= 400:
                return (
                    {"ok": False, **body}
                    if "error" in body
                    else _err(UPSTREAM_UNAVAILABLE, f"status query failed ({status})")
                )
            if asyncio.get_running_loop().time() >= deadline:
                return {
                    "ok": False,
                    "status": "unknown_outcome",
                    "invocation_id": invocation_id,
                    "error": {
                        "code": INVOCATION_UNKNOWN_OUTCOME,
                        "message": "result polling timed out; query the invocation later instead of retrying",
                    },
                }
            await asyncio.sleep(self._poll_interval)

    # -- engine tool surface --------------------------------------------------------
    def build_tools(self, session_id: str) -> list[Any]:
        """The three sync callables the ToolRegistry executes (asyncio.run per
        call — the engine already runs sync tools on worker threads)."""
        bridge = self

        def knowledge_search(query: str, space: Optional[str] = None) -> Any:
            return asyncio.run(bridge.knowledge_search(query, space))

        def knowledge_read(knowledge_id: str) -> Any:
            return asyncio.run(bridge.knowledge_read(knowledge_id))

        def mcp_invoke(
            service_id: str,
            tool_name: str,
            arguments: Optional[dict[str, Any]] = None,
        ) -> Any:
            return asyncio.run(
                bridge.mcp_invoke(
                    session_id=session_id,
                    service_id=service_id,
                    tool_name=tool_name,
                    arguments=arguments,
                )
            )

        knowledge_search.__name__ = KNOWLEDGE_SEARCH_TOOL
        knowledge_search.__doc__ = (
            "Search the enterprise knowledge base through the OPC platform "
            "(current subject + space, server-side re-authorized). Requires an "
            "active enterprise context."
        )
        knowledge_search.__aisuite_tool_metadata__ = ai.ToolMetadata(
            name=KNOWLEDGE_SEARCH_TOOL,
            category="enterprise",
            risk_level="low",
            capabilities=["enterprise", "knowledge"],
        )
        knowledge_search.__coworker_schema__ = {
            "type": "function",
            "function": {
                "name": KNOWLEDGE_SEARCH_TOOL,
                "description": knowledge_search.__doc__,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The search query."},
                        "space": {
                            "type": "string",
                            "description": "Optional knowledge space to search within.",
                        },
                    },
                    "required": ["query"],
                },
            },
        }
        knowledge_read.__name__ = KNOWLEDGE_READ_TOOL
        knowledge_read.__doc__ = (
            "Read one knowledge fragment's content by id, as authorized for the "
            "current enterprise subject."
        )
        knowledge_read.__aisuite_tool_metadata__ = ai.ToolMetadata(
            name=KNOWLEDGE_READ_TOOL,
            category="enterprise",
            risk_level="low",
            capabilities=["enterprise", "knowledge"],
        )
        knowledge_read.__coworker_schema__ = {
            "type": "function",
            "function": {
                "name": KNOWLEDGE_READ_TOOL,
                "description": knowledge_read.__doc__,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "knowledge_id": {
                            "type": "string",
                            "description": "The knowledge id returned by knowledge_search.",
                        }
                    },
                    "required": ["knowledge_id"],
                },
            },
        }
        mcp_invoke.__name__ = MCP_INVOKE_TOOL
        mcp_invoke.__doc__ = (
            "Invoke one managed MCP tool through the OPC platform (precise "
            "invocation, INT-150). The platform applies managed policy; when a "
            "human decision is required the request parks as an approval card. "
            "Requires an active enterprise context."
        )
        mcp_invoke.__aisuite_tool_metadata__ = ai.ToolMetadata(
            name=MCP_INVOKE_TOOL,
            category="enterprise",
            risk_level="medium",
            capabilities=["enterprise", "mcp"],
        )
        mcp_invoke.__coworker_schema__ = {
            "type": "function",
            "function": {
                "name": MCP_INVOKE_TOOL,
                "description": mcp_invoke.__doc__,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "service_id": {
                            "type": "string",
                            "description": "The managed MCP service id.",
                        },
                        "tool_name": {
                            "type": "string",
                            "description": "The tool's original name on that service.",
                        },
                        "arguments": {
                            "type": "object",
                            "description": "The tool arguments (a JSON object).",
                        },
                    },
                    "required": ["service_id", "tool_name"],
                },
            },
        }
        return [knowledge_search, knowledge_read, mcp_invoke]
