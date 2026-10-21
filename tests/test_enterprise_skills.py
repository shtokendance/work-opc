"""TASK-W03 — the managed enterprise skill catalog (BND-120/142, INT-142).

Install validation (nothing half-written, audit rows), per-tenant isolation
(cache shards and dir layout), the loader priority that keeps a personal
same-named skill from shadowing a managed one, the distribution-grant refresh
that fails closed (offline ⇒ no new enterprise skills), and the INT-142
catalog sync against a fake platform.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import httpx
import pytest

from coworker.agent import build_engine
from coworker.agents import chat_agent
from coworker.enterprise import skills as es
from coworker.enterprise.auth import EnterpriseContext
from coworker.skills import SkillLoader
from coworker.testing.fake_opc import FakeOpc


def _ctx(tenant: str = "tenant-a", generation: int = 1, subject: str = "sub-1") -> EnterpriseContext:
    return EnterpriseContext(
        issuer="https://opc.example.com",
        subject=subject,
        tenant=tenant,
        generation=generation,
        authz_version=1,
        spaces=[],
        access_token="opc-access-test",
    )


def _payload_digest(files: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode("utf-8"))
        h.update(b"\x00")
        h.update(files[name])
        h.update(b"\x00")
    return h.hexdigest()


def _bundle(
    skill_name: str = "opc-hr",
    *,
    skill_id: str = "opc.hr",
    version: str = "1.0.0",
    exec_mode: str = "local_instruction",
    clients: tuple[str, ...] = ("work",),
    sha256: str | None = None,
    instructions: str = "managed body",
    manifest: dict | None = None,
    extra_files: dict[str, bytes] | None = None,
    root_folder: bool = False,
) -> bytes:
    skill_md = (
        f"---\nname: {skill_name}\ndescription: managed skill\n---\n\n{instructions}\n"
    ).encode("utf-8")
    files: dict[str, bytes] = {"SKILL.md": skill_md}
    if manifest is None:
        payload = dict(extra_files or {})
        manifest = {
            "skill_id": skill_id,
            "version": version,
            "sha256": sha256 or _payload_digest({"SKILL.md": skill_md, **payload}),
            "exec_mode": exec_mode,
            "clients": list(clients),
            "allowed_tool_categories": [],
            "dependencies": [],
            "min_client_protocol": 1,
        }
    files["opc-client.json"] = json.dumps(manifest).encode("utf-8")
    files.update(extra_files or {})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(f"{root_folder and 'pkg/' or ''}{name}", content)
    return buf.getvalue()


@pytest.fixture
def fake_opc():
    fake = FakeOpc().start()
    try:
        yield fake
    finally:
        fake.stop()


@pytest.fixture(autouse=True)
def _clean_caches():
    es.clear_caches()
    yield
    es.clear_caches()


# -- install validation -----------------------------------------------------------


def test_install_happy_path_lands_under_tenant_dir():
    ctx = _ctx()
    audit: list[dict] = []
    out = es.install_enterprise_skill(ctx, "pub-1", _bundle(), audit=audit.append)
    assert out["ok"], out
    target = es.skill_install_dir(ctx, "pub-1")
    assert (target / "SKILL.md").is_file()
    assert (target / "opc-client.json").is_file()
    record = json.loads((target / "installed.json").read_text())
    assert record["publish_id"] == "pub-1"
    assert record["sha256"]
    assert audit and audit[-1]["stage"] == "installed"
    # Visible via the catalog APIs.
    dirs = es.build_enterprise_skill_dirs(ctx)
    assert dirs == [es.managed_root(ctx.tenant)]
    managed = es.managed_skills(ctx)
    assert [m.name for m in managed] == ["opc-hr"]
    assert managed[0].publish_id == "pub-1"


def test_install_rejects_bundle_without_manifest_and_audits():
    ctx = _ctx()
    audit: list[dict] = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SKILL.md", "---\nname: x\n---\n\nbody\n")
    out = es.install_enterprise_skill(ctx, "pub-x", buf.getvalue(), audit=audit.append)
    assert not out["ok"]
    assert out["error"]["code"] == es.SKILL_MANIFEST_INVALID
    assert not es.skill_install_dir(ctx, "pub-x").exists()
    assert audit and audit[-1]["stage"] == "rejected"


@pytest.mark.parametrize(
    "manifest_problem",
    [
        {"exec_mode": "cloud_only"},
        {"exec_mode": "remote_run"},
        {"clients": ["code"]},
        {"clients": "work"},
        {"sha256": "0" * 64},  # digest mismatch
        {"skill_id": ""},
    ],
)
def test_install_rejects_invalid_manifest(manifest_problem):
    ctx = _ctx()
    payload = {"SKILL.md": b"---\nname: opc-hr\n---\n\nmanaged body\n"}
    manifest = {
        "skill_id": "opc.hr",
        "version": "1.0.0",
        "sha256": _payload_digest(payload),
        "exec_mode": "local_instruction",
        "clients": ["work"],
        **manifest_problem,
    }
    audit: list[dict] = []
    out = es.install_enterprise_skill(
        ctx, "pub-bad", _bundle(manifest=manifest), audit=audit.append
    )
    assert not out["ok"], manifest_problem
    assert out["error"]["code"] == es.SKILL_MANIFEST_INVALID
    assert not es.skill_install_dir(ctx, "pub-bad").exists()
    assert audit and audit[-1]["stage"] == "rejected"


def test_install_rejects_unsafe_zip_entries():
    ctx = _ctx()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SKILL.md", "---\nname: x\n---\n\nbody\n")
        z.writestr("opc-client.json", "{}")
        z.writestr("../escape.txt", "nope")
    audit: list[dict] = []
    out = es.install_enterprise_skill(ctx, "pub-zip", buf.getvalue(), audit=audit.append)
    assert not out["ok"]
    assert not es.skill_install_dir(ctx, "pub-zip").exists()
    assert audit and audit[-1]["stage"] == "rejected"


def test_install_rejects_symlink_entry():
    ctx = _ctx()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SKILL.md", "---\nname: x\n---\n\nbody\n")
        z.writestr("opc-client.json", "{}")
        info = zipfile.ZipInfo("link")
        info.external_attr = 0xA000 << 16  # symlink mode bits
        z.writestr(info, "/etc/passwd")
    out = es.install_enterprise_skill(ctx, "pub-link", buf.getvalue())
    assert not out["ok"]
    assert not es.skill_install_dir(ctx, "pub-link").exists()


def test_failed_reinstall_preserves_previous_version():
    ctx = _ctx()
    first = es.install_enterprise_skill(ctx, "pub-1", _bundle(instructions="v1 body"))
    assert first["ok"]
    target = es.skill_install_dir(ctx, "pub-1")
    before = (target / "SKILL.md").read_text()
    bad = es.install_enterprise_skill(
        ctx, "pub-1", _bundle(instructions="v2 body", sha256="f" * 64)
    )
    assert not bad["ok"]
    assert (target / "SKILL.md").read_text() == before  # old version intact


def test_publish_id_traversal_rejected():
    ctx = _ctx()
    out = es.install_enterprise_skill(ctx, "../evil", _bundle())
    assert not out["ok"]
    assert not (es.managed_root(ctx.tenant).parent / "evil").exists()


# -- per-tenant isolation + cache shards (BND-120) ---------------------------------


def test_same_skill_name_two_tenants_does_not_bleed():
    ctx_a = _ctx(tenant="tenant-a")
    ctx_b = _ctx(tenant="tenant-b")
    assert es.install_enterprise_skill(ctx_a, "pub-1", _bundle(instructions="A"))["ok"]
    assert es.install_enterprise_skill(ctx_b, "pub-1", _bundle(instructions="B"))["ok"]

    root_a = es.managed_root("tenant-a")
    root_b = es.managed_root("tenant-b")
    dirs_a = es.build_enterprise_skill_dirs(ctx_a)
    dirs_b = es.build_enterprise_skill_dirs(ctx_b)
    assert dirs_a == [root_a]
    assert dirs_b == [root_b]
    assert dirs_a != dirs_b

    # The catalog cache is sharded by context key: A's cached lookup must not
    # serve B's tenant even though both share the publish id.
    managed_a = es.managed_skills(ctx_a)
    assert managed_a[0].path.parent == root_a
    names_a = {m.name for m in managed_a}
    es.install_enterprise_skill(ctx_b, "pub-2", _bundle(skill_name="opc-b2"))
    assert {m.name for m in es.managed_skills(ctx_a)} == names_a

    loader_a = SkillLoader(dirs_a)
    loader_b = SkillLoader(dirs_b)
    assert "A" in loader_a.get("opc-hr").instructions
    assert "B" in loader_b.get("opc-hr").instructions


def test_clear_context_cache_only_drops_that_shard():
    ctx_a = _ctx(tenant="tenant-a")
    ctx_b = _ctx(tenant="tenant-b")
    es.install_enterprise_skill(ctx_a, "pub-1", _bundle())
    es.install_enterprise_skill(ctx_b, "pub-1", _bundle())
    es.managed_skills(ctx_a)
    es.managed_skills(ctx_b)
    es.clear_context_cache(ctx_a.key)
    # B's shard still warm (re-install for A must not have invalidated B).
    with es._lock:
        assert ctx_b.key in es._catalog_cache
        assert ctx_a.key not in es._catalog_cache


# -- loader priority: managed shadows personal (BND-120) ----------------------------


def test_enterprise_dir_wins_over_personal_same_name():
    personal = es.state_dir() / "skills" / "opc-hr"
    publish = es.managed_root("tenant-a") / "pub-1"
    for path, body in (
        (personal, "personal copy"),
        (publish, "managed copy"),
    ):
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(
            f"---\nname: opc-hr\ndescription: d\n---\n\n{body}\n", encoding="utf-8"
        )
    ctx = _ctx()
    dirs = es.build_enterprise_skill_dirs(ctx)
    assert dirs == [es.managed_root("tenant-a")]
    # The manager passes enterprise dirs LAST in the loader: later dirs win a
    # name collision, so the managed copy shadows the personal one.
    loader = SkillLoader([personal, *dirs])
    assert loader.get("opc-hr").path == str(publish)


def test_build_engine_enterprise_dirs_shadow_personal(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    personal = tmp_path / "state" / "skills" / "opc-hr"
    personal.mkdir(parents=True)
    (personal / "SKILL.md").write_text(
        "---\nname: opc-hr\ndescription: d\n---\n\npersonal copy\n", encoding="utf-8"
    )
    managed_root = tmp_path / "managed-opc" / "tenant-a"
    publish = managed_root / "pub-1"
    publish.mkdir(parents=True)
    (publish / "SKILL.md").write_text(
        "---\nname: opc-hr\ndescription: d\n---\n\nmanaged copy\n", encoding="utf-8"
    )

    class _Stub:
        def complete(self, **kwargs):  # pragma: no cover
            raise NotImplementedError

        def capabilities(self, model):
            from coworker.providers import ModelCapabilities

            return ModelCapabilities()

    engine = build_engine(
        agent=chat_agent(),
        provider=_Stub(),
        enterprise_skill_dirs=[managed_root],
    )
    assert engine.skill_loader.get("opc-hr").path == str(publish)
    assert engine.enterprise_skill_dirs == [managed_root]
    # No enterprise dirs ⇒ default dirs, behavior unchanged (personal mode).
    plain = build_engine(agent=chat_agent(), provider=_Stub())
    assert plain.enterprise_skill_dirs == []
    assert plain.skill_loader.get("opc-hr").path == str(personal)


# -- distribution grant (BND-142 执行前刷新, fail closed) ----------------------------


@pytest.mark.asyncio
async def test_grant_refresh_ok_and_fail_closed(fake_opc: FakeOpc):
    ctx = _ctx()
    fake_opc.session_check_ok = True
    assert await es.refresh_distribution_grant(ctx, platform_base=fake_opc.base_url)
    assert es.grant_allows(ctx)
    assert not es.grant_is_stale(ctx)

    fake_opc.session_check_ok = False
    assert not await es.refresh_distribution_grant(ctx, platform_base=fake_opc.base_url)
    assert not es.grant_allows(ctx)  # offline ⇒ refuse to start enterprise skills
    assert fake_opc.session_calls >= 2


@pytest.mark.asyncio
async def test_grant_refresh_without_platform_fails_closed():
    ctx = _ctx()
    assert not await es.refresh_distribution_grant(ctx, platform_base="")
    assert not es.grant_allows(ctx)


@pytest.mark.asyncio
async def test_grant_ttl_expiry():
    ctx = _ctx()
    await es.refresh_distribution_grant(ctx, platform_base="")  # records a verdict
    with es._grants_lock:
        entry = es._grants[ctx.key]
        entry.checked_at = entry.checked_at - (es.GRANT_TTL_SECONDS + 1)
    assert es.grant_is_stale(ctx)
    assert not es.grant_allows(ctx)


# -- INT-142 catalog sync ------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_sync_installs_listed_skills(fake_opc: FakeOpc):
    fake = fake_opc
    fake.skill_bundles["pub-9"] = _bundle(skill_name="opc-sync")
    ctx = _ctx()
    audit: list[dict] = []
    out = await es.sync_enterprise_catalog(
        ctx,
        platform_base=fake.base_url,
        access_token=ctx.access_token,
        audit=audit.append,
    )
    assert out["listed"] == 1
    assert out["installed"] == ["pub-9"]
    assert {m.name for m in es.managed_skills(ctx)} == {"opc-sync"}
    assert any(a["stage"] == "installed" for a in audit)


@pytest.mark.asyncio
async def test_catalog_sync_skips_delisted_and_bad_bytes(fake_opc: FakeOpc):
    fake = fake_opc
    fake.delisted_skills.add("pub-delisted")
    fake.skill_bundles["pub-delisted"] = _bundle()
    fake.skill_bundles["pub-broken"] = b"not a zip"
    fake.skill_listing_extra.append(
        {"publish_id": "pub-delisted", "sha256": hashlib.sha256(fake.skill_bundles["pub-delisted"]).hexdigest()}
    )
    fake.skill_listing_extra.append(
        {"publish_id": "pub-broken", "sha256": hashlib.sha256(b"not a zip").hexdigest()}
    )
    ctx = _ctx()
    out = await es.sync_enterprise_catalog(
        ctx, platform_base=fake.base_url, access_token=ctx.access_token
    )
    assert out["installed"] == []
    assert [next(iter(f)) for f in out["failed"]] == ["pub-broken"]  # the corrupt bundle
    assert [next(iter(s)) for s in out["skipped"]] == ["pub-delisted"]
    assert not es.skill_install_dir(ctx, "pub-delisted").exists()


@pytest.mark.asyncio
async def test_catalog_sync_listing_failure_is_audited(fake_opc: FakeOpc):
    fake = fake_opc
    fake.session_check_ok = False  # no effect here; break the listing instead
    ctx = _ctx()
    audit: list[dict] = []
    real_get = httpx.AsyncClient.get

    async def _boom(self, url, **kwargs):
        if "/integration/v1/skills" in str(url):
            raise httpx.ConnectError("down")
        return await real_get(self, url, **kwargs)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(httpx.AsyncClient, "get", _boom)
    try:
        out = await es.sync_enterprise_catalog(
            ctx, platform_base=fake.base_url, access_token=ctx.access_token, audit=audit.append
        )
    finally:
        monkey.undo()
    assert out.get("error") == es.UPSTREAM_UNAVAILABLE
    assert out["installed"] == []
    assert audit and audit[-1]["stage"] == "list_failed"


def test_bundle_with_root_folder_installs(tmp_path):
    ctx = _ctx()
    out = es.install_enterprise_skill(ctx, "pub-root", _bundle(root_folder=True))
    assert out["ok"], out
    assert (es.skill_install_dir(ctx, "pub-root") / "SKILL.md").is_file()


# -- whole-archive hash pinning (BND-142 下载钉 hash) -------------------------------


def test_install_verifies_listing_pin_before_staging():
    ctx = _ctx()
    audit: list[dict] = []
    out = es.install_enterprise_skill(
        ctx, "pub-h", _bundle(), expected_sha256="0" * 64, audit=audit.append
    )
    assert not out["ok"]
    assert out["error"]["code"] == es.SKILL_HASH_MISMATCH
    assert not es.skill_install_dir(ctx, "pub-h").exists()
    assert audit and audit[-1]["stage"] == "rejected"

    # A matching pin installs and is RECORDED as the catalog pin.
    bundle = _bundle()
    pin = hashlib.sha256(bundle).hexdigest()
    out = es.install_enterprise_skill(ctx, "pub-h", bundle, expected_sha256=pin)
    assert out["ok"], out
    assert out["skill"]["sha256"] == pin


def test_hash_mismatch_reinstall_keeps_previous_version():
    ctx = _ctx()
    first = _bundle(instructions="v1 body")
    out = es.install_enterprise_skill(
        ctx, "pub-1", first, expected_sha256=hashlib.sha256(first).hexdigest()
    )
    assert out["ok"]
    target = es.skill_install_dir(ctx, "pub-1")
    before = (target / "SKILL.md").read_text()
    bad = es.install_enterprise_skill(ctx, "pub-1", _bundle(instructions="v2"), expected_sha256="f" * 64)
    assert bad["error"]["code"] == es.SKILL_HASH_MISMATCH
    assert (target / "SKILL.md").read_text() == before  # old version intact


@pytest.mark.asyncio
async def test_catalog_sync_pins_whole_bundle_and_skips_reinstall(fake_opc: FakeOpc):
    fake = fake_opc
    bundle = _bundle(skill_name="opc-sync")
    fake.skill_bundles["pub-9"] = bundle
    ctx = _ctx()
    out = await es.sync_enterprise_catalog(
        ctx, platform_base=fake.base_url, access_token=ctx.access_token
    )
    assert out["installed"] == ["pub-9"]
    # installed.json records the LISTING pin — the whole-archive byte hash
    # (what the platform also sends as X-SHA256), not the manifest digest.
    record = json.loads((es.skill_install_dir(ctx, "pub-9") / "installed.json").read_text())
    assert record["sha256"] == hashlib.sha256(bundle).hexdigest()
    downloads = [r for r in fake.skills_requests if r["path"].endswith("/download")]
    assert len(downloads) == 1

    # A second sync over an unchanged listing is a no-op — no re-download,
    # no reinstall (the reviewer hit: manifest digest ≠ listing hash meant
    # EVERY sync re-installed everything).
    out2 = await es.sync_enterprise_catalog(
        ctx, platform_base=fake.base_url, access_token=ctx.access_token
    )
    assert out2["installed"] == []
    downloads = [r for r in fake.skills_requests if r["path"].endswith("/download")]
    assert len(downloads) == 1

    # Drift on the platform side (new bytes → new listing pin) DOES reinstall.
    fake.skill_bundles["pub-9"] = _bundle(skill_name="opc-sync", instructions="v2")
    out3 = await es.sync_enterprise_catalog(
        ctx, platform_base=fake.base_url, access_token=ctx.access_token
    )
    assert out3["installed"] == ["pub-9"]
    assert "v2" in (es.skill_install_dir(ctx, "pub-9") / "SKILL.md").read_text()


@pytest.mark.asyncio
async def test_catalog_sync_refuses_bytes_that_miss_the_listing_pin(fake_opc: FakeOpc):
    fake = fake_opc
    good = _bundle(skill_name="opc-hr", instructions="v1")
    fake.skill_bundles["pub-1"] = good
    ctx = _ctx()
    audit: list[dict] = []
    out = await es.sync_enterprise_catalog(
        ctx, platform_base=fake.base_url, access_token=ctx.access_token, audit=audit.append
    )
    assert out["installed"] == ["pub-1"]
    before = (es.skill_install_dir(ctx, "pub-1") / "SKILL.md").read_text()

    # The listing now pins bytes the download endpoint does NOT serve — both
    # for a new publish_id and for the already-installed one.
    fake.skill_listing_extra.append({"publish_id": "pub-2", "sha256": "0" * 64})
    fake.skill_bundles["pub-2"] = _bundle(skill_name="opc-new")
    fake.skill_listing_extra.append({"publish_id": "pub-1", "sha256": "f" * 64})
    out2 = await es.sync_enterprise_catalog(
        ctx, platform_base=fake.base_url, access_token=ctx.access_token, audit=audit.append
    )
    assert out2["installed"] == []
    assert {next(iter(f)): next(iter(f.values())) for f in out2["failed"]} == {
        "pub-1": es.SKILL_HASH_MISMATCH,
        "pub-2": es.SKILL_HASH_MISMATCH,
    }
    assert not es.skill_install_dir(ctx, "pub-2").exists()
    # The previous install of pub-1 was NOT clobbered by the mismatched bytes.
    assert (es.skill_install_dir(ctx, "pub-1") / "SKILL.md").read_text() == before
