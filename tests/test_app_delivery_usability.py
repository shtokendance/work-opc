import pytest

from coworker.app_delivery import fingerprint
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


@pytest.mark.asyncio
async def test_public_exposes_resources_and_never_bypasses_unresolved(service):
    job = await analyzed(service)
    data = service.load(job["id"])
    data["resources"] = [{"id": "pg-main", "name": "平台共享 PostgreSQL", "type": "postgres", "env_keys": ["DATABASE_URL"]}]
    data["proposal"]["unresolved"] = ["是否仅作为内网试点使用？"]
    data["proposal"]["contract"]["unresolved"] = ["是否仅作为内网试点使用？"]
    data["proposal"]["contract"]["resources"] = [{"id": "db", "type": "postgres", "purpose": "业务库", "env": ["DATABASE_URL"]}]
    data["proposal_hash"] = fingerprint(data["proposal"])
    service.save(data)

    pub = service.public(job["id"])
    assert pub["resources"][0]["id"] == "pg-main"
    assert pub["proposal"]["detected_resources"][0]["type"] == "postgres"

    with pytest.raises(ValueError, match="待确认问题"):
        await service.approve(job["id"], approved_body(pub))

    with pytest.raises(ValueError, match="待确认问题"):
        await service.approve(job["id"], {**approved_body(pub), "ignore_unresolved": True})


@pytest.mark.asyncio
async def test_revise_packaged_app_archives_version_and_invalidates_upload(service):
    job = await analyzed(service)
    ready = await service.approve(job["id"], approved_body(job))
    assert service.skill_preview(job["id"])["contract"]["slug"] == job["slug"]
    service.confirm_deployment(job["id"], {"artifact_hash": ready["artifact_hash"], "app_config": [], "resource_ids": []})
    service.manager.provider.calls.clear()
    service.launch_analysis(job["id"], "保留原数据库")
    await service.tasks[job["id"]]
    current = service.load(job["id"])
    assert current["status"] == "review"
    assert current["revision"] == 2
    assert "confirmation_token" not in current and "artifact_hash" not in current
    assert (service.path(job["id"]) / "history/1/bundle/skill.zip").exists()
    assert current["conversation"][-2]["text"] == "保留原数据库"
    with pytest.raises(ValueError, match="尚未生成"):
        service.skill_preview(job["id"])

@pytest.mark.asyncio
async def test_skill_preview_rejects_tampering_and_config_draft_survives_reload(service):
    job = await analyzed(service)
    ready = await service.approve(job["id"], approved_body(job))
    configs = [{"key": "PORT", "value": "8080"}]
    service.save_config_draft(job["id"], {"artifact_hash":ready["artifact_hash"],"configs":configs})
    assert service.public(job["id"])["config_draft"]["configs"] == configs
    with pytest.raises(ValueError, match="版本"):
        service.save_config_draft(job["id"], {"artifact_hash":"stale","configs":configs})
    (service.path(job["id"]) / "bundle/skill.zip").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="发生变化"):
        service.skill_preview(job["id"])

@pytest.mark.asyncio
async def test_technical_preparation_is_not_fabricated_human_approval(service):
    import json
    job = await analyzed(service)
    ready = await service.approve(job["id"], {"proposal_hash": job["proposal_hash"]}, technical_preparation=True)
    assert ready["status"] == "ready"
    data = service.load(job["id"])
    assert "confirmation_token" not in data and not data.get("published")
    review = json.loads((service.path(job["id"]) / "approved/review.json").read_text())
    assert review["kind"] == "ai-technical" and review["scope"] == "deployment-adaptation"
    assert (service.path(job["id"]) / "review/source/main.py").read_text() == "print('original')\n"

@pytest.mark.asyncio
async def test_technical_preparation_cannot_skip_connection_decisions(service):
    job = await analyzed(service)
    data = service.load(job["id"])
    data["proposal"]["contract"]["unresolved"] = ["请选择替换电脑数据库的连接"]
    data["proposal_hash"] = fingerprint(data["proposal"])
    service.save(data)
    assert service.public(job["id"])["proposal"]["unresolved"]
    with pytest.raises(ValueError, match="待确认问题"):
        await service.approve(job["id"], {"proposal_hash":data["proposal_hash"]}, technical_preparation=True)

@pytest.mark.asyncio
async def test_deployment_requires_resolution_of_loopback_connection(service):
    job = await analyzed(service)
    ready = await service.approve(job["id"], {"proposal_hash":job["proposal_hash"]}, technical_preparation=True)
    item = {"key":"DATABASE_URL", "value":"mysql://fixture@127.0.0.1/app", "value_source":"manual"}
    body = {"artifact_hash":ready["artifact_hash"], "app_config":[item], "resource_ids":[]}
    with pytest.raises(ValueError, match="连接仍指向"):
        service.confirm_deployment(job["id"], body)
    item['value'] = 'mysql://fixture@internal-db/app'
    assert service.confirm_deployment(job["id"], body)['confirmation_token']
    item['value'] = 'mysql://fixture@127.0.0.1/app'
    item['local_connection_confirmed'] = True
    assert service.confirm_deployment(job["id"], body)['confirmation_token']

