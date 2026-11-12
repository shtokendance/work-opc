import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from coworker.app_delivery_auth import resolve_authentication, validate_auth_questions, validate_auth_probe_coverage
from coworker.app_delivery_migration import scan_app_routes_and_pages
from coworker.app_delivery_skill.scripts.invoke_api import execute, build_request


def test_names_never_invent_admin_tokens(tmp_path):
    (tmp_path / 'ui.js').write_text('const nav = [{view: "permissions", label: "权限设置"}, {view: "admin", label: "管理"}];')
    scan = scan_app_routes_and_pages(tmp_path)
    assert scan['pages']
    assert all(p['auth']['type'] == 'unknown' for p in scan['pages'])
    assert 'X-Admin-Token' not in json.dumps(scan)


def test_inherited_auth_and_public_exception():
    contract = {'apis': [{'path': '/api/items', 'method': 'GET'}, {'path': '/api/session', 'method': 'POST'}], 'auth_rules': [
        {'target': 'api', 'prefix': '/api/', 'auth': {'type': 'header', 'header': 'Cookie', 'profile': 'account'}, 'evidence': ['server.mjs:10']},
        {'target': 'api', 'path': '/api/session', 'method': 'POST', 'auth': {'type': 'none'}, 'evidence': ['server.mjs:20']},
    ]}
    resolve_authentication(contract)
    assert contract['apis'][0]['auth']['header'] == 'Cookie'
    assert contract['apis'][1]['auth']['type'] == 'none'


def test_unknown_auth_returns_to_model():
    with pytest.raises(ValueError, match='认证尚未分析'):
        resolve_authentication({'apis': [{'path': '/admin', 'auth': {'type': 'unknown'}}]})
    with pytest.raises(ValueError, match='缺少证据'):
        validate_auth_questions({'unresolved': ['请提供管理令牌'], 'contract': {}})


def test_public_probe_does_not_certify_protected_identity():
    with pytest.raises(ValueError, match='认证验证计划不完整'):
        validate_auth_probe_coverage({'auth_profiles': {'account': {}}, 'apis': [{'id': 'private', 'path': '/private', 'auth': {'type': 'bearer', 'profile': 'account'}, 'effect': 'read'}], 'verification': {'cases': [{'operation': 'health'}]}})


@pytest.mark.parametrize('kind', ['cookie', 'bearer'])
def test_real_login_then_protected_request(kind):
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append(body)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Set-Cookie', 'sid=fixture; HttpOnly; Path=/')
            self.end_headers()
            self.wfile.write(b'{"data":{"access_token":"fixture"}}')
        def do_GET(self):
            valid = self.headers.get('Cookie') == 'sid=fixture' if kind == 'cookie' else self.headers.get('Authorization') == 'Bearer fixture'
            self.send_response(200 if valid else 401)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        auth = {'type': 'header', 'header': 'Cookie', 'profile': 'account'} if kind == 'cookie' else {'type': 'bearer', 'profile': 'account'}
        contract = {'base_url': f'http://127.0.0.1:{server.server_port}', 'auth_profiles': {'account': {'login_url': '/login', 'username_env': 'USER', 'password_env': 'PASS', 'token_json_path': 'data.access_token'}}, 'apis': [{'id': 'read', 'path': '/items', 'method': 'GET', 'effect': 'read', 'auth': auth}]}
        code, _, _ = execute(contract, 'read', {}, None, credential_env={'USER': 'fixture-user', 'PASS': 'fixture-password'})
        assert code == 200
        assert seen == [{'username': 'fixture-user', 'password': 'fixture-password'}]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_profiles_do_not_share_tokens():
    contract = {'base_url': 'https://app.example', 'auth_profile': {'token_env': 'WRONG_TOKEN'}, 'auth_profiles': {'one': {'token_env': 'RIGHT_TOKEN'}}, 'apis': [{'id': 'read', 'path': '/items', 'method': 'GET', 'effect': 'read', 'auth': {'type': 'bearer', 'profile': 'one'}}]}
    assert build_request(contract, 'read', {}, None, credential_env={'WRONG_TOKEN': 'wrong', 'RIGHT_TOKEN': 'right'}).get_header('Authorization') == 'Bearer right'
    contract['apis'][0]['auth']['profile'] = 'absent'
    with pytest.raises(ValueError, match='unknown authentication profile'):
        build_request(contract, 'read', {}, None, credential_env={'WRONG_TOKEN': 'wrong', 'RIGHT_TOKEN': 'right'})


def test_sqlite_custom_directory_uses_volume_with_companions(tmp_path):
    import sqlite3
    from coworker.app_delivery_migration import inspect_local_file_storage
    folder = tmp_path / 'local_store'
    folder.mkdir()
    with sqlite3.connect(folder / 'business.sqlite') as conn:
        conn.execute('CREATE TABLE items(id INTEGER)')
    (folder / 'business.sqlite-wal').write_bytes(b'fixture')
    result = inspect_local_file_storage(tmp_path, slug='sample')
    mount = next(m for m in result['storage_mounts'] if m['container_path'] == '/app/local_store')
    assert 'local_store/business.sqlite' in mount['seed_files']
    assert 'local_store/business.sqlite-wal' in mount['seed_files']


def test_sqlite_root_does_not_cover_application_code(tmp_path):
    from coworker.app_delivery_migration import inspect_local_file_storage
    (tmp_path / 'business.sqlite').write_bytes(b'fixture')
    (tmp_path / 'main.py').write_text('print("business")')
    assert not any(m['container_path'] == '/app' for m in inspect_local_file_storage(tmp_path)['storage_mounts'])
