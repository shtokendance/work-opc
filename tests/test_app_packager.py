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
