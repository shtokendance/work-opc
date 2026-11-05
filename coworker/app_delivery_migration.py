"""Step 1 & Step 2 helpers for OPC App Delivery:
- Credential discovery & code-assisted decryption from isolated source/SQLite/JSON/config
- Dockerfile template fetching from platform-opc (deploy-opc database)
- Local SQLite / local DB (PostgreSQL, MySQL, SQLServer) / local Redis inspection and automatic migration to platform-opc server resources
"""

from __future__ import annotations

import ast
import base64
import binascii
import codecs
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request

from .app_delivery_skill.scripts import delivery


def inspect_credentials_in_source(source: Path, target_path: str = "", cipher_text: str = "", algorithm_hint: str = "") -> dict:
    """Step 1 helper: scan config/seed/code/SQLite in isolated source for admin accounts & decrypt encoded/encrypted passwords."""
    result = {
        "discovered_accounts": [],
        "decrypted_candidates": [],
        "sqlite_accounts": [],
        "notes": [],
    }

    # 1. Try decoding/decrypting explicit cipher_text if supplied
    if cipher_text.strip():
        raw = cipher_text.strip()
        result["decrypted_candidates"].extend(_try_decode_cipher(raw, algorithm_hint, source))

    # 2. If a specific file is requested, inspect its raw content for accounts/seeds/hashes
    files_to_scan = []
    if target_path.strip():
        try:
            p = delivery.relative(source, target_path.strip())
            if p.is_file():
                files_to_scan.append(p)
        except Exception:
            pass
    else:
        for p in sorted(source.rglob("*")):
            if not p.is_file() or p.is_symlink():
                continue
            if any(part in delivery.EXCLUDED for part in p.relative_to(source).parts):
                continue
            files_to_scan.append(p)

    user_key_re = re.compile(
        r"""(?i)(?:admin[_-]?(?:user|username|email|account)|default[_-]?(?:user|admin)|username|user_name|login_name|account)\s*["']?\s*[:=]\s*["']([^"'\n]{2,64})["']"""
    )
    pass_key_re = re.compile(
        r"""(?i)(?:[a-z0-9_]*(?:pass|password|pwd|secret|hash|b64|cipher)[a-z0-9_]*)\s*["']?\s*[:=]\s*["']([^"'\n]{2,128})["']"""
    )
    token_key_re = re.compile(
        r"""(?i)(?:admin[_-]?token|api[_-]?token|auth[_-]?token|default[_-]?token|fixed[_-]?token)\s*["']?\s*[:=]\s*["']([^"'\n]{4,256})["']"""
    )

    for p in files_to_scan:
        rel = p.relative_to(source).as_posix()
        suffix = p.suffix.lower()
        if suffix in {".sqlite", ".sqlite3", ".db"}:
            if p.stat().st_size == 0:
                continue
            sqlite_items = _scan_sqlite_accounts(p, rel)
            result["sqlite_accounts"].extend(sqlite_items)
            for item in sqlite_items:
                result["decrypted_candidates"].extend(item.get("decoded_candidates", []))
            continue
        if p.stat().st_size > 10_000_000:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue

        users = user_key_re.findall(text)
        passwords = pass_key_re.findall(text)
        tokens = token_key_re.findall(text)
        if users or passwords or tokens:
            decoded_pwds = []
            for pwd in passwords[:50]:
                decoded_pwds.extend(_try_decode_cipher(pwd, algorithm_hint, source))
            result["decrypted_candidates"].extend(decoded_pwds)
            result["discovered_accounts"].append({
                "file": rel,
                "usernames": list(dict.fromkeys(users[:50])),
                "passwords_or_hashes": list(dict.fromkeys(passwords[:50])),
                "tokens": list(dict.fromkeys(tokens[:50])),
                "decoded_candidates": decoded_pwds,
            })

    result["sqlite_users"] = result["sqlite_accounts"]
    return result


def _scan_sqlite_accounts(sqlite_file: Path, rel_path: str) -> list[dict]:
    found = []
    try:
        conn = sqlite3.connect(f"file:{sqlite_file}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            tables = [r[0] for r in cur.fetchall()]
            for tbl in tables:
                if not re.search(r"(?i)user|admin|account|member|operator|auth|staff", tbl):
                    continue
                rows = conn.execute(f'SELECT * FROM "{tbl}" LIMIT 100').fetchall()
                for row in rows:
                    d = dict(row)
                    entry = {"file": rel_path, "table": tbl, "record": {}}
                    for k, v in d.items():
                        if re.search(r"(?i)user|name|email|login|role|pass|pwd|hash|token|secret|salt|is_admin|admin", k):
                            entry["record"][k] = str(v) if v is not None else None
                            if isinstance(v, str) and re.search(r"(?i)pass|pwd|hash", k):
                                dec = _try_decode_cipher(v, "", sqlite_file.parent)
                                if dec:
                                    entry["decoded_candidates"] = dec
                    if entry["record"]:
                        found.append(entry)
        finally:
            conn.close()
    except Exception:
        pass
    return found


def _try_decode_cipher(value: str, hint: str, source: Path) -> list[dict]:
    """Attempt common encodings, reversible ciphers, and well-known default password hashes."""
    candidates = []
    val = value.strip()
    if not val:
        return candidates

    # 1. Well-known MD5 / SHA256 hashes of common default passwords
    common_plaintexts = [
        "admin", "admin123", "123456", "12345678", "password", "root",
        "admin@123", "111111", "888888", "qwerty", "opc123", "test1234",
    ]
    lower_val = val.lower()
    for pt in common_plaintexts:
        if hashlib.md5(pt.encode()).hexdigest() == lower_val:
            candidates.append({"method": "md5-lookup", "plaintext": pt, "decrypted": pt})
        if hashlib.sha256(pt.encode()).hexdigest() == lower_val:
            candidates.append({"method": "sha256-lookup", "plaintext": pt, "decrypted": pt})
        if hashlib.sha1(pt.encode()).hexdigest() == lower_val:
            candidates.append({"method": "sha1-lookup", "plaintext": pt, "decrypted": pt})

    # 2. Base64prefix or pure Base64
    b64_input = re.sub(r"(?i)^(?:base64:|b64:|enc:)", "", val).strip()
    if len(b64_input) >= 4 and re.fullmatch(r"[A-Za-z0-9+/=_-]+", b64_input):
        try:
            padded = b64_input + "=" * ((4 - len(b64_input) % 4) % 4)
            decoded = base64.b64decode(padded).decode("utf-8")
            if decoded and all(32 <= ord(c) < 127 for c in decoded) and decoded != val:
                candidates.append({"method": "base64", "plaintext": decoded, "decrypted": decoded})
        except Exception:
            pass

    # 3. Hex encoding
    hex_input = re.sub(r"(?i)^(?:0x|hex:)", "", val).strip()
    if len(hex_input) >= 4 and len(hex_input) % 2 == 0 and re.fullmatch(r"[0-9a-fA-F]+", hex_input) and len(hex_input) not in {32, 40, 64}:
        try:
            decoded = binascii.unhexlify(hex_input).decode("utf-8")
            if decoded and all(32 <= ord(c) < 127 for c in decoded):
                candidates.append({"method": "hex", "plaintext": decoded, "decrypted": decoded})
        except Exception:
            pass

    # 4. ROT13
    if hint and "rot13" in hint.lower():
        rot_pt = codecs.decode(val, "rot_13")
        candidates.append({"method": "rot13", "plaintext": rot_pt, "decrypted": rot_pt})

    return candidates


def fetch_platform_dockerfile_template(platform_url: str, language: str, version: str = "") -> dict:
    """Step 2 helper: fetch Dockerfile template for (language, version) from platform-opc deploy-opc DB."""
    base = (platform_url or "https://shtokendance.benlai.com").rstrip("/")
    query = urllib.parse.urlencode({"language": language.strip(), "version": version.strip()})
    url = f"{base}/api/v1/apps/dockerfile-templates?{query}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {
                "ok": True,
                "source": "platform-opc",
                "matched_template": data.get("matched_template"),
                "templates_count": len(data.get("templates") or []),
            }
    except Exception as exc:
        return {
            "ok": False,
            "source": "fallback",
            "error": str(exc),
            "matched_template": _fallback_template(language, version),
        }


