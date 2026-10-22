---
project: work-opc
demandId: OPC-INTEGRATION-001
loop: 1
document: specification
status: archived
version: 1.0
updatedAt: 2026-10-20
---

# 规格：OPC 全量集成与双客户端统一接入

依据：[已确认 Requirements v1.3](requirements.md)、[已确认 Architecture v1.0](architecture.md)、[长期架构基线](../../../architecture/overview.md)、[参与仓清单](../repositories.json)。用户在架构批准后的独立接手指令中授权进入 Specification（audit `decision` b49f0268）。

本规格把已确认架构中的"下一阶段固定"项落成可验证条款：接口路径与字段、TTL 与预算、错误语义、状态机、权限矩阵与验收场景。本规格不改变任何已确认 REQ/ADR/BND/DATA/INT 条款的语义；如实施中事实要求变更，先改相应文档并重新确认。

## 0. 编号与阅读约定

- 本规格新增条款沿用既有编号族，`1xx` 号段归 Specification 所有：`INT-1xx` 接口契约、`DATA-1xx` 实体字段、`BND-1xx` 行为条款、`VAL-1xx` 验收场景、`OPEN-1xx` 待决项。架构阶段 `0xx` 编号不变。
- 每条强制行为一个稳定编号，供 Tasks、代码变更和测试引用。条款内写适用边界、强制/禁止动作、例外与停止条件。
- 源码证据写作 `仓库ID:相对路径:行号`；标注"候选"的内容是实现建议，标注"前置"的内容未满足时对应能力停止交付而不是降级。
- 文中所有 TTL/预算数值为本规格固定值；标 [建议值] 的性能类数值见第 11 节，需用户确认后才作为验收阈值。
- 实体均为 API/存储对象，本轮不新增管理页面（NG-007）；权限矩阵中"页面归属"五列对全部实体逐列为否，审批交互在双端既有可信 UI（work Inbox / code 审批面）内完成。

## 1. 范围锚点

覆盖 REQ-001～007、REQ-009～012 及 ACC-001～012（REQ-008/SCN-007 已移出本轮，见 [backlog](../../../backlog.md)）。四项工程扩展 testing/pbt/security/resiliency 启用，本阶段不产生 PASS 结论。

---

## 2. 身份与会话（REQ-001，ADR-003/004）

### INT-101 platform 客户端认证接口组

适用边界：work 桌面、code 桌面/Daemon 经系统浏览器的企业登录；不覆盖既有个人登录与 knowledge 原生 UI 登录。

| 端点 | 方法与路径 | 语义 |
| --- | --- | --- |
| INT-101a | `POST /integration/v1/auth/authorize` | 客户端提交 `client_id`（预登记：`work-desktop`/`code-desktop`）、`redirect_uri`（仅允许 `http://127.0.0.1:<port>/callback` 回环）、`state`、`code_challenge`（S256）、`device` 指纹。返回企微扫码/静默授权跳转 URL 与 `login_txn_id` |
| INT-101b | `GET /integration/v1/auth/callback` | 企微回调落点（平台侧）。校验 `state`↔`login_txn`、企微 code 换 UserID、账户中心主体解析、供给关系检查；成功签发一次性授权码并经 `redirect_uri` 回跳 |
| INT-101c | `POST /integration/v1/auth/token` | 授权码 + `code_verifier` + `device` 兑换。原子消费；work 受众返回 platform 会话；code 受众先返回绑定 `aud=code-backend` 的一次性兑换材料 |
| INT-101d | `POST /integration/v1/auth/refresh` | refresh 轮换：作废旧 refresh、签发新 access+refresh；检测到已作废 refresh 复用即撤销整个会话族 |
| INT-101e | `POST /integration/v1/auth/logout` | 撤销会话族并使关联 access 失效 |
| INT-101f | `GET /integration/v1/session` | 当前会话的主体、空间投影、授权版本与能力集合；客户端每次启动与切空间时调用 |
| INT-101g | `POST /integration/v1/auth/code-exchange` | code 后端专用：凭一次性兑换材料 + code 服务身份换取主体映射结果（不返回 platform access token） |

强制：`state`、`code_challenge`、回调端口与 `device` 在 `auth_transactions` 绑定存储；授权码与兑换材料一次性，重放返回固定错误。禁止：把长期 token 放入回调 URL（现有 `/#oidc_result=` 做法不得复用于新流程，`platform-opc:pkg/handler/handler.go:264-286`）；复用占位 `/oidc/*`（`handler.go:316-337` 为假实现）；email/username 自动联号（现有 `storage.go:65-66` 的 `email OR username` 查重不得用于身份关联）。

### INT-102 服务间身份接口组

| 端点 | 语义 |
| --- | --- |
| INT-102a `POST /integration/v1/internal/grants` | code 后端为已认证企业任务申请运行租约：绑定 `subject + tenant + workspace + task + runtime/device + originating_user`，返回短时租约 token |
| INT-102b `GET /integration/v1/internal/authz` | code 后端/Daemon 查询主体-空间授权版本与撤权状态（支持批量 subject 版本号） |
| INT-102c `POST /integration/v1/internal/revoke` | 同步服务/管理操作写入撤权：持久化身份/授权版本拒绝项，再异步通知 |

强制：服务调用使用独立服务身份 + 受众（`aud` 白名单），服务身份不能声明任意员工主体；`originating_user` 必须由 code 已认证请求及持久化任务来源（`code-opc:agent_task_queue.initiator_user_id/originator_user_id`）证明，无明确发起人的企业任务拒绝派发。

### DATA-101 platform 实体字段

所属模块均为 platform-opc 自有状态库（独立库/账号，不写入 knowledge 业务表）。页面归属五列逐列为否（无页面）；上游：账户中心/企微；下游：两端客户端、code 后端、knowledge 兼容桥。

| 实体 | 关键字段（类型与约束） |
| --- | --- |
| DATA-101a `identity_links` | `id uuid PK`；`identity_source text`（如 `wecom`）；`organization_id text`；`immutable_subject text`（账户中心 `SysUser.ID`，`platform-opc:pkg/accountcenter/client.go:45`）；`knowledge_user_id varchar(36)`；`code_user_id uuid NULL`；`status ∈ active/conflicted/disabled`；`created_at/updated_at`。唯一约束 `(identity_source, organization_id, immutable_subject)`；`immutable_subject` 永不变更，改邮箱不新建行 |
| DATA-101b `auth_transactions` | `login_txn_id uuid PK`；`client_id`、`redirect_uri`、`state_hash`、`code_challenge`、`device_fingerprint`；`code_hash NULL`；`audience`；`expires_at`；`consumed_at NULL`；`status ∈ pending/completed/failed/expired` |
| DATA-101c `sessions` | `session_id uuid PK`；`identity_link_id FK`；`family_id uuid`（轮换族）；`refresh_verifier_hash`（只存哈希）；`device_fingerprint`；`authz_version bigint`；`expires_at`；`revoked_at/revoke_reason`；`last_seen_at` |
| DATA-101d `runtime_grants` | `grant_id uuid PK`；`session_id NULL`（work 绑定会话）；`task_id/workspace_id/runtime_id NULL`（code 绑定任务）；`subject`、`tenant_id`、`originating_user_id`；`capabilities jsonb`；`authz_version`；`lease_expires_at`；`revoked_at` |
| DATA-101e `workspace_links` | `identity_link_id`；`code_workspace_id uuid`；`knowledge_tenant_id`；`source ∈ sync/manual`；唯一约束 `(identity_link_id, code_workspace_id)` |

