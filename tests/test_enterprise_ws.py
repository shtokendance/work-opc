"""WORK-RESOURCES-001 — WS acceptance, drafts, JSONL metadata (TASK-019).

VAL-206/212/214/219 over the real WebSocket: knowledge_scope + skill_ref ride
the user_message frame; rejections carry an INT-105 code and keep the draft;
acceptance persists the server-generated labels on the JSONL user message;
history without fields renders as before; and the provider projection strips
every internal field.
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
    start = resp.json()
    code = fake.approve_login(start["login_txn_id"])
    state = fake.authorize_requests[start["login_txn_id"]]["state"]
    client.get(f"/v1/enterprise/oauth/callback?code={code}&state={state}")
    result = client.get(f"/v1/enterprise/login/result?login_txn_id={start['login_txn_id']}")
    return result.json()["refresh_token"]


def _ws(client: TestClient, session_id: str, ws_tmp):
    ws = client.websocket_connect(f"/ws/session/{session_id}?workspace={ws_tmp}")
    ws.__enter__()
    ws.receive_json()  # ready
    return ws


def _context_id(client: TestClient) -> str:
    return client.get("/v1/enterprise/session").json()["context_id"]


def _bind_u1(client: TestClient, samples) -> None:
    """The login token is the fake's default identity (no T1/T2 entitlement);
    minted U1 token carries T1+T2 so resource authorization sees the samples."""
    ctx = client.app.state.manager.enterprise_context()
    ctx.access_token = samples.u1_token


def _install_skill(client, samples) -> None:
    cid = _context_id(client)
    resp = client.post(
        "/v1/enterprise/resources/skills/cat-c1/install",
        json={
            "tenant_id": "T1",
            "expected_sha256": samples.h1,
            "context_id": cid,
            "name": "summarizer",
        },
    )
    assert resp.status_code in (200, 202), resp.text
    if resp.status_code == 202:
        op = resp.json()["operation_id"]
        deadline = time.time() + 10
        while time.time() < deadline:
            view = client.get(f"/v1/enterprise/resources/operations/{op}").json()
            if view.get("status") == "succeeded":
                return
            time.sleep(0.02)
        raise AssertionError("install did not succeed")


def test_ws_scope_and_skill_ref_accepted_and_persisted(fake_opc, tmp_path, monkeypatch):
    """VAL-206/212: scope+skill_ref 被接收、turn 启动、JSONL 持久化服务端标签；
    provider 投影不含任何内部字段。"""
    samples = add_work_resources_samples(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    _bind_u1(client, samples)
    _install_skill(client, samples)

    ws_tmp = tmp_path / "ws"
    ws_tmp.mkdir()
    ws = _ws(client, "sess-1", str(ws_tmp))
    cid = _context_id(client)
    ws.send_json(
        {
            "type": "user_message",
            "text": "整理苹果要点",
            "knowledge_scope": {
                "tenant_id": "T1",
                "knowledge_base_id": "kb-a",
                "context_id": cid,
            },
            "skill_ref": {
                "source": "catalog",
                "tenant_id": "T1",
                "catalog_id": "cat-c1",
                "bundle_sha256": samples.h1,
                "context_id": cid,
            },
        }
    )
    seen_turn_start = False
    while True:
        event = ws.receive_json()
        if event["type"] == "turn_start":
            seen_turn_start = True
            break
        if event["type"] == "input_rejected":
            raise AssertionError(f"message rejected: {event}")
    assert seen_turn_start

    manager = client.app.state.manager
    engine = manager._engines.get("sess-1") or manager.get_engine(
        "sess-1", workspace=str(ws_tmp)
    )
    user_msgs = [m for m in engine.messages if m.get("role") == "user"]
    assert user_msgs, "no user message persisted"
    msg = user_msgs[-1]
    meta = msg.get("_resources")
    assert meta is not None, f"missing _resources sidecar: {msg.keys()}"
    assert meta["labels"]["knowledge_scope"]["knowledge_base_id"] == "kb-a"
    assert meta["labels"]["knowledge_scope"]["knowledge_base_name"] == "A"
    assert meta["labels"]["skill_ref"]["bundle_sha256"] == samples.h1
    assert meta["turn_id"]
    # M4：_display 只保留用户正文；标签由 ResourceChips 从 _resources 渲染
    display = msg.get("_display")
    assert display is None or "知识库" not in str(display)
    assert display is None or "整理苹果要点" in str(display)
    # provider 投影：内部字段全部剥离
    outbound = engine._outbound_messages()
    for m in outbound:
        assert "_resources" not in m
        assert "_display" not in m
        assert "source" not in m
    ws.close()


def test_ws_rejects_bad_scope_and_keeps_draft(fake_opc, tmp_path, monkeypatch):
    """BND-004.4: 校验失败拒绝该消息（带 INT-105 码、keep_draft），不启动 turn。"""
    samples = add_work_resources_samples(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    ws_tmp = tmp_path / "ws"
    ws_tmp.mkdir()
    ws = _ws(client, "sess-1", str(ws_tmp))

    # 缺字段
    ws.send_json(
        {
            "type": "user_message",
            "text": "草稿保留",
            "knowledge_scope": {"tenant_id": "T1"},
        }
    )
    event = ws.receive_json()
    assert event["type"] == "input_rejected"
    assert event["data"]["code"] == "INVALID_REQUEST"
    assert event["data"]["keep_draft"] is True

    # 错误 context_id（旧身份）
    ws.send_json(
        {
            "type": "user_message",
            "text": "草稿保留",
            "knowledge_scope": {
                "tenant_id": "T1",
                "knowledge_base_id": "kb-a",
                "context_id": "0" * 32,
            },
        }
    )
    event = ws.receive_json()
    assert event["type"] == "input_rejected"
    assert event["data"]["code"] == "CONTEXT_CHANGED"

    # 未安装的 skill_ref
    ws.send_json(
        {
            "type": "user_message",
            "text": "草稿保留",
            "skill_ref": {
                "source": "catalog",
                "tenant_id": "T1",
                "catalog_id": "cat-missing",
                "bundle_sha256": samples.h1,
                "context_id": _context_id(client),
            },
        }
    )
    event = ws.receive_json()
    assert event["type"] == "input_rejected"
    assert event["data"]["code"] == "SKILL_NOT_AVAILABLE"

    # 互斥：个人 skill + 企业 skill_ref
    ws.send_json(
        {
            "type": "user_message",
            "text": "x",
            "skill": "some-skill",
            "skill_ref": {
                "source": "catalog",
                "tenant_id": "T1",
                "catalog_id": "cat-c1",
                "bundle_sha256": samples.h1,
                "context_id": _context_id(client),
            },
        }
    )
    event = ws.receive_json()
    assert event["type"] == "input_rejected"
    assert event["data"]["code"] == "INVALID_REQUEST"

    # turn 从未启动
    assert not client.app.state.manager.is_running("sess-1")
    ws.close()


def test_ws_scope_requires_signin(fake_opc, tmp_path, monkeypatch):
    """未登录时企业选择被拒绝（SESSION_REVOKED），不能静默走个人路径。"""
    add_work_resources_samples(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    ws_tmp = tmp_path / "ws"
    ws_tmp.mkdir()
    ws = _ws(client, "sess-1", str(ws_tmp))
    ws.send_json(
        {
            "type": "user_message",
            "text": "hi",
            "knowledge_scope": {
                "tenant_id": "T1",
                "knowledge_base_id": "kb-a",
                "context_id": "0" * 32,
            },
        }
    )
    event = ws.receive_json()
    assert event["type"] == "input_rejected"
    assert event["data"]["code"] == "SESSION_REVOKED"
    ws.close()


def test_history_without_fields_untouched(fake_opc, tmp_path, monkeypatch):
    """VAL-219 兼容性：无 _resources 字段的历史消息按原样存在与投影。"""
    add_work_resources_samples(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    ws_tmp = tmp_path / "ws"
    ws_tmp.mkdir()
    ws = _ws(client, "sess-1", str(ws_tmp))
    ws.send_json({"type": "user_message", "text": "普通消息"})
    while True:
        event = ws.receive_json()
        if event["type"] == "turn_start":
            break
        if event["type"] == "input_rejected":
            raise AssertionError(event)
    manager = client.app.state.manager
    engine = manager._engines["sess-1"]
    user_msg = [m for m in engine.messages if m.get("role") == "user"][-1]
    assert "_resources" not in user_msg
    # 历史加载：保存+重新加载后仍无字段
    manager.save("sess-1", engine)
    record = manager.session_store.load("sess-1")
    loaded = [m for m in record.messages if m.get("role") == "user"][-1]
    assert "_resources" not in loaded
    ws.close()


# -- B2 / M3 / M4 回归 ----------------------------------------------------------------


class _Gate:
    """让被桩掉的 engine.run/retry 在 turn_start 后挂起，测试可在 turn 存活
    期间断言 turn 内状态（bridge scope/ref/pins），再放行结束。挂起用
    threading.Event（fake_run 在 TestClient portal 的线程事件循环里跑，
    set() 从测试主线程触发——threading.Event 跨线程安全）。"""

    def __init__(self):
        import threading

        self._release = threading.Event()

    def set(self) -> None:
        self._release.set()

    def wrap(self):
        import asyncio
        import threading

        from coworker.engine import Event, EventType

        gate = self

        async def fake_run(self_engine, user_input, **kwargs):
            # 镜像 engine.run 的持久化副作用：用户消息 + sidecar 落 messages，
            # 使 M4/重试恢复（_recover_turn_meta）可测。
            import time as _time

            message = {"role": "user", "content": user_input, "ts": _time.time()}
            if kwargs.get("display") is not None:
                message["_display"] = kwargs["display"]
            if kwargs.get("resources"):
                message["_resources"] = kwargs["resources"]
            self_engine.messages.append(message)
            yield Event(EventType.TURN_START, {"input": user_input})
            # turn_done 由 run_turn 的 finally 广播（真实路径没有 TURN_DONE 事件）。
            await asyncio.to_thread(gate._release.wait)

        return fake_run


def _install_skill(client, samples) -> None:  # noqa: F811 - 覆盖上方同名辅助
    cid = _context_id(client)
    resp = client.post(
        "/v1/enterprise/resources/skills/cat-c1/install",
        json={
            "tenant_id": "T1",
            "expected_sha256": samples.h1,
            "context_id": cid,
            "name": "summarizer",
        },
    )
    assert resp.status_code in (200, 202), resp.text
    if resp.status_code == 202:
        op = resp.json()["operation_id"]
        deadline = time.time() + 10
        while time.time() < deadline:
            view = client.get(f"/v1/enterprise/resources/operations/{op}").json()
            if view.get("status") == "succeeded":
                return
            time.sleep(0.02)
        raise AssertionError("install did not succeed")


def test_b2_enterprise_load_skill_succeeds_inside_accepted_turn(
    fake_opc, tmp_path, monkeypatch
):
    """B2 端到端：WS 接收 scope+skill_ref → turn 内 enterprise_load_skill
    真实成功（授权缓存键修复前该路径必得 SKILL_REVOKED）。"""
    samples = add_work_resources_samples(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    _bind_u1(client, samples)
    _install_skill(client, samples)

    import asyncio

    from coworker.engine import TurnEngine

    # turn_start 会触发自动标题（真实 provider 调用）——测试中禁用，保持门控确定性。
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, sid: None)
    gate = _Gate()
    monkeypatch.setattr(TurnEngine, "run", gate.wrap())

    ws_tmp = tmp_path / "ws"
    ws_tmp.mkdir()
    ws = _ws(client, "sess-1", str(ws_tmp))
    cid = _context_id(client)
    ws.send_json(
        {
            "type": "user_message",
            "text": "整理卖点",
            "skill_ref": {
                "source": "catalog",
                "tenant_id": "T1",
                "catalog_id": "cat-c1",
                "bundle_sha256": samples.h1,
                "context_id": cid,
            },
        }
    )
    event = ws.receive_json()
    assert event["type"] == "turn_start", event

    manager = client.app.state.manager
    bridge = manager._get_enterprise_bridge()

    async def _load():
        return await asyncio.to_thread(bridge.load_enterprise_skill, "sess-1")

    out = asyncio.run(_load())
    assert out.get("ok") is True, out
    assert "总结给定文本" in out["instructions"]

    gate.set()
    while True:
        done = ws.receive_json()
        if done["type"] == "turn_done":
            break
    # turn 结束后引用已释放（M6）：registry 中无残留 pin
    from coworker.enterprise.registry import REGISTRY

    assert REGISTRY.ref_counts() == {}
    ws.close()


def test_m3_retry_restores_scope_and_pinned_skill_sha(fake_opc, tmp_path, monkeypatch):
    """M3：provider 错误后的 retry 恢复原消息的范围与所选技能 SHA。"""
    samples = add_work_resources_samples(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    _bind_u1(client, samples)
    _install_skill(client, samples)

    import asyncio

    from coworker.engine import TurnEngine

    # turn_start 会触发自动标题（真实 provider 调用）——测试中禁用，保持门控确定性。
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, sid: None)
    gate = _Gate()
    monkeypatch.setattr(TurnEngine, "run", gate.wrap())

    ws_tmp = tmp_path / "ws"
    ws_tmp.mkdir()
    ws = _ws(client, "sess-1", str(ws_tmp))
    cid = _context_id(client)
    ws.send_json(
        {
            "type": "user_message",
            "text": "整理卖点",
            "knowledge_scope": {
                "tenant_id": "T1",
                "knowledge_base_id": "kb-a",
                "context_id": cid,
            },
            "skill_ref": {
                "source": "catalog",
                "tenant_id": "T1",
                "catalog_id": "cat-c1",
                "bundle_sha256": samples.h1,
                "context_id": cid,
            },
        }
    )
    assert ws.receive_json()["type"] == "turn_start"
    gate.set()
    while ws.receive_json()["type"] != "turn_done":
        pass

    manager = client.app.state.manager
    bridge = manager._get_enterprise_bridge()

    # 让 retry 守卫通过：历史尾部需要一条 error notice（真实 retry 语义）。
    engine = manager._engines["sess-1"]
    engine._append_notice("error", "provider exploded")

    # turn_start 会触发自动标题（真实 provider 调用）——测试中禁用，保持门控确定性。
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, sid: None)
    gate2 = _Gate()

    async def blocking_loop(self_engine):
        import asyncio as _asyncio

        if False:
            yield  # async gen 协议（不能产出 None 事件）
        await _asyncio.to_thread(gate2._release.wait)

    async def noop_sandbox(self_engine):
        if False:
            yield

    monkeypatch.setattr(TurnEngine, "_loop", blocking_loop)
    monkeypatch.setattr(TurnEngine, "_start_sandbox", noop_sandbox)
    ws.send_json({"type": "retry"})
    assert ws.receive_json()["type"] == "turn_start"
    # M3 retry 恢复：scope 与 ref 重新绑定到本 turn
    assert bridge.turn_scope("sess-1") is not None
    assert bridge.turn_scope("sess-1").knowledge_base_id == "kb-a"
    assert bridge._turn_refs["sess-1"].bundle_sha256 == samples.h1
    gate2.set()
    while ws.receive_json()["type"] != "turn_done":
        pass
    ws.close()


def test_m4_display_keeps_user_text_without_label_leak(fake_opc, tmp_path, monkeypatch):
    """M4：display 只含用户正文——资源标签只存 `_resources` 由 ResourceChips
    渲染，刷新后气泡完整且不与服务端硬编码文案重复。"""
    samples = add_work_resources_samples(fake_opc)
    client = _client(tmp_path, monkeypatch, fake_opc.base_url)
    _login(client, fake_opc)
    _bind_u1(client, samples)
    _install_skill(client, samples)

    import asyncio

    from coworker.engine import TurnEngine

    # turn_start 会触发自动标题（真实 provider 调用）——测试中禁用，保持门控确定性。
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, sid: None)
    gate = _Gate()
    monkeypatch.setattr(TurnEngine, "run", gate.wrap())

    ws_tmp = tmp_path / "ws"
    ws_tmp.mkdir()
    ws = _ws(client, "sess-1", str(ws_tmp))
    cid = _context_id(client)
    ws.send_json(
        {
            "type": "user_message",
            "text": "整理卖点",
            "knowledge_scope": {
                "tenant_id": "T1",
                "knowledge_base_id": "kb-a",
                "context_id": cid,
            },
        }
    )
    assert ws.receive_json()["type"] == "turn_start"
    gate.set()
    while ws.receive_json()["type"] != "turn_done":
        pass

    manager = client.app.state.manager
    engine = manager._engines["sess-1"]
    user_msg = [m for m in engine.messages if m.get("role") == "user"][-1]
    display = user_msg.get("_display")
    assert display is None or "知识库" not in str(display)
    assert display is None or "整理卖点" in str(display) or display == ""
    # 标签仍在 _resources（ResourceChips 数据源）
    assert user_msg["_resources"]["labels"]["knowledge_scope"]["knowledge_base_id"] == "kb-a"
    ws.close()
