"""Store contract for enterprise credentials (BND-160).

Both implementations hold the access token + profile metadata in memory ONLY —
the enterprise refresh token's system of record is the macOS Keychain entry
the Tauri main process owns, and neither implementation may write anything to
disk (in particular never to the SecretStore's secrets.json).
"""

from __future__ import annotations

import pytest

from coworker.enterprise.store import (
    EnterpriseProfileMeta,
    KeychainStore,
    MemoryStore,
)


@pytest.mark.parametrize("cls", [MemoryStore, KeychainStore])
def test_access_token_round_trip(cls):
    store = cls()
    assert store.get_access_token("p1") is None
    store.set_access_token("p1", "tok-1")
    assert store.get_access_token("p1") == "tok-1"
    store.set_access_token("p1", "tok-2")  # overwrite
    assert store.get_access_token("p1") == "tok-2"


@pytest.mark.parametrize("cls", [MemoryStore, KeychainStore])
def test_meta_round_trip_and_context_key(cls):
    store = cls()
    meta = EnterpriseProfileMeta(
        issuer="https://opc.example.com",
        subject="u1",
        tenant="t1",
        generation=3,
    )
    store.set_meta("p1", meta)
    got = store.get_meta("p1")
    assert got is not None
    assert got.subject == "u1"
    # BND-120 isolation key: every component distinguishes the context.
    assert got.context_key() == "https://opc.example.com|u1|t1|3"
    assert got.context_key() != EnterpriseProfileMeta(
        issuer="https://opc.example.com", subject="u1", tenant="t1", generation=4
    ).context_key()


@pytest.mark.parametrize("cls", [MemoryStore, KeychainStore])
def test_clear_drops_everything(cls):
    store = cls()
    store.set_access_token("p1", "tok")
    store.set_meta("p1", EnterpriseProfileMeta(subject="u1"))
    store.clear("p1")
    assert store.get_access_token("p1") is None
    assert store.get_meta("p1") is None
    assert store.profiles() == []


def test_keychain_store_persists_nothing():
    # The desktop split: the sidecar store must behave exactly like the memory
    # one. Persistence of either token or metadata is a BND-160 violation.
    store = KeychainStore()
    assert store.PERSISTS is False
    store.set_access_token("p", "tok")
    # A second instance sees nothing — no shared/file backing.
    assert KeychainStore().get_access_token("p") is None
