# Kimi 接手：OPC-INTEGRATION-001 / Loop 1

交接日期：2026-10-20。本文件是接手说明，不是第二份需求或状态权威。阶段状态以 [status.md](status.md) 和锁定工作流审计为准。

## 现在从哪里继续

**Requirements v1.3、Architecture v1.0 及长期 Architecture Baseline 已确认；下一份要编写的是 Specification。尚未创建 Specification、Tasks，也没有实施业务代码。**

用户最新原话：

> Continue。你的 token 快不够了，你写一份交接文档，接下来都让 kimi 干吗，他和你是同一台本机，拥有一样的权限

此消息紧接 Codex 对 Architecture v1.0 和 EXT-001/EXT-002 两项具体扩展的确认请求，按上下文作为本版架构及这两项限定扩展的批准。用户同时将原“Codex 写文档、Kimi 写代码”分工改为 **Kimi 接手后续文档、实施、验证及交付流程**。不再让用户重复批准同一版架构或两项扩展。

本次 Codex 仅完成确认留痕与交接。full 模式仍要求阶段批准后的独立 Continue：用户把本交接发给你并要求“继续”时，就是下一次继续指令，可记录该实际输入后开始规格；不要虚构跨阶段批准。Specification、Tasks 仍需各自确认，Tasks 确认及后续 Continue 后才进入实施。

用户已明确：

> 基线肯定一致，你不需要浪费 token 查了

**不要重新 fetch/比对四仓基线、检查服务器部署版本或反复 SSH 核查镜像。** 固定值已在清单，按用户确认推进。开工只做必要的工作区状态和治理检查，防止覆盖无关工作，不重新做已经完成的发现与访谈。

## 必读入口与本机定位

主仓是 work-opc；code-opc、platform-opc、knowledge-opc 都在主仓的同级目录，可从主仓分别以 `../code-opc`、`../platform-opc`、`../knowledge-opc` 访问。同一台本机、同等权限，不需要用户搬运源码、日志或重新授予普通本地读写权限。

按顺序读：

1. 主仓 `AGENTS.md` 及其引用的 RTK 规则；各参与仓在写入前读自己的 `AGENTS.md`。
2. 当前用户指定的 `sdd-loop/skills/sdd-solo/SKILL.md`（同级 sdd-loop 仓库）；若原路径不可用，读取本机已安装的 sdd-solo。继续已锁定 full + loop，不重选路线或初始化。
3. [唯一状态](status.md)、[参与清单](repositories.json)、[工作流锁](loop-1/workflow.lock.json)、[追加审计](loop-1/workflow.audit.jsonl)。
4. [已确认需求](loop-1/requirements.md)、[需求独立复核](loop-1/requirements-review.md)、[已确认架构](loop-1/architecture.md)、[长期架构基线](../../../architecture/overview.md)。
5. [用户决定来源](sources/user-decisions.md)、[原始 v3 快照](sources/integration-plan-v3.md)、[后续待办](../../../backlog.md)。

原始 v3 只是参考材料；后续用户决定及已确认 Requirements 优先。不要恢复被移出本轮的范围。

主仓需求目录：`docs/loops/demands/OPC-INTEGRATION-001/`。所有四仓使用同一分支：

`feature/261020_OPC-INTEGRATION-001_opc-integration`

以下仅为方便接手复制清单的固定值，不要求重新核对：

| 仓库 | 起点 | 固定 commit |
| --- | --- | --- |
| work-opc | main | `5bf0d2fb8c728932fdbc6b49a24113aebc2a4ddd` |
| code-opc | main | `2f247cc92f0c5cbf604097e57012b572046d7a6f` |
| platform-opc | main | `d6a07bf02fd7aeded69fca810c9c7e4f2f07d74c` |
| knowledge-opc | release | `628d974dd68736c39e09a2cc8a077047f5d486f4` |

platform-opc 远程仍叫 wecom-opc；knowledge-opc 远程仍叫 OPC-2.0。不是漏了两个仓库。

## 已定范围，不要重问