### BND-101 登录与兑换行为

适用边界：INT-101 全部端点。强制：授权码 TTL 120 秒；一次性兑换材料 TTL 120 秒；兑换原子消费（`UPDATE ... WHERE consumed_at IS NULL`）；回调端口仅接受 `127.0.0.1` 且路径预登记；企微 UserID 为空（外部人员）拒绝。禁止：重复使用 state/授权码；跨受众复用兑换材料；登录触发组织全量同步（现有缓存补偿触发 SyncAll 的路径 `pkg/syncer/syncer.go:236-238` 必须移除出登录链路）。例外与停止：账户中心不可达时登录失败关闭，返回 `ACCOUNT_CENTER_UNAVAILABLE`，不降权为企微字段直连开户（现有降级兜底 `handler.go:185-190` 不得用于企业会话）。

### BND-102 会话 TTL、轮换与复用检测

| 凭据 | TTL | 轮换/检测 |
| --- | --- | --- |
| 授权码 / code 兑换材料 | 120s，一次性 | 重放即 `AUTH_CODE_REPLAYED`，并撤销该 `login_txn` |
| platform access token | 15min | 无轮换；过期走 refresh |
| platform refresh token | 14d | 每次刷新轮换；旧值复用 → 撤销 `family_id` 全族并审计 `REFRESH_REUSE_DETECTED` |
| knowledge 上游个人 token（兼容桥） | ≤120s | 仅服务端持有，按调用窗口重签；不落入客户端、日志或 auth_tokens 之外的存储；桥按 INT-105 逐次校验平台授权状态后才签发 |
| code 自有 JWT | 维持现有 `AUTH_TOKEN_TTL`（默认 30d，滑动续期），但企业会话必须带 `ent_sid`（企业会话 ID）+ `authz_version`，每次企业资源调用校验 | 版本不符即 401 `ENTITLEMENT_REVOKED`；滑动续期不得绕过版本校验 |
| 任务租约（runtime_grants） | 30s | Daemon 每 ≤15s 续租；续租失败即停止新云调用；任务终态即回收 |

例外：非企业个人功能的 code JWT/PAT 行为不变。停止条件：refresh 复用检测失效（存储不可用）时拒绝刷新而不是放行。

### BND-103 身份冲突与未供给处置

强制：登录解析到 `(identity_source, organization_id, immutable_subject)` 后查 `identity_links`：无映射且无冲突 → 引导管理员手动同步供给（REQ-006），不发企业权限；检测到同一主体映射到多个 knowledge/code 用户，或存量记录无法判定来源 → `status=conflicted`，拒绝企业资源并进入管理员处理清单。禁止：按 email/姓名自动合并或自动绑定存量账号；用 `UpsertUser` 的 `email OR username` 匹配结果充当身份关联。例外：管理员在受保护接口显式确认绑定后解除 conflicted，操作写 `audit_events`。

### 登录事务状态机（auth_transactions）

```text
pending --企微回调成功+主体解析--> completed（签发授权码，等待兑换）
pending --回调超时(10min)/企微失败/主体未供给/冲突--> failed（终态，固定错误码）
pending --TTL 过期--> expired（终态）
completed --兑换原子消费--> [sessions 建立]；重复兑换 --> failed + AUTH_CODE_REPLAYED
```

守卫：`completed` 只允许一次消费；任何终态不可逆；并发兑换只放行一个。

### 权限矩阵（登录事务与会话，逐状态）

| 对象/状态 | 可操作项 | 可见字段 | 只读字段 |
| --- | --- | --- | --- |
| auth_transaction pending | 客户端等待回调；无 API 操作 | 无（客户端仅持有 state/verifier） | state_hash、code_challenge、device_fingerprint |
| auth_transaction completed | 一次性兑换（INT-101c） | 兑换响应中的会话凭据 | code_hash、audience |
| auth_transaction failed/expired | 重新发起 INT-101a | 固定错误码 | 全部内部字段 |
| session active | refresh（INT-101d）、logout、查询 INT-101f | 主体、空间投影、授权版本 | refresh_verifier_hash、family_id |
| session revoked | 无（重新登录） | 撤销结果 | revoke_reason |

---

## 3. 撤权（REQ-003，ADR-005）

### BND-110 60 秒预算分配

起算点：platform 持久化撤权/授权版本变更成功（INT-102c 提交事务）。预算分配（累计 ≤55s，留 5s 余量）：

| 段 | 时限 | 机制 |
| --- | --- | --- |
| 网关拒绝新调用与租约续期 | T+≤5s | 撤权先持久化后通知；`authz_version` 提升立即对 platform 侧校验生效（无缓存或 ≤5s 失效） |
| knowledge 兼容桥 | T+≤10s | 桥逐调用校验平台授权状态，校验失败即不再签发上游 token；已签发未过期上游 token TTL ≤120s 且 knowledge `ValidateToken` 每次查 `auth_tokens` 行（`knowledge-opc:internal/application/service/user.go:1285-1345`），撤权时该行同步撤销 |
| code 后端（JWT/PAT/WS/HTTP） | T+≤35s | 企业 `authz_version` 缓存 ≤30s（Redis）；撤权事件主动失效缓存；`ent_sid` 会话映射删除 |
| 任务租约/Daemon | T+≤45s | 租约 30s 到期不续即失效；撤权事件推送 Daemon 立刻中止等待中的企业调用 |
| 在途写操作 | 执行点 | 敏感写执行前在线校验当前授权版本；授权服务不可达 → 写失败关闭 |

强制：派发、续租、每次云调用都读到撤权结果；旧 WS 连接在撤权后不得继续发起企业操作（服务端按连接维护 `authz_version`，事件到达即关闭企业频道）。禁止：只注销 UI；凭 PAT 缓存（现有 10min，`code-opc:server/internal/middleware/auth.go:201-249`）放行企业请求——企业会话绑定 PAT 必须纳入 `authz_version` 校验路径或排除出企业入口。例外与停止：账户中心变更到管理员手动触发之间无自动检测 SLA（REQ-003 已明确）；通知推送失败不影响拒绝生效。

### BND-111 撤权覆盖清单（验收必须逐项通过）

platform 会话族、code JWT（含滑动续期）、code PAT、code task token、WS（浏览器 `/ws` 与 daemon `/api/daemon/ws`）、Daemon 已领取任务的后续云调用、knowledge 兼容桥上游 token、历史共享 endpoint token（`/mcp/:endpoint_id` 机器主体）。历史共享凭据：先盘点受众清单，再按发布清单撤销或轮换；回滚不得重新启用已撤销凭据。

### BND-112 撤权状态语义

