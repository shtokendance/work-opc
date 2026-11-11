"""Tests for OPC platform configuration endpoints and dynamic hot-reloading."""

from __future__ import annotations

import json
import pytest
from fastapi.testclient import TestClient

from coworker.config import load_config
from coworker.secrets import state_dir
from coworker.server import SessionManager, create_app
from coworker.server.app_delivery_routes import default_platform_url


def _client(tmp_path, monkeypatch, *, token: str = "") -> TestClient:
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    if token:
        monkeypatch.setenv("COWORKER_API_TOKEN", token)
    manager = SessionManager(workspace=tmp_path, provider=None)
    return TestClient(create_app(manager), base_url="http://127.0.0.1:8765")


def test_get_opc_config_defaults(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    resp = client.get("/v1/opc/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "platform_base" in data
    assert "effective_platform_base" in data
    assert "effective_deploy_base" in data
    assert "effective_web_origin" in data
    assert data["default_platform_base"] == "https://shtokendance.benlai.com"


def test_update_opc_config_and_hot_reload(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    new_platform = "https://opc.mycompany.com"
    new_deploy = "https://deploy.mycompany.com"
    new_web = "https://web.mycompany.com"

    resp = client.put(
        "/v1/opc/config",
        json={
            "platform_base": new_platform,
            "deploy_base": new_deploy,
            "web_origin": new_web,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["platform_base"] == new_platform
    assert data["deploy_base"] == new_deploy
    assert data["web_origin"] == new_web
    assert data["effective_platform_base"] == new_platform
    assert data["effective_deploy_base"] == new_deploy
    assert data["effective_web_origin"] == new_web

    # Verify config.toml was persisted
    cfg = load_config()
    assert cfg.opc_platform_base == new_platform
    assert cfg.opc_deploy_base == new_deploy
    assert cfg.opc_web_origin == new_web

    # Verify app-delivery platform_config.json was synced
    platform_json = state_dir() / "app-delivery" / "platform_config.json"
    assert platform_json.is_file()
    saved_cfg = json.loads(platform_json.read_text(encoding="utf-8"))
    assert saved_cfg["platform_url"] == new_deploy

    # Verify default_platform_url uses new deploy base
    assert default_platform_url() == new_deploy

    # In-memory enterprise_auth platform_base updated
    app = client.app
    assert app.state.enterprise.platform_base == new_platform


def test_update_opc_config_invalid_url(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    resp = client.put(
        "/v1/opc/config",
        json={"platform_base": "not-a-valid-url"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_URL"


def test_test_opc_connection_invalid_url(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    resp = client.post(
        "/v1/opc/config/test",
        json={"url": "ftp://bad-scheme.com"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_URL"


def test_test_opc_connection_success(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    # Testing loopback should connect or return an error gracefully
    resp = client.post(
        "/v1/opc/config/test",
        json={"url": "http://127.0.0.1:8765/v1/health"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "ok" in data
    assert "latency_ms" in data
