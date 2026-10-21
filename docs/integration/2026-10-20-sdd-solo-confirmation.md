---
status: confirmed
document: solo-discovery-confirmation
updatedAt: 2026-10-20
---

# OPC 全量集成：联合确认卡

本文是用户已联合确认的需求和模式确认材料，不是正式 Requirements、Architecture、Specification 或 Tasks。下列“已答复”仅确认对应事实，不代表整体需求、交付模式、阶段文档或实施已获批准。

## 已答复的决定

| 项目 | 用户原文 / 固定含义 |
| --- | --- |
| 总范围 | “本次规划并实施 v3 全部阶段” |
| 分支 | “都拉一个分支。knowledge 从 release 拉，其他的从 main 拉分支” |
| 首批验收 | “先 macOS，三种 CLI”；三种为 Claude Code、Codex、Kimi Code |
| 分工 | Codex 编写文档；进入代码阶段交给 Kimi |

## 已答复的业务背景

- 产品方向：以后对外提供产品，当前先面向内部员工试用。
- 当前阶段：试点。
- 预计用户规模：1000 人；用户未说明是否为同时在线人数，不将其等同于并发指标。
- 当前最急痛点：产品登录和权限尚未打通。全量 v3 范围不变，优先验证身份与授权贯通。
- 部门数量尚未提供；后续按实际组织数据制定试点样本。
- 用户选择标准访谈，只补已有材料的缺口。此交互偏好不写入正式产品需求条款。

## 已确认的开发处理原则

用户确认原文：“好，符合我的意思”。确认日期：2026-10-20；确认对象为上一轮解释的处理原则，不是整份阶段文档审批。

- 保留全量 v3 目标，优先打通登录和权限。
- Kimi 按 Codex 编写的验证标准先做关键链路的小范围实现验证，再扩大实施。
- 优先复用 knowledge-opc 现有接口；确需修改时列出必要改动、原因和升级维护影响，由用户另行确认。
- 不擅自删减功能，不把关闭能力当作全量交付完成。验证失败须修订方案或重新确认范围，不能静默降级验收。

## 已确认的需求身份和组织方式

- demandId：`OPC-INTEGRATION-001`。
- 标题：OPC 全量集成与双客户端统一接入。
- 对话名称：`[SDD Solo][OPC-INTEGRATION-001] OPC 全量集成与双客户端统一接入`。
- 四仓标准分支：`feature/261020_OPC-INTEGRATION-001_opc-integration`；首次命名日期固定，不随续作变更。
- 主仓：work-opc。唯一权威文档维护于主仓 demand unit，其他仓只保存本仓相关追溯包。
- 单人、单会话、多仓需求，不把参与仓当成 stream。主仓工作内容是 Python/FastAPI 运行时和 React/Tauri 桌面产品，不按技术层单独建流。
- 治理身份建议沿用当前四仓 Git 身份 Roger0808 / yj198788@hotmail.com；六个治理角色均由该用户承担，Agent 为执行工具，独立 AI reviewer 另行留痕。

## 远程分析基线

2026-10-20 使用 git ls-remote 核实；knowledge-opc 已 fetch，未切换或覆盖工作树。

| 仓库 ID | 规范 remote | 远程基线 | commit | 当前职责 |
| --- | --- | --- | --- | --- |
| work-opc | https://github.com/Roger0808/work-opc.git | main | 5bf0d2fb8c728932fdbc6b49a24113aebc2a4ddd | 主仓；办公客户端 |
| code-opc | https://github.com/Roger0808/code-opc.git | main | 2f247cc92f0c5cbf604097e57012b572046d7a6f | 代码客户端、自有后端、Daemon |
| platform-opc | https://github.com/Roger0808/wecom-opc.git | main | d6a07bf02fd7aeded69fca810c9c7e4f2f07d74c | 文档 wecom-opc；组织身份与接入网关 |
| knowledge-opc | https://github.com/Roger0808/OPC-2.0.git | release | 628d974dd68736c39e09a2cc8a077047f5d486f4 | 文档 OPC-2.0；云资源与权限权威源、兼容验证 |

