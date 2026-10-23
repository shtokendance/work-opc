"""WORK-RESOURCES-001 — turn knowledge scope + read allow-set (TASK-018, BND-005).

VAL-205/206/207 through the bridge against the fake platform, plus the PBT
property P1 (VAL-102, seed=20261022, ≥500 cases): for ARBITRARY
scope/model-params/document-ownership combinations, the set of documents the
tools may access is always a subset of the selected KB — and conflicting
model parameters produce SCOPE_MISMATCH with NO upstream request at all.
"""

from __future__ import annotations

import random

import httpx
import pytest

from coworker.enterprise.auth import EnterpriseContext
from coworker.enterprise.scope import TurnScope
from coworker.enterprise.tools import EnterpriseBridge
from coworker.inbox import InboxStore
from coworker.testing.fake_opc import FakeOpc, add_work_resources_samples


def _ctx(token: str, tenant: str = "T1", subject: str = "subject-u1") -> EnterpriseContext:
    return EnterpriseContext(
        issuer="https://opc.example.com",
        subject=subject,
        tenant=tenant,
        generation=1,
        authz_version=1,
        spaces=["T1", "T2"],
        access_token=token,
    )


@pytest.fixture
def fake_opc():
    fake = FakeOpc().start()
    try:
        yield fake
    finally:
        fake.stop()


def _bridge(ctx, base_url):
    return EnterpriseBridge(
        context_getter=lambda: ctx,
        platform_base_getter=lambda: base_url,
        prompt_router=lambda sid: {"inbox": "default", "visibility": "inbox"},
        inbox=InboxStore(),
        poll_interval=0.01,
    )


# -- VAL-205: scope enforcement ---------------------------------------------------


async def test_scope_search_fills_fixed_kb_and_records_hits(fake_opc):
    samples = add_work_resources_samples(fake_opc)
    ctx = _ctx(samples.u1_token)
    bridge = _bridge(ctx, fake_opc.base_url)
    scope = TurnScope(
        turn_id="t1", context_key=ctx.key, tenant_id="T1", knowledge_base_id="kb-a"
    )
    bridge.set_turn_scope("s1", scope)
    out = await bridge.knowledge_search("苹果", session_id="s1")
    assert out["results"][0]["knowledge_id"] == "kn-apple"
    # 服务端补的固定值，不是模型给的
    call = fake_opc.knowledge_search_calls[-1]
    assert call["tenant_id"] == "T1" and call["knowledge_base_id"] == "kb-a"
    assert scope.allows_read("kn-apple")


async def test_scope_conflict_params_refused_without_upstream(fake_opc):
    """VAL-205: 模型强行给别的 tenant/KB → SCOPE_MISMATCH 且无越界上游请求。"""
    samples = add_work_resources_samples(fake_opc)
    ctx = _ctx(samples.u1_token)
    bridge = _bridge(ctx, fake_opc.base_url)
    scope = TurnScope(
        turn_id="t1", context_key=ctx.key, tenant_id="T1", knowledge_base_id="kb-a"
    )
    bridge.set_turn_scope("s1", scope)
    calls_before = len(fake_opc.knowledge_search_calls)
    out = await bridge.knowledge_search(
        "香蕉", space="T2", knowledge_base_id="kb-b", session_id="s1"
    )
    assert not out["ok"] and out["error"]["code"] == "SCOPE_MISMATCH"
    assert len(fake_opc.knowledge_search_calls) == calls_before  # 没有发出请求


async def test_scope_read_requires_hit_and_double_checks_ownership(fake_opc):
    """VAL-205/207: read 只服务本 turn 命中；平台校验权威归属后才输出正文。"""
    samples = add_work_resources_samples(fake_opc)
    ctx = _ctx(samples.u1_token)
    bridge = _bridge(ctx, fake_opc.base_url)
    scope = TurnScope(
        turn_id="t1", context_key=ctx.key, tenant_id="T1", knowledge_base_id="kb-a"
    )
    bridge.set_turn_scope("s1", scope)

    # 不在命中集合 → 拒绝（即使文档真实存在且有权）
    blocked = await bridge.knowledge_read("kn-banana", session_id="s1")
    assert not blocked["ok"] and blocked["error"]["code"] == "SCOPE_MISMATCH"

    # 命中后读取：URL 携带 scope，正文输出
    scope.record_hits([{"knowledge_id": "kn-apple"}])
    doc = await bridge.knowledge_read("kn-apple", session_id="s1")
    assert doc["data"]["title"] == "苹果型号"

    # VAL-207: 文档移到 B 后，本 turn 再读 → 平台 SCOPE_MISMATCH，无越界正文
    fake_opc.move_knowledge("kn-apple", "T1", "kb-b")
    moved = await bridge.knowledge_read("kn-apple", session_id="s1")
    assert not moved["ok"] and moved["error"]["code"] == "SCOPE_MISMATCH"


async def test_scope_released_and_no_cross_turn_leak(fake_opc):
    """VAL-206: 结束释放；下一 turn/会话不继承命中集合。"""
    samples = add_work_resources_samples(fake_opc)
    ctx = _ctx(samples.u1_token)
    bridge = _bridge(ctx, fake_opc.base_url)
    scope = TurnScope(
        turn_id="t1", context_key=ctx.key, tenant_id="T1", knowledge_base_id="kb-a"
    )
    bridge.set_turn_scope("s1", scope)
    scope.record_hits([{"knowledge_id": "kn-apple"}])
    bridge.clear_turn_scope("s1")
    # 清掉后 read 不再带 scope 参数（缺省行为），且不再受限本 turn 命中
    doc = await bridge.knowledge_read("kn-apple", session_id="s1")
    assert doc.get("data"), doc
    # 另一个会话永远没有 s1 的 scope
    assert bridge.turn_scope("s2") is None


