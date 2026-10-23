# WORK-RESOURCES-001 测试环境部署证据（knowledge-opc + platform-opc）

- 部署日期: 2026-10-23（Asia/Shanghai，窗口约 08:52–09:20）
- 目标环境: Tokendance 测试服务器 10.101.102.116（ecs-user，docker 经 `sudo -n`）
- 部署人: Kimi Code subagent（部署工程师角色，用户已授权测试环境部署；生产不在范围）
- 版本 tag: `wr001-20261023`（knowledge app/frontend 镜像 + gateway 镜像/二进制）
- 源码来源（未提交 worktree）:
  - knowledge: `/Users/yangjing/code/knowledge-opc.wr001`（基线 `071fd96c`，44 修改 + 5 删除 + 18 新增路径）
  - platform: `/Users/yangjing/code/platform-opc.wr001`（基线 `27de3c77`，21 修改 + 3 删除 + 若干新增路径）

## 0. 部署前实况（勘察要点）

- WeKnora-app/frontend 运行 `opc-int-001` 镜像（37 小时前起，OPC-INTEGRATION-001 构建）；weknora-gateway 运行 `weknora-tokendance-weknora-gateway:latest`（镜像 ID `a1323475ba93`）。
- knowledge 容器由 `/data/OPC-2.0/docker-compose.yml`（project `weknora`）管理；`.env` 的 `WEKNORA_VERSION` 决定镜像 tag；`AUTO_MIGRATE=true`。
- DB: 共享 PG16 容器 `db`。knowledge 库 = `weknora`（owner root）；platform statedb = `weknora_gateway_state`（owner opc_platform）。
- `DB_DRIVER=postgres`、`DB_PATH=<unset>`（SQLite 迁移在本部署不执行，见 N/A 节）。
- 上一需求构建树 `/data/build/{knowledge-opc,platform-opc}` 在服务器上留存；其中 platform 源码树已滞后于在跑的 gateway 二进制（缺 12e4fab 之后的 service-identity 等提交，直接叠加本次变更会编译失败）——platform 改为**全量 worktree 传输构建**（见偏差节）。
- BND-011 旧写停止确认: 近 7 天 app 日志无任何 `POST …/publish|delist` 调用；`skill_release` 与 `skill_publish_jobs` 两表均为 0 行；`/skills/releases` 仅有 GET 空列表读取（最近 2026-10-22 13:04 UTC，Cloudflare 段 IP）。旧写路径事实上已闲置，platform 新代码不再引用 knowledge release 路由（仅注释提及）。

## 1. 备份与恢复演练（任何变更前完成，TS=20261023-085247 CST）

服务器落盘目录 `/data/backup/wr001/`（root 755，可 sudo 读取）：

| 文件 | 大小 | sha256 |
| --- | --- | --- |
| `wr001-weknora-20261023-085247.sql`（weknora 全库） | 593,534,040 B | `899db85c69b84af7c4286561a2e8129b3142015504482e1ae50b05d0f696ea29` |
| `wr001-weknora-skill_release-20261023-085247.sql`（单表） | 3,841 B | `26dfff56f485c05a05148c9b44ebd3e3ecd58745dfb1d0c91cde18de3da5a6cc` |
| `wr001-statedb-20261023-085247.sql`（statedb 全库） | 437,703 B | `082f5e1d924963243d958946e6bf2e24093dfe71b336dce2a2da022b73903a96` |
| `wr001-statedb-skill_publish_jobs-20261023-085247.sql`（单表） | 2,035 B | `b067ec459be3fc0b7ae5ce7946f2e22fa688dd4ea163a1b3f9fa5a98be3942df` |

