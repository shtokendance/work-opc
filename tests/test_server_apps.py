import base64
import asyncio
import io
import time
import threading
import zipfile
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from coworker.server import SessionManager, create_app
from test_app_delivery import ConfiguredProvider


def test_read_only_probe_retries_transport_and_gateway_failures():
    from coworker.server.app_delivery_routes import read_only_probe
    ok = (200, "application/json", b'{"code":0}')
    with patch("coworker.server.app_delivery_routes.delivery.execute", side_effect=[TimeoutError(), (503, "text/plain", b"starting"), ok]) as execute, patch("coworker.server.app_delivery_routes.asyncio.sleep", new_callable=AsyncMock) as sleep:
        assert asyncio.run(read_only_probe({}, "search-app", {})) == ok
        assert execute.call_count == 3 and sleep.await_count == 2


def test_read_only_probe_does_not_retry_credentials_or_business_responses():
    from coworker.server.app_delivery_routes import read_only_probe
    import pytest
    with patch("coworker.server.app_delivery_routes.delivery.execute", side_effect=ValueError("missing credential")) as execute:
        with pytest.raises(ValueError, match="missing credential"):
            asyncio.run(read_only_probe({}, "search-app", {}))
        assert execute.call_count == 1
    for result in [(401, "application/json", b'{}'), (200, "application/json", b'{"code":9}')]:
        with patch("coworker.server.app_delivery_routes.delivery.execute", return_value=result) as execute:
            assert asyncio.run(read_only_probe({}, "search-app", {})) == result
            assert execute.call_count == 1


