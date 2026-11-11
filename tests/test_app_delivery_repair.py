import io
import tarfile
import shutil
from pathlib import Path

import pytest

from coworker.app_delivery import AppDelivery
from coworker.app_delivery_repair import check_archive_syntax, read_archive_file


def archive(tmp_path, content, name='server.mjs'):
    target = tmp_path / 'app.tar'
    with tarfile.open(target, 'w') as tar:
        member = tarfile.TarInfo(name)
        body = content.encode()
        member.size = len(body)
        tar.addfile(member, io.BytesIO(body))
    return target


@pytest.mark.skipif(not shutil.which('node'), reason='Node.js required')
def test_exact_archive_rejects_top_level_return(tmp_path):
    target = archive(tmp_path, "if (req.url === '/healthz') { return; }")
    with pytest.raises(ValueError, match='Illegal return statement'):
        check_archive_syntax(target)


@pytest.mark.skipif(not shutil.which('node'), reason='Node.js required')
def test_check_never_executes_application(tmp_path):
    sentinel = tmp_path / 'must-not-exist'
    target = archive(tmp_path, f"import fs from 'node:fs'; fs.writeFileSync({str(sentinel)!r}, 'bad');")
    assert check_archive_syntax(target)['status'] == 'passed'
    assert not sentinel.exists()


def test_missing_node_is_not_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, 'which', lambda _: None)
    assert check_archive_syntax(archive(tmp_path, 'return;'))['status'] == 'pending'


def test_does_not_follow_archive_links(tmp_path):
    target = tmp_path / 'app.tar'
    with tarfile.open(target, 'w') as tar:
        member = tarfile.TarInfo('server.mjs')
        member.type = tarfile.SYMTYPE
        member.linkname = '/etc/passwd'
        tar.addfile(member)
    with pytest.raises(ValueError):
        read_archive_file(target, 'server.mjs')


def test_rejects_business_file_replaced_by_snippet(tmp_path):
    (tmp_path / 'server.mjs').write_text('// business\n' * 200)
    with pytest.raises(ValueError, match='代码片段'):
        AppDelivery.validate_file_change(tmp_path, {'id': 'fix', 'path': 'server.mjs', 'reason': 'health', 'content': 'return;\n'})


def test_reader_uses_archive_not_live_source(tmp_path):
    target = archive(tmp_path, 'const version = "failed";')
    (tmp_path / 'server.mjs').write_text('const version = "original";')
    assert 'failed' in read_archive_file(target, 'server.mjs')