`managed_entitlements` 记录 `(subject, tenant, capability, source, authz_version, status ∈ active/revoked/suspended)`；全局停用/离职使所有来源授权失效（含手工来源的访问能力，但保留手工来源记录）；组织来源撤权只回收 `source=sync` 的行。撤权不承诺抹除已返回到本机的信息，不强制终止与云无关的本地工作。

---

## 4. 空间隔离与知识访问（REQ-002/004，ADR-006）

### INT-120 platform 知识适配接口组

| 端点 | 语义 |
| --- | --- |
| INT-120a `POST /integration/v1/knowledge/search` | 当前会话主体 + 指定空间内检索；服务端从会话解析主体、校验空间授权，再以兼容桥个人身份调 knowledge `/:id/hybrid-search`（Viewer+KBAccessRead，`knowledge-opc:internal/router/routes_knowledge.go:252-253`） |
| INT-120b `GET /integration/v1/knowledge/{knowledge_id}` | 片段/内容读取（Viewer 预览级） |
| INT-120c `GET /integration/v1/knowledge/{knowledge_id}/file` | 原文件下载，要求 knowledge 侧 Contributor+ + KBAccessWrite（`routes_knowledge.go:138`）；Viewer 请求返回 403 `SPACE_FORBIDDEN` |

强制：响应规范化 `knowledge_id/chunk_id/title/locator/source_tenant`；所有资源 ID 服务端重授权，客户端传入的 tenant/KB/document ID 仅作请求参数、不作授权依据；引用跳转与下载各自独立授权。禁止：用 Admin 预览/全局 key 代理员工请求；把预览权升级为下载权；共享 `/mcp/:endpoint_id` 作为新员工入口。例外与停止：兼容桥不可用时返回 `UPSTREAM_UNAVAILABLE`，不降级到共享凭据。

### BND-120 客户端上下文隔离

强制：运行上下文键 = `gateway_issuer + immutable_subject + tenant_id + session_generation`；切账号/空间即终止旧企业连接、等待中的审批与租约，重建上下文；技能缓存、MCP 连接、检索会话按该键隔离。禁止：给已有 engine/连接重新贴标签复用于新上下文；个人同名技能遮蔽企业技能（命名空间 `opc/<tenant>/<publish_id>` 保留，见 BND-142）。

---

## 5. 手动组织同步（REQ-006，ADR-011）

### INT-130 同步接口组（全部要求 BND-170 管理鉴权）

| 端点 | 方法与路径 | 语义 |
| --- | --- | --- |
| INT-130a | `POST /integration/v1/admin/sync/runs` | 触发同步。Body：`scope ∈ full/department`、`department_code`（scope=department 时必填）、`idempotency_key`、`rule_version`。返回 `run_id` 与初始状态；相同 `idempotency_key` 返回原运行 |
| INT-130b | `GET /integration/v1/admin/sync/runs/{run_id}` | 查询进度、范围、快照统计、差异摘要、步骤结果与失败明细 |
| INT-130c | `GET /integration/v1/admin/sync/runs` | 分页列出运行（按时间倒序，含状态过滤） |
| INT-130d | `POST /integration/v1/admin/sync/runs/{run_id}/retry` | 仅重试失败步骤；不改变已成功步骤结果 |
| INT-130e | `GET /integration/v1/admin/identity-conflicts` | 冲突/未供给/来源不明清单（BND-103 的处置入口） |
| INT-130f | `POST /integration/v1/admin/identity-conflicts/{id}/resolve` | 管理员显式绑定/隔离处置，写审计 |

权限：系统管理员可 full；部门管理员仅本空间对应 department scope，跨空间返回 403。强制：触发与查询分离；运行使用独立受控生命周期（持久化锁/租约，不用请求 context 跑后台 goroutine，替代现有裸 goroutine 模式 `platform-opc:pkg/syncer/syncer.go:201-206`）。禁止：启动、登录、缓存未命中或 cron 触发全量/隐式同步（现有 `main.go:53-59` 启动触发、`main.go:62-80` cron、`syncer.go:236-238` 缓存补偿、`handler.go:296-305` 无鉴权触发点全部归一到本接口组或移除）；`isSyncing` 进程内布尔充当跨实例互斥（`pkg/handler/admin.go:75-110` 现状）。

### DATA-110 同步与授权账本实体

| 实体 | 关键字段 |
| --- | --- |
| DATA-110a `sync_runs` | `run_id uuid PK`；`scope`、`department_code NULL`；`idempotency_key unique`；`rule_version`；`triggered_by`（管理员主体）；`status`（见状态机）；`snapshot_digest`、`snapshot_stats jsonb`；`started_at/finished_at`；`lock_until`（持久化互斥租约） |
| DATA-110b `sync_steps` | `run_id + step_seq`；`step_type ∈ snapshot/validate/diff/provision/revoke/reconcile`；`subject_ref`；`idempotency_key`；`status ∈ pending/applied/failed/skipped`；`error`；`retryable bool` |
| DATA-110c `managed_entitlements` | 见 BND-112；另含 `source ∈ sync/manual`、`rule_version`、`granted_by`、`granted_at`、`revoked_at`；唯一约束 `(subject, tenant, capability, source)` |
| DATA-110d `audit_events` | `event_id`；`actor`（管理员/系统）；`action`；`target`；`before/after jsonb`；`request_id`；`created_at`。只追加 |

### BND-130 权威快照契约（前置条款）

适用边界：所有 full/department 同步的"抓取权威快照"阶段。强制：快照来源为统一账户中心 GraphQL。2026-10-20 已对该端点做只读内省与计数核实（用户指派）：`sysUsers(paging: CursorPaging!, filter: SysUserFilter!, sorting: …)` 成员清单查询**存在**，支持游标分页（`first/after` + `pageInfo.hasNextPage/endCursor`）；`sysUserAggregate(filter)` 提供同过滤条件 `count` 用于完整性对账；`SysUserFilter` 支持 `departmentCode/userStatus/isDeleted/unionNo` 等全字段过滤；`sysDepartments` 同样支持游标分页与 `sysDepartmentAggregate` 计数。

快照实现契约：分页循环直到 `hasNextPage=false`；抓取计数必须与同过滤 `sysUserAggregate.count` 一致，不一致即快照不完整；部门快照同理对账。现有写死 `first:2000`、不解 `pageInfo` 的实现（`pkg/accountcenter/client.go:116-161`）不满足本契约，必须重写分页。快照完成后计算 `snapshot_digest` 并持久化统计（部门数、成员数、各状态计数）。

在职判定（白名单，已核实取值域）：仅 `userStatus == 1 AND isDeleted == 0` 为在职；实测全量 17,356 用户中 `userStatus` 分布为 0=13,728 / 1=3,009 / 2=519（2 的语义未取得账户中心负责方确认，按非在职处理）；`isDeleted=1` 有 38 条。部门启用判定：`departmentStatus == 1 AND isDeleted == 0`（实测 497 个）。`SysUser.id` 为 NON_NULL 数值字符串（自增主键形态），作为不可变主体键使用并以契约测试守护；`unionNo` 可能为 null（未关联企微的记录不得按 WeCom UserID 匹配）。

