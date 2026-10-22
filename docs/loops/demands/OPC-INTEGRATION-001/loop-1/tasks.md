---
project: work-opc
demandId: OPC-INTEGRATION-001
loop: 1
document: tasks
status: confirmed
version: 1.0
updatedAt: 2026-10-20
---

# Tasks：OPC 全量集成与双客户端统一接入

依据：[Requirements v1.3](requirements.md)、[Architecture v1.0](architecture.md)、[Specification v1.0](specification.md)、[参与仓清单](../repositories.json)。用户在规格确认后以"Continue"授权进入 Tasks（audit `decision` 0c859ce3）。

编号约定：`TASK-P/K/W/C/X` 分别归 platform / knowledge / work / code / 跨仓集成；每项引用 REQ/ADR/规格条款编号，含完成条件与验证方法。实施时按 sdd-solo 在主仓 `repositories/<repoId>/tasks.md` 落逐仓子任务（只列本仓任务并回引此处编号），不在参与仓另建可编辑文档。

所有仓已在标准分支 `feature/261020_OPC-INTEGRATION-001_opc-integration`，基线见 repositories.json。实施顺序即本节任务组顺序；每组内可并行，跨组按依赖先后。

## 1. platform-opc（统一接入网关）

### TASK-P01 网关安全基线

引用：REQ-005、BND-170/171、VAL-105。现状：`main.go:83` 无中间件、`pkg/wecom/client.go:30` 跳过 TLS、`handler.go:145-149` 回调覆写 CorpID、`handler.go:290-293` 事件回调不验签、`pkg/crypto/crypto.go:24` 无 key 静默明文。
完成条件：管理面（`/admin/**`、INT-130/141 组）全部挂服务内认证 + 系统/部门管理员范围检查 + Origin/CSRF 防护 + 审计落 `audit_events`；企微客户端恢复证书校验；事件回调验签解密或移除；CorpID 覆写逻辑删除；`SystemAESKey` 缺失时加密写入显式失败；上游错误不透传堆栈。
验证：`go test ./...` 含新增负向矩阵（未认证/错误角色/跨空间/伪造来源）；`git diff --check`；手工以错误证书与伪造来源各打一次记录证据。

### TASK-P02 账户中心客户端分页重写

引用：REQ-006、BND-130、OPEN-101 核实结论。现状：`pkg/accountcenter/client.go:116-161` 写死 `first:2000` 不解 `pageInfo`。
完成条件：`sysUsers`/`sysDepartments` 游标分页循环至 `hasNextPage=false`；与同过滤 `sys*Aggregate.count` 对账；失败/中断/不一致返回 `SYNC_SNAPSHOT_INCOMPLETE`；在职白名单 `userStatus==1 AND isDeleted==0`；超时与限流（并发 ≤10）；响应不泄露堆栈。
验证：单测以假 server 注入分页中断/计数不符/空快照；对真实端点只读跑一次计数对账（只读，不改数据），记录样本统计。

### TASK-P03 平台自有状态库与迁移

引用：DATA-101/110、DATA-120b、DATA-011。现状：仅 `WeComDeptTenantMapping` 走 AutoMigrate（`storage.go:33`）。
完成条件：platform 独立逻辑库/账号；显式版本化迁移建 `identity_links`、`auth_transactions`、`sessions`、`runtime_grants`、`workspace_links`、`managed_entitlements`、`sync_runs`、`sync_steps`、`audit_events`、`skill_publish_jobs`（含唯一约束/索引）；迁移可重复执行且回滚脚本存在；不再向 knowledge 库写新表。
验证：空库与含存量数据副本各跑一次 migrate/up→down→up；`go test ./pkg/storage/...`。

### TASK-P04 客户端认证接口组

引用：REQ-001、INT-101、BND-101/102/103、DATA-101a-c。现状：`/oidc/*` 为占位（`handler.go:316-337`）。
完成条件：INT-101a-g 全部实现；授权码/兑换材料 120s 一次性原子消费；PKCE S256 校验；会话族轮换与 refresh 复用撤销；未供给/冲突按 BND-103 拒绝；企微回调不再触发 SyncAll（移除 `syncer.go:236-238` 登录链路触发）。
验证：单测覆盖重放、跨受众兑换、过期、并发兑换只放行一个；集成测试走通 authorize→callback→token→refresh→logout 全流程（假企微/账户中心）。

