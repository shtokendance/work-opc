---
project: work-opc
document: overview-change-surface
demandId: WORK-RESOURCES-001
loop: 1
status: draft
updatedAt: 2026-10-23
---

# WORK-RESOURCES-001 Loop 1 change surface（七类汇总）

依据三仓隔离 worktree 未提交差异（work 基线 `7be2e4ce` 共 45 路径＝23 修改＋22 新增；platform 基线 `27de3c77` 共 23 路径；knowledge 基线 `071fd96c` 共 47 路径）与三仓 `repositories/<repoId>/implementation.md`。证据路径均为各仓库内相对路径。三仓交付均未提交、未推送、未部署、未手测；凡此情形一律标注「已实现，未上线」。

## 1. 代码与模块边界

- work 新增 4 个 enterprise 模块：`coworker/enterprise/resources.py`（platform 客户端 + ≤30s 失败关闭授权缓存 + OperationService 操作编排）、`coworker/enterprise/installer.py`（原子安装与包校验）、`coworker/enterprise/registry.py`（结构化 SkillRef 注册表与 turn 钉版）、`coworker/enterprise/scope.py`（turn 级知识范围）。证据：`coworker/enterprise/{resources,installer,registry,scope}.py`。
- work 删除旧发布依赖：`coworker/enterprise/skills.py` 移除 publish_id 安装路径与自动 catalog 同步（约 400 行），`coworker/enterprise/__init__.py` 同步导出；编排层接线 `coworker/server/app.py`、`coworker/server/manager.py`、`coworker/engine.py`（resources 参数 + `_resources` 出站剥离）。证据：同路径 diff。
- platform 新增资源 adapter 与 scope 读取：`pkg/handler/resources.go`（新增）、`pkg/handler/knowledge.go`（GetKnowledge scope 模式）、`pkg/handler/knowledge_upstream.go`（目录契约 + 独立下载 client/idleReader）、`main.go`（路由注册 234-237 行）。删除旧发布协调：`pkg/handler/skills.go`（已删）、`pkg/model/platform_state.go` 与 `pkg/storage/state.go`（SkillPublishJob 模型与 repository 方法删除）。证据：同路径 diff。
- knowledge 新增归档服务：`internal/application/service/tenant_skill_catalog_archive.go`、`internal/handler/skill_catalog_archive.go`、`internal/router/routes_agent.go`（Viewer+ 注册，93 行）。删除 release 全链路：`internal/handler/skill_release.go`、`internal/handler/skill_release_test.go`、`internal/application/service/skill_release.go`、`internal/application/service/skill_release_test.go`、`internal/application/repository/skill_release.go`（均删），`internal/types/tenant_skill.go`（SkillRelease* 类型组）、`internal/container/container.go`（4 处注入）、`internal/application/service/tenant_skill_catalog.go`（DeleteCatalog 去账本检查）、`tenant_skill_service.go`。证据：同路径 diff。
- knowledge 前端：`frontend/src/utils/resourceDeepLink.ts`（新增），`frontend/src/router/index.ts`、`frontend/src/views/auth/Login.vue`、`frontend/src/views/settings/SkillSettings.vue`、`frontend/src/views/toolbox/Toolbox.vue`、6 个 locale 文件修改。证据：同路径 diff。

## 2. 公开接口与兼容性

