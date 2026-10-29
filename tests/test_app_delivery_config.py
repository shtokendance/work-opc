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


def test_loopback_connection_metadata_preserves_values_without_exposing_them(tmp_path):
    from coworker.app_delivery_config import original_fields, loopback_connection
    (tmp_path / '.env').write_text('DATABASE_URL=mysql://reader:private-token@127.0.0.1:3306/app\nHOST=127.0.0.1\n')
    metadata = original_fields(tmp_path, '.env')
    assert metadata[0]['connection_hint'].startswith('loopback:')
    assert 'private-token' not in str(metadata)
    assert 'connection_hint' not in metadata[1]
    assert loopback_connection('DB_DSN', 'reader:secret@tcp(127.1.1.0:3306)/app')
    assert not loopback_connection('DATABASE_URL', 'mysql://reader:127.0.0.1@internal-db:3306/app')
    assert not loopback_connection('DATABASE_URL', 'postgres://10.101.102.116/app')
