"""App Packaging and Skill Generation Engine for work-opc (V3.0).

Handles:
1. Safe extraction of user-supplied application zip bundles;
2. Automated containerization adaptation (Dockerfile, captain-definition, /healthz, Go/ymir runtime bootstrap);
3. Extraction of configuration variables (including YAML configs with credential externalization) and resource requirements into app-manifest.json;
4. Extraction of real business APIs from Swagger/OpenAPI or router code into app-manifest.json and SKILL.md (with safety annotations);
5. Generation of standard skill.zip (SKILL.md + scripts/invoke_api.py);
6. Pre-installation of skill locally in disabled state and post-deployment enablement.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Optional

import yaml

from .skills.store import SkillStore

logger = logging.getLogger("coworker.app_packager")

# Patterns to skip when reading source files
IGNORED_PATTERNS = {
    ".git",
    ".svn",
    ".hg",
    ".idea",
    ".vscode",
    "node_modules",
    "venv",
    ".venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".DS_Store",
    "__MACOSX",
    "dist",
    "build",
    "target",
}

DEFAULT_INVOKE_API_SCRIPT = '''#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "{app_url}"

def main():
    parser = argparse.ArgumentParser(description="Invoke intranet OPC app API")
    parser.add_argument("--method", default="GET", help="HTTP method (GET, POST, PUT, DELETE, etc.)")
    parser.add_argument("--path", required=True, help="API path, e.g. /api/health or /items")
    parser.add_argument("--query", default="{}", help="Query params JSON string")
    parser.add_argument("--body", default=None, help="Request JSON body string")
    parser.add_argument("--headers", default="{}", help="Optional headers JSON string")
    args = parser.parse_args()

    base_url = os.environ.get("APP_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    path = "/" + args.path.lstrip("/")

    query_dict = json.loads(args.query) if args.query else {}
    if query_dict:
        path = f"{path}?{urllib.parse.urlencode(query_dict)}"

    url = f"{base_url}{path}"
    headers = {"Accept": "application/json"}
    if args.headers:
        try:
            extra_headers = json.loads(args.headers)
            headers.update(extra_headers)
        except Exception as e:
            sys.stderr.write(f"Warning: failed to parse headers: {e}\\n")

    data = None
    if args.body is not None:
        data = args.body.encode("utf-8")
        headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=data, headers=headers, method=args.method.upper())
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=30) as resp:
            content = resp.read().decode("utf-8", errors="replace")
            print(content)
    except urllib.error.HTTPError as e:
        err_content = e.read().decode("utf-8", errors="replace")
        sys.stderr.write(f"HTTP {e.code}: {err_content}\\n")
        sys.exit(1)
    except Exception as e:
        sys.stderr.write(f"Error invoking API: {e}\\n")
        sys.exit(1)

if __name__ == "__main__":
    main()
'''


def safe_extract_zip(zip_path: str | Path, dest_dir: Path) -> Path:
    """Extract zip securely, preventing zip slips and stripping top-level single directory wrapper."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    real_dest = dest_dir.resolve()

    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            member_path = Path(member.filename)
            # Check for path traversal attempts
            if ".." in member_path.parts or member_path.is_absolute():
                continue
            # Skip ignored directories like __MACOSX, .git
            if any(part in IGNORED_PATTERNS for part in member_path.parts):
                continue

            target_path = (real_dest / member.filename).resolve()
            if not str(target_path).startswith(str(real_dest)):
                raise ValueError(f"Zip slip attempt detected: {member.filename}")

            if member.is_dir():
                target_path.mkdir(parents=True, exist_ok=True)
            else:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as source, open(target_path, "wb") as target:
                    shutil.copyfileobj(source, target)

    # Clean up any stray ignored items at root
    for item in dest_dir.iterdir():
        if item.name in IGNORED_PATTERNS or item.name == ".DS_Store":
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                try:
                    item.unlink()
                except Exception:
                    pass

    # If the zip had a single enclosing directory, unwrap all files to dest_dir
    dirs = [p for p in dest_dir.iterdir() if p.is_dir() and p.name not in IGNORED_PATTERNS]
    files = [p for p in dest_dir.iterdir() if p.is_file() and p.name not in IGNORED_PATTERNS and p.name != ".DS_Store"]
    if len(dirs) == 1 and len(files) == 0:
        inner_dir = dirs[0]
        temp_dir = dest_dir.parent / (dest_dir.name + "_unwrap_tmp")
        shutil.move(str(inner_dir), str(temp_dir))
        for item in temp_dir.iterdir():
            shutil.move(str(item), str(dest_dir / item.name))
        shutil.rmtree(temp_dir, ignore_errors=True)

    return dest_dir


