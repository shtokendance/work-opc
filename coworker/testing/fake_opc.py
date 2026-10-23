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
import re
import secrets as pysecrets
import threading
import time
import unicodedata
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


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_skill_package(
    skill_md_body: str,
    *,
    root: Optional[str] = None,
    extra_files: Optional[dict[str, str]] = None,
    frontmatter_name: str = "",
    frontmatter_description: str = "",
) -> bytes:
    """Build a STANDARD skill package (TASK-002): one SKILL.md + its files in a
    single root folder, no opc-client.json, no release metadata. The h1/h2
    hashes used across the repo are sha256 of THESE bytes."""
    import io
    import zipfile

    fm = "---\n"
    if frontmatter_name:
        fm += f"name: {frontmatter_name}\n"
    if frontmatter_description:
        fm += f"description: {frontmatter_description}\n"
    fm += "---\n"
    root = (root or "skill").strip("/")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{root}/SKILL.md", fm + skill_md_body)
        for rel, text in (extra_files or {}).items():
            zf.writestr(f"{root}/{rel.lstrip('/')}", text)
    return buf.getvalue()


class WorkResourcesSamples:
    """The TASK-002 acceptance fixtures (VAL-101): U1/U2/U3 identities, T1/T2
    spaces, KBs A/B/A2, catalogs C1/C2 with real h1/h2 byte hashes."""

    def __init__(self, fake: "FakeOpc") -> None:
        # Identities: U1 sees T1+T2, U2 sees T1 (same tenant, different
        # subject), U3 sees nothing in T1.
        self.u1_token = fake.mint_token("subject-u1", ["T1", "T2"])
        self.u2_token = fake.mint_token("subject-u2", ["T1"])
        self.u3_token = fake.mint_token("subject-u3", [])
        fake.tenant_names.update({"T1": "交付中心", "T2": "品牌中心"})

        # Knowledge bases: T1 has A (apples only) and B (bananas only); T2 has
        # the same-named A2.
        fake.add_knowledge_base("T1", "kb-a", "A", "苹果知识库")
        fake.add_knowledge_base("T1", "kb-b", "B", "香蕉知识库")
        fake.add_knowledge_base("T2", "kb-a2", "A", "另一个空间的同名 A")

        # Documents with authoritative ownership.
        fake.add_scoped_document(
            "kn-apple", "T1", "kb-a", title="苹果型号", content="iPhone 16 Pro"
        )
        fake.add_scoped_document(
            "kn-banana", "T1", "kb-b", title="香蕉品种", content="香芽蕉"
        )
        fake.add_scoped_document(
            "kn-apple2", "T2", "kb-a2", title="苹果型号", content="完全不同的苹果内容"
        )

        # Catalogs: C1 (T1) and C2 (T2) share the display name "summarizer"
        # (also the name of a personal skill in the client tests). h1 is the
        # initial archive; h2 is the updated archive. Hashes are REAL — from
        # the package bytes, never fabricated.
        self.h1_bytes = build_skill_package(
            "总结给定文本的要点。\n",
            root="summarizer",
            extra_files={"reference.md": "要点模板"},
            frontmatter_name="summarizer",
            frontmatter_description="文本总结",
        )
        self.h2_bytes = build_skill_package(
            "总结给定文本的要点（v2）。\n",
            root="summarizer",
            extra_files={"reference.md": "要点模板 v2"},
            frontmatter_name="summarizer",
            frontmatter_description="文本总结",
        )
        self.h1 = sha256_hex(self.h1_bytes)
        self.h2 = sha256_hex(self.h2_bytes)
        assert self.h1 != self.h2
        fake.add_skill_catalog(
            "T1", "cat-c1", "summarizer", self.h1_bytes,
            description="T1 的总结技能", version="1.0.0",
        )
        fake.add_skill_catalog(
            "T2", "cat-c2", "summarizer", self.h2_bytes,
            description="T2 的同名总结技能", version="2.0.0",
        )