- 全部备份非空，sha256 记录于 `/data/backup/wr001/backup-sha256.txt`。
- **恢复演练（VAL-217）**: 以全库 dump 分别还原到临时库 `wr001_reh_weknora` / `wr001_reh_state`（`psql -q < dump`，均 `EXITCODE=0`），对 11+6 张共享表比对 `count(*)` 与 `md5(string_agg(pk))` 指纹，与迁移前证据**完全一致**；临时库已 DROP。
- 迁移前证据文件: `pre-migration-weknora-evidence.txt`（11 表）、`pre-migration-statedb-evidence.txt`（6 表 + schema_migrations）。
- gateway 旧二进制备份: `/data/weknora-tokendance/tokendance-gateway.bak-wr001-20261023-091216`（sha256 `f81b7742…44dd3ed`，记录于 `gateway-old-binary-sha256.txt`）。
- `.env` 备份: `/data/OPC-2.0/.env.bak-wr001-20261023-090958`。

回滚点镜像（保留未删）: `wechatopenai/weknora-app:opc-int-001` = `6a977458f66c`；`wechatopenai/weknora-ui:opc-int-001` = `09dc8dcb3906`；gateway 旧镜像 `a1323475ba93`（同 `:latest` 旧指针，已由新 `:latest` 覆盖，可用 `docker tag a1323475ba93 weknora-tokendance-weknora-gateway:rollback-pre-wr001` 需要时重建指针）+ 旧二进制 `.bak-wr001-20261023-091216`。

## 2. knowledge 构建与迁移

构建树 `/data/build/wr001-knowledge`（= rsync `/data/build/knowledge-opc` 叠加 44 个变更文件、删除 5 个 release 文件；dbd9ab57..071fd96c 区间仅 docs 变更，代码树完备；部署文件 Dockerfile/compose/config 未受叠加影响）。构建参数与上次一致（`APK_MIRROR_ARG=mirrors.tencent.com`、`WITH_ANYDOC=1`、`GOPROXY=goproxy.cn`、`NPM_REGISTRY=registry.npmmirror.com`）。

镜像:
- `wechatopenai/weknora-app:wr001-20261023` = `197000c9027c`
- `wechatopenai/weknora-ui:wr001-20261023` = `8ddbcff78ebe`

部署: `.env` 改 `WEKNORA_VERSION=wr001-20261023` 后 `docker compose up -d --no-deps app frontend`（09:09:58 CST）。app 09:10:24 启动，AUTO_MIGRATE 执行 versioned `000133_drop_skill_release`。

迁移验证:
- `schema_migrations` = `133 | f`；`skill_release` 表已不存在（pg_tables 计数 0，含 4 个专属索引一并删除）。
- 迁移后 10 张共享表（tenant_skill_catalog/tenant_skills/users/tenants/auth_tokens/knowledge_bases/audit_logs/tenant_api_keys/mcp_invocations/mcp_invocation_decisions）行数 + 指纹与迁移前**逐项一致**（`post-migration-weknora-evidence.txt`）。
- `/health` = 200 `{"status":"ok"}`；前端 `/` = 200。

## 3. platform 构建与迁移

- 二进制: 全量 worktree 树 `/data/build/wr001-platform-full` 上服务器原生 `go 1.26.5` 构建（`CGO_ENABLED=0 -trimpath -ldflags "-s -w"`），产物 `tokendance-gateway.wr001` sha256 `447da161f75d9a4addec39a16b6b5b571c7b7f8ed7ad5b9092df74fe6dad7625`（静态 ELF x86-64）。
- 部署: 旧二进制备份后 `rm+cp` 换入新二进制（执行中的旧文件 ETXTBSY，需删后新建 inode）；`docker build -t weknora-tokendance-weknora-gateway:wr001-20261023 -t …:latest .`；`docker compose up -d --force-recreate weknora-gateway`（09:12 CST）。
- 镜像: `weknora-tokendance-weknora-gateway:wr001-20261023` = `0c95c41eceb2`（容器实跑 ID 一致）。
- 迁移: 启动日志 `[Storage] migration applied: 000017_drop_skill_publish_jobs`（09:12:42）；`schema_migrations` 最新版本 `000017_drop_skill_publish_jobs`；`skill_publish_jobs` 表已不存在。
- statedb 迁移后共享表（sessions/managed_entitlements/audit_events/identity_links/sync_runs）行数 + 指纹与迁移前**逐项一致**（`post-migration-statedb-evidence.txt`）。冒烟期间新增 4 行 audit（匿名探测的 401 denial 审计，属预期行为，审计行 jsonb 形状正常）。
- `/health` = 200。

