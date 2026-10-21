# 上线执行清单：OPC-INTEGRATION-001 OPC 全量集成与双客户端统一接入

- demandId: `OPC-INTEGRATION-001`
- 清单状态: `ready`（2026-10-21 按最终代码复核；执行动作均未勾选）
- 目标环境: 既有测试服务器（knowledge 3001/3003、platform 3002 所在 compose 网络；公网入口按 ADR-012 在安全基线用例通过后开放）
- 执行负责人: Roger0808
- 计划窗口: not-scheduled

> `ready` 表示内容已经补全，不表示已经上线。实际执行后再把对应的 `[ ]` 改成 `[x]`。

## 发布项目与顺序

- [ ] 1. `备份与副本验证` — 环境：测试服务器；发布物：knowledge/code/platform 数据库快照；前置：无；动作：备份并在隔离副本恢复；验证：副本可查询、行数对账一致
- [ ] 2. `knowledge-opc` — 环境：测试服务器；发布物：标准分支构建的版本化镜像（记录源码 SHA 与镜像 digest）；前置：步骤 1；动作：运行迁移 000129~000131（EXT-001/002）后部署，功能开关默认关闭；验证：`/health` 200、迁移版本正确、既有 RBAC/技能/MCP 契约测试通过
- [ ] 3. `platform-opc` — 环境：测试服务器；发布物：标准分支构建的版本化镜像（替代裸挂二进制）；前置：步骤 2；动作：建 platform 自有库（statedb）并迁移 000001~000015，注入密钥（见项目配置节），部署安全基线与新接口组，企业功能默认关闭；清理 WeKnora 主库可能遗留的 P01 期 audit_events 孤表（人工确认后）；验证：管理面负向用例、`/health` 三档报告、登录链路对假/真实账户中心联调
- [ ] 4. `code-opc` — 环境：测试服务器 + 试点 Mac；发布物：后端版本化镜像与桌面构建；前置：步骤 3；动作：应用迁移 585~589，部署后端与 Daemon/桌面新版本（desktop.json 配 platformUrl）；验证：企业任务 failed-closed 用例、旧 Daemon 不领企业任务、JWT/PAT/WS ent_ver 校验
- [ ] 5. `work-opc` — 环境：试点 Mac；发布物：Tauri 桌面构建（需 Rust 工具链环境更新 Cargo.lock 并验证 keyring）；前置：步骤 3；动作：配置 OPC_PLATFORM_BASE，安装并验证 Keychain 适配与回环登录；验证：登录/刷新/退出全链路、凭据扫描零命中
- [ ] 6. `单部门灰度` — 环境：测试服务器 + 试点部门 `技术交付中心`；发布物：配置开关；前置：步骤 1–5；动作：仅对试点空间开启企业功能（登录、知识、技能、精确 MCP），knowledge 侧配置 `WEKNORA_INTEGRATION_CONTEXT_AUDIENCE=platform-integration` 与 platform 配对；验证：验证 A/B 与精确 MCP 实测（VAL-101~111 相关项，账号 `杨晶`，MCP 端点 `技术部离职文档`）
- [ ] 7. `扩大试点` — 环境：同上；前置：灰度观察期满且无阻断问题；动作：逐空间扩大；验证：撤权与同步抽查

## SQL 脚本

- [ ] `knowledge 库@测试服务器` — 脚本：knowledge-opc `migrations/versioned/000129_skill_release.*`、`000130_mcp_invocations.*`、`000131_mcp_invocation_decisions.*`（golang-migrate 顺序执行）；顺序：2 之前；条件：备份完成；校验：迁移版本表递增、表结构与 DATA-120a/130 一致；回滚：对应 `.down.sql`，保留审计数据
- [ ] `platform 自有库@测试服务器` — 脚本：platform-opc `migrations/000001~000015`（audit_events/identity_links/auth_transactions/sessions/runtime_grants/workspace_links/managed_entitlements/sync_runs/sync_steps/skill_publish_jobs/authz_epochs/三列增补）；顺序：3 之前；条件：库与账号已建（不与 WeKnora 同账号）；校验：唯一约束/索引存在；回滚：down 迁移，首次回退不 DROP 新表（DATA-011）
- [ ] `code 库@测试服务器` — 脚本：code-opc `server/migrations/585_enterprise_user_identity.*`、`586_enterprise_workspace_link.*`、`587_..._unique.*`（单语句 CONCURRENTLY）、`588_enterprise_pat_subject.*`、`589_enterprise_workspace_link_tenant.*`；顺序：4 之前；条件：备份完成；校验：`make sqlc` 生成物一致、迁移记录落 `schema_migrations`；回滚：对应 down 迁移

## 项目配置

- [ ] `platform-opc@测试服务器` — 位置/键：环境变量注入——`PLATFORM_ADMIN_KEY`（管理面）、`PLATFORM_AUTH_KEY`（认证面）、`PLATFORM_BRIDGE_KEY`（兼容桥，与 weknora.jwt_secret 同源但独立注入）、`STATE_DB_*`（自有库）、企微/账户中心既有项；动作：全部从仓库外受控来源注入，YAML 模板不含真实值；生效：重启；验证：无明文密钥落盘、企微 TLS 校验开启（`wecom.insecure_skip_verify` 默认 false）；回滚：恢复原配置并重启
- [ ] `knowledge-opc@测试服务器` — 位置/键：`WEKNORA_INTEGRATION_CONTEXT_AUDIENCE=platform-integration`（与 platform 调用上下文 aud 配对）、`WEKNORA_INTEGRATION_CONTEXT_KEY`/`WEKNORA_INTEGRATION_ARGS_KEY`（受控注入）；动作：灰度前 EXT 功能开关保持关闭；生效：重启；验证：关闭态新路由不可达；回滚：关回开关
- [ ] `code-opc@测试服务器` — 位置/键：`PLATFORM_BASE_URL`、`PLATFORM_SERVICE_TOKEN`、`PLATFORM_LEASE_HMAC_SECRET`、`MULTICA_ENTERPRISE_CLAUDE_PERMISSION_MODE`/`MULTICA_ENTERPRISE_CODEX_APPROVAL_POLICY`（可选覆盖）；动作：企业功能开关灰度前保持关闭；生效：重启；验证：关闭态企业任务不可领取；回滚：关回开关
- [ ] `work-opc 桌面@试点 Mac` — 位置/键：`OPC_PLATFORM_BASE`（sidecar env）；动作：未配置则企业入口不显示（fail-closed）；生效：重启 sidecar；验证：企业登录入口出现且登录可达；回滚：清空配置
- [ ] `code 桌面@试点 Mac` — 位置/键：desktop.json `platformUrl`（http/https 校验）；动作：未配置不显示企业登录按钮；生效：重启应用；验证：回环登录全链路；回滚：移除字段

## FilerUI 配置

- [x] N/A — 原因：本需求无 FilerUI 使用证据，配置来源为各仓环境变量与 compose 配置（ADR-012）。

## 发布后验证与回滚

- [ ] 冒烟与业务核对：三服务 `/health`、契约测试集（TASK-X01）、试点账号 `杨晶` 登录/检索/技能/精确 MCP 抽查（端点 `技术部离职文档`）
- [ ] 监控观察：授权拒绝率、同步运行记录、MCP 调用 unknown_outcome 计数、60s 撤权计时抽样，灰度期每日核对
- [ ] 回滚触发条件：兼容契约失败、撤权预算不达标、数据迁移校验失败；回滚顺序：客户端 → code → platform → knowledge 扩展（保留新表支持向前修复，不重新启用已撤销凭据，DATA-011）；负责人：Roger0808
