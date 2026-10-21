"""Managed enterprise skill catalog (BND-120/142, INT-142, DATA-120c).

Enterprise skills are installed under a tenant-scoped managed root::

    state_dir()/enterprise-skills/opc/<tenant>/<publish_id>/

Isolation (BND-120): only the CURRENT tenant's directories are ever visible to
a session — the layout, the per-context catalog cache, and the loader injection
all key on the context (issuer|subject|tenant|generation), so the same skill
name installed for two tenants never bleeds across. Switching context (or back
to personal) drops the old shard: see :func:`clear_context_cache` plus the
manager's switch hook.

Install (BND-142): a downloaded bundle lands only after full validation —
the listing's ``sha256`` (the whole-archive byte hash, the value the platform
also sends as ``X-SHA256``) is re-computed over the downloaded bytes first
(``SKILL_HASH_MISMATCH`` on drift, nothing written), then zip safety
(paths/symlinks/limits, mirroring the knowledge bundle rules),
``opc-client.json`` present, ``exec_mode ∈ local_instruction|local_sandbox``
(``cloud_only`` never installs locally), ``clients`` includes ``work``, and the
manifest ``sha256`` equals the payload digest (everything in the bundle except
the manifest itself, hashed as ``sha256(relpath \\0 bytes)`` over sorted
paths — the same "digest of what executes" semantics knowledge K03 pins).
The two hashes are distinct pins: the listing hash pins the ARCHIVE bytes (and
is what ``installed.json`` records, so a later sync can skip a reinstall), the
manifest hash pins the unpacked payload. Any failure leaves disk untouched and
writes an audit row.

Distribution authorization (BND-142 执行前刷新): before enterprise skills may
start, the platform session is re-queried (INT-101f endpoint). A failed or
stale check fails CLOSED — enterprise names leave the skill menu while
personal skills keep working unchanged.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import shutil
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from ..secrets import state_dir
from .auth import EnterpriseContext, UPSTREAM_UNAVAILABLE

logger = logging.getLogger("coworker.enterprise.skills")

MANIFEST_NAME = "opc-client.json"
INSTALL_RECORD = "installed.json"
EXEC_MODES = ("local_instruction", "local_sandbox")
CLIENT_NAME = "work"
DEFAULT_PLATFORM_TIMEOUT = 15.0

# BND-180 stable codes produced locally (platform codes pass through untouched).
SKILL_MANIFEST_INVALID = "SKILL_MANIFEST_INVALID"
SKILL_HASH_MISMATCH = "SKILL_HASH_MISMATCH"
SKILL_PACKAGE_UNSAFE = "SKILL_PACKAGE_UNSAFE"
ENTERPRISE_NOT_CONFIGURED = "ENTERPRISE_NOT_CONFIGURED"

# Bundle limits, mirroring BND-140 (knowledge tenant_skill_bundle.go).
_MAX_ENTRIES = 100_000
_MAX_FILES = 20_000
_MAX_FILE_BYTES = 32 * 1024 * 1024
_MAX_TOTAL_BYTES = 512 * 1024 * 1024

# Distribution-grant freshness (BND-142 执行前刷新).
GRANT_TTL_SECONDS = 60.0

_SEGMENT_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Platform-issued segments (tenant names, publish ids) may legitimately contain
# CJK — the rule is traversal safety, not ASCII: no separators, no "..", no
# leading dot, no control characters, and a sane length cap.
_FORBIDDEN_SEGMENT_CHARS = ("/", "\\", "\0")


def _safe_segment(value: str, what: str) -> str:
    value = (value or "").strip()
    if not value or len(value) > 128:
        raise ValueError(f"unsafe {what}: {value!r}")
    if (
        any(c in value for c in _FORBIDDEN_SEGMENT_CHARS)
        or ".." in value
        or value.startswith(".")
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError(f"unsafe {what}: {value!r}")
    return value

AuditFn = Callable[[dict[str, Any]], None]


def _audit(audit: Optional[AuditFn], row: dict[str, Any]) -> None:
    if audit is None:
        logger.info("enterprise skill audit: %s", row)
        return
    try:
        audit(row)
    except Exception:  # noqa: BLE001 - audit must never break install/switch
        logger.warning("enterprise skill audit sink failed: %s", row)


def _err(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


# -- layout ---------------------------------------------------------------------


def managed_root(tenant: str) -> Path:
    """The tenant's managed skill root (opc/<tenant>)."""
    return state_dir() / "enterprise-skills" / "opc" / _safe_segment(tenant, "tenant")