### TASK-P05 服务间接口与运行租约

引用：INT-102、ADR-005、DATA-101d。
完成条件：INT-102a-c 实现；服务身份 + `aud` 白名单校验；租约 30s/续期 ≤15s；无明确 `originating_user` 拒绝。
验证：伪造服务身份/受众/发起人的负向用例；租约过期拒绝用例。

### TASK-P06 撤权引擎与 60 秒预算

引用：REQ-003、BND-110~112。
完成条件：INT-102c 先持久化后通知；`authz_version` 提升即时生效；knowledge 兼容桥逐调用校验；失效事件推送通道；覆盖清单（BND-111）逐项可测。
验证：故障注入（通知失败不影响拒绝）；计时测试断言预算分配（T+5s/T+35s/T+45s）；并发撤权 5 主体。

### TASK-P07 知识适配接口

引用：REQ-004、INT-120、ADR-006。
完成条件：INT-120a-c 实现；规范化响应（knowledge_id/chunk_id/title/locator/source_tenant）；服务端重授权；Viewer 下载返回 `SPACE_FORBIDDEN`；桥不可用返回 `UPSTREAM_UNAVAILABLE`。
验证：与 knowledge 的契约测试（固定 release）；Viewer/Contributor 双角色用例。

### TASK-P08 手动同步服务

引用：REQ-006、INT-130、BND-130/131、DATA-110、同步状态机。
完成条件：INT-130a-f 实现；启动/cron/缓存补偿/无鉴权 `/wecom/sync` 触发点全部归一或移除（`main.go:53-80`、`handler.go:296-305`）；持久化锁与幂等键；供给/调岗/降权/离职/停用差异与账本；partial_failed 可重试；冲突清单与 resolve。
验证：P02 假数据 + 真实端点副本各跑全量；并发/重复触发、分页中断、空快照、单部门失败注入；处理时限对照规格 §11（全量 ≤15min）。

### TASK-P09 knowledge 兼容桥隔离

引用：ADR-004、DATA-010。
完成条件：桥独立模块；只为有效映射用户签 ≤120s 上游 token；独立签名密钥；DB 账号最小权限（仅列明表/操作）；不签管理员 token；契约测试覆盖 token 持久化/停用/撤销。
验证：契约测试在 knowledge 固定 release 上跑；越权表写被拒用例。

### TASK-P10 技能发布协调

引用：REQ-007、INT-141/142、DATA-120b。
完成条件：publish-jobs 创建/查询、幂等键、逐目标结果；客户端技能列表/下载转发（授权校验后转发 INT-140d 字节）。
验证：幂等重复创建返回原任务；目标空间失败部分可见。

### TASK-P11 platform 测试与工程扩展证据

引用：VAL-001、NFR-001~005。
完成条件：`go test ./...` 全绿；PBT（现有框架 + 确定性 seed）：权限不扩大、同步幂等、授权码单次消费；安全用例集（VAL-105）；Resiliency（账户中心分页中断、DB 不可达、重启恢复）。
验证：测试报告含 PBT property/case 数/seed。

## 2. knowledge-opc（EXT-001/002，已批准限定范围）

### TASK-K01 技能发布数据与迁移

引用：EXT-001、DATA-120a、DATA-011。
完成条件：golang-migrate 新迁移建 `skill_release`（唯一约束 `(catalog_id,version)`、`bundle_sha256`）；不动既有表结构；down 迁移存在。
验证：`migrations/versioned` 新序号迁移在副本库 up/down；现有迁移回归。

### TASK-K02 技能发布/查询/下载/下架接口

引用：INT-140、BND-141、状态机。
完成条件：INT-140a-e 挂现有 RBAC（Admin 发布/下架，Viewer+策略内下载）；发布时服务端重算 SHA-256；下载走 `fs.GetFile` 且带 hash 头；版本冲突 `SKILL_VERSION_CONFLICT`；下架停新下载。
验证：handler 级测试覆盖权限矩阵；篡改字节/越界路径包拒绝；与既有 catalog 测试共存不回归。

### TASK-K03 manifest 解析与安全校验