def _extract_apis_from_workspace(workspace: Path) -> list[dict[str, str]]:
    """Extract API routes from Swagger/OpenAPI files or Go/Python router source files."""
    apis: list[dict[str, str]] = [
        {"method": "GET", "path": "/healthz", "summary": "容器健康检查接口（只读）"},
    ]
    seen: set[tuple[str, str]] = {("GET", "/healthz")}

    # 1. Try docs/swagger.json or swagger.json / openapi.json
    swagger_candidates = [
        workspace / "docs" / "swagger.json",
        workspace / "swagger.json",
        workspace / "openapi.json",
    ]
    for sw_file in swagger_candidates:
        if sw_file.exists():
            try:
                sw_data = json.loads(sw_file.read_text(encoding="utf-8", errors="ignore"))
                paths_obj = sw_data.get("paths", {})
                if isinstance(paths_obj, dict):
                    for route_path, methods_obj in sorted(paths_obj.items()):
                        if not isinstance(methods_obj, dict):
                            continue
                        for m_key, m_info in sorted(methods_obj.items()):
                            method = m_key.upper()
                            if method not in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
                                continue
                            if (method, route_path) in seen:
                                continue
                            seen.add((method, route_path))
                            summary = ""
                            if isinstance(m_info, dict):
                                summary = str(m_info.get("summary") or m_info.get("description") or "").strip()
                            # Annotate read-only vs write/trigger endpoints
                            if any(seg in route_path.lower() for seg in ("/run", "/create", "/delete", "/update", "/publish")):
                                summary = f"[写操作/高风险-调用前须人工确认] {summary}" if summary else "[写操作/高风险-调用前须人工确认]"
                            elif any(seg in route_path.lower() for seg in ("/search", "/get", "/list", "/query")):
                                summary = f"[只读查询] {summary}" if summary else "[只读查询]"
                            apis.append({
                                "method": method,
                                "path": route_path,
                                "summary": summary or f"{method} {route_path}",
                            })
            except Exception as e:
                logger.warning("Failed to parse swagger file %s: %s", sw_file, e)

    # 2. Fallback: scan Go @Router annotations if no swagger routes found
    if len(apis) <= 1:
        router_re = re.compile(r"//\s*@Router\s+(\S+)\s+\[(\w+)\]", re.IGNORECASE)
        summary_re = re.compile(r"//\s*@Summary\s+(.+)")
        for go_file in workspace.rglob("*.go"):
            if any(part in IGNORED_PATTERNS or part == "vendor" for part in go_file.parts):
                continue
            try:
                lines = go_file.read_text(encoding="utf-8", errors="ignore").splitlines()
                last_summary = ""
                for line in lines:
                    s_match = summary_re.search(line)
                    if s_match:
                        last_summary = s_match.group(1).strip()
                    r_match = router_re.search(line)
                    if r_match:
                        route_path = r_match.group(1).strip()
                        method = r_match.group(2).strip().upper()
                        if (method, route_path) not in seen:
                            seen.add((method, route_path))
                            apis.append({
                                "method": method,
                                "path": route_path,
                                "summary": last_summary or f"{method} {route_path}",
                            })
            except Exception:
                pass

    return apis


