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


_PERSISTENT_DIR_CATALOG: dict[str, dict[str, str]] = {
    "data": {"purpose": "业务状态与 JSON/本地数据文件持久化目录", "kind": "data_dir"},
    "uploads": {"purpose": "用户上传文件与现场证据附件持久化目录", "kind": "upload_dir"},
    "upload": {"purpose": "用户上传文件持久化目录", "kind": "upload_dir"},
    "reports": {"purpose": "评估报告与导出文件持久化目录", "kind": "report_dir"},
    "backups": {"purpose": "业务数据快照与备份归档目录", "kind": "backup_dir"},
    "backup": {"purpose": "业务数据快照与备份归档目录", "kind": "backup_dir"},
    "storage": {"purpose": "应用运行时本地持久化存储目录", "kind": "data_dir"},
    "attachments": {"purpose": "业务附件与凭证持久化目录", "kind": "upload_dir"},
    "exports": {"purpose": "业务导出报表持久化目录", "kind": "report_dir"},
    "db_data": {"purpose": "本地数据库与数据文件持久化目录", "kind": "data_dir"},
}

_EXCLUDED_PERSISTENT_PARENT_DIRS = {
    "src", "lib", "app", "web", "public", "static", "assets", "views", "pages",
    "components", "routes", "controllers", "models", "services", "utils", "helpers",
    "middleware", "test", "tests", "__tests__", "spec", "docs", "doc", "miniprogram",
    "node_modules", "vendor", "dist", "build", ".git", ".github", ".vscode", ".idea",
}

_SOURCE_CODE_EXTENSIONS = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".go", ".java", ".kt", ".cs", ".rb", ".php", ".vue", ".svelte",
}

_CODE_MUTABLE_DIR_RE = re.compile(
    r"""(?i)(?:join|resolve)\s*\([^)]*?['"](data|uploads|upload|reports|backups|backup|storage|attachments|exports|db_data)['"]\s*\)|"""
    r"""(?:DATA_DIR|UPLOAD_DIR|UPLOAD_FOLDER|REPORT_DIR|BACKUP_DIR|STORAGE_DIR)\s*(?:=|\|\||\?\?)\s*[^;\n]*?['"](/[^'"]+|data|uploads|upload|reports|backups|backup|storage|attachments|exports)['"]"""
)


def inspect_local_file_storage(
    source: Path,
    slug: str = "",
    workdir: str = "/app",
    has_migrations: bool = False,
) -> dict:
    """Detect local JSON/file data directories, upload folders, report/backup folders, and seed files
    that require external persistent volume mounting (Docker Volume / Host Bind / S3-OSS).
    """
    clean_workdir = "/" + (workdir or "/app").strip().strip("/")
    clean_slug = re.sub(r"[^a-z0-9-]+", "-", (slug or "app").lower()).strip("-") or "app"

    dir_seed_files: dict[str, list[str]] = {}
    dir_has_code: set[str] = set()
    code_referenced_dirs: set[str] = set()

    for p in sorted(source.rglob("*")):
        if p.is_symlink():
            continue
        rel_parts = p.relative_to(source).parts
        if not rel_parts or any(part in delivery.EXCLUDED for part in rel_parts):
            continue
        top = rel_parts[0]
        if top in _EXCLUDED_PERSISTENT_PARENT_DIRS:
            continue

        # 1. Check top-level persistent directories (e.g. data/, uploads/, reports/, backups/)
        if top in _PERSISTENT_DIR_CATALOG:
            if p.is_dir() and len(rel_parts) == 1:
                dir_seed_files.setdefault(top, [])
            elif p.is_file():
                ext = p.suffix.lower()
                if ext in _SOURCE_CODE_EXTENSIONS:
                    dir_has_code.add(top)
                elif not (has_migrations and ext in {".sqlite", ".sqlite3", ".db"}):
                    dir_seed_files.setdefault(top, []).append(p.relative_to(source).as_posix())

        # 2. Check second-level persistent directories (e.g. reimburse-web/server/data)
        if len(rel_parts) >= 2 and top not in _PERSISTENT_DIR_CATALOG and not any(part in _EXCLUDED_PERSISTENT_PARENT_DIRS for part in rel_parts[:-1]):
            if p.is_dir() and len(rel_parts) <= 3 and rel_parts[-1] in _PERSISTENT_DIR_CATALOG:
                rel_dir = p.relative_to(source).as_posix()
                dir_seed_files.setdefault(rel_dir, [])
            elif p.is_file():
                for idx in range(2, min(4, len(rel_parts))):
                    cand_name = rel_parts[idx - 1]
                    if cand_name in _PERSISTENT_DIR_CATALOG:
                        rel_dir = "/".join(rel_parts[:idx])
                        ext = p.suffix.lower()
                        if ext in _SOURCE_CODE_EXTENSIONS:
                            dir_has_code.add(rel_dir)
                        elif not (has_migrations and ext in {".sqlite", ".sqlite3", ".db"}):
                            dir_seed_files.setdefault(rel_dir, []).append(p.relative_to(source).as_posix())

        # 3. Scan source code for runtime mutable directories (e.g. join(root, "uploads"), join(root, "reports"))
        if p.is_file() and p.suffix.lower() in {".js", ".mjs", ".cjs", ".ts", ".py", ".go", ".java", ".cs"} and p.stat().st_size <= 2_000_000:
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for m in _CODE_MUTABLE_DIR_RE.finditer(text):
                raw_dir = (m.group(1) or m.group(2) or "").strip().strip("/")
                if not raw_dir:
                    continue
                base_dir = raw_dir.split("/")[-1].lower()
                if base_dir in _PERSISTENT_DIR_CATALOG:
                    code_referenced_dirs.add(base_dir)

    # SQLite may live outside conventional data/ directories. Mount its parent,
    # including WAL/SHM companions, but never cover application code or the root.
    for db_file in sorted(source.rglob("*")):
        if db_file.is_symlink() or not db_file.is_file():
            continue
        if db_file.suffix.lower() not in {".sqlite", ".sqlite3", ".db"} and not delivery.is_sqlite(db_file):
            continue
        rel = db_file.relative_to(source)
        if len(rel.parts) < 2 or any(part in delivery.EXCLUDED or part in _EXCLUDED_PERSISTENT_PARENT_DIRS for part in rel.parts[:-1]):
            continue
        parent = db_file.parent
        if any(p.suffix.lower() in _SOURCE_CODE_EXTENSIONS for p in parent.rglob("*") if p.is_file()):
            continue
        rel_dir = parent.relative_to(source).as_posix()
        dir_seed_files.setdefault(rel_dir, []).extend(p.relative_to(source).as_posix() for p in parent.rglob("*") if p.is_file() and not p.is_symlink())

    for d in sorted(code_referenced_dirs):
        if d not in dir_has_code:
            dir_seed_files.setdefault(d, [])

    candidate_dirs = sorted(d for d in dir_seed_files if d not in dir_has_code)
    # Deduplicate nested directories (if both 'data' and 'data/uploads' matched, keep outer 'data')
    filtered_dirs: list[str] = []
    for d in candidate_dirs:
        if any(parent != d and d.startswith(parent + "/") for parent in candidate_dirs):
            continue
        # Only keep a directory if it either has seed files OR was explicitly referenced in code OR is a top-level existing directory
        if dir_seed_files.get(d) or d in code_referenced_dirs or ("/" not in d and (source / d).is_dir()):
            filtered_dirs.append(d)

    mounts: list[dict] = []
    for rel_dir in filtered_dirs:
        base_name = rel_dir.split("/")[-1].lower()
        meta = _PERSISTENT_DIR_CATALOG.get(base_name, {"purpose": "应用持久化数据目录", "kind": "data_dir"})
        seeds = sorted(set(dir_seed_files.get(rel_dir, [])))
        container_path = f"{clean_workdir}/{rel_dir}".replace("//", "/")
        vol_suffix = re.sub(r"[^a-z0-9-]+", "-", rel_dir.lower()).strip("-") or base_name
        purpose = meta["purpose"]
        if seeds:
            sample_names = ", ".join(Path(s).name for s in seeds[:3])
            if len(seeds) > 3:
                sample_names += f" 等 {len(seeds)} 个文件"
            purpose = f"{meta['purpose']}（含初始种子文件：{sample_names}）"
        mounts.append({
            "name": base_name if "/" not in rel_dir else vol_suffix,
            "relative_path": rel_dir,
            "container_path": container_path,
            "purpose": purpose,
            "kind": meta["kind"],
            "volume_name": f"opc-vol-{clean_slug}-{vol_suffix}",
            "storage_backend": "docker_volume",
            "seed_files_count": len(seeds),
            "seed_files": seeds[:10],
        })

    return {
        "has_persistent_file_storage": bool(mounts),
        "storage_mounts": mounts,
    }


