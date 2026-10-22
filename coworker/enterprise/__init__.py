"""Enterprise (OPC platform) integration for the work desktop client.

The desktop joins an OPC platform (WeCom-based enterprise identity) with:
browser login against INT-101, a refresh credential held in the macOS Keychain
by the Tauri main process (never in `secrets.json`, logs, or tool arguments),
and an in-memory-only access token (BND-160/163, INT-101).

`store` holds the access-token/profile-metadata contract; `auth` drives the
login transaction state machine against the platform's token endpoints;
`skills` is the managed per-tenant skill catalog (BND-142); `tools` carries
the enterprise engine tools and the MCP approval bridge (INT-120/150).
"""

from .auth import EnterpriseAuthService, EnterpriseContext
from .store import EnterpriseCredentialStore, KeychainStore, MemoryStore
from .skills import (
    build_enterprise_skill_dirs,
    clear_context_cache,
    install_enterprise_skill,
    managed_skills,
)
from .tools import EnterpriseBridge

__all__ = [
    "EnterpriseAuthService",
    "EnterpriseContext",
    "EnterpriseCredentialStore",
    "KeychainStore",
    "MemoryStore",
    "EnterpriseBridge",
    "build_enterprise_skill_dirs",
    "clear_context_cache",
    "install_enterprise_skill",
    "managed_skills",
]
