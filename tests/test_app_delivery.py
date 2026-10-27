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
    assert len(service.manager.provider.calls) == 2
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


@pytest.mark.asyncio
async def test_original_values_survive_redaction_and_approval_without_entering_artifacts(service):
    from coworker.app_delivery_config import original_fields
    data = create(service)
    source = service.path(data["id"]) / "review/source"
    (source / "config.yaml").write_text('db:\n  password: "OPC_TEST_KEEP_PASSWORD"\n  port: 3306\nredis:\n  url: "redis://original.internal:6379"\n')
    files, _ = delivery.inspect_source(source)
    inv = delivery.load(source.parent / "inventory.json")
    inv["files"] = files
    delivery.save(source.parent / "inventory.json", inv)
    plan = proposal()
    for key, keys, secret in [("DB_PASSWORD", ["db", "password"], True), ("DB_PORT", ["db", "port"], False), ("REDIS_URL", ["redis", "url"], False)]:
        plan["contract"]["configs"].append({"key": key, "source": {"path": "config.yaml", "keys": keys}, "default": "", "required": True, "secret": secret})
    data.update(status="review", proposal=plan, proposal_hash=fingerprint(plan))
    service.save(data)
    assert "OPC_TEST_KEEP_PASSWORD" not in json.dumps(original_fields(source, "config.yaml"))
    before = service.public(data["id"])
    assert before["proposal"]["configs"][0]["has_original_value"]
    assert "OPC_TEST_KEEP_PASSWORD" not in json.dumps(before)
    ready = await service.approve(data["id"], approved_body(before))
    assert ready["status"] == "ready", ready.get("error")
    runtime = {c["key"]: c["value"] for c in ready["runtime_configs"]}
    assert runtime == {"DB_PASSWORD": "OPC_TEST_KEEP_PASSWORD", "DB_PORT": "3306", "REDIS_URL": "redis://original.internal:6379"}
    for path in (service.path(data["id"]) / "bundle").iterdir():
        assert b"OPC_TEST_KEEP_PASSWORD" not in path.read_bytes()
    body = {"artifact_hash": ready["artifact_hash"], "app_config": ready["runtime_configs"], "resource_ids": []}
    token = service.confirm_deployment(data["id"], body)["confirmation_token"]
    service.verified_package(data["id"], {**body, "confirmation_token": token})
    assert service.load(data["id"])["deployment_confirmation"]["app_config"][0]["value"] == "OPC_TEST_KEEP_PASSWORD"


def test_rejects_erasing_a_masked_original_credential_or_unresolvable_source(service):
    data = create(service)
    source = service.path(data["id"]) / "review/source"
    (source / "config.yaml").write_text('password: "OPC_TEST_KEEP_PASSWORD"\n')
    candidate = proposal()
    candidate["changes"].append({"id": "C02", "path": "config.yaml", "reason": "Runtime injection", "content": 'password: ""\n'})
    with pytest.raises(ValueError, match="不能清空"):
        service.validate_proposal(data, source, candidate)
    candidate["contract"]["configs"] = [{"key": "PASSWORD", "secret": True, "required": True, "default": "", "source": {"path": "config.yaml", "keys": ["password"]}}]
    service.validate_proposal(data, source, candidate)
    candidate["contract"]["configs"][0]["source"]["keys"] = ["absent"]
    with pytest.raises(ValueError, match="找不到"):
        service.validate_proposal(data, source, candidate)


@pytest.mark.asyncio
async def test_analysis_model_sees_reference_metadata_but_not_original_credential(service):
    data = create(service)
    source = service.path(data["id"]) / "review/source"
    (source / "config.yaml").write_text('db:\n  password: "original-private-test-value"\n')
    plan = proposal()
    plan["contract"]["configs"] = [{"key": "PASSWORD", "secret": True, "required": True, "default": "", "source": {"path": "config.yaml", "keys": ["db", "password"]}}]
    observed = []

    def complete(*, model, messages, tools=None, **settings):
        observed.append(json.dumps(messages, default=str))
        if len(observed) == 1:
            return AssistantTurn(tool_calls=[ToolCall("read-config", "read_source", {"path": "config.yaml"})])
        if len(observed) == 2:
            return AssistantTurn(tool_calls=[ToolCall("submit-plan", "submit_proposal", {"proposal_json": json.dumps(plan)})])
        return AssistantTurn(text="等待确认")

    service.manager.provider.complete = complete
    service.launch_analysis(data["id"])
    await service.tasks[data["id"]]
    assert service.public(data["id"])["status"] == "review"
    assert "original-private-test-value" not in "\n".join(observed)
    assert "Original configuration references" in observed[-1]
    assert (source / "config.yaml").read_text().endswith('"original-private-test-value"\n')


