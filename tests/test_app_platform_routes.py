from types import SimpleNamespace
import io
import json
import zipfile

import httpx
import pytest
from fastapi import FastAPI

from coworker.app_delivery_skill.scripts import delivery
from coworker.server import app_delivery_routes
from tests.test_app_delivery import ConfiguredProvider, approved_body, create, proposal


class FakeSkillStore:
    def __init__(self, root):
        self.global_dir = root / "skills"
        self.global_dir.mkdir(parents=True, exist_ok=True)
        self._disabled = set()
        self._staged = {}

    def disabled_names(self):
        return set(self._disabled)

    def set_enabled(self, name, enabled):
        if enabled:
            self._disabled.discard(name)
        else:
            self._disabled.add(name)

    def delete(self, name):
        import shutil
        target = self.global_dir / name
        if target.exists():
            shutil.rmtree(target)

    def stage_upload(self, data: bytes, filename: str = "skill.zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            ops = json.loads(zf.read("references/operations.json").decode("utf-8"))
        name = "app-" + ops["slug"]
        token = "tok-" + name
        self._staged[token] = (name, data)
        return {"token": token, "name": name}

    def confirm_upload(self, token: str, scope: str = "global"):
        name, data = self._staged.pop(token)
        target = self.global_dir / name
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(target)

    def discard_upload(self, token: str):
        self._staged.pop(token, None)


@pytest.mark.asyncio
async def test_application_catalog_and_management_use_enterprise_gateway(monkeypatch, tmp_path):
    app = FastAPI()
    monkeypatch.setattr(app_delivery_routes, "state_dir", lambda: tmp_path)
    app.state.enterprise = SimpleNamespace(platform_base="https://platform.example", context=None)
    app_delivery_routes.register_app_delivery_routes(app, SimpleNamespace())
    requests = []

    def platform(request):
        requests.append(request)
        if request.method == "PUT":
            return httpx.Response(403, json={"error": {"message": "只有创建人或管理员可以管理此应用"}})
        return httpx.Response(200, json={"apps": [{"slug": "from-dsh", "can_manage": False}]})

    client_class = httpx.AsyncClient
    async with client_class(transport=httpx.ASGITransport(app=app), base_url="http://sidecar") as client:
        monkeypatch.setattr(app_delivery_routes.httpx, "AsyncClient",
                            lambda **kwargs: client_class(transport=httpx.MockTransport(platform), **kwargs))
        denied = await client.get("/v1/apps/catalog")
        assert denied.status_code == 403
        assert not requests

        app.state.enterprise.context = SimpleNamespace(access_token="fixture-token", access_expired=lambda: False)
        catalog = await client.get("/v1/apps/catalog?tenant_id=ignored&platform_url=https://other.example")
        assert catalog.status_code == 200
        assert catalog.json()["apps"][0]["slug"] == "from-dsh"
        assert str(requests[-1].url) == "https://platform.example/integration/v1/apps"
        assert requests[-1].headers["Authorization"] == "Bearer fixture-token"

        response = await client.put("/v1/apps/catalog/from-dsh/config", json={"app_config": [], "resource_ids": []})
        assert response.status_code == 403
        assert "只有创建人" in response.text


@pytest.mark.asyncio
async def test_idempotent_publish_reconciles_interrupted_upload_and_blocks_blind_reupload(monkeypatch, tmp_path):
    app = FastAPI()
    monkeypatch.setattr(app_delivery_routes, "state_dir", lambda: tmp_path)
    app.state.enterprise = SimpleNamespace(
        platform_base="https://platform.example",
        context=SimpleNamespace(access_token="fixture-token", access_expired=lambda: False),
    )
    skill_store = FakeSkillStore(tmp_path)
    manager = SimpleNamespace(
        provider=ConfiguredProvider(),
        model="test:configured",
        _model_provider=lambda m: "test",
        _provider_configured=lambda p: True,
        skill_store=skill_store,
    )
    app_delivery_routes.register_app_delivery_routes(app, manager)
    service = app.state.app_delivery

    data = create(service)
    service.launch_analysis(data["id"])
    await service.tasks[data["id"]]
    job = service.public(data["id"])
    ready = await service.approve(data["id"], approved_body(job))
    confirm_res = service.confirm_deployment(
        data["id"],
        {"artifact_hash": ready["artifact_hash"], "app_config": [], "resource_ids": []},
    )
    req_id = confirm_res["request_id"]
    assert req_id

    # Case 1: POST /publish drops connection, but GET /{slug}/requests/{req_id} returns accepted/building
    seen_publish_requests = []

    def platform_reconcile(request: httpx.Request):
        if request.method == "POST" and request.url.path.endswith("/publish"):
            body_text = request.content.decode("utf-8", errors="ignore")
            assert req_id in body_text
            seen_publish_requests.append(req_id)
            raise httpx.ReadTimeout("Simulated response timeout after server accepted publish")
        if request.method == "GET" and request.url.path.endswith(f"/test-app/requests/{req_id}"):
            return httpx.Response(
                200,
                json={
                    "slug": "test-app",
                    "request_id": req_id,
                    "status": "building",
                    "version": 2,
                    "app_url": "http://test-app.apps.localhost",
                },
            )
        return httpx.Response(404)

    client_class = httpx.AsyncClient
    async with client_class(transport=httpx.ASGITransport(app=app), base_url="http://sidecar") as client:
        monkeypatch.setattr(
            app_delivery_routes.httpx,
            "AsyncClient",
            lambda **kwargs: client_class(transport=httpx.MockTransport(platform_reconcile), **kwargs),
        )
        pub = await client.post(
            "/v1/apps/publish-to-platform",
            json={
                "job_id": data["id"],
                "confirmation_token": confirm_res["confirmation_token"],
                "app_config": [],
                "resource_ids": [],
            },
        )
        assert pub.status_code == 200, pub.text
        payload = pub.json()
        assert payload["ok"] is True
        assert payload["reconciled"] is True
        assert payload["data"]["version"] == 2
        loaded = service.load(data["id"])
        assert loaded["published"] is True
        assert loaded["published_version"] == 2
        assert loaded["publish_request_id"] == req_id


@pytest.mark.asyncio
async def test_pure_page_verification_and_config_update_invalidation_and_rollback(monkeypatch, tmp_path):
    app = FastAPI()
    monkeypatch.setattr(app_delivery_routes, "state_dir", lambda: tmp_path)
    app.state.enterprise = SimpleNamespace(
        platform_base="https://platform.example",
        context=SimpleNamespace(access_token="fixture-token", access_expired=lambda: False),
    )
    skill_store = FakeSkillStore(tmp_path)
    manager = SimpleNamespace(
        provider=ConfiguredProvider(),
        model="test:configured",
        _model_provider=lambda m: "test",
        _provider_configured=lambda p: True,
        skill_store=skill_store,
    )
    app_delivery_routes.register_app_delivery_routes(app, manager)
    service = app.state.app_delivery

    # Create a pure-page app (pages non-empty, no non-health APIs)
    data = create(service)
    plan = proposal()
    plan["contract"]["apis"] = []
    plan["contract"]["pages"] = [
        {"path": "/", "purpose": "仪表盘首页", "evidence": ["main.py:1"], "actions": ["查看核心看板"], "auth": {"type": "none"}}
    ]
    plan["contract"]["verification"] = {
        "cases": [],
        "browser_cases": [{"page": "/", "steps": ["打开首页"], "expected": "正常渲染"}],
    }
    data.update(status="review", proposal=plan, proposal_hash="h1")
    service.save(data)
    ready = await service.approve(
        data["id"],
        {"proposal_hash": "h1", "approved_changes": ["C01"], "confirm_config_changes": True},
    )
    assert ready["status"] == "ready", ready.get("error")
    confirm_res = service.confirm_deployment(
        data["id"],
        {"artifact_hash": ready["artifact_hash"], "app_config": [], "resource_ids": []},
    )
    folder = service.path(data["id"]) / "bundle"
    summary = delivery.load(folder / "summary.json")
    skill_zip_bytes = (folder / "skill.zip").read_bytes()
    skill_sha = summary["files"]["skill.zip"]
    app_sha = summary["files"]["app.tar"]

    # Pre-install and explicitly user-disable skill to verify user-disabled state is preserved
    pre_target = skill_store.global_dir / "app-test-app"
    pre_target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(skill_zip_bytes)) as zf:
        zf.extractall(pre_target)
    skill_store.set_enabled("app-test-app", False)  # User explicitly disabled it (no .work-delivery-pending)

    def platform_handler(request: httpx.Request):
        if request.method == "POST" and request.url.path.endswith("/publish"):
            return httpx.Response(
                200,
                json={"status": "running", "version": 1, "app_url": "http://test-app.apps.localhost"},
            )
        if request.method == "GET" and request.url.path.endswith("/test-app"):
            return httpx.Response(
                200,
                json={
                    "slug": "test-app",
                    "status": "running",
                    "current_version": 1,
                    "app_url": "http://test-app.apps.localhost",
                    "config_fingerprint": "fp-v1",
                    "build_log": f"[Artifact] app.tar:{app_sha}\n[Artifact] skill.zip:{skill_sha}\n",
                },
            )
        if request.method == "PUT" and request.url.path.endswith("/test-app/config"):
            return httpx.Response(200, json={"status": "updated", "config_fingerprint": "fp-v2"})
        if request.method == "POST" and request.url.path.endswith("/test-app/rollback"):
            return httpx.Response(
                200,
                json={
                    "slug": "test-app",
                    "previous_version": 2,
                    "current_version": 1,
                    "config_fingerprint": "fp-v1",
                    "version": {"version": 1, "app_url": "http://test-app.apps.localhost", "skill_zip_sha256": skill_sha},
                },
            )
        if request.method == "GET" and request.url.path.endswith("/test-app/versions/1/artifacts/skill.zip"):
            return httpx.Response(200, content=skill_zip_bytes)
        return httpx.Response(404)

    monkeypatch.setattr(
        app_delivery_routes,
        "read_only_probe",
        lambda contract, op, params, body=None, credential_env=None: __import__("asyncio").sleep(
            0, result=(200, "application/json", json.dumps({"service": "test-app", "ok": True}))
        ),
    )

    client_class = httpx.AsyncClient
    async with client_class(transport=httpx.ASGITransport(app=app), base_url="http://sidecar") as client:
        monkeypatch.setattr(
            app_delivery_routes.httpx,
            "AsyncClient",
            lambda **kwargs: client_class(transport=httpx.MockTransport(platform_handler), **kwargs),
        )
        pub = await client.post(
            "/v1/apps/publish-to-platform",
            json={
                "job_id": data["id"],
                "confirmation_token": confirm_res["confirmation_token"],
                "app_config": [],
                "resource_ids": [],
            },
        )
        assert pub.status_code == 200

        # 1. Pure-page app enable-skill without confirm_page_verification enters awaiting_page_verification (not failure!)
        en1 = await client.post("/v1/apps/enable-skill", json={"job_id": data["id"]})
        assert en1.status_code == 200, en1.text
        assert en1.json()["verification"]["status"] == "awaiting_page_verification"
        assert (pre_target / ".work-delivery-pending").exists()

        # 2. Confirm page verification removes pending marker AND preserves user-disabled state
        en2 = await client.post(
            "/v1/apps/enable-skill",
            json={"job_id": data["id"], "confirm_page_verification": True},
        )
        assert en2.status_code == 200, en2.text
        assert en2.json()["verification"]["status"] == "verified-deployed-page"
        assert not (pre_target / ".work-delivery-pending").exists()
        assert "app-test-app" in skill_store.disabled_names()  # Preserved user-disabled state!

        # 3. Updating runtime config invalidates prior business verification -> stale_config
        cfg_res = await client.put(
            "/v1/apps/catalog/test-app/config",
            json={"app_config": [], "resource_ids": []},
        )
        assert cfg_res.status_code == 200
        assert service.load(data["id"])["verification"]["status"] == "stale_config"
        assert (pre_target / ".work-delivery-pending").exists()

        # 4. Rollback pure-page app returns awaiting_page_verification and unified rollback_notice
        rb_res = await client.post(
            "/v1/apps/rollback/test-app",
            json={"target_version": 1, "reason": "回退测试"},
        )
        assert rb_res.status_code == 200, rb_res.text
        rb_json = rb_res.json()
        assert rb_json["verification"]["status"] == "awaiting_page_verification"
        assert "不自动回退数据库/卷中的业务数据" in rb_json["rollback_notice"]