def add_work_resources_samples(fake: "FakeOpc") -> WorkResourcesSamples:
    return WorkResourcesSamples(fake)


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

        # -- WORK-RESOURCES-001 / TASK-002: resource catalog model -------------
        # The fake plays the NEW platform surface (INT-101 resources family):
        # bearer token -> identity -> entitled tenants; per-tenant knowledge
        # bases and skill catalogs with REAL archive bytes. h1/h2 sample
        # packages are plain standard SKILL.md zips (no opc-client.json, no
        # release record) whose hashes are computed from the actual bytes.
        self.token_identities: dict[str, dict[str, Any]] = {}
        self.knowledge_bases: dict[str, dict[str, dict[str, Any]]] = {}
        self.tenant_names: dict[str, str] = {}
        self.skill_catalogs: dict[str, dict[str, dict[str, Any]]] = {}
        # Per-tenant list failure injection (BND-002.5 partial semantics).
        self.fail_tenants: set[str] = set()
        # Archive behavior per catalog id:
        #   "ok" (default) | "not_ready" | "version_changed" | "hash_mismatch"
        #   | "timeout" | "truncated" | "bad_sha_header"
        self.archive_behaviors: dict[str, str] = {}
        self.archive_delay_seconds: dict[str, float] = {}
        # Scoped knowledge model: knowledge_id -> authoritative document with
        # its ACTUAL tenant/kb ownership (VAL-207: moves are modeled by
        # rewriting this mapping).
        self.knowledge_owner: dict[str, dict[str, Any]] = {}
        for _kid in self.knowledge_docs:
            self.knowledge_owner[_kid] = {
                "tenant": list(self.spaces)[0] if self.spaces else self.tenant,
                "kb": "default",
            }
        self.catalog_archive_calls: list[dict[str, Any]] = []
        self.resources_list_calls: list[dict[str, Any]] = []
        # TASK-023 tripwire: ANY request to a release/publish route records a
        # violation (tests assert this stays empty — 新链路不得碰旧协议).
        self.release_violations: list[str] = []

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

    # -- WORK-RESOURCES-001: identity + resource controls -----------------------

    def mint_token(
        self, subject: str, tenants: Optional[list[str]] = None, token: str = ""
    ) -> str:
        """Bind a bearer token to an identity with an entitled-tenant set.
        Unrecognized tokens keep the fake's legacy permissive behavior."""
        token = token or "opc-access-" + pysecrets.token_urlsafe(12)
        self.token_identities[token] = {
            "subject": subject,
            "tenants": list(tenants or []),
        }
        return token

    def identity_for(self, authorization: str) -> Optional[dict[str, Any]]:
        """The MINTED identity for a token, or None for a legacy/unregistered
        token (old-protocol tests keep the fake's permissive behavior)."""
        token = authorization[7:] if authorization.startswith("Bearer ") else ""
        return self.token_identities.get(token)

    def entitled_tenants(self, authorization: str) -> Optional[list[str]]:
        """Entitled tenants for a minted token; None = unknown token (legacy
        permissive mode: entitlement checks are skipped)."""
        ident = self.identity_for(authorization)
        if ident is None:
            return None
        return list(ident.get("tenants") or [])

    def revoke_tenant(self, subject: str, tenant: str) -> None:
        """Simulate an authoritative revocation (VAL-215 t0)."""
        for ident in self.token_identities.values():
            if ident.get("subject") == subject and tenant in ident.get("tenants", []):
                ident["tenants"] = [t for t in ident["tenants"] if t != tenant]

    def grant_tenant(self, subject: str, tenant: str) -> None:
        for ident in self.token_identities.values():
            if ident.get("subject") == subject and tenant not in ident.get("tenants", []):
                ident["tenants"].append(tenant)

    def add_knowledge_base(self, tenant: str, kb_id: str, name: str, description: str = "") -> None:
        self.knowledge_bases.setdefault(tenant, {})[kb_id] = {
            "knowledge_base_id": kb_id,
            "name": name,
            "description": description,
        }

    def add_skill_catalog(
        self,
        tenant: str,
        catalog_id: str,
        name: str,
        archive: Optional[bytes],
        *,
        description: str = "",
        version: str = "",
    ) -> None:
        """Register a catalog; archive=None models ARCHIVE_NOT_READY."""
        self.skill_catalogs.setdefault(tenant, {})[catalog_id] = {
            "catalog_id": catalog_id,
            "name": name,
            "description": description,
            "version": version,
            "archive": archive,
        }

    def move_knowledge(self, knowledge_id: str, tenant: str, kb_id: str) -> None:
        """Rewrite a document's authoritative ownership (VAL-207)."""
        self.knowledge_owner[knowledge_id] = {"tenant": tenant, "kb": kb_id}

    def add_scoped_document(
        self, knowledge_id: str, tenant: str, kb_id: str, *, title: str, content: str
    ) -> None:
        self.knowledge_owner[knowledge_id] = {"tenant": tenant, "kb": kb_id}
        self.knowledge_docs[knowledge_id] = {
            "knowledge_id": knowledge_id,
            "chunk_id": f"{knowledge_id}-c1",
            "title": title,
            "locator": f"kb://{kb_id}/{knowledge_id}",
            "content": content,
            "tenant_id": tenant,
            "knowledge_base_id": kb_id,
        }

    def assert_no_release_calls(self) -> None:
        """TASK-023 tripwire assertion: the new chain must never touch the old
        release/publish protocol."""
        assert not self.release_violations, (
            f"release/publish protocol was used: {self.release_violations}"
        )

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
            # 与真平台 INT-120a/INT-104 对齐：tenant_id（或 space 别名）+ query
            # 为必需；scope 模式下按权威归属过滤命中，不让 work 用请求参数补造。
            if not (body.get("tenant_id") or body.get("space")) or not body.get("query"):
                return JSONResponse(
                    _err("INVALID_REQUEST", "tenant_id and query are required"),
                    status_code=400,
                )
            tenant = str(body.get("tenant_id") or body.get("space"))
            entitled = self.entitled_tenants(auth)
            if entitled is not None and tenant not in entitled:
                return JSONResponse(
                    _err("SPACE_FORBIDDEN", "tenant not in active entitlement set"),
                    status_code=403,
                )
            kb_filter = str(body.get("knowledge_base_id") or "").strip()
            if kb_filter:
                hits = []
                for kid, doc in sorted(self.knowledge_docs.items()):
                    owner = self.knowledge_owner.get(kid) or {}
                    if owner.get("tenant") != tenant or owner.get("kb") != kb_filter:
                        continue
                    hits.append(
                        {
                            "knowledge_id": kid,
                            "chunk_id": doc.get("chunk_id", f"{kid}-c1"),
                            "title": doc.get("title", ""),
                            "source_tenant": tenant,
                            "knowledge_base_id": kb_filter,
                            "score": 0.9,
                        }
                    )
                return JSONResponse({"results": hits})
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

        # -- WORK-RESOURCES-001: new resources family (INT-101) --------------------

        def _auth(request: Request) -> str:
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                self.seen_tokens.append(auth[7:])
            return auth

        async def _resources_list(request: Request, kind: str) -> JSONResponse:
            auth = _auth(request)
            params = request.query_params
            self.resources_list_calls.append(
                {"kind": kind, "q": params.get("q", ""),
                 "tenant_id": params.get("tenant_id", ""),
                 "offset": params.get("offset", ""), "limit": params.get("limit", "")}
            )
            tenants = self.entitled_tenants(auth)
            if tenants is None:
                tenants = list(self.spaces)
            tenant_filter = (params.get("tenant_id") or "").strip()
            if tenant_filter:
                if tenant_filter not in tenants:
                    return JSONResponse(
                        _err("SPACE_FORBIDDEN", "tenant not in active entitlement set"),
                        status_code=403,
                    )
                tenants = [tenant_filter]
            try:
                offset = max(0, int(params.get("offset") or 0))
                limit = int(params.get("limit") or 50)
            except ValueError:
                return JSONResponse(_err("INVALID_REQUEST", "bad offset/limit"), status_code=400)
            if limit < 1 or limit > 100:
                return JSONResponse(
                    _err("INVALID_REQUEST", "limit must be within 1..100"), status_code=400
                )
            q = (params.get("q") or "").strip()
            if len(q) > 200:
                return JSONResponse(_err("INVALID_REQUEST", "q too long"), status_code=400)

            items: list[dict[str, Any]] = []
            failures: list[dict[str, Any]] = []
            for tenant in tenants:
                if tenant in self.fail_tenants:
                    failures.append({"tenant_id": tenant, "code": "UPSTREAM_UNAVAILABLE"})
                    continue
                tenant_name = self.tenant_names.get(tenant, tenant)
                if kind == "knowledge_base":
                    for kb in (self.knowledge_bases.get(tenant) or {}).values():
                        items.append(
                            {
                                "resource_kind": "knowledge_base",
                                "tenant_id": tenant,
                                "tenant_name": tenant_name,
                                "knowledge_base_id": kb["knowledge_base_id"],
                                "name": kb["name"],
                                "description": kb.get("description", ""),
                                "availability": "available",
                            }
                        )
                else:
                    for cat in (self.skill_catalogs.get(tenant) or {}).values():
                        archive = cat.get("archive")
                        sha = sha256_hex(archive) if archive is not None else ""
                        item = {
                            "resource_kind": "skill_catalog",
                            "tenant_id": tenant,
                            "tenant_name": tenant_name,
                            "catalog_id": cat["catalog_id"],
                            "name": cat["name"],
                            "description": cat.get("description", ""),
                            "version": cat.get("version", ""),
                            "bundle_sha256": sha,
                            "availability": "available" if sha else "not_ready",
                        }
                        if not sha:
                            item["unavailable_reason"] = "ARCHIVE_NOT_READY"
                        items.append(item)
            if tenants and len(failures) == len(tenants):
                return JSONResponse(
                    _err("UPSTREAM_UNAVAILABLE", "all entitled spaces failed"),
                    status_code=502,
                )
            if q:
                folded = unicodedata.normalize("NFC", q).casefold()
                items = [
                    it for it in items
                    if folded in unicodedata.normalize("NFC", it.get("name", "")).casefold()
                ]
            items.sort(
                key=lambda it: (
                    it["tenant_id"],
                    unicodedata.normalize("NFC", it.get("name", "")).casefold(),
                    it.get("knowledge_base_id") or it.get("catalog_id") or "",
                )
            )
            total = len(items)
            page = items[offset:][:limit] if offset < len(items) else []
            return JSONResponse(
                {
                    "items": page,
                    "total": total,
                    "offset": offset,
                    "limit": limit,
                    "complete": not failures,
                    "failures": failures,
                }
            )

        async def resources_knowledge_bases(request: Request) -> JSONResponse:
            return await _resources_list(request, "knowledge_base")

        async def resources_skill_catalogs(request: Request) -> JSONResponse:
            return await _resources_list(request, "skill_catalog")

        async def resources_skill_catalog(request: Request) -> JSONResponse:
            auth = _auth(request)
            catalog_id = request.path_params["catalog_id"]
            tenant = (request.query_params.get("tenant_id") or "").strip()
            if not tenant:
                return JSONResponse(
                    _err("INVALID_REQUEST", "tenant_id query parameter is required"),
                    status_code=400,
                )
            entitled = self.entitled_tenants(auth)
            if entitled is not None and tenant not in entitled:
                return JSONResponse(
                    _err("SPACE_FORBIDDEN", "tenant not in active entitlement set"),
                    status_code=403,
                )
            cat = (self.skill_catalogs.get(tenant) or {}).get(catalog_id)
            if cat is None:
                return JSONResponse(
                    _err("RESOURCE_NOT_ACCESSIBLE", "skill catalog is not accessible"),
                    status_code=404,
                )
            archive = cat.get("archive")
            sha = sha256_hex(archive) if archive is not None else ""
            item = {
                "resource_kind": "skill_catalog",
                "tenant_id": tenant,
                "tenant_name": self.tenant_names.get(tenant, tenant),
                "catalog_id": cat["catalog_id"],
                "name": cat["name"],
                "description": cat.get("description", ""),
                "version": cat.get("version", ""),
                "bundle_sha256": sha,
                "availability": "available" if sha else "not_ready",
            }
            if not sha:
                item["unavailable_reason"] = "ARCHIVE_NOT_READY"
            return JSONResponse({"item": item})

        async def resources_skill_archive(request: Request) -> JSONResponse:
            from starlette.responses import Response

            auth = _auth(request)
            catalog_id = request.path_params["catalog_id"]
            params = request.query_params
            tenant = (params.get("tenant_id") or "").strip()
            expected = (params.get("expected_sha256") or "").strip().lower()
            self.catalog_archive_calls.append(
                {"catalog_id": catalog_id, "tenant_id": tenant, "expected_sha256": expected}
            )
            if not tenant:
                return JSONResponse(
                    _err("INVALID_REQUEST", "tenant_id query parameter is required"),
                    status_code=400,
                )
            if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
                return JSONResponse(
                    _err("INVALID_REQUEST", "expected_sha256 must be 64-char hex"),
                    status_code=400,
                )
            entitled_a = self.entitled_tenants(auth)
            if entitled_a is not None and tenant not in entitled_a:
                return JSONResponse(
                    _err("SPACE_FORBIDDEN", "tenant not in active entitlement set"),
                    status_code=403,
                )
            cat = (self.skill_catalogs.get(tenant) or {}).get(catalog_id)
            if cat is None:
                return JSONResponse(
                    _err("RESOURCE_NOT_ACCESSIBLE", "skill catalog is not accessible"),
                    status_code=404,
                )
            behavior = self.archive_behaviors.get(catalog_id, "ok")
            if behavior == "not_ready":
                return JSONResponse(
                    _err("ARCHIVE_NOT_READY", "archive of this catalog is not ready"),
                    status_code=409,
                )
            archive = cat.get("archive")
            if archive is None:
                return JSONResponse(
                    _err("ARCHIVE_NOT_READY", "archive of this catalog is not ready"),
                    status_code=409,
                )
            current_sha = sha256_hex(archive)
            if behavior == "version_changed" or (
                expected != current_sha and behavior != "hash_mismatch"
            ):
                # 目录已被替换 / 预期与当前不一致：版本冲突，不静默替换。
                return JSONResponse(
                    _err(
                        "SKILL_VERSION_CHANGED",
                        "the skill catalog has a different version now",
                    ),
                    status_code=409,
                )
            delay = self.archive_delay_seconds.get(catalog_id, 0.0)
            if delay or behavior == "timeout":
                await asyncio.sleep(delay if behavior != "timeout" else 30.0)
            if behavior == "timeout":
                return JSONResponse(_err("UPSTREAM_UNAVAILABLE", "slow"), status_code=502)
            payload = archive
            if behavior == "truncated":
                payload = archive[: max(1, len(archive) // 2)]
            if behavior == "hash_mismatch":
                # 存储字节与目录摘要不符（腐败/篡改）：仍按预期声明，客户端
                # 三向校验必须发现。
                payload = archive + b"corrupted"
            header_sha = current_sha
            if behavior == "bad_sha_header":
                header_sha = "0" * 64
            return Response(
                payload,
                media_type="application/zip",
                headers={"X-SHA256": header_sha,
                         "Content-Length": str(len(payload))},
            )

        # -- TASK-023: retired release/publish routes + tripwire -------------------

        async def retired_release_route(request: Request) -> JSONResponse:
            """The old release distribution routes are gone (VAL-216): a 404,
            and — because the new chain must never touch this protocol — a
            recorded violation tests assert stays empty."""
            self.release_violations.append(request.url.path)
            return JSONResponse(
                _err("RESOURCE_NOT_ACCESSIBLE", "release routes are retired"),
                status_code=404,
            )

        async def scoped_knowledge_read(request: Request) -> JSONResponse:
            """GET /integration/v1/knowledge/{id} — default + explicit scope mode
            (INT-104): ?tenant=&knowledge_base_id= 成对出现时校验权威归属，
            错配 403 SCOPE_MISMATCH，不泄露正文（VAL-207）。"""
            auth = _auth(request)
            knowledge_id = request.path_params["knowledge_id"]
            scope_tenant = (request.query_params.get("tenant") or "").strip()
            scope_kb = (request.query_params.get("knowledge_base_id") or "").strip()
            if scope_kb and not scope_tenant:
                return JSONResponse(
                    _err("INVALID_REQUEST", "tenant is required with knowledge_base_id"),
                    status_code=400,
                )
            entitled_c = self.entitled_tenants(auth)
            if entitled_c is not None and scope_tenant and scope_tenant not in entitled_c:
                return JSONResponse(
                    _err("SPACE_FORBIDDEN", "tenant not in active entitlement set"),
                    status_code=403,
                )
            doc = self.knowledge_docs.get(knowledge_id)
            owner = self.knowledge_owner.get(knowledge_id)
            if doc is None or owner is None:
                return JSONResponse(
                    _err("RESOURCE_NOT_ACCESSIBLE", "knowledge is not accessible"),
                    status_code=404,
                )
            if scope_kb:
                if owner["tenant"] != scope_tenant or owner["kb"] != scope_kb:
                    return JSONResponse(
                        _err(
                            "SCOPE_MISMATCH",
                            "knowledge does not belong to the scoped tenant or KB",
                        ),
                        status_code=403,
                    )
                return JSONResponse(
                    {
                        "knowledge_id": knowledge_id,
                        "source_tenant": scope_tenant,
                        "data": dict(doc),
                    }
                )
            if scope_tenant and owner["tenant"] != scope_tenant:
                return JSONResponse(
                    _err("SPACE_FORBIDDEN", "knowledge not visible in this tenant"),
                    status_code=403,
                )
            return JSONResponse(
                {
                    "knowledge_id": knowledge_id,
                    "source_tenant": owner["tenant"],
                    "data": dict(doc),
                }
            )

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
                    scoped_knowledge_read,
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
                # WORK-RESOURCES-001: new resources family (INT-101/103/104).
                Route(
                    "/integration/v1/resources/knowledge-bases",
                    resources_knowledge_bases,
                    methods=["GET"],
                ),
                Route(
                    "/integration/v1/resources/skills",
                    resources_skill_catalogs,
                    methods=["GET"],
                ),
                Route(
                    "/integration/v1/resources/skills/{catalog_id}",
                    resources_skill_catalog,
                    methods=["GET"],
                ),
                Route(
                    "/integration/v1/resources/skills/{catalog_id}/archive",
                    resources_skill_archive,
                    methods=["GET"],
                ),
                # TASK-023: the old release distribution routes are retired —
                # any hit is a 404 AND a recorded violation (tripwire).
                Route("/integration/v1/skills", retired_release_route, methods=["GET"]),
                Route(
                    "/integration/v1/skills/{publish_id}/download",
                    retired_release_route,
                    methods=["GET"],
                ),
                Route(
                    "/integration/v1/internal/skills",
                    retired_release_route,
                    methods=["GET"],
                ),
            ]
        )
