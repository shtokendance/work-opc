---
demandId: OPC-INTEGRATION-001
document: requirements-review
status: draft
updatedAt: 2026-10-20
---

# 独立 Requirements 审查

本记录仅用于 sdd-solo 要求的需求独立审查，不是 Implementation 后的代码 Review，不代替用户阶段批准。reviewer 为隔离上下文只读子代理 requirements_review（首次以 fork_turns=none 创建）；主作者 Codex 未自行给出独立审查结论。

## 第一轮：v1.0

结论：CHANGES_REQUIRED。审查对象 SHA-256：f9d3b75ce98d53cc263941960cd9a5bcd585563a3e3bb67604344b53fbe8ff18。

P2：组织治理仅明确验收反向清权，缺少正向供给。REQ-006 和 ACC-006 未明确新员工、新部门的身份/空间/工作区关系供给，以及调岗后新部门权限到达双端。只撤销旧权限却不授予新权限也可能被误判通过。来源依据为 v3 的组织供给链路和“调岗先算差异再增新收旧”。

主作者处置：在 v1.1 中补充 REQ-006、SCN-005、ACC-006，增加正向供给、新授权到达两端、重复同步不重复创建或扩大授权、新增与回收的部分失败可追溯并安全重试。属于补齐既有 v3 范围，不新增产品范围；同一 reviewer 第二轮已确认修复，原发现关闭。

审查依据 SHA-256：

| 文件（相对 demand 根） | SHA-256 |
| --- | --- |
| sources/integration-plan-v3.md | de0646987a3586dbfb9c8ba978c4205df5248b9c546ae9a41d7ef88253a76a04 |
| sources/user-decisions.md | b84b675cb9f1fbb0ebbf12e8b42b620ec72aa18e67c327b000cd4aff42ef57ac |
| repositories.json（第一轮） | 5bb594bb9cdf61e651f7202e46562c7a6cb2d3903684430ad551165ec446d6c1 |
| 联合确认卡（仓库 docs/integration/2026-10-20-sdd-solo-confirmation.md） | 89e8856d944cddfeb2c8fe83acb59f99cbd92310feb0c4c676ff7c2f7c28146c |

reviewer 实际检查：完整阅读需求及四份依据，核对来源覆盖、用户决定、范围、安全边界、验收和阶段门禁；读取 AGENTS、状态与锁定工作流；solo@1、governance@2 和显式 demand check 均退出 0。

reviewer 判断：组织检测时延、并发/p95/资源预算、macOS/CLI 具体版本明确留待盘点及规格确认前固定，不属于本次需求确认阻断项；60 秒起算点没有冒充端到端离职撤权承诺。

限制：未读取业务源码、重新查询远程基线、访问部署或执行业务测试；不证明任何集成能力已实现。reviewer 未修改文件或 Git。

## 主作者文档检查

- 文档引用目标存在；REQ/ACC/SCN/NFR/NG 定义无重复且编号完整。
- 原始 v3 快照与用户文件逐字节一致。
- manifest 需求哈希与当前文档一致；四仓当前分支和 HEAD 与批准的分支/固定基线一致。
- git diff --check 通过；sdd-loop check 显式 demand 和按分支自动选择均退出 0，均定位 Requirements draft。
- 新文档仅以 git add -N 登记，git diff --cached --stat 为空；无提交、推送、部署或业务代码变更。原有未跟踪文件保留。
- 初始化按已批准的单人/单系统 demand 目录执行：创建 AGENTS.md、CLAUDE.md 和需求 status；模板删除不适用的多人/分流条款，路径定向改为 demand 布局，保留用户 RTK/CodeGraph 规则。未创建第二份根 status、历史审计或后续阶段空壳。参与仓的既有治理文件未更改。

## 第二轮：v1.1

结论：READY_FOR_HUMAN_REVIEW。原 P2 已解决，未发现新的阻断问题。reviewer 完整重读 v1.1，并重点检查修订条款、相关安全边界、状态和指纹。

REQ-006 第 92 行明确新员工、新部门供给和调岗增新收旧；SCN-005 第 160 行、ACC-006 第 177 行要求双端新权限可用、旧受管权限回收。没有新增独立产品范围、提前批准映射算法或突破 knowledge-opc 源码修改的单独确认边界。

| 固定对象 | SHA-256 |
| --- | --- |
| loop-1/requirements.md v1.1 | c32df0c901de19b6a425603a1db33c16460e944be60ab72a1cf8cac100f3e755 |
| repositories.json（第二轮） | db4c0370c5141dbd9ecf854dcd6a881b0fa293f88a743fed76c76a83aad1d4d5 |