@pytest.mark.asyncio
async def test_legacy_file_proposals_are_not_implicitly_authorized_for_automatic_changes(service):
    job = await analyzed(service)
    data = service.load(job["id"])
    data.pop("preparation_policy")
    service.save(data)
    with pytest.raises(ValueError, match="重新分析"):
        await service.approve(job["id"], {"proposal_hash":job["proposal_hash"]}, technical_preparation=True)

@pytest.mark.asyncio
async def test_handoff_snapshots_applied_version_and_preserves_user_decisions(service):
    from pathlib import Path
    job = await analyzed(service)
    data = service.load(job["id"])
    data["feedback"] = "保留原数据库，不迁移数据"
    service.save(data)
    await service.approve(job["id"], approved_body(job))
    first = service.handoff(job["id"])
    second = service.handoff(job["id"])
    assert first["source_path"] != second["source_path"]
    snapshot = Path(first["source_path"])
    assert (snapshot / "main.py").read_text() == "print('adapted')\n"
    assert "保留原数据库，不迁移数据" in first["prompt"]
    (snapshot / "main.py").write_text("external change")
    assert (service.path(job["id"]) / "approved/source/main.py").read_text() == "print('adapted')\n"
    assert (service.path(job["id"]) / "review/source/main.py").read_text() == "print('original')\n"

@pytest.mark.asyncio
async def test_preparation_reports_required_connection_problems_without_leaking_values(service):
    job = await analyzed(service)
    data = service.load(job["id"])
    data["proposal"]["contract"]["configs"] = [
        {"key":"DATABASE_URL","required":True,"secret":True,"default":""},
        {"key":"TOKEN","required":True,"secret":True,"default":""}]
    data["proposal_hash"] = fingerprint(data["proposal"])
    service.save(data)
    ready = await service.approve(job["id"], {"proposal_hash":data["proposal_hash"]}, technical_preparation=True)
    assert {p["key"] for p in ready["configuration_problems"]} == {"DATABASE_URL", "TOKEN"}
    configs = [{"key":"DATABASE_URL","value":"mysql://user:private@localhost/db"}, {"key":"TOKEN","value":"private"}]
    service.save_config_draft(job["id"], {"artifact_hash":ready["artifact_hash"],"configs":configs})
    problems = service.public(job["id"])["configuration_problems"]
    assert len(problems) == 1 and problems[0]["key"] == "DATABASE_URL"
    assert "private" not in str(problems)
    configs[0]["value"] = "mysql://user:private@10.0.0.5/db"
    service.save_config_draft(job["id"], {"artifact_hash":ready["artifact_hash"],"configs":configs})
    assert service.public(job["id"])["configuration_problems"] == []
    assert ready["preparation_checks"][-1]["status"] == "pending"

@pytest.mark.asyncio
async def test_decision_choices_cannot_remove_their_unresolved_question(service):
    job = await analyzed(service)
    data = service.load(job["id"])
    proposal = data["proposal"]
    proposal["decisions"] = [{"id":"db","question":"切换数据库？","impact":"不会迁移数据","options":["保留原值","换服务器数据库"]}]
    source = service.path(job["id"]) / "review/source"
    with pytest.raises(ValueError, match="关联未解决问题"):
        service.validate_proposal(data, source, proposal)
    proposal["unresolved"] = ["切换数据库？"]
    service.validate_proposal(data, source, proposal)
    data["proposal_hash"] = fingerprint(proposal)
    service.save(data)
    with pytest.raises(ValueError, match="待确认问题"):
        await service.approve(job["id"], {"proposal_hash":data["proposal_hash"]}, technical_preparation=True)

@pytest.mark.asyncio
async def test_reimport_retains_user_decisions_but_never_old_verification(service):
    job = await analyzed(service)
    data = service.load(job["id"])
    data["feedback"] = "保留原数据库"
    data["conversation"] = [{"role":"user","text":"保留原数据库"}, {"role":"assistant","text":"已验证"}]
    data["verification"] = {"status":"passed"}
    service.save(data)
    archive = (service.path(job["id"]) / "uploaded.zip").read_bytes()
    imported = service.create({"name":"Test", "slug":job["slug"], "app_url":job["app_url"], "previous_job_id":job["id"]}, archive)
    assert imported["feedback"] == "保留原数据库"
    assert imported["conversation"] == [{"role":"user","text":"保留原数据库"}]
    assert imported["status"] == "created"
    assert not imported.get("verification") and not imported.get("artifact_hash")
    with pytest.raises(ValueError, match="保持原应用"):
        service.create({"name":"Other", "slug":"other-app", "app_url":job["app_url"], "previous_job_id":job["id"]}, archive)

@pytest.mark.asyncio
async def test_followup_keeps_question_context_for_short_natural_answers(service):
    job = await analyzed(service)
    data = service.load(job["id"])
    data["proposal"]["unresolved"] = ["数据库怎么连接？"]
    data["proposal"]["decisions"] = [{"id":"db","question":"数据库怎么连接？","impact":"需要明确目标","options":["保留原值","使用测试库"]}]
    data["proposal_hash"] = fingerprint(data["proposal"])
    service.save(data)
    service.manager.provider.calls.clear()
    service.launch_analysis(job["id"], "保留原值")
    await service.tasks[job["id"]]
    previous = service.previous_plan(job["id"])
    assert previous["unresolved"] == ["数据库怎么连接？"]
    assert previous["decisions"][0]["options"] == ["保留原值", "使用测试库"]
    assert service.load(job["id"])["feedback"] == "保留原值"
