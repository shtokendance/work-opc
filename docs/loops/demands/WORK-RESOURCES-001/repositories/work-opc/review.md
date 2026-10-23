---
project: work-opc
document: review
demandId: WORK-RESOURCES-001
repositoryId: work-opc
loop: 1
status: draft
updatedAt: 2026-10-23
---

# work-opc AI 代码审查

## 审查路径与 reviewer 身份

- 人类选择（2026-10-23）：留在当前 Agent 的只读 subagent 审查。
- reviewer：agent-6（explore，无实施上下文，只读；在 /tmp 沙箱副本执行动态探针，未触碰 worktree）。实施者 agent-2 未参与审查。
- 轮次：初审（CHANGES_REQUIRED：B1/B2 + M1~M7 + 5 minor）→ 修复（agent-2，+13 回归测试）→ 差量复审（READY_FOR_HUMAN_REVIEW）。

## 审查指纹（差量复审后最终）

- 基线：7be2e4cec8b91f6bb430a332241629500d7bf5ff；HEAD = 基线（全部未提交）
- 45 个未提交路径（23 修改 + 22 新增）；diff +1872/−1380
- `git status --short` 指纹 578f35f5b0675002（sha256 前 16 位）
- reviewer 沙箱复跑：enterprise 组 175 passed（exit 0，CWD canary 完好）、GUI 定向 32 passed

## 发现与处置

- B1（blocker）安装成功路径 rmtree('.') 清空进程 CWD → **已修复**：staging 哨兵 Optional 化 + `_rmtree_staging_guarded`（managed-root 前缀 + `.staging-` 双校验），三路径 canary 测试。
- B2（blocker）registry 授权门空 SHA 致生产 enterprise_load_skill 100% SKILL_REVOKED → **已修复**：授权传 ref.bundle_sha256 + 通配回退；新增端到端测试（WS 接收→turn 内真实 load 成功，修复前必红）。
- M1 Composer 仅选知识库可发空消息 + 空转测试 → **已修复**：发送门恢复规格语义，测试接入真实实例。
- M2 选择器键盘失效且 Enter 变发送 → **已修复**：键盘处理提升到 Composer 层拦截。
- M3 重试静默丢 scope/skill_ref → **已修复**：内存 + `_resources` sidecar 双路恢复。
- M4 历史气泡丢用户正文 → **已修复**：_display 只留正文，chips 走 GUI 渲染，硬编码中文移除。
- M5 PATCH enabled 缺 409 且与 install 不同锁 → **已修复**：进行中操作 409 + 共用 `_install_lock`（慢 unpack 线程竞争测试）。
- M6 引用计数死代码、卸载删 pinned 版本 → **已修复**：per-turn pins + turn 结束 release + pinned 非空 409 + 归零回收。
- M7 远端会话无本机资源守卫 → **已修复**：App.send 拦截并提示。
- 5 项 minor（401/502 映射、BadZipFile→422、scope 拒未知字段、chips i18n、停用≠撤权钉版可完成）全部落实。

差量复审确认：净变化恰为声明内容，两轮增量与声明一致，新增 9 个测试非空转；reviewer 独立复现 pytest enterprise 组 180 passed、GUI 587 passed/3 个基线失败。2 项 minor：Composer 同名并存行 duplicate key（已修：`key={s.scope + ":" + s.name}`）；httpBase 修复的自动化回归保护依赖 mock 形态（记录在案，可接受）。

## 增量复审（第二轮，2026-10-23）

在初审+第一轮差量复审（均 READY_FOR_HUMAN_REVIEW）之后又两轮变化：
1. 手测修复轮：useEnterpriseState.ts/enterpriseResources.ts 改 httpBase() 绝对地址（桌面 WebView 相对 fetch 静默失败，真机日志实证）。
2. 用户批准增量「/菜单合并企业技能」：manager.py session_skills_view 合并企业已装技能（scope=enterprise，不受个人 mute/Settings 禁用，未装不进菜单）；Composer pickSkill 分叉（企业→skill_ref 标签）；另纠正实施者一次错误的相对地址回退。reviewer 独立复现 pytest 180 passed、GUI 587/3（基线）。
3. minor 处置：同名并存 duplicate key 已修；httpBase 测试盲区记录在案。

## 残余风险（均不阻断）

- B2 单元测试「修复前必红」声明略有夸大，真正守卫是端到端测试（现状可接受）。
- M4 缺刷新后气泡正文的 GUI 层断言（服务端断言已抓住旧 bug）。
- `release_session` 仍不减引用（per-turn pins 覆盖主路径）。
- VAL-215/VAL-203/204/VAL-208/DMG 手测归 TASK-025～027；GUI 全量 3 个失败为基线既有 enterprise.test.ts。

## 唯一结论

**READY_FOR_HUMAN_REVIEW**
