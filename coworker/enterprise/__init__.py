"""Enterprise (OPC platform) integration for the work desktop client.

The desktop joins an OPC platform (WeCom-based enterprise identity) with:
browser login against INT-101, a refresh credential held in the macOS Keychain
by the Tauri main process (never in `secrets.json`, logs, or tool arguments),
and an in-memory-only access token (BND-160/163, INT-101).

WORK-RESOURCES-001: `resources` is the sidecar resource service (lists,
single-item authorization, install operations, the ≤30s resource authz
cache); `installer` atomically activates standard SKILL.md archives into the
identity-sharded catalog root; `registry` resolves structured skill_refs;
`scope` pins one knowledge base per turn; `tools` carries the enterprise
engine tools (scope-enforced knowledge_search/read, MCP bridge, the
ref-based enterprise_load_skill). The retired publish_id/release chain is
gone (TASK-023) — installing is an explicit user operation.
"""

from .auth import EnterpriseAuthService, EnterpriseContext
from .store import EnterpriseCredentialStore, KeychainStore, MemoryStore
from .tools import EnterpriseBridge

__all__ = [
    "EnterpriseAuthService",
    "EnterpriseContext",
    "EnterpriseCredentialStore",
    "KeychainStore",
    "MemoryStore",
    "EnterpriseBridge",
]
