---
demandId: OPC-INTEGRATION-001
repositoryId: work-opc
document: verification
status: draft
---

# work-opc 验证记录（主仓）

验证对象：TASK-W01~W04（W05 构建证据并入本节）。环境：本机 macOS 27.0.1 arm64、Python venv（.venv）、node v24.19.0；Rust 工具链本机不可用（cargo check 记 N/A）。

## 自动化验证（2026-10-20/21 执行）

| 命令 | 结果 |
| --- | --- |
| `.venv/bin/pytest tests/test_enterprise_store.py tests/test_enterprise_auth.py -q` | PASS（21，主会话复验） |
| `.venv/bin/pytest tests/test_enterprise_skills.py tests/test_enterprise_tools.py tests/test_enterprise_context.py -q` | PASS（42，主会话复验） |
| `.venv/bin/pytest tests/test_secrets.py tests/test_server.py -q` | PASS（58，主会话复验） |
| 全量 `.venv/bin/pytest tests/`（子代理执行） | 855 passed / 9 skipped / 1 failed——唯一失败 `test_fake_slack.py::test_connect_resolves_bot_and_receives_hello` 为 `.venv` 未装 slack-bolt 可选 extra 的环境性问题，基线即存在 |
| GUI `npm test`（子代理执行） | 56 文件 / 551 用例全过 |
| GUI `npm run build`（子代理执行） | PASS（tsc + vite） |
| `cargo check`（src-tauri） | N/A——本机无 Rust 工具链；`keyring="3"` 已入 Cargo.toml，Cargo.lock 更新与真机钥匙串验证为遗留（backlog） |

## 规格映射（关键条款 → 证据）

- BND-160/W01：企业 refresh 只经 Rust Keychain 命令存取；不落盘扫描测试（test_enterprise_store/auth）；secrets.json 与日志零企业凭据断言。
- BND-101/102/OPEN-104/W02：假平台全链路（authorize→callback→token→refresh→logout）、授权码重放、refresh 族撤销、回环回调（sidecar tokenless 仅 callback）。
- BND-120/142/W03：上下文切换 orphan 审批与 generation 递增、缓存分片隔离、企业命名空间尾插优先（修正 extra_skill_dirs 遮蔽问题）、manifest 校验失败不落盘、授权刷新失败关闭。
- INT-120/150/W04：knowledge_search/read 假平台往返、202→Inbox 企业审批→decision→轮询、超时 unknown_outcome 不重发、orphaned 不发决定、grant 词汇服务端重校验。
- BND-163：云审批不扩大本机许可（deny 默认 + `_grant_offered` 精神的服务端重校验沿用既有测试）。

## 差异与遗留（诚实记录）

- `slack-bolt` 未装致 1 个既有测试失败（环境性）；GUI 曾有 1 次 WS 时序 flaky（单独重跑通过）。
- Tauri Keychain 命令未在 Rust 环境编译验证（无工具链）；桌面真机冒烟留待部署阶段（release-checklist 第 5 项）。
- 真实平台联调（W02/W04 对 INT-101/120/150 的真实端）属跨仓验证 X03，随测试环境部署执行。

## 工程扩展

- Testing：见上表。PBT/Security/Resiliency 明细在跨仓验证阶段（X01/X03）统一回填。
