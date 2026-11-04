"""Step 1 & Step 2 helpers for OPC App Delivery:
- Credential discovery & code-assisted decryption from isolated source/SQLite/JSON/config
- Dockerfile template fetching from platform-opc (deploy-opc database)
- Local SQLite / local DB (PostgreSQL, MySQL, SQLServer) / local Redis inspection and automatic migration to platform-opc server resources
"""

from __future__ import annotations

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

    for p in files_to_scan[:200]:
        rel = p.relative_to(source).as_posix()
        suffix = p.suffix.lower()
        if suffix in {".sqlite", ".sqlite3", ".db"}:
            sqlite_items = _scan_sqlite_accounts(p, rel)
            result["sqlite_accounts"].extend(sqlite_items)
            for item in sqlite_items:
                result["decrypted_candidates"].extend(item.get("decoded_candidates", []))
            continue
        if p.stat().st_size > 1_000_000:
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
            for pwd in passwords[:10]:
                decoded_pwds.extend(_try_decode_cipher(pwd, algorithm_hint, source))
            result["decrypted_candidates"].extend(decoded_pwds)
            result["discovered_accounts"].append({
                "file": rel,
                "usernames": list(dict.fromkeys(users[:5])),
                "passwords_or_hashes": list(dict.fromkeys(passwords[:5])),
                "tokens": list(dict.fromkeys(tokens[:5])),
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
                rows = conn.execute(f'SELECT * FROM "{tbl}" LIMIT 10').fetchall()
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
        if p.is_file():
            candidates.append(p)
    else:
        for p in sorted(source.rglob("*")):
            if p.is_file() and p.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
                candidates.append(p)

    databases = []
    for db_file in candidates:
        rel = db_file.relative_to(source).as_posix()
        tables = extract_sqlite_tables(db_file, max_rows_per_table=5)
        databases.append({
            "path": rel,
            "size_bytes": db_file.stat().st_size,
            "table_count": len(tables),
            "tables": [
                {
                    "name": t["name"],
                    "columns": t["columns"],
                    "row_count_preview": len(t["rows"]),
                }
                for t in tables
            ],
        })
    return {"sqlite_databases": databases}


def extract_sqlite_tables(sqlite_file: Path, max_rows_per_table: int = 5000) -> list[dict]:
    """Read full schema (columns + types + PKs) and rows from a local SQLite file for server migration."""
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
            rows_raw = conn.execute(f'SELECT * FROM "{table_name}" LIMIT {int(max_rows_per_table)}').fetchall()
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
    """Extract schema & data from local SQLite (or provided tables/redis_keys) and upload to platform-opc POST /api/v1/apps/:slug/migrate-data."""
    tables = tables_override or []
    if not tables and source_type.lower() == "sqlite":
        if sqlite_path.strip():
            db_file = delivery.relative(source_dir, sqlite_path.strip())
        else:
            found = [p for p in sorted(source_dir.rglob("*")) if p.is_file() and p.suffix.lower() in {".sqlite", ".sqlite3", ".db"}]
            if not found:
                raise ValueError("未在源码目录中找到 SQLite 数据库文件")
            db_file = found[0]
        tables = extract_sqlite_tables(db_file)

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
        with opener.open(req, timeout=30) as resp:
            resp_json = json.loads(resp.read().decode("utf-8"))
            if resp_json.get("code") == 0 and resp_json.get("data"):
                return resp_json["data"]
            return resp_json
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"平台数据迁移接口返回错误 ({exc.code}): {detail}") from exc
    except Exception as exc:
        raise ValueError(f"调用平台数据迁移接口失败: {exc}") from exc