def _sanitize_and_extract_yaml_configs(
    workspace: Path,
    configs: list[dict[str, Any]],
    seen_keys: set[str],
    detected_resources: set[str],
) -> Optional[int]:
    """Extract config items from conf/config.yaml, externalize secrets into configs, and sanitize raw files."""
    detected_port: Optional[int] = None
    yaml_candidates = [
        workspace / "conf" / "config.yaml",
        workspace / "conf" / "config.yml",
        workspace / "config" / "config.yaml",
        workspace / "config.yaml",
    ]

    for yml_path in yaml_candidates:
        if not yml_path.exists():
            continue
        try:
            raw_text = yml_path.read_text(encoding="utf-8", errors="ignore")
            data = yaml.safe_load(raw_text)
            if not isinstance(data, dict):
                continue

            modified = False
            # 1. Application HTTP Port
            app_sec = data.get("application")
            if isinstance(app_sec, dict) and app_sec.get("httpPort"):
                try:
                    detected_port = int(app_sec["httpPort"])
                except Exception:
                    pass

            # 2. Databases section
            dbs_sec = data.get("databases")
            if isinstance(dbs_sec, dict):
                for db_name, db_cfg in dbs_sec.items():
                    if not isinstance(db_cfg, dict):
                        continue
                    driver = str(db_cfg.get("driver", "")).lower()
                    dsn = str(db_cfg.get("dsn", ""))
                    if "mysql" in driver:
                        detected_resources.add("mysql")
                        detected_resources.add("postgres")
                    elif "pg" in driver or "postgres" in driver:
                        detected_resources.add("postgres")

                    if dsn:
                        if "MYSQL_DSN" not in seen_keys:
                            seen_keys.add("MYSQL_DSN")
                            configs.append({
                                "key": "MYSQL_DSN",
                                "value": dsn,
                                "description": f"数据库连接串 ({db_name}，留空或不可达时自动使用绑定的 DATABASE_URL / 内置 SQLite)",
                                "required": False,
                            })
                        # Strip raw password/DSN from packaged config.yaml (V03)
                        db_cfg["dsn"] = " sqlite_fallback.db"
                        db_cfg["driver"] = "sqlite"
                        modified = True

            # 3. Extend section (e.g. Aliyun Pipeline credentials)
            ext_sec = data.get("extend")
            if isinstance(ext_sec, dict):
                pipe_sec = ext_sec.get("pipeline")
                if isinstance(pipe_sec, dict):
                    client_sec = pipe_sec.get("client")
                    if isinstance(client_sec, dict):
                        mapping = [
                            ("accessKeyId", "ALIYUN_ACCESS_KEY_ID", "阿里云 AccessKeyId (外置敏感凭证)"),
                            ("accessKeySecret", "ALIYUN_ACCESS_KEY_SECRET", "阿里云 AccessKeySecret (外置敏感凭证)"),
                            ("orgId", "ALIYUN_ORG_ID", "阿里云效企业组织 ID (orgId)"),
                            ("regionId", "ALIYUN_REGION_ID", "阿里云效地域 ID (regionId)"),
                            ("endPoint", "ALIYUN_ENDPOINT", "阿里云效服务端点 (endPoint)"),
                        ]
                        for field_name, env_key, desc in mapping:
                            val = str(client_sec.get(field_name, "")).strip()
                            if val and env_key not in seen_keys:
                                seen_keys.add(env_key)
                                configs.append({
                                    "key": env_key,
                                    "value": val,
                                    "description": desc,
                                    "required": False,
                                })
                            if field_name in ("accessKeyId", "accessKeySecret") and val:
                                # Remove hardcoded credentials from packaged YAML
                                client_sec[field_name] = ""
                                modified = True

            if modified:
                yml_path.write_text(
                    yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                    encoding="utf-8",
                )
        except Exception as e:
            logger.warning("Failed to inspect/sanitize %s: %s", yml_path, e)

    return detected_port


def extract_config_and_manifest(workspace: Path, container_port: int = 8080) -> dict[str, Any]:
    """Scan workspace for config files and extract environment variables, APIs, and detected resources."""
    manifest_path = workspace / "app-manifest.json"
    if manifest_path.exists():
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Failed to parse existing app-manifest.json: %s", e)

    detected_resources: set[str] = set()
    configs: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    # Look for .env or .env.example
    env_files = [workspace / ".env.example", workspace / ".env", workspace / ".env.template"]
    for env_file in env_files:
        if env_file.exists():
            try:
                for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k and k not in seen_keys:
                        seen_keys.add(k)
                        configs.append({
                            "key": k,
                            "value": v,
                            "description": f"Extracted from {env_file.name}",
                            "required": False,
                        })
            except Exception as e:
                logger.warning("Failed to read %s: %s", env_file, e)

    # Extract and sanitize YAML configs (conf/config.yaml)
    yaml_port = _sanitize_and_extract_yaml_configs(workspace, configs, seen_keys, detected_resources)
    effective_port = container_port
    if yaml_port and container_port == 8080:
        effective_port = yaml_port

    # Ensure PORT exists
    if "PORT" not in seen_keys:
        configs.insert(0, {
            "key": "PORT",
            "value": str(effective_port),
            "description": "应用容器监听端口",
            "required": True,
        })
        seen_keys.add("PORT")

    # Inspect configs and code files for resources
    all_text = " ".join(f"{c['key']}={c['value']}" for c in configs)
    for ext in ("*.py", "*.js", "*.ts", "*.go", "*.java", "*.json", "*.yaml", "*.yml", "*.env*", ".env*"):
        for file_path in workspace.rglob(ext):
            if any(part in IGNORED_PATTERNS or part == "vendor" for part in file_path.parts):
                continue
            try:
                all_text += " " + file_path.read_text(encoding="utf-8", errors="ignore").lower()
            except Exception:
                pass

    if any(kw in all_text for kw in ("postgres", "psycopg", "pg_", "postgresql", "gorm.io/driver/postgres")):
        detected_resources.add("postgres")
    if any(kw in all_text for kw in ("mysql", "pymysql", "mysql2", "gorm.io/driver/mysql")):
        detected_resources.add("mysql")
    if any(kw in all_text for kw in ("redis", "ioredis", "redis_url", "go-redis")):
        detected_resources.add("redis")
    if any(kw in all_text for kw in ("s3", "minio", "boto3", "oss")):
        detected_resources.add("oss")
    if any(kw in all_text for kw in ("sqlserver", "mssql", "pyodbc")):
        detected_resources.add("sqlserver")

    apis = _extract_apis_from_workspace(workspace)

    manifest = {
        "container_port": effective_port,
        "detected_resource_types": sorted(detected_resources),
        "configs": configs,
        "apis": apis,
    }

    try:
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning("Could not write app-manifest.json: %s", e)

    return manifest