def inspect_local_sqlite_files(source: Path, sqlite_rel_path: str = "") -> dict:
    """Inspect SQLite database files AND local JSON/file storage mounts inside the application source directory."""
    candidates = []
    if sqlite_rel_path.strip():
        p = delivery.relative(source, sqlite_rel_path.strip())
        if p.is_file() and p.stat().st_size > 0:
            candidates.append(p)
    else:
        for p in sorted(source.rglob("*")):
            if p.is_file() and not p.is_symlink() and p.stat().st_size > 0:
                if p.suffix.lower() in {".sqlite", ".sqlite3", ".db"} or delivery.is_sqlite(p):
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
    file_storage = inspect_local_file_storage(source)
    return {
        "sqlite_databases": databases,
        "local_file_storage": file_storage,
    }


def extract_sqlite_tables(sqlite_file: Path, max_rows_per_table: int | None = None) -> list[dict]:
    """Read full schema (columns + types + PKs) and ALL rows from a local SQLite file for server migration."""
    tables = []
    preexisting_aux = {
        sfx: Path(f"{sqlite_file}{sfx}").exists()
        for sfx in ("-shm", "-wal")
    }
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
        # Clean up only transient shm/wal files created during readonly inspection that did not exist beforehand
        for sfx, existed in preexisting_aux.items():
            if existed:
                continue
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
        "supplier": "供应商",
        "suppliers": "供应商",
        "task": "任务",
        "tasks": "任务",
        "checklist": "检查表",
        "corrective": "整改项",
        "action": "处理项",
        "actions": "处理项",
        "evidence": "现场证据",
        "finding": "发现项",
        "findings": "发现项",
        "signature": "签字确认",
        "signatures": "签字确认",
        "session": "登录会话",
        "directory": "目录",
        "activity": "活动记录",
        "standard": "标准版本",
        "reset": "重置",
        "state": "状态数据",
        "report": "评估报告",
        "exports": "导出记录",
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

    # 3. Scan backend API routes across specs & frameworks
    apis: list[dict] = []
    # 3.1 OpenAPI / Swagger specifications (highest fidelity)
    apis.extend(_scan_openapi_specs(source, source_files))
    # 3.2 Python routes (FastAPI, Flask, Django, DRF, Sanic, etc.)
    apis.extend(_scan_python_routes(source, source_files, pydantic_models))
    # 3.3 Node.js / TypeScript routes (Express, Koa, Fastify, NestJS, Next.js, native HTTP)
    apis.extend(_scan_js_ts_routes(source, source_files))
    # 3.4 Go routes (Gin, Echo, Chi, Fiber, standard net/http, table-driven)
    apis.extend(_scan_go_routes(source, source_files))
    # 3.5 Java / Kotlin routes (Spring Boot, JAX-RS, JDK HttpServer)
    apis.extend(_scan_java_routes(source, source_files))
    # 3.6 C# / .NET routes (ASP.NET Core Minimal API, WebAPI Controllers)
    apis.extend(_scan_dotnet_routes(source, source_files))

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

    # 5. Scan local file storage (JSON state files, uploads, reports, backups)
    local_file_storage = inspect_local_file_storage(source)

    return {
        "runtime": runtime_info,
        "api_count": len(sorted_apis),
        "api_modules": api_modules,
        "apis": sorted_apis,
        "page_count": len(pages),
        "pages": pages,
        "local_file_storage": local_file_storage,
    }


def _scan_runtime_info(source: Path, files: list[Path]) -> dict:
    has_root_node = (source / "package.json").is_file()
    has_root_go = (source / "go.mod").is_file()
    has_root_py = (source / "requirements.txt").is_file() or (source / "pyproject.toml").is_file() or (source / "Pipfile").is_file() or (source / "setup.py").is_file()
    has_root_java = (source / "pom.xml").is_file() or (source / "build.gradle").is_file() or any(p.name.endswith(".java") and p.parent == source for p in files)
    has_root_dotnet = any(p.suffix == ".csproj" for p in files)
    has_dockerfile = (source / "Dockerfile").is_file()

    py_count = sum(1 for p in files if p.suffix == ".py")
    js_ts_count = sum(1 for p in files if p.suffix in {".js", ".ts", ".mjs", ".cjs", ".vue", ".jsx", ".tsx"})
    go_count = sum(1 for p in files if p.suffix == ".go")
    java_count = sum(1 for p in files if p.suffix in {".java", ".kt"})
    dotnet_count = sum(1 for p in files if p.suffix in {".cs", ".fs"})

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

    # Determine primary runtime language by weighted project artifacts rather than simple any()
    if has_root_node and (not has_root_py or js_ts_count >= py_count):
        language = "nodejs"
    elif has_root_go or (go_count > 0 and go_count >= py_count and go_count >= js_ts_count):
        language = "golang"
    elif has_root_dotnet or dotnet_count > 5:
        language = "dotnet"
    elif has_root_java or java_count > 5:
        language = "java"
    elif has_root_py or (py_count > 0 and py_count >= js_ts_count):
        language = "python"
    elif js_ts_count > 0:
        language = "nodejs"
    elif py_count > 0:
        language = "python"
    elif go_count > 0:
        language = "golang"
    else:
        language = "unknown"

    runtime_version = py_min_version if language == "python" else ("20" if language == "nodejs" else ("1.22" if language == "golang" else ("21" if language == "java" else ("8.0" if language == "dotnet" else "unknown"))))
    return {
        "language": language,
        "runtime_version": runtime_version,
        "frameworks": frameworks,
        "syntax_requirements": py_syntax_notes,
        "has_dockerfile": has_dockerfile,
        "dockerfile_base_image": dockerfile_base,
    }


def _scan_openapi_specs(source: Path, files: list[Path]) -> list[dict]:
    """Parse OpenAPI/Swagger JSON and YAML specs if present for highest fidelity API contracts."""
    routes: list[dict] = []
    spec_files: list[Path] = []
    for p in files:
        if p.suffix.lower() in {".json", ".yaml", ".yml"} and p.stat().st_size < 1_500_000:
            name_lower = p.name.lower()
            if any(name_lower.startswith(k) for k in ("swagger", "openapi")) or any(k in name_lower for k in ("swagger.", "openapi.")):
                spec_files.append(p)

    try:
        import yaml
    except Exception:
        yaml = None

    for p in spec_files:
        rel = p.relative_to(source).as_posix()
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
            if p.suffix.lower() == ".json":
                doc = json.loads(txt)
            elif yaml is not None:
                doc = yaml.safe_load(txt)
            else:
                continue
        except Exception:
            continue

        if not isinstance(doc, dict):
            continue
        base_path = str(doc.get("basePath") or "").strip()
        paths = doc.get("paths")
        if not isinstance(paths, dict):
            continue

        for path_pattern, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            full_path = _join_route_paths(base_path, path_pattern)
            for mth in ("get", "post", "put", "patch", "delete"):
                op = path_item.get(mth)
                if not isinstance(op, dict):
                    continue
                mth_upper = mth.upper()
                summary = str(op.get("summary") or op.get("description") or "").strip()
                op_id = op.get("operationId") or ""
                purpose = summary if summary else _humanize_identifier(op_id, mth_upper, full_path)
                effect = "read" if mth_upper == "GET" else ("destructive" if mth_upper == "DELETE" else "write")
                raw_id = re.sub(r"[^a-z0-9]+", "-", f"{mth}-{full_path.strip('/') or 'root'}").strip("-")
                api_id = op_id if op_id and op_id[0].isalpha() else (raw_id if raw_id and raw_id[0].isalpha() else f"op-{raw_id or 'root'}")

                params: list[dict] = []
                for param in op.get("parameters") or []:
                    if isinstance(param, dict) and param.get("name"):
                        p_in = param.get("in", "query")
                        params.append({
                            "name": param["name"],
                            "in": p_in,
                            "type": param.get("type", "string"),
                            "required": bool(param.get("required")),
                            "description": param.get("description") or f"参数 {param['name']}",
                            "schema": param.get("schema") or {"type": param.get("type", "string")},
                        })

                # Route discovery is not authentication analysis.
                auth_obj = {"type": "unknown"}

                routes.append({
                    "id": api_id,
                    "method": mth_upper,
                    "path": full_path,
                    "handler": op_id or f"openapi_{p.stem}",
                    "purpose": purpose,
                    "evidence": [f"{rel}:1"],
                    "parameters": params,
                    "request_body": None,
                    "responses": {"200": {"type": "object", "required": [], "properties": {"ok": {"type": "boolean"}}}},
                    "auth": auth_obj,
                    "effect": effect,
                    "confirmation_required": effect != "read",
                })
    return routes


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

                auth_obj = {"type": "unknown"}

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

    # Third pass: Django & Django REST Framework (urls.py)
    django_url_re = re.compile(r"""(?:path|re_path)\(\s*['"]([a-zA-Z0-9_\-/{}\<\>: \^\$\*\+\.\?]+)['"]\s*,\s*([a-zA-Z0-9_.]+)""")
    for rel, (_p, tree, text) in parsed_trees.items():
        if not ("urls" in rel or "urlpatterns" in text):
            continue
        for idx, line in enumerate(text.splitlines(), start=1):
            for m in django_url_re.finditer(line):
                raw_path = m.group(1).lstrip("^").rstrip("$")
                handler = m.group(2)
                # Normalize django path params <int:id> or <slug> to {id} or {slug}
                clean_path = "/" + re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", raw_path.strip("/"))
                api_id = re.sub(r"[^a-z0-9]+", "-", f"get-{clean_path.strip('/') or 'root'}").strip("-")
                routes.append({
                    "id": api_id,
                    "method": "GET",
                    "path": clean_path,
                    "handler": handler,
                    "purpose": _humanize_identifier(handler.split(".")[-1], "GET", clean_path),
                    "evidence": [f"{rel}:{idx}"],
                    "parameters": [],
                    "request_body": None,
                    "responses": {"200": {"type": "object", "required": [], "properties": {"ok": {"type": "boolean"}}}},
                    "auth": {"type": "unknown"},
                    "effect": "read",
                    "confirmation_required": False,
                })

    # Fourth pass: Python BaseHTTPRequestHandler (do_GET, do_POST)
    for rel, (_p, tree, text) in parsed_trees.items():
        if "BaseHTTPRequestHandler" not in text:
            continue
        for m in re.finditer(r"""def\s+(do_GET|do_POST|do_PUT|do_DELETE)\s*\(""", text):
            mth = m.group(1).replace("do_", "").upper()
            start_pos = m.start()
            method_block = text[start_pos:start_pos+3000]
            for pm in re.finditer(r"""(?:self\.path|url\.path)\s*==\s*['"](/[^'"]*)['"]""", method_block):
                path = pm.group(1)
                api_id = re.sub(r"[^a-z0-9]+", "-", f"{mth.lower()}-{path.strip('/') or 'root'}").strip("-")
                routes.append({
                    "id": api_id,
                    "method": mth,
                    "path": path,
                    "handler": m.group(1),
                    "purpose": _humanize_identifier("", mth, path),
                    "evidence": [f"{rel}:1"],
                    "parameters": [],
                    "request_body": None,
                    "responses": {"200": {"type": "object", "required": [], "properties": {"ok": {"type": "boolean"}}}},
                    "auth": {"type": "unknown"},
                    "effect": "read" if mth == "GET" else "write",
                    "confirmation_required": mth != "GET",
                })

    return routes