停止条件（任一命中即整运行失败关闭，不产生任何差异与清权步骤）：分页中断或计数对账失败；快照为空或显著偏离上次统计（阈值：部门或成员数为 0，或较上次缩减 >50%）；单部门快照中断（full scope）。例外：scope=department 的快照只要求该部门完整。上游错误不得透传原始堆栈给调用方（该端点会把 SQL 错误栈返回客户端，网关必须包装为 BND-180 错误码）。

### BND-131 差异、供给与清权行为

强制：差异按"快照 ↔ 当前 `identity_links`/`managed_entitlements`/knowledge/code 投影"计算；新员工/新部门供给身份、空间与 code 工作区关系（正向供给，ACC-006）；调岗先算差异再增新收旧；主管降权回收组织来源的管理角色；离职/停用（`userStatus != 1` 或 `isDeleted != 0`，判定口径见 BND-130）回收组织来源授权、会话与租约，并通知 knowledge 停用/撤销 token（复用 `SetUserActive` 语义：改 `is_active` + 撤销全部 token，`knowledge-opc:internal/handler/system.go:1597-1645`）。手工来源授权保留。重复运行幂等：不重复建身份/关系、不扩大授权；同一 scope 互斥（持久化锁），并发触发返回 `SYNC_RUN_CONFLICT` 或归并到在运行。禁止：以企微缺失记录认定离职；用全库恢复覆盖同期合法操作；重复同步恢复已停用用户。例外与停止：新增授权步骤失败 → 该步骤 `failed/retryable`，运行进入 partial_failed，不报告成功；管理员可 INT-130d 重试。

### 同步运行状态机（sync_runs）

```text
queued --> snapshotting --> validating --> diffing --> applying --> reconciling --> succeeded
snapshotting/validating --快照契约失败--> failed（终态，无差异产生）
applying/reconciling --部分步骤失败--> partial_failed --INT-130d 重试成功--> succeeded
partial_failed --重试仍失败--> partial_failed（保持，可多次重试）
任意状态 --锁被抢/进程重启--> 恢复自持久化状态；lock_until 过期且心跳丢失可由新运行接管
```

守卫：进入 diffing 必须有通过校验的 `snapshot_digest`；succeeded 要求 reconcile 核对 applied==planned；partial_failed 不允许被查询接口报告为成功。

### 权限矩阵（同步对象，逐状态）

| 对象/状态 | 可操作项 | 可见字段 | 只读字段 |
| --- | --- | --- | --- |
| sync_run queued/running 各态 | 系统管理员：查询；部门管理员：仅本空间运行查询 | 状态、范围、统计、步骤进度 | triggered_by、snapshot_digest、rule_version |
| sync_run succeeded/failed/partial_failed | 查询；partial_failed 可 retry（同上权限） | 全部步骤结果与失败明细 | 已应用步骤的 before/after |
| identity_conflict（open） | 系统管理员 resolve | 主体来源证据、冲突双方 | 自动检测快照原文 |
| identity_conflict（resolved） | 查询 | 处置记录 | 同上 |

---

## 6. 企业技能分发（REQ-007，ADR-007，EXT-001）

### INT-140 knowledge 发布接口组（EXT-001，路由挂 `/api/v1/skills/` 现有 RBAC 体系）

| 端点 | 权限 | 语义 |
| --- | --- | --- |
| INT-140a `POST /api/v1/skills/catalog/{catalog_id}/publish` | 空间 Admin | 以当前 catalog 归档字节创建发布记录：服务端重算 SHA-256、固化 `release_id`、manifest（BND-140）、目标空间/角色策略、`idempotency_key`。幂等：同 key 返回原发布 |
| INT-140b `GET /api/v1/skills/releases` | Viewer+（按空间过滤） | 列出当前用户可见空间的已发布版本 |
| INT-140c `GET /api/v1/skills/releases/{release_id}` | Viewer+ 且目标空间成员 | 发布元数据（含 manifest、hash、策略） |
| INT-140d `GET /api/v1/skills/releases/{release_id}/archive` | 个人身份 + 发布策略允许的空间/角色 | 完整归档字节下载：读 knowledge 自有文件服务（`tenant_skill_files.go:194-215` 的 `fs.GetFile` 路径），响应带 `SHA-256` 头与字节长度 |
| INT-140e `POST /api/v1/skills/releases/{release_id}/delist` | 空间 Admin | 下架：停止新下载与新安装；已安装副本按 BND-141 处理 |

强制：发布时归档字节不可变（`release_id + sha256` 唯一）；catalog 更新/删除不覆盖已发布字节（现有 `pinInstallsToReplacedBundle` 保留语义对齐，`tenant_skill_catalog.go:355-357,465-531`）；无完整源归档的 catalog 条目不得发布，提示管理员重新上传。禁止：用 Admin 逐文件预览拼包（`ListSkillFiles/ReadSkillFile` 不是分发路径）；暴露 MinIO 凭据或任意存储路径；给员工发放 Admin token。

### INT-141 platform 发布协调接口

`POST /integration/v1/admin/skills/publish-jobs`（创建跨空间发布任务：源 `catalog_id+release_id`、目标空间集合、幂等键）、`GET .../publish-jobs/{job_id}`（进度/逐目标结果）。platform 只协调与记账（DATA-120b），内容与分发权限裁定在 knowledge；母空间广播沿用显式目标选择，不默认向所有沙箱安装。

### INT-142 客户端技能获取契约

`GET /integration/v1/skills`（当前主体+空间可见的企业技能列表：publish_id、版本、manifest、hash）、`GET /integration/v1/skills/{publish_id}/download`（平台校验授权后转发 INT-140d 字节流，附 hash）。客户端校验后安装，见 BND-142。

### DATA-120 技能实体

| 实体 | 所有者 | 关键字段 |
| --- | --- | --- |
| DATA-120a `skill_release` | knowledge | `release_id uuid PK`；`catalog_id`；`version`；`bundle_sha256`；`archive_ref`（不可变存储引用）；`manifest jsonb`（BND-140）；`target_tenants jsonb`；`target_roles jsonb`；`status ∈ published/delisted`；`published_by/at`、`delisted_at`；唯一约束 `(catalog_id, version)` 与 `(bundle_sha256)` 防重 |
| DATA-120b `skill_publish_jobs` | platform | `job_id`；`release_id`；`source_tenant`；`targets jsonb`；`idempotency_key unique`；`status`；逐目标结果 `steps jsonb` |
| DATA-120c 客户端受管缓存记录 | work/code 本地 | `publish_id`；`sha256`；`issuer`；`subject`；`tenant`；安装目录命名 `opc/<tenant>/<publish_id>`；`installed_at`；`last_authz_check` |

### BND-140 技能包 manifest（`opc-client.json` 随包）