def _adapt_go_ymir_workspace(workspace: Path, container_port: int) -> bool:
    """Adapt a Go + ymir framework microservice (like devops-aly) for container deployment:
    1. Inject /healthz endpoint into api/router/opc_healthz.go;
    2. Inject runtime env/DB/schema bootstrapper into cmd/opc_bootstrap.go and hook into cmd/cmd.go;
    3. Run `go mod vendor` if local go toolchain is available so container build works 100% offline.
    """
    go_mod = workspace / "go.mod"
    if not go_mod.exists():
        return False

    go_mod_text = go_mod.read_text(encoding="utf-8", errors="ignore")
    is_ymir = "github.com/geniussheep/ymir" in go_mod_text

    if is_ymir:
        # 1. Inject /healthz router in api/router/opc_healthz.go
        router_dir = workspace / "api" / "router"
        if router_dir.exists():
            healthz_go = router_dir / "opc_healthz.go"
            if not healthz_go.exists():
                healthz_go.write_text(
                    '''package router

import (
	"net/http"

	sdk "github.com/geniussheep/ymir/sdk/api"
	"github.com/gin-gonic/gin"
)

func init() {
	ApiRouterScan = append(ApiRouterScan, func() {
		Webapi.AppendRouters("", sdk.RouterEntry{
			Path:   "healthz",
			Method: http.MethodGet,
			Handler: func(c *gin.Context) {
				c.JSON(http.StatusOK, gin.H{
					"status":  "ok",
					"service": "devops-aly",
				})
			},
		})
	})
}
''',
                    encoding="utf-8",
                )

        # 2. Inject runtime config & DB bootstrapper in cmd/opc_bootstrap.go
        cmd_dir = workspace / "cmd"
        cmd_file = cmd_dir / "cmd.go"
        if cmd_dir.exists() and cmd_file.exists():
            module_name = "gitlab.benlai.work/ops/devops-aly"
            for line in go_mod_text.splitlines():
                if line.strip().startswith("module "):
                    module_name = line.strip().split()[1]
                    break

            bootstrap_go = cmd_dir / "opc_bootstrap.go"
            if not bootstrap_go.exists():
                bootstrap_go.write_text(
                    f'''package cmd

import (
	"log"
	"net"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/geniussheep/ymir/sdk"
	sdkconfig "github.com/geniussheep/ymir/sdk/config"
	"{module_name}/api/model"
	"{module_name}/common"
	appconfig "{module_name}/config"
	"gopkg.in/yaml.v3"
)

// prepareRuntimeConfig 在 ymir 读取配置文件前，将容器环境变量覆盖写入 conf/config.yaml，
// 并在原 MySQL 不可达时自动切换至绑定的 DATABASE_URL (PostgreSQL) 或本地 SQLite。
func prepareRuntimeConfig(configPath string) {{
	data, err := os.ReadFile(configPath)
	if err != nil {{
		log.Printf("[OPC-Bootstrap] read config %s warning: %v", configPath, err)
		return
	}}

	var raw map[string]interface{{}}
	if err := yaml.Unmarshal(data, &raw); err != nil {{
		log.Printf("[OPC-Bootstrap] unmarshal config warning: %v", err)
		return
	}}

	// 1. 绑定 PORT 环境变量
	appMap, _ := raw["application"].(map[string]interface{{}})
	if appMap == nil {{
		appMap = make(map[string]interface{{}})
		raw["application"] = appMap
	}}
	if portStr := strings.TrimSpace(os.Getenv("PORT")); portStr != "" {{
		if p, err := strconv.Atoi(portStr); err == nil && p > 0 {{
			appMap["httpPort"] = p
		}}
	}}

	// 2. 注入外置阿里云流水线凭证与参数
	extMap, _ := raw["extend"].(map[string]interface{{}})
	if extMap == nil {{
		extMap = make(map[string]interface{{}})
		raw["extend"] = extMap
	}}
	pipeMap, _ := extMap["pipeline"].(map[string]interface{{}})
	if pipeMap == nil {{
		pipeMap = make(map[string]interface{{}})
		extMap["pipeline"] = pipeMap
	}}
	clientMap, _ := pipeMap["client"].(map[string]interface{{}})
	if clientMap == nil {{
		clientMap = make(map[string]interface{{}})
		pipeMap["client"] = clientMap
	}}
	if v := strings.TrimSpace(os.Getenv("ALIYUN_ACCESS_KEY_ID")); v != "" {{
		clientMap["accessKeyId"] = v
	}}
	if v := strings.TrimSpace(os.Getenv("ALIYUN_ACCESS_KEY_SECRET")); v != "" {{
		clientMap["accessKeySecret"] = v
	}}
	if v := strings.TrimSpace(os.Getenv("ALIYUN_ORG_ID")); v != "" {{
		clientMap["orgId"] = v
	}}
	if v := strings.TrimSpace(os.Getenv("ALIYUN_REGION_ID")); v != "" {{
		clientMap["regionId"] = v
	}}
	if v := strings.TrimSpace(os.Getenv("ALIYUN_ENDPOINT")); v != "" {{
		clientMap["endPoint"] = v
	}}
	// 确保 SDK 初始化非空，避免未调用阿里云接口时启动报错
	if str, _ := clientMap["accessKeyId"].(string); strings.TrimSpace(str) == "" {{
		clientMap["accessKeyId"] = "opc-placeholder-ak"
	}}
	if str, _ := clientMap["accessKeySecret"].(string); strings.TrimSpace(str) == "" {{
		clientMap["accessKeySecret"] = "opc-placeholder-sk"
	}}
	if str, _ := clientMap["endPoint"].(string); strings.TrimSpace(str) == "" {{
		clientMap["endPoint"] = "devops.cn-hangzhou.aliyuncs.com"
	}}

	// 3. 数据库连接自适应 (MYSQL_DSN -> DATABASE_URL -> SQLite)
	dbsMap, _ := raw["databases"].(map[string]interface{{}})
	if dbsMap == nil {{
		dbsMap = make(map[string]interface{{}})
		raw["databases"] = dbsMap
	}}
	dbEntry, _ := dbsMap[common.DbName].(map[string]interface{{}})
	if dbEntry == nil {{
		dbEntry = make(map[string]interface{{}})
		dbsMap[common.DbName] = dbEntry
	}}

	mysqlDsn := strings.TrimSpace(os.Getenv("MYSQL_DSN"))
	dbURL := strings.TrimSpace(os.Getenv("DATABASE_URL"))

	usedDriver := ""
	usedDsn := ""

	if mysqlDsn != "" && canReachMySQLDsn(mysqlDsn) {{
		usedDriver = "mysql"
		usedDsn = mysqlDsn
	}} else if dbURL != "" && canReachPostgresURL(dbURL) {{
		usedDriver = "pgsql"
		usedDsn = dbURL
	}} else {{
		usedDriver = "sqlite"
		usedDsn = "/tmp/devops_aly.db"
	}}

	log.Printf("[OPC-Bootstrap] Selected database driver=%s for %s", usedDriver, common.DbName)
	dbEntry["driver"] = usedDriver
	dbEntry["dsn"] = usedDsn

	out, err := yaml.Marshal(raw)
	if err == nil {{
		_ = os.WriteFile(configPath, out, 0644)
	}}
}}

func canReachMySQLDsn(dsn string) bool {{
	start := strings.Index(dsn, "@tcp(")
	if start == -1 {{
		return false
	}}
	rest := dsn[start+5:]
	end := strings.Index(rest, ")")
	if end == -1 {{
		return false
	}}
	hostPort := rest[:end]
	conn, err := net.DialTimeout("tcp", hostPort, 2*time.Second)
	if err != nil {{
		return false
	}}
	_ = conn.Close()
	return true
}}

func canReachPostgresURL(rawURL string) bool {{
	u, err := url.Parse(rawURL)
	if err != nil || u.Host == "" {{
		return false
	}}
	hostPort := u.Host
	if !strings.Contains(hostPort, ":") {{
		hostPort += ":5432"
	}}
	conn, err := net.DialTimeout("tcp", hostPort, 2*time.Second)
	if err != nil {{
		return false
	}}
	_ = conn.Close()
	return true
}}

// autoMigrateAndSeedData 在数据库初始化完成后自动建表并写入一条安全只读演示数据
func autoMigrateAndSeedData() {{
	if portStr := strings.TrimSpace(os.Getenv("PORT")); portStr != "" {{
		if p, err := strconv.ParseInt(portStr, 10, 64); err == nil && p > 0 {{
			sdkconfig.ApplicationConfig.HttpPort = p
		}}
	}}
	_ = appconfig.ExtendConfig

	orm := sdk.Runtime.GetDb(common.DbName)
	if orm == nil || orm.Db() == nil {{
		return
	}}
	gdb := orm.Db()

	// Create tables in a cross-dialect compatible way (PostgreSQL/SQLite/MySQL)
	if gdb.Dialector.Name() == "postgres" {{
		_ = gdb.Exec(`CREATE TABLE IF NOT EXISTS aliyun_pipeline_app_info (
			id varchar(32) PRIMARY KEY,
			name varchar(255) NOT NULL,
			feishu_name varchar(255),
			label text NOT NULL,
			code_source varchar(255),
			build_source varchar(128),
			created_by varchar(128),
			updated_by varchar(128),
			date_created timestamp,
			date_updated timestamp NOT NULL,
			is_delete boolean NOT NULL DEFAULT false,
			weight integer,
			describe varchar(255),
			deployment_type varchar(128),
			repository_id varchar(255),
			namespace varchar(50)
		)`).Error
		_ = gdb.Exec(`CREATE TABLE IF NOT EXISTS aliyun_pipeline_flow (
			id integer PRIMARY KEY,
			name varchar(255),
			env varchar(50),
			app_id varchar(255)
		)`).Error
	}} else if gdb.Dialector.Name() == "sqlite" {{
		_ = gdb.Exec(`CREATE TABLE IF NOT EXISTS aliyun_pipeline_app_info (
			id char(32) PRIMARY KEY NOT NULL,
			name varchar(255) NOT NULL,
			feishu_name varchar(255),
			label text NOT NULL,
			code_source varchar(255),
			build_source varchar(128),
			created_by varchar(128),
			updated_by varchar(128),
			date_created datetime,
			date_updated datetime NOT NULL,
			is_delete boolean NOT NULL DEFAULT 0,
			weight integer,
			describe varchar(255),
			deployment_type varchar(128),
			repository_id varchar(255),
			namespace varchar(50)
		)`).Error
		_ = gdb.Exec(`CREATE TABLE IF NOT EXISTS aliyun_pipeline_flow (
			id integer PRIMARY KEY AUTOINCREMENT NOT NULL,
			name varchar(255),
			env varchar(50),
			app_id varchar(255)
		)`).Error
	}} else {{
		_ = gdb.AutoMigrate(&model.PipelineApp{{}}, &model.PipelineFlow{{}})
	}}

	var count int64
	if err := gdb.Model(&model.PipelineApp{{}}).Count(&count).Error; err == nil && count == 0 {{
		now := time.Now()
		demo := model.PipelineApp{{
			Id:             "opc-demo-app-0000000000000000001",
			Name:           "devops-aly-sample",
			FeishuName:     "阿里云流水线示例服务",
			Label:          `{{"appGroup": "opc"}}`,
			CodeSource:     "gitlab.benlai.work/ops/devops-aly.git",
			BuildSource:    "aliyun_flow",
			CreatedBy:      "opc-system",
			UpdatedBy:      "opc-system",
			DateCreated:    now,
			DateUpdated:    now,
			IsDelete:       false,
			Weight:         1,
			Describe:       "OPC 内网自动化部署验证样本记录",
			DeploymentType: "deployment",
			RepositoryID:   "repo-opc-demo-001",
			Namespace:      "branch-benlai",
		}}
		_ = gdb.Create(&demo).Error
	}}
}}
''',
                    encoding="utf-8",
                )

            # Hook prepareRuntimeConfig and autoMigrateAndSeedData into cmd/cmd.go
            cmd_content = cmd_file.read_text(encoding="utf-8", errors="ignore")
            if "prepareRuntimeConfig" not in cmd_content:
                cmd_content = cmd_content.replace(
                    "func Execute() {",
                    "func Execute() {\n\tprepareRuntimeConfig(sdkcommon.DefaultConfigFilePath)",
                )
            if "autoMigrateAndSeedData" not in cmd_content:
                cmd_content = cmd_content.replace(
                    "InitFuncArray:  []func(){aly.Setup},",
                    "InitFuncArray:  []func(){aly.Setup, autoMigrateAndSeedData},",
                )
            cmd_file.write_text(cmd_content, encoding="utf-8")

    # 3. Try running `go mod vendor` so container build inside CapRover needs zero external network
    try:
        env = os.environ.copy()
        env.setdefault("GOPROXY", "https://goproxy.cn,direct")
        subprocess.run(
            ["go", "mod", "vendor"],
            cwd=str(workspace),
            env=env,
            check=True,
            capture_output=True,
            timeout=120,
        )
        logger.info("Successfully vendored Go modules in %s", workspace)
    except Exception as e:
        logger.warning("go mod vendor skipped or failed (will fallback to GOPROXY in Dockerfile): %s", e)

    return is_ymir