def _scan_js_ts_routes(source: Path, files: list[Path]) -> list[dict]:
    routes: list[dict] = []
    route_re = re.compile(r"""\b(?:app|router|api|server)\.(get|post|put|patch|delete)\(\s*['"`](/[^'"`]*)['"`]""", re.I)
    native_exact_re = re.compile(
        r"""(?:(?:request|req)\.method|method)\s*===\s*['"](GET|POST|PUT|PATCH|DELETE)['"]\s*&&\s*(?:url\.)?pathname\s*===\s*['"](/[^'"]+)['"]"""
        r"""|(?:url\.)?pathname\s*===\s*['"](/[^'"]+)['"]\s*&&\s*(?:(?:request|req)\.method|method)\s*===\s*['"](GET|POST|PUT|PATCH|DELETE)['"]""",
        re.I,
    )
    native_method_re = re.compile(r"""(?:(?:request|req)\.method|method)\s*===\s*['"](GET|POST|PUT|PATCH|DELETE)['"]""", re.I)
    native_starts_re = re.compile(r"""(?:url\.)?pathname\.startsWith\(\s*['"](/api/[^'"]+)['"]\s*\)""")
    native_ends_re = re.compile(r"""(?:url\.)?pathname\.endsWith\(\s*['"](/[^'"]+)['"]\s*\)""")
    native_includes_re = re.compile(r"""(?:url\.)?pathname\.includes\(\s*['"](/[^'"]+)['"]\s*\)""")

    def _make_js_route(mth: str, path: str, rel: str, idx: int) -> dict:
        mth = mth.upper()
        effect = "read" if mth == "GET" else ("destructive" if mth == "DELETE" else "write")
        raw_id = re.sub(r"[^a-z0-9]+", "-", f"{mth.lower()}-{path.strip('/') or 'root'}").strip("-")
        api_id = raw_id if (raw_id and raw_id[0].isalpha()) else f"op-{raw_id or 'root'}"
        path_params = []
        seen_p: set[str] = set()
        for pname in re.findall(r"\{([a-zA-Z0-9_]+)\}", path):
            if pname not in seen_p:
                seen_p.add(pname)
                path_params.append({
                    "name": pname,
                    "in": "path",
                    "type": "string",
                    "required": True,
                    "description": f"路径参数 {pname}",
                    "schema": {"type": "string"},
                })
        req_body = (
            {
                "required": False,
                "content_type": "application/json",
                "schema": {"type": "object", "required": [], "properties": {"payload": {"type": "object"}}},
            }
            if mth in {"POST", "PUT", "PATCH"}
            else None
        )
        return {
            "id": api_id,
            "method": mth,
            "path": path,
            "handler": f"line_{idx}",
            "purpose": _humanize_identifier("", mth, path),
            "evidence": [f"{rel}:{idx}"],
            "parameters": path_params,
            "request_body": req_body,
            "responses": {"200": {"type": "object", "required": [], "properties": {"ok": {"type": "boolean"}}}},
            "auth": {"type": "unknown"},
            "effect": effect,
            "confirmation_required": effect != "read",
        }

    # NestJS Controller decorators
    nest_controller_re = re.compile(r"""@Controller\(\s*['"]?([^'"]*?)['"]?\s*\)[\s\S]*?class\s+([A-Za-z0-9_]+)""")
    nest_method_re = re.compile(r"""@(Get|Post|Put|Patch|Delete)\(\s*['"]?([^'"]*?)['"]?\s*\)[\s\S]*?(?:async\s+)?([a-zA-Z0-9_]+)\s*\(""")

    for p in files:
        if p.suffix not in {".js", ".ts", ".mjs", ".cjs"} or p.stat().st_size > 500_000:
            continue
        rel = p.relative_to(source).as_posix()
        if any(seg in rel for seg in ("static/", "public/", "dist/", "assets/", "components/", "miniprogram/")):
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
            lines = content.splitlines()
        except Exception:
            continue

        for idx, line in enumerate(lines, start=1):
            for m in route_re.finditer(line):
                routes.append(_make_js_route(m.group(1), m.group(2), rel, idx))
            for m in native_exact_re.finditer(line):
                if "return false" in line:
                    continue
                mth = m.group(1) or m.group(4)
                path = m.group(2) or m.group(3)
                routes.append(_make_js_route(mth, path, rel, idx))
            # Node native if (url.pathname === '/api/...') without explicit method => GET
            if "return false" not in line and not native_exact_re.search(line):
                m_exact_path = re.search(r"""(?:url\.)?pathname\s*===\s*['"](/api/[^'"]+)['"]""", line)
                if m_exact_path:
                    routes.append(_make_js_route("GET", m_exact_path.group(1), rel, idx))
            if "return false" not in line and "pathname.startsWith" in line:
                m_mth = native_method_re.search(line)
                m_st = native_starts_re.search(line)
                if m_mth and m_st:
                    mth = m_mth.group(1)
                    prefix = m_st.group(1).rstrip("/")
                    m_end = native_ends_re.search(line)
                    m_inc = native_includes_re.search(line)
                    if m_end:
                        suffix = m_end.group(1)
                        synth_path = f"{prefix}/{{id}}{suffix}"
                    elif m_inc:
                        mid = m_inc.group(1).strip("/")
                        synth_path = f"{prefix}/{{id}}/{mid}/{{item_id}}"
                    else:
                        synth_path = f"{prefix}/{{id}}"
                    routes.append(_make_js_route(mth, synth_path, rel, idx))

        # Check NestJS
        if "@Controller" in content:
            m_ctrl = nest_controller_re.search(content)
            ctrl_prefix = m_ctrl.group(1).strip("/") if m_ctrl else ""
            for m in nest_method_re.finditer(content):
                mth, sub, handler = m.group(1).upper(), m.group(2).strip("/"), m.group(3)
                full = "/" + "/".join(p for p in (ctrl_prefix, sub) if p)
                routes.append(_make_js_route(mth, full, rel, 1))

    # Next.js App Router API routes (app/api/**/route.ts)
    for p in files:
        rel = p.relative_to(source).as_posix()
        if ("app/api/" in rel or "src/app/api/" in rel) and p.stem == "route" and p.suffix in {".js", ".ts"}:
            dir_path = p.parent.relative_to(source).as_posix()
            api_path = "/" + dir_path.split("app/", 1)[-1]
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")
                for mth in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                    if re.search(rf"\bexport\s+(?:async\s+)?function\s+{mth}\b", txt):
                        routes.append(_make_js_route(mth, api_path, rel, 1))
            except Exception:
                continue

    return routes


