import json

import pytest

from coworker.app_delivery_config import original_fields, original_value, runtime_configs


@pytest.mark.parametrize("name,text,keys,value", [
    ("config.yaml", "db:\n  password: fixture-secret\n", ["db", "password"], "fixture-secret"),
    ("appsettings.json", '{"ConnectionStrings":{"Main":"fixture-secret"}}', ["ConnectionStrings", "Main"], "fixture-secret"),
    ("config.toml", '[db]\nport = 5432\n', ["db", "port"], "5432"),
    (".env", 'export TOKEN="${UNCHANGED_LITERAL}"\n', ["TOKEN"], "${UNCHANGED_LITERAL}"),
    ("app.properties", 'redis.password=fixture-secret\n', ["redis.password"], "fixture-secret"),
    ("config.yaml", 'servers:\n- port: 8080\n', ["servers", 0, "port"], "8080"),
])
def test_original_bindings_preserve_scalar_values_locally(tmp_path, name, text, keys, value):
    (tmp_path / name).write_text(text)
    ref = {"path": name, "keys": keys}
    assert original_value(tmp_path, ref) == value
    assert ref in [f["source"] for f in original_fields(tmp_path, name)]
    if "fixture-secret" in text:
        assert "fixture-secret" not in json.dumps(original_fields(tmp_path, name))
    configs = runtime_configs(tmp_path, [{"key": "SETTING", "secret": True, "required": True, "default": "", "source": ref}])
    assert configs[0]["value"] == value and configs[0]["value_source"] == "original"


def test_original_reference_cannot_escape_or_silently_fall_back_to_empty(tmp_path):
    (tmp_path / "config.yaml").write_text('db:\n  password: fixture-secret\n')
    for ref in [{"path": "../outside.yaml", "keys": ["password"]}, {"path": "config.yaml", "keys": ["missing"]}, {"path": "config.yaml", "keys": ["db"]}]:
        with pytest.raises(ValueError):
            original_value(tmp_path, ref)
