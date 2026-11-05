"""Desktop application-delivery routes; approval and package identity are server checked."""

import base64
import asyncio
import json
import os
from pathlib import Path
import zipfile

import httpx
from fastapi import HTTPException
from fastapi.responses import FileResponse, Response

from ..app_delivery import AppDelivery
from ..app_delivery_skill.scripts import delivery
from ..config import load_config
from ..secrets import state_dir


DEFAULT_PLATFORM_URL = "https://shtokendance.benlai.com"


def verification_credentials(contract, deployment_config, supplied):
    """Transient probe credentials; never add login sessions to deployment config."""
    allowed = {a.get("auth", {}).get("env") for a in contract.get("apis", []) if a.get("auth", {}).get("type") in {"header", "bearer"}}
    supplied = {} if supplied is None else supplied
    if not isinstance(supplied, dict) or any(k not in allowed or not isinstance(v, str) or len(v) > 16384 or any(c in v for c in "\r\n\x00") for k, v in supplied.items()):
        raise ValueError("业务验证凭据无效，请仅填写本应用声明的登录凭据")
    return {**{c["key"]: c.get("value", "") for c in deployment_config}, **supplied}


def default_platform_url() -> str:
    try:
        cfg_file = state_dir() / "app-delivery" / "platform_config.json"
        if cfg_file.is_file():
            saved = json.loads(cfg_file.read_text(encoding="utf-8"))
            if isinstance(saved, dict) and str(saved.get("platform_url") or "").strip():
                return str(saved["platform_url"]).strip().rstrip("/")
    except Exception:
        pass
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

    @app.post("/v1/apps/jobs/{job_id}/artifacts/{filename}/export")
    def export_artifact(job_id: str, filename: str):
        if filename not in {"app.tar", "skill.zip", "app-manifest.json", "summary.json"}:
            raise HTTPException(status_code=404, detail="Unknown artifact")
        try:
            import shutil
            import subprocess
            import sys

            data = service.load(job_id)
            src_path = service.path(job_id) / "bundle" / filename
            if not data.get("artifact_hash") or not src_path.is_file():
                raise ValueError("交付包尚未生成")
            downloads_dir = Path.home() / "Downloads"
            if not downloads_dir.exists():
                downloads_dir = Path.home()
            target_name = f"{data.get('slug', 'app')}-{filename}"
            dest = downloads_dir / target_name
            shutil.copy2(src_path, dest)
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(dest)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif sys.platform == "win32":
                subprocess.Popen(["explorer", f"/select,{dest}"])
            else:
                subprocess.Popen(["xdg-open", str(dest.parent)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"ok": True, "path": str(dest), "filename": dest.name}
        except (ValueError, OSError) as exc:
            raise fail(exc)

    @app.get("/v1/apps/jobs/{job_id}/skill-preview")
    def skill_preview(job_id: str):
        try:
            return service.skill_preview(job_id)
        except (ValueError, OSError) as exc:
            raise fail(exc)

    @app.post("/v1/apps/jobs/{job_id}/config-draft")
    def config_draft(job_id: str, body: dict):
        try:
            return service.save_config_draft(job_id, body)
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

    @app.post("/v1/apps/jobs/{job_id}/prepare")
    async def prepare_job(job_id: str, body: dict):
        try:
            # Legacy drafts were prepared under different approval rules. Reanalyze them
            # before automatic writes instead of treating old file proposals as authorized.
            data = service.load(job_id)
            if data.get("preparation_policy") != "technical-preparation-v1":
                if data["status"] != "review" or body.get("proposal_hash") != data.get("proposal_hash"):
                    raise ValueError("方案已变化，请重新读取")
                service.launch_analysis(job_id, "请按最新流程重新检查：自动完成技术适配，保留原业务、数据和连接；仅真实部署阻塞和连接替换需要用户决定。")
                return service.public(job_id)
            # Technical preparation does not approve resource replacement or deployment.
            return await service.approve(job_id, body, technical_preparation=True)
        except (ValueError, OSError) as exc:
            raise fail(exc)

    @app.post("/v1/apps/jobs/{job_id}/confirm-deployment")
    def confirm(job_id: str, body: dict):
        try:
            return service.confirm_deployment(job_id, body)
        except (ValueError, OSError) as exc:
            raise fail(exc)

    @app.get("/v1/apps/platform-config")
    def get_platform_config():
        url = default_platform_url()
        return {
            "ok": True,
            "platform_url": url,
            "resource_api_url": f"{url}/api/v1/apps/resources",
            "resources_api_url": f"{url}/api/v1/apps/resources",
            "dockerfile_template_api_url": f"{url}/api/v1/apps/dockerfile-templates",
            "dockerfile_templates_api_url": f"{url}/api/v1/apps/dockerfile-templates",
            "admin_deploy_url": f"{url}/admin/deploy",
            "deploy_admin_url": f"{url}/admin/deploy",
        }

    @app.put("/v1/apps/platform-config")
    def update_platform_config(body: dict):
        try:
            raw_url = str(body.get("platform_url") or "").strip().rstrip("/")
            if not raw_url:
                raw_url = DEFAULT_PLATFORM_URL
            delivery.valid_url(raw_url)
            cfg_dir = state_dir() / "app-delivery"
            cfg_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            cfg_file = cfg_dir / "platform_config.json"
            cfg_file.write_text(json.dumps({"platform_url": raw_url}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return {
                "ok": True,
                "platform_url": raw_url,
                "resource_api_url": f"{raw_url}/api/v1/apps/resources",
                "resources_api_url": f"{raw_url}/api/v1/apps/resources",
                "dockerfile_template_api_url": f"{raw_url}/api/v1/apps/dockerfile-templates",
                "dockerfile_templates_api_url": f"{raw_url}/api/v1/apps/dockerfile-templates",
                "admin_deploy_url": f"{raw_url}/admin/deploy",
                "deploy_admin_url": f"{raw_url}/admin/deploy",
            }
        except (ValueError, OSError) as exc:
            raise fail(exc)

    @app.get("/v1/apps/runtime-logs/{slug}")
    async def get_app_runtime_logs(slug: str, platform_url: str = ""):
        try:
            if not delivery.SLUG.fullmatch(slug):
                raise ValueError("应用标识格式无效")
            target_platform = str(platform_url or default_platform_url()).strip().rstrip("/")
            delivery.valid_url(target_platform)
            async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
                resp = await client.get(f"{target_platform}/api/v1/apps/{slug}/logs")
            if resp.status_code != 200:
                raise ValueError(f"获取应用日志失败 ({resp.status_code}): {delivery_error(resp.text)}")
            return resp.json()
        except (ValueError, OSError, httpx.HTTPError) as exc:
            raise fail(exc)

    @app.post("/v1/apps/recheck/{slug}")
    async def recheck_app_health(slug: str, body: dict = None):
        try:
            if not delivery.SLUG.fullmatch(slug):
                raise ValueError("应用标识格式无效")
            body = body or {}
            target_platform = str(body.get("platform_url") or default_platform_url()).strip().rstrip("/")
            delivery.valid_url(target_platform)

            app_url = str(body.get("app_url") or "").strip().rstrip("/")
            async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
                if not app_url:
                    resp = await client.get(f"{target_platform}/api/v1/apps/{slug}")
                    if resp.status_code != 200:
                        raise ValueError(f"获取应用信息失败 ({resp.status_code}): {delivery_error(resp.text)}")
                    app_data = resp.json()
                    app_url = str(app_data.get("app_url") or "").strip().rstrip("/")

                if not app_url:
                    raise ValueError("应用尚未分配访问地址，无法执行健康检查")

                health_url = app_url + "/healthz"
                try:
                    probe_resp = await client.get(health_url, timeout=10.0)
                except Exception as probe_err:
                    raise ValueError(f"健康检查请求失败: {probe_err}")

                s = probe_resp.text.lower().strip()
                is_invalid = (
                    "powered by caprover" in s
                    or "captain-root-domain" in s
                    or s.startswith("<!doctype html")
                    or s.startswith("<html")
                )
                if probe_resp.status_code != 200 or is_invalid:
                    raise ValueError(f"健康检查未通过 (HTTP {probe_resp.status_code})：服务未就绪或返回异常")

                # 探活通过，向平台更新状态为 running
                try:
                    await client.put(
                        f"{target_platform}/api/v1/apps/{slug}",
                        json={"status": "running"}
                    )
                except Exception:
                    pass

            return {"ok": True, "status": "running", "message": "应用健康检查通过，状态已恢复正常运行"}
        except (ValueError, OSError, httpx.HTTPError) as exc:
            raise fail(exc)

    @app.post("/v1/apps/publish-to-platform")
    async def publish(body: dict):
        try:
            data, folder = service.verified_package(body.get("job_id", ""), body)
            platform_url = str(body.get("platform_url") or data.get("platform_url") or default_platform_url()).rstrip("/")
            delivery.valid_url(platform_url)
            data["platform_url"] = platform_url
            version_note = str(body.get("version_note") or data.get("version_note") or "").strip()
            if version_note:
                data["version_note"] = version_note
            service.install_pending_skill(data, folder)
            runtime = data["proposal"]["contract"].get("runtime", {})
            dockerfile_path = service.path(data["id"]) / "approved" / "source" / "Dockerfile"
            dockerfile_content = dockerfile_path.read_text(encoding="utf-8", errors="replace") if dockerfile_path.is_file() else ""
            fields = {
                "slug": data["slug"],
                "name": data["name"],
                "description": data["description"],
                "container_port": str(runtime.get("port", 8080)),
                "language": str(runtime.get("language", "")),
                "runtime_version": str(runtime.get("version", "")),
                "dockerfile_content": dockerfile_content,
                "version_note": version_note,
                "app_config": json.dumps(body.get("app_config", [])),
                "resource_ids": json.dumps(body.get("resource_ids", [])),
            }
            files = {"app_tar": ("app.tar", (folder / "app.tar").read_bytes(), "application/x-tar"), "skill_zip": ("skill.zip", (folder / "skill.zip").read_bytes(), "application/zip")}
            async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
                response = await client.post(platform_url + "/api/v1/apps/publish", data=fields, files=files)
            if response.status_code not in {200, 202}:
                raise ValueError("平台拒绝部署：" + str(response.status_code) + " " + delivery_error(response.text))
            result = response.json()
            if result.get("app_url") != data["app_url"]:
                raise ValueError("平台返回的 URL 与用户确认地址不同，拒绝启用技能")
            pub_ver = int(result.get("version") or 1)
            data["published_version"] = pub_ver
            service.archive_published_version(data["slug"], pub_ver, folder)
            data["events"].append(f"已上传经过确认的同一交付包 (v{pub_ver})，物理文件已归档至服务端 MinIO，等待部署及业务验证")
            data["published"] = True
            service.save(data)
            return {"ok": True, "data": result}
        except (ValueError, OSError, httpx.HTTPError) as exc:
            raise fail(exc)

    @app.get("/v1/apps/versions/{slug}")
    async def get_app_versions(slug: str, platform_url: str = ""):
        try:
            if not delivery.SLUG.fullmatch(slug):
                raise ValueError("应用标识格式无效")
            target_platform = str(platform_url or default_platform_url()).strip().rstrip("/")
            delivery.valid_url(target_platform)
            async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
                resp = await client.get(f"{target_platform}/api/v1/apps/{slug}/versions")
            if resp.status_code != 200:
                raise ValueError(f"查询应用版本历史失败 ({resp.status_code}): {delivery_error(resp.text)}")
            payload = resp.json()
            versions = payload.get("versions")
            if isinstance(versions, list) and len(versions) > 1:
                has_running = any(v.get("status") == "running" or v.get("is_active") for v in versions if isinstance(v, dict))
                filtered = []
                for idx, v in enumerate(versions):
                    if isinstance(v, dict) and v.get("status") == "failed" and not v.get("is_active"):
                        if has_running or idx > 0:
                            continue
                    filtered.append(v)
                payload["versions"] = filtered
            return payload
        except (ValueError, OSError, httpx.HTTPError) as exc:
            raise fail(exc)

    @app.get("/v1/apps/versions/{slug}/{version}/artifacts/{filename}")
    async def download_app_version_artifact(slug: str, version: int, filename: str, platform_url: str = ""):
        if filename not in {"app.tar", "skill.zip", "Dockerfile", "manifest.json"}:
            raise HTTPException(status_code=404, detail="Unknown artifact")
        try:
            if not delivery.SLUG.fullmatch(slug) or version <= 0:
                raise ValueError("应用标识或版本号无效")
            target_platform = str(platform_url or default_platform_url()).strip().rstrip("/")
            delivery.valid_url(target_platform)
            async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
                resp = await client.get(f"{target_platform}/api/v1/apps/{slug}/versions/{version}/artifacts/{filename}")
            if resp.status_code == 200:
                media_type = resp.headers.get("content-type", "application/octet-stream")
                return Response(
                    content=resp.content,
                    media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{slug}-v{version}-{filename}"'},
                )
            local_fallback = service.root / "versions" / slug / f"v{version}" / filename
            if local_fallback.is_file():
                return FileResponse(local_fallback, filename=f"{slug}-v{version}-{filename}")
            raise ValueError(f"下载版本 v{version} 制品 {filename} 失败 ({resp.status_code}): {delivery_error(resp.text)}")
        except (ValueError, OSError, httpx.HTTPError) as exc:
            raise fail(exc)

    @app.post("/v1/apps/rollback/{slug}")
    async def rollback_app_version(slug: str, body: dict):
        try:
            if not delivery.SLUG.fullmatch(slug):
                raise ValueError("应用标识格式无效")
            target_version = int(body.get("target_version") or 0)
            if target_version <= 0:
                raise ValueError("请指定有效的目标回退版本号")
            reason = str(body.get("reason") or f"回退至版本 v{target_version}").strip()
            target_platform = str(body.get("platform_url") or default_platform_url()).strip().rstrip("/")
            delivery.valid_url(target_platform)

            async with httpx.AsyncClient(timeout=180, trust_env=False) as client:
                rb_resp = await client.post(
                    f"{target_platform}/api/v1/apps/{slug}/rollback",
                    json={"target_version": target_version, "reason": reason},
                )
                if rb_resp.status_code != 200:
                    raise ValueError(f"服务端版本回退失败 ({rb_resp.status_code}): {delivery_error(rb_resp.text)}")
                rb_data = rb_resp.json()

                # 从 platform-opc (MinIO) 拉取目标版本的 skill.zip 并同步覆盖本地应用技能
                skill_zip_bytes = b""
                zip_resp = await client.get(f"{target_platform}/api/v1/apps/{slug}/versions/{target_version}/artifacts/skill.zip")
                if zip_resp.status_code == 200 and zip_resp.content:
                    skill_zip_bytes = zip_resp.content
                else:
                    local_zip = service.root / "versions" / slug / f"v{target_version}" / "skill.zip"
                    if local_zip.is_file():
                        skill_zip_bytes = local_zip.read_bytes()

            local_skill_synced = False
            verification = None
            if skill_zip_bytes:
                ver_info = rb_data.get("version") or {}
                app_url = str(ver_info.get("app_url") or "")
                contract = service.install_rolled_back_skill(slug, app_url, skill_zip_bytes, target_version)
                local_skill_synced = True
                health = contract.get("health") or {}
                base_url = contract.get("base_url") or app_url
                if base_url and health.get("path"):
                    try:
                        health_contract = {
                            "base_url": base_url,
                            "apis": [{"id": "health", "path": health["path"], "method": "GET", "effect": "read", "auth": {"type": "none"}}],
                        }
                        code, content_type, content = await read_only_probe(health_contract, "health", {})
                        verification = {
                            "status": "verified-rolled-back" if code == health.get("status", 200) else "health-mismatch",
                            "http_status": code,
                            "base_url": base_url,
                        }
                    except Exception as probe_exc:
                        verification = {"status": "probe-warning", "detail": str(probe_exc)}

            return {
                "ok": True,
                "slug": slug,
                "previous_version": rb_data.get("previous_version"),
                "current_version": rb_data.get("current_version", target_version),
                "active_version": rb_data.get("current_version") or rb_data.get("active_version") or target_version,
                "rollback_mode": rb_data.get("rollback_mode", "auto"),
                "restore_mode": rb_data.get("rollback_mode") or rb_data.get("restore_mode") or "auto",
                "local_skill_synced": local_skill_synced,
                "local_skill_replaced": local_skill_synced,
                "local_skill_verified": bool(verification and verification.get("status") == "verified-rolled-back"),
                "verification": verification,
                "version": rb_data.get("version"),
                "message": rb_data.get("message") or f"已将应用 {slug} 及对应技能同步回退至 v{target_version}",
            }
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
            credentials = verification_credentials(contract, data["deployment_confirmation"]["app_config"], body.get("verification_credentials"))
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