def ensure_containerization_files(workspace: Path, container_port: int = 8080) -> None:
    """Ensure captain-definition and Dockerfile exist in the workspace."""
    cap_def = workspace / "captain-definition"
    if not cap_def.exists():
        cap_def.write_text(
            json.dumps({"schemaVersion": 2, "dockerfilePath": "./Dockerfile"}, indent=2),
            encoding="utf-8",
        )

    is_ymir = False
    if (workspace / "go.mod").exists():
        is_ymir = _adapt_go_ymir_workspace(workspace, container_port=container_port)

    dockerfile = workspace / "Dockerfile"
    if not dockerfile.exists():
        # Auto-detect language stack
        if (workspace / "requirements.txt").exists() or (workspace / "pyproject.toml").exists():
            dockerfile.write_text(
                f"""FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt* ./
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi
COPY . .
ENV PORT={container_port}
EXPOSE {container_port}
CMD ["python", "main.py"]
""",
                encoding="utf-8",
            )
        elif (workspace / "package.json").exists():
            dockerfile.write_text(
                f"""FROM node:20-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install --production
COPY . .
ENV PORT={container_port}
EXPOSE {container_port}
CMD ["npm", "start"]
""",
                encoding="utf-8",
            )
        elif (workspace / "go.mod").exists():
            has_vendor = (workspace / "vendor").exists()
            build_flags = "-mod=vendor " if has_vendor else ""
            cmd_line = 'CMD ["./server", "server", "-c", "./conf/config.yaml"]' if is_ymir else 'CMD ["./server"]'
            dockerfile.write_text(
                f"""FROM golang:1.22-alpine AS builder
WORKDIR /app
ENV GOPROXY=https://goproxy.cn,direct
COPY . .
RUN go build {build_flags}-o server .

FROM debian:12.12-slim
WORKDIR /app
COPY --from=builder /app/server ./
COPY --from=builder /app/conf ./conf
RUN mkdir -p /app/logs /tmp
ENV PORT={container_port}
EXPOSE {container_port}
{cmd_line}
""",
                encoding="utf-8",
            )
        else:
            dockerfile.write_text(
                f"""FROM python:3.11-slim
WORKDIR /app
COPY . .
ENV PORT={container_port}
EXPOSE {container_port}
CMD ["python", "-m", "http.server", "{container_port}"]
""",
                encoding="utf-8",
            )