其余来源依据与第一轮一致。记录时间：2026-10-20T18:06:25+08:00。

处置：原 P2 关闭，需求可交用户进行阶段确认；requirements.md 保持 draft，不记录 requirements 的 stage_completed，也不提前进入 Architecture。后续修改需求或固定依据须重新判断并复核。

限制：未执行业务测试、访问部署或验证实际集成能力；reviewer 未修改文件或 Git。此结论不充当最终代码 Review，也不代替用户批准。

## 第三轮：v1.2 范围调整复核

用户修改范围后，v1.1 结论不覆盖 v1.2。首次复核调用因 reviewer 用量限制中断，没有产生结论；用户要求继续后重试完成。需求保持 draft，未进入 Architecture。

结论：CHANGES_REQUIRED。P2：REQ-009 将 OAuth 凭据与审批一并写为不可跨请求复用，可能错误要求每次调用重新授权。个人 OAuth 应允许在其用户、允许空间、目标服务和有效授权范围内复用，每次重新检查权限与撤销；单次审批应绑定具体请求及操作。

| 固定对象 | SHA-256 |
| --- | --- |
| loop-1/requirements.md v1.2 | cafa3e29f2c04223908489794936ba39fdf8f94ef86f6dbb129112d99793a024 |
| repositories.json（第三轮） | a0dd9d331c0138ce8b949d9cb66f74689fa49021e901c27f63ef583177ccbb03 |
| sources/user-decisions.md | f5ac2d425df070bc6c6cda0b24653ed3d26e7e3c0e4fd48d390976ef3c006214 |
| docs/backlog.md（仓库根相对） | 3668a25cdd11a2a339a9b60c0e588b2b397a4380ed25c7e128562fe17da9d4b8 |

其余重点检查未发现阻断：技能/MCP 必做，云委托及 C 后置，手动同步含供给和清权且 60 秒起算正确，OpenBao 无隐性依赖，发布回滚范围收敛，历史全量声明不覆盖最新决定。

主作者处置：v1.3 修复上述凭据/审批歧义，增加同一请求查询/回调不得重复副作用，等待 reviewer 复核。reviewer 实际完整阅读需求、最新决定、manifest、backlog 及状态；未修改文件/Git、未执行业务测试或验证部署。

## 第四轮：v1.3

记录时间：2026-10-20T18:29:04+08:00。结论：READY_FOR_HUMAN_REVIEW。原 P2 已解决，完整重读后未发现新的阻断问题。REQ-009 区分许可范围内可复用的 OAuth 凭据与绑定具体请求/操作的审批，逐次检查授权和撤销，查询/回调无重复副作用。

技能和精确 MCP 必做、手动同步、60 秒起算、云委托后置、OpenBao 单独批准及必要发布回滚均与最新用户决定及验收一致。

| 固定对象 | SHA-256 |
| --- | --- |
| loop-1/requirements.md v1.3 | 6a88295d027316944fb81f826dd4d4c8ae0c099a2bdb2f9865264eebecea66cd |
| repositories.json（第四轮） | 31608e5327a2cc00854b303232123cd65a3dadde9ddbc947a5badb70aeb8180f |
| sources/user-decisions.md | f5ac2d425df070bc6c6cda0b24653ed3d26e7e3c0e4fd48d390976ef3c006214 |
| docs/backlog.md（仓库根相对） | 3668a25cdd11a2a339a9b60c0e588b2b397a4380ed25c7e128562fe17da9d4b8 |

处置：原 P2 关闭，待用户 Requirements 全文阶段确认；文档仍为 draft，不记录 stage_completed、不提前创建架构文档。

限制：reviewer 未修改文件/Git，未验证源码、部署或集成行为，未执行业务测试；本结论仅审查需求文档一致性，不是最终代码 Review 或用户批准。

## v1.3 用户确认留痕

记录时间：2026-10-20T18:36:01+08:00。用户在 v1.3 全文确认请求后回复“继续”，按上下文确认当前版本。批准前草稿 SHA-256 为 6a88295d027316944fb81f826dd4d4c8ae0c099a2bdb2f9865264eebecea66cd，确认后 SHA-256 为 9c63a8c01548da0c573c166e3eb64a9bcd7433b770da35bb69f1f6ea7761cac9。本次仅变更 frontmatter 状态、开头的待确认说明及文末确认记录，业务条款无变化；第四轮独立审查对应批准前草稿，不伪称 reviewer 重审了确认元数据。本次未进入 Architecture。
