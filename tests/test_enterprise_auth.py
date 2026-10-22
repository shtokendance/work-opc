"""TASK-W02 — enterprise login against a fake OPC platform (INT-101a-f).

End-to-end through the real FastAPI app and the real httpx stack (the app's
service builds its own AsyncClient against ``OPC_PLATFORM_BASE``, exactly as in
production), with ``coworker.testing.fake_opc`` standing in for the platform:
start → loopback callback → token exchange (PKCE S256 + one-time code) →
refresh rotation → reuse detection → logout, plus context-switch isolation and
the no-persistence scan (BND-160: no refresh value in secrets.json or logs).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from coworker.server import SessionManager, create_app
from coworker.testing.fake_opc import FakeOpc


@pytest.fixture
def fake_opc():
    fake = FakeOpc().start()
    try:
        yield fake
    finally:
        fake.stop()


def _client(tmp_path, monkeypatch, *, platform: str | None = "use-fake", token: str = "") -> TestClient:
    """A TestClient for the real app. `platform="use-fake"` is resolved by the
    caller through OPC_PLATFORM_BASE; None leaves the enterprise surface
    unconfigured. TestClient's base_url is the sidecar's loopback origin so the
    authorize redirect_uri passes the platform's 127.0.0.1 check."""
    if platform is not None:
        monkeypatch.setenv("OPC_PLATFORM_BASE", platform)
    if token:
        monkeypatch.setenv("COWORKER_API_TOKEN", token)
    manager = SessionManager(workspace=tmp_path, provider=None)
    return TestClient(create_app(manager), base_url="http://127.0.0.1:8765")


def _start(client: TestClient, fake: FakeOpc) -> dict:
    resp = client.post("/v1/enterprise/login/start")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"], body
    assert body["authorize_url"].startswith("http://127.0.0.1:")
    state = fake.authorize_requests[body["login_txn_id"]]["state"]
    assert state
    return {"txn_id": body["login_txn_id"], "state": state}


def _finish(client: TestClient, fake: FakeOpc, start: dict) -> TestClient:
    code = fake.approve_login(start["txn_id"])
    return client.get(
        f"/v1/enterprise/oauth/callback?code={code}&state={start['state']}"
    )


def _login(client: TestClient, fake: FakeOpc) -> str:
    """Drive a full successful login; returns the one-time refresh handoff."""
    start = _start(client, fake)
    resp = _finish(client, fake, start)
    assert resp.status_code == 200, resp.text
    result = client.get(f"/v1/enterprise/login/result?login_txn_id={start['txn_id']}")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["state"] == "succeeded", body
    refresh = body.get("refresh_token")
    assert refresh and refresh.startswith("opc-refresh-")
    return refresh


# -- happy path -----------------------------------------------------------------


def test_start_callback_success_and_session(fake_opc, tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, platform=fake_opc.base_url)
    start = _start(client, fake_opc)

    resp = _finish(client, fake_opc, start)
    assert resp.status_code == 200
    assert "Enterprise sign-in complete" in resp.text

    # The GUI polls the result; the refresh token hands off EXACTLY once.
    result = client.get(f"/v1/enterprise/login/result?login_txn_id={start['txn_id']}")
    body = result.json()
    assert body["state"] == "succeeded"
    assert body["context"]["subject"] == fake_opc.subject
    assert body["context"]["tenant"] == fake_opc.tenant
    assert body["context"]["generation"] == fake_opc.generation
    first_handoff = body["refresh_token"]
    second = client.get(f"/v1/enterprise/login/result?login_txn_id={start['txn_id']}").json()
    assert "refresh_token" not in second  # one-time presentation

    # INT-101f view: identity + versions, never the access token.
    session = client.get("/v1/enterprise/session")
    assert session.status_code == 200
    view = session.json()
    assert view["subject"] == fake_opc.subject
    assert view["authz_version"] == fake_opc.authz_version
    assert "access_token" not in view
    assert "refresh_token" not in view


def test_login_start_requires_loopback_redirect(fake_opc, tmp_path, monkeypatch):
    # The TestClient base_url is 127.0.0.1, so the authorize call validates the
    # redirect_uri the service derives from the request — a Host-spoofed start
    # would fail at the platform. Here we assert the platform SAW the loopback
    # redirect: the flow succeeded, so the URI passed the platform's 127.0.0.1
    # gate (enforced in fake.authorize).
    client = _client(tmp_path, monkeypatch, platform=fake_opc.base_url)
    start = _start(client, fake_opc)
    req = fake_opc.authorize_requests[start["txn_id"]]
    assert req["redirect_uri"] == "http://127.0.0.1:8765/callback"
    assert req["code_challenge_method"] == "S256"
    assert req["code_challenge"]  # PKCE challenge present


# -- state: wrong / replay --------------------------------------------------------


def test_callback_unknown_state_rejected(fake_opc, tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, platform=fake_opc.base_url)
    resp = client.get("/v1/enterprise/oauth/callback?code=whatever&state=bogus")
    assert resp.status_code == 400
    assert "Enterprise sign-in failed" in resp.text