引用：BND-140。
完成条件：`opc-client.json` 解析（skill_id/version/sha256/exec_mode/clients/allowed_tool_categories/dependencies/min_client_protocol）；未声明或校验失败默认不进本地执行；包约束复用现有 20000/100k/32MiB/512MiB/防越界校验。
验证：单测含缺字段、错 hash、symlink、超限样本。

### TASK-K04 精确调用数据与迁移

引用：EXT-002、DATA-130。
完成条件：新迁移建 `mcp_invocations`、`mcp_invocation_decisions`（幂等键/单次决定唯一约束）。
验证：副本库 up/down；与 `agent_prompts` 语义对齐评审（不回填历史）。

### TASK-K05 精确调用适配与审批绑定

引用：INT-150、BND-150~152、调用状态机。
完成条件：INT-150a-e；双身份上下文（个人上游 token + platform 调用上下文）一致才受理；缺审批上下文的危险调用直接拒绝（不允许 `mcp_tool.go:196` 静默跳过）；decision 绑定 `args_digest+policy_version`；写超时/崩溃记 `unknown_outcome`；stdio 限制不解除。
验证：状态机逐态测试；自审批/过期/重复审批拒绝；断网后结果查询；PBT：重复回调无重复副作用。

### TASK-K06 knowledge 回归与契约

引用：VAL-001、REQ-011。
完成条件：相关 Go 包测试全绿；frontend type-check/test 不回归；既有 RBAC/技能/MCP 测试无回归。
验证：测试报告；兼容契约失败阻断写入用例。

## 3. work-opc（办公客户端）

### TASK-W01 macOS Keychain 适配

引用：REQ-010、BND-160。
完成条件：Tauri 引入 keyring 能力（Rust 侧命令或插件）；企业 refresh 进出 Keychain；`secrets.json`、日志、工具参数不含企业凭据；SecretStore 接口语义不变。
验证：`security find-generic-password` 检索到条目；凭据扫描测试零命中；pytest `tests/test_secrets*.py` 不回归。

### TASK-W02 企业登录流程

引用：INT-101、BND-101/102、OPEN-104（回环）。
完成条件：系统浏览器打开 authorize；本机 `127.0.0.1` 临时端口收回调；PKCE 生成/校验；会话建立/刷新/退出/停用可观察。
验证：假 platform 端对端（UI 自动化或集成测试）；重放/错 state 拒绝。

### TASK-W03 上下文隔离与受管技能目录

引用：BND-120/142、ADR-010。
完成条件：上下文键 `issuer+subject+tenant+generation`；切换即终止旧连接/审批/租约；`extra_skill_dirs` 挂 `opc/<tenant>/<publish_id>` 受管目录；`skill_filter` callable 实时求值。
验证：切账号/空间后旧句柄关闭断言；同名个人技能不遮蔽企业技能用例。

### TASK-W04 企业工具与审批桥

引用：INT-120/150、BND-151/163。
完成条件：`knowledge.search/read`、精确 MCP 调用工具经 `extra_tools` 注入；审批落 Inbox（`POST /v1/inbox/{id}/resolve`），语义服务端重校验；离线不启动新企业技能。
验证：pytest 覆盖工具注入与审批降级校验；GUI Vitest 审批卡不回归。

### TASK-W05 work 构建与测试

引用：VAL-001。
完成条件：pytest 全绿；GUI `npm test`（Vitest）与 Vite build 通过；Tauri 构建通过。
验证：命令输出记录。

## 4. code-opc（代码客户端）

### TASK-C01 企业身份关联与会话纪元

引用：REQ-001/003、INT-101g/102a/b、DATA-006、BND-110。
完成条件：code 后端 federation exchange（一次性 `aud=code-backend` 材料换主体映射）；企业会话带 `ent_sid+authz_version`；JWT/WS/PAT/task token 企业请求全部校验版本；版本缓存 ≤30s + 主动失效；用户表新增外部身份关联（迁移遵守 `CREATE INDEX CONCURRENTLY` 单语句规则）。
验证：middleware/handler 测试矩阵（含滑动续期不绕过版本）；迁移副本演练。

### TASK-C02 Daemon 受管任务与失败关闭