def skill_install_dir(context: EnterpriseContext, publish_id: str) -> Path:
    return managed_root(context.tenant) / _safe_segment(publish_id, "publish_id")


def build_enterprise_skill_dirs(context: EnterpriseContext) -> list[Path]:
    """The managed dir injected into the session's SkillLoader for THIS context.

    Returns the tenant root ``opc/<tenant>`` (the loader treats each listed
    dir as a scope whose subfolders are skills — here one subfolder per
    publish_id), or [] when the tenant has no installs. Only the current
    tenant is ever visible (BND-120); a subfolder counts only while it holds
    a SKILL.md (a half-removed install degrades to absent, never to an error).
    """
    root = managed_root(context.tenant)
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []
    if any(p.is_dir() and (p / "SKILL.md").is_file() for p in entries):
        return [root]
    return []


# -- manifest + payload validation ----------------------------------------------


def _parse_manifest(staged: Path) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    raw = staged / MANIFEST_NAME
    if not raw.is_file():
        return None, f"{MANIFEST_NAME} is missing"
    try:
        manifest = json.loads(raw.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"{MANIFEST_NAME} is not valid JSON: {exc}"
    if not isinstance(manifest, dict):
        return None, f"{MANIFEST_NAME} must be a JSON object"
    return manifest, None


def validate_manifest(manifest: dict[str, Any], payload_sha256: str) -> list[str]:
    """Every problem with an opc-client.json, as human-readable strings ([] = valid)."""
    problems: list[str] = []
    skill_id = manifest.get("skill_id")
    if not isinstance(skill_id, str) or not skill_id.strip():
        problems.append("skill_id must be a non-empty string")
    version = manifest.get("version")
    if not isinstance(version, str) or not version.strip():
        problems.append("version must be a non-empty string")
    sha = manifest.get("sha256")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        problems.append("sha256 must be a 64-char lowercase hex digest")
    exec_mode = manifest.get("exec_mode")
    if exec_mode not in EXEC_MODES:
        problems.append(
            f"exec_mode must be one of {list(EXEC_MODES)} (cloud_only never installs locally)"
        )
    clients = manifest.get("clients")
    if (
        not isinstance(clients, list)
        or not all(isinstance(c, str) for c in clients)
        or CLIENT_NAME not in clients
    ):
        problems.append(f"clients must be a list including {CLIENT_NAME!r}")
    for key in ("allowed_tool_categories", "dependencies"):
        value = manifest.get(key)
        if value is not None and not isinstance(value, list):
            problems.append(f"{key} must be a list when present")
    if manifest.get("min_client_protocol") is not None and not isinstance(
        manifest.get("min_client_protocol"), (str, int)
    ):
        problems.append("min_client_protocol must be a string or number when present")
    if not re.fullmatch(r"[0-9a-f]{64}", str(sha or "")):
        return problems  # nothing sensible to compare against yet
    if sha != payload_sha256:
        problems.append("sha256 does not match the bundle payload digest")
    return problems


def payload_digest(staged: Path) -> str:
    """sha256 over the bundle's executable content: every file except the
    manifest, hashed as ``relpath \\0 bytes`` in sorted-path order. This is
    the client-side "payload digest" the manifest's sha256 must equal."""
    h = hashlib.sha256()
    files = sorted(
        p for p in staged.rglob("*") if p.is_file() and p.name != MANIFEST_NAME
    )
    for p in files:
        h.update(str(p.relative_to(staged)).encode("utf-8"))
        h.update(b"\x00")
        h.update(p.read_bytes())
        h.update(b"\x00")
    return h.hexdigest()


# -- safe unpack ------------------------------------------------------------------


def _unpack_safe(bundle: bytes, staged: Path) -> Optional[str]:
    """Extract a bundle zip into `staged` with the BND-140 guardrails.
    Returns an error string, or None on success."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(bundle))
    except zipfile.BadZipFile:
        return "bundle is not a valid zip archive"
    infos = [
        i
        for i in archive.infolist()
        if not i.is_dir()
        and "__MACOSX" not in Path(i.filename).parts
        and Path(i.filename).name != ".DS_Store"
        and not Path(i.filename).name.startswith("._")
    ]
    if not infos:
        return "bundle is empty"
    if len(infos) > _MAX_ENTRIES:
        return f"bundle exceeds the {_MAX_ENTRIES} entry limit"
    md_entries = [i for i in infos if Path(i.filename).name == "SKILL.md"]
    roots = {Path(i.filename).parts[0] if len(Path(i.filename).parts) > 1 else "" for i in md_entries}
    if len(md_entries) != 1 or len(roots) != 1:
        return "bundle must contain exactly one skill (one SKILL.md)"
    root = roots.pop()
    total = 0
    n_files = 0
    for info in infos:
        parts = Path(info.filename).parts
        if Path(info.filename).is_absolute() or ".." in parts or (parts and ":" in parts[0]):
            return f"bundle contains an unsafe path: {info.filename!r}"
        # Reject symlink entries (external_attr high bits: 0xA000 == symlink).
        if (info.external_attr >> 16) & 0xF000 == 0xA000:
            return f"bundle contains a symlink: {info.filename!r}"
        size = info.file_size
        if size > _MAX_FILE_BYTES:
            return f"bundle file exceeds the 32MiB limit: {info.filename!r}"
        total += size
        if total > _MAX_TOTAL_BYTES:
            return "bundle exceeds the 512MiB total limit"
        n_files += 1
        if n_files > _MAX_FILES:
            return f"bundle exceeds the {_MAX_FILES} file limit"
        rel = Path(*parts[1:]) if root and parts[0] == root else Path(info.filename)
        if not str(rel):
            continue
        target = staged / rel
        try:
            target.resolve().relative_to(staged.resolve())
        except (ValueError, OSError):
            return f"bundle path escapes the staging dir: {info.filename!r}"
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
    return None


# -- install ----------------------------------------------------------------------


@dataclass
class ManagedSkill:
    """One installed enterprise skill (DATA-120c local record fields).

    ``sha256`` is the pin the catalog sync compares against: the LISTING's
    whole-archive hash when the install came through a sync, otherwise the
    manifest's payload digest (a direct install has no listing to pin to)."""

    publish_id: str
    skill_id: str
    version: str
    sha256: str
    exec_mode: str
    name: str  # the SKILL.md frontmatter name — what load_skill/catalog use
    path: Path
    installed_at: float = 0.0
    last_authz_check: float = 0.0


def install_enterprise_skill(
    context: EnterpriseContext,
    publish_id: str,
    bundle: bytes,
    *,
    expected_sha256: str = "",
    audit: Optional[AuditFn] = None,
) -> dict[str, Any]:
    """Validate a downloaded bundle and atomically switch it into the managed
    root. Any validation failure leaves the previous install (if any) intact
    and writes an audit row — no half-installed state (BND-142).

    ``expected_sha256`` is the listing's whole-archive pin: when given, the
    downloaded bytes must hash to it BEFORE anything is staged, otherwise the
    install is refused with ``SKILL_HASH_MISMATCH`` (and the previous version
    stays put)."""
    base = {
        "session_id": "",
        "tool": "enterprise_skill_install",
        "arguments": {"tenant": context.tenant, "publish_id": publish_id},
    }
    try:
        publish_id = _safe_segment(publish_id, "publish_id")
    except ValueError as exc:
        _audit(audit, {**base, "stage": "rejected", "status": "refused", "reason": str(exc)})
        return _err(SKILL_MANIFEST_INVALID, str(exc))

    expected_sha256 = (expected_sha256 or "").strip().lower()
    if expected_sha256:
        actual = hashlib.sha256(bundle).hexdigest()
        if actual != expected_sha256:
            reason = (
                "downloaded bytes do not match the listed sha256 "
                f"(got {actual[:16]}…, listed {expected_sha256[:16]}…)"
            )
            _audit(
                audit,
                {**base, "stage": "rejected", "status": "refused", "reason": reason},
            )
            return _err(SKILL_HASH_MISMATCH, reason)

    staged_parent = Path(tempfile.mkdtemp(prefix="opc-skill-"))
    staged = staged_parent / "staged"
    staged.mkdir()
    try:
        problem = _unpack_safe(bundle, staged)
        if problem is None:
            manifest, problem = _parse_manifest(staged)
        if problem is None:
            digest = payload_digest(staged)
            problems = validate_manifest(manifest or {}, digest)
            problem = "; ".join(problems) if problems else None
        if problem is not None:
            _audit(
                audit,
                {**base, "stage": "rejected", "status": "refused", "reason": problem},
            )
            return _err(SKILL_MANIFEST_INVALID, problem)

        manifest = manifest or {}
        target = skill_install_dir(context, publish_id)
        record = {
            "publish_id": publish_id,
            "skill_id": str(manifest.get("skill_id") or ""),
            "version": str(manifest.get("version") or ""),
            # The catalog pin: the listing's whole-archive hash when there is
            # one (sync), else the manifest's payload digest (direct install).
            "sha256": expected_sha256 or str(manifest.get("sha256") or ""),
            "exec_mode": str(manifest.get("exec_mode") or ""),
            "installed_at": time.time(),
            "last_authz_check": 0.0,
        }
        (staged / INSTALL_RECORD).write_text(
            json.dumps(record, indent=2), encoding="utf-8"
        )
        # Atomic switch: the new tree is fully validated before it replaces
        # the old one; a crash mid-swap leaves either tree whole.
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(staged), str(target))
        _audit(
            audit,
            {**base, "stage": "installed", "status": "granted", "reason": record["skill_id"]},
        )
        clear_context_cache(context.key)
        return {"ok": True, "publish_id": publish_id, "skill": record}
    finally:
        shutil.rmtree(staged_parent, ignore_errors=True)


