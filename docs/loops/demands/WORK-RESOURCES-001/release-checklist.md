# 上线执行清单：WORK-RESOURCES-001 work-opc 知识库与技能轻量入口

- demandId: `WORK-RESOURCES-001`
- 清单状态: `ready`
- 目标环境: Tokendance 测试环境（10.101.102.116，ecs-user SSH，参考 tokendance-test-ssh 技能）；生产环境另行授权
- 执行负责人: Roger0808（yj198788@hotmail.com）
- 计划窗口: 取得部署授权后执行，日期在实施收口时约定

> `ready` 表示内容已经补全，不表示已经上线。实际执行后再把对应的 `[ ]` 改成 `[x]`。发布顺序 knowledge → platform → work；旧写入停用时点受规格 BND-011 约束（先停旧写，再清表）。

## 发布项目与顺序

- [ ] 1. `knowledge-opc` — 环境：测试环境（10.101.102.116）；发布物/版本来源：分支 `feature/261022_WORK-RESOURCES-001_resource-entry`（基线 071fd96c）构建的 WeKnora 服务镜像/二进制 + frontend 构建产物；前置：停旧技能 release 写入（旧发布链路已随本版删除，确认无外部调用方后再继续）；动作：部署服务、执行 SQL 见下、前端发布；验证：`GET /api/v1/skills/catalog/:id/archive` Viewer 下载成功且 X-SHA256 与实际字节一致；旧 release 路由全部 404（VAL-216）；`make docs` 重新生成 swagger。
- [ ] 2. `platform-opc` — 环境：测试环境（10.101.102.116）；发布物/版本来源：分支 `feature/261022_WORK-RESOURCES-001_resource-entry`（基线 27de3c77）构建的 weknora-tokendance 二进制；前置：knowledge 第 1 步已上线且新归档路由可用；动作：部署、执行 SQL 见下；验证：`/integration/v1/resources/knowledge_bases` 与 `skill_catalogs` 列表/单项/归档代理正常，无 release/publish 路由（VAL-216），scope 知识读取 403 SCOPE_MISMATCH 不泄露正文（VAL-205/207）。
- [ ] 3. `work-opc` — 环境：测试环境 Mac 测试机；发布物/版本来源：分支 `feature/261022_WORK-RESOURCES-001_resource-entry`（基线 7be2e4ce）经 `packaging/build_macos_cli.sh` 与 DMG 流程构建；前置：platform 第 2 步已上线；动作：安装 DMG、登录企业账号；验证：知识库列表/搜索/刷新、技能浏览→下载安装→选用发送闭环、上传仅打开 https://shtokendance.benlai.com、真实 60 秒撤权计时（VAL-215）。

## SQL 脚本

- [ ] `knowledge 主库(PostgreSQL)`@测试环境 — 脚本：`migrations/versioned/000133*`（drop skill_release 及 4 个专属索引，down 重建结构）；顺序：第 1 步部署时随迁移执行；条件：先用 `internal/database/skill_release_cleanup.go` 备份工具导出 skill_release 行+schema+archive_ref 清单并校验 SHA-256；校验：迁移后共享表（catalog/tenant_skills/MCP/身份）行数与 hash 不变，恢复演练通过（VAL-217）；回滚：执行 down 并从备份工具恢复。
- [ ] `knowledge SQLite`@测试环境 — 脚本：`migrations/sqlite/000047*`；顺序：第 1 步；条件：同上万无一失备份；校验：SQLite 000046 的 MCP 表前后不变；回滚：down + 备份恢复。
- [ ] `platform statedb(PostgreSQL)`@测试环境 — 脚本：`migrations/000017_drop_skill_publish_jobs.{up,down}.sql`（仅 drop statedb.skill_publish_jobs）；顺序：第 2 步；条件：先用 `pkg/storage/publish_jobs_backup.go` 备份旧任务行并校验；校验：session/entitlement/audit 等共享表不变，恢复演练通过；回滚：down + 备份恢复（事务幂等插回）。
- [x] N/A — 原因：`work-opc` 本需求无数据库变更（客户端本地状态文件不属于 SQL 范畴）。

## 项目配置

- [ ] `knowledge-opc`@测试环境 — 位置/键：服务路由配置无新增外部依赖，归档路由内建于 router；动作：无配置变更；生效：随部署；验证：归档路由 smoke；回滚：回退部署镜像。
- [ ] `platform-opc`@测试环境 — 位置/键：`config.example.yaml` 同级实际配置中 knowledge upstream 地址保持现有指向；动作：无配置变更；生效：随部署；验证：upstream 健康；回滚：回退二进制。
- [ ] `work-opc`@测试机 — 位置/键：sidecar 与 GUI 构建版本对齐 platform/knowledge 本次发布版本；动作：用锁定分支构建 DMG；生效：安装后启动；验证：关于页/日志中的版本与来源 commit 一致；回滚：重装旧版 DMG，旧技能目录保留作应用回退。

## FilerUI 配置

- [x] N/A — 原因：三仓均未使用 FilerUI 配置中心，本需求不涉及。

## 发布后验证与回滚

- [ ] 冒烟与业务核对：知识库列表/搜索/刷新；技能目录浏览→归档下载→原子安装→选用发送；@ 知识单选仅当条消息生效；跨空间同名资源区分；引用只来自实际命中；上传仅打开 shtokendance.benlai.com 首页。
- [ ] 监控观察：三服务错误率与上游 4xx/5xx 分布观察 30 分钟；platform 下载预算（120s 总/15s 无进度）无异常触发；work sidecar 操作轮询无超时堆积。
- [ ] 回滚触发条件：任一仓 smoke 失败、迁移校验失败或 VAL-215 撤权实测失败；回滚顺序：work 回退 DMG → platform 回退二进制并执行 000017 down + 备份恢复 → knowledge 回退镜像并执行 000133/000047 down + 备份恢复；负责人：Roger0808。
