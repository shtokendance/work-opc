"""Enterprise credential storage contract (BND-160).

The enterprise **refresh** token never reaches this process's disk:

* Desktop: the renderer stores/rotates it in the macOS Keychain through the
  Tauri main process (`enterprise_secret_*` commands, service
  ``com.openworker.desktop.enterprise``). The sidecar — a plain Python child
  process — cannot call Tauri commands, so it only ever sees a one-time
  presentation of the refresh token (the login handoff, each refresh call),
  and keeps it in memory for the duration of that call only.
* Tests / headless: :class:`MemoryStore` behaves identically with no OS calls.

What the sidecar DOES hold is the short-lived **access** token and the
enterprise profile metadata (issuer/subject/tenant/generation — DATA-120c
fields), both memory-only. ``SecretStore`` (coworker/secrets.py) is untouched:
no enterprise profile ever enters `secrets.json`.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol


@dataclass
class EnterpriseProfileMeta:
    """Non-secret enterprise identity metadata (DATA-120c source fields)."""

    issuer: str = ""
    subject: str = ""
    tenant: str = ""
    generation: int = 0

    def context_key(self) -> str:
        """BND-120 isolation key: switching any component orphans the context."""
        return f"{self.issuer}|{self.subject}|{self.tenant}|{self.generation}"


class EnterpriseCredentialStore(Protocol):
    """Where the sidecar keeps the live access token + profile metadata.

    Implementations must never persist what they hold: no files, no logs.
    """

    def set_access_token(self, profile: str, access_token: str) -> None:
        """Remember the current access token for `profile` (memory only)."""
        ...

    def get_access_token(self, profile: str) -> Optional[str]:
        ...

    def set_meta(self, profile: str, meta: EnterpriseProfileMeta) -> None:
        ...

    def get_meta(self, profile: str) -> Optional[EnterpriseProfileMeta]:
        ...

    def clear(self, profile: str) -> None:
        """Drop everything held for `profile` (logout / reuse detection)."""
        ...

    def profiles(self) -> list[str]:
        """Profile names currently held — for tests and status views only."""
        ...


@dataclass
class _Entry:
    access_token: Optional[str] = None
    meta: Optional[EnterpriseProfileMeta] = None


class MemoryStore:
    """Process-local store for tests and desktop-less environments."""

    def __init__(self) -> None:
        self._entries: Dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def set_access_token(self, profile: str, access_token: str) -> None:
        with self._lock:
            self._entries.setdefault(profile, _Entry()).access_token = access_token

    def get_access_token(self, profile: str) -> Optional[str]:
        with self._lock:
            entry = self._entries.get(profile)
            return entry.access_token if entry else None

    def set_meta(self, profile: str, meta: EnterpriseProfileMeta) -> None:
        with self._lock:
            self._entries.setdefault(profile, _Entry()).meta = meta

    def get_meta(self, profile: str) -> Optional[EnterpriseProfileMeta]:
        with self._lock:
            entry = self._entries.get(profile)
            return entry.meta if entry else None

    def clear(self, profile: str) -> None:
        with self._lock:
            self._entries.pop(profile, None)

    def profiles(self) -> list[str]:
        with self._lock:
            return sorted(self._entries)


class KeychainStore(MemoryStore):
    """Desktop store: refresh lives in the macOS Keychain (renderer-held), so
    the sidecar side is memory-only like :class:`MemoryStore`.

    The name documents the split: the *system of record* for the refresh token
    is the Keychain entry written via the Tauri `enterprise_secret_*` commands;
    this class is what the sidecar holds instead of ever writing that value to
    `secrets.json` or any file. Behavior is identical to MemoryStore on
    purpose — persistence of either token or metadata is forbidden here.
    """

    # Persist nothing: the Keychain entry belongs to the Tauri main process.
    PERSISTS = False
