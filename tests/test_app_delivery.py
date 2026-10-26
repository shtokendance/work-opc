import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from coworker.app_delivery import AppDelivery, fingerprint, redacted
from coworker.app_delivery_skill.scripts import delivery
from coworker.providers.base import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall


def test_config_redaction_preserves_field_names_without_credential_values():
    source = 'databases:\n  devops-db:\n    dsn: "reader:sensitive-value@tcp(db:3306)/test"\n    driver: mysql\n'
    result = redacted(source, "conf/config.yaml")
    assert '    dsn: "[REDACTED]"' in result
    assert "sensitive-value" not in result and "reader" not in result
    assert "    driver: mysql" in result
    assert len(result.splitlines()) == len(source.splitlines())
    assert redacted('PASSWORD="sensitive-value"', '.env') == 'PASSWORD= "[REDACTED]"'
    assert redacted('password := "sensitive-value"', 'main.go') == '[REDACTED]'


def proposal():
    contract = {"schema_version": 1, "slug": "test-app", "name": "Test", "description": "Lookup", "base_url": "http://test-app.apps.localhost", "runtime": {"language": "python", "version": "3.11", "port": 8080}, "build_context": ["Dockerfile", "main.py"], "health": {"path": "/healthz", "status": 200, "json_contains": {"service": "test-app"}}, "configs": [], "resources": [], "pages": [], "apis": [{"id": "lookup", "method": "GET", "path": "/lookup", "purpose": "Read data", "evidence": ["main.py:1"], "parameters": [], "request_body": None, "responses": {"200": {"description": "Lookup result"}}, "auth": {"type": "none"}, "effect": "read", "confirmation_required": False}], "verification": {"cases": [{"operation": "lookup", "json_contains": {"ok": True}}]}, "unresolved": []}
    return {"summary": "Deployment-only change", "issues": [{"id": "P01", "evidence": "main.py:1", "impact": "Listening", "recommendation": "Use configured port"}], "unresolved": [], "changes": [{"id": "C01", "path": "main.py", "reason": "Use PORT", "content": "print('adapted')\n"}], "contract": contract}


class ConfiguredProvider(ProviderClient):
    def __init__(self):
        self.calls = []

    def capabilities(self, model):
        return ModelCapabilities()

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls.append(model)
        if len(self.calls) == 1:
            return AssistantTurn(tool_calls=[ToolCall("read-1", "read_source", {"path": "main.py"})])
        if len(self.calls) == 2:
            return AssistantTurn(tool_calls=[ToolCall("submit-1", "submit_proposal", {"proposal_json": json.dumps(proposal())})])
        return AssistantTurn(text="Waiting for user approval")


@pytest.fixture
def service(tmp_path):
    provider = ConfiguredProvider()
    manager = SimpleNamespace(provider=provider, model="test:configured", _model_provider=lambda m: "test", _provider_configured=lambda p: True)
    return AppDelivery(manager, tmp_path / "jobs")


def create(service):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        z.writestr("main.py", "print('original')\n")
        z.writestr("Dockerfile", 'FROM python:3.11-alpine\nCOPY main.py /app.py\nCMD ["python","/app.py"]\n')
    return service.create({"name": "Test", "slug": "test-app", "app_url": "http://test-app.apps.localhost", "model": "test:configured"}, stream.getvalue())


async def analyzed(service):
    data = create(service)
    service.launch_analysis(data["id"])
    await service.tasks[data["id"]]
    result = service.public(data["id"])
    assert result["status"] == "review", result
    return result


def approved_body(job):
    return {"proposal_hash": job["proposal_hash"], "approved_changes": ["C01"], "confirm_config_changes": True}


@pytest.mark.asyncio
async def test_real_runtime_uses_selected_provider_and_never_writes_before_approval(service):
    job = await analyzed(service)
    assert len(service.manager.provider.calls) == 3
    assert set(service.manager.provider.calls) == {"test:configured"}
    assert (service.path(job["id"]) / "review/source/main.py").read_text() == "print('original')\n"
    assert "adapted" in job["proposal"]["changes"][0]["diff"]
    ready = await service.approve(job["id"], approved_body(job))
    assert ready["status"] == "ready", ready
    assert (service.path(job["id"]) / "review/source/main.py").read_text() == "print('original')\n"
    assert (service.path(job["id"]) / "approved/source/main.py").read_text() == "print('adapted')\n"


@pytest.mark.asyncio
async def test_every_file_and_all_configuration_changes_need_explicit_approval(service):
    job = await analyzed(service)
    for changes, confirmed in [([], True), (["C01"], False), (["C01", "unseen"], True)]:
        with pytest.raises(ValueError, match="批准"):
            await service.approve(job["id"], {**approved_body(job), "approved_changes": changes, "confirm_config_changes": confirmed})
    assert not (service.path(job["id"]) / "approved").exists()