# -- catalog cache (BND-120 技能缓存按上下文键隔离) --------------------------------


@dataclass
class _CacheEntry:
    checked_at: float
    skills: list[ManagedSkill] = field(default_factory=list)


_lock = threading.Lock()
_catalog_cache: dict[str, _CacheEntry] = {}


def _iter_dirs(root: Path) -> list[Path]:
    try:
        return sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return []


def _read_install(context: EnterpriseContext, path: Path) -> Optional[ManagedSkill]:
    md = path / "SKILL.md"
    if not md.is_file():
        return None
    try:
        record = json.loads((path / INSTALL_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = {}
    name = path.name
    try:
        from ..skills.base import _parse_skill

        name = _parse_skill(md).name or name
    except (OSError, ValueError):
        pass
    return ManagedSkill(
        publish_id=str(record.get("publish_id") or path.name),
        skill_id=str(record.get("skill_id") or ""),
        version=str(record.get("version") or ""),
        sha256=str(record.get("sha256") or ""),
        exec_mode=str(record.get("exec_mode") or ""),
        name=name,
        path=path,
        installed_at=float(record.get("installed_at") or 0.0),
        last_authz_check=float(record.get("last_authz_check") or 0.0),
    )


def managed_skills(context: EnterpriseContext) -> list[ManagedSkill]:
    """The current tenant's installed skills — cached per context key, so a
    lookup for one tenant never serves another's shard."""
    with _lock:
        entry = _catalog_cache.get(context.key)
        if entry is not None:
            return list(entry.skills)
    skills = [
        s
        for root in build_enterprise_skill_dirs(context)
        for s in (
            _read_install(context, p)
            for p in _iter_dirs(root)
            if (p / "SKILL.md").is_file()
        )
        if s is not None
    ]
    with _lock:
        _catalog_cache[context.key] = _CacheEntry(checked_at=time.time(), skills=skills)
    return list(skills)


def clear_context_cache(context_key: str) -> None:
    """Drop one context's catalog shard (called on context switch)."""
    with _lock:
        _catalog_cache.pop(context_key, None)


def clear_caches() -> None:
    with _lock:
        _catalog_cache.clear()


def mark_authz_checked(context: EnterpriseContext) -> None:
    """Record a successful distribution-grant refresh on the cached shard."""
    with _lock:
        entry = _catalog_cache.get(context.key)
        if entry is not None:
            entry.checked_at = time.time()
            for skill in entry.skills:
                skill.last_authz_check = entry.checked_at


# -- distribution grant (BND-142 执行前刷新分发授权) ---------------------------------


@dataclass
class _GrantEntry:
    ok: bool
    checked_at: float
    detail: str = ""


_grants: dict[str, _GrantEntry] = {}
_grants_lock = threading.Lock()
_refresh_in_flight: set[str] = set()


async def refresh_distribution_grant(
    context: EnterpriseContext,
    *,
    platform_base: str = "",
    http_client: Optional[httpx.AsyncClient] = None,
) -> bool:
    """Re-query the platform session (INT-101f) before enterprise skills may
    start. Failure — unreachable, unconfigured, revoked — fails closed:
    :func:`grant_allows` returns False until a refresh succeeds."""
    base = (platform_base or "").rstrip("/")
    if http_client is None and not base:
        entry = _GrantEntry(ok=False, checked_at=time.time(), detail="no platform_base")
        with _grants_lock:
            _grants[context.key] = entry
        return False
    try:
        if http_client is not None:
            resp = await http_client.get(
                "/integration/v1/session",
                headers={"Authorization": f"Bearer {context.access_token}"},
            )
        else:
            async with httpx.AsyncClient(
                base_url=base, timeout=DEFAULT_PLATFORM_TIMEOUT
            ) as client:
                resp = await client.get(
                    "/integration/v1/session",
                    headers={"Authorization": f"Bearer {context.access_token}"},
                )
        ok = resp.status_code == 200
        detail = "" if ok else f"platform session check returned {resp.status_code}"
    except httpx.HTTPError as exc:
        ok, detail = False, f"platform unreachable: {exc}"
    with _grants_lock:
        _grants[context.key] = _GrantEntry(ok=ok, checked_at=time.time(), detail=detail)
    if ok:
        mark_authz_checked(context)
    return ok


def grant_allows(context: EnterpriseContext) -> bool:
    """The cached verdict: may enterprise skills START right now? Absent or
    stale-past-TTL means no (fail closed); the manager schedules the refresh."""
    with _grants_lock:
        entry = _grants.get(context.key)
    if entry is None:
        return False
    if time.time() - entry.checked_at > GRANT_TTL_SECONDS:
        return False
    return entry.ok


def grant_is_stale(context: EnterpriseContext) -> bool:
    with _grants_lock:
        entry = _grants.get(context.key)
    return entry is None or time.time() - entry.checked_at > GRANT_TTL_SECONDS


def grant_detail(context: EnterpriseContext) -> str:
    """Why the grant currently fails ("" when it allows) — for audit rows."""
    with _grants_lock:
        entry = _grants.get(context.key)
    if entry is None:
        return "distribution grant not yet verified"
    if time.time() - entry.checked_at > GRANT_TTL_SECONDS:
        return "distribution grant stale — re-verification pending"
    return entry.detail or "distribution grant refused"


def refresh_in_flight(context_key: str) -> bool:
    with _grants_lock:
        return context_key in _refresh_in_flight


def set_refresh_in_flight(context_key: str, value: bool) -> None:
    with _grants_lock:
        if value:
            _refresh_in_flight.add(context_key)
        else:
            _refresh_in_flight.discard(context_key)


def drop_grant(context_key: str) -> None:
    with _grants_lock:
        _grants.pop(context_key, None)


# -- platform download (INT-142) ---------------------------------------------------


async def fetch_enterprise_skill_bundle(
    platform_base: str,
    access_token: str,
    publish_id: str,
    *,
    http_client: Optional[httpx.AsyncClient] = None,
) -> bytes:
    """Download one skill's archive bytes from the platform's INT-142 download
    endpoint (authorization re-checked server-side before it forwards bytes)."""
    base = platform_base.rstrip("/")
    if not base:
        raise ValueError(ENTERPRISE_NOT_CONFIGURED)
    publish_id = _safe_segment(publish_id, "publish_id")
    headers = {"Authorization": f"Bearer {access_token}"}
    if http_client is not None:
        resp = await http_client.get(
            f"/integration/v1/skills/{publish_id}/download", headers=headers
        )
    else:
        async with httpx.AsyncClient(base_url=base, timeout=DEFAULT_PLATFORM_TIMEOUT) as client:
            resp = await client.get(
                f"/integration/v1/skills/{publish_id}/download", headers=headers
            )
    if resp.status_code != 200:
        raise RuntimeError(
            f"skill download failed: {resp.status_code} {resp.text[:300]}"
        )
    return resp.content


async def sync_enterprise_catalog(
    context: EnterpriseContext,
    *,
    platform_base: str,
    access_token: str,
    audit: Optional[AuditFn] = None,
    http_client: Optional[httpx.AsyncClient] = None,
) -> dict[str, Any]:
    """List the current subject's visible skills (INT-142) and install any
    publish_id this client doesn't have yet (or whose hash drifted). A listing
    or download failure is recorded and skipped — it never blocks the login
    flow, and never removes what's installed (BND-141 下架 stops NEW installs)."""
    base_row = {
        "session_id": "",
        "tool": "enterprise_skill_sync",
        "arguments": {"tenant": context.tenant},
    }
    base = platform_base.rstrip("/")
    headers = {"Authorization": f"Bearer {access_token}"}
    out: dict[str, Any] = {"listed": 0, "installed": [], "failed": [], "skipped": []}
    if not base:
        return {**out, "error": ENTERPRISE_NOT_CONFIGURED}
    try:
        if http_client is not None:
            resp = await http_client.get("/integration/v1/skills", headers=headers)
        else:
            async with httpx.AsyncClient(
                base_url=base, timeout=DEFAULT_PLATFORM_TIMEOUT
            ) as client:
                resp = await client.get("/integration/v1/skills", headers=headers)
        if resp.status_code != 200:
            _audit(
                audit,
                {
                    **base_row,
                    "stage": "list_failed",
                    "status": "refused",
                    "reason": f"platform skills list returned {resp.status_code}",
                },
            )
            return {**out, "error": UPSTREAM_UNAVAILABLE}
        listing = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        _audit(
            audit,
            {**base_row, "stage": "list_failed", "status": "refused", "reason": str(exc)},
        )
        return {**out, "error": UPSTREAM_UNAVAILABLE}

    have = {s.publish_id: s for s in managed_skills(context)}
    entries = listing.get("skills") or listing.get("items") or []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        out["listed"] += 1
        publish_id = str(entry.get("publish_id") or "")
        wanted_hash = str(entry.get("sha256") or entry.get("hash") or "")
        if not publish_id:
            out["skipped"].append("entry without publish_id")
            continue
        existing = have.get(publish_id)
        wanted_hash = wanted_hash.lower()
        if existing is not None and (
            not wanted_hash or existing.sha256 == wanted_hash
        ):
            continue  # already pinned at the published hash (BND-141)
        try:
            bundle = await fetch_enterprise_skill_bundle(
                base, access_token, publish_id, http_client=http_client
            )
            result = install_enterprise_skill(
                context, publish_id, bundle, expected_sha256=wanted_hash, audit=audit
            )
            if result.get("ok"):
                out["installed"].append(publish_id)
            else:
                out["failed"].append(
                    {publish_id: (result.get("error") or {}).get("code", "failed")}
                )
        except (RuntimeError, ValueError, httpx.HTTPError) as exc:
            reason = str(exc)[:200]
            if "SKILL_DELISTED" in reason or "SKILL_NOT_FOUND" in reason:
                # The platform's normal "not for you anymore" signal: a skip,
                # not a failure (BND-141 stops NEW installs; existing installs
                # are governed by the grant refresh, not by this sync).
                out["skipped"].append({publish_id: reason})
            else:
                out["failed"].append({publish_id: reason})
    return out