## 4. 冒烟结果

### knowledge（app :3001 / frontend :3003）

测试身份: 用部署 JWT_SECRET 铸造 10 分钟 Viewer JWT（用户 `88aa93f8-…-f5adbec6ef06`/杨晶, tenant 296492），并按 `auth_tokens` 服务端会话校验要求临时插入会话行、测试后删除（auth_tokens 恢复 228 行、指纹 `9021b965…d4931ae` 不变）。

| 探测 | 结果 |
| --- | --- |
| `GET /api/v1/skills/releases` | **404**（带认证） |
| `GET /api/v1/skills/releases/<uuid>` | **404** |
| `GET /api/v1/skills/releases/<uuid>/archive` | **404** |
| `POST /api/v1/skills/releases/<uuid>/delist` | **404** |
| `POST /api/v1/skills/catalog/<uuid>/publish` | **404**（VAL-216 全过） |
| `GET /api/v1/skills/catalog`（回归） | 200，列表正常 |
| `GET …/catalog/not-valid-id../archive`（认证） | 400，INT-105 信封 `{success:false,error:{code:INVALID_REQUEST,message,request_id}}` |
| `GET …/catalog/<uuid全零>/archive?expected_sha256=…`（认证） | 404，信封 `RESOURCE_NOT_ACCESSIBLE` |
| `GET …/catalog/<已知id>/archive?expected_sha256=<目录详情 bundle_sha256>`（认证） | **200**，`Content-Type: application/zip`，`X-SHA256=e2664ec5…11e2838` 与实际字节 sha256 **一致**（HASH-MATCH-OK），zip 内含 SKILL.md/LICENSE.txt |
| 同上但 `expected_sha256` 传 64 位全零 | 409，信封 `SKILL_VERSION_CHANGED` |
| `GET …/catalog/<id>/archive` 未认证 | 401 `{"error":"Unauthorized: missing authentication"}`（全局认证中间件形状，见 N/A 节） |
| 前端 `/`、`/api/v1/system/info` | 200 / 401（回归正常） |

### platform（gateway :3002）

| 探测 | 结果 |
| --- | --- |
| 路由注册表（gin debug 启动日志） | `/integration/v1/resources/knowledge-bases`、`/resources/skills`、`/resources/skills/:catalog_id`、`/resources/skills/:catalog_id/archive` 全部注册存在 |
| 上述 4 个资源路由 + `/integration/v1/knowledge/:id` + `/session` 未认证 | 全部 401，信封 `{error:{code:SESSION_REVOKED,message:"missing bearer token",request_id}}`（fail-closed） |
| `GET /integration/v1/admin/sync/runs`（HS256 admin JWT） | 200（admin 回归通过） |
| `GET /integration/v1/admin/skills/publish-jobs/<uuid>`（admin JWT，旧路由） | **404**（VAL-216 platform 侧通过；v1 session 组旧 `/skills` 路由因匿名一律 401 无法匿名区分，以构建代码删除 + 本 admin 组 404 为证） |
| `POST /oidc/token`（假凭据 + grant_type） | 401 invalid_client（回归） |
| `GET /wecom/login?redirect_uri=<knowledge oidc callback>` | 302（白名单回归） |
| gateway 日志 `22P02` 自重建后 | 0 次（audit jsonb 修复保持） |

## 5. 偏差与 N/A