def test_complete_proposal_cannot_omit_missing_build_files(service):
    job = create(service)
    data = service.load(job["id"])
    source = service.path(job["id"]) / "review/source"
    candidate = proposal()
    candidate["changes"] = []
    service.validate_proposal(data, source, candidate)  # unchanged deployable inputs remain valid
    (source / "Dockerfile").unlink()
    with pytest.raises(ValueError, match="构建|Dockerfile"):
        service.validate_proposal(data, source, candidate)
    candidate["unresolved"] = ["Need the owner's deployment decision"]
    service.validate_proposal(data, source, candidate)  # incomplete plans can still ask questions
    candidate["unresolved"] = []
    candidate["changes"] = [{"id": "C1", "path": "Dockerfile", "reason": "Container build", "content": "FROM python:3.11-alpine\n"}]
    service.validate_proposal(data, source, candidate)
    candidate["contract"]["build_context"].append("missing-launch.sh")
    with pytest.raises(ValueError, match="构建"):
        service.validate_proposal(data, source, candidate)


@pytest.mark.asyncio
async def test_source_drift_invalidates_approval(service):
    job = await analyzed(service)
    (service.path(job["id"]) / "review/source/main.py").write_text("changed externally")
    with pytest.raises(ValueError, match="源码"):
        await service.approve(job["id"], approved_body(job))


@pytest.mark.asyncio
async def test_config_and_resource_drift_invalidate_deployment_confirmation(service):
    job = await analyzed(service)
    job = await service.approve(job["id"], approved_body(job))
    body = {"artifact_hash": job["artifact_hash"], "app_config": [{"key": "EXTERNAL_SERVICE_URL", "value": "http://approved.internal"}], "resource_ids": []}
    token = service.confirm_deployment(job["id"], body)["confirmation_token"]
    service.verified_package(job["id"], {**body, "confirmation_token": token})
    for key in ["REDIS_URL", "MONGODB_URI", "DATABASE_URL", "EXTERNAL_SERVICE_URL"]:
        with pytest.raises(ValueError, match="确认已失效"):
            service.verified_package(job["id"], {**body, "confirmation_token": token, "app_config": [{"key": key, "value": "http://different.internal"}]})
    with pytest.raises(ValueError, match="未知"):
        service.confirm_deployment(job["id"], {**body, "resource_ids": ["unapproved-resource"]})


@pytest.mark.asyncio
async def test_artifact_or_summary_tampering_cannot_reuse_user_approval(service):
    job = await analyzed(service)
    job = await service.approve(job["id"], approved_body(job))
    body = {"artifact_hash": job["artifact_hash"], "app_config": [], "resource_ids": []}
    token = service.confirm_deployment(job["id"], body)["confirmation_token"]
    folder = service.path(job["id"]) / "bundle"
    (folder / "app.tar").write_bytes(b"unapproved archive")
    summary = delivery.load(folder / "summary.json")
    summary["files"]["app.tar"] = delivery.sha(b"unapproved archive")
    delivery.save(folder / "summary.json", summary)
    with pytest.raises(ValueError, match="清单摘要"):
        service.verified_package(job["id"], {**body, "confirmation_token": token})


def test_bundled_invoker_matches_canonical_python_source():
    root = Path(__file__).parents[1] / "coworker/app_delivery_skill"
    assert (root / "assets/invoke_api.py.txt").read_bytes() == (root / "scripts/invoke_api.py").read_bytes()


def test_plan_cannot_modify_slug_url_port_or_agent_policy(service):
    data = create(service)
    for path in ["../../outside", ".git/config", ".coworker/skills/custom/SKILL.md"]:
        p = proposal()
        p["changes"][0]["path"] = path
        with pytest.raises(ValueError):
            service.validate_proposal(data, service.path(data["id"]) / "review/source", p)


def test_bad_zip_and_invalid_inputs_do_not_leave_orphaned_jobs(service):
    payload = {"name": "Test", "slug": "test-app", "app_url": "http://test-app.apps.localhost"}
    for fields in [{"container_port": 0}, {"container_port": True}, {"slug": "../escape"}, {"name": ""}]:
        with pytest.raises(ValueError):
            service.create({**payload, **fields}, b"invalid")
    with pytest.raises(zipfile.BadZipFile):
        service.create(payload, b"not a zip")
    assert list(service.root.iterdir()) == []


@pytest.mark.asyncio
async def test_cancellation_preserves_original_source_and_prevents_approval(service):
    job = create(service)
    service.launch_analysis(job["id"])
    task = service.tasks[job["id"]]
    service.cancel(job["id"])
    with pytest.raises(asyncio.CancelledError):
        await task
    assert service.public(job["id"])["status"] == "cancelled"
    assert (service.path(job["id"]) / "review/source/main.py").read_text() == "print('original')\n"
    assert not (service.path(job["id"]) / "approved").exists()
