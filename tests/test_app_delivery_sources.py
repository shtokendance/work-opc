from pathlib import Path

import pytest

from coworker.app_delivery_sources import dependency_path


def test_dependency_reader_only_allows_the_declared_cached_version(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "go.mod").write_text('module app\nrequire (\n example.com/Some/sdk v1.2.3\n example.com/missing v2.0.0\n)\n')
    cache = tmp_path / "cache"
    monkeypatch.setenv("GOMODCACHE", str(cache))
    module = cache / 'example.com/!some/sdk@v1.2.3'
    module.mkdir(parents=True)
    (module / 'server.go').write_text('package sdk\n')
    assert dependency_path(source)[0] == {"module": "example.com/Some/sdk", "version": "v1.2.3", "cached": True}
    assert dependency_path(source, "example.com/Some/sdk", "server.go") == module / 'server.go'
    assert dependency_path(source, "example.com/Some/sdk")[0]["path"] == "server.go"
    for name in ["../outside", "/etc/passwd"]:
        with pytest.raises(ValueError):
            dependency_path(source, "example.com/Some/sdk", name)
    with pytest.raises(ValueError, match="只能查看"):
        dependency_path(source, "example.com/undeclared", "server.go")
    with pytest.raises(ValueError, match="尚未缓存"):
        dependency_path(source, "example.com/missing", "server.go")
    (module / 'link').symlink_to(Path('/etc/passwd'))
    with pytest.raises(ValueError, match="source|symlink"):
        dependency_path(source, "example.com/Some/sdk", "link")
    with (source / "go.mod").open("a") as f:
        f.write('replace example.com/Some/sdk => ../private-fork\n')
    with pytest.raises(ValueError, match="replace"):
        dependency_path(source, "example.com/Some/sdk", "server.go")