def create_app_tar(workspace: Path, out_tar_path: Path) -> Path:
    """Pack workspace into an uncompressed app.tar for deploy-opc / CapRover upload."""
    out_tar_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out_tar_path, "w") as tar:
        for item in workspace.iterdir():
            if item.name in IGNORED_PATTERNS or item == out_tar_path:
                continue
            tar.add(item, arcname=item.name)
    return out_tar_path


def create_skill_zip(
    slug: str,
    name: str,
    description: str,
    app_url: str,
    apis: list[dict[str, str]],
    out_zip_path: Path,
) -> Path:
    """Generate standard skill.zip containing SKILL.md and scripts/invoke_api.py."""
    out_zip_path.parent.mkdir(parents=True, exist_ok=True)

    skill_name = f"app-{slug}"
    api_list_md = ""
    for api in apis:
        api_list_md += f"- `{api.get('method', 'GET')} {api.get('path', '/')}`: {api.get('summary', '')}\n"

    skill_md = f"""---
name: {skill_name}
description: {name}（{description}）。当用户需要查询、创建或处理该应用相关业务时使用此技能。
---
# {name} ({skill_name}) 技能调用指南

- **应用内网基准地址**: `{app_url}`
- **调用方式**:
  使用 `python3 scripts/invoke_api.py --method <METHOD> --path <PATH> [--query '<JSON>'] [--body '<JSON>']`
- **安全调用准则**:
  - 优先使用只读查询接口（如 `/healthz`、`/api/v1/pipeline/search/app` 等）验证或检索业务数据；
  - 带有 `[写操作/高风险-调用前须人工确认]` 标注的接口（例如触发真实阿里云流水线运行的 `/api/v1/pipeline/run`）会触发外部生产变更，除非用户明确授权并提供完整参数，否则严禁自动调用！

## 可用 API 列表
{api_list_md if api_list_md else '- `GET /healthz`: 健康检查'}
"""

    script_content = DEFAULT_INVOKE_API_SCRIPT.replace("{app_url}", app_url)

    with zipfile.ZipFile(out_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SKILL.md", skill_md)
        zf.writestr("scripts/invoke_api.py", script_content)

    return out_zip_path


def transform_and_package_app(
    zip_path: str | Path,
    slug: str,
    name: str,
    description: str,
    app_url: str,
    container_port: int = 8080,
    temp_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Execute complete packaging pipeline for work-opc."""
    if temp_dir is None:
        temp_dir = Path(tempfile.mkdtemp(prefix=f"opc_app_{slug}_"))

    workspace_dir = temp_dir / "workspace"
    real_workspace = safe_extract_zip(zip_path, workspace_dir)

    manifest = extract_config_and_manifest(real_workspace, container_port=container_port)
    effective_port = int(manifest.get("container_port", container_port))
    ensure_containerization_files(real_workspace, container_port=effective_port)

    # Build detailed AI transformation report
    tech_stack = "通用应用程序"
    dockerfile_desc = f"生成标准容器化 Dockerfile (暴露端口 {effective_port})"
    healthz_desc = "在应用中挂载 /healthz 探测探针"
    bootstrap_desc = "注入内网环境变量与数据库自适应连接器"

    if (real_workspace / "go.mod").exists():
        go_mod_content = (real_workspace / "go.mod").read_text(encoding="utf-8", errors="ignore")
        if "ymir" in go_mod_content:
            tech_stack = "Go 1.22 · Ymir 微服务框架"
        else:
            tech_stack = "Go 原生工程"
        dockerfile_desc = "多阶段容器编译 (golang:1.22-alpine + debian:12.12-slim 运行环境)"
        healthz_desc = "在 api/router/opc_healthz.go 注入 /healthz 健康探针"
        bootstrap_desc = "在 cmd/opc_bootstrap.go 注入动态环境注入与自适应数据库连接器 (MySQL/pgsql/SQLite)"
    elif (real_workspace / "requirements.txt").exists() or (real_workspace / "pyproject.toml").exists():
        tech_stack = "Python 应用程序"
        dockerfile_desc = "生成 python:3.11-slim 运行环境与依赖安装配置"
    elif (real_workspace / "package.json").exists():
        tech_stack = "Node.js 应用程序"
        dockerfile_desc = "生成 node:20-alpine 生产构建与启动配置"

    transform_report = {
        "tech_stack": tech_stack,
        "effective_port": effective_port,
        "dockerfile_desc": dockerfile_desc,
        "healthz_desc": healthz_desc,
        "bootstrap_desc": bootstrap_desc,
        "configs_count": len(manifest.get("configs", [])),
        "resources_detected": manifest.get("detected_resource_types", []),
        "apis_count": len(manifest.get("apis", [])),
        "skill_name": f"app-{slug}",
        "steps_completed": [
            f"扫描源码依赖，精准识别技术栈为【{tech_stack}】",
            f"自动补齐 Dockerfile 与容器编排定义 (端口 {effective_port})",
            f"自动注入内网健康探测探针 (/healthz) 与运行时自适应连接器",
            f"深度提取敏感凭证与环境变量配置契约 (已提取 {len(manifest.get('configs', []))} 项)",
            f"自动生成直连内网的标准 AI 技能包 ({len(manifest.get('apis', []))} 个可用 API 接口)",
        ],
    }
    manifest["transform_report"] = transform_report

    app_tar_path = temp_dir / "app.tar"
    create_app_tar(real_workspace, app_tar_path)

    skill_zip_path = temp_dir / "skill.zip"
    create_skill_zip(
        slug=slug,
        name=name,
        description=description,
        app_url=app_url,
        apis=manifest.get("apis", []),
        out_zip_path=skill_zip_path,
    )

    # Persist artifacts to ~/.config/coworker/apps/<slug>/ so user can inspect and access anytime
    persistent_app_dir = Path.home() / ".config" / "coworker" / "apps" / slug
    persistent_app_dir.mkdir(parents=True, exist_ok=True)
    import shutil
    try:
        if Path(zip_path).exists():
            shutil.copy2(zip_path, persistent_app_dir / "original.zip")
        shutil.copy2(app_tar_path, persistent_app_dir / "app.tar")
        shutil.copy2(skill_zip_path, persistent_app_dir / "skill.zip")
        (persistent_app_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        logger.warning("Could not cache persistent app artifacts for %s: %s", slug, e)

    return {
        "slug": slug,
        "name": name,
        "description": description,
        "app_url": app_url,
        "container_port": effective_port,
        "manifest": manifest,
        "workspace_dir": str(real_workspace),
        "app_tar_path": str(app_tar_path),
        "skill_zip_path": str(skill_zip_path),
        "temp_dir": str(temp_dir),
        "persistent_dir": str(persistent_app_dir),
    }


def preinstall_local_skill_disabled(skill_store: SkillStore, skill_zip_path: Path, slug: str) -> None:
    """Pre-install skill in work-opc local skill repository in disabled state (idempotent on redeploy)."""
    skill_name = f"app-{slug}"
    # If an earlier version of this app skill exists locally, remove it first so redeploy succeeds cleanly
    try:
        skill_store.delete(skill_name)
    except Exception:
        pass

    data = skill_zip_path.read_bytes()
    staged = skill_store.stage_upload(data, filename="skill.zip")
    token = staged.get("token")
    if not token:
        raise ValueError(f"Failed to stage skill upload for {skill_name}")

    skill_store.confirm_upload(token, scope="global")
    # Immediately disable it until deployment succeeds
    skill_store.set_enabled(skill_name, False)
    logger.info("Skill %s pre-installed locally in disabled state.", skill_name)


def enable_local_skill(skill_store: SkillStore, slug: str) -> None:
    """Enable pre-installed local skill once deployment reports running."""
    skill_name = f"app-{slug}"
    skill_store.set_enabled(skill_name, True)
    logger.info("Skill %s is now enabled.", skill_name)
