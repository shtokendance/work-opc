import base64
import io
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app


class DummyProvider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="done", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def test_app_endpoints(tmp_path: Path):
    manager = SessionManager(workspace=tmp_path, provider=DummyProvider())
    client = TestClient(create_app(manager))

    # 1. Create zip data
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("main.py", "print('demo')")
        zf.writestr(".env", "PORT=8080\nDB_URL=postgres://...\n")
    data_b64 = base64.b64encode(zip_buf.getvalue()).decode("utf-8")

    # 2. Transform app
    resp = client.post(
        "/v1/apps/transform",
        json={
            "slug": "demo-app",
            "name": "演示应用",
            "description": "测试用演示应用",
            "app_url": "http://demo-app.apps.opc.intra",
            "container_port": 8080,
            "data_b64": data_b64,
        },
    )
    assert resp.status_code == 200
    res = resp.json()
    assert res["ok"] is True
    assert res["slug"] == "demo-app"
    assert "postgres" in res["manifest"]["detected_resource_types"]

    # 3. Publish to platform (mock httpx post)
    from unittest.mock import MagicMock
    mock_response = MagicMock()
    mock_response.status_code = 202
    mock_response.json.return_value = {"status": "building", "slug": "demo-app"}

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        pub_resp = client.post(
            "/v1/apps/publish-to-platform",
            json={
                "slug": "demo-app",
                "platform_url": "http://mock-platform:3002",
                "app_config": [{"key": "PORT", "value": "8080"}],
                "resource_ids": ["shared-pg"],
            },
        )
        assert pub_resp.status_code == 200
        pub_res = pub_resp.json()
        assert pub_res["ok"] is True
        # Verify local skill was pre-installed in disabled state
        assert "app-demo-app" in manager.skill_store.disabled_names()

    # 4. Enable skill
    en_resp = client.post("/v1/apps/enable-skill", json={"slug": "demo-app"})
    assert en_resp.status_code == 200
    assert en_resp.json()["ok"] is True
    # Verify local skill is now enabled
    assert "app-demo-app" not in manager.skill_store.disabled_names()
