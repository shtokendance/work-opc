"""Desktop application-delivery routes; approval and package identity are server checked."""

import base64
import asyncio
import json
import os
from pathlib import Path
import zipfile

import httpx
from fastapi import HTTPException
from fastapi.responses import FileResponse

from ..app_delivery import AppDelivery
from ..app_delivery_skill.scripts import delivery
from ..config import load_config
from ..secrets import state_dir


DEFAULT_PLATFORM_URL = "https://shtokendance.benlai.com"


def default_platform_url() -> str:
    env_url = (
        os.environ.get("OPC_PLATFORM_HTTP")
        or os.environ.get("OPC_PLATFORM_BASE")
        or ""
    ).strip()
    if env_url:
        return env_url.rstrip("/")
    try:
        cfg_url = (load_config().opc_platform_base or "").strip()
        if cfg_url:
            return cfg_url.rstrip("/")
    except Exception:
        pass
    return DEFAULT_PLATFORM_URL


async def read_only_probe(contract, operation, params, body=None, credential_env=None):
    """Retry transient routing/startup failures, never business mismatches or auth errors."""
    for attempt in range(3):
        try:
            result = await asyncio.to_thread(delivery.execute, contract, operation, params, body, credential_env=credential_env)
        except OSError as exc:
            if attempt == 2:
                raise ValueError(f"{operation} 验证请求失败（{type(exc).__name__}）") from exc
        else:
            if result[0] not in {502, 503, 504} or attempt == 2:
                return result
        await asyncio.sleep(1 + attempt)


