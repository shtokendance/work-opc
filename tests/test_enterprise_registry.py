"""WORK-RESOURCES-001 — stable registry + PBT P2 (TASK-017).

VAL-212/213 invariants and the VAL-102 property P2 (seed=20261023, ≥500
cases): for ARBITRARY subject/tenant/catalog/SHA/same-name combinations the
registry + active pointer never cross subjects and never pick the wrong
object; updates only affect new turns; disable/uninstall forbid new starts
while pinned running references stay intact until released.
"""

from __future__ import annotations

import random

import pytest

from coworker.enterprise import installer
from coworker.enterprise.auth import EnterpriseContext
from coworker.enterprise.registry import (
    REGISTRY,
    SkillRef,
    SkillRefError,
    SKILL_NOT_AVAILABLE,
    SKILL_VERSION_CHANGED,
)
from coworker.testing.fake_opc import build_skill_package, sha256_hex


def _ctx(subject: str, issuer: str = "https://opc.example.com", generation: int = 1):
    return EnterpriseContext(
        issuer=issuer,
        subject=subject,
        tenant="T1",
        generation=generation,
        authz_version=1,
        spaces=[],
        access_token="tok-" + subject,
    )


@pytest.fixture(autouse=True)
def _registry_cleanup():
    yield
    REGISTRY._refs.clear()


def _install(ctx, tenant, catalog, body, name="summarizer"):
    blob = build_skill_package(body, root=name, frontmatter_name=name)
    sha = sha256_hex(blob)
    out = installer.install(ctx, tenant, catalog, blob, expected_sha256=sha, name=name)
    assert out["ok"], out
    return sha


# -- VAL-212/213 --------------------------------------------------------------------


def test_same_name_two_subjects_and_spaces_resolve_independently(tmp_path, monkeypatch):
    """个人/C1/C2 同名：主体/空间目录隔离，各自加载准确内容。"""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    u1, u2 = _ctx("subject-u1"), _ctx("subject-u2")
    c1 = _install(u1, "T1", "cat-c1", "C1 body\n")
    c2 = _install(u1, "T2", "cat-c2", "C2 body\n")
    also_c1 = _install(u2, "T1", "cat-c1", "C1 for u2\n")

    r1 = REGISTRY.resolve(u1, SkillRef("T1", "cat-c1", c1))
    assert "C1 body" in r1["instructions"]
    r2 = REGISTRY.resolve(u1, SkillRef("T2", "cat-c2", c2))
    assert "C2 body" in r2["instructions"]
    # 同目录同 SHA 另一主体 → 自己的内容，绝不串用
    r3 = REGISTRY.resolve(u2, SkillRef("T1", "cat-c1", also_c1))
    assert "C1 for u2" in r3["instructions"]
    assert "C1 body" not in r3["instructions"]
    # u1 的 pinned ref 在 u2 上下文里解析不到（身份分根）
    with pytest.raises(SkillRefError):
        REGISTRY.resolve(u2, SkillRef("T1", "cat-c1", c1))


def test_turn_pins_sha_updates_only_affect_new_turns(tmp_path, monkeypatch):
    """VAL-213: 运行 turn 固定 h1；更新 h2 后旧 ref 仍解析旧版本内容。"""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    ctx = _ctx("subject-u1")
    h1 = _install(ctx, "T1", "cat-c1", "version ONE\n")
    ref1 = SkillRef("T1", "cat-c1", h1)
    REGISTRY.acquire(ctx, ref1, session_id="sess-run")
    # 用户更新到 h2
    h2 = _install(ctx, "T1", "cat-c1", "version TWO\n")
    # 运行中的 turn 仍解析 h1
    pinned = REGISTRY.resolve(ctx, ref1)
    assert "version ONE" in pinned["instructions"]
    # 新 turn 用新 active
    ref2 = SkillRef("T1", "cat-c1", h2)
    fresh = REGISTRY.resolve(ctx, ref2)
    assert "version TWO" in fresh["instructions"]
    REGISTRY.release(ctx, "T1", "cat-c1", h1)


