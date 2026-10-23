"""Managed enterprise skill installer (WORK-RESOURCES-001, BND-006, DATA-103).

Installs STANDARD skill packages (raw catalog archives whose entry point is a
single ``SKILL.md`` — no ``opc-client.json``, no ``publish_id``, no release
record) into an identity-sharded managed root::

    state_dir()/enterprise-skills/catalog/<identity_hash>/<tenant>/<catalog_id>/
        active.json            # atomic pointer record (os.replace)
        versions/<sha256>/     # immutable, fully validated unpacked skill
        .staging-<uuid>/       # same-disk staging; crash residue is cleaned

Atomic switch (the old installer's "rmtree then move" is explicitly NOT
reused — ADR-002): the archive bytes are hashed against the expected SHA
FIRST, unpacked into a same-disk staging dir with the full safety rules,
renamed into the immutable versions dir, and only then is ``active.json``
replaced atomically. ANY failure before the pointer flip leaves the previous
active version byte-for-byte intact. First install lands ``enabled=true``;
updates preserve the previous ``enabled`` value; a same-SHA reinstall is an
idempotent no-op for state. No package scripts are ever executed.

Path/zip guardrails (BND-006): absolute paths, Windows drives/UNC, ``..``
escapes, backslash separators, symlinks, normalized duplicate paths, more
than one skill root, and the size ceilings (256 MiB archive / 100k entries /
20k files / 32 MiB per file / 512 MiB total) all refuse the install BEFORE
the active pointer moves.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Optional

from ..secrets import state_dir
from .auth import EnterpriseContext

logger = logging.getLogger("coworker.enterprise.installer")

SCHEMA_VERSION = 1
SOURCE_CATALOG = "catalog"
ACTIVE_RECORD = "active.json"
VERSIONS_DIR = "versions"
STAGING_PREFIX = ".staging-"

# BND-006 ceilings (fixed by the spec from current parser facts).
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_ENTRIES = 100_000
MAX_FILES = 20_000
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024

# Stable codes produced locally (INT-105 family).
SKILL_HASH_MISMATCH = "SKILL_HASH_MISMATCH"
SKILL_PACKAGE_UNSAFE = "SKILL_PACKAGE_UNSAFE"
SKILL_PACKAGE_TOO_LARGE = "SKILL_PACKAGE_TOO_LARGE"
SKILL_NOT_INSTALLED = "SKILL_NOT_INSTALLED"
INSTALL_FAILED = "INSTALL_FAILED"

_SEGMENT_OK = re.compile(r"^[^\s/\\]+$")

AuditFn = Callable[[dict[str, Any]], None]


def _err(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


def _audit(audit: Optional[AuditFn], row: dict[str, Any]) -> None:
    if audit is None:
        logger.info("enterprise install audit: %s", row)
        return
    try:
        audit(row)
    except Exception:  # noqa: BLE001 - audit must never break install
        logger.warning("enterprise install audit sink failed: %s", row)


def _safe_segment(value: str, what: str) -> str:
    """One path segment: non-empty, ≤128 chars, no separators/dots/control."""
    value = (value or "").strip()
    if not value or len(value) > 128:
        raise ValueError(f"unsafe {what}")
    if (
        any(c in value for c in ("/", "\\", "\0"))
        or ".." in value
        or value.startswith(".")
        or any(ord(c) < 32 for c in value)
        or not _SEGMENT_OK.match(value)
    ):
        raise ValueError(f"unsafe {what}")
    return value


def identity_hash(context: EnterpriseContext) -> str:
    """SHA-256 of the canonical (issuer, subject) serialization — the shard
    key. No token material ever enters the path (DATA-103)."""
    raw = unicodedata.normalize("NFC", f"{context.issuer}\n{context.subject}")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def managed_root(context: EnterpriseContext) -> Path:
    return state_dir() / "enterprise-skills" / "catalog" / identity_hash(context)


def catalog_dir(context: EnterpriseContext, tenant_id: str, catalog_id: str) -> Path:
    tenant_id = _safe_segment(tenant_id, "tenant_id")
    catalog_id = _safe_segment(catalog_id, "catalog_id")
    return managed_root(context) / tenant_id / catalog_id


def legacy_root(context: EnterpriseContext) -> Path:
    """The OLD publish_id-based root (kept on disk as rollback material; never
    scanned as a catalog source — TASK-023)."""
    return state_dir() / "enterprise-skills" / "opc" / _safe_segment(
        context.tenant or "none", "tenant"
    )


@dataclass
class InstallRecord:
    """DATA-103 local install record (active.json payload)."""

    tenant_id: str
    catalog_id: str
    name: str = ""
    version: str = ""
    active_sha256: str = ""
    enabled: bool = True
    installed_at: float = 0.0
    identity_hash: str = ""
    schema_version: int = SCHEMA_VERSION
    source: str = SOURCE_CATALOG
    last_error: dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return stable_ref_key(self.identity_hash, self.tenant_id, self.catalog_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "identity_hash": self.identity_hash,
            "tenant_id": self.tenant_id,
            "catalog_id": self.catalog_id,
            "name": self.name,
            "version": self.version,
            "active_sha256": self.active_sha256,
            "enabled": self.enabled,
            "installed_at": self.installed_at,
            "last_error": dict(self.last_error),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InstallRecord":
        return cls(
            tenant_id=str(data.get("tenant_id") or ""),
            catalog_id=str(data.get("catalog_id") or ""),
            name=str(data.get("name") or ""),
            version=str(data.get("version") or ""),
            active_sha256=str(data.get("active_sha256") or ""),
            enabled=bool(data.get("enabled", True)),
            installed_at=float(data.get("installed_at") or 0.0),
            identity_hash=str(data.get("identity_hash") or ""),
            schema_version=int(data.get("schema_version") or SCHEMA_VERSION),
            source=str(data.get("source") or SOURCE_CATALOG),
            last_error=dict(data.get("last_error") or {}),
        )


def stable_ref_key(identity: str, tenant_id: str, catalog_id: str) -> str:
    """The stable registry key (BND-007): 主体/空间/目录 — never the name."""
    return f"{identity}:{tenant_id}:{catalog_id}"


# -- safe unpack --------------------------------------------------------------------


def _normalized_parts(name: str) -> Optional[tuple[str, ...]]:
    """Pure-posix normalization; None when the entry is structurally unsafe."""
    if "\0" in name or "\\" in name:
        return None
    pure = PurePosixPath(name)
    if pure.is_absolute():
        return None
    parts: list[str] = []
    for part in pure.parts:
        if part in ("", "."):
            continue
        if part == "..":
            return None
        if ":" in part:  # Windows drive / ADS
            return None
        parts.append(part)
    return tuple(parts) if parts else None


def unpack_safe(bundle: bytes, staged: Path) -> Optional[str]:
    """Extract a standard skill archive into `staged` with the BND-006
    guardrails. Returns an error string, or None on success. Exactly one
    skill root (one SKILL.md) is required; the content lands under that root
    folder inside `staged`."""
    if len(bundle) > MAX_ARCHIVE_BYTES:
        return f"archive exceeds the 256MiB limit ({len(bundle)} bytes)"
    try:
        archive = zipfile.ZipFile(io.BytesIO(bundle))
    except zipfile.BadZipFile:
        return "archive is not a valid zip"
    raw_infos = [
        i
        for i in archive.infolist()
        if not i.is_dir()
        and "__MACOSX" not in PurePosixPath(i.filename).parts
        and PurePosixPath(i.filename).name != ".DS_Store"
        and not PurePosixPath(i.filename).name.startswith("._")
    ]
    if not raw_infos:
        return "archive is empty"
    if len(raw_infos) > MAX_ENTRIES:
        return f"archive exceeds the {MAX_ENTRIES} entry limit"
    seen_paths: set[tuple[str, ...]] = set()
    md_roots: set[tuple[str, ...]] = set()
    md_count = 0
    for info in raw_infos:
        parts = _normalized_parts(info.filename)
        if parts is None:
            return f"archive contains an unsafe path: {info.filename!r}"
        if parts in seen_paths:  # 规范化后重复路径（a//b、a/./b、真重复条目）
            return f"archive contains a duplicate normalized path: {info.filename!r}"
        seen_paths.add(parts)
        if (info.external_attr >> 16) & 0xF000 == 0xA000:
            return f"archive contains a symlink: {info.filename!r}"
        if PurePosixPath(info.filename).name == "SKILL.md":
            md_count += 1
            md_roots.add(parts[:-1])
    if md_count != 1 or len(md_roots) != 1:
        return "archive must contain exactly one skill (one SKILL.md)"
    total = 0
    n_files = 0
    for info in raw_infos:
        parts = _normalized_parts(info.filename)
        assert parts is not None  # checked above
        if info.file_size > MAX_FILE_BYTES:
            return f"archive file exceeds the 32MiB limit: {info.filename!r}"
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            return "archive exceeds the 512MiB decompressed limit"
        n_files += 1
        if n_files > MAX_FILES:
            return f"archive exceeds the {MAX_FILES} file limit"
        # Keep the root folder inside staged so the layout mirrors the archive.
        target = staged.joinpath(*parts)
        try:
            target.resolve().relative_to(staged.resolve())
        except (ValueError, OSError):
            return f"archive path escapes the staging dir: {info.filename!r}"
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with archive.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
        except (zipfile.BadZipFile, RuntimeError) as exc:
            # 条目级损坏（中央目录声称存在但数据不可读）：422 家族的
            # 明确拒绝，而不是让异常穿透成 500。
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            return f"archive entry is corrupt: {info.filename!r} ({exc})"
    return None


def find_skill_root(version_dir: Path) -> Optional[Path]:
    """Locate the directory holding SKILL.md (the root may nest one or more
    levels). Returns None when the version dir is incomplete."""
    try:
        candidates = [p for p in version_dir.rglob("SKILL.md") if p.is_file()]
    except OSError:
        return None
    return candidates[0].parent if len(candidates) == 1 else None


# -- record IO ----------------------------------------------------------------------


def _read_record(base: Path) -> Optional[InstallRecord]:
    raw = base / ACTIVE_RECORD
    if not raw.is_file():
        return None
    try:
        record = InstallRecord.from_dict(json.loads(raw.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None
    version_dir = base / VERSIONS_DIR / record.active_sha256
    if record.active_sha256 and find_skill_root(version_dir) is None:
        # Crash residue or tampering: the pointer names a version that is not
        # whole. Fail closed — this install does not exist (BND-006).
        return None
    return record


def _write_record_atomic(base: Path, record: InstallRecord) -> None:
    tmp = base / f".active-{os.getpid()}-{time.time_ns()}.tmp"
    tmp.write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")
    os.replace(tmp, base / ACTIVE_RECORD)


def get_install(context: EnterpriseContext, tenant_id: str, catalog_id: str) -> Optional[InstallRecord]:
    try:
        base = catalog_dir(context, tenant_id, catalog_id)
    except ValueError:
        return None
    return _read_record(base)


def list_installs(context: EnterpriseContext) -> list[InstallRecord]:
    root = managed_root(context)
    out: list[InstallRecord] = []
    try:
        tenants = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return out
    for tenant in tenants:
        try:
            catalogs = sorted(p for p in tenant.iterdir() if p.is_dir())
        except OSError:
            continue
        for catalog in catalogs:
            record = _read_record(catalog)
            if record is not None:
                out.append(record)
    return out


# -- lifecycle ----------------------------------------------------------------------

_install_lock = threading.Lock()


def _rmtree_staging_guarded(path: Path) -> None:
    """B1 防护：staging 清理只许删除 managed root（state_dir 下
    enterprise-skills/catalog）内的 .staging-* 目录。resolve 后前缀校验，
    拒绝 '.'、相对路径陷阱与任何越界路径——绝不可能碰到进程 CWD。"""
    try:
        resolved = path.resolve()
    except OSError:
        return
    root = (state_dir() / "enterprise-skills" / "catalog").resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        logger.error("refusing to remove staging outside the managed root: %s", resolved)
        return
    if not resolved.name.startswith(STAGING_PREFIX):
        logger.error("refusing to remove non-staging path: %s", resolved)
        return
    shutil.rmtree(resolved, ignore_errors=True)


def install(
    context: EnterpriseContext,
    tenant_id: str,
    catalog_id: str,
    bundle: bytes,
    *,
    expected_sha256: str,
    name: str = "",
    version: str = "",
    audit: Optional[AuditFn] = None,
) -> dict[str, Any]:
    """Validate `bundle` and atomically activate it for (identity, tenant,
    catalog). The previous active version survives ANY failure."""
    base_row = {
        "session_id": "",
        "tool": "enterprise_skill_install",
        "arguments": {
            "tenant": tenant_id,
            "catalog_id": catalog_id,
            "expected_sha256": expected_sha256,
        },
    }
    try:
        base = catalog_dir(context, tenant_id, catalog_id)
    except ValueError as exc:
        _audit(audit, {**base_row, "stage": "rejected", "status": "refused", "reason": str(exc)})
        return _err(SKILL_PACKAGE_UNSAFE, str(exc))

    expected = (expected_sha256 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        return _err(SKILL_HASH_MISMATCH, "expected_sha256 must be a 64-char lowercase hex digest")
    actual = hashlib.sha256(bundle).hexdigest()
    if actual != expected:
        _audit(
            audit,
            {**base_row, "stage": "rejected", "status": "refused",
             "reason": "downloaded bytes do not match the expected sha256"},
        )
        return _err(
            SKILL_HASH_MISMATCH,
            f"archive bytes hash to {actual[:16]}…, expected {expected[:16]}…",
        )

    with _install_lock:  # serialize per-process installs of the same object
        previous = _read_record(base)
        if (
            previous is not None
            and previous.active_sha256 == expected
            and find_skill_root(base / VERSIONS_DIR / expected) is not None
        ):
            # 同 SHA 幂等：不改变 enabled，不重落地。
            return {"ok": True, "record": previous.to_dict(), "unchanged": True}

        # B1 修复：staging 哨兵用 None（不是 Path()——str(Path()) 是 '.'
        # 会让 finally 的 rmtree 清空进程 CWD）。rmtree 前再加一道
        # resolve 前缀断言：只删 managed root 下的真实 .staging-* 目录。
        staging: Optional[Path] = Path(
            tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=base if base.is_dir() else None)
        )
        # Same-disk guarantee: when the catalog dir did not exist yet the
        # tempdir landed on the default tmpfs — rebuild staging inside it so
        # the final rename is a true same-filesystem atomic switch.
        try:
            base.mkdir(parents=True, exist_ok=True)
            if staging.parent != base:
                inner = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=base))
                shutil.rmtree(staging, ignore_errors=True)
                staging = inner
            problem = unpack_safe(bundle, staging)
            if problem is not None:
                code = (
                    SKILL_PACKAGE_TOO_LARGE
                    if "exceeds" in problem
                    else SKILL_PACKAGE_UNSAFE
                )
                _audit(audit, {**base_row, "stage": "rejected", "status": "refused", "reason": problem})
                _set_last_error(base, previous, code, problem)
                return _err(code, problem)

            version_dir = base / VERSIONS_DIR / expected
            if version_dir.exists():
                shutil.rmtree(version_dir)
            try:
                version_dir.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging, version_dir)  # same-disk rename
            except OSError as exc:
                _audit(
                    audit,
                    {**base_row, "stage": "activate", "status": "failed", "reason": str(exc)},
                )
                _set_last_error(base, previous, INSTALL_FAILED, str(exc))
                return _err(INSTALL_FAILED, f"could not activate the new version: {exc}")
            staging = None  # consumed by the rename — finally must NOT touch it

            record = InstallRecord(
                tenant_id=tenant_id,
                catalog_id=catalog_id,
                name=name or (previous.name if previous else ""),
                version=version or (previous.version if previous else ""),
                active_sha256=expected,
                # 首次安装启用；更新保留此前 enabled（BND-006）。
                enabled=True if previous is None else previous.enabled,
                installed_at=time.time(),
                identity_hash=identity_hash(context),
            )
            try:
                _write_record_atomic(base, record)
            except OSError as exc:
                # The version dir is whole on disk; only the pointer failed.
                # The OLD active record (if any) is untouched on disk — the
                # tmp write never replaced it.
                _audit(
                    audit,
                    {**base_row, "stage": "activate", "status": "failed", "reason": str(exc)},
                )
                return _err(INSTALL_FAILED, f"could not write the active record: {exc}")
        finally:
            if staging is not None:
                _rmtree_staging_guarded(staging)
                staging = None

    _audit(audit, {**base_row, "stage": "installed", "status": "granted", "reason": expected[:16]})
    return {"ok": True, "record": record.to_dict(), "unchanged": False}


def _set_last_error(
    base: Path, previous: Optional[InstallRecord], code: str, message: str
) -> None:
    """Failure feedback WITHOUT touching the active record's core fields."""
    if previous is None:
        return
    updated = InstallRecord.from_dict(previous.to_dict())
    updated.last_error = {"code": code, "message": message[:300]}
    try:
        _write_record_atomic(base, updated)
    except OSError:
        logger.warning("could not persist last_error for %s", base)