强制字段：`skill_id`、`version`、`sha256`（= 包内除 `opc-client.json` 外全部文件字节的确定性 payload digest，打包方可预计算；整包 zip 字节哈希为 `bundle_sha256` 列与下载响应 `X-SHA256` 头，服务端按实际字节重算——v1.0 措辞"与整包哈希一致"在实施中修正：zip 内 manifest 无法哈希含自身的整包，无不动点）、`exec_mode ∈ local_instruction/local_sandbox/cloud_only`、`clients ⊂ {work, code}`、`allowed_tool_categories`（数组，可为空）、`dependencies`（声明式列表）、`min_client_protocol`。未声明或校验失败的包默认不进入本地执行；`cloud_only` 包不得安装为本地技能（本轮不为其补云端 Agent，REQ-008 后置）。包约束沿用 knowledge 现有校验：≤20000 文件、≤100k zip entry、单文件 ≤32MiB、总 ≤512MiB、拒绝符号链接/越界路径/控制字符（`tenant_skill_bundle.go:26-38,94-234`）。

### BND-141 不可变性与下架

强制：版本不可替换——同 `(catalog_id, version)` 二次发布且 hash 不同返回 `SKILL_VERSION_CONFLICT`；客户端按固定 `publish_id+sha256` 下载，服务端返回字节与请求 hash 不符即 `SKILL_HASH_MISMATCH`。下架：立即停止新下载（403 `SKILL_DELISTED`）与新安装；已安装副本在下一次授权刷新后停止新加载，已在本地执行的进程按原本机权限管理。例外：审计保留期内归档不物理删除；引用释放且审计期过后才允许清理。

### BND-142 客户端安装与隔离

强制：先下载到隔离临时目录 → 校验字节 hash、文件数/大小、路径/链接、manifest → 原子切换到 `opc/<tenant>/<publish_id>` 受管目录；任何一步失败不破坏已验证版本（无半安装状态）。执行前刷新分发授权；离线不启动新的企业技能。禁止：默默运行包内安装脚本或安装缺失依赖（必要本机操作走客户端既有许可流程）；按显示名缓存或授权；code 企业技能套用"请求旧 hash 返回最新包"行为（现有 `ResolveTaskSkillBundles` 不按 hash 固定，`code-opc:server/internal/handler/daemon.go:3979-4059`，企业路径必须按 hash 固定）。

### 技能发布状态机

```text
（catalog 已有归档）--INT-140a--> published --INT-140e--> delisted
published：可下载/可安装/可被 publish-job 引用
delisted：拒绝新下载与新安装；已安装副本授权刷新后停用；归档保留至审计期结束
```

守卫：publish 要求 catalog 归档存在且 hash 可重算；delisted 不可逆回 published（同版本），恢复分发需新版本发布。

### 权限矩阵（技能发布，逐状态）

| 对象/状态 | 可操作项 | 可见字段 | 只读字段 |
| --- | --- | --- | --- |
| skill_release published | Admin：delist；授权员工：查询元数据、下载归档 | manifest、版本、hash、目标策略 | archive_ref、published_by/at |
| skill_release delisted | 仅查询元数据 | 同上 + delisted_at | 同上；归档字节不可再取 |

---

## 7. 精确 MCP 调用（REQ-009，ADR-009，EXT-002）

### INT-150 knowledge 精确调用接口组（EXT-002，独立 integration 路由）

| 端点 | 方法与路径 | 语义 |
| --- | --- | --- |
| INT-150a | `POST /api/v1/integration/mcp/invocations` | 创建调用：`service_id`、`tool_name`（原始名）、`arguments`、`idempotency_key`、调用上下文（BND-150）。返回 `invocation_id` 与初始状态；同 key 返回原调用 |
| INT-150b | `GET /api/v1/integration/mcp/invocations/{id}` | 状态与结果查询：状态机当前态、结果引用、错误、审批/OAuth 待办信息；断网恢复后以此查询原请求 |
| INT-150c | `POST /api/v1/integration/mcp/invocations/{id}/cancel` | 取消：未发送上游 → 确定取消；已发送 → 状态置 `cancel_requested`，以实际回执为准，不伪称回滚 |
| INT-150d | `POST /api/v1/integration/mcp/invocations/{id}/decision` | 人工审批决定：`approve/deny`、最终参数摘要、策略版本；绑定请求单次有效 |
| INT-150e | `GET /api/v1/integration/mcp/services` | 当前主体+空间可用服务与工具目录（∩ 受管策略 ∩ 调用上下文能力） |

platform 侧对应适配 `POST /integration/v1/mcp/invoke` 等（转发并附调用上下文），客户端不直连 knowledge integration 路由。

### DATA-130 调用与审批记录（knowledge）

| 实体 | 关键字段 |
| --- | --- |
| DATA-130a `mcp_invocations` | `invocation_id uuid PK`；`tenant_id`；`subject`（个人 principal）；`actor_kind ∈ user/task`；`task_id/device NULL`；`service_id`、`tool_name`；`args_digest`（参数 SHA-256，参数本体加密存或仅存摘要+引用）；`idempotency_key unique`；`status`（状态机）；`policy_version`；`result_ref`；`error`；`created_at/sent_at/finished_at`；`expires_at`（结果保留期，候选 7 天） |
| DATA-130b `mcp_invocation_decisions` | `invocation_id FK`；`decided_by`（有权交互用户）；`decision ∈ approve/deny`；`args_digest`、`policy_version` 绑定；`decided_at`；唯一约束 `(invocation_id)` 单次有效 |

复用对齐：`agent_prompts` 的状态机/lease 语义可作为实现参照（`knowledge-opc:internal/types/agent_prompt.go:42-77`），但不把 Agent 历史回填为精确调用。

### BND-150 调用上下文与双身份一致

强制：INT-150 入口同时要求 (a) 个人上游身份（兼容桥签发的短时 token）与 (b) platform 签发的短时调用上下文（绑定 `sub/tenant/actor_kind/request_id/task_id/device/目标操作/参数摘要`，独立密钥与受众，TTL ≤120s，防重放）；两者主体一致才受理，客户端自报 header 无效。主体键约定（v1.0 实施中裁决）：`sub` = knowledge 用户 id（与上游 token `user_id` 一致，knowledge `validateContext` 强制校验），`user` = immutable_subject（平台审计追踪，v1.0 文字"user=主体"修正）。禁止：裸调底层 `CallTool` 绕过策略；端点级 `ask` 共享机器主体冒充个人执行；执行 token（task token/租约）换取审批凭据。

### BND-151 审批流（MCP 人工决定）

节点顺序：策略判定 `Ask`（工具 risk/access-level，`mcp_tool.go:183-201`）→ 双端可信 UI 展示目标服务、工具、最终参数摘要与后果 → 有权交互用户决定 → 绑定 `invocation_id + args_digest + policy_version` 落 DATA-130b → 执行前再次授权 → 精确调用。

审批人来源：发起请求的当前员工本人经会话认证提交；Daemon/task token、无人值守上下文不得提交决定（现有 Unattended 拒审批语义保留，`approval/gate.go:278-284`）。审批操作集：`approve`（本次）、`deny`。驳回：`deny` → 调用转 `failed(denied)`，不重试。撤回：决定后未执行前可 `cancel`；已批准记录不得删除，重复决定不重复执行。参数修改必须重新校验并绑定新摘要。现有"无执行元数据时 Ask 被静默跳过"的路径（`mcp_tool.go:196`）在本适配中禁止出现：缺少审批上下文的危险调用直接拒绝。

