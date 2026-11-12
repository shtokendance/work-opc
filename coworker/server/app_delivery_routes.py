"""Desktop application-delivery routes; approval and package identity are server checked."""

import base64
import asyncio
import json
import os
from pathlib import Path
import uuid
import zipfile

import httpx
from fastapi import HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse

from ..app_delivery import AppDelivery
from ..app_delivery_skill.scripts import delivery
from ..config import load_config
from ..secrets import state_dir


DEFAULT_PLATFORM_URL = "https://shtokendance.benlai.com"
ROLLBACK_NOTICE = "回滚恢复代码与运行配置，不自动回退数据库/卷中的业务数据；旧代码和新数据兼容性仍需验证。"


def verification_credentials(contract, deployment_config, supplied):
    """Transient probe credentials; never add login sessions to deployment config."""
    allowed = {a.get("auth", {}).get("env") for a in contract.get("apis", []) if a.get("auth", {}).get("type") in {"header", "bearer"}}
    for profile in [contract.get("auth_profile", {}), *contract.get("auth_profiles", {}).values()]:
        allowed.update(profile[k] for k in ("username_env", "password_env", "token_env") if isinstance(profile, dict) and profile.get(k))
    supplied = {} if supplied is None else supplied
    if not isinstance(supplied, dict) or any(k not in allowed or not isinstance(v, str) or len(v) > 16384 or any(c in v for c in "\r\n\x00") for k, v in supplied.items()):
        raise ValueError("业务验证凭据无效，请仅填写本应用声明的登录凭据")
    return {**{c["key"]: c.get("value", "") for c in deployment_config}, **supplied}