def set_enabled(
    context: EnterpriseContext, tenant_id: str, catalog_id: str, enabled: bool
) -> dict[str, Any]:
    try:
        base = catalog_dir(context, tenant_id, catalog_id)
    except ValueError as exc:
        return _err(SKILL_PACKAGE_UNSAFE, str(exc))
    # M5 修复：与 install 共用同一把锁——读-改-写 active 记录期间不允许
    # 安装并发替换指针（否则旧 active_sha256 可能复活，违反 INT-103 同锁）。
    with _install_lock:
        record = _read_record(base)
        if record is None:
            return _err(SKILL_NOT_INSTALLED, "this catalog is not installed for the current identity")
        record.enabled = bool(enabled)
        try:
            _write_record_atomic(base, record)
        except OSError as exc:
            return _err(INSTALL_FAILED, str(exc))
        return {"ok": True, "record": record.to_dict()}


def uninstall(context: EnterpriseContext, tenant_id: str, catalog_id: str) -> dict[str, Any]:
    """Remove THIS identity's record + its version dirs. Repeating an
    uninstall of an absent install is a success (INT-105). Personal skills and
    other identities' shards are never touched."""
    try:
        base = catalog_dir(context, tenant_id, catalog_id)
    except ValueError as exc:
        return _err(SKILL_PACKAGE_UNSAFE, str(exc))
    if not base.exists():
        return {"ok": True, "removed": False}
    try:
        shutil.rmtree(base)
    except OSError as exc:
        return _err(INSTALL_FAILED, str(exc))
    return {"ok": True, "removed": True}