- work 新增 7 个 sidecar 端点（INT-101/103）：`GET /v1/enterprise/resources/knowledge-bases`、`GET /skills`、`GET /skills/{catalog_id}`、`POST /skills/{catalog_id}/install`、`PATCH /skills/{catalog_id}`、`DELETE /skills/{catalog_id}`、`GET /operations/{operation_id}`（`coworker/server/app.py:2056-2213`）；WS `user_message` 扩展 `knowledge_scope`/`skill_ref`（INT-102，`app.py:3741-3904`），provider 标准消息结构不变。
- platform 新增 4 路由（`main.go:234-237`）：`GET /integration/v1/resources/knowledge-bases|skills|skills/:catalog_id|skills/:catalog_id/archive`；`GET /integration/v1/knowledge/:id` 新增 `knowledge_base_id` 参数（与 `tenant` 成对启用，INT-104）。
- platform 删除旧路由（404，不重定向）：`/integration/v1/skills`、`/skills/:publish_id/download`、`/internal/skills`、admin `/skills/publish-jobs*`。证据：`pkg/handler/skills.go` 已删；404 断言见 `pkg/handler/resources_test.go`（TestOldSkillRoutes_NotRegistered）。
- knowledge 新增 `GET /api/v1/skills/catalog/:id/archive?expected_sha256=`（Viewer+，INT-105 信封，X-SHA256）；删除 `/api/v1/skills/catalog/:id/publish` 与 `/api/v1/skills/releases*` 家族。证据：`internal/router/skill_release_removal_test.go` 404 断言。
- 兼容性：code-opc 不改不测（Requirements v1.2 用户决定）；work 旧 publish_id 本地目录显示 `legacy_requires_reinstall`、不进执行 registry（`coworker/enterprise/installer.py`）；新 sidecar 遇旧服务端结构性 404 映射 501 `RESOURCE_API_UNSUPPORTED`（`coworker/enterprise/resources.py`）；旧草稿/历史消息无新字段按缺省读取。

## 3. 配置/权限/安全

- work：GUI 受信常量 `TRUSTED_WEB_ORIGIN = "https://shtokendance.benlai.com"`（`surfaces/gui/src/enterpriseResources.ts:10`），精确同源门、禁外域/javascript/file/data URL；授权缓存 `GRANT_TTL_SECONDS = 30.0` 失败关闭（`coworker/enterprise/resources.py:61`）；sidecar token 与 origin 校验沿用既有（`coworker/server/app.py`）。
- platform：不持有 Admin 身份代用户；按当前 entitlement 逐空间签个人桥 token；显式无权空间 403 `SPACE_FORBIDDEN`；归档代理三向哈希校验后才输出（`pkg/handler/resources.go`）。
- knowledge：归档路由 Viewer+（`internal/router/routes_agent.go:93`，API key 默认拒绝）；Admin 文件预览/上传/云沙箱安装路由未动；资源不存在与不可见对外统一 404 `RESOURCE_NOT_ACCESSIBLE`，不返回存储内部路径/沙箱 env/BundleRef。
- 密钥与日志：安装记录不含 Token（spec DATA-103）；审计只记身份摘要/资源 ID/版本 hash/结果，不记 Token、正文、技能指令（BND-012，沿用既有 auditEvent）。本轮无密钥系统变更。

## 4. 数据模型/迁移/回滚

- knowledge PG：`migrations/versioned/000133_drop_skill_release.{up,down}.sql`——只 drop `skill_release` 及 4 个专属索引，down 重建空表结构，不改写历史迁移。SQLite：`migrations/sqlite/000047_drop_skill_release.{up,down}.sql`——不动 combined 000046 的 MCP 表，禁止用其 down 整体回退。备份恢复工具 `internal/database/skill_release_cleanup.go`（Backup/Write/Read/Verify/Restore + checksum 清单 + 事务恢复），SQLite 与真实 PG 双引擎验证（空库/代表性旧行/回滚/篡改检测）。
- platform：`migrations/000017_drop_skill_publish_jobs.{up,down}.sql`（up 幂等 DROP IF EXISTS，不改历史 000011，不动 session/entitlement/audit 等共享表）；备份工具 `pkg/storage/publish_jobs_backup.go`（Export/Import，规范化 JSON + SHA-256 校验和，篡改/行数不符/表缺失拒绝，事务幂等插回）；真实 PostgreSQL 16 临时库全链验证（含共享表存活、重启 RunMigrations 幂等，验证后已 DROP）。
- work：无 SQL/对象存储变更；会话 JSONL 新增 `_resources` sidecar（旧记录缺省读取）；SQLite 会话索引不加列。
- 执行状态：以上迁移与工具已实现并验证，但生产/共享环境清理未执行——前置条件（旧写入停止、备份导出与恢复演练、新链路验收）见 BND-011 与 `release-checklist.md`；对象存储清理本轮只盘点/备份，未删对象。