def _scan_go_routes(source: Path, files: list[Path]) -> list[dict]:
    routes: list[dict] = []
    # 1. Match direct router method calls (.GET, .POST, .Get, .Post, .HandleFunc)
    route_re = re.compile(r"""([a-zA-Z0-9_.]*)\s*\.\s*(GET|POST|PUT|PATCH|DELETE|HandleFunc|Get|Post|Put|Patch|Delete)\(\s*"([^"]+)"\s*,\s*([a-zA-Z0-9_.]+)""")
    # 2. Table-driven RouterEntry{Path: "...", Method: http.MethodPost, Handler: app.Run}
    table_re = re.compile(r"""(?:AppendRouters|RouterEntry)\s*\(\s*(?:([a-zA-Z0-9_]+)\s*,\s*)?sdk\.RouterEntry\s*\{\s*Path:\s*"([^"]+)"\s*,\s*Method:\s*(?:http\.Method)?([A-Za-z]+)\s*,\s*Handler:\s*([a-zA-Z0-9_.]+)""")
    # 3. Swaggo comments: // @Router /path [method]
    swaggo_router_re = re.compile(r"""//\s*@Router\s+([^\s\[]+)\s*\[(get|post|put|patch|delete)\]""", re.I)
    swaggo_summary_re = re.compile(r"""//\s*@Summary\s+(.+)""")

    # Map Group variables to path prefixes: v1 := r.Group("/api/v1")
    group_var_prefixes: dict[str, str] = {}
    const_prefixes: dict[str, str] = {"V1": "/api/v1", "v1": "/api/v1"}

    for p in files:
        if p.suffix != ".go" or p.name.endswith("_test.go") or p.stat().st_size > 500_000:
            continue
        rel = p.relative_to(source).as_posix()
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
            lines = content.splitlines()
        except Exception:
            continue

        # Extract const string definitions like const V1 = "/api/v1"
        for cm in re.finditer(r"""const\s*\(\s*([\s\S]*?)\)""", content):
            for line in cm.group(1).splitlines():
                m_kv = re.search(r"""([a-zA-Z0-9_]+)\s*=\s*"([^"]+)""", line)
                if m_kv:
                    const_prefixes[m_kv.group(1)] = m_kv.group(2)
        for m_kv in re.finditer(r"""const\s+([a-zA-Z0-9_]+)\s*=\s*"([^"]+)""", content):
            const_prefixes[m_kv.group(1)] = m_kv.group(2)

        # Extract group definitions
        for gm in re.finditer(r"""([a-zA-Z0-9_]+)\s*:=\s*([a-zA-Z0-9_.]*)\s*\.\s*Group\(\s*"([^"]+)"\s*\)""", content):
            gvar, parent, gpath = gm.group(1), gm.group(2), gm.group(3)
            pref = group_var_prefixes.get(parent, "")
            group_var_prefixes[gvar] = _join_route_paths(pref, gpath)

        for idx, line in enumerate(lines, start=1):
            for m in route_re.finditer(line):
                obj, raw_mth, path, handler = m.group(1), m.group(2), m.group(3), m.group(4)
                mth = "GET" if raw_mth == "HandleFunc" else raw_mth.upper()
                pref = group_var_prefixes.get(obj.split(".")[-1], "")
                full_path = _join_route_paths(pref, path)
                effect = "read" if mth == "GET" else ("destructive" if mth == "DELETE" else "write")
                api_id = re.sub(r"[^a-z0-9]+", "-", f"{mth.lower()}-{full_path.strip('/') or 'root'}").strip("-")
                routes.append({
                    "id": api_id,
                    "method": mth,
                    "path": full_path,
                    "handler": handler,
                    "purpose": _humanize_identifier(handler.split(".")[-1], mth, full_path),
                    "evidence": [f"{rel}:{idx}"],
                    "parameters": [],
                    "request_body": None,
                    "responses": {"200": {"type": "object", "required": [], "properties": {"code": {"type": "integer"}}}},
                    "auth": {"type": "unknown"},
                    "effect": effect,
                    "confirmation_required": effect != "read",
                })

            for m in table_re.finditer(line):
                gconst, path, raw_mth, handler = m.group(1), m.group(2), m.group(3), m.group(4)
                mth = raw_mth.upper()
                pref = const_prefixes.get(gconst, "") if gconst else ""
                full_path = _join_route_paths(pref, path)
                effect = "read" if mth == "GET" else ("destructive" if mth == "DELETE" else "write")
                api_id = re.sub(r"[^a-z0-9]+", "-", f"{mth.lower()}-{full_path.strip('/') or 'root'}").strip("-")
                routes.append({
                    "id": api_id,
                    "method": mth,
                    "path": full_path,
                    "handler": handler,
                    "purpose": _humanize_identifier(handler.split(".")[-1], mth, full_path),
                    "evidence": [f"{rel}:{idx}"],
                    "parameters": [],
                    "request_body": None,
                    "responses": {"200": {"type": "object", "required": [], "properties": {"code": {"type": "integer"}}}},
                    "auth": {"type": "unknown"},
                    "effect": effect,
                    "confirmation_required": effect != "read",
                })

        # Scan swaggo comments
        curr_summary = ""
        for idx, line in enumerate(lines, start=1):
            line_s = line.strip()
            if line_s.startswith("//"):
                sm = swaggo_summary_re.search(line_s)
                if sm:
                    curr_summary = sm.group(1).strip()
                rm = swaggo_router_re.search(line_s)
                if rm:
                    path, mth = rm.group(1), rm.group(2).upper()
                    effect = "read" if mth == "GET" else ("destructive" if mth == "DELETE" else "write")
                    api_id = re.sub(r"[^a-z0-9]+", "-", f"{mth.lower()}-{path.strip('/') or 'root'}").strip("-")
                    purpose = curr_summary if curr_summary else _humanize_identifier("", mth, path)
                    routes.append({
                        "id": api_id,
                        "method": mth,
                        "path": path,
                        "handler": f"swaggo_{Path(rel).stem}",
                        "purpose": purpose,
                        "evidence": [f"{rel}:{idx}"],
                        "parameters": [],
                        "request_body": None,
                        "responses": {"200": {"type": "object", "required": [], "properties": {"code": {"type": "integer"}}}},
                        "auth": {"type": "unknown"},
                        "effect": effect,
                        "confirmation_required": effect != "read",
                    })
            else:
                if not line_s.startswith("//") and line_s:
                    curr_summary = ""

    return routes


def _scan_java_routes(source: Path, files: list[Path]) -> list[dict]:
    """Scan Java / Kotlin Spring Boot, JAX-RS, and JDK HttpServer routes."""
    routes: list[dict] = []
    # Spring Boot @RequestMapping on class
    spring_class_re = re.compile(r"""@(?:Rest)?Controller[\s\S]*?@RequestMapping\(\s*["']([^"']+)["']\s*\)[\s\S]*?class\s+([A-Za-z0-9_]+)""")
    # Spring Boot methods @GetMapping, @PostMapping, etc.
    spring_method_re = re.compile(r"""@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)(?:\(\s*(?:(?:value|path)\s*=\s*)?["']?([^"'\)]*)["']?\s*\))?[\s\S]*?(?:public|protected|private)?\s+[A-Za-z0-9_<>, \t\n\r]+\s+([A-Za-z0-9_]+)\s*\(""")
    # JDK HttpServer createContext("/api/stock", ...)
    jdk_context_re = re.compile(r"""\.createContext\(\s*["']([^"']+)["']""")

    for p in files:
        if p.suffix not in {".java", ".kt"} or p.stat().st_size > 500_000:
            continue
        rel = p.relative_to(source).as_posix()
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        class_prefix = ""
        m_cls = spring_class_re.search(content)
        if m_cls:
            class_prefix = m_cls.group(1).strip()

        for m in spring_method_re.finditer(content):
            ann, sub, handler = m.group(1), m.group(2) or "", m.group(3)
            mth = ann.replace("Mapping", "").upper()
            full_path = _join_route_paths(class_prefix, sub)
            api_id = re.sub(r"[^a-z0-9]+", "-", f"{mth.lower()}-{full_path.strip('/') or 'root'}").strip("-")
            routes.append({
                "id": api_id,
                "method": mth,
                "path": full_path,
                "handler": handler,
                "purpose": _humanize_identifier(handler, mth, full_path),
                "evidence": [f"{rel}:1"],
                "parameters": [],
                "request_body": None,
                "responses": {"200": {"type": "object", "required": [], "properties": {"ok": {"type": "boolean"}}}},
                "auth": {"type": "unknown"},
                "effect": "read" if mth == "GET" else "write",
                "confirmation_required": mth != "GET",
            })

        for m in jdk_context_re.finditer(content):
            path = m.group(1)
            api_id = re.sub(r"[^a-z0-9]+", "-", f"get-{path.strip('/') or 'root'}").strip("-")
            routes.append({
                "id": api_id,
                "method": "GET",
                "path": path,
                "handler": "createContext",
                "purpose": _humanize_identifier("", "GET", path),
                "evidence": [f"{rel}:1"],
                "parameters": [],
                "request_body": None,
                "responses": {"200": {"type": "object", "required": [], "properties": {"ok": {"type": "boolean"}}}},
                "auth": {"type": "unknown"},
                "effect": "read",
                "confirmation_required": False,
            })

    return routes