四仓本地均可访问，远程均无 master。线上部署、统一账户中心真实数据与 OpenBao 可用性尚未现场验证，不以旧勘察快照冒充当前事实。knowledge-opc 当前本地仍为 58b14903b6548d405e77db586e2b07bb1999b770，不能作为新分支起点。

## 已选 release 的增量源码核实

以下均直接读取 commit 628d974dd68736c39e09a2cc8a077047f5d486f4，而非本地旧工作树：

- internal/middleware/mcp_endpoint_auth.go:60–72 仍创建 mcp-<endpoint_id> 用户与 MCP endpoint 主体，不能推断个人 membership 撤销会使它失效。
- internal/router/routes_knowledge.go:138–139 下载要求 Contributor 和 KB 写权限，而预览为 Viewer 与 KB 读权限。
- internal/router/routes_chat.go:142–144 提供 Viewer 起步的个人 AgentQA 路由；完整会话所有权、审批和取消链路仍需原型 C 验证。
- internal/handler/skill_catalog.go 包含 catalog 文件预览接口，不能凭该接口的存在宣布无损完整包导出已解决。

## 全量范围及验收方向

| 范围 | 可验收结果 |
| --- | --- |
| Phase 0 网关安全与发布基线 | 管理接口服务内鉴权和范围检查、CSRF/来源校验、审计、TLS 验证、密钥注入和版本化发布证据；未通过不开放公网 |
| Phase 1 受控知识试点 | 两端在受控配置下访问同一知识源；共享 endpoint token 不作为员工个人凭据分发，验证撤权路径 |
| 原型 A / P1 身份、空间、RAG | 个人登录、稳定身份映射、空间隔离；双端检索有引用；网关应用权限变更后 60 秒内停止新云调用与续租，已领取任务也受约束；写前刷新，服务不可达则写失败关闭 |
| P2 组织治理 | 完整快照、差异与幂等执行；调岗、降权、停用正确回收，异常/空快照不批量清权，保留有来源手工授权；同步记录、重试与并发锁可追踪 |
| 原型 C / P2 云委托 | 按个人身份进行会话、审批、OAuth、取消与结果恢复；执行 token 不能自审批；写超时结果未知可见且不自动重复副作用 |
| 原型 B / P3a 技能 | 确定完整归档获取路线，固定版本、字节哈希、授权、安全解包及原子安装；按账号和空间隔离缓存，个人技能不能静默遮蔽企业技能 |
| P3b 精确 MCP | 独立能力证明与契约测试；凭据留在云端；不把 ask 委托表述为精确工具调用；失败时保留未完成状态，修订方案或重新确认范围，不以关闭能力代替交付 |
| P4 灰度与升级演练 | 副本数据验证、两类角色/两空间测试、单部门灰度、升级兼容检查与回滚；兼容失败阻断写操作 |
| 跨仓交付 | 每仓真实测试/审查/就绪凭据；唯一发布清单；只读追溯包提交并校验；聚合审查与机器收口后最终人工验收 |

业务主线：同一员工 work 查询部门知识并得到正确引用，code 在对应工作区完成任务；权限撤销后两端及已领取任务不得继续获得受限云资源。撤权不承诺抹除已经返回到员工电脑的信息。

## 非目标与边界

- 本轮首批验收不覆盖 Windows、Linux 或其他 CLI，不将其表述为已验证支持。
- 保留两端各自执行引擎、自有办公/任务/Git 能力及本地权限审批。
- 不复制知识向量库；不让客户端持管理员凭据、云 MCP 服务密钥或云 stdio command/env。
- 网关不建立第二套可独立编辑知识、技能、MCP 和空间授权的后台。
- 不默认把企业技能安装到所有沙箱；不按相同 email 自动合并账号。
- 完成文档或选定源代码基线不等于已授权发布、合并或升级现网。
- knowledge-opc 必要源码修改须说明原因和维护影响并单独确认；原型失败不能直接关闭能力并宣称全量完成。