- 内部员工试点，未来对外；预计 1000 用户，不是 1000 并发。优先解决产品登录与权限贯通。
- 首批 macOS；code 验收 Claude Code、Codex、Kimi Code。Windows/Linux 客户端及其他 CLI 后置。
- 登录、空间权限、个人知识库访问、撤权、企业技能分发和精确 MCP 均为必做。不能以关闭能力、只有云问答或文档完成冒充交付。
- REQ-006 只做受保护接口手动组织同步，保留新增身份/部门、调岗、主管降权、离职/停用、来源隔离、幂等和部分失败处理；不做事件/cron/自动重试调度或专用管理页面。旧启动/登录/缓存补偿入口也不能偷偷触发全量同步。
- REQ-005 本轮不引入 OpenBao；如果证明非用不可，先解释理由和替代方案不足，再取得用户决定。服务器已有 OpenBao 不等于本轮获准依赖它。
- REQ-008 云端 Agent 委托及验证 C 已明确移出并记入 backlog。MCP 自己所需的 OAuth、审批、取消及未知结果处理仍必做。
- REQ-011 仅固定版本溯源、必要契约、单部门灰度和回滚；涉及迁移要用代表性存量数据验证，不建设通用发布平台。
- 撤权 60 秒从网关应用变更起算。账户中心变更到管理员手动触发间不承诺自动检测 SLA。不能只注销 UI 而漏掉 JWT/PAT/WS/task token/Daemon/在途任务。

## 已确认架构与 knowledge 改动边界

以 architecture.md 为完整权威，以下仅为索引：

- ADR-003～005：系统浏览器、回环回调、一次性码和 PKCE；稳定主体映射；平台/code 各自会话；macOS Keychain；短时任务授权与逐次授权。邮箱/姓名不能自动联号，Daemon 注册身份不能冒充员工。
- ADR-004：现有 knowledge JWT/auth_tokens 兼容桥继续受控使用，隔离在平台适配器，服务端持有短时个人上游凭据，独立签名密钥和最小 DB 权限。不自动扩展为新身份供给 API。
- ADR-006：个人 REST 知识访问，不依赖共享 endpoint token 或 `ask`。
- ADR-007/008 **EXT-001 已批准**：knowledge 增加本地技能发布记录、不可变归档引用及授权发布/查询/完整包下载接口与必要迁移；复用 catalog/RBAC/文件服务。不是预览拼包，不给员工 Admin token，不改云沙箱安装引擎。
- ADR-008/009 **EXT-002 已批准**：knowledge 增加精确工具调用适配、必要请求记录、状态/取消/人工决定，复用 MCP manager、个人 OAuth 和策略，补请求级审批。不能直接裸调 CallTool 绕过策略；不启动云端 Agent。
- knowledge 扩展限上述边界，涉及隔离模块、路由/依赖注入和少量模型/迁移，不是假装“仅加两个端点”。不修改检索或 Agent 引擎，不解除 stdio 安全限制；更大范围需另说明。
- ADR-010：work 利用引擎注入点；code 保留后端/任务/Daemon；企业 MCP 解析失败关闭，空集合不能回退本机全部配置；技能固定 hash；Claude 企业模式不能默认 bypass，Kimi ACP 不能自动许可。
- ADR-011、DATA、MIG、INT、ADR-012：手动同步运行账本、数据所有权、增量迁移与来源回填、跨仓契约、发布/回退顺序已写明。

架构内“候选键/候选模块/具体 TTL 下一阶段固定”等是留给 Specification 的细化，不是授权你随意更换已确认安全模型。若事实要求改变设计，明确影响并重新确认相应条款。

## Specification 需要补齐的缺口

先利用既有源码证据，按缺口定向调查，不重读四仓全部代码：

1. 账户中心不可变主体的永久性、完整分页、停用/删除语义。字段存在不等于契约成立；无完整快照不能批量清权。
2. 身份冲突与历史受管/手工授权来源。确定拒绝、人工处理及回填行为，不按邮箱猜测。
3. 授权码/访问/刷新/任务租约 TTL、轮换复用检测、60 秒撤权预算分配、错误语义与状态机。
4. 两项 knowledge 扩展的路径/字段/权限/幂等与审批绑定，技能包 manifest/安全限制/版本保留与下架；未知 MCP 结果不能重发写操作。
5. 原生 Keychain 与 CLI 受管参数/本机审批传递的可执行验证方法。不要用注释或“应该支持”替代证据。
6. 代表性技能包及读/写/OAuth/审批 MCP 测试资源；macOS/CPU/CLI 版本及试点部门/账号。
7. 手动同步处理时限、负载/并发/p95 的建议验收值，向用户集中补问。1000 人不推导出并发，未做测试不写达标。

