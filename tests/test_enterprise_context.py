"""TASK-W03 — manager/session-layer switch semantics for the enterprise context.

End-to-end through the real app + fake platform: a login binds live sessions
(tools, managed skill dirs, the persisted binding), a context switch orphans
the old context's pending enterprise approvals while leaving personal
approvals alone, bumps the generation into the new key, re-points live
engines' managed dirs, and logout returns everything to personal mode.
"""

from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from coworker.enterprise import skills as es
from coworker.inbox import STATE_RESOLVED
from coworker.server import SessionManager, create_app
from coworker.testing.fake_opc import FakeOpc


def _skill_bundle(skill_name: str = "opc-hr", body: str = "managed body") -> bytes:
    files = {
        "SKILL.md": f"---\nname: {skill_name}\ndescription: d\n---\n\n{body}\n".encode(),
    }
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode())
        h.update(b"\x00")
        h.update(files[name])
        h.update(b"\x00")
    manifest = {
        "skill_id": "opc.hr",
        "version": "1.0.0",
        "sha256": h.hexdigest(),
        "exec_mode": "local_instruction",
        "clients": ["work"],
    }
    files["opc-client.json"] = json.dumps(manifest).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


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


# -- login binds live sessions -----------------------------------------------------


def test_login_binds_sessions_and_syncs_managed_skills(fake_opc, tmp_path, monkeypatch):
    fake_opc.skill_bundles["pub-1"] = _skill_bundle()
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    session_view = client.get("/v1/enterprise/session")
    assert session_view.status_code == 200
    manager = client.app.state.manager
    key1 = manager._enterprise_context_key
    assert key1 and f"|{fake_opc.generation}" in key1

    # The switch hook installs the listed managed skill in the background.
    _wait_for(lambda: es.managed_skills(manager.enterprise_context()))

    ws = tmp_path / "ws"
    ws.mkdir()
    engine = manager.get_engine("sess-1", workspace=str(ws))
    tools = engine.registry.names()
    assert "knowledge_search" in tools
    assert "knowledge_read" in tools
    assert "mcp_invoke" in tools
    # The managed dir rides at the TAIL of the loader dir list.
    managed_root = es.managed_root(fake_opc.tenant)
    assert list(engine.enterprise_skill_dirs) == [managed_root]
    assert list(engine.skill_loader._dirs)[-1] == managed_root
    engine.skill_loader.rescan()
    skill = engine.skill_loader.get("opc-hr")
    assert skill is not None and str(managed_root) in skill.path

    # The live filter admits the managed name (grant refresh succeeded).
    assert "opc-hr" in manager._live_skill_filter("sess-1", str(ws), "code")

    # The session record carries the binding (BND-160/DATA-120c).
    manager.persist_session("sess-1")
    record = manager.session_store.load("sess-1")
    assert record.enterprise["tenant"] == fake_opc.tenant
    assert record.enterprise["subject"] == fake_opc.subject
    assert record.enterprise["generation"] == fake_opc.generation


