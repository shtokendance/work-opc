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


@pytest.mark.asyncio
@pytest.mark.parametrize('always_invalid', [False, True])
async def test_packaging_validation_is_repaired_in_same_round_or_stops(service, always_invalid):
    from test_app_delivery import proposal, ConfiguredProvider
    from coworker.providers.base import AssistantTurn, ToolCall

    class RepairProvider(ConfiguredProvider):
        def complete(self, *, model, messages, tools=None, **settings):
            self.calls.append(model)
            candidate = proposal()
            if always_invalid or len(self.calls) == 1:
                candidate['contract']['apis'][0]['request_body'] = {'schema': {'type': 'object'}}
            return AssistantTurn(tool_calls=[ToolCall(f'submit-{len(self.calls)}', 'submit_proposal', candidate)])

    service.manager.provider = RepairProvider()
    job = create(service)
    service.launch_analysis(job['id'], '保留原值')
    await service.tasks[job['id']]
    result = service.load(job['id'])
    assert result['revision'] == 1
    assert result['feedback'] == '保留原值'
    assert (service.path(job['id']) / 'review/source/main.py').read_text() == "print('original')\n"
    if always_invalid:
        assert result['status'] == 'failed'
        assert '停止自动重试' in result['error']
        assert len(service.manager.provider.calls) == 6
    else:
        assert result['status'] == 'review'
        assert len(service.manager.provider.calls) == 2


def test_handoff_cannot_precede_analysis(service):
    job = create(service)
    with pytest.raises(ValueError, match="分析完成"):
        service.handoff(job["id"])


@pytest.mark.asyncio
async def test_phase_one_preserves_config_json_sqlite_in_app_but_not_skill_or_model(service, tmp_path):
    import json
    import sqlite3
    import tarfile
    import zipfile
    from test_app_delivery import proposal, ConfiguredProvider
    from coworker.providers.base import AssistantTurn, ToolCall

    original = tmp_path / 'original-app'
    original.mkdir()
    (original / 'main.py').write_text("print('original')\n")
    (original / 'Dockerfile').write_text('FROM python:3.11-alpine\nWORKDIR /app\nCOPY . .\nCMD ["python", "main.py"]\n')
    (original / '.env').write_text('PASSWORD=original-env-value\n')
    (original / 'data.json').write_text('{"passwordHash":"existing-account-hash","supplier":"original"}')
    with sqlite3.connect(original / 'business.sqlite') as db:
        db.execute('CREATE TABLE records (id INTEGER, value TEXT)')
        db.execute("INSERT INTO records VALUES (1, 'existing-business-record')")
    preserved = {name: (original / name).read_bytes() for name in ['.env', 'data.json', 'business.sqlite']}

    class DataProvider(ConfiguredProvider):
        def complete(self, *, model, messages, tools=None, **settings):
            self.calls.append(model)
            self.model_messages = repr(messages)
            if len(self.calls) == 1:
                return AssistantTurn(tool_calls=[ToolCall('env', 'read_source', {'path': '.env'}), ToolCall('data', 'read_source', {'path': 'data.json'})])
            candidate = proposal()
            candidate['contract']['build_context'].extend(preserved)
            return AssistantTurn(tool_calls=[ToolCall('submit', 'submit_proposal', candidate)])

    service.manager.provider = DataProvider()
    job = service.create({'name': 'Test', 'slug': 'test-app', 'app_url': 'http://test-app.apps.localhost'}, original)
    service.launch_analysis(job['id'], '原样保留所有配置和数据')
    await service.tasks[job['id']]
    result = service.public(job['id'])
    assert result['status'] == 'review'
    assert not result['proposal']['unresolved']
    assert 'original-env-value' not in service.manager.provider.model_messages
    assert 'existing-account-hash' not in service.manager.provider.model_messages
    ready = await service.approve(job['id'], {'proposal_hash': result['proposal_hash']}, technical_preparation=True)
    assert ready['status'] == 'ready'
    folder = service.path(job['id']) / 'bundle'
    with tarfile.open(folder / 'app.tar') as archive:
        for name, content in preserved.items():
            assert archive.extractfile(name).read() == content
            assert (original / name).read_bytes() == content
    with zipfile.ZipFile(folder / 'skill.zip') as skill:
        text = b'\n'.join(skill.read(name) for name in skill.namelist())
        assert b'original-env-value' not in text and b'existing-account-hash' not in text
        assert b'existing-business-record' not in text
    assert not json.loads((folder / 'app-manifest.json').read_text())['configs']


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


