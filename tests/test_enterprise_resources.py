"""WORK-RESOURCES-001 — sidecar resource API + install operations (TASK-015).

VAL-201/209/210/214/218 through the real app + fake platform: list shapes and
partial failures, single-item authorization, explicit install with operation
polling, same-SHA idempotency, version conflicts, 404-vs-501 discrimination,
and the late-response identity guard.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from coworker.enterprise import installer
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


def _context_id(client: TestClient) -> str:
    resp = client.get("/v1/enterprise/session")
    assert resp.status_code == 200, resp.text
    return resp.json()["context_id"]


def _bind_u1(client: TestClient, samples) -> None:
    """The login token is the fake's default identity (fallback spaces without
    T1/T2); the minted U1 token carries the real entitlement."""
    client.app.state.manager.enterprise_context().access_token = samples.u1_token


def _wait_for(predicate, timeout: float = 10.0, interval: float = 0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("condition not met in time")


# -- VAL-201: lists ------------------------------------------------------------


def test_knowledge_base_list_signed_in(fake_opc, tmp_path, monkeypatch):
    samples = add_work_resources_samples(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    _bind_u1(client, samples)
    resp = client.get("/v1/enterprise/resources/knowledge-bases")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["context_id"] == _context_id(client)
    assert body["total"] == 3
    assert body["complete"] is True and body["failures"] == []
    # 排序：tenant、名称（NFC fold）、ID 升序
    keys = [(i["tenant_id"], i["name"].casefold(),
             i.get("knowledge_base_id") or i.get("catalog_id")) for i in body["items"]]
    assert keys == sorted(keys)
    # 同名 A 与 A2 不合并
    names = [i["name"] for i in body["items"]]
    assert names.count("A") == 2


def test_knowledge_base_list_partial_failure(fake_opc, tmp_path, monkeypatch):
    """VAL-201: 部分授权空间失败 → complete=false + failures，不当成空/撤权。"""
    samples = add_work_resources_samples(fake_opc)
    fake_opc.fail_tenants.add("T2")
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    _bind_u1(client, samples)
    resp = client.get("/v1/enterprise/resources/knowledge-bases")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["complete"] is False
    assert body["failures"] == [{"tenant_id": "T2", "code": "UPSTREAM_UNAVAILABLE"}]
    assert body["total"] == 2  # 只剩 T1 的 A/B


def test_lists_require_signin(fake_opc, tmp_path, monkeypatch):
    """VAL-202: 未登录 → 401，不泄露任何资源信息。"""
    add_work_resources_samples(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    resp = client.get("/v1/enterprise/resources/knowledge-bases")
    assert resp.status_code == 401
    resp = client.get("/v1/enterprise/resources/skills")
    assert resp.status_code == 401


def test_skills_list_merges_local_state(fake_opc, tmp_path, monkeypatch):
    samples = add_work_resources_samples(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    _bind_u1(client, samples)
    manager = client.app.state.manager
    ctx = manager.enterprise_context()
    out = installer.install(
        ctx, "T1", "cat-c1", samples.h1_bytes,
        expected_sha256=samples.h1, name="summarizer",
    )
    assert out["ok"]
    resp = client.get("/v1/enterprise/resources/skills")
    assert resp.status_code == 200, resp.text
    items = {i["catalog_id"]: i for i in resp.json()["items"]}
    assert items["cat-c1"]["local_status"] == "installed_enabled"
    assert items["cat-c1"]["installed_sha256"] == samples.h1
    assert items["cat-c2"]["local_status"] == "not_installed"


# -- VAL-209/210: install + operations ------------------------------------------


def _install(client, samples, sha=None, tenant="T1"):
    sha = sha or samples.h1
    return client.post(
        "/v1/enterprise/resources/skills/cat-c1/install",
        json={
            "tenant_id": tenant,
            "expected_sha256": sha,
            "context_id": _context_id(client),
            "name": "summarizer",
            "version": "1.0.0",
        },
    )


def test_install_flow_202_then_succeeded(fake_opc, tmp_path, monkeypatch):
    """VAL-209: 点击安装 → 202；轮询 → succeeded + installed_sha256；同 SHA
    重复点击不产生第二次落地。"""
    samples = add_work_resources_samples(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    _bind_u1(client, samples)

    resp = _install(client, samples)
    assert resp.status_code == 202, resp.text
    op = resp.json()
    assert op["operation_id"] and op["status"] == "downloading"

    # 同 SHA 重复请求 → 同一进行中 operation
    again = _install(client, samples)
    assert again.status_code == 202
    assert again.json()["operation_id"] == op["operation_id"]

    _wait_for(
        lambda: client.get(
            f"/v1/enterprise/resources/operations/{op['operation_id']}"
        ).json().get("status") == "succeeded"
    )
    final = client.get(f"/v1/enterprise/resources/operations/{op['operation_id']}")
    assert final.json()["installed_sha256"] == samples.h1

    # 已安装同 SHA 再点 → 200 幂等，不重复落地
    done = _install(client, samples)
    assert done.status_code == 200, done.text
    assert done.json()["installed_sha256"] == samples.h1

    manager = client.app.state.manager
    record = installer.get_install(manager.enterprise_context(), "T1", "cat-c1")
    assert record is not None and record.enabled is True  # 首次安装启用

    # 浏览/刷新没有触发任何 release 流量
    fake_opc.assert_no_release_calls()


def test_install_version_conflict(fake_opc, tmp_path, monkeypatch):
    """VAL-210: 目录已更新到 h2，请求仍带 h1 → 409 SKILL_VERSION_CHANGED，
    不静默装 h2；刷新（取 h2）后用户再点成功。"""
    samples = add_work_resources_samples(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    _bind_u1(client, samples)

    first = _install(client, samples)
    assert first.status_code == 202
    _wait_for(
        lambda: client.get(
            f"/v1/enterprise/resources/operations/{first.json()['operation_id']}"
        ).json().get("status") == "succeeded"
    )
    # 平台侧 h1→h2，并清掉 authz 缓存里的旧版本
    fake_opc.add_skill_catalog(
        "T1", "cat-c1", "summarizer", samples.h2_bytes,
        description="T1 的总结技能", version="2.0.0",
    )
    from coworker.enterprise.resources import AUTHZ

    manager = client.app.state.manager
    AUTHZ.drop_context(manager.enterprise_context().key)

    conflict = _install(client, samples, sha=samples.h1)
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "SKILL_VERSION_CHANGED"

    ok = _install(client, samples, sha=samples.h2)
    assert ok.status_code in (200, 202)
    if ok.status_code == 202:
        _wait_for(
            lambda: client.get(
                f"/v1/enterprise/resources/operations/{ok.json()['operation_id']}"
            ).json().get("status") == "succeeded"
        )
    record = installer.get_install(manager.enterprise_context(), "T1", "cat-c1")
    assert record.active_sha256 == samples.h2


def test_operation_owner_scope(fake_opc, tmp_path, monkeypatch):
    """操作归属当前身份：他人/无会话不可枚举。"""
    samples = add_work_resources_samples(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    resp = _install(client, samples)
    op_id = resp.json()["operation_id"]
    other = _client(tmp_path / "other", monkeypatch, fake_opc.base_url)
    resp2 = other.get(f"/v1/enterprise/resources/operations/{op_id}")
    assert resp2.status_code == 401
    _wait_for(
        lambda: client.get(f"/v1/enterprise/resources/operations/{op_id}").json().get("status")
        in ("succeeded", "failed")
    )


def test_enable_disable_uninstall(fake_opc, tmp_path, monkeypatch):
    samples = add_work_resources_samples(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    resp = _install(client, samples)
    _wait_for(
        lambda: client.get(
            f"/v1/enterprise/resources/operations/{resp.json()['operation_id']}"
        ).json().get("status") == "succeeded"
    )
    cid = _context_id(client)
    off = client.patch(
        "/v1/enterprise/resources/skills/cat-c1",
        json={"tenant_id": "T1", "context_id": cid, "enabled": False},
    )
    assert off.status_code == 200 and off.json()["record"]["enabled"] is False
    on = client.patch(
        "/v1/enterprise/resources/skills/cat-c1",
        json={"tenant_id": "T1", "context_id": cid, "enabled": True},
    )
    assert on.status_code == 200 and on.json()["record"]["enabled"] is True
    manager = client.app.state.manager
    record = installer.get_install(manager.enterprise_context(), "T1", "cat-c1")
    assert record.enabled is True
    gone = client.delete(
        "/v1/enterprise/resources/skills/cat-c1?tenant_id=T1&context_id=" + cid
    )
    assert gone.status_code == 200
    assert installer.get_install(manager.enterprise_context(), "T1", "cat-c1") is None
    again = client.delete(
        "/v1/enterprise/resources/skills/cat-c1?tenant_id=T1&context_id=" + cid
    )
    assert again.status_code == 200  # 重复卸载视为成功


def test_stale_context_id_rejected(fake_opc, tmp_path, monkeypatch):
    """BND-004.5: 旧 context_id 的选择被拒绝（409 CONTEXT_CHANGED），不重绑。"""
    samples = add_work_resources_samples(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    bad = client.post(
        "/v1/enterprise/resources/skills/cat-c1/install",
        json={
            "tenant_id": "T1",
            "expected_sha256": samples.h1,
            "context_id": "0" * 32,
        },
    )
    assert bad.status_code == 409
    assert bad.json()["error"]["code"] == "CONTEXT_CHANGED"


def test_single_item_404_distinct(fake_opc, tmp_path, monkeypatch):
    """单资源不存在 = 404 RESOURCE_NOT_ACCESSIBLE（不是 501）。"""
    add_work_resources_samples(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    resp = client.get("/v1/enterprise/resources/skills/cat-missing?tenant_id=T1")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RESOURCE_NOT_ACCESSIBLE"


def test_unknown_protocol_maps_501(fake_opc, tmp_path, monkeypatch):
    """INT-105: 上游 resources 家族结构性 404 → 501 RESOURCE_API_UNSUPPORTED，
    与单资源 404 区分。"""
    samples = add_work_resources_samples(fake_opc)
    import asyncio
    import threading

    import uvicorn
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    async def missing(request):
        return PlainTextResponse("404 page not found", status_code=404)

    old = Starlette(routes=[Route("/integration/v1/resources/skills", missing, methods=["GET"])])
    config = uvicorn.Config(old, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=lambda: asyncio.run(server.serve()), daemon=True).start()
    deadline = time.time() + 5
    while not server.started:
        time.sleep(0.01)
        assert time.time() < deadline
    port = server.servers[0].sockets[0].getsockname()[1]

    from coworker.enterprise.auth import EnterpriseContext

    client = _client(tmp_path / "app", monkeypatch, f"http://127.0.0.1:{port}")
    ctx = EnterpriseContext(
        issuer="i", subject="s", tenant="T1", generation=1, authz_version=1,
        spaces=["T1"], access_token=samples.u1_token,
    )
    client.app.state.manager.enterprise_auth._context = ctx
    resp = client.get("/v1/enterprise/resources/skills")
    assert resp.status_code == 501, resp.text
    assert resp.json()["error"]["code"] == "RESOURCE_API_UNSUPPORTED"
    server.should_exit = True


def test_late_response_never_lands_on_new_identity(fake_opc, tmp_path, monkeypatch):
    """VAL-214: 安装下载期间切换身份 → 迟到结果不激活给新身份。"""
    samples = add_work_resources_samples(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    fake_opc.archive_delay_seconds["cat-c1"] = 0.8
    resp = _install(client, samples)
    op_id = resp.json()["operation_id"]
    # 下载进行中切身份
    fake_opc.tenant = "tenant-b"
    fake_opc.generation = 2
    _login(client, fake_opc)
    _wait_for(
        lambda: client.get(f"/v1/enterprise/resources/operations/{op_id}").status_code
        == 404
    )
    _wait_for(lambda: len(fake_opc.catalog_archive_calls) >= 1, timeout=5)
    time.sleep(1.2)  # 等迟到的下载分支走完
    manager = client.app.state.manager
    ctx = manager.enterprise_context()
    assert installer.get_install(ctx, "T1", "cat-c1") is None


# -- M5 / minor 回归 -----------------------------------------------------------------


def test_patch_conflicts_with_inflight_install(fake_opc, tmp_path, monkeypatch):
    """M5：进行中安装时 PATCH enabled → 409 OPERATION_IN_PROGRESS（与卸载同语义）。"""
    samples = add_work_resources_samples(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    _bind_u1(client, samples)
    fake_opc.archive_delay_seconds["cat-c1"] = 0.8
    resp = _install(client, samples)
    assert resp.status_code == 202
    cid = _context_id(client)
    patch = client.patch(
        "/v1/enterprise/resources/skills/cat-c1",
        json={"tenant_id": "T1", "context_id": cid, "enabled": False},
    )
    assert patch.status_code == 409, patch.text
    assert patch.json()["error"]["code"] == "OPERATION_IN_PROGRESS"
    _wait_for(
        lambda: client.get("/v1/enterprise/resources/skills").json()["items"][0][
            "local_status"
        ].startswith("installed")
    )


def test_install_error_mapping_session_revoked_and_upstream(fake_opc, tmp_path, monkeypatch):
    """Minor：401 SESSION_REVOKED / 502 UPSTREAM_UNAVAILABLE 按 INT-105 映射，不压成 404。"""
    from coworker.enterprise import resources as res

    samples = add_work_resources_samples(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    manager = client.app.state.manager
    ctx = manager.enterprise_context()
    ctx.access_token = samples.u1_token

    original = res.authorize_catalog

    async def denied_revoked(client_, context, tenant_id, catalog_id, sha=""):
        return False, {"error": {"code": "SESSION_REVOKED", "message": "revoked"}}

    async def denied_upstream(client_, context, tenant_id, catalog_id, sha=""):
        return False, {"error": {"code": "UPSTREAM_UNAVAILABLE", "message": "down"}}

    monkeypatch.setattr(res, "authorize_catalog", denied_revoked)
    resp = client.post(
        "/v1/enterprise/resources/skills/cat-c1/install",
        json={
            "tenant_id": "T1",
            "expected_sha256": samples.h1,
            "context_id": ctx.context_id,
        },
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["error"]["code"] == "SESSION_REVOKED"

    monkeypatch.setattr(res, "authorize_catalog", denied_upstream)
    resp2 = client.post(
        "/v1/enterprise/resources/skills/cat-c1/install",
        json={
            "tenant_id": "T1",
            "expected_sha256": samples.h1,
            "context_id": ctx.context_id,
        },
    )
    assert resp2.status_code == 502, resp2.text
    assert resp2.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"
