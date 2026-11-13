"""Resolve scanner uncertainty using explicit, evidence-backed authentication rules."""
import copy
import re

from .app_delivery_skill.scripts import delivery


def resolve_authentication(contract):
    rules = contract.get('auth_rules', [])
    if not isinstance(rules, list):
        raise ValueError('auth_rules 必须是数组')
    for rule in rules:
        if not isinstance(rule, dict) or rule.get('target') not in {'api', 'page'} or not rule.get('evidence'):
            raise ValueError('认证规则必须声明 api/page 范围与源码证据')
        if not isinstance(rule.get('auth'), dict) or rule['auth'].get('type') not in {'none', 'header', 'bearer'}:
            raise ValueError('认证规则必须使用已确认的 none/header/bearer 方式')
        if not isinstance(rule.get('evidence'), list) or not all(isinstance(e, str) and e for e in rule['evidence']):
            raise ValueError('认证规则 evidence 必须是源码位置数组')
        location = rule.get('path') or rule.get('prefix')
        if not isinstance(location, str) or not location.startswith('/'):
            raise ValueError('认证规则路径必须以 / 开头')
        if bool(rule.get('path')) == bool(rule.get('prefix')):
            raise ValueError('认证规则必须选择精确 path 或路径 prefix')
    for target, key in [('api', 'apis'), ('page', 'pages')]:
        for item in contract.get(key, []):
            matches = [r for r in rules if r['target'] == target
                       and (not r.get('method') or r['method'].upper() == item.get('method', '').upper())
                       and (r.get('path') == item['path'] or
                            (r.get('prefix') and item['path'].startswith(r['prefix'])))]
            if matches:
                ranked = sorted(matches, key=lambda r: (bool(r.get('path')), len(r.get('prefix', '')), bool(r.get('method'))), reverse=True)
                best = ranked[0]
                rank = lambda r: (bool(r.get('path')), len(r.get('prefix', '')), bool(r.get('method')))
                if any(rank(r) == rank(best) and r.get('auth') != best.get('auth') for r in ranked[1:]):
                    raise ValueError('存在互相冲突的认证规则：' + item['path'])
                item['auth'] = {**copy.deepcopy(best.get('auth', {})), 'evidence': best['evidence']}
            auth = item.get('auth', {'type': 'unknown'})
            if auth.get('type') == 'unknown':
                raise ValueError('认证尚未分析：' + item['path'] + '。请读取登录入口、统一中间件和权限检查，填写有证据的 auth 或 auth_rules；不要向用户索要猜测的令牌。')
    return contract


def validate_auth_evidence(source, proposal):
    """Check citations in the exact source snapshot the proposal describes."""
    contract = proposal["contract"]
    changes = {change["path"]: change["content"] for change in proposal.get("changes", [])}
    line_counts = {}
    for entry in [*contract.get("auth_rules", []), *contract.get("auth_blockers", [])]:
        if not isinstance(entry, dict) or not isinstance(entry.get("evidence"), list) or not entry["evidence"]:
            raise ValueError("认证证据必须是非空的 文件:行号 数组")
        for evidence in entry["evidence"]:
            match = re.fullmatch(r"(.+):(\d+)(?:-(\d+))?", evidence) if isinstance(evidence, str) else None
            if not match:
                raise ValueError("认证证据必须引用 read_source 返回的 文件:行号")
            path, start, end = match.groups()
            if path not in line_counts:
                source_path = delivery.relative(source, path)
                content = changes.get(path)
                if content is None and source_path.is_file():
                    content = delivery.text_of(source_path)
                line_counts[path] = len(content.splitlines()) if content is not None else 0
            count = line_counts[path]
            if not 1 <= int(start) <= int(end or start) <= count:
                location = "拟提交文件" if path in changes else "当前导入源码"
                raise ValueError(
                    f"认证证据位置不存在：{evidence}。{location} {path} 共 {count} 行。"
                    "请只重读该文件的登录处理、会话读取或权限检查，用实际行号修正证据后重新提交。"
                )


def verification_credential_fields(contract):
    """Describe the credential references the verification form may accept."""
    fields = {}
    for api in contract.get("apis", []):
        auth = api.get("auth", {})
        if auth.get("type") in {"header", "bearer"} and auth.get("env"):
            fields[auth["env"]] = "session"
    for profile in [contract.get("auth_profile", {}), *contract.get("auth_profiles", {}).values()]:
        for key, kind in (("token_env", "session"), ("username_env", "username"), ("password_env", "password")):
            if profile.get(key):
                fields[profile[key]] = kind
    return [{"key": key, "kind": kind} for key, kind in fields.items()]


def validate_auth_questions(proposal):
    """Technical uncertainty is returned to the model, not turned into a user prompt."""
    contract = proposal.get('contract', {})
    questions = set(proposal.get('unresolved', []) + contract.get('unresolved', []))
    blockers = contract.get('auth_blockers', [])
    if not isinstance(blockers, list):
        raise ValueError('auth_blockers 必须是数组')
    for question in questions:
        if re.search(r'令牌|token|登录|账密|凭据|授权码', question, re.I):
            if not any(b.get('question') == question and b.get('evidence') and b.get('affected')
                       and b.get('action') and b.get('reason') in {'external_authorization', 'missing_credential', 'role_change'}
                       for b in blockers if isinstance(b, dict)):
                raise ValueError('认证提问缺少证据：请先核对实际认证代码；确实需要外部授权时提供 auth_blockers 的 question/evidence/affected/action/reason，不要重复询问推测的管理令牌。')


def validate_auth_probe_coverage(contract):
    """New auth plans must exercise every protected API identity with a read-only case."""
    if not contract.get('auth_rules') and not contract.get('auth_profiles'):
        return  # Existing packaged skills keep their original verification contract.
    def identity(api):
        auth = api.get('auth', {})
        return (auth.get('profile', 'default'), auth.get('type'), auth.get('header', ''), auth.get('env', ''), tuple(auth.get('roles', [])))
    protected = [a for a in contract.get('apis', []) if a.get('auth', {}).get('type') in {'header', 'bearer'}]
    operations = {c.get('operation') for c in contract.get('verification', {}).get('cases', [])}
    covered = {identity(a) for a in protected if a.get('id') in operations and a.get('effect') == 'read'
               and a.get('path') != contract.get('health', {}).get('path')}
    missing = {identity(a) for a in protected} - covered
    if missing:
        raise ValueError('认证验证计划不完整：每种认证身份/权限范围需要只读业务用例；不能用公开健康接口代替。请自动补齐验证计划。')