def test_callback_state_replay_rejected(fake_opc, tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, platform=fake_opc.base_url)
    start = _start(client, fake_opc)
    assert _finish(client, fake_opc, start).status_code == 200
    # Reusing the consumed state must not establish a second session.
    replay = client.get(
        f"/v1/enterprise/oauth/callback?code=again&state={start['state']}"
    )
    assert replay.status_code == 400
    session = client.get("/v1/enterprise/session")
    assert session.status_code == 200  # original login still the only one


# -- platform error passthrough (BND-180) -------------------------------------------


def test_platform_error_code_passthrough(fake_opc, tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, platform=fake_opc.base_url)
    start = _start(client, fake_opc)
    fake_opc.fail_next_token("SUBJECT_NOT_PROVISIONED", http_status=403)
    resp = _finish(client, fake_opc, start)
    assert resp.status_code == 400
    result = client.get(f"/v1/enterprise/login/result?login_txn_id={start['txn_id']}")
    assert result.status_code == 404  # failed terminal state
    body = result.json()
    assert body["state"] == "failed"
    assert body["error"]["code"] == "SUBJECT_NOT_PROVISIONED"
    # Nothing half-established.
    assert client.get("/v1/enterprise/session").status_code == 404


def test_platform_unreachable_is_upstream_unavailable(tmp_path, monkeypatch):
    # A port with nothing listening: the 15s-timeout path collapses to 502
    # UPSTREAM_UNAVAILABLE without a hang (connect refusal is instant).
    monkeypatch.setenv("OPC_PLATFORM_BASE", "http://127.0.0.1:1")
    client = _client(tmp_path, monkeypatch, platform="http://127.0.0.1:1")
    resp = client.post("/v1/enterprise/login/start")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"


def test_not_configured(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, platform=None)
    resp = client.post("/v1/enterprise/login/start")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "ENTERPRISE_NOT_CONFIGURED"


# -- refresh: rotation + reuse detection (BND-102) -----------------------------------


def test_refresh_rotation(fake_opc, tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, platform=fake_opc.base_url)
    refresh = _login(client, fake_opc)
    resp = client.post("/v1/enterprise/session/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"]
    rotated = body["refresh_token"]
    assert rotated and rotated != refresh
    # The new refresh works; the platform rotated the old one out.
    again = client.post("/v1/enterprise/session/refresh", json={"refresh_token": rotated})
    assert again.status_code == 200


def test_refresh_reuse_detected_clears_context(fake_opc, tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, platform=fake_opc.base_url)
    refresh = _login(client, fake_opc)
    rotated = (
        client.post("/v1/enterprise/session/refresh", json={"refresh_token": refresh})
        .json()["refresh_token"]
    )
    # Presenting the OLD (rotated-out) refresh: platform revokes the family and
    # the sidecar clears its local enterprise context.
    reuse = client.post("/v1/enterprise/session/refresh", json={"refresh_token": refresh})
    assert reuse.status_code == 401
    assert reuse.json()["error"]["code"] == "REFRESH_REUSE_DETECTED"
    assert client.get("/v1/enterprise/session").status_code == 404
    # Even the legitimately rotated refresh is dead now (family revoked).
    assert (
        client.post("/v1/enterprise/session/refresh", json={"refresh_token": rotated}).status_code
        == 401
    )


def test_refresh_requires_token(fake_opc, tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, platform=fake_opc.base_url)
    resp = client.post("/v1/enterprise/session/refresh", json={"refresh_token": ""})
    assert resp.status_code == 502  # mapped upstream-style failure, never a crash
    assert resp.json()["error"]["code"] == "AUTH_STATE_MISMATCH"


# -- logout (INT-101e) -----------------------------------------------------------------


def test_logout_revokes_and_clears(fake_opc, tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, platform=fake_opc.base_url)
    refresh = _login(client, fake_opc)
    resp = client.post("/v1/enterprise/logout", json={"refresh_token": refresh})
    assert resp.status_code == 200
    assert resp.json()["ok"]
    assert client.get("/v1/enterprise/session").status_code == 404
    assert fake_opc.logouts, "platform logout endpoint was not called"
    assert fake_opc.logouts[0]["refresh_token"] == refresh


# -- context isolation (BND-120) --------------------------------------------------------


def test_context_switch_orphans_old_context(fake_opc, tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, platform=fake_opc.base_url)
    _login(client, fake_opc)

    # A different subject (account switch) establishes a new context.
    fake_opc.subject = "subject-someone-else"
    _login(client, fake_opc)

    session = client.get("/v1/enterprise/session").json()
    assert session["subject"] == "subject-someone-else"
    service = create_app_service(client)
    old_key = f"https://opc.example.com|subject-yangjing|{fake_opc.tenant}|1"
    assert old_key in service.orphaned_context_keys
    # Only one live context, and it is the new one.
    assert service.context is not None
    assert service.context.subject == "subject-someone-else"


def create_app_service(client: TestClient):
    return client.app.state.enterprise


# -- BND-160: no refresh anywhere it must not be -------------------------------------------