def _scan_dotnet_routes(source: Path, files: list[Path]) -> list[dict]:
    """Scan C# / .NET Minimal APIs and Controllers."""
    routes: list[dict] = []
    # Minimal API: app.MapGet("/api/products/{sku}", ...)
    min_api_re = re.compile(r"""\.\s*Map(Get|Post|Put|Delete|Patch)\(\s*["']([^"']+)["']""")
    # Controller: [HttpGet("...")], [HttpPost("...")]
    controller_route_re = re.compile(r"""\[Route\(\s*["']([^"']+)["']\s*\)\][\s\S]*?class\s+([A-Za-z0-9_]+)""")
    controller_method_re = re.compile(r"""\[Http(Get|Post|Put|Delete|Patch)(?:\(\s*["']?([^"'\)]*)["']?\s*\))?\][\s\S]*?(?:public|protected)?\s+[A-Za-z0-9_<>, \t\n\r]+\s+([A-Za-z0-9_]+)\s*\(""")

    for p in files:
        if p.suffix not in {".cs", ".fs"} or p.stat().st_size > 500_000:
            continue
        rel = p.relative_to(source).as_posix()
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        for m in min_api_re.finditer(content):
            mth = m.group(1).upper()
            path = m.group(2)
            api_id = re.sub(r"[^a-z0-9]+", "-", f"{mth.lower()}-{path.strip('/') or 'root'}").strip("-")
            routes.append({
                "id": api_id,
                "method": mth,
                "path": path,
                "handler": "MinimalApi",
                "purpose": _humanize_identifier("", mth, path),
                "evidence": [f"{rel}:1"],
                "parameters": [],
                "request_body": None,
                "responses": {"200": {"type": "object", "required": [], "properties": {"ok": {"type": "boolean"}}}},
                "auth": {"type": "unknown"},
                "effect": "read" if mth == "GET" else "write",
                "confirmation_required": mth != "GET",
            })

        ctrl_prefix = ""
        m_ctrl = controller_route_re.search(content)
        if m_ctrl:
            raw_pref = m_ctrl.group(1)
            cname = m_ctrl.group(2).replace("Controller", "")
            ctrl_prefix = raw_pref.replace("[controller]", cname).replace("[action]", "")

        for m in controller_method_re.finditer(content):
            mth, sub, handler = m.group(1).upper(), m.group(2) or "", m.group(3)
            full_path = _join_route_paths(ctrl_prefix, sub)
            api_id = re.sub(r"[^a-z0-9]+", "-", f"{mth.lower()}-{full_path.strip('/') or 'root'}").strip("-")
            routes.append({
                "id": api_id,
                "method": mth,
                "path": full_path,
                "handler": handler,
                "purpose": _humanize_identifier(handler, mth, full_path),
                "evidence": [f"{rel}:1"],
                "parameters": [],
                "request_body": None,
                "responses": {"200": {"type": "object", "required": [], "properties": {"ok": {"type": "boolean"}}}},
                "auth": {"type": "unknown"},
                "effect": "read" if mth == "GET" else "write",
                "confirmation_required": mth != "GET",
            })

    return routes


def _scan_frontend_pages(source: Path, files: list[Path]) -> list[dict]:
    pages: list[dict] = []
    seen_ids: set[str] = set()

    # 1. Check index.html or root entry page (deduplicate multiple index.html)
    for p in files:
        rel = p.relative_to(source).as_posix()
        if p.name == "index.html" and "index-page" not in seen_ids:
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
                "auth": {"type": "unknown"},
                "evidence": [f"{rel}:1"],
            })
            seen_ids.add("index-page")
            break

    # 2. Scan SPA Nav groups, view metadata, and view section containers
    # e.g. NAV_GROUPS = [{ items: [{ view: "dashboard", label: "审厂看板" }] }]
    # VIEW_META = { dashboard: ["年度审厂看板", "..."] }
    # <section id="dashboard" class="view">
    for p in files:
        if p.suffix not in {".js", ".ts", ".mjs", ".vue", ".jsx", ".tsx", ".html"} or p.stat().st_size > 500_000:
            continue
        rel = p.relative_to(source).as_posix()
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        # Match navigation items: view: "xxx", label: "yyy"
        for m in re.finditer(r"""view:\s*['"]([a-zA-Z0-9_-]+)['"],\s*label:\s*['"]([^'"]+)['"]""", text):
            vid, label = m.group(1), m.group(2).strip()
            if vid not in seen_ids:
                seen_ids.add(vid)
                pages.append({
                    "id": vid,
                    "path": f"/#/{vid}",
                    "purpose": f"{label}（业务视图）",
                    "actions": [f"查看或操作 {label}"],
                    "auth": {"type": "unknown"},
                    "evidence": [f"{rel}:1"],
                })

        # Match VIEW_META = { dashboard: ["title", "desc"] } (ensure it's in a view/meta context)
        if "view_meta" in text.lower() or "nav_groups" in text.lower() or "viewmeta" in text.lower():
            for m in re.finditer(r"""([a-zA-Z0-9_-]+)\s*:\s*\[\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\]""", text):
                vid, title, desc = m.group(1), m.group(2).strip(), m.group(3).strip()
                # Must be a Chinese or capitalized title, not code tokens like 'admin' / 'camera'
                if vid not in seen_ids and any('\u4e00' <= ch <= '\u9fff' for ch in title) and len(title) <= 30:
                    seen_ids.add(vid)
                    pages.append({
                        "id": vid,
                        "path": f"/#/{vid}",
                        "purpose": f"{title}（{desc[:35]}）" if desc else f"{title}（业务视图）",
                        "actions": [f"查看或操作 {title}"],
                        "auth": {"type": "unknown"},
                        "evidence": [f"{rel}:1"],
                    })

        # Match Vue / React Router configs: path: "/users", name: "...", meta: { title: "..." }
        for m in re.finditer(r"""path:\s*['"](/[^'"]*)['"]""", text):
            r_path = m.group(1)
            if r_path == "/" and "index-page" in seen_ids:
                continue
            window = text[m.start():m.start() + 350]
            m_name = re.search(r"""name:\s*['"]([^'"]+)['"]""", window)
            m_title = re.search(r"""(?:title|label):\s*['"]([^'"]+)['"]""", window)
            r_name = m_name.group(1) if m_name else r_path.strip("/").replace("/", "-") or "root"
            r_title = m_title.group(1) if m_title else r_name
            pid = re.sub(r"[^a-z0-9]+", "-", f"route-{r_name.lower()}").strip("-")
            if pid not in seen_ids:
                seen_ids.add(pid)
                pages.append({
                    "id": pid,
                    "path": r_path,
                    "purpose": f"{r_title}（前端路由视图）",
                    "actions": [f"访问并操作 {r_title} 页面"],
                    "auth": {"type": "unknown"},
                    "evidence": [f"{rel}:1"],
                })

        # Match HTML <section id="xxx" class="view">
        for m in re.finditer(r"""<section\s+id=['"]([a-zA-Z0-9_-]+)['"]\s+class=['"][^'"]*\bview\b[^'"]*['"]""", text):
            vid = m.group(1)
            if vid not in seen_ids:
                seen_ids.add(vid)
                pages.append({
                    "id": vid,
                    "path": f"/#/{vid}",
                    "purpose": f"{vid} 业务视图",
                    "actions": [f"查看或操作 {vid}"],
                    "auth": {"type": "unknown"},
                    "evidence": [f"{rel}:1"],
                })

    # 3. Scan pages/, views/, and *-page.* / *-panel.* components
    for p in files:
        if p.suffix not in {".js", ".ts", ".vue", ".jsx", ".tsx", ".html"} or p.stat().st_size > 500_000:
            continue
        rel = p.relative_to(source).as_posix()
        stem = p.stem.lower()
        is_page_dir = any(part in {"pages", "views", "screens", "panels"} for part in p.relative_to(source).parts)
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
        auth_obj = {"type": "unknown"}
        pages.append({
            "id": page_id,
            "path": path,
            "purpose": purpose,
            "actions": actions,
            "auth": auth_obj,
            "evidence": [f"{rel}:1"],
        })

    return pages


def _normalize_resource_type_group(raw_type: str) -> str:
    t = str(raw_type or "").strip().lower()
    if t == "postgresql":
        return "postgres"
    if t == "mssql":
        return "sqlserver"
    return t


def _is_preferred_default_resource_id(res_id: str) -> bool:
    rid = str(res_id or "").strip().lower()
    return rid.endswith("-main") or rid.endswith("-default") or rid.endswith("-server")