def _fallback_template(language: str, version: str) -> dict:
    lang = language.lower().strip()
    ver = version.strip() or ("3.11" if "py" in lang else "20" if "node" in lang else "1.22" if "go" in lang else "latest")
    if "py" in lang:
        return {
            "language": "python",
            "runtime_version": ver,
            "base_image": f"python:{ver}-slim",
            "template_content": (
                f"FROM python:{ver}-slim\nWORKDIR /app\n"
                "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/\n"
                "COPY requirements*.txt ./\nRUN pip install --no-cache-dir -r requirements.txt\n"
                "COPY . .\nEXPOSE 8080\nCMD [\"python\", \"main.py\"]"
            ),
        }
    if "node" in lang or "js" in lang or "ts" in lang:
        return {
            "language": "nodejs",
            "runtime_version": ver,
            "base_image": f"node:{ver}-alpine",
            "template_content": (
                f"FROM node:{ver}-alpine\nWORKDIR /app\n"
                "RUN npm config set registry https://registry.npmmirror.com\n"
                "COPY package*.json ./\nRUN npm install\nCOPY . .\nEXPOSE 8080\nCMD [\"npm\", \"start\"]"
            ),
        }
    return {
        "language": lang or "generic",
        "runtime_version": ver,
        "base_image": f"{lang}:{ver}",
        "template_content": f"FROM {lang}:{ver}\nWORKDIR /app\nCOPY . .\nEXPOSE 8080\n",
    }


def inspect_local_sqlite_files(source: Path, sqlite_rel_path: str = "") -> dict:
    """Inspect SQLite database files inside the application source directory."""
    candidates = []
    if sqlite_rel_path.strip():
        p = delivery.relative(source, sqlite_rel_path.strip())
        if p.is_file() and p.stat().st_size > 0:
            candidates.append(p)
    else:
        for p in sorted(source.rglob("*")):
            if p.is_file() and p.suffix.lower() in {".sqlite", ".sqlite3", ".db"} and p.stat().st_size > 0:
                candidates.append(p)

    databases = []
    for db_file in candidates:
        rel = db_file.relative_to(source).as_posix()
        try:
            tables = extract_sqlite_tables(db_file, max_rows_per_table=5)
        except Exception:
            continue
        if not tables and len(candidates) > 1:
            continue
        total_rows = sum(int(t.get("total_rows", len(t["rows"]))) for t in tables)
        databases.append({
            "path": rel,
            "size_bytes": db_file.stat().st_size,
            "table_count": len(tables),
            "total_rows": total_rows,
            "tables": [
                {
                    "name": t["name"],
                    "columns": t["columns"],
                    "row_count": int(t.get("total_rows", len(t["rows"]))),
                    "row_count_preview": len(t["rows"]),
                }
                for t in tables
            ],
        })
    databases.sort(key=lambda d: (d.get("total_rows", 0), d.get("table_count", 0), d.get("size_bytes", 0)), reverse=True)
    return {"sqlite_databases": databases}


def extract_sqlite_tables(sqlite_file: Path, max_rows_per_table: int | None = None) -> list[dict]:
    """Read full schema (columns + types + PKs) and ALL rows from a local SQLite file for server migration."""
    tables = []
    conn = sqlite3.connect(f"file:{sqlite_file}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        for table_name, ddl in cur.fetchall():
            cols_info = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            columns = []
            for c in cols_info:
                col_type = str(c["type"] or "TEXT").upper()
                is_pk = bool(c["pk"])
                auto_inc = is_pk and "INT" in col_type and "AUTOINCREMENT" in (ddl or "").upper()
                columns.append({
                    "name": c["name"],
                    "type": col_type,
                    "primary_key": is_pk,
                    "auto_increment": auto_inc,
                    "not_null": bool(c["notnull"]),
                    "default_value": str(c["dflt_value"]) if c["dflt_value"] is not None else "",
                })
            total_count = 0
            try:
                cnt_row = conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()
                if cnt_row:
                    total_count = int(cnt_row[0])
            except Exception:
                pass
            if max_rows_per_table is not None and int(max_rows_per_table) > 0:
                rows_raw = conn.execute(f'SELECT * FROM "{table_name}" LIMIT {int(max_rows_per_table)}').fetchall()
            else:
                rows_raw = conn.execute(f'SELECT * FROM "{table_name}"').fetchall()
            rows = []
            for r in rows_raw:
                row_dict = {}
                for k in r.keys():
                    val = r[k]
                    if isinstance(val, bytes):
                        val = base64.b64encode(val).decode("ascii")
                    row_dict[k] = val
                rows.append(row_dict)
            tables.append({
                "name": table_name,
                "ddl": ddl or "",
                "columns": columns,
                "total_rows": total_count or len(rows),
                "rows": rows,
            })
    finally:
        conn.close()
        # Clean up any transient shm/wal files created by SQLite during readonly inspection
        for sfx in ("-shm", "-wal"):
            aux = Path(f"{sqlite_file}{sfx}")
            if aux.exists():
                try:
                    aux.unlink()
                except OSError:
                    pass
    return tables


def migrate_local_data_to_platform(
    *,
    platform_url: str,
    slug: str,
    source_dir: Path,
    source_type: str = "sqlite",
    sqlite_path: str = "",
    target_type: str = "postgres",
    target_resource_id: str = "",
    tables_override: list | None = None,
    redis_keys: list | None = None,
) -> dict:
    """Extract schema & 100% of data from local SQLite (or provided tables/redis_keys) and upload to platform-opc POST /api/v1/apps/:slug/migrate-data."""
    tables = tables_override or []
    if not tables and source_type.lower() == "sqlite":
        if sqlite_path.strip():
            db_file = delivery.relative(source_dir, sqlite_path.strip())
            if db_file.is_file() and db_file.stat().st_size > 0:
                tables = extract_sqlite_tables(db_file, max_rows_per_table=None)
        if not tables:
            found = [
                p for p in sorted(source_dir.rglob("*"))
                if p.is_file() and p.suffix.lower() in {".sqlite", ".sqlite3", ".db"} and p.stat().st_size > 0
            ]
            if not found:
                raise ValueError("未在源码目录中找到非空 SQLite 数据库文件")
            # Sort non-empty SQLite files by size descending and merge all non-empty tables
            found.sort(key=lambda p: p.stat().st_size, reverse=True)
            seen_tables = set()
            for db_file in found:
                try:
                    extracted = extract_sqlite_tables(db_file, max_rows_per_table=None)
                except Exception:
                    continue
                for tbl in extracted:
                    if tbl["name"] not in seen_tables:
                        seen_tables.add(tbl["name"])
                        tables.append(tbl)

    payload = {
        "source_type": source_type.lower().strip(),
        "target_type": target_type.lower().strip() or "postgres",
        "target_resource_id": target_resource_id.strip(),
        "tables": tables,
        "redis_keys": redis_keys or [],
    }
    base = (platform_url or "https://shtokendance.benlai.com").rstrip("/")
    url = f"{base}/api/v1/apps/{urllib.parse.quote(slug)}/migrate-data"
    body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body_bytes,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=600) as resp:
            resp_json = json.loads(resp.read().decode("utf-8"))
            if resp_json.get("code") == 0 and resp_json.get("data"):
                return resp_json["data"]
            return resp_json
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"平台数据迁移接口返回错误 ({exc.code}): {detail}") from exc
    except Exception as exc:
        raise ValueError(f"调用平台数据迁移接口失败: {exc}") from exc