### BND-152 幂等、取消与未知结果

强制：创建幂等（`idempotency_key` 唯一）；同一请求的重复状态查询/回调不产生重复副作用；写工具超时不自动重发，状态置 `unknown_outcome` 并保留可追踪身份；进程在"已发送未记录结果"间崩溃同样记 unknown；仅当确定未发送或上游明确幂等保证时才允许安全重试（读类工具）。结果查询有有限期限（DATA-130a `expires_at`）。禁止：未知结果自动重发写操作；把外部错误包装成成功；取消伪称回滚已发生副作用。

### 调用状态机（mcp_invocations）

```text
created --> policy_checked
policy_checked --Deny--> failed(policy_denied)（终态）
policy_checked --Ask--> waiting_approval --deny--> failed(denied)（终态）
waiting_approval --approve+执行前授权通过--> executing
policy_checked --需个人 OAuth 且未授权--> waiting_oauth --授权完成--> executing
waiting_oauth --超时(候选 15min)/用户放弃--> failed(oauth_timeout)（终态）
executing --上游回执--> succeeded / failed(upstream_error)（终态）
executing --超时/连接中断--> unknown_outcome（可查询，不可自动重发写）
created/policy_checked/waiting_* --cancel--> cancelled（终态，确定未发送）
executing --cancel--> cancel_requested --> 按实际回执落 cancelled / succeeded / failed / unknown_outcome
waiting_* --调用上下文/授权过期--> failed(expired)（终态）
```

守卫：进入 executing 必须有当前授权版本校验通过 + 必要决定存在且绑定摘要一致；succeeded 必须有上游结果引用；任何终态不可逆。

### 权限矩阵（调用对象，逐状态）

| 对象/状态 | 可操作项 | 可见字段 | 只读字段 |
| --- | --- | --- | --- |
| invocation created/policy_checked | 发起者查询、cancel | 状态、目标工具、参数摘要 | 主体、上下文绑定、policy_version |
| waiting_approval/waiting_oauth | 发起者 decide/cancel；非发起者仅本人可见性按空间权限 | 同上 + 待办类型 | 决定绑定摘要 |
| executing | 查询、cancel（语义见状态机） | 状态 | args_digest、sent_at |
| succeeded/failed/cancelled/unknown_outcome | 查询（保留期内） | 结果引用/错误 | 全部执行事实 |

---

## 8. 客户端受管行为（REQ-010，ADR-010）

### BND-160 work 企业适配

强制：企业 refresh 凭据存 macOS Keychain（Tauri 主进程新增 Keychain 命令适配；SecretStore 的 `get/put/delete/status/exclusive` 接口语义不变，`work-opc:coworker/secrets.py:229-320`），企业 profile 不写入 `secrets.json`；access token 只存主进程/内存；renderer 仅获短时状态。引擎接入仅用既有注入点 `extra_tools/extra_skill_dirs/skill_filter`（`coworker/agent.py:261-295`）；`skill_filter` 用 callable 形态按当前上下文实时求值。会话持久化新增企业绑定元数据（issuer/subject/tenant/generation，DATA-120c 同源），旧会话不自动迁移归属。验证方法（可执行）：`security find-generic-password` 能检索到企业条目；`secrets.json`、会话日志、工具参数中扫描不到 refresh 值；切账号后旧上下文连接句柄全部关闭（进程内断言 + 日志审计）。

### BND-161 code 失败关闭与空配置语义

强制：企业 MCP 配置获取/解析/授权/网络失败 → 企业调用中止并呈现失败，受管空集合（`{}`）与未配置（`null`）语义不同，严禁回退继承本机全部 MCP（现状在 `code-opc:server/internal/handler/daemon.go:2548-2586` 会回退，企业路径必须切断）；未声明支持受管协议的旧 Daemon 不得领取企业任务（个人任务行为不变）。企业 refresh/PAT 不写入 daemon profile `config.json`（现状明文，`apps/desktop/src/main/daemon-manager.ts:703-760`）与 renderer `localStorage`（现状 JWT 在 `packages/core/auth/store.ts:79`）；企业凭据走 Electron `safeStorage`（macOS 即 Keychain）+ 主进程持有。禁止：daemon 注册身份单独换取员工资源权限；空任务字段解释为无限企业权限。

### BND-162 三种 CLI 受管参数矩阵（macOS 首批验收对象）

| CLI（本机实测版本） | 企业 MCP 注入 | 权限模式约束 | 禁止 |
| --- | --- | --- | --- |
| Claude Code 2.1.295 | daemon 写 task-local `--mcp-config` 文件 + `--strict-mcp-config`（现状机制 `server/pkg/agent/claude.go:57-69,1093-1097`） | 企业任务不得默认 `--permission-mode bypassPermissions`（现状硬编码 `claude.go:1078-1092`），改用受管许可并由本机审批兜底 | 用 `custom_args` 覆盖回 bypass（现有 `claudeBlockedArgs` 拦截保留） |
| Codex 0.162.0 | 每任务 `$CODEX_HOME/config.toml` 受管块（begin/end marker），剥离用户 `-c mcp_servers.*` 覆盖（现状 `codex.go:581-603`） | `thread/start` 显式传企业 `approvalPolicy`/`sandbox`，不再传 nil 取默认（现状 `codex.go:2084-2098`）；macOS sandbox 现状为 `danger-full-access`（上游 Seatbelt 限制，`execenv/codex_sandbox.go:15-34`），企业任务须显式声明并接受该事实或缩小范围 | daemon 自动 accept 一切审批请求（现状 `codex.go:2940-2997` 在企业任务中必须改为路由到有权用户或拒绝） |
| Kimi Code 2.1.1（ACP） | `session/new` 参数携带受管 MCP 数组（现状 `kimi.go:52-59`） | `session/request_permission` 在企业任务中转入有权用户本机审批；daemon 自动选择仅限 `reject_once` | 自动选 `allow_once/allow_always`（现有选择策略 `hermes.go:1538-1593` 的企业模式收紧） |

强制：长期 token 不进命令行参数与生成的 MCP 配置文件；CLI 进程只经环境变量获得任务级短时凭据（沿用 `MULTICA_TOKEN` 任务 token 模式 `server/internal/daemon/daemon.go:168-194`）。验证方法：适配器单测断言 argv/配置文件内容；真实 CLI smoke 遵守 code 仓规则（`agentintegration` tag + `MULTICA_RUN_REAL_AGENT_SMOKE=1` + 显式授权）。

### BND-163 本机桥与审批传递

强制：CLI/Daemon 与平台间只走任务限权远程连接或本地受保护桥；loopback 桥校验随机会话能力 + 任务上下文绑定 + 允许来源，不只靠 localhost（对齐 work 现有 sidecar token 模式 `coworker/server/app.py:225-279`）。code 桌面企业登录回调固定为回环 `127.0.0.1`（OPEN-104 用户已决，与本机 Codex/Claude CLI 登录模式一致）；`multica://` deep link 仅保留给既有 web 登录，企业流程不使用。云审批请求落在双端既有审批面（work Inbox `POST /v1/inbox/{id}/resolve`；code 审批 UI），审批语义服务端重校验（沿用 `manager.py:6171-6226` 的 `_grant_offered` 降级校验模式）。禁止：云审批结果自动扩大本机文件/shell 许可；无人值守任务通过云审批扩大预先允许范围。