def test_read_only_probe_reports_stage_without_leaking_transport_details():
    from coworker.server.app_delivery_routes import read_only_probe
    import pytest
    with patch("coworker.server.app_delivery_routes.delivery.execute", side_effect=OSError("credential-bearing private details")) as execute, patch("coworker.server.app_delivery_routes.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(ValueError, match="search-app 验证请求失败") as error:
            asyncio.run(read_only_probe({}, "search-app", {}))
        assert "private details" not in str(error.value)
        assert execute.call_count == 3


def test_app_approval_publish_and_verified_enable(tmp_path, monkeypatch):
    monkeypatch.setattr("coworker.server.app_delivery_routes.state_dir", lambda: tmp_path / "state")
    manager = SessionManager(workspace=tmp_path, provider=ConfiguredProvider())
    monkeypatch.setattr(manager, "_provider_configured", lambda _: True)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("main.py", "print('original')\n")
        z.writestr("Dockerfile", 'FROM python:3.11-alpine\nCOPY main.py /app.py\nCMD ["python","/app.py"]\n')
    app = create_app(manager)
    original_create = app.state.app_delivery.create
    worker_threads = []
    def observed_create(*args, **kwargs):
        import asyncio
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            worker_threads.append(threading.get_ident())
        else:
            raise AssertionError("ZIP parsing must not block the async server loop")
        return original_create(*args, **kwargs)
    monkeypatch.setattr(app.state.app_delivery, "create", observed_create)
    with TestClient(app) as client:
        response = client.post("/v1/apps/transform", json={"slug": "test-app", "name": "Test", "app_url": "http://test-app.apps.localhost", "data_b64": base64.b64encode(archive.getvalue()).decode()})
        assert response.status_code == 200, response.text
        assert worker_threads
        job_id = response.json()["job_id"]
        deadline = time.monotonic() + 5
        while True:
            job = client.get("/v1/apps/jobs/" + job_id).json()
            if job["status"] != "analyzing":
                break
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert job["status"] == "review", job
        # Retrying must create a task on the event loop, not a FastAPI worker thread.
        manager.provider.calls.clear()
        retried = client.post(f"/v1/apps/jobs/{job_id}/analyze", json={"feedback": "Keep every business route unchanged"})
        assert retried.status_code == 200, retried.text
        deadline = time.monotonic() + 5
        while True:
            job = client.get("/v1/apps/jobs/" + job_id).json()
            if job["status"] != "analyzing":
                break
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert job["status"] == "review", job
        assert not (app.state.app_delivery.path(job_id) / "bundle").exists()
        assert client.post(f"/v1/apps/jobs/{job_id}/approve", json={"proposal_hash": job["proposal_hash"], "approved_changes": ["C01"]}).status_code == 422
        ready = client.post(f"/v1/apps/jobs/{job_id}/approve", json={"proposal_hash": job["proposal_hash"], "approved_changes": ["C01"], "confirm_config_changes": True}).json()
        assert ready["status"] == "ready", ready
        fields = {"job_id": job_id, "artifact_hash": ready["artifact_hash"], "app_config": [], "resource_ids": []}
        assert client.post("/v1/apps/publish-to-platform", json=fields).status_code == 422
        confirmation = client.post(f"/v1/apps/jobs/{job_id}/confirm-deployment", json=fields).json()
        fields["confirmation_token"] = confirmation["confirmation_token"]
        response = httpx.Response(202, json={"slug": "test-app", "status": "building", "app_url": "http://test-app.apps.localhost"})
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response)):
            published = client.post("/v1/apps/publish-to-platform", json=fields)
        assert published.status_code == 200, published.text
        assert "app-test-app" in manager.skill_store.disabled_names()
        assert (manager.skill_store.global_dir / "app-test-app/.work-delivery-pending").is_file()
        downloaded = client.get(f"/v1/apps/jobs/{job_id}/artifacts/skill.zip")
        assert downloaded.status_code == 200
        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as skill:
            assert "http://test-app.apps.localhost" in skill.read("SKILL.md").decode()
        assert client.get(f"/v1/apps/jobs/{job_id}/artifacts/job.json").status_code == 404
        jobs = client.get("/v1/apps/jobs?slug=test-app").json()["jobs"]
        assert jobs[0]["artifacts"]["skill.zip"].endswith("bundle/skill.zip")

        summary = __import__("json").loads((app.state.app_delivery.path(job_id) / "bundle/summary.json").read_text())
        receipt = "\n".join("[Artifact] " + filename + ":" + summary["files"][filename] for filename in ("app.tar", "skill.zip"))
        running = httpx.Response(200, json={"status": "running", "app_url": "http://test-app.apps.localhost", "build_log": receipt}, request=httpx.Request("GET", "http://platform/apps/test-app"))
        def probe(contract, operation, *_args, **_kwargs):
            return 200, "application/json", b'{"service":"test-app"}' if operation == "health" else b'{"ok":true}'
        unrelated = httpx.Response(200, json={"status": "running", "app_url": "http://test-app.apps.localhost", "build_log": "old deployment"}, request=running.request)
        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=unrelated)):
            assert client.post("/v1/apps/enable-skill", json={"job_id": job_id}).status_code == 422
        assert "app-test-app" in manager.skill_store.disabled_names()
        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=running)), patch("coworker.server.app_delivery_routes.delivery.execute", side_effect=probe):
            enabled = client.post("/v1/apps/enable-skill", json={"job_id": job_id})
        assert enabled.status_code == 200, enabled.text
        assert "app-test-app" not in manager.skill_store.disabled_names()
        assert not (manager.skill_store.global_dir / "app-test-app/.work-delivery-pending").exists()
        manager.skill_store.set_enabled("app-test-app", False)
        installed = manager.skill_store.global_dir / "app-test-app/scripts/invoke_api.py"
        installed.write_text("changed after approval")
        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=running)), patch("coworker.server.app_delivery_routes.delivery.execute", side_effect=probe):
            assert client.post("/v1/apps/enable-skill", json={"job_id": job_id}).status_code == 422
        assert "app-test-app" in manager.skill_store.disabled_names()


def test_enable_without_delivery_confirmation_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("coworker.server.app_delivery_routes.state_dir", lambda: tmp_path / "state")
    manager = SessionManager(workspace=tmp_path, provider=ConfiguredProvider())
    with TestClient(create_app(manager)) as client:
        response = client.post("/v1/apps/enable-skill", json={"slug": "test-app"})
        assert response.status_code == 422