def test_no_refresh_token_persistence(fake_opc, tmp_path, monkeypatch, caplog):
    client = _client(tmp_path, monkeypatch, platform=fake_opc.base_url)
    refresh = _login(client, fake_opc)
    rotated = (
        client.post("/v1/enterprise/session/refresh", json={"refresh_token": refresh})
        .json()["refresh_token"]
    )

    # 1. secrets.json (the SecretStore file) holds no enterprise profile/values.
    manager_store = client.app.state.manager.secrets
    for profile in manager_store.status():
        assert not profile["profile"].startswith("enterprise"), profile
    secrets_file = manager_store.path
    if secrets_file.is_file():
        text = secrets_file.read_text(encoding="utf-8")
        assert "opc-refresh-" not in text
        assert refresh not in text and rotated not in text

    # 2. Nothing in the sidecar's enterprise store is persisted: a fresh
    #    instance holds nothing (KeychainStore semantics).
    from coworker.enterprise import KeychainStore

    fresh = KeychainStore()
    assert fresh.profiles() == []

    # 3. The logs never carry the refresh values (caplog captures the sidecar
    #    loggers for the whole test).
    for value in (refresh, rotated):
        assert value not in caplog.text


# -- sidecar token gate (BND-163) -------------------------------------------------------------


def test_enterprise_routes_respect_sidecar_token(fake_opc, tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, platform=fake_opc.base_url, token="sidecar-secret")

    # Everything except the loopback callback demands the token.
    denied = client.get("/v1/enterprise/session")
    assert denied.status_code == 401
    denied = client.post("/v1/enterprise/login/start")
    assert denied.status_code == 401

    # The callback is tokenless by design: it reaches the handler (a wrong
    # state is a 400 from the app, NOT the middleware's 401).
    resp = client.get("/v1/enterprise/oauth/callback?code=x&state=y")
    assert resp.status_code == 400
    assert "missing or invalid OpenWorker sidecar token" not in resp.text

    # With the token the full flow works.
    authed = TestClient(
        client.app,
        base_url="http://127.0.0.1:8765",
        headers={"x-openworker-token": "sidecar-secret"},
    )
    refresh = _login(authed, fake_opc)
    assert refresh


# -- session expiry hints (BND-102/160: refresh before the access dies) -------------


def test_session_view_reports_expiry_hints(fake_opc, tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, platform=fake_opc.base_url)
    _login(client, fake_opc)
    view = client.get("/v1/enterprise/session").json()
    assert view["refresh_recommended"] is False
    assert 0 < view["seconds_until_expiry"] <= 900

    # Inside the leeway window the view asks the GUI for a refresh.
    service = create_app_service(client)
    service.context.obtained_at -= 900 - 30  # 30s of lifetime left
    view = client.get("/v1/enterprise/session").json()
    assert view["seconds_until_expiry"] <= 30
    assert view["refresh_recommended"] is True

    # Past expiry the hint is unambiguous (≤ 0).
    service.context.obtained_at -= 900
    view = client.get("/v1/enterprise/session").json()
    assert view["seconds_until_expiry"] <= 0
    assert view["refresh_recommended"] is True


def test_refresh_resets_the_expiry_hint(fake_opc, tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, platform=fake_opc.base_url)
    refresh = _login(client, fake_opc)
    service = create_app_service(client)
    service.context.obtained_at -= 900  # access is dead
    assert service.context.access_expired()
    resp = client.post("/v1/enterprise/session/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 200
    view = client.get("/v1/enterprise/session").json()
    assert view["refresh_recommended"] is False
    assert view["seconds_until_expiry"] > 800


# -- refresh-token memory hygiene (BND-160) -------------------------------------------


def test_refresh_handoff_clears_the_sidecar_copy(fake_opc, tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, platform=fake_opc.base_url)
    start = _start(client, fake_opc)
    assert _finish(client, fake_opc, start).status_code == 200
    service = create_app_service(client)
    txn = service._txns[start["txn_id"]]
    assert txn._refresh_token is not None  # minted at exchange, not yet handed off

    result = client.get(f"/v1/enterprise/login/result?login_txn_id={start['txn_id']}").json()
    assert result["refresh_token"]
    assert txn._refresh_token is None  # cleared in the same breath as the handoff

    again = client.get(f"/v1/enterprise/login/result?login_txn_id={start['txn_id']}").json()
    assert "refresh_token" not in again  # still one-time


def test_terminal_transactions_are_swept_on_next_login(fake_opc, tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, platform=fake_opc.base_url)
    start = _start(client, fake_opc)
    assert _finish(client, fake_opc, start).status_code == 200
    service = create_app_service(client)
    assert service._txns[start["txn_id"]].status == "succeeded"

    # A new login start sweeps terminal/expired transactions (and any state
    # keys pointing at them); the current one is always polled BEFORE that.
    nxt = client.post("/v1/enterprise/login/start")
    assert nxt.status_code == 200
    assert start["txn_id"] not in service._txns
    assert all(t._refresh_token is None for t in service._txns.values())
    # The swept transaction's one-time state no longer resolves.
    assert start["state"] not in service._by_state