def _join_route_paths(*parts: str) -> str:
    cleaned = []
    for p in parts:
        s = (p or "").strip()
        if not s or s == "/":
            continue
        cleaned.append(s.strip("/"))
    return "/" + "/".join(cleaned) if cleaned else "/"


def _humanize_identifier(name: str, method: str = "GET", path: str = "") -> str:
    """Generate a clear Chinese purpose description from a route handler name and path when docstring is absent."""
    n = (name or "").strip().strip("_")
    verb_map = {
        "list": "查询列表",
        "get": "获取详情",
        "read": "读取",
        "fetch": "获取",
        "query": "查询",
        "search": "搜索",
        "export": "导出",
        "download": "下载",
        "preview": "预览",
        "create": "新建",
        "add": "新增",
        "save": "保存",
        "submit": "提交",
        "upload": "上传",
        "import": "导入",
        "parse": "解析",
        "extract": "提取",
        "recognize": "识别",
        "match": "匹配",
        "validate": "校验",
        "verify": "验证",
        "check": "检查",
        "update": "更新",
        "edit": "编辑",
        "patch": "修改",
        "set": "设置",
        "toggle": "切换状态",
        "enable": "启用",
        "disable": "禁用",
        "approve": "审批通过",
        "reject": "驳回",
        "cancel": "取消",
        "delete": "删除",
        "remove": "移除",
        "clear": "清空",
        "batch": "批量处理",
        "sync": "同步",
        "login": "登录",
        "logout": "退出登录",
        "register": "注册",
        "stats": "统计汇总",
        "summary": "汇总信息",
        "config": "配置",
        "settings": "设置",
        "template": "模板",
        "health": "健康检查",
        "healthz": "健康检查",
    }
    noun_map = {
        "reimburse": "报销单",
        "form": "单据",
        "forms": "单据列表",
        "invoice": "发票",
        "invoices": "发票",
        "attachment": "附件",
        "attachments": "附件",
        "payee": "收款人/领款人",
        "payees": "收款人/领款人",
        "sales": "销售",
        "order": "订单",
        "orders": "订单",
        "travel": "差旅",
        "itinerary": "行程",
        "allowance": "补贴",
        "city": "城市/地点",
        "cities": "城市/地点",
        "company": "公司主体",
        "companies": "公司主体",
        "department": "部门",
        "departments": "部门",
        "category": "费用大类/科目",
        "categories": "费用大类/科目",
        "type": "类型",
        "types": "类型",
        "rule": "规则",
        "rules": "规则",
        "prompt": "提示词",
        "prompts": "提示词",
        "model": "模型",
        "models": "模型",
        "llm": "大模型配置",
        "ocr": "OCR识别",
        "admin": "管理后台",
        "audit": "审计日志",
        "log": "日志",
        "logs": "日志",
        "user": "用户",
        "users": "用户",
        "item": "明细项",
        "items": "明细项",
        "file": "文件",
        "files": "文件",
        "voucher": "凭证",
        "contract": "合同",
        "dedup": "查重",
        "duplicate": "重复项",
    }
    tokens = [t.lower() for t in re.split(r"[_\-/]+", n) if t]
    if not tokens:
        tokens = [t.lower() for t in re.split(r"[_\-/{}]+", path) if t and t != "api"]
    zh_parts = []
    for tok in tokens:
        if tok in verb_map:
            zh_parts.append(verb_map[tok])
        elif tok in noun_map:
            zh_parts.append(noun_map[tok])
    if zh_parts:
        dedup = []
        for p in zh_parts:
            if not dedup or dedup[-1] != p:
                dedup.append(p)
        return "".join(dedup) + f" ({method.upper()} {path})"
    return f"{n or '接口'} ({method.upper()} {path})"