async def test_scope_invalidated_on_identity_switch(fake_opc):
    samples = add_work_resources_samples(fake_opc)
    ctx = _ctx(samples.u1_token)
    bridge = _bridge(ctx, fake_opc.base_url)
    scope = TurnScope(
        turn_id="t1", context_key=ctx.key, tenant_id="T1", knowledge_base_id="kb-a"
    )
    bridge.set_turn_scope("s1", scope)
    assert bridge.turn_scope("s1") is scope
    # 模拟身份切换：换一个 key 的上下文
    ctx2 = _ctx(samples.u2_token, subject="subject-u2")
    bridge._context_getter = lambda: ctx2
    assert bridge.turn_scope("s1") is None  # 立即失效，不跨身份


# -- PBT P1 (VAL-102): allowed-set ⊆ selected KB, arbitrary inputs -----------------


def _p1_case(rng: random.Random) -> dict:
    tenants = ["T1", "T2"]  # U1 的已授权空间；T3 无权，scope 也不会绑定到它
    kbs = {"T1": ["kb-a", "kb-b"], "T2": ["kb-a2"]}
    tenant = rng.choice(tenants)
    kb = rng.choice(kbs[tenant])
    doc_tenant = rng.choice(tenants)
    doc_kb = rng.choice(kbs[doc_tenant])
    model_space = rng.choice([None, tenant, rng.choice(tenants)])
    model_kb = rng.choice([None, kb, rng.choice(kbs[tenant] + ["kb-zzz"])])
    return {
        "tenant": tenant, "kb": kb,
        "doc": (doc_tenant, doc_kb),
        "model_space": model_space, "model_kb": model_kb,
    }


def test_p1_scope_never_widens_seed_20261022():
    """P1: 任意 scope/模型参数/文档归属组合下，工具可访问集合始终是所选
    KB 的子集；冲突参数拒绝且无上游请求。seed=20261022, 600 cases。"""
    seed, cases = 20261022, 600
    rng = random.Random(seed)
    fake = FakeOpc().start()
    try:
        samples = add_work_resources_samples(fake)
        # 随机文档池：覆盖跨空间同名/移动后归属
        docs = {}
        for i in range(8):
            case = _p1_case(rng)
            kid = f"kn-r{i}"
            fake.add_scoped_document(
                kid, case["doc"][0], case["doc"][1], title=f"t{i}", content=f"c{i}"
            )
            docs[kid] = case["doc"]

        ctx = _ctx(samples.u1_token)
        bridge = _bridge(ctx, fake.base_url)
        mismatches = 0
        for i in range(cases):
            case = _p1_case(rng)
            scope = TurnScope(
                turn_id=f"turn-{i}", context_key=ctx.key,
                tenant_id=case["tenant"], knowledge_base_id=case["kb"],
            )
            bridge.set_turn_scope("s1", scope)
            calls_before = len(fake.knowledge_search_calls)

            out = bridge_knowledge_search_sync(
                bridge, "q", case["model_space"], case["model_kb"]
            )
            conflict = (
                (case["model_space"] is not None and case["model_space"] != case["tenant"])
                or (case["model_kb"] is not None and case["model_kb"] != case["kb"])
            )
            if conflict:
                # 必须拒绝，且绝不能发出越界上游请求
                assert not out["ok"], f"case {i}: conflict not refused: {case}"
                assert out["error"]["code"] == "SCOPE_MISMATCH", f"case {i}: {out}"
                mismatches += 1
            else:
                assert "results" in out, f"case {i}: {out}"
                # 命中全部属于所选 KB，且服务端确实带了固定参数
                call = fake.knowledge_search_calls[-1]
                assert call["tenant_id"] == case["tenant"], f"case {i}: {call}"
                assert call["knowledge_base_id"] == case["kb"], f"case {i}: {call}"
                for hit in out["results"]:
                    owner = docs.get(hit["knowledge_id"])
                    if owner is not None:
                        assert owner == (case["tenant"], case["kb"]), (
                            f"case {i}: hit {hit['knowledge_id']} owner {owner} "
                            f"outside scope {case}"
                        )
                # read：命中集合之外的 ID 必须拒绝
                outsider = next(
                    (k for k, o in docs.items() if o != (case["tenant"], case["kb"])),
                    None,
                )
                if outsider is not None:
                    denied = bridge_knowledge_read_sync(bridge, outsider)
                    assert not denied["ok"] and denied["error"]["code"] == "SCOPE_MISMATCH", (
                        f"case {i}: outsider read allowed: {denied}"
                    )
            bridge.clear_turn_scope("s1")
        assert mismatches > 0, "P1 generator never produced a conflict case"
        print(f"P1 seed={seed} cases={cases} conflicts={mismatches}")
    finally:
        fake.stop()


def bridge_knowledge_search_sync(bridge, query, space, kb):
    import asyncio

    return asyncio.run(bridge.knowledge_search(query, space, kb, session_id="s1"))


def bridge_knowledge_read_sync(bridge, kid):
    import asyncio

    return asyncio.run(bridge.knowledge_read(kid, session_id="s1"))


def test_scope_object_rejects_unknown_fields():
    """Minor：knowledge_scope 字段固定（DATA-102），未知字段一律拒绝。"""
    from coworker.enterprise.scope import validate_scope_object

    ok = validate_scope_object(
        {"tenant_id": "T1", "knowledge_base_id": "kb-a", "context_id": "c"}
    )
    assert ok is None
    bad = validate_scope_object(
        {
            "tenant_id": "T1",
            "knowledge_base_id": "kb-a",
            "context_id": "c",
            "space": "T1",
        }
    )
    assert bad is not None and bad["error"]["code"] == "INVALID_REQUEST"
    assert "space" in bad["error"]["message"]
