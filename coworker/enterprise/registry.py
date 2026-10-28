"""Stable enterprise skill registry (WORK-RESOURCES-001, BND-007, DATA-102/104).

Enterprise skills are addressed by a STRUCTURED ref — issuer/subject shard +
tenant + catalog + bundle SHA — never by SKILL.md ``name``. The old
name-shadowing loader behavior (managed dirs appended at the tail so a
same-named enterprise skill overwrote the personal one) is deliberately not
reused: the personal ``/name`` flow keeps its semantics, and the enterprise
flow resolves this registry, so a personal skill and C1/C2 same-named
enterprise skills coexist without overwriting each other (VAL-212).

Turn pinning (BND-007): a turn that starts with SHA h1 keeps loading h1 for
its whole life even after the user updates to h2 or the active pointer moves;
updates only affect NEW turns. Disable/uninstall forbid NEW starts
immediately; a running turn may finish its approved version, and the version
directory is recycled only after the last reference ends (引用归零回收).

Revocation beats pinning (BND-001): every load re-checks the resource-level
grant (the caller passes an ``authorize`` callback); a revoked catalog fails
the NEXT load even mid-turn-elsewhere.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .auth import EnterpriseContext
from . import installer

# Stable local codes (INT-105 family).
SKILL_NOT_AVAILABLE = "SKILL_NOT_AVAILABLE"
SKILL_VERSION_CHANGED = "SKILL_VERSION_CHANGED"
SKILL_REVOKED = "SKILL_REVOKED"


class SkillRefError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": self.message}}


@dataclass(frozen=True)
class SkillRef:
    """DATA-102 structured enterprise skill reference."""

    tenant_id: str
    catalog_id: str
    bundle_sha256: str  # the pinned version this ref demands
    name: str = ""
    source: str = "catalog"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillRef":
        if not isinstance(data, dict):
            raise SkillRefError("INVALID_REQUEST", "skill_ref must be an object")
        source = str(data.get("source") or "catalog")
        if source != "catalog":
            raise SkillRefError("INVALID_REQUEST", f"unsupported skill_ref source: {source!r}")
        tenant = str(data.get("tenant_id") or "").strip()
        catalog = str(data.get("catalog_id") or "").strip()
        sha = str(data.get("bundle_sha256") or "").strip().lower()
        if not tenant or not catalog or not sha:
            raise SkillRefError(
                "INVALID_REQUEST", "skill_ref requires tenant_id, catalog_id, bundle_sha256"
            )
        return cls(
            tenant_id=tenant,
            catalog_id=catalog,
            bundle_sha256=sha,
            name=str(data.get("name") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "tenant_id": self.tenant_id,
            "catalog_id": self.catalog_id,
            "bundle_sha256": self.bundle_sha256,
            "name": self.name,
        }


@dataclass
class _RefCount:
    identity: str
    tenant_id: str
    catalog_id: str
    sha256: str
    count: int = 0
    sessions: set[str] = field(default_factory=set)


class EnterpriseRegistry:
    """Ref-counted resolver from SkillRef → on-disk version directory."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._refs: dict[tuple[str, str, str, str], _RefCount] = {}
        self._pending_uninstall: set[tuple[str, str, str]] = set()  # (ident, tenant, catalog)

    def _pinned_versions_locked(
        self, ident: str, tenant_id: str, catalog_id: str
    ) -> set[str]:
        return {
            entry.sha256
            for (e_ident, e_tenant, e_cat, _sha), entry in self._refs.items()
            if e_ident == ident and e_tenant == tenant_id and e_cat == catalog_id and entry.count > 0
        }

    # -- resolution ---------------------------------------------------------------

    def resolve(
        self,
        context: EnterpriseContext,
        ref: SkillRef,
        *,
        authorize: Optional[Callable[[EnterpriseContext, str, str, str], bool]] = None,
    ) -> dict[str, Any]:
        """Locate the EXACT version directory for `ref`. Raises SkillRefError.

        ``authorize`` (resource-level grant re-check, TASK-020) runs on EVERY
        resolve — pinning fixes the VERSION, never the PERMISSION. The cache
        key carries the REF's sha so the grant entry actually hits (the authz
        shard records concrete versions; B2)."""
        ident = installer.identity_hash(context)
        with self._lock:
            if (ident, ref.tenant_id, ref.catalog_id) in self._pending_uninstall:
                raise SkillRefError(SKILL_REVOKED, "the catalog authorization was revoked")
        if authorize is not None and not authorize(
            context, ref.tenant_id, ref.catalog_id, ref.bundle_sha256
        ):
            raise SkillRefError(SKILL_REVOKED, "the catalog authorization was revoked")
        record = installer.get_install(context, ref.tenant_id, ref.catalog_id)
        if record is None:
            raise SkillRefError(
                SKILL_NOT_AVAILABLE, "this catalog is not installed for the current identity"
            )
        if record.active_sha256 != ref.bundle_sha256:
            # BND-007: a RUNNING turn keeps its pinned version while the version
            # dir is on disk (updates only affect new turns). New starts pin the
            # current active, so a ref naming a missing version is a hard error.
            # 停用≠撤权：钉住版本的运行中 turn 可完成当前版本（minor 修复），
            # 资源授权门已在上方按 ref 版本复核。
            base = installer.catalog_dir(context, ref.tenant_id, ref.catalog_id)
            pinned_root = installer.find_skill_root(
                base / installer.VERSIONS_DIR / ref.bundle_sha256
            )
            if pinned_root is None:
                raise SkillRefError(
                    SKILL_VERSION_CHANGED,
                    "the pinned version is no longer installed; start a new turn to use the current one",
                )
            from ..skills.base import _parse_skill

            skill = _parse_skill(pinned_root / "SKILL.md")
            return {
                "name": skill.name,
                "description": skill.description,
                "instructions": skill.instructions,
                "resources_path": str(pinned_root),
                "version": record.version,
                "tenant_id": ref.tenant_id,
                "catalog_id": ref.catalog_id,
                "bundle_sha256": ref.bundle_sha256,
                "enabled": record.enabled,
            }
        # 新启动（active 分支）：停用立即禁止 load。
        if not record.enabled:
            raise SkillRefError(SKILL_NOT_AVAILABLE, "this catalog is disabled")
        root = installer.active_version_dir(record, context)
        if root is None:
            raise SkillRefError(SKILL_NOT_AVAILABLE, "the installed version is incomplete")
        from ..skills.base import _parse_skill

        skill = _parse_skill(root / "SKILL.md")
        return {
            "name": skill.name,
            "description": skill.description,
            "instructions": skill.instructions,
            "resources_path": str(root),
            "version": record.version,
            "tenant_id": ref.tenant_id,
            "catalog_id": ref.catalog_id,
            "bundle_sha256": ref.bundle_sha256,
            "enabled": record.enabled,
        }

    # -- reference counting (turn pinning + deferred recycle) ----------------------

    def acquire(
        self,
        context: EnterpriseContext,
        ref: SkillRef,
        *,
        session_id: str = "",
        authorize: Optional[Callable[[EnterpriseContext, str, str, str], bool]] = None,
    ) -> dict[str, Any]:
        """Resolve AND pin the version dir for the caller's lifetime."""
        resolved = self.resolve(context, ref, authorize=authorize)
        key = (
            installer.identity_hash(context),
            ref.tenant_id,
            ref.catalog_id,
            ref.bundle_sha256,
        )
        with self._lock:
            entry = self._refs.get(key)
            if entry is None:
                entry = _RefCount(
                    identity=key[0],
                    tenant_id=ref.tenant_id,
                    catalog_id=ref.catalog_id,
                    sha256=ref.bundle_sha256,
                )
                self._refs[key] = entry
            entry.count += 1
            if session_id:
                entry.sessions.add(session_id)
        return resolved

    def release(
        self, context: EnterpriseContext, tenant_id: str, catalog_id: str, sha256: str
    ) -> bool:
        """Drop one pin. Returns True when the version's refcount hit zero
        (the caller may then recycle the directory if it is not active)."""
        ident = installer.identity_hash(context)
        key = (ident, tenant_id, catalog_id, sha256)
        trigger_uninstall = False
        with self._lock:
            entry = self._refs.get(key)
            if entry is not None:
                entry.count = max(0, entry.count - 1)
                if entry.count == 0:
                    del self._refs[key]
            if (ident, tenant_id, catalog_id) in self._pending_uninstall:
                remaining = self._pinned_versions_locked(ident, tenant_id, catalog_id)
                if not remaining:
                    self._pending_uninstall.discard((ident, tenant_id, catalog_id))
                    trigger_uninstall = True
        if trigger_uninstall:
            installer.uninstall(context, tenant_id, catalog_id)
        return True

    def pinned_versions(
        self, context: EnterpriseContext, tenant_id: str, catalog_id: str
    ) -> set[str]:
        ident = installer.identity_hash(context)
        with self._lock:
            return self._pinned_versions_locked(ident, tenant_id, catalog_id)

    def schedule_uninstall_or_now(
        self, context: EnterpriseContext, tenant_id: str, catalog_id: str
    ) -> bool:
        """Schedule or immediately perform uninstallation of an enterprise skill.
        If any version is currently pinned (in-use), deferred until all pins drop.
        Returns True if uninstalled immediately, False if deferred."""
        ident = installer.identity_hash(context)
        with self._lock:
            pinned = self._pinned_versions_locked(ident, tenant_id, catalog_id)
            if pinned:
                self._pending_uninstall.add((ident, tenant_id, catalog_id))
                return False
        # No pins active: execute uninstall now
        installer.uninstall(context, tenant_id, catalog_id)
        with self._lock:
            self._pending_uninstall.discard((ident, tenant_id, catalog_id))
        return True

    def release_session(self, session_id: str) -> None:
        """A session died / context switched: drop every pin it held."""
        with self._lock:
            for entry in self._refs.values():
                entry.sessions.discard(session_id)

    def drop_identity(self, identity_hash: str) -> None:
        """Identity switch: every pin held under the old subject shard is
        void (the new identity re-resolves from its own shard)."""
        with self._lock:
            dead = [k for k in self._refs if k[0] == identity_hash]
            for key in dead:
                self._refs.pop(key, None)
            dead_pending = [k for k in self._pending_uninstall if k[0] == identity_hash]
            for key in dead_pending:
                self._pending_uninstall.discard(key)

    def ref_counts(self) -> dict[tuple[str, str, str, str], int]:
        with self._lock:
            return {key: entry.count for key, entry in self._refs.items()}


# Process-wide registry (refs are in-process leases by nature).
REGISTRY = EnterpriseRegistry()