@pytest.mark.asyncio
async def test_reanalysis_retains_previous_user_decisions(service):
    data = create(service)
    data["feedback"] = "保留原数据库和密钥，不使用替代资源"
    service.save(data)
    service.launch_analysis(data["id"], "补充：端口8080，不新增鉴权")
    task = service.tasks[data["id"]]
    saved = service.load(data["id"])["feedback"]
    assert "保留原数据库和密钥" in saved and "端口8080，不新增鉴权" in saved
    service.cancel(data["id"])
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_valid_proposal_does_not_require_an_extra_model_summary_request(service):
    original = service.manager.provider.complete

    def complete(**kwargs):
        if len(service.manager.provider.calls) >= 2:
            raise ConnectionError("Summary request unavailable")
        return original(**kwargs)

    service.manager.provider.complete = complete
    job = await analyzed(service)
    assert job["proposal_hash"] and job["status"] == "review"
    assert len(service.manager.provider.calls) == 2


def test_validated_proposal_checkpoint_survives_client_restart(service):
    data = create(service)
    plan = proposal()
    service.validate_proposal(data, service.path(data["id"]) / "review/source", plan)
    data.update(status="analyzing", proposal=plan, proposal_hash=fingerprint(plan))
    service.save(data)
    assert service.load(data["id"])["status"] == "review"
    assert not (service.path(data["id"]) / "approved").exists()


@pytest.mark.asyncio
async def test_structured_proposal_can_reference_separate_file_drafts_without_writing_source(service):
    calls = []
    plan = proposal()

    def complete(*, model, messages, tools=None, **settings):
        calls.append(model)
        if len(calls) == 1:
            schema = next(t["function"]["parameters"] for t in tools if t["function"]["name"] == "submit_proposal")
            assert "proposal_json" not in schema["properties"]
            assert schema["properties"]["contract"]["properties"]["slug"]["enum"] == ["test-app"]
            return AssistantTurn(tool_calls=[ToolCall("file-draft", "propose_file_change", plan["changes"][0])])
        if len(calls) == 2:
            native = {**plan, "changes": [{k: v for k, v in plan["changes"][0].items() if k != "content"}]}
            return AssistantTurn(tool_calls=[ToolCall("native-submit", "submit_proposal", native)])
        raise AssertionError("No summary request is required after a valid proposal")

    service.manager.provider.complete = complete
    data = create(service)
    service.launch_analysis(data["id"])
    await service.tasks[data["id"]]
    job = service.public(data["id"])
    assert job["status"] == "review", job.get("error")
    assert "adapted" in job["proposal"]["changes"][0]["diff"]
    assert (service.path(data["id"]) / "review/source/main.py").read_text() == "print('original')\n"
    ready = await service.approve(data["id"], approved_body(job))
    assert ready["status"] == "ready", ready.get("error")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_validation_rejection_is_repaired_in_the_same_analysis_context(service):
    calls = []
    plan = proposal()

    def complete(*, model, messages, tools=None, **settings):
        calls.append(model)
        if len(calls) == 1:
            return AssistantTurn(tool_calls=[ToolCall("draft", "propose_file_change", plan["changes"][0])])
        native = {**plan, "changes": [{k: v for k, v in plan["changes"][0].items() if k != "content"}]}
        if len(calls) == 2:
            native["contract"] = {**plan["contract"], "apis": ["lookup"]}
            return AssistantTurn(tool_calls=[ToolCall("bad-submit", "submit_proposal", native)])
        if len(calls) == 3:
            return AssistantTurn(text="Already submitted")
        if len(calls) == 4:
            assert "apis" in json.dumps(messages, default=str)
            return AssistantTurn(tool_calls=[ToolCall("corrected-submit", "submit_proposal", native)])
        raise AssertionError("Unexpected extra model call")

    service.manager.provider.complete = complete
    data = create(service)
    service.launch_analysis(data["id"])
    await service.tasks[data["id"]]
    job = service.public(data["id"])
    assert job["status"] == "review", job.get("error")
    assert len(calls) == 4
    assert any("正在修正" in event for event in job["events"])
    assert "adapted" in job["proposal"]["changes"][0]["diff"]
    assert (service.path(data["id"]) / "review/source/main.py").read_text() == "print('original')\n"


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
