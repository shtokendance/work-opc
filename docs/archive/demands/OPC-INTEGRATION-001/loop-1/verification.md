---
demandId: OPC-INTEGRATION-001
loop: 1
document: verification
status: archived
confirmedBy: Roger0808 <yj198788@hotmail.com>
confirmedAt: 2026-10-22
confirmedVersion: "本文件 v1.0（2026-10-22 收口轮）；验证事实截至四仓 HEAD：work-opc 3f85859、platform-opc 0a543a3、code-opc 2e3883db、knowledge-opc 45fc71d1"
confirmedNote: 用户 2026-10-22 选择 B（撤权计时转后续）并指示"先结束这个 loop"，授权按本记录收口；未执行项已如实记录。
---

# OPC-INTEGRATION-001 Loop 1 验证记录（loop 级汇总）

逐仓明细与各任务级证据见 `repositories/<repo>/verification.md`；本文件为 Loop 级收口验证。

## 自动化验证（2026-10-20/21 实施轮 + 2026-10-22 验收修复轮）

| 仓 | 命令 | 结果 |
| --- | --- | --- |
| work-opc | `.venv/bin/pytest tests/test_enterprise_*.py`（auth/skills/tools/context/store） | PASS（实施轮 855 passed/9 skipped/1 环境性失败；验收轮 52+15+18 定向全过） |
| work-opc | GUI `npx tsc --noEmit` + `vitest run Sidebar` | PASS（0 错；5 用例） |
| platform-opc | `go build ./...` + `go test ./pkg/handler/ ./pkg/kbridge/` | PASS（含新增 GetAuthz 线形三用例、TestKnowledgeSearch_MultiKBMerge/TenantWithoutKB、integration 全组） |
| code-opc | `go build ./...` + `go test ./internal/platform/ ./internal/handler/` | PASS（定向；handler 全包重跑零失败——共享测试库跨进程残留偶发已清理后复跑确认） |
| knowledge-opc | 实施轮 EXT-001/002 单测 | PASS（追溯刷新轮无代码变更） |
| 四仓 | `sdd-loop solo check --write-receipt` | S1–S5 全绿 |

## 验收测试事实（测试服务器 https://shtokendance.benlai.com ，2026-10-22）

- knowledge 网页企微扫码登录全链路通（真 OIDC：企微 code→unionNo→账户中心→平台 JWT→userinfo）。
- work 桌面企业登录全链路通（audience/tenant/subject/email 修复后）；Multica（code）桌面企业登录通、登录后业务接口 200、成员列表显示真实姓名/邮箱。
- work 会话知识库检索实测通：`knowledge_search` 返回规范化命中（跨"员工离职交接文档库"与"wms-oms 技术知识库"合并、带 knowledge_base_id/name 与 locator）。
- 六轮验收修复均按 cycle 事件记录：问题/修复范围/定向测试/部署版本/复测 PASS（workflow.audit.jsonl）。
- 部署证据：`deploy-evidence-opc-int-001.txt`（二进制 sha256、回滚备份、验证 curl、回归检查）。

## 未执行项（诚实记录，均经产品决定）

| 项 | 处置 | 理由/去向 |
| --- | --- | --- |
| 撤权 60 秒计时验收（REQ-003 BND-110） | 转后续验证 | 用户 2026-10-22 选择 B；撤权机器在验收调试中已被实证（版本提升/失效推送/GetAuthz 重锚定实际触发）；计时精度随新迭代验收执行 |
| 精确 MCP 审批卡 E2E（REQ-009） | 随旧交互废止 | MCP 交互改为显性 @ 调用（新产品方向），策略驱动+审批卡交互不再补测 |
| 技能发布/自动下发验收（REQ-007 分发面） | 移出新迭代 | 发布流水线交互被产品决定取代；INT-142 列表/下载/受管安装代码保留，新需求裁决沿用或改造 |

## 工程扩展

| 扩展 | 结论 | 证据 |
| --- | --- | --- |
| testing | PASS | 上两表；规格正常/失败/权限/边界路径均有用例（假平台 4xx 透传、授权码重放、refresh 族撤销、PKCE 失败、跨租户 403、撤权 401/403、manifest 校验失败不落盘、zip 路径遍历拒绝） |
| pbt | N/A（有理由） | 无 PBT 库；核心不变量（状态一次性消费、版本单调、hash 一致性）以确定性 seed 单测覆盖：授权码/兑换材料重放、authz_version 单调递增守卫、bundle sha256 重算比对 |
| security | PASS | 敏感面验证：refresh 仅 macOS Keychain（store 测试+`security find-generic-password` 级断言）、审计脱敏、sensitive 不落日志断言；服务身份双形态（共享令牌/admin JWT）；redirect_uri 白名单（同源 302/跨站 400 实测）；桥 token ≤120s 且 tenant 绑定；无 Admin token 代理员工请求的客户端路径 |
| resiliency | PASS | fail-closed 证据：平台不可达→401/UPSTREAM_UNAVAILABLE 不降级共享凭据；授权刷新失败→企业技能出菜单且 loader 摘除；桥不可用→UPSTREAM_UNAVAILABLE；GetAuthz 解析修复后版本不再漂移；注入故障测试（fail_next_token/knowledge_search/searchStatus） |

## AI 代码审查

- 实施轮：三仓多轮只读 subagent 审查（第一轮→修复→第二/三轮差异重审），结论 READY_FOR_HUMAN_REVIEW，明细见 `repositories/<repo>/review.md` 与 `repositories/aggregate-review.md`。
- 验收修复轮：六轮修复均为契约对齐类（fake 与真平台线形不一致），每轮回补锁定用例（fake_opc 收紧 audience/tenant/query 校验、GetAuthz 线形三用例、MultiKBMerge 等），差量经定向测试与真平台实测复核。

## 人工审查包

- `repositories/aggregate-review.md`（聚合）
- `repositories/<repo>/review.md` × 4（含审查版本与代码指纹）
- 本文件 + implementation.md + workflow.audit.jsonl（14 decision / 6 cycle / 影响与重验事件）

## 规格映射摘要

身份认证 INT-101a~g / 服务身份 INT-102a~c / 撤权 BND-110·111 / 上下文隔离 BND-120 / 知识 INT-120a~c / 技能 INT-141·142·BND-140·142 / 精确 MCP INT-150·BND-151·163 / 数据模型 DATA-101·110·120·130——各条款证据见 `repositories/<repo>/verification.md` 规格映射节。
