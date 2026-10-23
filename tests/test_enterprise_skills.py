"""WORK-RESOURCES-001 — the retired release chain (TASK-023) + session grant.

The old publish_id installer / catalog auto-sync are deleted: the fake's old
routes now 404 AND record a tripwire violation, legacy on-disk installs are
surfaced as legacy_requires_reinstall (never executed), and the session-level
grant refresh (fail closed) is the remaining gate from the old module.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from coworker.enterprise import skills as es
from coworker.enterprise.auth import EnterpriseContext
from coworker.enterprise import installer
from coworker.testing.fake_opc import FakeOpc, add_work_resources_samples


def _ctx(tenant: str = "tenant-a", generation: int = 1) -> EnterpriseContext:
    return EnterpriseContext(
        issuer="https://opc.example.com",
        subject="sub-1",
        tenant=tenant,
        generation=generation,
        authz_version=1,
        spaces=[],
        access_token="opc-access-test",
    )


@pytest.fixture
def fake_opc():
    fake = FakeOpc().start()
    try:
        yield fake
    finally:
        fake.stop()


# -- session grant (the surviving half of the old module) ----------------------------


async def test_grant_refresh_ok_and_fail_closed(fake_opc: FakeOpc):
    ctx = _ctx()
    assert await es.refresh_distribution_grant(
        ctx, platform_base=fake_opc.base_url
    )
    assert es.grant_allows(ctx)

    fake_opc.session_check_ok = False
    assert not await es.refresh_distribution_grant(
        ctx, platform_base=fake_opc.base_url
    )
    assert not es.grant_allows(ctx)  # fail closed


async def test_grant_refresh_without_platform_fails_closed():
    ctx = _ctx()
    assert not await es.refresh_distribution_grant(ctx, platform_base="")
    assert not es.grant_allows(ctx)


def test_grant_ttl_expiry():
    ctx = _ctx()
    es._grants[ctx.key] = es._GrantEntry(ok=True, checked_at=time.time() - 120)
    assert not es.grant_allows(ctx)
    assert es.grant_is_stale(ctx)


# -- TASK-023: the old release routes are retired ------------------------------------


def test_release_routes_404_and_trip(fake_opc: FakeOpc):
    """VAL-216: the old distribution routes are gone — 404, and any hit marks
    the release tripwire."""
    import httpx

    token = fake_opc.mint_token("subject-u1", ["T1"])
    client = httpx.Client(base_url=fake_opc.base_url, timeout=10)
    headers = {"Authorization": f"Bearer {token}"}
    for path in (
        "/integration/v1/skills",
        "/integration/v1/skills/pub-1/download",
        "/integration/v1/internal/skills",
    ):
        resp = client.get(path, headers=headers)
        assert resp.status_code == 404, path
    assert len(fake_opc.release_violations) == 3


def test_new_chain_never_calls_release(fake_opc: FakeOpc, tmp_path, monkeypatch):
    """Browsing + installing through the new resources family never touches a
    release route (the assertion the tripwire exists for)."""
    import httpx

    from coworker.enterprise.resources import PlatformResourceClient

    samples = add_work_resources_samples(fake_opc)
    ctx = EnterpriseContext(
        issuer="https://opc.example.com",
        subject="subject-u1",
        tenant="T1",
        generation=1,
        authz_version=1,
        spaces=["T1", "T2"],
        access_token=samples.u1_token,
    )
    client = PlatformResourceClient(fake_opc.base_url, ctx)
    out = asyncio.run(client.list_resources("skills"))
    assert "error" not in out and len(out["items"]) == 2
    bundle, error = asyncio.run(client.download_archive("T1", "cat-c1", samples.h1))
    assert bundle == samples.h1_bytes and error is None
    result = installer.install(
        ctx, "T1", "cat-c1", bundle, expected_sha256=samples.h1,
        name="summarizer", version="1.0.0",
    )
    assert result["ok"]
    fake_opc.assert_no_release_calls()


# -- legacy installs: preserved, flagged, never executable ----------------------------


def test_legacy_installs_flagged_not_executable(fake_opc: FakeOpc, tmp_path, monkeypatch):
    """BND-010: an old publish_id directory on disk is surfaced as
    legacy_requires_reinstall and never joins the executable registry."""
    ctx = _ctx(tenant="tenant-old")
    legacy = installer.legacy_root(ctx) / "pub-1"
    legacy.mkdir(parents=True)
    (legacy / "SKILL.md").write_text("---\nname: opc-hr\n---\n\nold managed body\n", encoding="utf-8")
    (legacy / "installed.json").write_text(
        json.dumps({"publish_id": "pub-1", "skill_id": "opc.hr", "version": "1.0.0"}),
        encoding="utf-8",
    )
    rows = installer.legacy_installs(ctx)
    assert len(rows) == 1
    assert rows[0]["status"] == "legacy_requires_reinstall"
    assert rows[0]["publish_id"] == "pub-1"
    # Not a catalog install: the registry knows nothing about it.
    assert installer.get_install(ctx, "tenant-old", "pub-1") is None
    assert installer.list_installs(ctx) == []