def register_app_delivery_routes(app, manager):
    service = AppDelivery(manager, state_dir() / "app-delivery")
    app.state.app_delivery = service

    def fail(exc):
        return HTTPException(status_code=422, detail=str(exc))

    @app.post("/v1/apps/transform")
    async def transform(body: dict):
        try:
            def prepare_upload():
                source_path = str(body.get("source_path", "")).strip()
                if source_path:
                    path = Path(source_path).expanduser()
                    if not path.is_absolute() or source_path.startswith("\\\\") or path.is_symlink():
                        raise ValueError("请输入本机源码文件夹或 ZIP 的完整路径")
                    if path.is_dir():
                        archive = path
                    elif path.is_file() and path.suffix.lower() == ".zip":
                        if path.stat().st_size > 64 * 1024 * 1024:
                            raise ValueError("源码压缩包不能超过 64 MiB")
                        archive = path.read_bytes()
                    else:
                        raise ValueError("请输入本机源码文件夹或 ZIP 的完整路径")
                else:
                    archive = base64.b64decode(body.get("data_b64", ""), validate=True)
                if not archive:
                    raise ValueError("请选择源码 ZIP")
                platform_url = str(body.get("platform_url") or default_platform_url()).rstrip("/")
                delivery.valid_url(platform_url)
                data = service.create(body, archive)
                data["platform_url"] = platform_url
                service.save(data)
                return data

            # Reading an archive (or waiting on OS file access) must not freeze the entire sidecar.
            data = await asyncio.to_thread(prepare_upload)
            service.launch_analysis(data["id"])
            return {"ok": True, "job_id": data["id"]}
        except (ValueError, KeyError, OSError, zipfile.BadZipFile) as exc:
            raise fail(exc)

    @app.get("/v1/apps/jobs/{job_id}")
    def get_job(job_id: str):
        try:
            return service.public(job_id)
        except (ValueError, OSError) as exc:
            raise fail(exc)

    @app.get("/v1/apps/jobs")
    def list_jobs(slug: str = ""):
        return {"jobs": service.jobs(slug)}

    @app.get("/v1/apps/jobs/{job_id}/handoff")
    def handoff(job_id: str):
        try:
            return service.handoff(job_id)
        except (ValueError, OSError) as exc:
            raise fail(exc)

    @app.post("/v1/apps/jobs/{job_id}/cancel")
    async def cancel(job_id: str):
        try:
            return service.cancel(job_id)
        except (ValueError, OSError) as exc:
            raise fail(exc)

    @app.get("/v1/apps/jobs/{job_id}/artifacts/{filename}")
    def artifact(job_id: str, filename: str):
        if filename not in {"app.tar", "skill.zip", "app-manifest.json", "summary.json"}:
            raise HTTPException(status_code=404, detail="Unknown artifact")
        try:
            data = service.load(job_id)
            path = service.path(job_id) / "bundle" / filename
            if not data.get("artifact_hash") or not path.is_file():
                raise ValueError("交付包尚未生成")
            return FileResponse(path, filename=data["slug"] + "-" + filename)
        except (ValueError, OSError) as exc:
            raise fail(exc)

    @app.post("/v1/apps/jobs/{job_id}/analyze")
    async def analyze(job_id: str, body: dict):
        try:
            service.launch_analysis(job_id, str(body.get("feedback", "")))
            return {"ok": True}
        except (ValueError, OSError) as exc:
            raise fail(exc)

    @app.post("/v1/apps/jobs/{job_id}/approve")
    async def approve(job_id: str, body: dict):
        try:
            return await service.approve(job_id, body)
        except (ValueError, OSError) as exc:
            raise fail(exc)

    @app.post("/v1/apps/jobs/{job_id}/confirm-deployment")
    def confirm(job_id: str, body: dict):
        try:
            return service.confirm_deployment(job_id, body)
        except (ValueError, OSError) as exc:
            raise fail(exc)

    @app.post("/v1/apps/publish-to-platform")
    async def publish(body: dict):
        try:
            data, folder = service.verified_package(body.get("job_id", ""), body)
            platform_url = str(body.get("platform_url") or data.get("platform_url") or default_platform_url()).rstrip("/")
            delivery.valid_url(platform_url)
            data["platform_url"] = platform_url
            service.install_pending_skill(data, folder)
            fields = {"slug": data["slug"], "name": data["name"], "description": data["description"], "container_port": str(data["proposal"]["contract"]["runtime"]["port"]), "app_config": json.dumps(body.get("app_config", [])), "resource_ids": json.dumps(body.get("resource_ids", []))}
            files = {"app_tar": ("app.tar", (folder / "app.tar").read_bytes(), "application/x-tar"), "skill_zip": ("skill.zip", (folder / "skill.zip").read_bytes(), "application/zip")}
            async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
                response = await client.post(platform_url + "/api/v1/apps/publish", data=fields, files=files)
            if response.status_code not in {200, 202}:
                raise ValueError("平台拒绝部署：" + str(response.status_code) + " " + delivery_error(response.text))
            result = response.json()
            if result.get("app_url") != data["app_url"]:
                raise ValueError("平台返回的 URL 与用户确认地址不同，拒绝启用技能")
            data["events"].append("已上传经过确认的同一交付包，等待部署及业务验证")
            data["published"] = True
            service.save(data)
            return {"ok": True, "data": result}
        except (ValueError, OSError, httpx.HTTPError) as exc:
            raise fail(exc)

    @app.post("/v1/apps/enable-skill")
    async def enable(body: dict):
        try:
            data = service.load(body.get("job_id", ""))
            if not data.get("confirmation_token") or not data.get("published"):
                raise ValueError("应用尚未确认部署")
            platform_url = str(body.get("platform_url") or data.get("platform_url") or default_platform_url()).rstrip("/")
            delivery.valid_url(platform_url)
            async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
                response = await client.get(platform_url + "/api/v1/apps/" + data["slug"])
                response.raise_for_status()
                status = response.json()
            if status.get("status") != "running" or status.get("app_url") != data["app_url"]:
                raise ValueError("部署尚未成功，不能启用业务技能")
            summary = delivery.load(service.path(data["id"]) / "bundle/summary.json")
            for filename in ("app.tar", "skill.zip"):
                receipt = "[Artifact] " + filename + ":" + summary["files"][filename]
                if receipt not in status.get("build_log", ""):
                    raise ValueError("线上部署回执与当前应用包或技能版本不一致，拒绝启用")
            contract = data["proposal"]["contract"]
            health = contract["health"]
            health_contract = {"base_url": data["app_url"], "apis": [{"id": "health", "path": health["path"], "method": "GET", "effect": "read", "auth": {"type": "none"}}]}
            code, content_type, content = await read_only_probe(health_contract, "health", {})
            if code != health["status"] or "application/json" not in content_type or not delivery.subset(json.loads(content), health["json_contains"]):
                raise ValueError("应用健康身份校验失败，不能启用业务技能")
            credentials = {c["key"]: c.get("value", "") for c in data["deployment_confirmation"]["app_config"]}
            checks = []
            for case in contract["verification"].get("cases", []):
                operation = next((api for api in contract["apis"] if api["id"] == case["operation"]), {})
                if operation.get("path") == health["path"]:
                    continue  # Repeating the health probe cannot certify business behavior.
                code, content_type, content = await read_only_probe(contract, case["operation"], case.get("params", {}), case.get("body"), credential_env=credentials)
                if code != case.get("status", 200) or "application/json" not in content_type or not delivery.subset(json.loads(content), case["json_contains"]):
                    raise ValueError(f"只读业务验证失败：{case['operation']}（HTTP {code}，预期状态或业务字段不匹配）")
                checks.append({"operation": case["operation"], "status": code, "passed": True})
            if not checks:
                raise ValueError("缺少实际只读业务验证，健康检查不能代替。请在改造报告补充业务查询条件及预期结果后重新确认；页面应用需先完成浏览器验证。")
            service.enable_verified_skill(data)
            data["verification"] = {"status": "verified-deployed-api", "base_url": data["app_url"], "checks": checks, "pages_verified": False}
            service.save(data)
            return {"ok": True, "verification": data["verification"]}
        except (ValueError, OSError, httpx.HTTPError) as exc:
            raise fail(exc)


def delivery_error(text):
    from ..app_delivery import redacted
    return redacted(text)[:1000]