## 5. 运行时/部署/可观测性

- 无新增服务、端口、消息队列或密钥系统；拓扑沿用「桌面 GUI/Tauri → 本地 sidecar → platform → knowledge API/数据库/对象存储」，浏览器访问 knowledge-web（ADR-009）。
- 发布顺序固定 knowledge → platform → work；当前三仓均未构建镜像、未部署（已实现，未上线），线上仍跑旧二进制。
- 运行观测：work 操作阶段/错误经 `GET /operations/{operation_id}` 轮询（离页取消）；platform 沿用 auditEvent 审计；下载预算——列表 15s、归档总 120s/无进度 15s（platform `newDownloadClient`/`idleReader`；work 侧同预算 + 下载并发上限 2）、安装操作 5 分钟预算；失败/超时/终态语义按 INT-105。
- 部署授权：生产或共享环境执行迁移与清理仍需对应部署授权（BND-011），本阶段未执行。

## 6. 测试与验收

- work：新增 `tests/test_enterprise_{resources,scope,installer,registry,ws}.py` 等 9 个 Python 测试文件与 5 个 GUI 测试文件（`surfaces/gui/src/enterpriseResources.test.ts`、`components/{ResourcesView,EnterpriseSkillsPanel,ResourceChips}.test.tsx`、`components/Composer.resources.test.tsx`）。结果：pytest **162 passed**；GUI 定向 **22 passed**；全量回归 578 passed / 3 failed（`enterprise.test.ts` 在基线复现，既有环境性失败）；PBT P1～P4 固定 seed 全 PASS。证据：`repositories/work-opc/verification.md`。
- platform：新增 `pkg/handler/{resources_test,knowledge_scope_test,resources_pbt_test}.go`、`pkg/storage/publish_jobs_backup_test.go`，重写 `knowledge_fake_test.go` 夹具。结果：`go test ./...`、`go test -race ./pkg/handler`、`go build ./...`、`go vet ./...` 全绿；真实 PG16 迁移往返 6 子测试全 PASS；PBT P1（seed=20261022，500 case，允许输出集 ⊆ scope）/P2（seed=20261023，500 case）。证据：`repositories/platform-opc/verification.md`。
- knowledge：新增 `tenant_skill_catalog_archive{,_pbt}_test.go`、`catalog_archive_fixture_test.go`、`skill_catalog_archive{,_pbt}_test.go`、`skill_catalog_archive_route_test.go`、`skill_release_removal_test.go`、`skill_release_cleanup_test.go`、`frontend resourceDeepLink.test.mjs`、`loginRedirect.test.mjs`。结果：四包 Go 定向测试通过（`TestSkillPythonVerifier` 为基线既有环境性失败，已复现排除）；`go test ./internal/database` 全 ok；frontend type-check/build/test 全绿；PBT P4（seed=20261025，两处各 600 case）。证据：`repositories/knowledge-opc/verification.md`。
- 未执行（需授权）：三仓联调（TASK-025）、真实浏览器/DMG 手测与 60 秒撤权真实计时（TASK-027）、独立 AI Review 与人工签署（TASK-029/030）。

## 7. Loop 文档/README/长期架构

- 长期架构基线：`docs/architecture/overview.md` v1.2 → **v1.3**（本次反向对账回写，含原因/影响留痕；同步至 work-opc.wr001 交付树）。
- 本文件：新建。
- 主仓 demand 文档：`loop-1/{requirements,architecture,specification,tasks,implementation}.md`、`repositories/<repoId>/{implementation,verification,tasks}.md`、`release-checklist.md`、`repositories.json` 本轮新增/更新（status 以各文件 frontmatter 为准）。
- 各仓 README/AGENTS.md：无变更（三仓 diff 均未含 README、AGENTS.md、CLAUDE.md；platform worktree 的 AGENTS.md/CLAUDE.md/docs/loops 未跟踪内容按要求留在原工作区，不带入交付）。
