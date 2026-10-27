import pytest

from test_app_delivery import service, analyzed, approved_body, create


def test_directory_input_is_a_snapshot_and_rejects_symlinks(service, tmp_path):
    source = tmp_path / "my-app"
    source.mkdir()
    (source / "main.py").write_text("print('original')\n")
    (source / ".git").mkdir()
    (source / ".git/private").write_text("not source")
    payload = {"name": "Test", "slug": "test-app", "app_url": "http://test-app.apps.localhost"}
    job = service.create(payload, source)
    snapshot = service.path(job["id"]) / "review/source"
    assert (snapshot / "main.py").read_text() == "print('original')\n"
    assert not (snapshot / ".git").exists()
    (snapshot / "main.py").write_text("changed copy")
    assert (source / "main.py").read_text() == "print('original')\n"
    (source / "link.py").symlink_to(source / "main.py")
    with pytest.raises(ValueError, match="symlink"):
        service.create(payload, source)


@pytest.mark.asyncio
async def test_handoff_contains_targets_and_evidence_but_not_runtime_values(service):
    job = await analyzed(service)
    folder = service.path(job["id"])
    source = folder / "review/source/main.py"
    before = source.read_bytes()
    data = service.load(job["id"])
    data["deployment_confirmation"] = {"app_config": [{"key": "PASSWORD", "value": "private-runtime-only"}]}
    service.save(data)
    result = service.handoff(job["id"])["prompt"]
    assert job["app_url"] in result and "8080" in result
    assert "main.py" in result and "MongoDB" in result and "重新选择源码" in result
    assert "private-runtime-only" not in result
    assert source.read_bytes() == before
    assert service.load(job["id"])["status"] == "review"


def test_handoff_cannot_precede_analysis(service):
    job = create(service)
    with pytest.raises(ValueError, match="分析完成"):
        service.handoff(job["id"])


@pytest.mark.asyncio
async def test_field_resource_choice_does_not_implicitly_fill_other_required_fields(service):
    job = await analyzed(service)
    await service.approve(job["id"], approved_body(job))
    data = service.load(job["id"])
    data["resources"] = [{"id": "mysql", "env_keys": ["DB_DSN", "API_TOKEN"]}]
    data["proposal"]["contract"]["configs"] = [{"key": "DB_DSN", "required": True}, {"key": "API_TOKEN", "required": True}]
    service.save(data)
    body = {"artifact_hash": data["artifact_hash"], "resource_ids": ["mysql"], "app_config": [{"key": "DB_DSN", "value": "", "resource_id": "mysql"}, {"key": "API_TOKEN", "value": "", "value_source": "manual"}]}
    with pytest.raises(ValueError, match="API_TOKEN"):
        service.confirm_deployment(job["id"], body)
    body["app_config"][1]["value"] = "original-token"
    assert service.confirm_deployment(job["id"], body)["confirmation_token"]
    body["app_config"][0]["resource_id"] = "not-approved"
    with pytest.raises(ValueError, match="资源选择无效"):
        service.confirm_deployment(job["id"], body)
