"""App Packaging and Skill Generation Engine for work-opc (V3.0).

Handles:
1. Safe extraction of user-supplied application zip bundles;
2. Automated containerization adaptation (Dockerfile, captain-definition, /healthz);
3. Extraction of configuration variables and resource requirements into app-manifest.json;
4. Generation of standard skill.zip (SKILL.md + scripts/invoke_api.py);
5. Pre-installation of skill locally in disabled state and post-deployment enablement.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Optional

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
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
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
            # Skip ignored directories
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

    # If the zip had a single enclosing directory, return that inner directory
    children = [p for p in dest_dir.iterdir() if p.name not in IGNORED_PATTERNS]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return dest_dir


def extract_config_and_manifest(workspace: Path, container_port: int = 8080) -> dict[str, Any]:
    """Scan workspace for config files and extract environment variables and detected resources."""
    manifest_path = workspace / "app-manifest.json"
    if manifest_path.exists():
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Failed to parse existing app-manifest.json: %s", e)

    detected_resources = set()
    configs = []
    seen_keys = set()

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

    # Ensure PORT exists
    if "PORT" not in seen_keys:
        configs.insert(0, {
            "key": "PORT",
            "value": str(container_port),
            "description": "应用容器监听端口",
            "required": True,
        })
        seen_keys.add("PORT")

    # Inspect configs and code files for resources
    all_text = " ".join(f"{c['key']}={c['value']}" for c in configs)
    for ext in ("*.py", "*.js", "*.ts", "*.go", "*.java", "*.json", "*.yaml", "*.yml", "*.env*", ".env*"):
        for file_path in workspace.glob(ext):
            if any(part in IGNORED_PATTERNS for part in file_path.parts):
                continue
            try:
                all_text += " " + file_path.read_text(encoding="utf-8", errors="ignore").lower()
            except Exception:
                pass

    if any(kw in all_text for kw in ("postgres", "psycopg", "pg_", "postgresql")):
        detected_resources.add("postgres")
    if any(kw in all_text for kw in ("mysql", "pymysql", "mysql2")):
        detected_resources.add("mysql")
    if any(kw in all_text for kw in ("redis", "ioredis", "redis_url")):
        detected_resources.add("redis")
    if any(kw in all_text for kw in ("s3", "minio", "boto3", "oss")):
        detected_resources.add("oss")
    if any(kw in all_text for kw in ("sqlserver", "mssql", "pyodbc")):
        detected_resources.add("sqlserver")

    manifest = {
        "container_port": container_port,
        "detected_resource_types": sorted(detected_resources),
        "configs": configs,
        "apis": [
            {"method": "GET", "path": "/healthz", "summary": "健康检查接口"},
        ],
    }

    try:
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning("Could not write app-manifest.json: %s", e)

    return manifest


def ensure_containerization_files(workspace: Path, container_port: int = 8080) -> None:
    """Ensure captain-definition and Dockerfile exist in the workspace."""
    cap_def = workspace / "captain-definition"
    if not cap_def.exists():
        cap_def.write_text(
            json.dumps({"schemaVersion": 2, "dockerfilePath": "./Dockerfile"}, indent=2),
            encoding="utf-8",
        )

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
                f"""FROM node:18-alpine
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
            dockerfile.write_text(
                f"""FROM golang:1.22-alpine AS builder
WORKDIR /app
COPY . .
RUN go build -o server .
FROM alpine:3.19
WORKDIR /app
COPY --from=builder /app/server ./
ENV PORT={container_port}
EXPOSE {container_port}
CMD ["./server"]
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

    ensure_containerization_files(real_workspace, container_port=container_port)
    manifest = extract_config_and_manifest(real_workspace, container_port=container_port)

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

    return {
        "slug": slug,
        "name": name,
        "description": description,
        "app_url": app_url,
        "container_port": container_port,
        "manifest": manifest,
        "workspace_dir": str(real_workspace),
        "app_tar_path": str(app_tar_path),
        "skill_zip_path": str(skill_zip_path),
        "temp_dir": str(temp_dir),
    }


def preinstall_local_skill_disabled(skill_store: SkillStore, skill_zip_path: Path, slug: str) -> None:
    """Pre-install skill in work-opc local skill repository in disabled state."""
    skill_name = f"app-{slug}"
    # Read zip bytes and stage/confirm in skill_store
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
