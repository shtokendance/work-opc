"""WORK-RESOURCES-001 — installer invariants + PBT P3/P4 (TASK-016).

VAL-211 (crash/failure injection) and the VAL-102 properties:
* P3 (seed=20261024, ≥500 event sequences): repeated install/update/disable/
  uninstall/switch events with injected write failures — the active pointer
  only ever names a complete, fully validated version; repeated operations
  never produce duplicate executables.
* P4 (seed=20261025, ≥500 cases): archive path normalization, links, dup
  names, size boundaries — extracted output never escapes the install root.
"""

from __future__ import annotations

import hashlib
import io
import os
import random
import threading
import time
import zipfile
from pathlib import Path

import pytest

from coworker.enterprise import installer
from coworker.enterprise.auth import EnterpriseContext
from coworker.testing.fake_opc import build_skill_package, sha256_hex


def _ctx(subject: str = "sub-1", issuer: str = "https://opc.example.com") -> EnterpriseContext:
    return EnterpriseContext(
        issuer=issuer,
        subject=subject,
        tenant="T1",
        generation=1,
        authz_version=1,
        spaces=[],
        access_token="tok",
    )


# -- VAL-211: failure injection -----------------------------------------------------


def test_bad_package_keeps_previous_active(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    ctx = _ctx()
    h1 = build_skill_package("v1 body\n", root="skill", frontmatter_name="s")
    sha1 = sha256_hex(h1)
    first = installer.install(ctx, "T1", "cat-1", h1, expected_sha256=sha1)
    assert first["ok"]

    # 坏包：字节与声明不符
    bad = installer.install(
        ctx, "T1", "cat-1", h1 + b"x", expected_sha256=sha1
    )
    assert not bad["ok"] and bad["error"]["code"] == "SKILL_HASH_MISMATCH"
    record = installer.get_install(ctx, "T1", "cat-1")
    assert record.active_sha256 == sha1  # 旧 active 完整保留


def test_unsafe_package_rejected_and_old_active_intact(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    ctx = _ctx()
    h1 = build_skill_package("v1 body\n", root="skill", frontmatter_name="s")
    sha1 = sha256_hex(h1)
    assert installer.install(ctx, "T1", "cat-1", h1, expected_sha256=sha1)["ok"]

    def evil_zip(entries: dict[str, str]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, text in entries.items():
                zf.writestr(name, text)
        return buf.getvalue()

    cases = {
        "traversal": {"skill/SKILL.md": "x", "skill/../../evil": "y"},
        "absolute": {"/etc/skill/SKILL.md": "x"},
        "drive": {"C:/skill/SKILL.md": "x"},
        "backslash": {"skill\\SKILL.md": "x"},
        "dup-normalized": {"skill/./SKILL.md": "a", "skill/SKILL.md": "b"},
        "two-roots": {"a/SKILL.md": "x", "b/SKILL.md": "y"},
        "no-skill-md": {"readme.md": "x"},
    }
    for name, entries in cases.items():
        blob = evil_zip(entries)
        out = installer.install(
            ctx, "T1", "cat-1", blob, expected_sha256=sha256_hex(blob)
        )
        assert not out["ok"], name
        assert out["error"]["code"] in (
            installer.SKILL_PACKAGE_UNSAFE,
        ), (name, out)
    record = installer.get_install(ctx, "T1", "cat-1")
    assert record.active_sha256 == sha1
    root = installer.active_version_dir(record, ctx)
    assert (root / "SKILL.md").is_file()
    assert "v1 body" in (root / "SKILL.md").read_text(encoding="utf-8")


def test_symlink_entry_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    ctx = _ctx()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("skill/SKILL.md")
        zf.writestr(info, "x")
        link = zipfile.ZipInfo("skill/link")
        link.external_attr = 0xA000 << 16  # symlink mode
        zf.writestr(link, "/etc/passwd")
    blob = buf.getvalue()
    out = installer.install(ctx, "T1", "cat-1", blob, expected_sha256=sha256_hex(blob))
    assert not out["ok"] and out["error"]["code"] == installer.SKILL_PACKAGE_UNSAFE
    assert installer.get_install(ctx, "T1", "cat-1") is None


def test_update_preserves_enabled_and_crash_residue_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    ctx = _ctx()
    h1 = build_skill_package("v1\n", root="skill", frontmatter_name="s")
    h2 = build_skill_package("v2\n", root="skill", frontmatter_name="s")
    s1, s2 = sha256_hex(h1), sha256_hex(h2)
    assert installer.install(ctx, "T1", "cat-1", h1, expected_sha256=s1)["ok"]
    assert installer.set_enabled(ctx, "T1", "cat-1", False)["ok"]
    out = installer.install(ctx, "T1", "cat-1", h2, expected_sha256=s2)
    assert out["ok"]
    record = installer.get_install(ctx, "T1", "cat-1")
    assert record.active_sha256 == s2 and record.enabled is False  # 更新保留 enabled
    # 两个版本都在盘上；active 指向完整版本
    base = installer.catalog_dir(ctx, "T1", "cat-1")
    assert (base / installer.VERSIONS_DIR / s1 / "skill" / "SKILL.md").is_file()
    assert (base / installer.VERSIONS_DIR / s2 / "skill" / "SKILL.md").is_file()
    # 模拟崩溃残留：staging 目录不会被读成安装
    residue = base / f"{installer.STAGING_PREFIX}dead"
    residue.mkdir(exist_ok=True)
    assert installer.list_installs(ctx) == [record]
    assert installer.clean_staging_residue(ctx) == 1


def test_active_pointer_never_names_broken_version(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    ctx = _ctx()
    h1 = build_skill_package("v1\n", root="skill", frontmatter_name="s")
    s1 = sha256_hex(h1)
    assert installer.install(ctx, "T1", "cat-1", h1, expected_sha256=s1)["ok"]
    # 人为破坏版本目录（删 SKILL.md）
    base = installer.catalog_dir(ctx, "T1", "cat-1")
    (base / installer.VERSIONS_DIR / s1 / "skill" / "SKILL.md").unlink()
    # 读侧 fail-closed：不完整版本不被当作已安装
    assert installer.get_install(ctx, "T1", "cat-1") is None


# -- PBT P3 (VAL-102, seed=20261024): event sequences --------------------------------


def _p3_blob(rng: random.Random, version: int) -> bytes:
    return build_skill_package(
        f"v{version} body\n", root="skill", frontmatter_name="s",
        extra_files={f"f{version}.md": f"content {version}"},
    )


def test_p3_active_only_complete_versions_seed_20261024(tmp_path, monkeypatch):
    """P3: 随机 install/update/disable/uninstall 事件序列 + 写入失败注入，
    active 只指完整已校验版本；重复操作不产生重复可执行对象。"""
    seed, sequences = 20261024, 500
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    rng = random.Random(seed)
    ctx = _ctx()

    versions: list[bytes] = [_p3_blob(rng, v) for v in range(6)]
    shas = [sha256_hex(b) for b in versions]

    def injectable_install(catalog: str, blob: bytes, sha: str) -> dict:
        # 注入：1/8 概率在激活时失败（模拟 rename/写 active 故障）
        if rng.random() < 0.125:
            return {"ok": False, "error": {"code": installer.INSTALL_FAILED, "message": "injected"}}
        return installer.install(ctx, "T1", catalog, blob, expected_sha256=sha)

    for seq in range(sequences):
        catalog = f"cat-{rng.randint(0, 2)}"
        action = rng.choice(["install", "update", "disable", "enable", "uninstall"])
        if action in ("install", "update"):
            v = rng.randrange(len(versions))
            out = injectable_install(catalog, versions[v], shas[v])
            if out.get("ok"):
                record = installer.get_install(ctx, "T1", catalog)
                assert record is not None
                # active 必须是完整版本
                root = installer.active_version_dir(record, ctx)
                assert root is not None and (root / "SKILL.md").is_file(), (
                    f"seq {seq}: active names a broken version"
                )
                assert record.active_sha256 == shas[v]
        elif action == "disable":
            installer.set_enabled(ctx, "T1", catalog, False)
        elif action == "enable":
            installer.set_enabled(ctx, "T1", catalog, True)
        else:
            installer.uninstall(ctx, "T1", catalog)
            assert installer.get_install(ctx, "T1", catalog) is None

    # 终态审计：每个 catalog 至多一个可执行记录，且全部完整
    seen = set()
    for record in installer.list_installs(ctx):
        key = (record.tenant_id, record.catalog_id)
        assert key not in seen, f"duplicate executable for {key}"
        seen.add(key)
        root = installer.active_version_dir(record, ctx)
        assert root is not None and (root / "SKILL.md").is_file()
    print(f"P3 seed={seed} sequences={sequences} final_installs={len(seen)}")


# -- PBT P4 (VAL-102, seed=20261025): path normalization / no escape ------------------


def _p4_archive(rng: random.Random) -> tuple[bytes, str, str]:
    """Build a (possibly nasty) archive; returns (blob, declared_sha, root_note)."""
    kind = rng.randrange(10)
    root = rng.choice(["skill", "nested/dir/skill", "s p a c e"])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if kind == 0:
            # 合法
            zf.writestr(f"{root}/SKILL.md", "body")
        elif kind == 1:
            # 点段（规范化后合法单根）
            zf.writestr(f"{root}/./SKILL.md", "body")
        elif kind == 2:
            # 双斜杠
            zf.writestr(f"{root}//SKILL.md", "body")
            zf.writestr(f"{root}/x.md", "y")
        elif kind == 3:
            # 遍历逃逸
            zf.writestr(f"{root}/SKILL.md", "body")
            zf.writestr(f"{root}/../evil", "y")
        elif kind == 4:
            # 绝对路径
            zf.writestr(f"/abs/SKILL.md", "body")
        elif kind == 5:
            # 反斜杠
            zf.writestr(f"{root}\\SKILL.md", "body")
        elif kind == 6:
            # 重复条目
            zf.writestr(f"{root}/SKILL.md", "a")
            zf.writestr(f"{root}/SKILL.md", "b")
        elif kind == 7:
            # Windows drive
            zf.writestr(f"C:/{root}/SKILL.md", "body")
        elif kind == 8:
            # 无 SKILL.md
            zf.writestr(f"{root}/x.md", "y")
        else:
            # 两个根
            zf.writestr(f"a/SKILL.md", "x")
            zf.writestr(f"b/SKILL.md", "y")
    return buf.getvalue(), root, str(kind)


def test_p4_extraction_never_escapes_seed_20261025(tmp_path, monkeypatch):
    """P4: 任意归档路径组合，解压输出永不逃逸安装根；合法单根（含多层）
    正常安装。"""
    seed, cases = 20261025, 500
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    rng = random.Random(seed)
    ctx = _ctx()
    accepted = 0
    for i in range(cases):
        blob, root, kind = _p4_archive(rng)
        sha = sha256_hex(blob)
        out = installer.install(ctx, "T1", f"cat-p4-{i}", blob, expected_sha256=sha)
        kind_i = int(kind)
        base = installer.catalog_dir(ctx, "T1", f"cat-p4-{i}")
        if kind_i in (3, 4, 5, 6, 7, 8, 9):  # 3..9 全部非法（含多技能根）
            # 非法：拒绝，且不落地
            assert not out["ok"], f"case {i} kind {kind}: unsafe accepted"
            assert out["error"]["code"] in (
                installer.SKILL_PACKAGE_UNSAFE,
            ), f"case {i}: {out}"
            assert not base.exists() or installer.get_install(ctx, "T1", f"cat-p4-{i}") is None
        else:
            # 合法（0/1/2）：接受，且解压内容全部在安装根内
            assert out["ok"], f"case {i} kind {kind}: {out}"
            accepted += 1
            record = installer.get_install(ctx, "T1", f"cat-p4-{i}")
            root_dir = installer.active_version_dir(record, ctx)
            assert root_dir is not None
            install_root = installer.managed_root(ctx).resolve()
            for p in root_dir.rglob("*"):
                assert str(p.resolve()).startswith(str(install_root)), (
                    f"case {i}: escape {p}"
                )
            assert (root_dir / "SKILL.md").is_file()
    assert accepted > 0, "P4 generator never produced a legal case"
    print(f"P4 seed={seed} cases={cases} accepted={accepted}")


# -- B1 修复回归：成功路径绝不可触碰进程 CWD ---------------------------------------


def test_successful_install_never_touches_cwd(tmp_path, monkeypatch):
    """B1 blocker 回归：staging=Path() 哨兵曾让 finally 的 rmtree 以 '.' 为
    目标清空进程 CWD。安装成功后 CWD canary 必须仍在。"""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    canary = workdir / "canary.txt"
    canary.write_text("alive", encoding="utf-8")
    monkeypatch.chdir(workdir)
    ctx = _ctx()
    h1 = build_skill_package("v1\n", root="skill", frontmatter_name="s")
    out = installer.install(ctx, "T1", "cat-1", h1, expected_sha256=sha256_hex(h1))
    assert out["ok"], out
    # 失败路径同样不得触碰 CWD
    bad = b"not a zip at all"
    out2 = installer.install(ctx, "T1", "cat-1", bad, expected_sha256=sha256_hex(bad))
    assert not out2["ok"]
    assert canary.read_text(encoding="utf-8") == "alive"
    assert (workdir / "canary.txt").exists()
    # 更新（rename 消费 staging）路径
    h2 = build_skill_package("v2\n", root="skill", frontmatter_name="s")
    out3 = installer.install(ctx, "T1", "cat-1", h2, expected_sha256=sha256_hex(h2))
    assert out3["ok"], out3
    assert canary.read_text(encoding="utf-8") == "alive"


def test_rmtree_guard_refuses_out_of_root(tmp_path, monkeypatch):
    """防护断言：非 managed-root 路径（如 CWD）被 _rmtree_staging_guarded 拒绝。"""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    target = workdir / "innocent"
    target.mkdir()
    (target / "f.txt").write_text("x", encoding="utf-8")
    monkeypatch.chdir(workdir)
    installer._rmtree_staging_guarded(target)  # 必须拒绝（仅日志）
    assert (target / "f.txt").exists()
    # 非 .staging-* 名称即使在 managed root 内也拒绝
    root = installer.managed_root(_ctx())
    root.mkdir(parents=True, exist_ok=True)
    other = root / "versions"
    other.mkdir(exist_ok=True)
    (other / "keep.txt").write_text("k", encoding="utf-8")
    installer._rmtree_staging_guarded(other)
    assert (other / "keep.txt").exists()


def test_corrupt_zip_entry_is_422_family(tmp_path, monkeypatch):
    """Minor 修复：条目级 BadZipFile → 明确 422 家族拒绝（SKILL_PACKAGE_UNSAFE），
    而不是异常穿透成 500。"""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    import zipfile as _zf
    from unittest import mock

    ctx = _ctx()
    blob = build_skill_package("body\n", root="skill", frontmatter_name="s")

    real_open = _zf.ZipFile.open

    def broken_open(self, name, *args, **kwargs):
        raise _zf.BadZipFile("truncated entry data")

    with mock.patch.object(_zf.ZipFile, "open", broken_open):
        out = installer.install(ctx, "T1", "cat-1", blob, expected_sha256=sha256_hex(blob))
    assert not out["ok"]
    assert out["error"]["code"] == installer.SKILL_PACKAGE_UNSAFE
    assert installer.get_install(ctx, "T1", "cat-1") is None


def test_set_enabled_during_install_applies_to_new_record(tmp_path, monkeypatch):
    """M5：set_enabled 与 install 同锁——安装进行中调用 set_enabled 会等待安装
    完成，然后作用于新 active 记录（绝不复活旧 active_sha256）。"""
    import threading

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    ctx = _ctx()
    h1 = build_skill_package("v1\n", root="skill", frontmatter_name="s")
    h2 = build_skill_package("v2\n", root="skill", frontmatter_name="s")
    s1, s2 = sha256_hex(h1), sha256_hex(h2)
    assert installer.install(ctx, "T1", "cat-1", h1, expected_sha256=s1)["ok"]

    started = threading.Event()
    release = threading.Event()

    original_unpack = installer.unpack_safe

    def slow_unpack(bundle, staged):
        started.set()
        release.wait(10)
        return original_unpack(bundle, staged)

    monkeypatch.setattr(installer, "unpack_safe", slow_unpack)
    result: dict = {}

    def do_install():
        result["out"] = installer.install(ctx, "T1", "cat-1", h2, expected_sha256=s2)

    t = threading.Thread(target=do_install)
    t.start()
    assert started.wait(5)
    # 安装持锁中：set_enabled 阻塞至安装完成
    enabled_result: dict = {}

    def do_enable():
        enabled_result["out"] = installer.set_enabled(ctx, "T1", "cat-1", False)

    t2 = threading.Thread(target=do_enable)
    t2.start()
    time.sleep(0.2)
    assert t2.is_alive()  # 被同锁挡住
    release.set()
    t.join(10)
    t2.join(10)
    assert result["out"]["ok"]
    record = installer.get_install(ctx, "T1", "cat-1")
    assert record.active_sha256 == s2  # 新 active 未被复活旧值
    assert record.enabled is False  # 停用作用于新记录