def select_and_filter_platform_resources(resources: list[dict] | None, slug: str = "", *, default_only: bool = False) -> list[dict]:
    """Filter out other apps' db-<slug> resources, ensure each resource type has an `is_default=True` resource,
    and optionally return only the single default resource per type (plus db-<slug> if present) so the AI
    never gets confused by multiple resources of the same type.
    """
    if not isinstance(resources, list):
        return []
    slug_lower = str(slug or "").strip().lower()
    own_db_id = f"db-{slug_lower}" if slug_lower else ""

    filtered: list[dict] = []
    for item in resources:
        if not isinstance(item, dict):
            continue
        rid = str(item.get("id", "")).strip()
        rid_lower = rid.lower()
        if not rid:
            continue
        if rid_lower.startswith("db-") and rid_lower != own_db_id:
            continue
        copy_item = dict(item)
        if rid_lower.startswith("db-"):
            copy_item["is_default"] = False
        filtered.append(copy_item)

    by_type: dict[str, list[dict]] = {}
    for item in filtered:
        rid_lower = str(item.get("id", "")).strip().lower()
        if rid_lower.startswith("db-"):
            continue
        t_group = _normalize_resource_type_group(item.get("type", ""))
        if not t_group:
            continue
        by_type.setdefault(t_group, []).append(item)

    for group in by_type.values():
        defaults = [r for r in group if r.get("is_default") is True]
        if not defaults:
            chosen = next((r for r in group if _is_preferred_default_resource_id(r.get("id", ""))), group[0])
            chosen["is_default"] = True
        elif len(defaults) > 1:
            keep = next((r for r in defaults if _is_preferred_default_resource_id(r.get("id", ""))), defaults[0])
            for r in defaults:
                r["is_default"] = (r is keep)

    filtered.sort(key=lambda r: (0 if r.get("is_default") else 1, str(r.get("type", "")), str(r.get("id", ""))))

    if not default_only:
        return filtered

    result: list[dict] = []
    seen_types: set[str] = set()
    for item in filtered:
        rid_lower = str(item.get("id", "")).strip().lower()
        if rid_lower == own_db_id and own_db_id:
            result.append(item)
            continue
        t_group = _normalize_resource_type_group(item.get("type", ""))
        if item.get("is_default") and t_group not in seen_types:
            seen_types.add(t_group)
            result.append(item)
    return result


_PG_SQLITE3_COMPAT_SHIM = '''# --- OPC Platform PostgreSQL (DATABASE_URL) Compatibility Shim ---
import os as _opc_os, re as _opc_re
if _opc_os.getenv("DATABASE_URL", "").startswith(("postgres://", "postgresql://")):
    import psycopg2 as _opc_pg
    class _OpcPgRow(dict):
        def __init__(self, cols, vals):
            super().__init__(zip(cols, vals))
            self._vals = tuple(vals)
        def __getitem__(self, key):
            return self._vals[key] if isinstance(key, (int, slice)) else super().__getitem__(key)
    def _opc_translate_sql(sql: str) -> str:
        s = sql.strip()
        u = s.upper()
        if u.startswith("BEGIN IMMEDIATE") or u.startswith("BEGIN EXCLUSIVE") or u == "BEGIN":
            return "SELECT 1"
        if _opc_re.match(r"(?is)^INSERT\\s+OR\\s+IGNORE\\s+INTO\\b", s):
            s = _opc_re.sub(r"(?is)^INSERT\\s+OR\\s+IGNORE\\s+INTO\\b", "INSERT INTO", s, count=1).rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        out, in_sq, in_dq, i = [], False, False, 0
        while i < len(s):
            ch = s[i]
            if ch == "'" and not in_dq:
                in_sq = not in_sq
                out.append(ch)
            elif ch == '"' and not in_sq:
                in_dq = not in_dq
                out.append(ch)
            elif ch == "?" and not in_sq and not in_dq:
                out.append("%s")
            elif ch == "%" and not in_sq and not in_dq and (i + 1 >= len(s) or s[i + 1] != "s"):
                out.append("%%")
            else:
                out.append(ch)
            i += 1
        return "".join(out)
    class _OpcPgCursor:
        def __init__(self, rows=None, rowcount=0, lastrowid=None):
            self._rows = list(rows or [])
            self._idx = 0
            self.rowcount = rowcount
            self.lastrowid = lastrowid
        def fetchone(self):
            if self._idx >= len(self._rows):
                return None
            r = self._rows[self._idx]
            self._idx += 1
            return r
        def fetchall(self):
            r = self._rows[self._idx:]
            self._idx = len(self._rows)
            return r
        def __iter__(self):
            return iter(self._rows)
    class _OpcPgConn:
        def __init__(self, dsn: str):
            self._conn = _opc_pg.connect(dsn)
            self.row_factory = None
        def execute(self, sql: str, params=()):
            s = sql.strip()
            u = s.upper()
            if u.startswith("PRAGMA"):
                m = _opc_re.match(r'(?is)^PRAGMA\\s+table_info\\(\\s*["\\\']?([A-Za-z0-9_]+)["\\\']?\\s*\\)', s)
                if m and m.group(1).lower() != "invoice_registry":
                    tbl = m.group(1).lower()
                    with self._conn.cursor() as cur:
                        cur.execute("SELECT ordinal_position - 1 AS cid, column_name AS name, data_type AS type, CASE WHEN is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull, column_default AS dflt_value, 0 AS pk FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position", (tbl,))
                        cols = [d[0] for d in cur.description]
                        return _OpcPgCursor([_OpcPgRow(cols, r) for r in cur.fetchall()])
                return _OpcPgCursor([])
            if "SQLITE_MASTER" in u:
                return _OpcPgCursor([])
            pg_sql = _opc_translate_sql(s)
            with self._conn.cursor() as cur:
                cur.execute(pg_sql, tuple(params) if params else None)
                if cur.description:
                    cols = [d[0] for d in cur.description]
                    rows = [_OpcPgRow(cols, r) for r in cur.fetchall()]
                    return _OpcPgCursor(rows, cur.rowcount)
                last_id = None
                if u.startswith("INSERT"):
                    try:
                        cur.execute("SAVEPOINT _opc_lv; SELECT LASTVAL()")
                        r = cur.fetchone()
                        last_id = r[0] if r else None
                        cur.execute("RELEASE SAVEPOINT _opc_lv")
                    except Exception:
                        try:
                            cur.execute("ROLLBACK TO SAVEPOINT _opc_lv")
                        except Exception:
                            pass
                return _OpcPgCursor([], cur.rowcount, last_id)
        def executemany(self, sql: str, seq_of_params):
            pg_sql = _opc_translate_sql(sql)
            with self._conn.cursor() as cur:
                for p in seq_of_params:
                    cur.execute(pg_sql, tuple(p))
                return _OpcPgCursor([], cur.rowcount)
        def executescript(self, script: str):
            return _OpcPgCursor([])
        def cursor(self):
            return self
        def commit(self):
            self._conn.commit()
        def rollback(self):
            self._conn.rollback()
        def close(self):
            self._conn.close()
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
    sqlite3.connect = lambda *a, **kw: _OpcPgConn(_opc_os.environ["DATABASE_URL"])
# --- End OPC Platform PostgreSQL Compatibility Shim ---
'''


