"""Consistent SQLite snapshots; never copy a live main file without its WAL."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
import time


def is_sqlite(path):
    with Path(path).open('rb') as stream:
        return stream.read(16) == b'SQLite format 3\x00'


def validate_wal(path):
    """Fail closed on malformed archived WALs which SQLite may silently ignore."""
    data = Path(path).read_bytes()
    if not data:
        return
    if len(data) < 32:
        raise ValueError('WAL 文件不完整，请关闭原应用后重新导出')
    magic, version, page_size, checkpoint_seq, salt1, salt2 = struct.unpack('>IIIIII', data[:24])
    if magic not in (0x377f0682, 0x377f0683) or version != 3007000 or page_size < 512 or page_size > 65536 or page_size & (page_size - 1):
        raise ValueError('WAL 文件头无效，请重新导出完整数据库')
    endian = '<' if magic == 0x377f0682 else '>'

    def checksum(block, state=(0, 0)):
        a, b = state
        words = struct.unpack(endian + str(len(block) // 4) + 'I', block)
        for index in range(0, len(words), 2):
            a = (a + words[index] + b) & 0xffffffff
            b = (b + words[index + 1] + a) & 0xffffffff
        return a, b

    state = checksum(data[:24])
    frame_size = page_size + 24
    if state != struct.unpack('>II', data[24:32]) or (len(data) - 32) % frame_size:
        raise ValueError('WAL 校验失败或快照不完整，请重新导出')
    if len(data) == 32:
        return
    header_salt = data[16:24]
    active_frames = 0
    last_active_commit = False
    in_recycled_tail = False
    for offset in range(32, len(data), frame_size):
        frame = data[offset:offset + frame_size]
        frame_salt = frame[8:16]
        if frame_salt == header_salt:
            if in_recycled_tail:
                raise ValueError('WAL 帧顺序异常，请关闭原应用后重新导出')
            state = checksum(frame[:8] + frame[24:], state)
            if state != struct.unpack('>II', frame[16:24]):
                raise ValueError('WAL 帧校验失败，请重新导出')
            page_no, db_size = struct.unpack('>II', frame[:8])
            if page_no == 0:
                raise ValueError('WAL 帧页号无效，请重新导出')
            active_frames += 1
            last_active_commit = db_size > 0
        else:
            in_recycled_tail = True
            if active_frames == 0 and offset == 32:
                frame_salt1, frame_salt2 = struct.unpack('>II', frame_salt)
                is_zero_tail = frame == b'\x00' * frame_size
                is_prior_cycle = checkpoint_seq > 0 and frame_salt1 < salt1 and frame_salt2 != salt2
                if not (is_zero_tail or is_prior_cycle):
                    raise ValueError('WAL 帧不一致，请关闭原应用后重新导出')
    if active_frames > 0 and not last_active_commit:
        raise ValueError('WAL 含未提交或截断事务，请关闭原应用后重新导出')


def snapshot_database(source, target, timeout_seconds=30):
    source_path = Path(source)
    target_path = Path(target)
    wal_path = source_path.with_name(source_path.name + '-wal')
    if wal_path.is_file():
        validate_wal(wal_path)
    started = time.monotonic()

    def progress(status, remaining, total):
        if time.monotonic() - started > timeout_seconds:
            raise ValueError('数据库持续忙碌，请暂停写入后重新导入')

    try:
        connect_timeout = max(0.1, min(5.0, float(timeout_seconds)))
        with closing(sqlite3.connect(source_path.resolve().as_uri() + '?mode=ro', uri=True, timeout=connect_timeout)) as original:
            with closing(sqlite3.connect(str(target_path), timeout=connect_timeout)) as output:
                original.backup(output, pages=256, progress=progress, sleep=0.05)
                if output.execute('PRAGMA quick_check').fetchone() != ('ok',):
                    raise ValueError('数据库快照校验失败')
                mode_row = output.execute('PRAGMA journal_mode').fetchone()
                if not mode_row or str(mode_row[0]).lower() != 'delete':
                    output.execute('PRAGMA journal_mode=DELETE')
        if not wal_path.exists():
            src_bytes = source_path.read_bytes()
            dst_bytes = bytearray(target_path.read_bytes())
            if len(src_bytes) == len(dst_bytes) >= 100 and src_bytes[18:20] == b'\x01\x01':
                dst_bytes[24:28] = src_bytes[24:28]
                dst_bytes[92:96] = src_bytes[92:96]
                if bytes(dst_bytes) == src_bytes:
                    target_path.write_bytes(dst_bytes)
    except sqlite3.Error as error:
        raise ValueError('无法生成一致的 SQLite 快照，请关闭应用后重新导出') from error


def validate_sidecars(source):
    for item in Path(source).rglob('*'):
        if item.is_symlink():
            raise ValueError('source contains symlink')
        if not item.is_file():
            continue
        if item.name.endswith('-wal'):
            database = item.with_name(item.name[:-4])
            if not database.is_file() or not is_sqlite(database):
                raise ValueError('发现缺少原数据库的 WAL 文件：' + item.name)
        if item.name.endswith('-journal') and item.stat().st_size:
            raise ValueError('存在数据库事务日志，请关闭原应用后重新导出：' + item.name)


def normalize_extracted_databases(source):
    validate_sidecars(source)
    for database in list(Path(source).rglob('*')):
        if not database.is_file() or not is_sqlite(database):
            continue
        wal = database.with_name(database.name + '-wal')
        if wal.exists():
            validate_wal(wal)
        # SHM is a derived index, not data; reconstruct it from the archived WAL.
        database.with_name(database.name + '-shm').unlink(missing_ok=True)
        with tempfile.TemporaryDirectory(prefix='opc-sqlite-') as temp:
            snapshot = Path(temp) / 'snapshot.db'
            snapshot_database(database, snapshot)
            mode = database.stat().st_mode & 0o777
            with snapshot.open('rb') as inp, database.open('wb') as out:
                import shutil
                shutil.copyfileobj(inp, out)
            os.chmod(database, mode)
        wal.unlink(missing_ok=True)
        database.with_name(database.name + '-shm').unlink(missing_ok=True)