引用：BND-161、INT-102、ADR-010。现状：`daemon.go:2548-2586` 失败回退原配置。
完成条件：企业任务领取必须持租约；MCP 配置失败/解析失败/空集合语义按 BND-161（企业失败关闭，不回退 `null`）；未声明受管协议的旧 Daemon 领不到企业任务；任务发起人以持久化字段为准。
验证：claim/WS/租约过期用例；空配置 vs 未配置区分测试。

### TASK-C03 企业技能 resolver 固定版本

引用：BND-142。现状：`daemon.go:3979-4059` 不按 hash 固定。
完成条件：企业技能按 `publish_id+sha256` 固定；缓存键含 issuer/subject/tenant/publish_id/hash；字节校验失败即删且不破坏旧版本。
验证：`skill_cache` 与 resolver 测试；请求旧 hash 不得返回新包。

### TASK-C04 三 CLI 受管参数

引用：BND-162。
完成条件：Claude 企业任务去默认 bypassPermissions + `--strict-mcp-config`；Codex 显式 `approvalPolicy/sandbox` + 受管块剥离用户覆盖；Kimi ACP 权限请求路由有权用户，自动选择仅限拒绝项；长期 token 不进 argv/生成配置。
验证：adapter 单测断言 argv/配置文件内容；真实 CLI smoke 仅按仓规则授权后运行（`agentintegration` + `MULTICA_RUN_REAL_AGENT_SMOKE=1`）。

### TASK-C05 Electron 凭据与回环回调

引用：BND-161、OPEN-104。
完成条件：企业凭据走 `safeStorage`；企业 token 退出 `localStorage` 与 daemon profile `config.json`；主进程监听回环回调。
验证：桌面主进程测试；grep 断言无企业 token 落盘路径。

### TASK-C06 code 构建与测试

引用：VAL-001、code 仓 AGENTS.md。
完成条件：`make test`、`pnpm typecheck`、`pnpm lint`、`pnpm test` 通过；Playwright 相关用例通过；`make sqlc` 生成物一致。
验证：命令输出记录；真实 CLI smoke 单独标注授权与结果。

## 5. 跨仓集成与收口

### TASK-X01 契约测试集

引用：INT-002~007、REQ-011。
完成条件：platform↔knowledge（token/权限/发布/调用）、platform↔code（兑换/租约/撤权）契约测试固定版本可重复跑；失败阻断写入用例。
验证：契约测试报告随发布清单固定。

### TASK-X02 发布清单补全至 ready

引用：REQ-011、ADR-012、[release-checklist](../release-checklist.md)。
完成条件：按最终代码与部署事实消除全部待定项；每仓在发布顺序中有明确动作或 N/A 原因；`repositories.json.releaseChecklist.state=ready` 并写入规范化 sha256。
验证：`sdd-loop solo check` 通过。

### TASK-X03 纵向验收 A/B 与精确 MCP

引用：VAL-101~111、规格 §13 资源（试点部门 `技术交付中心`、账号 `杨晶`、MCP 端点 `技术部离职文档`）。
完成条件：验证 A（登录/撤权穿透双端与在途任务）、B（技能完整包按权限分发）、精确 MCP 独立执行证据（读类调用、审批/取消/未知结果）全部实测通过；60s 撤权计时记录。
验证：测试环境部署后实测，记录版本与指纹；缺资源则记缺口不冒充。

### TASK-X04 迁移与回滚演练

引用：DATA-011、ACC-010。
完成条件：代表性存量数据隔离副本上迁移 + 回滚通过；旧凭据盘点与轮换清单执行；兼容窗口内旧客户端个人能力不受影响。
验证：演练记录（数据量、步骤、耗时、回滚验证点）。

## 6. 依赖顺序与进入实施的条件

```text
P01(安全基线) → P02/P03 → P04/P05/P09 → P06/P07/P08/P10
K01/K04 → K02/K03/K05 → K06
P04 完成后：W01/W02、C01 可开始；W03/W04、C02~C05 随后
X01 贯穿；X03/X04 依赖各仓完成；X02 候选冻结前完成
```

进入实施的条件：本文件经用户确认（记录 tasks 的 `stage_completed`），并在后续消息 Continue。实施期如发现需改变已确认架构/规格的事实，先改文档重新确认，不静默扩大范围；新发现记入 `docs/backlog.md`。