对必须由用户决定的缺口给出建议，标准模式只补缺口。可由源码或本机环境确定的事实不要原样问用户。没有真实测试账号/资源就记缺口，不能自造成功结果。

## 下一步执行顺序

1. 按实际安装运行 `rtk proxy sdd-loop capabilities --require solo@1 --host agents` 和 `--require governance@2 --host agents`，再运行 `rtk proxy sdd-loop check --demand OPC-INTEGRATION-001`。协议失败只报告，不自动升级/安装。
2. 读取用户当前接手指令；若是批准后的独立 Continue，向现有 run 追加 `decision`，记录进入 Specification，更新唯一状态文件，不重启 workflow。
3. 补齐上述影响行为的缺口，创建 `loop-1/specification.md`；稳定行为编号，覆盖正常/失败/权限/边界、状态机和验收映射。完成后给用户具体可评审文档并等待确认。
4. 用户确认规格后记录 confirmed、确认人/日期/版本及 `stage_completed`；后续 Continue 再创建 Tasks。Tasks 引用 REQ/ADR/规格编号，含逐仓完成条件与验证，准备真正可执行的发布/回滚清单。
5. Tasks 确认及后续 Continue 后，由你直接实施。建立各 repository unit，记录分支/commit/范围，实际运行测试、构建与必要纵向验证。所有权威文档继续在主仓，不在参与仓另建可编辑需求/独立 Loop。
6. 最终按锁定 v2 工作流推进：验证、架构对账、独立 AI Review、人工签署、Solo 追溯包/receipt 与关闭。调用 sdd-review 前由用户选择独立 reviewer；实现者不能自审。测试扩展逐项有真实证据，不提前填 PASS。

工作流事件写入现有 `loop-1/workflow.audit.jsonl`，必须用 CLI 追加，不手改旧事件：

```sh
rtk proxy sdd-loop workflow record \
  --run docs/loops/demands/OPC-INTEGRATION-001/loop-1 \
  --event-json <准备好的事件JSON文件>
```

Git 登记身份为 Roger0808 / yj198788@hotmail.com；由工具读取实际配置匹配，不编造身份。全部阶段属于同一个 Loop 1。

## 当前文件与验证状态

- 主仓治理、来源、需求、审查、架构和交接文件尚未提交；部分新文件只是 `git add -N` 的 intent-to-add，用于工具可见，未暂存内容。不因此认为文件“已提交”。
- 既有无关文件包括主仓 `.codegraph/`、`docs/.DS_Store`、历史 integration 文档；platform 原有 AGENTS/CLAUDE/docs 等也需保留。不 reset、clean、stash 或覆盖。
- 已完成的是文档检查、链接/代码围栏检查、`git diff --check` 与 SDD 状态检查。requirements-review 有独立需求审查证据；不是代码审查。
- 未实施业务代码、运行迁移、构建/业务测试、真实双端登录/撤权/技能/MCP 验收，未发布或部署。不得继承为 PASS。
- 服务器运行快照已写入 Architecture Baseline，仅是此前只读观察；不再检查基线一致性。需要真正联调/部署时遵循具体授权和部署技能，不接管旁边另一套 OPC 服务。
- 未获用户明确要求，不提交、推送、合并或部署。相同本机权限是工具能力，不等于对生产、破坏性数据操作的授权。
- 所有 shell 命令用 rtk。索引存在时先 CodeGraph 定位源码，再定向 rg/读取；不要自动建索引。不要输出秘密或在文档/审计写本机绝对工作树路径。

## 最容易踩的坑

- 把 v3“全阶段”恢复成云 Agent 委托，或反过来把必做技能/MCP 当可选关闭。
- 把 shared endpoint token、内部服务身份或 code Daemon 身份当真实员工。
- 只改登录页面，漏掉旧刷新 token、PAT、WS、已领取任务和空间切换。
- 技能请求旧 hash 却返回新包；按显示名缓存；从 Admin 预览拼归档。
- 直接复用 MCP Execute，却没传审批上下文；让执行 token 自审批；未知写结果自动重试。
- 因账户中心少一页/空结果就清空成员，或重复同步恢复停用用户。
- 将既有 SecretStore JSON 当 Keychain；让云权限自动覆盖本机文件/shell 许可。
- 为通过门禁伪造测试、审查、审批、追溯提交或缩小真实交付范围。

后续遇到真正的新决定再询问；本文件已记录的范围、分支、架构和两项有限扩展不要反复确认。