def _auto_adapt_sqlite_to_postgres_changes(source: Path, slug: str, data: dict, proposal: dict) -> None:
    """When the user has migrated local SQLite to platform PostgreSQL (`data['migrations']`),
    guarantee that:
    1. All SQLite database connection modules in Python (e.g. `db.py`) are adapted to connect via `DATABASE_URL` (`psycopg2`).
    2. `requirements.txt` includes `psycopg2-binary`.
    3. `contract.configs` declares `DATABASE_URL` and `contract.resources` includes the migrated database resource.
    """
    migrations = [m for m in (data.get("migrations") or []) if isinstance(m, dict)]
    pg_migs = [m for m in migrations if str(m.get("target_type", "postgres")).lower() in {"postgres", "postgresql"}]
    if not pg_migs:
        return

    latest_mig = pg_migs[-1]
    res_id = str(latest_mig.get("resource_id") or f"db-{slug}")
    db_name = str(latest_mig.get("database_name") or f"app_{slug.replace('-', '_')}")

    contract = proposal.setdefault("contract", {})
    changes: list[dict] = proposal.setdefault("changes", [])
    changes_by_path: dict[str, dict] = {c.get("path", ""): c for c in changes if isinstance(c, dict) and c.get("path")}
    used_ids: set[str] = {str(c.get("id", "")) for c in changes if isinstance(c, dict)}

    def _unique_change_id(base_id: str) -> str:
        cid = base_id
        idx = 2
        while cid in used_ids:
            cid = f"{base_id}-{idx}"
            idx += 1
        used_ids.add(cid)
        return cid

    # 1. Find Python files that call sqlite3.connect(...)
    sqlite_py_files: list[Path] = []
    for p in sorted(source.rglob("*.py")):
        if not p.is_file() or p.is_symlink():
            continue
        rel_parts = p.relative_to(source).parts
        if any(part in delivery.EXCLUDED or part in {"tests", "test"} for part in rel_parts):
            continue
        try:
            txt = p.read_text(encoding="utf-8")
        except Exception:
            continue
        if "import sqlite3" in txt and "sqlite3.connect" in txt:
            sqlite_py_files.append(p)

    # Prioritize files named db.py / database.py / storage.py
    sqlite_py_files.sort(key=lambda p: (0 if p.stem.lower() in {"db", "database", "storage", "sqlite"} else 1, len(p.parts)))

    for py_file in sqlite_py_files:
        rel_path = py_file.relative_to(source).as_posix()
        existing_change = changes_by_path.get(rel_path)
        current_content = existing_change["content"] if (existing_change and isinstance(existing_change.get("content"), str)) else py_file.read_text(encoding="utf-8")
        if "DATABASE_URL" in current_content or "psycopg" in current_content:
            continue
        # Inject _PG_SQLITE3_COMPAT_SHIM right after `import sqlite3`
        new_content, n_subs = re.subn(
            r"(?m)^(\s*import\s+sqlite3\b[^\n]*\n)",
            lambda m: m.group(1) + _PG_SQLITE3_COMPAT_SHIM,
            current_content,
            count=1,
        )
        if n_subs == 0:
            continue
        if existing_change:
            existing_change["content"] = new_content
            if "PostgreSQL" not in existing_change.get("reason", ""):
                existing_change["reason"] = existing_change.get("reason", "").rstrip("。") + "；并接入平台 PostgreSQL 数据库 (DATABASE_URL)"
        else:
            change_obj = {
                "id": _unique_change_id("adapt-postgres-db"),
                "path": rel_path,
                "group": "迁移并接入平台 PostgreSQL 数据库",
                "reason": f"将本地 SQLite 数据库连接切换至平台默认 PostgreSQL（专属库 {db_name}，读取 DATABASE_URL 环境变量，保留原业务查询逻辑不变）",
                "content": new_content,
                "executable": False,
            }
            changes.append(change_obj)
            changes_by_path[rel_path] = change_obj

    # 2. Ensure requirements.txt includes psycopg2-binary
    req_files: list[Path] = []
    for p in sorted(source.rglob("requirements*.txt")):
        if p.is_file() and not p.is_symlink() and not any(part in delivery.EXCLUDED for part in p.relative_to(source).parts):
            req_files.append(p)
    req_files.sort(key=lambda p: (0 if p.name == "requirements.txt" else 1, len(p.parts)))
    if req_files:
        req_file = req_files[0]
        req_rel = req_file.relative_to(source).as_posix()
        existing_req = changes_by_path.get(req_rel)
        req_content = existing_req["content"] if (existing_req and isinstance(existing_req.get("content"), str)) else req_file.read_text(encoding="utf-8")
        if not re.search(r"(?i)\bpsycopg2(?:-binary)?\b", req_content):
            updated_req = req_content.rstrip() + "\npsycopg2-binary>=2.9\n"
            if existing_req:
                existing_req["content"] = updated_req
            else:
                req_change = {
                    "id": _unique_change_id("add-postgres-driver"),
                    "path": req_rel,
                    "group": "迁移并接入平台 PostgreSQL 数据库",
                    "reason": "添加 PostgreSQL 数据库驱动依赖 psycopg2-binary，使应用在容器内可直连平台 PostgreSQL",
                    "content": updated_req,
                    "executable": False,
                }
                changes.append(req_change)
                changes_by_path[req_rel] = req_change

    # 3. Ensure DATABASE_URL is declared in contract.configs
    configs: list[dict] = contract.setdefault("configs", [])
    if not any(isinstance(c, dict) and c.get("key") == "DATABASE_URL" for c in configs):
        configs.append({
            "key": "DATABASE_URL",
            "default": "",
            "secret": True,
            "required": True,
            "description": f"平台 PostgreSQL 数据库连接地址（自动绑定专属迁移库 {db_name}）",
        })

    # 4. Ensure migrated resource is listed in contract.resources
    c_resources: list[dict] = contract.setdefault("resources", [])
    if not any(isinstance(r, dict) and r.get("id") == res_id for r in c_resources):
        c_resources.append({
            "id": res_id,
            "type": "postgres",
            "purpose": f"平台自动迁移创建的专属 PostgreSQL 数据库 ({db_name})",
        })

    # 5. Ensure build_context covers any newly added/modified files
    build_ctx: list[str] = contract.setdefault("build_context", [])
    if isinstance(build_ctx, list) and build_ctx and "." not in build_ctx:
        for ch_path in changes_by_path:
            covered = any(ch_path == entry or ch_path.startswith(entry.rstrip("/") + "/") for entry in build_ctx if isinstance(entry, str))
            if not covered:
                top_dir = ch_path.split("/")[0]
                build_ctx.append(top_dir if "/" in ch_path else ch_path)