## 模式推荐与治理

推荐 `full` + v2 `loop`。理由是身份/权限安全语义、跨端租约、组织反向清权和多阶段发布需要独立架构及治理门禁；不是因为仓库多而选择完整流程。

已验证 solo@1、governance@2 均可用于 agents 宿主。work-opc 尚未初始化，按 sdd-init 一次性初始化，再以 demand unit 承载本需求。platform-opc 已有未跟踪的 v2 状态不作为本需求独立状态源。

预览内置 Loop 顺序：Requirements → Architecture → Specification → Tasks → Implementation → Deployment → Manual Test → Verification → Final Test → Reconciliation → Review → Signoff → Close。要求独立 Review，最终由提交者签署。已按 demand OPC-INTEGRATION-001 运行 workflow recommend，工具推荐 loop；定义指纹为 sha256:b23b86b97c0697d86430b4baddb515a2e24754a8985332a6dbb3eceaf5b20356。正式启动前仍需用户确认并锁定；这份预览不等于 workflow 已启动。

Codex 只推进至已获确认的文档阶段。Requirements 完成后安排隔离上下文的只读 AI 审查；Tasks 确认后向 Kimi 交接代码工作。不得伪造未执行的实施、测试、部署、审查或收口证据。

## 尚待答复和后续勘察

- **处理原则已确认**：见上文；原型验证失败须解决或重新确认范围，knowledge-opc 必要改动单独批准。
- **业务背景已答复**：未来对外产品，内部员工试点，预计 1000 人，登录与权限互通最紧急；部门数和并发量未提供。
- **交互偏好已答复**：标准访谈，只补缺口。
- **待勘察**：按选定 release 复核授权/技能/云委托契约；线上源码与镜像来源；账号冲突及手工授权；统一账户中心快照/停用语义；目标测试环境与依赖可用性。架构定稿前完成关键事实核实；不能把实际原型结果提前写成通过。
- **联合确认已完成**：用户答复“确认联合卡”；正式清单已迁至 docs/loops/demands/OPC-INTEGRATION-001/repositories.json。
- **没有答不上来的事项**：术语已解释，用户已明确确认处理原则。

## v3 来源覆盖

v3 第 1/3 节映射为目标与所有权边界；第 4/5/6/9/10 节映射为范围、场景与验收；第 2/7/11 节为基线、部署与待勘察证据；第 8 节保留为原型后决策边界；附录作为来源取舍记录保留，不当作新增功能。原 v3 文件保持原样，具体接口/数据/结构在后续获准进入的 Architecture 和 Specification 中展开。

## 联合确认后的下一步

一次确认需求身份、四仓清单、full + loop、主仓单系统 demand unit 形态及上述 Git 身份角色映射。之后从固定 commit 准备四仓同名分支，保留既有未跟踪文件；初始化主仓治理结构并编写 Requirements 草稿，安排独立只读需求审查。后续 Architecture、Specification、Tasks 逐阶段编写并确认；Tasks 确认后交接 Kimi。此次联合确认不代替后续阶段审批，也不授权提交、推送、部署或合并。

## 联合确认留痕

用户 Roger0808 / yj198788@hotmail.com 于 2026-10-20 明确答复“确认联合卡”。记录时间：2026-10-20T17:54:10+08:00。确认版本为本卡列明的 OPC-INTEGRATION-001、全量 v3、四仓固定基线、标准分支、full + loop 和 macOS/三 CLI 范围；当前改动仅反映批准与落地状态，不扩展业务范围。后续正式 Requirements 仍为 draft，独立审查与阶段批准未完成。