def test_npm_script_names_are_not_credentials_but_command_secrets_are():
    import json
    from coworker.app_delivery_skill.scripts import delivery
    assert delivery.secret_lines(json.dumps({'scripts': {'change-password': 'node scripts/change-password.mjs'}}, indent=2), 'package.json') == []
    assert delivery.secret_lines(json.dumps({'scripts': {'change-password': 'PASSWORD=real-secret node reset.mjs'}}, indent=2), 'package.json')
    assert delivery.secret_lines('{"password": "real-secret"}', 'package.json')
    assert not delivery.secret_lines('const field = isNew ? "new-password" : "current-password";', 'login.js')
    assert delivery.secret_lines('const config = ok ? {password: "real-secret"} : {};', 'login.js')
    for suffix in ('mjs', 'cjs'):
        assert not delivery.secret_lines('const token = randomUUID();\npasswordHash: hashPassword(body.password),\ncandidate.passwordHash === hashPassword(body.password)', 'server.' + suffix)
        assert delivery.secret_lines('const token = "real-secret";', 'server.' + suffix)


@pytest.mark.asyncio
async def test_preflight_catches_packaging_errors_without_mutating_source(service):
    import copy
    from coworker.app_delivery import AppDelivery
    job = await analyzed(service)
    work = service.path(job['id']) / 'review'
    source = work / 'source/main.py'
    before = source.read_bytes()
    proposal = copy.deepcopy(service.load(job['id'])['proposal'])
    proposal['contract']['apis'][0]['request_body'] = {'schema': {'type': 'object'}}
    with pytest.raises(ValueError, match='request body needs schema'):
        AppDelivery.preflight_proposal(work, proposal)
    proposal['contract']['apis'][0]['request_body']['required'] = False
    AppDelivery.preflight_proposal(work, proposal)
    proposal['changes'][0]['content'] = 'password = "actual-secret"\n'
    AppDelivery.preflight_proposal(work, proposal)  # original app credentials are no longer a packaging gate
    assert source.read_bytes() == before


@pytest.mark.asyncio
async def test_existing_cookie_auth_can_be_described_without_changing_application_login(service):
    from coworker.app_delivery_skill.scripts.invoke_api import build_request
    job = await analyzed(service)
    plan = service.load(job['id'])['proposal']
    contract = plan['contract']
    contract['apis'][0]['auth'] = {'type': 'header', 'header': 'Cookie', 'env': 'APP_SESSION_COOKIE'}
    service.preflight_proposal(service.path(job['id']) / 'review', plan)
    request = build_request(contract, 'lookup', {}, None, credential_env={'APP_SESSION_COOKIE': 'sid=fixture-session'})
    assert request.get_header('Cookie') == 'sid=fixture-session'
    assert request.full_url == 'http://test-app.apps.localhost/lookup'
    with pytest.raises(ValueError, match='missing credential'):
        build_request(contract, 'lookup', {}, None, credential_env={})
    with pytest.raises(ValueError, match='invalid header name/value'):
        build_request(contract, 'lookup', {}, None, credential_env={'APP_SESSION_COOKIE': 'sid=fixture\r\nHost: other'})


@pytest.mark.asyncio
async def test_ready_proposal_must_meet_actual_platform_health_route(service):
    job = await analyzed(service)
    data = service.load(job['id'])
    proposal = data['proposal']
    proposal['contract']['health'] = {'path': '/api/health', 'status': 200, 'json_contains': {'ok': True}}
    with pytest.raises(ValueError, match='部署平台固定检查 /healthz'):
        service.validate_proposal(data, service.path(job['id']) / 'review/source', proposal)


def test_probe_credentials_are_transient_and_scoped_to_declared_auth():
    from coworker.server.app_delivery_routes import verification_credentials
    config = [{'key': 'PORT', 'value': '8080'}]
    contract = {'apis': [{'auth': {'type': 'header', 'env': 'APP_SESSION_COOKIE'}}]}
    result = verification_credentials(contract, config, {'APP_SESSION_COOKIE': 'sid=test'})
    assert result == {'PORT': '8080', 'APP_SESSION_COOKIE': 'sid=test'}
    assert config == [{'key': 'PORT', 'value': '8080'}]
    for value in [{'PORT': 'evil'}, {'APP_SESSION_COOKIE': 'sid=x\r\nHost: evil'}, []]:
        with pytest.raises(ValueError, match='凭据无效'):
            verification_credentials(contract, config, value)


def test_business_probe_contains_records_without_requiring_whole_list_equality():
    from coworker.app_delivery_skill.scripts.delivery import subset
    actual = [{'id': 'OTHER'}, {'id': 'S-10021', 'name': 'original'}]
    assert subset(actual, [{'id': 'S-10021'}])
    assert not subset(actual, [{'id': 'MISSING'}])
    assert not subset(actual, [])
    assert subset([], [])
    assert not subset({'id': 'S-10021'}, [{'id': 'S-10021'}])
