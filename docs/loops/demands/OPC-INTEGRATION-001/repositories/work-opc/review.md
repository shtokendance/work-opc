---
demandId: OPC-INTEGRATION-001
repositoryId: work-opc
document: review
status: draft
---

# work-opc 独立审查记录

reviewer 路径：当前 Agent 的隔离只读 subagent（用户 2026-10-21 明确选择；审查前已完成自动化验证与架构回写）。

## 第一轮（2026-10-21，审查指纹 HEAD=5bf0d2fb…、工作区 diff 哈希 7e4526f5…）

结论：**CHANGES_REQUIRED**。

发现（含可复现证据）：
1. 高——BND-142 下载钉 hash 未实现 + digest 语义错配（sync 每次全量重装；SKILL_HASH_MISMATCH 从未抛出）。
2. 高——BND-102/160 refresh 链路生产不可达（15min access 到期后全 401，只能重登录）。
3. 中——BND-142 授权失败时同名遮蔽绕过 fail-closed（loader 目录未随授权失效移除）。
4. 低-中——conversations.py per-turn upsert 与 docstring 矛盾且可抹除企业绑定。
5. 低——refresh token 在 sidecar 内存超期驻留（卫生级）。
6. 观察项——refresh 经 renderer 中转（既定设计，提请人工定夺，不阻塞）。

已验证无问题清单见审查原始输出（一次性 state、PKCE、凭据扫描零命中、审批桥 orphan/superseded 语义、zip 安全、目录尾插优先、tokenless 面等）。

处置：修复代理执行 F1~F5（F6 记录），完成后重新固定指纹并重审差异。

## 修复回写（2026-10-21）

F1：install_enterprise_skill 加 expected_sha256（整包字节重算，不符抛 SKILL_HASH_MISMATCH、不落盘保旧版）；installed.json 记列表 pin，二次 sync 零下载。F2：session_view 带到期标记 + GUI 定时刷新（refreshSkewMs=120s）+ 复用检测清上下文，链路生产可达。F3：授权失效时受管目录从 live loader 摘除（个人同名恢复）、load_skill gate 前置。F4：docstring 对齐 + 空绑定不抹除（COALESCE 语义）。F5：handoff 即清空 + 终态事务清扫。F6 留人工定夺。修复后：企业相关 + secrets/server/skills 207 用例主会话复验全过；GUI 554 用例过。第二轮差异重审由原 reviewer 执行中。

## 第二轮（2026-10-21，差异重审，新指纹 4d546b6b…）

结论：**READY_FOR_HUMAN_REVIEW**。

F1~F5 逐条核对 PASS（钉 hash 实证二次 sync 下载数=1、刷新链路含 fake-timer 时序断言、遮蔽回归测试走真实 registry、绑定保留三态、refresh 内存即清+终态清扫）；复跑 131 pytest + 57 vitest 全过；无新阻塞发现。两条低危观察（loader 重扫瞬时 miss 同型既有模式；F6 留人工定夺）转人工审查包。