def default_platform_url() -> str:
    try:
        cfg = load_config()
        deploy_url = (getattr(cfg, "opc_deploy_base", "") or "").strip()
        if deploy_url:
            return deploy_url.rstrip("/")
    except Exception:
        pass
    try:
        cfg_file = state_dir() / "app-delivery" / "platform_config.json"
        if cfg_file.is_file():
            saved = json.loads(cfg_file.read_text(encoding="utf-8"))
            if isinstance(saved, dict) and str(saved.get("platform_url") or "").strip():
                return str(saved["platform_url"]).strip().rstrip("/")
    except Exception:
        pass
    env_url = (
        os.environ.get("OPC_DEPLOY_BASE")
        or os.environ.get("OPC_PLATFORM_HTTP")
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

    def platform_target():
        enterprise = getattr(app.state, "enterprise", None)
        context = enterprise.context if enterprise else None
        if not context or not context.access_token or context.access_expired():
            raise HTTPException(status_code=403, detail="请先完成企业微信登录，再打开应用中心")
        base = enterprise.platform_base.rstrip("/")
        delivery.valid_url(base)
        return base + "/integration/v1/apps", {"Authorization": f"Bearer {context.access_token}"}

    async def platform_request(method, path="", *, timeout=20, **kwargs):
        base, headers = platform_target()
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False) as client:
            fn = getattr(client, method.lower(), None)
            if fn is not None:
                result = await fn(base + path, headers=headers, **kwargs)
            else:
                result = await client.request(method, base + path, headers=headers, **kwargs)
        if result.status_code in {401, 403}:
            message = "企微登录已失效，请重新登录" if result.status_code == 401 else "没有管理此应用的权限"
            try:
                error = result.json().get("error")
                if isinstance(error, dict) and isinstance(error.get("message"), str):
                    message = error["message"]
            except ValueError:
                pass
            raise HTTPException(status_code=403, detail=message)
        return result

    async def catalog_request(method, path="", **kwargs):
        try:
            response = await platform_request(method, path, **kwargs)
            if not response.is_success:
                raise HTTPException(status_code=response.status_code, detail=delivery_error(response.text))
            return response.json()
        except (ValueError, OSError, httpx.HTTPError) as exc:
            raise fail(exc)

    @app.get("/v1/apps/catalog")
    async def catalog():
        return await catalog_request("GET")

    @app.post("/v1/apps/catalog/check-slug")
    async def check_slug(body: dict):
        return await catalog_request("POST", "/check-slug", json={"slug": body.get("slug", "")})

    @app.get("/v1/apps/catalog/resources")
    async def resources():
        return await catalog_request("GET", "/resources")

    @app.get("/v1/apps/catalog/{slug}")
    async def detail(slug: str):
        if not delivery.SLUG.fullmatch(slug):
            raise HTTPException(status_code=400, detail="应用标识格式无效")
        return await catalog_request("GET", f"/{slug}")

    @app.put("/v1/apps/catalog/{slug}/config")
    async def update_config(slug: str, body: dict):
        if not delivery.SLUG.fullmatch(slug):
            raise HTTPException(status_code=400, detail="应用标识格式无效")
        result = await catalog_request("PUT", f"/{slug}/config", json=body)
        # Invalidate prior business verification for local jobs of this app when config changes
        for file in sorted(service.root.glob("*/job.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                job_data = service.load(file.parent.name)
            except Exception:
                continue
            if job_data.get("slug") != slug:
                continue
            verif = job_data.get("verification")
            if isinstance(verif, dict) and str(verif.get("status", "")).startswith("verified"):
                verif["status"] = "stale_config"
                verif["stale_reason"] = "运行配置已变更，需重新执行业务验证"
                job_data["verification"] = verif
                service.save(job_data)
        target_skill = service.manager.skill_store.global_dir / f"app-{slug}"
        if target_skill.is_dir() and not target_skill.is_symlink():
            was_user_disabled = service._was_skill_user_disabled(
                target_skill, f"app-{slug}" in service.manager.skill_store.disabled_names()
            )
            pending_text = (
                json.dumps({"reason": "运行配置已变更，等待业务验证", "was_user_disabled": True}, ensure_ascii=False)
                if was_user_disabled
                else "运行配置已变更，等待业务验证"
            )
            (target_skill / ".work-delivery-pending").write_text(pending_text, encoding="utf-8")
            service.manager.skill_store.set_enabled(f"app-{slug}", False)
        return result

    @app.delete("/v1/apps/catalog/{slug}")
    async def delete_app(slug: str, purge_volumes: bool = False):
        if not delivery.SLUG.fullmatch(slug):
            raise HTTPException(status_code=400, detail="应用标识格式无效")
        params = {"purge_volumes": "true"} if purge_volumes else None
        return await catalog_request("DELETE", f"/{slug}", params=params)

    @app.post("/v1/apps/transform")
    async def transform(body: dict):
        try:
            platform_target()
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
                platform_url = get_platform_config()["platform_url"]
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
            service.launch_analysis(job_id, str(body.get("feedback", "")), repair=body.get("mode") == "repair")
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
        enterprise = getattr(app.state, "enterprise", None)
        url = (enterprise.platform_base if enterprise else "") or default_platform_url()
        return {
            "ok": True,
            "platform_url": url,
            "resource_api_url": f"{url}/integration/v1/apps/resources",
            "resources_api_url": f"{url}/integration/v1/apps/resources",
            "dockerfile_template_api_url": f"{url}/integration/v1/apps/dockerfile-templates",
            "dockerfile_templates_api_url": f"{url}/integration/v1/apps/dockerfile-templates",
            "admin_deploy_url": f"{url}/admin/deploy",
            "deploy_admin_url": f"{url}/admin/deploy",
        }

    @app.put("/v1/apps/platform-config")
    def update_platform_config(body: dict):
        config = get_platform_config()
        if str(body.get("platform_url", "")).rstrip("/") != config["platform_url"]:
            raise HTTPException(status_code=422, detail="应用中心使用企微登录的平台地址，请在企业设置中更改")
        return config

    @app.get("/v1/apps/runtime-logs/{slug}")
    async def get_app_runtime_logs(slug: str, tail: int = 200):
        try:
            if not delivery.SLUG.fullmatch(slug):
                raise ValueError("应用标识格式无效")
            resp = await platform_request("GET", f"/{slug}/logs", timeout=15, params={"tail": tail})
            if resp.status_code != 200:
                raise ValueError(f"获取应用日志失败 ({resp.status_code}): {delivery_error(resp.text)}")
            return resp.json()
        except (ValueError, OSError, httpx.HTTPError) as exc:
            raise fail(exc)

    @app.post("/v1/apps/recheck/{slug}")
    async def recheck_app_health(slug: str):
        if not delivery.SLUG.fullmatch(slug):
            raise HTTPException(status_code=400, detail="应用标识格式无效")
        return await catalog_request("POST", f"/{slug}/recheck", timeout=30)

    async def _reconcile_publish_request(data: dict, folder: Path) -> dict | None:
        request_id = str(data.get("publish_request_id") or "").strip()
        slug = data.get("slug", "")
        if request_id and slug:
            try:
                req_resp = await platform_request("GET", f"/{slug}/requests/{request_id}", timeout=15)
                if req_resp.status_code == 200:
                    req_data = req_resp.json()
                    req_status = str(req_data.get("status") or "").lower()
                    if req_status in {"accepted", "building", "running", "failed"}:
                        pub_ver = int(req_data.get("version") or data.get("published_version") or 1)
                        data["published_version"] = pub_ver
                        data["publish_state"] = req_status
                        if req_status in {"accepted", "building", "running"}:
                            data["published"] = True
                            if folder.is_dir():
                                service.archive_published_version(slug, pub_ver, folder)
                        service.save(data)
                        return req_data
                elif req_resp.status_code == 404:
                    data["publish_state"] = "not_received"
                    service.save(data)
            except Exception:
                pass
        return None

    @app.post("/v1/apps/jobs/{job_id}/reconcile-publish")
    async def reconcile_publish(job_id: str):
        try:
            data = service.load(job_id)
            folder = service.path(job_id) / "bundle"
            reconciled = await _reconcile_publish_request(data, folder)
            if reconciled:
                return {"ok": True, "reconciled": True, "publish_state": data.get("publish_state"), "data": reconciled}
            return {"ok": True, "reconciled": False, "publish_state": data.get("publish_state", "unknown")}
        except (ValueError, OSError, httpx.HTTPError) as exc:
            raise fail(exc)

    @app.post("/v1/apps/publish-to-platform")
    async def publish(body: dict):
        try:
            data, folder = service.verified_package(body.get("job_id", ""), body)
            base, _ = platform_target()
            platform_url = base.removesuffix("/integration/v1/apps")
            if data.get("platform_url") and data["platform_url"].rstrip("/") != platform_url:
                raise ValueError("此部署包属于另一个平台，请重新导入源码并确认部署地址")
            data["platform_url"] = platform_url
            version_note = str(body.get("version_note") or data.get("version_note") or "").strip()
            if version_note:
                data["version_note"] = version_note
            request_id = str(body.get("request_id") or data.get("publish_request_id") or "").strip() or uuid.uuid4().hex
            data["publish_request_id"] = request_id
            if data.get("publish_state") == "unknown" and not body.get("force_reupload"):
                reconciled = await _reconcile_publish_request(data, folder)
                if reconciled and str(reconciled.get("status") or "").lower() in {"accepted", "building", "running"}:
                    return {"ok": True, "reconciled": True, "data": reconciled}
                if data.get("publish_state") == "unknown":
                    raise ValueError("上次上传响应中断，发布状态未知（unknown）；请先核对服务端状态或明确确认重传")
            service.install_pending_skill(data, folder)
            data["publish_state"] = "uploading"
            service.save(data)
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
                "request_id": request_id,
                "app_config": json.dumps(body.get("app_config", [])),
                "resource_ids": json.dumps(body.get("resource_ids", [])),
                "storage_mounts": json.dumps(data["proposal"]["contract"].get("storage_mounts", [])),
            }
            files = {"app_tar": ("app.tar", (folder / "app.tar").read_bytes(), "application/x-tar"), "skill_zip": ("skill.zip", (folder / "skill.zip").read_bytes(), "application/zip")}
            try:
                response = await platform_request("POST", "/publish", timeout=60, data=fields, files=files)
            except (httpx.HTTPError, OSError) as net_exc:
                reconciled = await _reconcile_publish_request(data, folder)
                if reconciled and str(reconciled.get("status") or "").lower() in {"accepted", "building", "running"}:
                    if reconciled.get("app_url") and reconciled.get("app_url") != data["app_url"]:
                        raise ValueError("平台返回的 URL 与用户确认地址不同，拒绝启用技能")
                    data["events"].append(f"上传连接中断后已通过幂等请求号核实平台接收状态 (v{data.get('published_version', 1)})")
                    service.save(data)
                    return {"ok": True, "reconciled": True, "data": reconciled}
                if data.get("publish_state") != "not_received":
                    data["publish_state"] = "unknown"
                    service.save(data)
                raise net_exc
            if response.status_code not in {200, 202}:
                data["publish_state"] = "failed"
                service.save(data)
                raise ValueError("平台拒绝部署：" + str(response.status_code) + " " + delivery_error(response.text))
            result = response.json()
            if result.get("app_url") != data["app_url"]:
                raise ValueError("平台返回的 URL 与用户确认地址不同，拒绝启用技能")
            pub_ver = int(result.get("version") or 1)
            data["published_version"] = pub_ver
            data["publish_state"] = str(result.get("status") or "building")
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
            resp = await platform_request("GET", f"/{slug}/versions", timeout=20)
            if resp.status_code != 200:
                raise ValueError(f"查询应用版本历史失败 ({resp.status_code}): {delivery_error(resp.text)}")
            payload = resp.json()
            # 保留所有版本记录，包括部署未成功的版本，让版本列表与顶部当前版本保持完全透明一致，避免出现版本错位或“幽灵版本”
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
            resp = await platform_request("GET", f"/{slug}/versions/{version}/artifacts/{filename}", timeout=60)
            if resp.status_code == 200:
                media_type = resp.headers.get("content-type", "application/octet-stream")
                return Response(
                    content=resp.content,
                    media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{slug}-v{version}-{filename}"'},
                )
            local_fallback = service.root / "versions" / slug / f"v{version}" / filename
            if resp.status_code == 404 and local_fallback.is_file():
                remote = await catalog_request("GET", f"/{slug}")
                if remote.get("can_manage") is True:
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

            rb_resp = await platform_request(
                "POST", f"/{slug}/rollback", timeout=660,
                json={"target_version": target_version, "reason": reason},
            )
            if rb_resp.status_code != 200:
                raise ValueError(f"服务端版本回退失败 ({rb_resp.status_code}): {delivery_error(rb_resp.text)}")
            rb_data = rb_resp.json()

            # 从 platform-opc (MinIO) 拉取目标版本的 skill.zip 并同步覆盖本地应用技能
            skill_zip_bytes = b""
            zip_resp = await platform_request("GET", f"/{slug}/versions/{target_version}/artifacts/skill.zip", timeout=60)
            if zip_resp.status_code == 200 and zip_resp.content:
                skill_zip_bytes = zip_resp.content
            elif zip_resp.status_code == 404:
                local_zip = service.root / "versions" / slug / f"v{target_version}" / "skill.zip"
                if local_zip.is_file():
                    skill_zip_bytes = local_zip.read_bytes()

            local_skill_synced = False
            verification = None
            if skill_zip_bytes:
                ver_info = rb_data.get("version") or {}
                app_url = str(ver_info.get("app_url") or "")
                expected_sha = ver_info.get("skill_zip_sha256")
                if not expected_sha or delivery.sha(skill_zip_bytes) != expected_sha:
                    raise ValueError("回滚技能包摘要不一致，拒绝启用")
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
                        if code != health.get("status", 200) or "application/json" not in content_type or not delivery.subset(json.loads(content), health.get("json_contains", {"service": slug})):
                            raise ValueError("回滚后健康身份验证失败")
                        cases = contract.get("verification", {}).get("cases", [])
                        has_non_health_apis = any(
                            isinstance(a, dict) and a.get("path") != health.get("path")
                            for a in contract.get("apis", [])
                        )
                        latest = await platform_request("GET", f"/{slug}")
                        current = latest.json() if latest.status_code == 200 else {}
                        if latest.status_code != 200 or current.get("status") != "running" or current.get("current_version") != target_version:
                            raise ValueError("验证期间线上版本发生变化")
                        rb_fp = rb_data.get("config_fingerprint") or ver_info.get("config_fingerprint")
                        cur_fp = current.get("config_fingerprint")
                        if rb_fp and cur_fp and rb_fp != cur_fp:
                            raise ValueError("验证期间线上运行配置指纹发生变化")
                        if cases:
                            for case in cases:
                                operation = next((a for a in contract.get("apis", []) if a["id"] == case["operation"]), None)
                                if not operation or operation.get("effect") != "read" or operation.get("path") == "/healthz":
                                    raise ValueError("必须使用只读业务接口验证")
                                code, content_type, content = await read_only_probe(contract, case["operation"], case.get("params", {}), case.get("body"))
                                if code != case.get("status", 200) or "application/json" not in content_type or not delivery.subset(json.loads(content), case["json_contains"]):
                                    raise ValueError("回滚后业务验证失败")
                            service.finalize_rolled_back_skill(slug)
                            verification = {
                                "status": "verified-rolled-back",
                                "base_url": base_url,
                                "config_fingerprint": cur_fp or rb_fp or "",
                            }
                        elif contract.get("pages") and not has_non_health_apis:
                            verification = {
                                "status": "awaiting_page_verification",
                                "base_url": base_url,
                                "config_fingerprint": cur_fp or rb_fp or "",
                                "detail": "容器与健康身份已就绪，纯页面应用请打开页面完成人工页面验收",
                            }
                        else:
                            raise ValueError("缺少业务验证，技能保持停用")
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
                "rollback_notice": ROLLBACK_NOTICE,
                "message": rb_data.get("message") or f"已将应用 {slug} 及对应技能同步回退至 v{target_version}",
            }
        except (ValueError, OSError, httpx.HTTPError) as exc:
            raise fail(exc)

    @app.get("/v1/apps/storage/{slug}")
    async def get_app_storage_details(slug: str, platform_url: str = ""):
        try:
            if not delivery.SLUG.fullmatch(slug):
                raise ValueError("应用标识格式无效")
            resp = await platform_request("GET", f"/{slug}/storage", timeout=20)
            if resp.status_code != 200:
                raise ValueError(f"获取应用存储详情失败 ({resp.status_code}): {delivery_error(resp.text)}")
            return resp.json()
        except (ValueError, OSError, httpx.HTTPError) as exc:
            raise fail(exc)

    @app.get("/v1/apps/storage/{slug}/backup")
    async def download_app_storage_backup(slug: str, path: str = "", mount: str = "", platform_url: str = ""):
        try:
            if not delivery.SLUG.fullmatch(slug):
                raise ValueError("应用标识格式无效")
            params = {}
            if path:
                params["path"] = path
            if mount:
                params["mount"] = mount

            base, headers = platform_target()
            client = httpx.AsyncClient(timeout=300, trust_env=False, follow_redirects=False)
            req = client.build_request("GET", f"{base}/{slug}/storage/backup", params=params, headers=headers)
            try:
                resp = await client.send(req, stream=True)
            except BaseException:
                await client.aclose()
                raise
            if resp.status_code != 200:
                body = await resp.aread()
                await resp.aclose()
                await client.aclose()
                raise ValueError(f"下载存储备份失败 ({resp.status_code}): {delivery_error(body.decode('utf-8', errors='replace'))}")

            media_type = resp.headers.get("content-type", "application/x-tar")
            cd = resp.headers.get("content-disposition", f'attachment; filename="{slug}-storage-backup.tar"')

            async def stream_generator():
                try:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
                finally:
                    await resp.aclose()
                    await client.aclose()

            return StreamingResponse(stream_generator(), media_type=media_type, headers={"Content-Disposition": cd})
        except (ValueError, OSError, httpx.HTTPError) as exc:
            raise fail(exc)

    @app.post("/v1/apps/enable-skill")
    async def enable(body: dict):
        try:
            data = service.load(body.get("job_id", ""))
            folder = service.path(data["id"]) / "bundle"
            if not data.get("published") and data.get("publish_request_id"):
                await _reconcile_publish_request(data, folder)
            if not data.get("confirmation_token") or not data.get("published"):
                raise ValueError("应用尚未确认部署")
            response = await platform_request("GET", "/" + data["slug"])
            response.raise_for_status()
            status = response.json()
            if status.get("status") != "running" or status.get("app_url") != data["app_url"]:
                raise ValueError("部署尚未成功，不能启用业务技能")
            summary = delivery.load(folder / "summary.json")
            for filename in ("app.tar", "skill.zip"):
                receipt = "[Artifact] " + filename + ":" + summary["files"][filename]
                if receipt not in status.get("build_log", ""):
                    raise ValueError("线上部署回执与当前应用包或技能版本不一致，拒绝启用")
            contract = data["proposal"]["contract"]
            from ..app_delivery_auth import validate_auth_probe_coverage
            validate_auth_probe_coverage(contract)
            health = contract["health"]
            health_contract = {"base_url": data["app_url"], "apis": [{"id": "health", "path": health["path"], "method": "GET", "effect": "read", "auth": {"type": "none"}}]}
            code, content_type, content = await read_only_probe(health_contract, "health", {})
            if code != health["status"] or "application/json" not in content_type or not delivery.subset(json.loads(content), health["json_contains"]):
                raise ValueError("应用健康身份校验失败，不能启用业务技能")
            supplied_creds = body.get("verification_credentials")
            if supplied_creds is None and isinstance(data.get("saved_verification_credentials"), dict):
                supplied_creds = data["saved_verification_credentials"]
            credentials = verification_credentials(contract, data["deployment_confirmation"]["app_config"], supplied_creds)
            if isinstance(body.get("verification_credentials"), dict):
                data["saved_verification_credentials"] = dict(body["verification_credentials"])
            checks = []
            for case in contract["verification"].get("cases", []):
                operation = next((api for api in contract["apis"] if api["id"] == case["operation"]), {})
                if operation.get("path") == health["path"]:
                    continue  # Repeating the health probe cannot certify business behavior.
                code, content_type, content = await read_only_probe(contract, case["operation"], case.get("params", {}), case.get("body"), credential_env=credentials)
                if code != case.get("status", 200) or "application/json" not in content_type or not delivery.subset(json.loads(content), case["json_contains"]):
                    raise ValueError(f"只读业务验证失败：{case['operation']}（HTTP {code}，预期状态或业务字段不匹配）")
                checks.append({"operation": case["operation"], "status": code, "passed": True})
            config_fp = str(status.get("config_fingerprint") or "")
            if not checks:
                has_pages = bool(contract.get("pages"))
                has_non_health_apis = any(
                    isinstance(api, dict) and api.get("path") != health["path"]
                    for api in contract.get("apis", [])
                )
                if has_pages and body.get("confirm_page_verification") is True:
                    service.enable_verified_skill(data)
                    data["verified_config_fingerprint"] = config_fp
                    data["verified_version"] = status.get("current_version")
                    data["verification"] = {
                        "status": "verified-deployed-page",
                        "base_url": data["app_url"],
                        "checks": [],
                        "pages_verified": True,
                        "config_fingerprint": config_fp,
                    }
                    service.save(data)
                    return {"ok": True, "verification": data["verification"]}
                if has_pages and not has_non_health_apis:
                    data["verification"] = {
                        "status": "awaiting_page_verification",
                        "base_url": data["app_url"],
                        "checks": [],
                        "pages_verified": False,
                        "config_fingerprint": config_fp,
                        "detail": "容器已启动并通过健康身份校验，请打开应用页面完成人工验收后确认启用技能",
                    }
                    service.save(data)
                    return {"ok": True, "verification": data["verification"]}
                raise ValueError("缺少实际只读业务验证，健康检查不能代替。请在改造报告补充业务查询条件及预期结果后重新确认；页面应用需先完成浏览器验证。")
            service.enable_verified_skill(data)
            data["verified_config_fingerprint"] = config_fp
            data["verified_version"] = status.get("current_version")
            data["verification"] = {
                "status": "verified-deployed-api",
                "base_url": data["app_url"],
                "checks": checks,
                "pages_verified": False,
                "config_fingerprint": config_fp,
            }
            service.save(data)
            return {"ok": True, "verification": data["verification"]}
        except (ValueError, OSError, httpx.HTTPError) as exc:
            raise fail(exc)


def delivery_error(text):
    from ..app_delivery import redacted
    return redacted(text)[:1000]
