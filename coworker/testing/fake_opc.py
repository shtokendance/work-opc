"""FakeOpc — a controllable, in-process test double for the OPC platform (INT-101).

Implements just enough of ``/integration/v1/auth/*`` for the desktop's
enterprise login to run end-to-end with **no network and no real identity
system**: authorize → (test approves) → token exchange with PKCE S256 +
one-time code consumption → refresh rotation + reuse detection → logout.

Built on Starlette + uvicorn (both already core deps), served on an ephemeral
port from a **dedicated background thread** with its own event loop — so the
sync ``fastapi.testclient.TestClient`` (which runs the app in its own portal)
exercises a real TCP round trip, same as production. The desktop's service is
pointed at the fake by setting ``OPC_PLATFORM_BASE`` to the fake's URL.

Two ways to drive the browser half:

* ``approve_login(login_txn_id)`` mints the one-time authorization code the
  real platform would return through the WeCom redirect.
* ``fail_next_token(code, http_status)`` arms the next token exchange to fail
  with a specific BND-180 code (e.g. SUBJECT_NOT_PROVISIONED).

Control methods mutate plain dicts from the test thread while Starlette
handlers read them in the server thread — fine for a test double.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets as pysecrets
import threading
import time
import uuid
from typing import Any, Optional

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

logger = logging.getLogger("coworker.testing.fake_opc")


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _err(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "request_id": str(uuid.uuid4())}}


class FakeOpc:
    """A running fake OPC platform (one deployment)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port

        # authorize requests, keyed by login_txn_id.
        self.authorize_requests: dict[str, dict[str, Any]] = {}
        # one-time codes: code -> login_txn_id (consumed codes are removed;
        # REPLAY of a consumed code is observable via consumed_codes).
        self.codes: dict[str, str] = {}
        self.consumed_codes: list[str] = []
        # refresh rotation: refresh -> session family; rotated-out refreshes keep a
        # tombstone so reuse revokes the right family (INT-101d).
        self.families: dict[str, str] = {}  # refresh -> family_id
        self.rotated_out: dict[str, str] = {}  # old refresh -> family_id
        self.family_refresh: dict[str, str] = {}  # family_id -> current refresh
        self.revoked_families: set[str] = set()
        self.logouts: list[dict[str, Any]] = []
        self.authorize_calls = 0
        self.token_calls = 0
        self.refresh_calls = 0
        self._fail_next_token: Optional[tuple[str, int]] = None  # (code, http_status)
        self._fail_next_refresh: Optional[tuple[str, int]] = None

        # TASK-W03/W04 surfaces: knowledge adapter (INT-120), precise MCP
        # invocation (INT-150), managed skill listing/download (INT-142).
        self.knowledge_docs: dict[str, dict[str, Any]] = {
            "kn-1": {
                "knowledge_id": "kn-1",
                "chunk_id": "kn-1-c1",
                "title": "离职流程",
                "locator": "kb://技术部/离职文档",
                "content": "技术部离职流程：……",
            }
        }
        self.knowledge_search_results: list[dict[str, Any]] = [
            {"knowledge_id": "kn-1", "chunk_id": "kn-1-c1", "title": "离职流程", "score": 0.9}
        ]
        self._fail_next_knowledge_search: Optional[tuple[str, int]] = None
        self.knowledge_search_calls: list[dict[str, Any]] = []
        # Each mcp invoke pops one behavior:
        #   {"kind": "succeed", "result": ...}
        #   {"kind": "approval_required"}          → 202 MCP_APPROVAL_REQUIRED
        #   {"kind": "policy_denied"}              → 403 MCP_POLICY_DENIED
        #   {"kind": "error", "code": ..., "status": ...}
        self.mcp_invoke_behaviors: list[dict[str, Any]] = [
            {"kind": "succeed", "result": {"rows": [{"name": "alice"}]}}
        ]
        self.invocations: dict[str, dict[str, Any]] = {}
        self.mcp_decisions: list[dict[str, Any]] = []
        self.mcp_invoke_calls: list[dict[str, Any]] = []
        # Managed skills (INT-142): publish_id -> archive bytes; the listing
        # carries each entry's payload digest.
        self.skill_bundles: dict[str, bytes] = {}
        self.skill_listing_extra: list[dict[str, Any]] = []
        self.delisted_skills: set[str] = set()
        self.skills_requests: list[dict[str, Any]] = []
        # Flaky authz/session check for the grant refresh (BND-142).
        self.session_check_ok = True
        self.session_calls = 0
        self.seen_tokens: list[str] = []

        # Identities handed out by approve_login.
        self.subject = "subject-yangjing"
        self.tenant = "tenant-技术交付中心"
        self.generation = 1
        self.authz_version = 7
        self.spaces = ["space-技术交付中心"]

        self.app = self._build_app()
        self._server: Optional[uvicorn.Server] = None
        self._thread: Optional[threading.Thread] = None

    # -- urls / lifecycle -------------------------------------------------------

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> "FakeOpc":
        """Serve from a background thread's event loop (sync tests stay sync)."""
        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="warning",
            lifespan="off",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=lambda: asyncio.run(self._server.serve()),
            daemon=True,
            name="fake-opc",
        )
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started:
            if time.time() > deadline:
                raise RuntimeError("fake OPC platform did not start in 10s")
            time.sleep(0.01)
        sock = self._server.servers[0].sockets[0]
        self.port = sock.getsockname()[1]
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None

    def __enter__(self) -> "FakeOpc":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # -- test controls ----------------------------------------------------------

    def approve_login(self, login_txn_id: str, code: str = "") -> str:
        """Mint the one-time authorization code the real platform would issue
        after the WeCom redirect completes."""
        if login_txn_id not in self.authorize_requests:
            raise KeyError(f"unknown login_txn_id: {login_txn_id}")
        code = code or pysecrets.token_urlsafe(16)
        self.codes[code] = login_txn_id
        return code

    def fail_next_token(self, code: str, http_status: int = 403) -> None:
        """Arm the next /auth/token call to fail with this BND-180 code."""
        self._fail_next_token = (code, http_status)

    def fail_next_refresh(self, code: str, http_status: int = 401) -> None:
        self._fail_next_refresh = (code, http_status)

    def fail_next_knowledge_search(self, code: str, http_status: int = 403) -> None:
        self._fail_next_knowledge_search = (code, http_status)

    # -- routes ------------------------------------------------------------------

    def _build_app(self) -> Starlette:
        async def authorize(request: Request) -> JSONResponse:
            body = await request.json()
            self.authorize_calls += 1
            client_id = body.get("client_id", "")
            redirect_uri = body.get("redirect_uri", "")
            if client_id != "work-desktop":
                return JSONResponse(_err("AUTH_STATE_MISMATCH", "unknown client_id"), status_code=400)
            if not redirect_uri.startswith("http://127.0.0.1:"):
                return JSONResponse(
                    _err("AUTH_STATE_MISMATCH", "redirect_uri must be a 127.0.0.1 loopback"),
                    status_code=400,
                )
            login_txn_id = str(uuid.uuid4())
            self.authorize_requests[login_txn_id] = {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": body.get("state", ""),
                "code_challenge": body.get("code_challenge", ""),
                "code_challenge_method": body.get("code_challenge_method", ""),
                "device": body.get("device", ""),
                "expires_at": time.time() + 120,
            }
            return JSONResponse(
                {
                    "authorize_url": f"{self.base_url}/wecom/qr?txn={login_txn_id}",
                    "login_txn_id": login_txn_id,
                }
            )

        async def token(request: Request) -> JSONResponse:
            body = await request.json()
            self.token_calls += 1
            if self._fail_next_token is not None:
                code, status = self._fail_next_token
                self._fail_next_token = None
                return JSONResponse(_err(code, f"injected failure {code}"), status_code=status)
            if body.get("grant_type") != "authorization_code":
                return JSONResponse(_err("AUTH_STATE_MISMATCH", "bad grant_type"), status_code=400)
            code = body.get("code", "")
            if code in self.consumed_codes:
                # BND-101: replaying a consumed code is observable and fatal.
                return JSONResponse(_err("AUTH_CODE_REPLAYED", "code already consumed"), status_code=400)
            login_txn_id = self.codes.pop(code, None)
            if login_txn_id is None:
                return JSONResponse(_err("AUTH_STATE_MISMATCH", "unknown code"), status_code=400)
            self.consumed_codes.append(code)
            txn = self.authorize_requests.get(login_txn_id)
            if txn is None or txn["expires_at"] < time.time():
                return JSONResponse(_err("AUTH_STATE_MISMATCH", "login txn expired"), status_code=400)
            verifier = body.get("code_verifier", "")
            if txn["code_challenge"] and _s256(verifier) != txn["code_challenge"]:
                return JSONResponse(_err("AUTH_STATE_MISMATCH", "PKCE verification failed"), status_code=400)
            # 平台 token 端点按 client_id 推导受众并要求兑换回显（INT-101c）；
            # 缺字段/错值一律 AUTH_STATE_MISMATCH，与真平台契约对齐。
            if body.get("audience", "") != "work":
                return JSONResponse(
                    _err("AUTH_STATE_MISMATCH", "audience mismatch with transaction"),
                    status_code=400,
                )
            family_id = str(uuid.uuid4())
            access = "opc-access-" + pysecrets.token_urlsafe(12)
            refresh = "opc-refresh-" + pysecrets.token_urlsafe(16)
            self.families[refresh] = family_id
            self.family_refresh[family_id] = refresh
            return JSONResponse(
                {
                    "access_token": access,
                    "refresh_token": refresh,
                    "expires_in": 900,
                    "issuer": "https://opc.example.com",
                    "subject": self.subject,
                    "tenant": self.tenant,
                    "generation": self.generation,
                    "authz_version": self.authz_version,
                    "spaces": list(self.spaces),
                }
            )

        async def refresh(request: Request) -> JSONResponse:
            body = await request.json()
            self.refresh_calls += 1
            if self._fail_next_refresh is not None:
                code, status = self._fail_next_refresh
                self._fail_next_refresh = None
                return JSONResponse(_err(code, f"injected failure {code}"), status_code=status)
            refresh = body.get("refresh_token", "")
            family_id = self.families.get(refresh)
            if family_id is None or family_id in self.revoked_families:
                # An old (rotated) refresh presented again — or any refresh of
                # a revoked family. INT-101d: revoke the whole family.
                family = family_id or self.rotated_out.get(refresh)
                if family:
                    self.revoked_families.add(family)
                return JSONResponse(
                    _err("REFRESH_REUSE_DETECTED", "refresh reuse: family revoked"),
                    status_code=401,
                )
            # Rotation: old refresh dies at the platform, new one takes over.
            del self.families[refresh]
            self.rotated_out[refresh] = family_id
            new_access = "opc-access-" + pysecrets.token_urlsafe(12)
            new_refresh = "opc-refresh-" + pysecrets.token_urlsafe(16)
            self.families[new_refresh] = family_id
            self.family_refresh[family_id] = new_refresh
            return JSONResponse(
                {
                    "access_token": new_access,
                    "refresh_token": new_refresh,
                    "expires_in": 900,
                }
            )

        async def logout(request: Request) -> JSONResponse:
            body = await request.json()
            self.logouts.append(dict(body))
            refresh = body.get("refresh_token", "")
            family_id = self.families.get(refresh)
            if family_id:
                self.revoked_families.add(family_id)
            return JSONResponse({"ok": True})

        async def session(request: Request) -> JSONResponse:
            self.session_calls += 1
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                self.seen_tokens.append(auth[7:])
            if not self.session_check_ok:
                return JSONResponse(
                    _err("SESSION_REVOKED", "session check failing (injected)"),
                    status_code=401,
                )
            return JSONResponse(
                {
                    "subject": self.subject,
                    "tenant": self.tenant,
                    "authz_version": self.authz_version,
                    "spaces": list(self.spaces),
                }
            )

        # -- TASK-W04: knowledge adapter (INT-120) --------------------------------

        async def knowledge_search(request: Request) -> JSONResponse:
            body = await request.json()
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                self.seen_tokens.append(auth[7:])
            self.knowledge_search_calls.append(body)
            if self._fail_next_knowledge_search is not None:
                code, status = self._fail_next_knowledge_search
                self._fail_next_knowledge_search = None
                return JSONResponse(_err(code, f"injected failure {code}"), status_code=status)
            return JSONResponse({"results": list(self.knowledge_search_results)})

        async def knowledge_read(request: Request) -> JSONResponse:
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                self.seen_tokens.append(auth[7:])
            knowledge_id = request.path_params["knowledge_id"]
            doc = self.knowledge_docs.get(knowledge_id)
            if doc is None:
                return JSONResponse(
                    _err("SPACE_FORBIDDEN", "no such knowledge id for this subject"),
                    status_code=403,
                )
            return JSONResponse(dict(doc))

        # -- TASK-W04: precise MCP invocation (INT-150 platform adapter) ----------

        async def mcp_invoke(request: Request) -> JSONResponse:
            body = await request.json()
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                self.seen_tokens.append(auth[7:])
            self.mcp_invoke_calls.append(body)
            behavior = (
                self.mcp_invoke_behaviors.pop(0)
                if self.mcp_invoke_behaviors
                else {"kind": "succeed", "result": {"ok": True}}
            )
            kind = behavior.get("kind")
            if kind == "approval_required":
                invocation_id = str(uuid.uuid4())
                self.invocations[invocation_id] = {
                    "invocation_id": invocation_id,
                    "status": "waiting_approval",
                    "service_id": body.get("service_id", ""),
                    "tool_name": body.get("tool_name", ""),
                }
                return JSONResponse(
                    {
                        "error": {
                            "code": "MCP_APPROVAL_REQUIRED",
                            "message": "a human decision is required",
                            "request_id": str(uuid.uuid4()),
                        },
                        "approval": {"invocation_id": invocation_id},
                    },
                    status_code=202,
                )
            if kind == "policy_denied":
                return JSONResponse(
                    _err("MCP_POLICY_DENIED", "managed policy denied this call"),
                    status_code=403,
                )
            if kind == "error":
                return JSONResponse(
                    _err(str(behavior.get("code") or "UPSTREAM_UNAVAILABLE"), "injected"),
                    status_code=int(behavior.get("status") or 502),
                )
            invocation_id = str(uuid.uuid4())
            self.invocations[invocation_id] = {
                "invocation_id": invocation_id,
                "status": "succeeded",
                "result": behavior.get("result"),
            }
            return JSONResponse(
                {"invocation_id": invocation_id, "status": "succeeded", "result": behavior.get("result")}
            )

        async def mcp_invocation_get(request: Request) -> JSONResponse:
            invocation = self.invocations.get(request.path_params["invocation_id"])
            if invocation is None:
                return JSONResponse(_err("INVOCATION_NOT_FOUND", "unknown invocation"), status_code=404)
            return JSONResponse(dict(invocation))

        async def mcp_invocation_decision(request: Request) -> JSONResponse:
            invocation_id = request.path_params["invocation_id"]
            body = await request.json()
            self.mcp_decisions.append({"invocation_id": invocation_id, **body})
            invocation = self.invocations.get(invocation_id)
            if invocation is None:
                return JSONResponse(_err("INVOCATION_NOT_FOUND", "unknown invocation"), status_code=404)
            if invocation["status"] != "waiting_approval":
                # Single-use binding: repeat decisions are refused.
                return JSONResponse(
                    _err("INVOCATION_ALREADY_DECIDED", "this invocation was already decided"),
                    status_code=409,
                )
            if body.get("decision") == "approve":
                invocation["status"] = "succeeded"
                invocation["result"] = {"approved": True}
            else:
                invocation["status"] = "failed"
                invocation["error"] = {"code": "MCP_INVOCATION_DENIED", "message": "denied by user"}
            return JSONResponse({"ok": True, "invocation_id": invocation_id})

        # -- TASK-W03: managed skill listing/download (INT-142) -------------------

        async def skills_list(request: Request) -> JSONResponse:
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                self.seen_tokens.append(auth[7:])
            self.skills_requests.append({"path": "/integration/v1/skills"})
            entries = list(self.skill_listing_extra)
            import hashlib as _hashlib

            for publish_id, bundle in self.skill_bundles.items():
                if any(e.get("publish_id") == publish_id for e in entries):
                    continue
                entries.append(
                    {
                        "publish_id": publish_id,
                        "sha256": _hashlib.sha256(bundle).hexdigest(),
                        "skill_id": publish_id,
                        "version": "1.0.0",
                    }
                )
            return JSONResponse({"skills": entries})

        async def skills_download(request: Request) -> JSONResponse:
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                self.seen_tokens.append(auth[7:])
            publish_id = request.path_params["publish_id"]
            self.skills_requests.append({"path": f"/integration/v1/skills/{publish_id}/download"})
            if publish_id in self.delisted_skills:
                return JSONResponse(_err("SKILL_DELISTED", "delisted"), status_code=403)
            bundle = self.skill_bundles.get(publish_id)
            if bundle is None:
                return JSONResponse(_err("SKILL_NOT_FOUND", "unknown publish_id"), status_code=404)
            from starlette.responses import Response

            return Response(bundle, media_type="application/zip")

        return Starlette(
            routes=[
                Route("/integration/v1/auth/authorize", authorize, methods=["POST"]),
                Route("/integration/v1/auth/token", token, methods=["POST"]),
                Route("/integration/v1/auth/refresh", refresh, methods=["POST"]),
                Route("/integration/v1/auth/logout", logout, methods=["POST"]),
                Route("/integration/v1/session", session, methods=["GET"]),
                Route("/integration/v1/knowledge/search", knowledge_search, methods=["POST"]),
                Route(
                    "/integration/v1/knowledge/{knowledge_id}",
                    knowledge_read,
                    methods=["GET"],
                ),
                Route("/integration/v1/mcp/invoke", mcp_invoke, methods=["POST"]),
                Route(
                    "/integration/v1/mcp/invocations/{invocation_id}",
                    mcp_invocation_get,
                    methods=["GET"],
                ),
                Route(
                    "/integration/v1/mcp/invocations/{invocation_id}/decision",
                    mcp_invocation_decision,
                    methods=["POST"],
                ),
                Route("/integration/v1/skills", skills_list, methods=["GET"]),
                Route(
                    "/integration/v1/skills/{publish_id}/download",
                    skills_download,
                    methods=["GET"],
                ),
            ]
        )
