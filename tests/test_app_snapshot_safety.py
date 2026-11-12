import argparse
import importlib
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest


@pytest.fixture(params=['work', 'dsh'])
def delivery_module(request):
    if request.param == 'work':
        return importlib.import_module('coworker.app_delivery_skill.scripts.delivery')
    runtime = Path(__file__).resolve().parents[2] / 'dsh-plugin-opc/runtime'
    sys.path.insert(0, str(runtime))
    try:
        return importlib.import_module('app_delivery.scripts.delivery')
    finally:
        sys.path.remove(str(runtime))


@pytest.mark.parametrize('archive', [False, True])
def test_import_preserves_committed_wal_rows_without_changing_source(tmp_path, delivery_module, archive):
    source = tmp_path / 'source'
    source.mkdir()
    database = source / 'business.db'
    connection = sqlite3.connect(database)
    try:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA wal_autocheckpoint=0')
        connection.execute('CREATE TABLE records(value INTEGER)')
        connection.commit()
        connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        connection.execute('INSERT INTO records VALUES (42)')
        connection.commit()
        before = database.read_bytes()
        original = source
        if archive:
            original = tmp_path / 'source.zip'
            with zipfile.ZipFile(original, 'w') as output:
                for path in source.iterdir():
                    output.write(path, path.name)
        work = tmp_path / 'review'
        delivery_module.prepare(argparse.Namespace(archive=str(original), work_dir=str(work), slug='safe-app', url='https://safe.example.test'))
        with sqlite3.connect(work / 'source/business.db') as imported:
            assert imported.execute('SELECT value FROM records').fetchall() == [(42,)]
        assert database.read_bytes() == before
        assert connection.execute('SELECT value FROM records').fetchall() == [(42,)]
        assert not list((work / 'source').glob('*-wal'))
    finally:
        connection.close()


def test_orphan_wal_is_rejected_not_silently_discarded(tmp_path, delivery_module):
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('missing.db-wal', b'unrecoverable data')
        output.writestr('main.py', 'print("hello")')
    with pytest.raises(ValueError, match='WAL|wal'):
        delivery_module.prepare(argparse.Namespace(archive=str(archive), work_dir=str(tmp_path/'review'), slug='safe-app', url='https://safe.example.test'))


def test_truncated_wal_is_rejected(tmp_path, delivery_module):
    database = tmp_path / 'data.db'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE records(value INTEGER)')
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.write(database, 'data.db')
        output.writestr('data.db-wal', b'broken')
    with pytest.raises(ValueError, match='WAL'):
        delivery_module.prepare(argparse.Namespace(archive=str(archive), work_dir=str(tmp_path/'review'), slug='safe-app', url='https://safe.example.test'))


@pytest.mark.parametrize('ext', ['.sqlite', '.sqlite3', '.data'])
def test_wal_recycled_tail_and_custom_extensions_preserved(tmp_path, delivery_module, ext):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'main.py').write_text('print("ok")\n')
    database = source / f'custom{ext}'
    connection = sqlite3.connect(database)
    try:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA wal_autocheckpoint=0')
        connection.execute('CREATE TABLE items(val TEXT)')
        for idx in range(15):
            connection.execute('INSERT INTO items VALUES (?)', (f'row-{idx}-' + 'x' * 200,))
        connection.commit()
        connection.execute('PRAGMA wal_checkpoint(RESTART)')
        connection.execute('INSERT INTO items VALUES (?)', ('latest-after-restart',))
        connection.commit()
        before_db = database.read_bytes()
        before_wal = database.with_name(database.name + '-wal').read_bytes()
        archive = tmp_path / 'source.zip'
        with zipfile.ZipFile(archive, 'w') as output:
            for item in source.iterdir():
                if not item.name.endswith('-shm'):
                    output.write(item, item.name)
        work_zip = tmp_path / 'review-zip'
        delivery_module.prepare(argparse.Namespace(archive=str(archive), work_dir=str(work_zip), slug='safe-app', url='https://safe.example.test'))
        with sqlite3.connect(work_zip / f'source/custom{ext}') as imported:
            rows = [r[0] for r in imported.execute('SELECT val FROM items').fetchall()]
            assert len(rows) == 16 and 'latest-after-restart' in rows
        work_dir = tmp_path / 'review-dir'
        delivery_module.prepare(argparse.Namespace(archive=str(source), work_dir=str(work_dir), slug='safe-app', url='https://safe.example.test'))
        with sqlite3.connect(work_dir / f'source/custom{ext}') as imported_dir:
            rows_dir = [r[0] for r in imported_dir.execute('SELECT val FROM items').fetchall()]
            assert len(rows_dir) == 16 and 'latest-after-restart' in rows_dir
        assert database.read_bytes() == before_db
        assert database.with_name(database.name + '-wal').read_bytes() == before_wal
    finally:
        connection.close()


