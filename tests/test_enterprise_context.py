"""WORK-RESOURCES-001 — manager/session-layer switch semantics (BND-007/008/120).

End-to-end through the real app + fake platform: a login binds live sessions
(enterprise tools + the persisted binding), the personal SkillLoader NEVER
carries enterprise dirs (no name shadowing), a context switch orphans the old
context's pending enterprise approvals while leaving personal approvals alone,
and logout returns everything to personal mode. The retired auto catalog sync
is gone — nothing downloads on login/refresh (TASK-023).
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from coworker.enterprise import skills as es
from coworker.inbox import STATE_RESOLVED
from coworker.server import SessionManager, create_app
from coworker.testing.fake_opc import FakeOpc, add_work_resources_samples


@pytest.fixture
def fake_opc():
    fake = FakeOpc().start()
    try:
        yield fake
    finally:
        fake.stop()


def _client(tmp_path, monkeypatch, platform: str) -> TestClient:
    monkeypatch.setenv("OPC_PLATFORM_BASE", platform)
    manager = SessionManager(workspace=tmp_path, provider=None)
    return TestClient(create_app(manager), base_url="http://127.0.0.1:8765")


def _login(client: TestClient, fake: FakeOpc) -> str:
    resp = client.post("/v1/enterprise/login/start")
    assert resp.status_code == 200, resp.text
    start = resp.json()
    code = fake.approve_login(start["login_txn_id"])
    state = fake.authorize_requests[start["login_txn_id"]]["state"]
    cb = client.get(f"/v1/enterprise/oauth/callback?code={code}&state={state}")
    assert cb.status_code == 200, cb.text
    result = client.get(f"/v1/enterprise/login/result?login_txn_id={start['login_txn_id']}")
    assert result.status_code == 200, result.text
    return result.json()["refresh_token"]


def _wait_for(predicate, timeout: float = 8.0, interval: float = 0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("condition not met in time")


def _samples_cache(fake):
    if not hasattr(fake, "_wr001_samples"):
        fake._wr001_samples = add_work_resources_samples(fake)
    return fake._wr001_samples


# -- login binds live sessions -----------------------------------------------------


def test_login_binds_sessions_without_touching_the_personal_loader(
    fake_opc, tmp_path, monkeypatch
):
    _samples_cache(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    session_view = client.get("/v1/enterprise/session")
    assert session_view.status_code == 200
    assert session_view.json()["context_id"]
    manager = client.app.state.manager
    key1 = manager._enterprise_context_key
    assert key1 and f"|{fake_opc.generation}" in key1

    ws = tmp_path / "ws"
    ws.mkdir()
    engine = manager.get_engine("sess-1", workspace=str(ws))
    tools = engine.registry.names()
    assert "knowledge_search" in tools
    assert "knowledge_read" in tools
    assert "mcp_invoke" in tools
    assert "enterprise_load_skill" in tools
    # BND-007: the personal loader never carries enterprise dirs.
    assert list(engine.enterprise_skill_dirs) == []
    personal_dirs = list(engine.skill_loader._dirs)
    assert not any("enterprise-skills" in str(d) for d in personal_dirs)

    # The menu stays personal-only even with a fresh grant.
    assert "summarizer" not in manager._live_skill_filter("sess-1", str(ws), "code")

    # The session record carries the binding (BND-160/DATA-120c).
    manager.persist_session("sess-1")
    record = manager.session_store.load("sess-1")
    assert record.enterprise["tenant"] == fake_opc.tenant
    assert record.enterprise["subject"] == fake_opc.subject
    assert record.enterprise["generation"] == fake_opc.generation


def test_login_never_downloads_anything(fake_opc, tmp_path, monkeypatch):
    """VAL-208 / TASK-023: login + refresh must not issue install or release
    traffic — the fake trips on any release/publish route."""
    _samples_cache(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    client.get("/v1/enterprise/session")
    client.get("/v1/enterprise/resources/skills")
    client.get("/v1/enterprise/resources/knowledge-bases")
    assert fake_opc.catalog_archive_calls == []
    fake_opc.assert_no_release_calls()


def test_personal_mode_unchanged(fake_opc, tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    manager = client.app.state.manager
    ws = tmp_path / "ws"
    ws.mkdir()
    engine = manager.get_engine("sess-1", workspace=str(ws))
    assert "mcp_invoke" not in engine.registry.names()
    assert "enterprise_load_skill" not in engine.registry.names()
    assert engine.enterprise_skill_dirs == []
    assert manager._live_skill_filter("sess-1", str(ws), "code") == set(
        manager.effective_skill_names("sess-1", str(ws), agent="code")
    )
    manager.persist_session("sess-1")
    assert manager.session_store.load("sess-1").enterprise == {}


# -- offline refusal (BND-008) ------------------------------------------------------


def test_offline_grant_refusal_keeps_personal_menu(fake_opc, tmp_path, monkeypatch):
    _samples_cache(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    manager = client.app.state.manager
    ws = tmp_path / "ws"
    ws.mkdir()
    manager.get_engine("sess-1", workspace=str(ws))

    fake_opc.session_check_ok = False
    import asyncio

    asyncio.run(
        es.refresh_distribution_grant(
            manager.enterprise_context(), platform_base=fake_opc.base_url
        )
    )
    names = manager._live_skill_filter("sess-1", str(ws), "code")
    # personal menu untouched by the refusal
    assert names == manager.effective_skill_names("sess-1", str(ws), agent="code")


# -- switch semantics (BND-120) ------------------------------------------------------


def test_context_switch_orphans_approvals_and_clears_authz(fake_opc, tmp_path, monkeypatch):
    _samples_cache(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    refresh1 = _login(client, fake_opc)
    manager = client.app.state.manager
    key1 = manager._enterprise_context_key

    ws = tmp_path / "ws"
    ws.mkdir()
    engine = manager.get_engine("sess-1", workspace=str(ws))

    # Seed a resource-grant cache entry, then park one ENTERPRISE approval and
    # one PERSONAL approval.
    from coworker.enterprise.resources import AUTHZ

    AUTHZ.record(manager.enterprise_context(), "T1", "cat-c1", True)
    ent = manager.inbox.add_approval(
        "sess-1", "Allow `fire` on `svc-hr`?", data={"enterprise": True, "context_key": key1}
    )
    per = manager.inbox.add_approval("sess-1", "Run `ls`?")

    # Switch account/space: the fake issues a new tenant + generation.
    fake_opc.tenant = "tenant-b"
    fake_opc.generation = 2
    _login(client, fake_opc)
    client.get("/v1/enterprise/session")
    key2 = manager._enterprise_context_key
    assert key2 != key1
    assert key2.endswith("|tenant-b|2")

    # Old context's enterprise approval is orphaned; the personal one stays.
    assert manager.inbox.get(ent.id).state == STATE_RESOLVED
    assert manager.inbox.get(ent.id).resolution == "orphaned"
    assert manager.inbox.get(per.id).state == "pending"

    # The resource authz shard is gone; the loader stays personal-only.
    with AUTHZ._lock:
        assert all(k[0] != key1 for k in AUTHZ._grants)
    assert list(engine.enterprise_skill_dirs) == []
    assert "sess-1" in manager._stale_engines
    manager.persist_session("sess-1")
    record = manager.session_store.load("sess-1")
    assert record.enterprise["tenant"] == "tenant-b"
    assert record.enterprise["generation"] == 2

    # Back to personal: logout orphans the second context's items too.
    ent2 = manager.inbox.add_approval(
        "sess-2", "Allow `x`?", data={"enterprise": True, "context_key": key2}
    )
    client.post("/v1/enterprise/logout", json={"refresh_token": refresh1})
    assert client.get("/v1/enterprise/session").status_code == 404
    assert manager._enterprise_context_key is None
    assert manager.inbox.get(ent2.id).resolution == "orphaned"
    assert list(engine.enterprise_skill_dirs) == []
    manager.persist_session("sess-1")
    assert manager.session_store.load("sess-1").enterprise == {}


def test_refresh_reuse_clears_context_and_orphans(fake_opc, tmp_path, monkeypatch):
    """INT-101d reuse detection clears the local context — the manager must
    treat it as a switch (back to personal)."""
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    refresh = _login(client, fake_opc)
    manager = client.app.state.manager
    assert manager._enterprise_context_key is not None
    ent = manager.inbox.add_approval(
        "sess-1",
        "Allow `x`?",
        data={"enterprise": True, "context_key": manager._enterprise_context_key},
    )
    fake_opc.fail_next_refresh("REFRESH_REUSE_DETECTED", 401)
    resp = client.post("/v1/enterprise/session/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 401
    assert client.get("/v1/enterprise/session").status_code == 404
    assert manager._enterprise_context_key is None
    assert manager.inbox.get(ent.id).resolution == "orphaned"


# -- same-name skills: personal vs enterprise resolution (BND-007) -------------------


def test_personal_load_skill_never_resolves_enterprise_same_name(
    fake_opc, tmp_path, monkeypatch
):
    """VAL-212: a personal skill named `summarizer` loads the PERSONAL copy;
    enterprise C1/C2 resolve only through the stable registry."""
    from coworker.enterprise import installer
    from coworker.enterprise.registry import REGISTRY, SkillRef

    samples = _samples_cache(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    manager = client.app.state.manager

    # Personal skill with the SAME display name as C1/C2.
    personal = manager.skill_store.global_dir / "summarizer"
    personal.mkdir(parents=True)
    (personal / "SKILL.md").write_text(
        "---\nname: summarizer\ndescription: d\n---\n\npersonal copy\n",
        encoding="utf-8",
    )

    # Install C1 (h1) into the current identity shard.
    ctx = manager.enterprise_context()
    out = installer.install(
        ctx, "T1", "cat-c1", samples.h1_bytes,
        expected_sha256=samples.h1,
        name="summarizer", version="1.0.0",
    )
    assert out["ok"], out

    ws = tmp_path / "ws"
    ws.mkdir()
    engine = manager.get_engine("sess-1", workspace=str(ws))
    loaded = engine.registry.execute("load_skill", {"name": "summarizer"})
    assert "personal copy" in loaded["instructions"], loaded

    # The enterprise copy resolves via the registry to the exact version dir.
    ref = SkillRef(tenant_id="T1", catalog_id="cat-c1", bundle_sha256=samples.h1)
    resolved = REGISTRY.resolve(ctx, ref)
    assert "总结给定文本" in resolved["instructions"]
    assert "personal copy" not in resolved["instructions"]


# -- binding persistence across restarts (BND-160/DATA-120c) -------------------------


def test_turn_save_preserves_persisted_enterprise_binding(tmp_path):
    """A per-turn save carries the IN-MEMORY binding; after a sidecar restart
    that map is empty, and the save must not erase the persisted binding."""
    from coworker.conversations import ConversationStore
    from coworker.sessions import SessionRecord

    store = ConversationStore(tmp_path / "store")
    rec = SessionRecord(
        session_id="sess-1",
        workspace=str(tmp_path),
        model="m",
        mode="interactive",
        messages=[{"role": "user", "content": "hi"}],
    )
    store.save(rec)
    binding = {"issuer": "i", "subject": "s", "tenant": "t", "generation": 1}
    store.set_enterprise("sess-1", binding)

    # Restart scenario: rebuilt record, no in-memory binding ({}).
    rebuilt = store.load("sess-1")
    rebuilt.enterprise = {}
    store.save(rebuilt)
    assert store.load("sess-1").enterprise == binding

    # A deliberate switch to personal still clears it (write-through path).
    store.set_enterprise("sess-1", {})
    assert store.load("sess-1").enterprise == {}

    # And a save carrying a real binding still writes it.
    rebound = store.load("sess-1")
    rebound.enterprise = binding
    store.save(rebound)
    assert store.load("sess-1").enterprise == binding
    store.close()


# -- 增量：企业技能并入 Composer「/」菜单（session_skills_view，BND-009） -------------


def _install_skill_direct(manager, samples, enabled=True):
    from coworker.enterprise import installer

    ctx = manager.enterprise_context()
    out = installer.install(
        ctx, "T1", "cat-c1", samples.h1_bytes,
        expected_sha256=samples.h1, name="summarizer", version="1.0.0",
    )
    assert out["ok"], out
    if not enabled:
        assert installer.set_enabled(ctx, "T1", "cat-c1", False)["ok"]
    return ctx


def test_menu_merges_installed_enterprise_skill(fake_opc, tmp_path, monkeypatch):
    samples = _samples_cache(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    manager = client.app.state.manager
    _install_skill_direct(manager, samples)

    view = manager.session_skills_view("sess-menu", None)
    ent = [r for r in view["skills"] if r["scope"] == "enterprise"]
    assert len(ent) == 1
    row = ent[0]
    assert row["name"] == "summarizer"
    assert row["enabled"] is True
    assert row["enterprise"] == {
        "tenant_id": "T1",
        "catalog_id": "cat-c1",
        "bundle_sha256": samples.h1,
        "version": "1.0.0",
        "context_id": manager.enterprise_context().context_id,
    }
    # 未安装目录不在菜单
    assert all(r["enterprise"]["catalog_id"] != "cat-c2" for r in ent)


def test_menu_disabled_enterprise_row_not_enabled(fake_opc, tmp_path, monkeypatch):
    samples = _samples_cache(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    manager = client.app.state.manager
    _install_skill_direct(manager, samples, enabled=False)
    view = manager.session_skills_view("sess-menu", None)
    ent = [r for r in view["skills"] if r["scope"] == "enterprise"]
    assert len(ent) == 1 and ent[0]["enabled"] is False


def test_menu_no_enterprise_without_context(fake_opc, tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    manager = client.app.state.manager
    view = manager.session_skills_view("sess-menu", None)
    assert all(r["scope"] != "enterprise" for r in view["skills"])


def test_menu_same_name_personal_and_enterprise_coexist(fake_opc, tmp_path, monkeypatch):
    samples = _samples_cache(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    manager = client.app.state.manager
    # 同名个人技能
    personal = manager.skill_store.global_dir / "summarizer"
    personal.mkdir(parents=True, exist_ok=True)
    (personal / "SKILL.md").write_text(
        "---\nname: summarizer\ndescription: 个人\n---\n\n个人版\n", encoding="utf-8"
    )
    _install_skill_direct(manager, samples)
    view = manager.session_skills_view("sess-menu", None)
    rows = [r for r in view["skills"] if r["name"] == "summarizer"]
    scopes = {r["scope"] for r in rows}
    assert "global" in scopes and "enterprise" in scopes  # 并存不覆盖
    # 个人 mute 不影响企业行
    manager.session_skills.set("sess-menu", "summarizer", False)
    view2 = manager.session_skills_view("sess-menu", None)
    ent = [r for r in view2["skills"] if r["scope"] == "enterprise" and r["name"] == "summarizer"]
    assert ent and ent[0]["enabled"] is True
    per = [r for r in view2["skills"] if r["scope"] == "global" and r["name"] == "summarizer"]
    assert per and per[0]["enabled"] is False


def test_menu_uninstall_removes_enterprise_row(fake_opc, tmp_path, monkeypatch):
    samples = _samples_cache(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    manager = client.app.state.manager
    ctx = _install_skill_direct(manager, samples)
    from coworker.enterprise import installer

    assert installer.uninstall(ctx, "T1", "cat-c1")["ok"]
    view = manager.session_skills_view("sess-menu", None)
    assert all(r["scope"] != "enterprise" for r in view["skills"])