def test_disable_and_uninstall_forbid_new_starts(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    ctx = _ctx("subject-u1")
    h1 = _install(ctx, "T1", "cat-c1", "body\n")
    ref = SkillRef("T1", "cat-c1", h1)
    # 停用 → 新启动拒绝
    installer.set_enabled(ctx, "T1", "cat-c1", False)
    with pytest.raises(SkillRefError) as exc:
        REGISTRY.resolve(ctx, ref)
    assert exc.value.code == SKILL_NOT_AVAILABLE
    installer.set_enabled(ctx, "T1", "cat-c1", True)
    # 卸载 → 新启动拒绝；引用结束后回收由 pinned_versions 暴露
    REGISTRY.acquire(ctx, ref, session_id="sess-a")
    installer.uninstall(ctx, "T1", "cat-c1")
    with pytest.raises(SkillRefError):
        REGISTRY.resolve(ctx, ref)
    assert REGISTRY.pinned_versions(ctx, "T1", "cat-c1") == {h1}
    assert REGISTRY.release(ctx, "T1", "cat-c1", h1) is True
    assert REGISTRY.pinned_versions(ctx, "T1", "cat-c1") == set()


def test_revocation_beats_pinning(tmp_path, monkeypatch):
    """BND-001: authorize 回调拒绝 → 下一次 load 必须失败（即使版本仍完整）。"""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    ctx = _ctx("subject-u1")
    h1 = _install(ctx, "T1", "cat-c1", "body\n")
    ref = SkillRef("T1", "cat-c1", h1)
    allow = {"ok": True}
    auth = lambda c, t, cat, sha: allow["ok"]
    assert REGISTRY.resolve(ctx, ref, authorize=auth)["bundle_sha256"] == h1
    allow["ok"] = False  # 撤权
    with pytest.raises(SkillRefError) as exc:
        REGISTRY.resolve(ctx, ref, authorize=auth)
    assert exc.value.code == "SKILL_REVOKED"


# -- PBT P2 (VAL-102, seed=20261023) -------------------------------------------------


def test_p2_registry_never_crosses_subjects_seed_20261023(tmp_path, monkeypatch):
    """P2: 任意 主体/tenant/catalog/SHA/同名 组合：registry 解析与 active
    不跨主体、不误选对象。seed=20261023, 600 cases。"""
    seed, cases = 20261023, 600
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    rng = random.Random(seed)
    subjects = [f"sub-{s}" for s in range(4)]
    ctxs = {s: _ctx(s) for s in subjects}
    bodies: dict[tuple[str, str, str], tuple[str, str]] = {}  # (subj,tenant,catalog) -> (sha, marker)

    # 随机预装：同名目录跨主体/空间
    for _ in range(30):
        subj = rng.choice(subjects)
        tenant = rng.choice(["T1", "T2"])
        catalog = rng.choice(["cat-c1", "cat-c2", "cat-c3"])  # 固定 catalog 池制造同名
        marker = f"body-{subj}-{tenant}-{catalog}-{rng.randrange(1000)}"
        blob = build_skill_package(marker + "\n", root="summarizer", frontmatter_name="summarizer")
        sha = sha256_hex(blob)
        out = installer.install(ctxs[subj], tenant, catalog, blob, expected_sha256=sha)
        assert out["ok"]
        bodies[(subj, tenant, catalog)] = (sha, marker)

    name_collisions = 0
    for i in range(cases):
        subj = rng.choice(subjects)
        tenant = rng.choice(["T1", "T2"])
        catalog = rng.choice(["cat-c1", "cat-c2", "cat-c3"])
        ctx = ctxs[subj]
        installed = bodies.get((subj, tenant, catalog))
        if installed is None:
            # 未安装：必须 NOT_AVAILABLE
            sha, _ = bodies.get((subjects[0], tenant, catalog), ("0" * 64, ""))
            with pytest.raises(SkillRefError) as exc:
                REGISTRY.resolve(ctx, SkillRef(tenant, catalog, installed_sha_or(shas_fallback)))
            assert exc.value.code == SKILL_NOT_AVAILABLE, f"case {i}"
            continue
        sha, marker = installed
        resolved = REGISTRY.resolve(ctx, SkillRef(tenant, catalog, sha))
        assert marker in resolved["instructions"], f"case {i}: wrong object"
        assert "summarizer" == resolved["name"]  # 同名技能，内容来自正确主体/空间
        # 同名计数：另一主体同 tenant/catalog 内容不同（若存在）
        for other in subjects:
            if other == subj:
                continue
            alt = bodies.get((other, tenant, catalog))
            if alt is not None:
                name_collisions += 1
                other_resolved = REGISTRY.resolve(ctxs[other], SkillRef(tenant, catalog, alt[0]))
                assert alt[1] in other_resolved["instructions"]
                assert marker not in other_resolved["instructions"], (
                    f"case {i}: subject bleed {subj} <- {other}"
                )
        # 错误 SHA → VERSION_CHANGED，绝不静默换版本
        bogus = sha256_hex(f"bogus-{i}".encode())
        if bogus != sha:
            with pytest.raises(SkillRefError) as exc:
                REGISTRY.resolve(ctx, SkillRef(tenant, catalog, bogus))
            assert exc.value.code == SKILL_VERSION_CHANGED, f"case {i}"
    print(f"P2 seed={seed} cases={cases} same_name_pairs={name_collisions}")


def installed_sha_or(value):
    return value


def shas_fallback():
    return "0" * 64


# -- B2 / M6 / minor 回归 ----------------------------------------------------------


def test_resolve_authorize_uses_ref_sha_cache_hits(tmp_path, monkeypatch):
    """B2：授权缓存以复核时看到的具体 current_sha 落键；registry 门以
    ref.bundle_sha256 查询——修复后精确 miss 回退通配命中，生产
    enterprise_load_skill 不再永远 SKILL_REVOKED。"""
    from coworker.enterprise import resources as res

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    ctx = _ctx("subject-u1")
    h1 = _install(ctx, "T1", "cat-c1", "C1 body\n")
    ref = SkillRef("T1", "cat-c1", h1)

    # 模拟一次成功的目录复核（authorize_catalog 的落键行为：具体版本 + 通配）
    res.AUTHZ.record(ctx, "T1", "cat-c1", True, h1)
    res.AUTHZ.record(ctx, "T1", "cat-c1", True, "")

    def gate(c, t, cat, sha):
        # manager.authorize_ref_cached 的同构门：只看缓存（失败关闭）
        return res.AUTHZ.check(c, t, cat, sha)

    # 修复前：check(..., h1) 对通配/精确键永不命中 → 第二次 resolve 即 REVOKED
    assert REGISTRY.resolve(ctx, ref, authorize=gate)["bundle_sha256"] == h1
    assert REGISTRY.resolve(ctx, ref, authorize=gate)["bundle_sha256"] == h1
    # 缓存被否定后（撤权），同一门必须失败关闭
    res.AUTHZ.drop_context(ctx.key)
    res.AUTHZ.record(ctx, "T1", "cat-c1", False, "")
    with pytest.raises(SkillRefError) as exc:
        REGISTRY.resolve(ctx, ref, authorize=gate)
    assert exc.value.code == "SKILL_REVOKED"


def test_pinned_turn_survives_disable_minor(tmp_path, monkeypatch):
    """Minor：停用≠撤权——运行中 turn 钉住的版本在停用后仍可完成当前版本；
    新启动（active 分支）被拒。"""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    ctx = _ctx("subject-u1")
    h1 = _install(ctx, "T1", "cat-c1", "version ONE\n")
    ref1 = SkillRef("T1", "cat-c1", h1)
    REGISTRY.acquire(ctx, ref1, session_id="sess-run")
    h2 = _install(ctx, "T1", "cat-c1", "version TWO\n")
    installer.set_enabled(ctx, "T1", "cat-c1", False)
    # 钉住版本：仍可 load（当前版本完成）
    pinned = REGISTRY.resolve(ctx, ref1)
    assert "version ONE" in pinned["instructions"]
    # 新 active 启动：停用拒绝
    with pytest.raises(SkillRefError) as exc:
        REGISTRY.resolve(ctx, SkillRef("T1", "cat-c1", h2))
    assert exc.value.code == SKILL_NOT_AVAILABLE
    REGISTRY.release(ctx, "T1", "cat-c1", h1)


def test_uninstall_refuses_while_pinned_and_allows_after_release(tmp_path, monkeypatch):
    """M6：卸载前引用非空必须拒绝；turn 结束 release 后允许卸载且目录消失。"""
    from coworker.enterprise import resources as res

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    ctx = _ctx("subject-u1")
    h1 = _install(ctx, "T1", "cat-c1", "body\n")
    ref = SkillRef("T1", "cat-c1", h1)
    REGISTRY.acquire(ctx, ref, session_id="sess-a")
    # 引用未归零：卸载拒绝
    base = installer.catalog_dir(ctx, "T1", "cat-c1")
    assert REGISTRY.pinned_versions(ctx, "T1", "cat-c1") == {h1}
    from fastapi.testclient import TestClient
    from coworker.server import SessionManager, create_app

    monkeypatch.setenv("OPC_PLATFORM_BASE", "")
    manager = SessionManager(workspace=tmp_path / "mgr", provider=None)
    client = TestClient(create_app(manager), base_url="http://127.0.0.1:8765")
    # create_app 会重建 enterprise_auth —— 注入已安装技能的上下文（登录后形态）
    manager.enterprise_auth._context = ctx
    cid = res.context_id_of(ctx)
    resp = client.delete(f"/v1/enterprise/resources/skills/cat-c1?tenant_id=T1&context_id={cid}")
    assert resp.status_code == 409, resp.text
    assert base.exists()  # 目录未被删
    # turn 结束释放 → 卸载成功且版本目录被回收
    assert REGISTRY.release(ctx, "T1", "cat-c1", h1) is True
    resp2 = client.delete(f"/v1/enterprise/resources/skills/cat-c1?tenant_id=T1&context_id={cid}")
    assert resp2.status_code == 200, resp2.text
    assert not base.exists()


def test_schedule_uninstall_unpinned_removes_immediately(tmp_path, monkeypatch):
    """无运行中引用时，schedule_uninstall_or_now 立即物理删除目录。"""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    ctx = _ctx("subject-u1")
    _install(ctx, "T1", "cat-immediate", "body\n")
    base = installer.catalog_dir(ctx, "T1", "cat-immediate")
    assert base.exists()

    removed = REGISTRY.schedule_uninstall_or_now(ctx, "T1", "cat-immediate")
    assert removed is True
    assert not base.exists()


def test_schedule_uninstall_pinned_defers_until_release(tmp_path, monkeypatch):
    """运行中引用时，schedule_uninstall_or_now 延迟清理，新启动立即拒绝，最后 release 瞬间自动删除。"""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    ctx = _ctx("subject-u1")
    h1 = _install(ctx, "T1", "cat-pinned", "body\n")
    ref = SkillRef("T1", "cat-pinned", h1)
    REGISTRY.acquire(ctx, ref, session_id="sess-active")
    base = installer.catalog_dir(ctx, "T1", "cat-pinned")
    assert base.exists()

    # 标记清理，但因为有正在运行的会话，暂缓物理删除
    removed = REGISTRY.schedule_uninstall_or_now(ctx, "T1", "cat-pinned")
    assert removed is False
    assert base.exists()

    # 新启动尝试 resolve 立即被拒（SKILL_REVOKED）
    from coworker.enterprise.registry import SKILL_REVOKED
    with pytest.raises(SkillRefError) as exc:
        REGISTRY.resolve(ctx, ref)
    assert exc.value.code == SKILL_REVOKED

    # 运行中 turn 结束触发 release → 自动物理删除
    REGISTRY.release(ctx, "T1", "cat-pinned", h1)
    assert not base.exists()


def test_reconcile_local_installs_prunes_missing(tmp_path, monkeypatch):
    """列表拉取对账：本地已安装但云端不存在的技能，自动对账回收删除。"""
    from coworker.enterprise import resources as res

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    ctx = _ctx("subject-u1")
    _install(ctx, "T1", "cat-keep", "keep\n")
    _install(ctx, "T1", "cat-del", "del\n")

    base_keep = installer.catalog_dir(ctx, "T1", "cat-keep")
    base_del = installer.catalog_dir(ctx, "T1", "cat-del")
    assert base_keep.exists()
    assert base_del.exists()

    # 云端只剩下 cat-keep
    pruned = res.reconcile_local_installs(ctx, "T1", {"cat-keep"})
    assert pruned == ["cat-del"]
    assert base_keep.exists()
    assert not base_del.exists()