def _auto_adapt_persistent_file_storage(source: Path, slug: str, data: dict, proposal: dict) -> list[dict]:
    """Automatically detect local JSON/file data directories and upload/report/backup folders,
    populate contract['storage_mounts'], include existing seed folders in contract['build_context'],
    and inject non-destructive .opc-seed backup & startup seed hydration into Dockerfile.
    """
    contract = proposal.setdefault("contract", {})
    changes: list[dict] = proposal.setdefault("changes", [])
    changes_by_path: dict[str, dict] = {
        str(c.get("path", "")): c for c in changes if isinstance(c, dict) and c.get("path")
    }

    # 1. Extract WORKDIR from proposed or existing Dockerfile
    df_text = ""
    if "Dockerfile" in changes_by_path and isinstance(changes_by_path["Dockerfile"].get("content"), str):
        df_text = changes_by_path["Dockerfile"]["content"]
    elif (source / "Dockerfile").is_file():
        try:
            df_text = (source / "Dockerfile").read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass
    workdir = "/app"
    m_wd = re.search(r"(?im)^\s*WORKDIR\s+([^\s#]+)", df_text)
    if m_wd and m_wd.group(1).startswith("/"):
        workdir = m_wd.group(1).rstrip("/") or "/app"

    # 2. Deterministically inspect local file storage & merge with any AI-declared mounts
    scanned_storage = inspect_local_file_storage(source, slug=slug, workdir=workdir, has_migrations=False)
    mounts_by_cpath: dict[str, dict] = {}
    for item in scanned_storage.get("storage_mounts") or []:
        if isinstance(item, dict) and item.get("container_path"):
            mounts_by_cpath[item["container_path"]] = dict(item)

    for raw in contract.get("storage_mounts") or []:
        if not isinstance(raw, dict):
            continue
        rel = str(raw.get("relative_path") or raw.get("name") or "").strip().strip("/")
        cpath = str(raw.get("container_path") or (f"{workdir}/{rel}" if rel else "")).strip()
        if not cpath.startswith("/") or cpath in {"/", "/app", workdir}:
            continue
        if cpath not in mounts_by_cpath:
            base_name = cpath.rstrip("/").split("/")[-1].lower()
            clean_slug = re.sub(r"[^a-z0-9-]+", "-", (slug or "app").lower()).strip("-") or "app"
            mounts_by_cpath[cpath] = {
                "name": str(raw.get("name") or base_name),
                "relative_path": rel or base_name,
                "container_path": cpath,
                "purpose": str(raw.get("purpose") or "应用持久化数据与文件目录"),
                "kind": str(raw.get("kind") or "data_dir"),
                "volume_name": str(raw.get("volume_name") or f"opc-vol-{clean_slug}-{base_name}"),
                "storage_backend": str(raw.get("storage_backend") or "docker_volume"),
                "seed_files_count": int(raw.get("seed_files_count") or 0),
                "seed_files": list(raw.get("seed_files") or []),
            }

    mounts = sorted(mounts_by_cpath.values(), key=lambda m: m["container_path"])
    if not mounts:
        return []

    contract["storage_mounts"] = mounts

    # 3. Ensure build_context includes existing seed directories so initial JSON/files enter the image
    build_ctx: list[str] = contract.setdefault("build_context", [])
    if isinstance(build_ctx, list) and build_ctx and "." not in build_ctx:
        for m in mounts:
            rel_dir = str(m.get("relative_path") or "").strip().strip("/")
            if not rel_dir or not (source / rel_dir).exists():
                continue
            covered = any(rel_dir == entry.rstrip("/") or rel_dir.startswith(entry.rstrip("/") + "/") for entry in build_ctx if isinstance(entry, str))
            if not covered:
                top_dir = rel_dir.split("/")[0]
                if (source / top_dir).exists() and top_dir not in build_ctx:
                    build_ctx.append(top_dir)

    # 4. Inject .opc-seed build-time snapshot & /opc-storage-init.sh non-destructive startup seed hydration into Dockerfile
    if df_text and ".opc-seed" not in df_text and "opc-storage-init.sh" not in df_text:
        rel_dirs_sh = " ".join(
            f'"{m["relative_path"]}"' for m in mounts if re.fullmatch(r"[A-Za-z0-9_./-]+", str(m.get("relative_path", "")))
        )
        cpaths_sh = " ".join(
            f'"{m["container_path"]}"' for m in mounts if re.fullmatch(r"[A-Za-z0-9_./-]+", str(m.get("container_path", "")))
        )
        if rel_dirs_sh and cpaths_sh:
            has_entrypoint = bool(re.search(r"(?im)^\s*ENTRYPOINT\s+", df_text))
            seed_snippet_lines = [
                "# [OPC Persistent Storage] Backup initial seed data & hydrate external volume on startup",
                f"RUN mkdir -p {workdir}/.opc-seed {cpaths_sh} \\",
                f"    && for d in {rel_dirs_sh}; do \\",
                f'         if [ -d "{workdir}/$d" ] && [ -n "$(ls -A "{workdir}/$d" 2>/dev/null)" ]; then \\',
                f'           mkdir -p "{workdir}/.opc-seed/$d" && cp -a "{workdir}/$d/." "{workdir}/.opc-seed/$d/"; \\',
                "         fi; \\",
                "       done \\",
                f"    && printf '#!/bin/sh\\nset -e\\nfor d in {rel_dirs_sh}; do\\n  mkdir -p \"{workdir}/$d\"\\n  if [ -d \"{workdir}/.opc-seed/$d\" ]; then\\n    for f in \"{workdir}/.opc-seed/$d\"/* \"{workdir}/.opc-seed/$d\"/.[!.]*; do\\n      [ -e \"$f\" ] || continue\\n      bn=\"$(basename \"$f\")\"\\n      [ -e \"{workdir}/$d/$bn\" ] || cp -a \"$f\" \"{workdir}/$d/$bn\"\\n    done\\n  fi\\ndone\\nexec \"$@\"\\n' > /opc-storage-init.sh \\",
                "    && chmod +x /opc-storage-init.sh",
            ]
            if not has_entrypoint:
                seed_snippet_lines.append('ENTRYPOINT ["/opc-storage-init.sh"]')
            seed_block = "\n".join(seed_snippet_lines)

            lines = df_text.splitlines()
            insert_idx = len(lines)
            for idx in range(len(lines) - 1, -1, -1):
                stripped = lines[idx].strip().upper()
                if stripped.startswith("CMD ") or stripped.startswith("CMD[") or stripped.startswith("EXPOSE "):
                    insert_idx = idx
            updated_df = "\n".join(lines[:insert_idx] + [seed_block] + lines[insert_idx:]) + "\n"
            mount_summary = "、".join(m["container_path"] for m in mounts)
            if "Dockerfile" in changes_by_path:
                changes_by_path["Dockerfile"]["content"] = updated_df
                old_reason = str(changes_by_path["Dockerfile"].get("reason") or "")
                if "持久化" not in old_reason and "外部卷" not in old_reason:
                    changes_by_path["Dockerfile"]["reason"] = (
                        f"{old_reason.rstrip('。')}；自动配置 {mount_summary} 外部持久化卷种子保护，应用更新或重启不丢失本地数据。"
                    )
            elif (source / "Dockerfile").is_file():
                df_change = {
                    "id": "dockerfile-persistent-storage",
                    "path": "Dockerfile",
                    "group": "持久化数据与文件保护",
                    "reason": f"配置 {mount_summary} 外部持久化存储卷与初始种子数据保护，确保版本升级不丢失本地文件",
                    "content": updated_df,
                    "executable": False,
                }
                changes.append(df_change)
                changes_by_path["Dockerfile"] = df_change

    return mounts


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
            "auth": scanned_page.get("auth", {"type": "unknown"}),
        })
    contract["pages"] = existing_pages

    # 2b. When user has migrated SQLite to platform PostgreSQL, auto-complete any missing DB code/driver/config adaptations
    # Local file databases remain on volumes; historical migration receipts do not trigger code rewrites.

    # 2b-2. Automatically detect & adapt persistent local file storage (JSON state, uploads, reports, backups)
    storage_mounts = _auto_adapt_persistent_file_storage(source, str(data.get("slug", "")), data, proposal)

    # 2c. Filter out internal tool/truncation/row-diff/db-adaptation/default-password/local-json noise from unresolved & decisions
    internal_noise_re = re.compile(
        r"(截断|truncated|补齐.*扫描|完整接口详情|接口扫描显示|扫描结果不一致|自动路由扫描未识别|逐表差异|逐表核对|10,?264|14,?758|healthz|健康检查接口|尚无.*PostgreSQL.*连接适配|数据库切换不能视为已完成|专属\s*PostgreSQL.*连接适配|默认管理员密码|公开的默认.*密码|演示口令|仅存于容器本地文件|容器重建后.*丢失)",
        re.I,
    )
    has_migrated = bool(data.get("migrations"))
    feedback_text = str(data.get("feedback") or "")
    user_confirmed_db = has_migrated or bool(re.search(r"(迁移|PostgreSQL|平台.*数据库|直接迁移|保留.*SQLite)", feedback_text, re.I))
    user_confirmed_local_or_pwd = bool(re.search(r"(保留演示口令|保留文件存储|保留原|先保留)", feedback_text, re.I))

    def _keep_question(q: str) -> bool:
        s = str(q or "").strip()
        if not s:
            return False
        if internal_noise_re.search(s):
            return False
        if user_confirmed_db and re.search(r"(SQLite.*迁移|补迁|保留\s*SQLite|PostgreSQL\s*连接适配|数据库切换)", s, re.I):
            return False
        if user_confirmed_local_or_pwd and re.search(r"(密码|口令|本地文件|文件存储|JSON)", s, re.I):
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
    resources = select_and_filter_platform_resources(data.get("resources") or [], str(data.get("slug", "")))
    pg_resources = [
        r for r in resources
        if str(r.get("type", "")).lower() in {"postgres", "postgresql"}
        and not str(r.get("id", "")).lower().startswith("db-")
    ]
    pg_resources.sort(key=lambda r: (0 if r.get("is_default") else 1, str(r.get("id", ""))))
    storage_type = step1_ai.get("storage_type") or (
        "sqlite" if sqlite_dbs else ("persistent_volume" if storage_mounts else "none")
    )
    mount_paths_desc = "、".join(m["container_path"] for m in storage_mounts)
    default_storage_plan = (
        f"保留 SQLite 原引擎、表结构和数据，通过持久化卷部署（{mount_paths_desc or '需核对数据库文件所在目录的挂载'}），不迁移到平台数据库"
        if sqlite_dbs else (
            f"本地文件通过持久化卷部署（{mount_paths_desc}），首次补齐种子，后续不覆盖已有数据"
            if storage_mounts else "未发现本地持久化数据文件"
        )
    )

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
            "api_auth": step1_ai.get("api_auth") or ("按接口认证方案校验；尚需部署后验证" if any((a.get("auth") or {}).get("type") != "none" for a in existing_apis) else "按契约声明公开；尚需验证"),
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
            "can_migrate_to_platform_pg": False,
            "platform_pg_resource": pg_resources[0]["name"] if pg_resources else "",
            "platform_pg_id": pg_resources[0]["id"] if pg_resources else "",
            "storage_mounts": storage_mounts,
            "storage_migration_plan": default_storage_plan if sqlite_dbs else (step2_ai.get("storage_migration") or default_storage_plan),
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
        "storage_summary": discovery_report["storage_and_database"]["storage_migration_plan"],
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

    # 4. Format proposal["summary"] so Step 1 is structured (not a single jammed paragraph)
    #    and Round 2+ never repeats Step 1 discovery content after user confirmation.
    raw_summary = str(proposal.get("summary") or "").strip()
    revision = int(data.get("revision", 1) or 1)
    if data.get("analysis_mode") != "repair" and revision > 1 and feedback_text.strip() and (len(raw_summary) > 80 or "第一步" in raw_summary):
        latest_fb = feedback_text.split("本次补充（有冲突时以本次为准）：")[-1].strip()
        latest_fb_one_line = "；".join(ln.strip() for ln in latest_fb.splitlines() if ln.strip())[:120]
        proposal["summary"] = (
            f"【确认已生效】已按你的要求（{latest_fb_one_line}）更新改造方案，无需重复摸底分析。\n"
            f"【改造结果】共新增 {len(added_files)} 个文件、修改 {len(modified_files)} 个文件（{discovery_report['foundation_and_container']['dockerfile_action']}）。\n"
            f"【数据与技能】{transformation_report['storage_summary']}；已生成应用技能 app-{data.get('slug', '')}（收录 {len(existing_pages)} 个页面、{len(existing_apis)} 个后端接口）。"
        )
    elif len(raw_summary) > 80 and "\n" not in raw_summary:
        purpose_short = (contract.get("description") or raw_summary.split("。")[0]).replace("第一步发现：", "").strip()
        fw_txt = f"（{' + '.join(scan['runtime']['frameworks'])}）" if scan["runtime"]["frameworks"] else ""
        proposal["summary"] = (
            f"【应用定位】{purpose_short}\n"
            f"【运行环境】{lang} {ver}{fw_txt} · 容器监听端口 {data.get('port', 8080)}\n"
            f"【接口与页面】共识别 {len(existing_apis)} 个后端 API 接口、{len(existing_pages)} 个前端页面/业务视图\n"
            f"【登录与鉴权】{discovery_report['auth_and_credentials']['page_auth']}；{discovery_report['auth_and_credentials']['api_auth']}\n"
            f"【数据与存储】{discovery_report['storage_and_database']['storage_migration_plan']}\n"
            f"【改造与技能】{discovery_report['foundation_and_container']['dockerfile_action']}（拟新增 {len(added_files)} 项、修改 {len(modified_files)} 项文件），全量生成 app-{data.get('slug', '')} 技能"
        )

    return proposal