---

## 9. 网关安全基线（REQ-005）

### BND-170 管理面与来源防护

强制：`/admin/**`、INT-130、INT-141 全部管理端点要求服务内认证（管理员会话 token）+ 权限范围检查（系统管理员全局，部门管理员限本空间）+ 来源/CSRF 防护（Origin/Referer 白名单 + 变更操作要求自定义头或同源 token）+ 审计（DATA-110d）。现状为零中间件（`platform-opc:main.go:83` 仅 Logger+Recovery），本条款是公网开放硬前置。既有 `/wecom/sync`、`POST /wecom/callback` 事件入口：验签/解密修复或移除，不得保留"不解密直接回 success"（现状 `handler.go:290-293`）。企微回调不得用回调内 CorpID 覆写运行时配置（现状 `handler.go:145-149` 移除）。

### BND-171 TLS、密钥与日志

强制：恢复企微客户端 TLS 证书校验（移除硬编码 `InsecureSkipVerify: true`，`pkg/wecom/client.go:30-32`）；账户中心出站改 HTTPS 或经受信内网策略并在清单注明（现状默认 `http://omni-router.benlai.com/graphql`，`pkg/accountcenter/client.go:20`）；秘密经环境变量/受控注入（沿用现有 env 覆盖模式 `config/config.go:76-111`），YAML 模板不含真实值；`SystemAESKey` 未配置时加密字段写入必须失败而不是明文落库（现状 `pkg/crypto/crypto.go:24-26` 静默返回明文，需改为显式错误）；日志与审计不含 token/密钥/完整凭据。禁止：本轮引入 OpenBao（REQ-005 用户决定）。停止条件：安全基线用例（ACC-005）未通过前不开放新公网入口。

---

## 10. 错误语义与可观测

### BND-180 稳定业务错误码

全部接口返回 `{"error": {"code": <稳定枚举>, "message": <人读>, "request_id": <uuid>}}`；HTTP 状态与 code 映射固定，客户端只依赖 code。初始枚举（可按需追加，不得改义）：

| code | HTTP | 语义 |
| --- | --- | --- |
| `AUTH_CODE_REPLAYED` | 400 | 授权码/兑换材料重放 |
| `AUTH_STATE_MISMATCH` | 400 | state/PKCE 校验失败 |
| `SUBJECT_NOT_PROVISIONED` | 403 | 主体未供给，提示管理员处理 |
| `SUBJECT_CONFLICT` | 409 | 身份冲突，进入管理员清单 |
| `ACCOUNT_CENTER_UNAVAILABLE` | 502 | 账户中心不可达，失败关闭 |
| `SESSION_REVOKED` / `ENTITLEMENT_REVOKED` | 401/403 | 会话撤销 / 授权版本失效 |
| `SPACE_FORBIDDEN` | 403 | 空间或资源角色不足 |
| `REFRESH_REUSE_DETECTED` | 401 | refresh 复用，会话族已撤销 |
| `LEASE_EXPIRED` | 401 | 任务租约过期或未续 |
| `UPSTREAM_UNAVAILABLE` | 502 | knowledge/上游不可达 |
| `SYNC_SNAPSHOT_INCOMPLETE` | 502 | 快照完整性验证失败 |
| `SYNC_RUN_CONFLICT` | 409 | 同 scope 已有运行或幂等键冲突处理中 |
| `SKILL_VERSION_CONFLICT` / `SKILL_HASH_MISMATCH` / `SKILL_DELISTED` | 409/422/403 | 版本替换 / 字节不符 / 已下架 |
| `MCP_POLICY_DENIED` / `MCP_APPROVAL_REQUIRED` | 403/202 | 策略拒绝 / 等待人工决定 |
| `INVOCATION_UNKNOWN_OUTCOME` | 409（查询返回 200+状态） | 结果未知，禁止自动重发 |
| `COMPAT_CONTRACT_VIOLATION` | 503 | 兼容契约检查失败，阻断相关写入 |

强制：授权决定、同步运行、外部调用以 `request_id` 关联审计；健康报告区分存活/依赖可达/契约兼容三档（REQ-011/ACC-010）。

---

## 11. 性能与容量验收值

用户已于 2026-10-20 确认下列阈值（答复"没问题"）。

| 项 | 验收值 | 依据 |
| --- | --- | --- |
| 手动全量同步（≤1000 用户、≤50 部门） | 端到端 ≤15min（p95），快照 ≤5min | 逐人解析为 N+1 调用（`syncer.go:200-207`），需加并发上限（≤10）与速率控制 |
| 单部门同步 | ≤2min | 同上 |
| 撤权全链路 | ≤60s（BND-110 固定预算） | REQ-003 |
| 网关授权检查 | p95 ≤300ms | 本地库查询 + ≤30s 缓存 |
| 知识检索代理 | p95 ≤2s（不含模型生成） | knowledge 现有检索实测为准 |
| 登录并发 | 10 并发全部成功且无串号 | 试点规模推导下限 |
| 撤权并发 | 5 主体同时撤权全部生效 | 同上 |
| 精确 MCP 调用 | 读类 p95 ≤10s（不含上游长尾）；写类不设延迟承诺，只承诺状态可查 | 上游不可控 |

1000 用户不推导为 1000 并发；验收以实际测量记录为准。

---

## 12. 验收场景映射（VAL-1xx ↔ ACC）

| 编号 | 场景（规格级） | 覆盖 ACC / SCN | 关键断言 |
| --- | --- | --- | --- |
| VAL-101 | 双端首次登录、续期、退出、停用；同名/同 email/改 email；授权码重放 | ACC-001 / SCN-001 | 同一 `immutable_subject` 关联；重放 `AUTH_CODE_REPLAYED`；凭据扫描零命中 |
| VAL-102 | 两空间两角色（含 Viewer）+ 跨空间 ID 注入 + 账号/空间切换 | ACC-002 / SCN-002/004 | 服务端拒绝越权；旧上下文连接/缓存零复用 |
| VAL-103 | 撤权计时：网关变更 → 各入口拒绝时刻记录 | ACC-003 / SCN-003 | BND-110 预算内全入口拒绝；写前校验与故障关闭注入通过 |
| VAL-104 | 知识检索/预览/下载权限分层 | ACC-004 | Viewer 预览通过、下载 403；引用定位正确 |
| VAL-105 | 管理面负向矩阵：未认证/错误角色/跨空间/CSRF/来源伪造 | ACC-005 | 全拒绝且有审计；TLS 错误证书拒绝 |
| VAL-106 | 手动同步：新员工/新部门/调岗/降权/离职/停用；重复与并发触发；分页中断/空快照/单部门失败注入 | ACC-006 / SCN-005 | 正向供给到达两端；旧受管权限回收；不误删；partial_failed 可重试可追溯 |
| VAL-107 | 技能：完整字节一致、版本不可替换、篡改/越界/链接/超限拒绝、中断原子性、同名不遮蔽、下架与撤权后行为 | ACC-007 / SCN-006 | 逐条对应 BND-140~142 |
| VAL-108 | 精确 MCP：个人 OAuth 不串号；审批归属与自审批拒绝；过期/重复审批；取消与断网查询；写超时 unknown 不重复副作用 | ACC-008 / SCN-008 | 状态机逐态证据；独立于 `ask` 的执行证据 |
| VAL-109 | 三 CLI 受管参数矩阵逐项（BND-162）+ 无配置/空配置/错误配置 | ACC-009 | 失败不继承本机 MCP；云凭据/stdio 配置不下发 |
| VAL-110 | 发布/回滚：固定源码/镜像/配置溯源；契约失败阻断写入；单部门灰度暂停/恢复；迁移与回滚在代表性副本验证 | ACC-010/011 / SCN-009 | 清单逐项证据；镜像 digest 与源码 SHA 对账 |
| VAL-111 | 验证 A/B 与精确 MCP 独立证据汇总 | ACC-012 | A/B 实际结果；C 已记 backlog；无能力关闭冒充 |