def test_corrupted_wal_frame_checksum_and_active_journal_rejected(tmp_path, delivery_module):
    source = tmp_path / 'source'
    source.mkdir()
    database = source / 'app.db'
    connection = sqlite3.connect(database)
    try:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA wal_autocheckpoint=0')
        connection.execute('CREATE TABLE items(val TEXT)')
        connection.execute('INSERT INTO items VALUES ("committed")')
        connection.commit()
        wal_bytes = bytearray(database.with_name('app.db-wal').read_bytes())
        wal_bytes[-10] ^= 0xff
        bad_wal_zip = tmp_path / 'bad-wal.zip'
        with zipfile.ZipFile(bad_wal_zip, 'w') as output:
            output.write(database, 'app.db')
            output.writestr('app.db-wal', bytes(wal_bytes))
        with pytest.raises(ValueError, match='WAL'):
            delivery_module.prepare(argparse.Namespace(archive=str(bad_wal_zip), work_dir=str(tmp_path / 'review-bad-wal'), slug='safe-app', url='https://safe.example.test'))
    finally:
        connection.close()

    journal_zip = tmp_path / 'bad-journal.zip'
    with zipfile.ZipFile(journal_zip, 'w') as output:
        output.write(database, 'app.db')
        output.writestr('app.db-journal', b'active-rollback-journal-bytes')
    with pytest.raises(ValueError, match='事务日志|journal'):
        delivery_module.prepare(argparse.Namespace(archive=str(journal_zip), work_dir=str(tmp_path / 'review-journal'), slug='safe-app', url='https://safe.example.test'))


def test_sqlite_snapshot_busy_timeout_and_helper_sync(tmp_path):
    from coworker.app_delivery_skill.scripts import sqlite_snapshot as work_snap
    root = Path(__file__).resolve().parents[2]
    work_file = (root / 'work-opc/coworker/app_delivery_skill/scripts/sqlite_snapshot.py').read_text(encoding='utf-8')
    dsh_file = (root / 'dsh-plugin-opc/runtime/app_delivery/scripts/sqlite_snapshot.py').read_text(encoding='utf-8')
    assert work_file == dsh_file

    database = tmp_path / 'locked.db'
    conn = sqlite3.connect(database)
    try:
        conn.execute('CREATE TABLE t(x INT)')
        conn.commit()
        conn.isolation_level = None
        conn.execute('BEGIN EXCLUSIVE')
        conn.execute('INSERT INTO t VALUES (1)')
        with pytest.raises(ValueError, match='快照|忙碌'):
            work_snap.snapshot_database(database, tmp_path / 'out.db', timeout_seconds=0.2)
    finally:
        conn.close()


def test_generated_skill_never_prints_embedded_credentials(delivery_module):
    contract = {'slug': 'safe-app', 'name': 'Safe', 'description': 'Read', 'base_url': 'https://safe.example.test', 'apis': [], 'pages': [], 'auth_profile': {'username':'user', 'password':'secret-leak', 'token':'token-leak', 'notes':'secret-leak'}}
    markdown = delivery_module.render_skill_markdown(contract)
    assert 'secret-leak' not in markdown
    assert 'token-leak' not in markdown
    assert '已内置' not in markdown


def test_contract_rejects_embedded_secrets_before_packaging(delivery_module):
    for contract in [{'auth_profile': {'password': 'secret'}}, {'auth_profiles': {'admin': {'token': 'secret'}}}]:
        with pytest.raises(ValueError, match='credential'):
            delivery_module.validate_credential_references(contract)
    delivery_module.validate_credential_references({'auth_profile': {'username_env': 'APP_USER', 'password_env': 'APP_PASSWORD'}})


def test_failed_artifact_reader_redacts_and_rejects_links(tmp_path):
    import io
    import json
    import subprocess
    import tarfile
    worker = Path(__file__).resolve().parents[2] / 'dsh-plugin-opc/runtime/worker.py'
    bundle = tmp_path / 'revision1/bundle'
    bundle.mkdir(parents=True)
    with tarfile.open(bundle/'app.tar', 'w') as output:
        raw = b'PASSWORD="private-value"\nprint("hello")\n'
        member = tarfile.TarInfo('main.py'); member.size = len(raw)
        output.addfile(member, io.BytesIO(raw))
        member = tarfile.TarInfo('link.py'); member.type = tarfile.SYMTYPE; member.linkname = '/etc/passwd'
        output.addfile(member)
    def call(path):
        result = subprocess.run([sys.executable, '-B', str(worker)], input=json.dumps({'action':'read_failed','root':str(tmp_path),'revision':'revision1','path':path}), text=True,capture_output=True)
        return json.loads(result.stdout)
    result=call('main.py')
    assert result['ok'] and 'hello' in result['value']['text']
    assert 'private-value' not in result['value']['text']
    assert not call('link.py')['ok']
    assert not call('../outside')['ok']
