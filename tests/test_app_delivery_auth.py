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


def test_enrichment_never_turns_sample_passwords_into_defaults(tmp_path):
    from coworker.app_delivery_migration import enrich_contract_and_discovery_report

    (tmp_path / 'README.md').write_text('管理员账号：admin，默认密码：FixturePassword123!')
    contract = {
        'auth_profile': {'login_url': '/api/session', 'username_env': 'APP_USER', 'password_env': 'APP_PASSWORD'},
        'configs': [{'key': 'APP_PASSWORD', 'default': '', 'secret': True, 'required': False}],
    }
    data = {'slug': 'app-example'}
    proposal = {'contract': contract, 'changes': [], 'unresolved': []}
    enrich_contract_and_discovery_report(tmp_path, data, proposal)
    assert contract['configs'] == [{'key': 'APP_PASSWORD', 'default': '', 'secret': True, 'required': False}]
    assert 'FixturePassword123!' not in json.dumps(proposal)
    assert 'saved_verification_credentials' not in data


def test_embedded_credential_aliases_cannot_be_packaged():
    from coworker.app_delivery_skill.scripts.delivery import validate_credential_references

    for key in ('default_username', 'default_password', 'default_token', 'login_body'):
        with pytest.raises(ValueError, match='embedded credentials'):
            validate_credential_references({'auth_profiles': {'admin': {key: 'fixture'}}})


def test_verification_credentials_ignore_old_defaults():
    from coworker.server.app_delivery_routes import verification_credentials
    contract = {
        'apis': [{'id': 'test', 'path': '/test', 'auth': {'type': 'header', 'header': 'Cookie', 'env': 'OPC_SESSION_TOKEN'}}],
        'auth_profiles': {
            'admin': {
                'username_env': 'OPC_ADMIN_USERNAME',
                'password_env': 'OPC_ADMIN_PASSWORD',
                'token_env': 'OPC_SESSION_TOKEN',
                'default_username': 'admin',
                'default_password': 'ChangeMe123!',
            }
        },
        'configs': [
            {'key': 'OPC_ADMIN_USERNAME', 'default': 'admin'},
            {'key': 'OPC_ADMIN_PASSWORD', 'default': 'ChangeMe123!', 'secret': True},
        ]
    }
    creds = verification_credentials(contract, [{'key': 'OPC_ADMIN_PASSWORD', 'value': ''}], None)
    assert not creds.get('OPC_ADMIN_USERNAME')
    assert not creds.get('OPC_ADMIN_PASSWORD')
    explicit = verification_credentials(contract, [], {'OPC_ADMIN_USERNAME': 'operator', 'OPC_ADMIN_PASSWORD': 'provided'})
    assert explicit == {'OPC_ADMIN_USERNAME': 'operator', 'OPC_ADMIN_PASSWORD': 'provided'}


def test_auto_login_requires_explicit_credentials_even_with_old_defaults():
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append(body)
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Set-Cookie', 'sid=auto-cookie; Path=/')
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        def do_GET(self):
            valid = self.headers.get('Cookie') == 'sid=auto-cookie'
            self.send_response(200 if valid else 401)
            self.send_header('Content-Type', 'application/octet-stream')
            self.end_headers()
            self.wfile.write(b'{"authenticated":true}')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        from coworker.app_delivery_skill.scripts import delivery
        contract = {
            'base_url': f'http://127.0.0.1:{server.server_port}',
            'auth_profiles': {
                'admin': {
                    'login_url': '/login',
                    'username_env': 'ADMIN_USER',
                    'password_env': 'ADMIN_PASS',
                    'default_username': 'default_admin',
                    'default_password': 'DefaultPassword99!',
                }
            },
            'apis': [{
                'id': 'get-data',
                'path': '/data',
                'method': 'GET',
                'effect': 'read',
                'auth': {'type': 'header', 'header': 'Cookie', 'profile': 'admin'},
            }]
        }
        with pytest.raises(ValueError, match='missing credential'):
            execute(contract, 'get-data', {}, None, credential_env={})
        assert seen == []
        code, content_type, data = execute(
            contract, 'get-data', {}, None, credential_env={'ADMIN_USER': 'operator', 'ADMIN_PASS': 'provided'},
        )
        assert code == 200
        assert delivery.json_matches(data, {'authenticated': True})
        assert seen == [{'username': 'operator', 'password': 'provided'}]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