---

## 13. 测试资源与环境矩阵

已固定事实（本机采集）：macOS 27.0.1（arm64）；Claude Code 2.1.295；Codex CLI 0.162.0；Kimi Code 2.1.1。服务端运行系统不在客户端范围排除内（NFR-007）。

用户 2026-10-20 指定的验收资源：试点部门 `技术交付中心`（level 1，`departmentCode=1120600000000000000`）；个人验证账号 `杨晶`；MCP 纵向测试端点 `技术部离职文档`。

账户中心只读核实结果（同日）：`杨晶` 唯一记录 `id=104032`，`userStatus=1`（在职）、`isDeleted=0`、`unionNo=1524`（已关联企微）、工号 `1000570`，所属 `BDE平台开发部`（level 2，在职），向上追溯父部门即 `技术交付中心`——**该账号可用于登录类验收**，且按现有 level 1 建空间规则落入试点空间。此前用户提到过的 `yangjing`（杨静）在账户中心有 2 条重复记录，均 `userStatus=0`（停用）、`unionNo=null`、所属部门均已删除——不能用于登录验收，但恰好是身份冲突/停用场景的现成样本。

仍需用户补齐：技术交付中心内另 1 个 Viewer 角色账号、1 个系统管理员与 1 个部门管理员、1 个调岗测试账号（可计划从本部门调入/调出）；代表性技能包 2 个（`local_instruction` 与 `local_sandbox` 各一，含脚本与依赖声明，可基于 knowledge 仓现有技能 fixture 制作）；除指定端点外，读/写/审批类 MCP 各一的纵向服务（单元/集成层用 knowledge 现有 fake 与测试基建）。

---

## 14. 待决项（集中补问用户）

| 编号 | 状态 | 缺口与处置 |
| --- | --- | --- |
| OPEN-101 | **已解决**（2026-10-20 用户指派只读核实 GraphQL 端点） | `sysUsers` 游标分页清单与 `sysUserAggregate` 计数存在并可用，取值域实测完毕，契约已固化进 BND-130；`userStatus=2`（519 条）语义仍建议向账户中心负责方确认，但不阻塞——白名单口径已把它排除在职之外 |
| OPEN-102 | **部分解决** | 用户指定试点部门 `技术交付中心`、验证账号 `杨晶`（已核实在职且关联企微，可用）与 MCP 纵向端点 `技术部离职文档`；其余账号与技能包按第 13 节补齐 |
| OPEN-103 | **已解决** | 用户 2026-10-20 答复"没问题"，第 11 节阈值确认为验收值 |
| OPEN-104 | **已解决** | 用户 2026-10-20 答复"是的，用回环"：code 桌面企业登录回调固定回环 `127.0.0.1`（与本机 Codex CLI 0.162.0 自身登录模式一致，实证记录于 [user-decisions](../sources/user-decisions.md)）；`multica://` deep link 仅保留给既有 web 登录 |

---

## 15. 追溯映射

| 需求 | 架构 | 本规格条款 | 验收 |
| --- | --- | --- | --- |
| REQ-001 | ADR-003/004 | INT-101/102、DATA-101、BND-101~103 | VAL-101，ACC-001 |
| REQ-003 | ADR-005 | BND-110~112、DATA-101c/d | VAL-103，ACC-003 |
| REQ-002/004 | ADR-006 | INT-120、BND-120 | VAL-102/104，ACC-002/004 |
| REQ-006 | ADR-011 | INT-130、DATA-110、BND-130/131 | VAL-106，ACC-006 |
| REQ-007 | ADR-007/008 EXT-001 | INT-140~142、DATA-120、BND-140~142 | VAL-107，ACC-007 |
| REQ-009 | ADR-008/009 EXT-002 | INT-150、DATA-130、BND-150~152 | VAL-108，ACC-008 |
| REQ-010 | ADR-010 | BND-160~163 | VAL-109，ACC-001/003/009 |
| REQ-005 | ADR-012 | BND-170/171、BND-180 | VAL-105/110，ACC-005/010 |
| REQ-011/012 | ADR-012、DATA/MIG | 第 10~13 节 | VAL-110/111，ACC-010~012 |

---

## 确认记录

确认人：Roger0808 / yj198788@hotmail.com（本对话用户，已登记治理身份）。记录时间：2026-10-20。确认版本：v1.0（草稿迭代 v0.1→v0.3 补齐缺口与指定资源，OPEN-104 回环决定写入 BND-163 后按确认版记为 v1.0）。用户在收到规格全文、OPEN-101~104 处置与"回环/deep link"选择请求后回复："是的，用回环。Continue"——其中"是的，用回环"为 OPEN-104 决定，整句按上下文作为本版规格确认。

修订沿革：v0.1 初稿（50 个稳定条款，覆盖 REQ-001~012 与 ACC-001~012）；v0.2 账户中心 GraphQL 只读核实固化（OPEN-101 解决）、性能阈值确认（OPEN-103 解决）、指定资源登记（OPEN-102 部分）；v0.3 试点部门 `技术交付中心` 与验证账号 `杨晶` 核实登记；v1.0 OPEN-104 回环决定写入，全部待决项关闭或转为实施期资源清单（第 13 节）。本次确认不代表接口已实施；实施待 Tasks 确认与其后的 Continue。

v1.0 实施后修订（2026-10-21，对账差量回写，经 `impact` + `stage_revalidated` 机器流程）：① BND-140 `manifest.sha256` 语义澄清为 payload digest（zip 内 manifest 无法哈希含自身的整包，实施中数学上不可行；安全语义不弱化）；② BND-150 调用上下文主体键澄清（`sub`=knowledge 用户 id，按 knowledge 已交付校验的事实优先裁决；`user`=immutable_subject）。两处均为措辞与事实对齐，不改变任何已确认行为语义；无其它条款变动。