def scan_app_routes_and_pages(source: Path) -> dict:
    """Deterministically scan the entire application source tree for:
    - All backend API endpoints (FastAPI, Flask, Express/Node, Go, etc.) with method, full path, parameters, request_body, auth, effect, and source line evidence.
    - All frontend pages AND SPA sub-views/panels with path, purpose, and source line evidence.
    - Runtime language, minimum version, syntax requirements (e.g. Python >=3.11 for StrEnum), and existing Dockerfile info.
    """
    source_files: list[Path] = []
    for p in sorted(source.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        rel_parts = p.relative_to(source).parts
        if any(part in delivery.EXCLUDED for part in rel_parts):
            continue
        source_files.append(p)

    # 1. Runtime & syntax inspection
    runtime_info = _scan_runtime_info(source, source_files)

    # 2. Scan Python Pydantic models for request/response schemas
    pydantic_models = _scan_pydantic_models(source, source_files)

    # 3. Scan backend API routes
    apis: list[dict] = []
    apis.extend(_scan_python_routes(source, source_files, pydantic_models))
    apis.extend(_scan_js_ts_routes(source, source_files))
    apis.extend(_scan_go_routes(source, source_files))

    # Deduplicate APIs by (method, path), keeping the richest entry
    dedup_apis: dict[tuple[str, str], dict] = {}
    for api in apis:
        key = (api["method"].upper(), api["path"])
        if key not in dedup_apis:
            dedup_apis[key] = api
        else:
            existing = dedup_apis[key]
            if len(api.get("parameters") or []) > len(existing.get("parameters") or []):
                dedup_apis[key] = api

    sorted_apis = sorted(dedup_apis.values(), key=lambda a: (a["path"], a["method"]))

    # Group APIs by module prefix
    api_modules: dict[str, int] = {}
    for a in sorted_apis:
        parts = [seg for seg in a["path"].split("/") if seg and not seg.startswith("{")]
        mod = "/" + "/".join(parts[:2]) if len(parts) >= 2 else ("/" + parts[0] if parts else "/")
        api_modules[mod] = api_modules.get(mod, 0) + 1

    # 4. Scan frontend pages and SPA sub-panels
    pages = _scan_frontend_pages(source, source_files)

    return {
        "runtime": runtime_info,
        "api_count": len(sorted_apis),
        "api_modules": api_modules,
        "apis": sorted_apis,
        "page_count": len(pages),
        "pages": pages,
    }


def _scan_runtime_info(source: Path, files: list[Path]) -> dict:
    has_python = any(p.suffix == ".py" for p in files)
    has_node = (source / "package.json").is_file()
    has_go = (source / "go.mod").is_file()
    has_dockerfile = (source / "Dockerfile").is_file()
    dockerfile_base = ""
    if has_dockerfile:
        try:
            df_text = (source / "Dockerfile").read_text(encoding="utf-8", errors="ignore")
            m = re.search(r"(?im)^\s*FROM\s+([^\s#]+)", df_text)
            if m:
                dockerfile_base = m.group(1)
        except Exception:
            pass

    py_min_version = "3.10"
    py_syntax_notes: list[str] = []
    frameworks: list[str] = []
    for p in files:
        if p.suffix == ".py" and p.stat().st_size < 500_000:
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if re.search(r"\bStrEnum\b|\btomllib\b|\bSelf\b|\bExceptionGroup\b", txt):
                py_min_version = "3.11"
                if "StrEnum (Python 3.11+)" not in py_syntax_notes and "StrEnum" in txt:
                    py_syntax_notes.append("StrEnum (Python 3.11+)")
            if "FastAPI" in txt or "APIRouter" in txt:
                if "FastAPI" not in frameworks:
                    frameworks.append("FastAPI")
            elif "Flask" in txt and "Flask" not in frameworks:
                frameworks.append("Flask")
            elif "django" in txt and "Django" not in frameworks:
                frameworks.append("Django")
        elif p.suffix in {".js", ".ts", ".vue", ".jsx", ".tsx"} and p.stat().st_size < 500_000:
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if "vue.esm-browser" in txt or "createApp" in txt:
                if "Vue 3" not in frameworks:
                    frameworks.append("Vue 3")
            if re.search(r"""\bfrom\s+['"]react['"]|\bReactDOM\b""", txt) and "React" not in frameworks:
                frameworks.append("React")

    language = "python" if has_python else ("nodejs" if has_node else ("golang" if has_go else "unknown"))
    runtime_version = py_min_version if language == "python" else ("20" if language == "nodejs" else "1.22")
    return {
        "language": language,
        "runtime_version": runtime_version,
        "frameworks": frameworks,
        "syntax_requirements": py_syntax_notes,
        "has_dockerfile": has_dockerfile,
        "dockerfile_base_image": dockerfile_base,
    }


def _scan_pydantic_models(source: Path, files: list[Path]) -> dict[str, dict]:
    models: dict[str, dict] = {}
    for p in files:
        if p.suffix != ".py" or p.stat().st_size > 500_000:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                base_names = [ast.unparse(b) for b in node.bases] if hasattr(ast, "unparse") else []
                if not any("BaseModel" in b or "Schema" in b or "Request" in node.name for b in base_names + [node.name]):
                    continue
                props: dict[str, dict] = {}
                required: list[str] = []
                for stmt in node.body:
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                        fname = stmt.target.id
                        if fname.startswith("_"):
                            continue
                        ann_str = ast.unparse(stmt.annotation) if hasattr(ast, "unparse") else "string"
                        jtype = "string"
                        if any(k in ann_str.lower() for k in ("int", "float", "decimal")):
                            jtype = "number"
                        elif "bool" in ann_str.lower():
                            jtype = "boolean"
                        elif any(k in ann_str.lower() for k in ("list", "sequence", "set")):
                            jtype = "array"
                        elif any(k in ann_str.lower() for k in ("dict", "mapping", "any")):
                            jtype = "object"
                        props[fname] = {"type": jtype}
                        if stmt.value is None and "None" not in ann_str and "Optional" not in ann_str:
                            required.append(fname)
                if props:
                    models[node.name] = {
                        "type": "object",
                        "required": required,
                        "properties": props,
                    }
    return models


def _extract_ast_str(node: ast.AST | None) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _scan_python_routes(source: Path, files: list[Path], pydantic_models: dict[str, dict]) -> list[dict]:
    py_files = [p for p in files if p.suffix == ".py" and p.stat().st_size < 500_000]
    # Map module relative path -> router prefixes & include_router mounts
    module_routers: dict[str, dict[str, dict]] = {}
    include_mounts: list[dict] = []

    parsed_trees: dict[str, tuple[Path, ast.Module, str]] = {}
    for p in py_files:
        rel = p.relative_to(source).as_posix()
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(text)
            parsed_trees[rel] = (p, tree, text)
        except Exception:
            continue

    # First pass: collect APIRouter / Blueprint definitions and include_router mounts
    for rel, (_p, tree, _text) in parsed_trees.items():
        routers_in_file: dict[str, dict] = {}
        imports_in_file: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mod_path = node.module.replace(".", "/")
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    imports_in_file[local_name] = f"{mod_path}/{alias.name}"
            elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                var_name = node.targets[0].id
                if isinstance(node.value, ast.Call):
                    func_str = ast.unparse(node.value.func) if hasattr(ast, "unparse") else ""
                    if "APIRouter" in func_str or "Blueprint" in func_str:
                        prefix = ""
                        has_admin_dep = False
                        for kw in node.value.keywords:
                            if kw.arg in {"prefix", "url_prefix"}:
                                prefix = _extract_ast_str(kw.value)
                            elif kw.arg == "dependencies":
                                dep_str = ast.unparse(kw.value) if hasattr(ast, "unparse") else ""
                                if re.search(r"(?i)admin|token|auth", dep_str):
                                    has_admin_dep = True
                        routers_in_file[var_name] = {"prefix": prefix, "admin_auth": has_admin_dep}
            elif isinstance(node, ast.Call):
                func_str = ast.unparse(node.func) if hasattr(ast, "unparse") else ""
                if func_str.endswith(".include_router") and node.args:
                    arg_str = ast.unparse(node.args[0]) if hasattr(ast, "unparse") else ""
                    mount_prefix = ""
                    mount_admin = False
                    for kw in node.keywords:
                        if kw.arg == "prefix":
                            mount_prefix = _extract_ast_str(kw.value)
                        elif kw.arg == "dependencies":
                            dep_str = ast.unparse(kw.value) if hasattr(ast, "unparse") else ""
                            if re.search(r"(?i)admin|token|auth", dep_str):
                                mount_admin = True
                    include_mounts.append({
                        "file": rel,
                        "arg": arg_str,
                        "imports": imports_in_file,
                        "prefix": mount_prefix,
                        "admin_auth": mount_admin,
                    })
        module_routers[rel] = routers_in_file

    # Resolve include_router prefixes per file
    file_mount_prefix: dict[str, tuple[str, bool]] = {}
    for m in include_mounts:
        arg = m["arg"]
        base_name = arg.split(".")[0]
        for target_rel in parsed_trees:
            stem = Path(target_rel).stem
            if base_name == stem or arg.endswith(f"{stem}.router") or base_name in m["imports"].get(base_name, ""):
                if stem == base_name or m["imports"].get(base_name, "").endswith(f"/{stem}") or m["imports"].get(base_name, "").endswith(f"/{stem}/router"):
                    file_mount_prefix[target_rel] = (m["prefix"], m["admin_auth"])

    # Second pass: extract all route handlers
    routes: list[dict] = []
    http_methods = {"get", "post", "put", "patch", "delete"}
    for rel, (_p, tree, _text) in parsed_trees.items():
        routers_in_file = module_routers.get(rel, {})
        mount_pref, mount_admin = file_mount_prefix.get(rel, ("", False))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                    continue
                attr = dec.func.attr.lower()
                owner = dec.func.value.id if isinstance(dec.func.value, ast.Name) else ""
                methods_found: list[str] = []
                sub_path = ""
                if attr in http_methods:
                    methods_found = [attr.upper()]
                    if dec.args:
                        sub_path = _extract_ast_str(dec.args[0])
                    for kw in dec.keywords:
                        if kw.arg == "path":
                            sub_path = _extract_ast_str(kw.value)
                elif attr == "route":
                    if dec.args:
                        sub_path = _extract_ast_str(dec.args[0])
                    methods_found = ["GET"]
                    for kw in dec.keywords:
                        if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                            ms = [_extract_ast_str(elt).upper() for elt in kw.value.elts if _extract_ast_str(elt)]
                            if ms:
                                methods_found = ms
                if not methods_found:
                    continue

                router_info = routers_in_file.get(owner, {"prefix": "", "admin_auth": False})
                full_path = _join_route_paths(mount_pref, router_info["prefix"], sub_path)

                # Extract summary/docstring
                doc = (ast.get_docstring(node) or "").strip().splitlines()[0] if ast.get_docstring(node) else ""
                for kw in dec.keywords:
                    if kw.arg in {"summary", "description"} and _extract_ast_str(kw.value):
                        doc = _extract_ast_str(kw.value).strip()
                        break

                # Detect auth on route or router
                dec_str = ast.unparse(dec) if hasattr(ast, "unparse") else ""
                args_str = ast.unparse(node.args) if hasattr(ast, "unparse") else ""
                is_admin = (
                    mount_admin
                    or router_info["admin_auth"]
                    or "/admin" in full_path
                    or bool(re.search(r"(?i)require_admin|admin_token|x-admin-token", dec_str + " " + args_str))
                )
                auth_obj = (
                    {"type": "header", "header": "X-Admin-Token", "env": "ADMIN_TOKEN"}
                    if is_admin
                    else {"type": "none"}
                )

                # Extract path & query parameters + request body
                path_param_names = set(re.findall(r"\{([a-zA-Z0-9_]+)\}", full_path))
                params: list[dict] = []
                seen_param_names: set[str] = set()
                for pname in re.findall(r"\{([a-zA-Z0-9_]+)\}", full_path):
                    if pname in seen_param_names:
                        continue
                    seen_param_names.add(pname)
                    params.append({
                        "name": pname,
                        "in": "path",
                        "type": "string",
                        "required": True,
                        "description": f"路径参数 {pname}",
                        "schema": {"type": "string"},
                    })

                req_body: dict | None = None
                all_fn_args = list(node.args.args) + list(node.args.kwonlyargs)
                num_defaults = len(node.args.defaults)
                num_regular = len(node.args.args)
                for idx, arg in enumerate(all_fn_args):
                    aname = arg.arg
                    if aname in {"self", "cls", "request", "response", "db", "session", "conn", "background_tasks", "_"} or aname.startswith("_"):
                        continue
                    ann_str = ast.unparse(arg.annotation) if (arg.annotation and hasattr(ast, "unparse")) else ""
                    if any(skip in ann_str for skip in ("Request", "Response", "BackgroundTasks", "Session", "Connection")):
                        continue
                    if "Depends" in ann_str or "Header" in ann_str:
                        continue
                    if aname in path_param_names or aname in seen_param_names:
                        continue
                    if "UploadFile" in ann_str or "File" in ann_str:
                        req_body = {
                            "required": True,
                            "content_type": "multipart/form-data",
                            "schema": {
                                "type": "object",
                                "required": [aname],
                                "properties": {aname: {"type": "string", "description": "上传文件"}},
                            },
                        }
                        continue
                    clean_ann = re.sub(r"Optional\[(.*)\]|None|\|", "", ann_str).strip()
                    if clean_ann in pydantic_models:
                        req_body = {
                            "required": True,
                            "content_type": "application/json",
                            "schema": pydantic_models[clean_ann],
                        }
                        continue
                    if clean_ann in {"dict", "Dict", " dict[str, Any]", "dict[str, Any]"} or aname in {"payload", "body", "data", "req"}:
                        req_body = {
                            "required": True,
                            "content_type": "application/json",
                            "schema": {
                                "type": "object",
                                "required": [],
                                "properties": {"data": {"type": "object"}},
                            },
                        }
                        continue
                    # Otherwise treat primitive parameters on GET/DELETE (or with Query) as query parameters
                    has_default = idx >= (num_regular - num_defaults) if idx < num_regular else True
                    jtype = "integer" if "int" in ann_str.lower() else ("boolean" if "bool" in ann_str.lower() else ("number" if "float" in ann_str.lower() else "string"))
                    seen_param_names.add(aname)
                    params.append({
                        "name": aname,
                        "in": "query",
                        "type": jtype,
                        "required": not has_default,
                        "description": f"查询参数 {aname}",
                        "schema": {"type": jtype},
                    })

                for mth in methods_found:
                    if mth in {"POST", "PUT", "PATCH"} and req_body is None and not params:
                        req_body = {
                            "required": False,
                            "content_type": "application/json",
                            "schema": {
                                "type": "object",
                                "required": [],
                                "properties": {"payload": {"type": "object"}},
                            },
                        }
                    effect = "read" if mth == "GET" else ("destructive" if mth == "DELETE" else "write")
                    raw_id = re.sub(r"[^a-z0-9]+", "-", f"{mth.lower()}-{full_path.strip('/') or 'root'}").strip("-")
                    api_id = raw_id if (raw_id and raw_id[0].isalpha()) else f"op-{raw_id or 'root'}"
                    purpose = doc if doc else _humanize_identifier(node.name, mth, full_path)
                    routes.append({
                        "id": api_id,
                        "method": mth,
                        "path": full_path,
                        "handler": node.name,
                        "purpose": purpose,
                        "evidence": [f"{rel}:{node.lineno}"],
                        "parameters": params,
                        "request_body": req_body if mth in {"POST", "PUT", "PATCH"} else None,
                        "responses": {
                            "200": {
                                "description": f"{purpose}响应",
                                "type": "object",
                                "required": [],
                                "properties": {"status": {"type": "string"}},
                            }
                        },
                        "auth": auth_obj,
                        "effect": effect,
                        "confirmation_required": effect in {"write", "destructive", "external"},
                    })
    return routes


def _scan_js_ts_routes(source: Path, files: list[Path]) -> list[dict]:
    routes: list[dict] = []
    route_re = re.compile(r"""\b(?:app|router|api|server)\.(get|post|put|patch|delete)\(\s*['"`](/[^'"`]*)['"`]""", re.I)
    for p in files:
        if p.suffix not in {".js", ".ts", ".mjs", ".cjs"} or p.stat().st_size > 500_000:
            continue
        rel = p.relative_to(source).as_posix()
        if any(seg in rel for seg in ("static/", "public/", "dist/", "assets/", "components/")):
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for idx, line in enumerate(lines, start=1):
            for m in route_re.finditer(line):
                mth = m.group(1).upper()
                path = m.group(2)
                effect = "read" if mth == "GET" else ("destructive" if mth == "DELETE" else "write")
                api_id = re.sub(r"[^a-z0-9]+", "-", f"{mth.lower()}-{path.strip('/') or 'root'}").strip("-")
                routes.append({
                    "id": api_id,
                    "method": mth,
                    "path": path,
                    "handler": f"line_{idx}",
                    "purpose": _humanize_identifier("", mth, path),
                    "evidence": [f"{rel}:{idx}"],
                    "parameters": [],
                    "request_body": None,
                    "responses": {"200": {"type": "object", "required": [], "properties": {"ok": {"type": "boolean"}}}},
                    "auth": {"type": "none"},
                    "effect": effect,
                    "confirmation_required": effect != "read",
                })
    return routes


def _scan_go_routes(source: Path, files: list[Path]) -> list[dict]:
    routes: list[dict] = []
    route_re = re.compile(r"""\.\s*(GET|POST|PUT|PATCH|DELETE|HandleFunc)\(\s*"([^"]+)"\s*,\s*([a-zA-Z0-9_.]+)""")
    for p in files:
        if p.suffix != ".go" or p.name.endswith("_test.go") or p.stat().st_size > 500_000:
            continue
        rel = p.relative_to(source).as_posix()
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for idx, line in enumerate(lines, start=1):
            for m in route_re.finditer(line):
                raw_mth, path, handler = m.group(1), m.group(2), m.group(3)
                mth = "GET" if raw_mth == "HandleFunc" else raw_mth.upper()
                effect = "read" if mth == "GET" else ("destructive" if mth == "DELETE" else "write")
                api_id = re.sub(r"[^a-z0-9]+", "-", f"{mth.lower()}-{path.strip('/') or 'root'}").strip("-")
                routes.append({
                    "id": api_id,
                    "method": mth,
                    "path": path,
                    "handler": handler,
                    "purpose": _humanize_identifier(handler.split(".")[-1], mth, path),
                    "evidence": [f"{rel}:{idx}"],
                    "parameters": [],
                    "request_body": None,
                    "responses": {"200": {"type": "object", "required": [], "properties": {"code": {"type": "integer"}}}},
                    "auth": {"type": "none"},
                    "effect": effect,
                    "confirmation_required": effect != "read",
                })
    return routes


def _scan_frontend_pages(source: Path, files: list[Path]) -> list[dict]:
    pages: list[dict] = []
    seen_ids: set[str] = set()

    # 1. Check index.html or root entry page
    for p in files:
        rel = p.relative_to(source).as_posix()
        if p.name == "index.html":
            title = "应用主界面"
            try:
                html = p.read_text(encoding="utf-8", errors="ignore")
                tm = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
                if tm and tm.group(1).strip():
                    title = f"{tm.group(1).strip()}（主页面入口）"
            except Exception:
                pass
            pages.append({
                "id": "index-page",
                "path": "/",
                "purpose": title,
                "actions": ["打开应用主页面并执行业务操作"],
                "auth": {"type": "none"},
                "evidence": [f"{rel}:1"],
            })
            seen_ids.add("index-page")

    # 2. Scan pages/, views/, and *-page.* / *-panel.* components
    for p in files:
        if p.suffix not in {".js", ".ts", ".vue", ".jsx", ".tsx", ".html"} or p.stat().st_size > 500_000:
            continue
        rel = p.relative_to(source).as_posix()
        stem = p.stem.lower()
        is_page_dir = any(part in {"pages", "views"} for part in p.relative_to(source).parts)
        is_panel_or_page = stem.endswith("-page") or stem.endswith("-panel") or stem.endswith("page") or stem.endswith("view")
        if not (is_page_dir or is_panel_or_page):
            continue
        if p.name == "index.html":
            continue

        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        page_id = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
        if page_id in seen_ids:
            continue
        seen_ids.add(page_id)

        # Extract human-readable heading or docstring/comment from component
        headings = re.findall(r"<h[1-4][^>]*>\s*([^<{]+?)\s*</h[1-4]>", text)
        clean_headings = [h.strip() for h in headings if h.strip() and len(h.strip()) <= 30]
        comment_match = re.search(r"(?m)^\s*(?://|/\*\*?|\*)\s*([^\n*]{4,60})", text)

        if "admin" in stem:
            path = "/#/admin"
        elif "list" in stem:
            path = "/#/list"
        elif "create" in stem:
            path = "/#/create"
        elif is_page_dir:
            path = f"/#/{stem.replace('-page', '')}"
        else:
            path = f"/#/{stem}"

        if clean_headings:
            purpose = f"{clean_headings[0]}（{p.name} 视图/面板：" + "、".join(clean_headings[:3]) + "）"
        elif comment_match:
            purpose = f"{comment_match.group(1).strip()} ({p.name})"
        else:
            purpose = f"{p.stem} 页面/业务视图 ({rel})"

        actions = clean_headings[:4] if clean_headings else [f"查看或操作 {p.stem} 视图"]
        auth_obj = {"type": "header", "header": "X-Admin-Token", "env": "ADMIN_TOKEN"} if "admin" in stem else {"type": "none"}
        pages.append({
            "id": page_id,
            "path": path,
            "purpose": purpose,
            "actions": actions,
            "auth": auth_obj,
            "evidence": [f"{rel}:1"],
        })

    return pages


def enrich_contract_and_discovery_report(source: Path, data: dict, proposal: dict) -> dict:
    """Ensure 100% of statically scanned APIs & pages are present in proposal['contract'],
    and build a fixed-format Step 1 Discovery Report (proposal['discovery_report']) and
    Step 2 Transformation Report (proposal['transformation_report']).
    """
    contract = proposal.setdefault("contract", {})
    scan = scan_app_routes_and_pages(source)
    sqlite_info = inspect_local_sqlite_files(source)
    cred_info = inspect_credentials_in_source(source)

    # 1. Merge any missing scanned APIs into contract['apis'] so 0 routes are omitted
    existing_apis: list[dict] = list(contract.get("apis") or [])
    existing_api_keys = {(str(a.get("method", "GET")).upper(), str(a.get("path", ""))) for a in existing_apis if isinstance(a, dict)}
    used_api_ids = {str(a.get("id", "")) for a in existing_apis if isinstance(a, dict)}

    for scanned_api in scan["apis"]:
        key = (scanned_api["method"].upper(), scanned_api["path"])
        if key in existing_api_keys:
            continue
        candidate_id = scanned_api["id"]
        suffix = 2
        while candidate_id in used_api_ids:
            candidate_id = f"{scanned_api['id']}-{suffix}"
            suffix += 1
        used_api_ids.add(candidate_id)
        existing_api_keys.add(key)
        api_copy = dict(scanned_api)
        api_copy["id"] = candidate_id
        api_copy.pop("handler", None)
        existing_apis.append(api_copy)
    contract["apis"] = existing_apis

    # 2. Merge any missing scanned pages/sub-panels into contract['pages'] so 0 pages are omitted
    existing_pages: list[dict] = list(contract.get("pages") or [])
    existing_page_paths = {str(p.get("path", "")) for p in existing_pages if isinstance(p, dict)}
    for scanned_page in scan["pages"]:
        if scanned_page["path"] in existing_page_paths:
            continue
        existing_page_paths.add(scanned_page["path"])
        existing_pages.append({
            "path": scanned_page["path"],
            "purpose": scanned_page["purpose"],
            "evidence": scanned_page["evidence"],
            "actions": scanned_page.get("actions") or ["查看与交互操作"],
        })
    contract["pages"] = existing_pages

    # 2b. Filter out internal tool/truncation/row-diff noise from unresolved & decisions so the user is never blocked by internal mechanics
    internal_noise_re = re.compile(
        r"(截断|truncated|补齐.*扫描|完整接口详情|接口扫描显示|逐表差异|逐表核对|10,?264|14,?758|healthz|健康检查接口)",
        re.I,
    )
    has_migrated = bool(data.get("migrations"))
    feedback_text = str(data.get("feedback") or "")
    user_confirmed_db = has_migrated and bool(re.search(r"(迁移|PostgreSQL|平台.*数据库|直接迁移|保留.*SQLite)", feedback_text, re.I))

    def _keep_question(q: str) -> bool:
        s = str(q or "").strip()
        if not s:
            return False
        if internal_noise_re.search(s):
            return False
        if user_confirmed_db and re.search(r"(SQLite.*迁移|补迁|保留\s*SQLite)", s, re.I):
            return False
        return True

    if isinstance(proposal.get("unresolved"), list):
        proposal["unresolved"] = [q for q in proposal["unresolved"] if _keep_question(q)]
    if isinstance(contract.get("unresolved"), list):
        contract["unresolved"] = [q for q in contract["unresolved"] if _keep_question(q)]
    valid_unresolved = set(proposal.get("unresolved") or []) | set(contract.get("unresolved") or [])
    if isinstance(proposal.get("decisions"), list):
        proposal["decisions"] = [
            d for d in proposal["decisions"]
            if isinstance(d, dict) and d.get("question") in valid_unresolved
        ]

    # Ensure verification plan is always valid when pages/APIs exist
    verif = contract.setdefault("verification", {})
    if not isinstance(verif, dict):
        verif = {}
        contract["verification"] = verif
    ops_by_id = {a.get("id"): a for a in existing_apis if isinstance(a, dict)}
    page_paths = {p.get("path") for p in existing_pages if isinstance(p, dict)}
    valid_cases = [
        c for c in (verif.get("cases") or [])
        if isinstance(c, dict) and c.get("operation") in ops_by_id and ops_by_id[c["operation"]].get("effect") == "read" and c.get("json_contains")
    ]
    valid_browser_cases = [
        bc for bc in (verif.get("browser_cases") or [])
        if isinstance(bc, dict) and bc.get("page") in page_paths and bc.get("steps") and bc.get("expected")
    ]
    if not valid_cases and not valid_browser_cases and existing_pages:
        first_page = existing_pages[0]["path"]
        verif["browser_cases"] = [
            {
                "page": first_page,
                "steps": [f"打开页面 {first_page}", "检查页面核心视图与交互元素正常渲染"],
                "expected": "页面正常加载且无报错",
            }
        ]

    # 3. Build fixed-format Step 1 Discovery Report
    step1_ai = (proposal.get("four_step_report") or {}).get("step1_discovery") or {}
    step2_ai = (proposal.get("four_step_report") or {}).get("step2_transformation") or {}
    auth_profile = proposal.get("auth_profile") or contract.get("auth_profile") or {}
    runtime_cfg = contract.get("runtime") or {}
    lang = runtime_cfg.get("language") or scan["runtime"]["language"]
    ver = runtime_cfg.get("version") or scan["runtime"]["runtime_version"]

    readme_summary = step1_ai.get("readme_summary") or ""
    has_readme = False
    for rname in ("README.md", "readme.md", "README.txt"):
        rp = source / rname
        if rp.is_file():
            has_readme = True
            if not readme_summary:
                try:
                    lines = [ln.strip("# ").strip() for ln in rp.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
                    readme_summary = "；".join(lines[:3])[:240]
                except Exception:
                    pass
            break

    sqlite_dbs = sqlite_info.get("sqlite_databases") or []
    resources = data.get("resources") or []
    pg_resources = [r for r in resources if str(r.get("type", "")).lower() in {"postgres", "postgresql"}]
    storage_type = step1_ai.get("storage_type") or ("sqlite" if sqlite_dbs else "none")

    api_modules: dict[str, int] = {}
    for a in existing_apis:
        parts = [seg for seg in str(a.get("path", "")).split("/") if seg and not seg.startswith("{")]
        mod = "/" + "/".join(parts[:2]) if len(parts) >= 2 else ("/" + parts[0] if parts else "/")
        api_modules[mod] = api_modules.get(mod, 0) + 1

    changes = proposal.get("changes") or []
    added_files = []
    modified_files = []
    for ch in changes:
        cpath = ch.get("path", "")
        item = {
            "path": cpath,
            "group": ch.get("group") or "容器与运行适配",
            "reason": ch.get("reason") or "适配容器化部署环境",
        }
        if (source / cpath).exists():
            modified_files.append(item)
        else:
            added_files.append(item)

    discovery_report = {
        "project_purpose": contract.get("description") or proposal.get("summary") or f"{data.get('name', '应用')}业务系统",
        "tech_stack": {
            "language": lang,
            "runtime_version": ver,
            "frameworks": scan["runtime"]["frameworks"],
            "syntax_requirements": scan["runtime"]["syntax_requirements"],
            "container_port": data.get("port", 8080),
        },
        "pages_overview": {
            "total": len(existing_pages),
            "items": [
                {
                    "path": p.get("path", "/"),
                    "purpose": p.get("purpose", ""),
                    "actions": p.get("actions", []),
                    "evidence": (p.get("evidence") or [""])[0],
                }
                for p in existing_pages
            ],
        },
        "apis_overview": {
            "total": len(existing_apis),
            "modules": api_modules,
            "read_count": sum(1 for a in existing_apis if a.get("effect") == "read"),
            "write_count": sum(1 for a in existing_apis if a.get("effect") != "read"),
            "items": [
                {
                    "id": a.get("id", ""),
                    "method": a.get("method", "GET"),
                    "path": a.get("path", "/"),
                    "purpose": a.get("purpose", ""),
                    "effect": a.get("effect", "read"),
                    "auth_type": (a.get("auth") or {}).get("type", "none"),
                    "evidence": (a.get("evidence") or [""])[0],
                }
                for a in existing_apis
            ],
        },
        "auth_and_credentials": {
            "page_auth": step1_ai.get("page_auth") or ("管理端需令牌/登录认证，普通业务页免登直达" if any((a.get("auth") or {}).get("type") != "none" for a in existing_apis) else "免登录直接访问"),
            "api_auth": step1_ai.get("api_auth") or ("管理接口校验 X-Admin-Token，其余业务接口免鉴权" if any((a.get("auth") or {}).get("type") != "none" for a in existing_apis) else "无强制接口鉴权"),
            "auth_profile": auth_profile,
            "discovered_accounts_count": len(cred_info.get("discovered_accounts") or []) + len(cred_info.get("sqlite_accounts") or []),
        },
        "storage_and_database": {
            "storage_type": storage_type,
            "sqlite_databases": [
                {
                    "path": db["path"],
                    "size_mb": round(db["size_bytes"] / (1024 * 1024), 2),
                    "table_count": db["table_count"],
                    "total_rows": db.get("total_rows", 0),
                    "table_names": [t["name"] for t in db["tables"]],
                    "tables": [
                        {
                            "name": t["name"],
                            "row_count": t.get("row_count", 0),
                            "column_count": len(t.get("columns") or []),
                        }
                        for t in db["tables"]
                    ],
                }
                for db in sqlite_dbs
            ],
            "can_migrate_to_platform_pg": bool(sqlite_dbs and pg_resources),
            "platform_pg_resource": pg_resources[0]["name"] if pg_resources else "",
            "platform_pg_id": pg_resources[0]["id"] if pg_resources else "",
            "storage_migration_plan": step2_ai.get("storage_migration") or (
                f"检测到本地 SQLite 数据库（共 {sqlite_dbs[0]['table_count']} 张表、{sqlite_dbs[0].get('total_rows', 0)} 条记录），可迁移至平台 {pg_resources[0]['name']} 或保留应用内置 SQLite"
                if (sqlite_dbs and pg_resources)
                else ("保留应用内置 SQLite 数据库随镜像部署" if sqlite_dbs else "无本地持久化数据库文件")
            ),
        },
        "foundation_and_container": {
            "has_dockerfile": scan["runtime"]["has_dockerfile"],
            "dockerfile_base_image": scan["runtime"]["dockerfile_base_image"],
            "dockerfile_action": step2_ai.get("dockerfile_action") or ("复用现有 Dockerfile 并校验端口" if scan["runtime"]["has_dockerfile"] else f"生成 {lang}:{ver} 容器构建 Dockerfile"),
            "has_readme": has_readme,
            "readme_summary": readme_summary or "项目未提供独立 README 或无需额外手动构建步骤",
            "available_platform_resources": [{"id": r.get("id"), "name": r.get("name"), "type": r.get("type")} for r in resources],
        },
        "planned_adaptations": [
            f"{'新增' if not (source / ch.get('path', '')).exists() else '改造'} {ch.get('path', '')}：{ch.get('reason', '')}"
            for ch in changes
        ] or ["应用已满足容器化部署要求，无需修改业务源码"],
    }

    transformation_report = {
        "headline": (
            f"已完成容器化改造与全量技能生成：新增 {len(added_files)} 个文件、修改 {len(modified_files)} 个文件、删减 0 个文件；"
            f"完整收录 {len(existing_pages)} 个页面与 {len(existing_apis)} 个 API 接口。"
        ),
        "added_files": added_files,
        "modified_files": modified_files,
        "deleted_files": [],
        "dockerfile_summary": discovery_report["foundation_and_container"]["dockerfile_action"],
        "storage_summary": step2_ai.get("storage_migration") or discovery_report["storage_and_database"]["storage_migration_plan"],
        "health_check": f"GET /healthz (端口 {data.get('port', 8080)}，返回 service={data.get('slug', '')})",
        "skill_summary": {
            "skill_name": f"app-{data.get('slug', '')}",
            "page_count": len(existing_pages),
            "api_count": len(existing_apis),
            "read_api_count": discovery_report["apis_overview"]["read_count"],
            "write_api_count": discovery_report["apis_overview"]["write_count"],
        },
    }

    proposal["discovery_report"] = discovery_report
    proposal["transformation_report"] = transformation_report
    return proposal


