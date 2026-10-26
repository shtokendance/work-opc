import json
import tarfile
import tempfile
import zipfile
from pathlib import Path

import pytest

from coworker.app_packager import (
    create_app_tar,
    create_skill_zip,
    enable_local_skill,
    extract_config_and_manifest,
    preinstall_local_skill_disabled,
    safe_extract_zip,
    transform_and_package_app,
)
from coworker.skills.store import SkillStore


def test_safe_extract_and_transform(tmp_path: Path):
    # 1. Create a sample app zip
    source_dir = tmp_path / "source_app"
    source_dir.mkdir()
    (source_dir / "main.py").write_text("print('hello')", encoding="utf-8")
    (source_dir / ".env.example").write_text("PORT=8080\nDB_HOST=10.0.0.1\nDATABASE_URL=postgres://...\n", encoding="utf-8")

    zip_file = tmp_path / "sample.zip"
    with zipfile.ZipFile(zip_file, "w") as zf:
        zf.writestr("app/main.py", (source_dir / "main.py").read_text())
        zf.writestr("app/.env.example", (source_dir / ".env.example").read_text())

    # 2. Transform and package
    res = transform_and_package_app(
        zip_path=zip_file,
        slug="test-expense",
        name="报销助手",
        description="内部报销审批",
        app_url="http://test-expense.apps.opc.intra",
        container_port=8080,
    )

    assert res["slug"] == "test-expense"
    manifest = res["manifest"]
    assert "postgres" in manifest["detected_resource_types"]
    assert any(c["key"] == "PORT" for c in manifest["configs"])

    # Verify app.tar
    app_tar = Path(res["app_tar_path"])
    assert app_tar.exists()
    with tarfile.open(app_tar, "r") as tar:
        names = tar.getnames()
        assert "Dockerfile" in names
        assert "captain-definition" in names
        assert "main.py" in names

    # Verify skill.zip
    skill_zip = Path(res["skill_zip_path"])
    assert skill_zip.exists()
    with zipfile.ZipFile(skill_zip, "r") as zf:
        assert "SKILL.md" in zf.namelist()
        assert "scripts/invoke_api.py" in zf.namelist()
        skill_md = zf.read("SKILL.md").decode("utf-8")
        assert "name: app-test-expense" in skill_md
        assert "http://test-expense.apps.opc.intra" in skill_md


def test_preinstall_and_enable_local_skill(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    store = SkillStore(global_dir=skills_dir)

    skill_zip = tmp_path / "skill.zip"
    with zipfile.ZipFile(skill_zip, "w") as zf:
        zf.writestr("SKILL.md", "---\nname: app-my-app\ndescription: demo app\n---\n# My App")
        zf.writestr("scripts/invoke_api.py", "#!/usr/bin/env python3\nprint('api')")

    # Pre-install in disabled state
    preinstall_local_skill_disabled(store, skill_zip, "my-app")
    assert "app-my-app" in store.disabled_names()

    # Enable once running
    enable_local_skill(store, "my-app")
    assert "app-my-app" not in store.disabled_names()


def test_devops_aly_yaml_secret_externalization_and_swagger_extraction(tmp_path: Path):
    source_dir = tmp_path / "devops_sample"
    (source_dir / "conf").mkdir(parents=True)
    (source_dir / "docs").mkdir(parents=True)
    (source_dir / "api" / "router").mkdir(parents=True)
    (source_dir / "cmd").mkdir(parents=True)

    (source_dir / "go.mod").write_text(
        "module gitlab.benlai.work/ops/devops-aly\n\ngo 1.22\n\nrequire github.com/geniussheep/ymir v1.1.7\n",
        encoding="utf-8",
    )
    (source_dir / "cmd" / "cmd.go").write_text(
        'package cmd\nfunc Execute() {\n\tInitFuncArray:  []func(){aly.Setup},\n}\n',
        encoding="utf-8",
    )
    (source_dir / "conf" / "config.yaml").write_text(
        """application:
  appId: "devops-aly"
  httpPort: 9090
databases:
  devops-db:
    dsn: 'db_admin:SecretPwd123@tcp(blsh-test.mysql.rds.aliyuncs.com:3306)/bl_devops'
    driver: 'mysql'
extend:
  pipeline:
    client:
      accessKeyId: "LTAI_FAKE_KEY_ID"
      accessKeySecret: "FAKE_SECRET_VALUE"
      orgId: "org-123"
""",
        encoding="utf-8",
    )
    (source_dir / "docs" / "swagger.json").write_text(
        json.dumps({
            "swagger": "2.0",
            "paths": {
                "/api/v1/pipeline/search/app": {
                    "get": {"summary": "模糊查询应用信息"},
                },
                "/api/v1/pipeline/run": {
                    "post": {"summary": "运行阿里云流水线"},
                },
            },
        }),
        encoding="utf-8",
    )

    zip_file = tmp_path / "devops.zip"
    with zipfile.ZipFile(zip_file, "w") as zf:
        for file_path in source_dir.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, arcname=str(file_path.relative_to(source_dir)))

    res = transform_and_package_app(
        zip_path=zip_file,
        slug="devops-aly",
        name="阿里云流水线服务",
        description="DevOps 流水线查询与管理",
        app_url="http://devops-aly.apps.localhost",
        container_port=8080,
    )

    manifest = res["manifest"]
    # 1. Port detected from conf/config.yaml (9090)
    assert manifest["container_port"] == 9090
    config_keys = {c["key"]: c["value"] for c in manifest["configs"]}
    assert config_keys["PORT"] == "9090"
    assert config_keys["ALIYUN_ACCESS_KEY_ID"] == "LTAI_FAKE_KEY_ID"
    assert config_keys["ALIYUN_ACCESS_KEY_SECRET"] == "FAKE_SECRET_VALUE"
    assert "SecretPwd123" in config_keys["MYSQL_DSN"]

    # 2. Raw conf/config.yaml inside workspace/app.tar must NOT contain raw secrets
    packaged_yaml = (Path(res["workspace_dir"]) / "conf" / "config.yaml").read_text(encoding="utf-8")
    assert "LTAI_FAKE_KEY_ID" not in packaged_yaml
    assert "FAKE_SECRET_VALUE" not in packaged_yaml
    assert "SecretPwd123" not in packaged_yaml

    # 3. APIs extracted from swagger.json and annotated in SKILL.md
    api_paths = {a["path"] for a in manifest["apis"]}
    assert "/healthz" in api_paths
    assert "/api/v1/pipeline/search/app" in api_paths
    assert "/api/v1/pipeline/run" in api_paths

    with zipfile.ZipFile(res["skill_zip_path"], "r") as zf:
        skill_md = zf.read("SKILL.md").decode("utf-8")
        assert "/api/v1/pipeline/search/app" in skill_md
        assert "[只读查询]" in skill_md
        assert "[写操作/高风险-调用前须人工确认]" in skill_md