def test_personal_mode_unchanged(fake_opc, tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    manager = client.app.state.manager
    ws = tmp_path / "ws"
    ws.mkdir()
    engine = manager.get_engine("sess-1", workspace=str(ws))
    assert "mcp_invoke" not in engine.registry.names()
    assert engine.enterprise_skill_dirs == []
    assert manager._live_skill_filter("sess-1", str(ws), "code") == set(
        manager.effective_skill_names("sess-1", str(ws), agent="code")
    )
    manager.persist_session("sess-1")
    assert manager.session_store.load("sess-1").enterprise == {}


# -- offline refusal (BND-142) ------------------------------------------------------


def test_offline_grant_refusal_hides_enterprise_skills(fake_opc, tmp_path, monkeypatch):
    fake_opc.skill_bundles["pub-1"] = _skill_bundle()
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    manager = client.app.state.manager
    _wait_for(lambda: es.managed_skills(manager.enterprise_context()))
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
    assert "opc-hr" not in names  # enterprise skills refuse to start
    # personal skills are untouched by the refusal
    assert names == manager.effective_skill_names("sess-1", str(ws), agent="code")


# -- switch semantics (BND-120) ------------------------------------------------------


def test_context_switch_orphans_approvals_and_rebinds(fake_opc, tmp_path, monkeypatch):
    fake_opc.skill_bundles["pub-1"] = _skill_bundle()
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    refresh1 = _login(client, fake_opc)
    manager = client.app.state.manager
    key1 = manager._enterprise_context_key
    _wait_for(lambda: es.managed_skills(manager.enterprise_context()))

    ws = tmp_path / "ws"
    ws.mkdir()
    engine = manager.get_engine("sess-1", workspace=str(ws))

    # Park one ENTERPRISE approval and one PERSONAL approval.
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
    assert key2.endswith("|tenant-b|2")  # generation bump is part of the key

    # Old context's enterprise approval is orphaned; the personal one stays pending.
    assert manager.inbox.get(ent.id).state == STATE_RESOLVED
    assert manager.inbox.get(ent.id).resolution == "orphaned"
    assert manager.inbox.get(per.id).state == "pending"

    # The old context's cache shard and grant are gone; the live engine's
    # managed dirs were re-pointed at the new tenant (sync installs in the
    # background, so the tail dir follows tenant-b once it lands).
    with es._lock:
        assert key1 not in es._catalog_cache
    _wait_for(
        lambda: engine.enterprise_skill_dirs
        and engine.enterprise_skill_dirs[0] == es.managed_root("tenant-b")
    )
    assert "sess-1" in manager._stale_engines  # rebuilt at next idle
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


# -- fail-closed at the loader (BND-142 reviewer finding) ---------------------------


def test_grant_refusal_unshadows_personal_same_name(fake_opc, tmp_path, monkeypatch):
    """Reviewer scenario: with the grant refused, `load_skill` must NOT resolve
    a same-named skill to the MANAGED copy — the managed dirs leave the live
    loader, and the personal copy is visible again."""
    import asyncio

    fake_opc.skill_bundles["pub-1"] = _skill_bundle(body="managed copy")
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    manager = client.app.state.manager
    _wait_for(lambda: es.managed_skills(manager.enterprise_context()))

    # A personal skill with the SAME name as the managed one.
    personal = es.state_dir() / "skills" / "opc-hr"
    personal.mkdir(parents=True)
    (personal / "SKILL.md").write_text(
        "---\nname: opc-hr\ndescription: d\n---\n\npersonal copy\n", encoding="utf-8"
    )

    ws = tmp_path / "ws"
    ws.mkdir()
    engine = manager.get_engine("sess-1", workspace=str(ws))
    # Grant fresh: the managed copy shadows the personal one.
    asyncio.run(
        es.refresh_distribution_grant(
            manager.enterprise_context(), platform_base=fake_opc.base_url
        )
    )
    loaded = engine.registry.execute("load_skill", {"name": "opc-hr"})
    assert "managed copy" in loaded["instructions"], loaded

    # Grant refused: the menu drops the managed name AND the loader stops
    # resolving to the managed copy — this very call already sees the
    # stripped dirs (the gate runs before resolution).
    fake_opc.session_check_ok = False
    asyncio.run(
        es.refresh_distribution_grant(
            manager.enterprise_context(), platform_base=fake_opc.base_url
        )
    )
    assert not es.grant_allows(manager.enterprise_context())
    loaded = engine.registry.execute("load_skill", {"name": "opc-hr"})
    assert "personal copy" in loaded["instructions"], loaded
    assert list(engine.enterprise_skill_dirs) == []

    # Grant recovers: the managed copy shadows again from the next call.
    fake_opc.session_check_ok = True
    asyncio.run(
        es.refresh_distribution_grant(
            manager.enterprise_context(), platform_base=fake_opc.base_url
        )
    )
    loaded = engine.registry.execute("load_skill", {"name": "opc-hr"})
    assert "managed copy" in loaded["instructions"], loaded


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