def active_version_dir(record: InstallRecord, context: EnterpriseContext) -> Optional[Path]:
    base = catalog_dir(context, record.tenant_id, record.catalog_id)
    root = find_skill_root(base / VERSIONS_DIR / record.active_sha256)
    return root


def legacy_installs(context: EnterpriseContext) -> list[dict[str, Any]]:
    """Old publish_id-format installs still sitting on disk (BND-010:
    legacy_requires_reinstall — preserved as rollback material, never executed,
    never scanned as a catalog)."""
    root = legacy_root(context)
    out: list[dict[str, Any]] = []
    try:
        entries = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return out
    for entry in entries:
        has_md = (entry / "SKILL.md").is_file()
        record_raw = entry / "installed.json"
        record: dict[str, Any] = {}
        if record_raw.is_file():
            try:
                record = json.loads(record_raw.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                record = {}
        out.append(
            {
                "publish_id": entry.name,
                "name": str(record.get("skill_id") or entry.name),
                "version": str(record.get("version") or ""),
                "sha256": str(record.get("sha256") or ""),
                "path_present": has_md,
                "status": "legacy_requires_reinstall",
            }
        )
    return out


def clean_staging_residue(context: EnterpriseContext) -> int:
    """Crash-residue sweep: staging dirs left by a dead process. Only `.staging-*`
    under THIS identity's managed root is removed; versions/active are sacred."""
    removed = 0
    root = managed_root(context)
    try:
        stack = [root]
        while stack:
            current = stack.pop()
            for child in current.iterdir():
                if child.is_dir() and child.name.startswith(STAGING_PREFIX):
                    shutil.rmtree(child, ignore_errors=True)
                    removed += 1
                elif child.is_dir():
                    stack.append(child)
    except OSError:
        pass
    return removed