- **SQLite 000047**: N/A — 测试环境 `DB_DRIVER=postgres` 且 `DB_PATH` 未设置，SQLite 迁移器不启动；等价语义已由 PG 000133 覆盖（同为 drop skill_release）。
- **匿名 401 信封**: 未认证请求由全局认证中间件先行拦截，返回简洁 401（非 INT-105 信封）；INT-105 信封在认证后由归档处理器生成（INVALID_REQUEST / RESOURCE_NOT_ACCESSIBLE / ARCHIVE_NOT_READY / SKILL_VERSION_CHANGED / SKILL_HASH_MISMATCH 均按规格形状实测出现）。与 OPC-INTEGRATION-001 已知问题 #2 同一行为。
- **platform 资源路由路径**: 需求文档/任务消息写作 `/resources/knowledge_bases`，实际实现为 `/resources/knowledge-bases`（连字符），以代码为准。
- **platform 服务器构建树过期**: `/data/build/platform-opc` 源码滞后于在跑二进制（缺 12e4fab 等 6 个提交），本次改为全量 worktree 传输构建（tar 316KB，sha256 `93e11d0f…a6b620`）；diff 校验确认与已部署源码差异仅为本次 24 个变更路径。
- **个人桥身份**: 测试环境无真实企业微信会话，platform 资源路由未能做带 entitlement 的正向 200 / VAL-205/207 的 403 SCOPE_MISMATCH 实测；已验证路由注册存在 + 未认证 401 语义正确。留待联调客户端或用户验收。
- **网络**: 部署期间直连 SSH 两次瞬断（与 2026-10-22 上午同类自愈事件），全程改用 Cloudflare tunnel（127.0.0.1:2222）完成，无服务器侧影响。
- 未触碰: `/data/WeKnora`、`/data/weknora-tokendance` 原检出（仅读取/备份/按惯例换二进制+打镜像）、opc-code/opc-app/opc-agent/opc-frontend/cloudflared/openbao/WeKnora-mcp/docreader/paddleocr/langfuse 等容器均未改动。

## 6. 回滚路径（如需）

1. knowledge: `/data/OPC-2.0/.env` 恢复 `WEKNORA_VERSION=opc-int-001`（备份 `.env.bak-wr001-20261023-090958`）→ `docker compose up -d --no-deps app frontend`；如需恢复 skill_release 数据则以 `wr001-weknora-*.sql` 全库/单表 dump 还原（down 迁移仅重建空表，数据恢复必须走备份，演练已通过）。
2. platform: `cp tokendance-gateway.bak-wr001-20261023-091216 tokendance-gateway`（rm+cp 保持 inode 语义）→ 重打 `:latest` 镜像 → `compose up -d --force-recreate`；statedb 用 `wr001-statedb-*.sql` 还原（同演练）。

## 7. 证据文件索引（服务器 `/data/backup/wr001/`）

`pre-migration-{weknora,statedb}-evidence.txt`、`post-migration-{weknora,statedb}-evidence.txt`、`backup-sha256.txt`、`rehearse-{weknora,state}.log`、`gateway-old-binary-sha256.txt`、4 份 pg_dump（见第 1 节表）。

## 8. 联调交叉引用（2026-10-23 追加）

work 客户端已对接本环境完成 TASK-025 核心业务闭环与 VAL-215 真实撤权计时（t1−t0≈0.31s ≤ 60s），全程三端零 release/publish 调用。证据（含网络接入、真实令牌登录说明、逐步命令与日志摘录、未决项）：[integration-evidence-wr001.md](integration-evidence-wr001.md)。

## 补充（2026-10-23）：平台网关公网入口事实

- platform gateway 经华为 WAF 挂在 `https://shtokendance.benlai.com` **根路径**（与 knowledge 网页 SPA、`/api` 同域不同路由）：`/integration/v1/*` 实测为新版本（resources 路由存在，未认证 401 SESSION_REVOKED，INT-105 信封含嵌套 request_id）。
- 直连 `10.101.102.116:3002` TCP 通但 HTTP 空回复（服务器 ingress 问题，与 WAF 无关）；直连 SSH 间歇性断连，运维通道走 Cloudflare 隧道。
- work 客户端机器级配置：`opc_platform_base = "https://shtokendance.benlai.com"`（`~/.config/coworker/config.toml`）；联调期临时 SSH 转发（127.0.0.1:13002）已停用。
