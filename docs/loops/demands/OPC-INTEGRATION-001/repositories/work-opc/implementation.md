---
demandId: OPC-INTEGRATION-001
repositoryId: work-opc
document: implementation
---

# work-opc 实施记录（主仓）

- 基线：main / `5bf0d2fb8c728932fdbc6b49a24113aebc2a4ddd`（远程固定）。
- 当前分支：`feature/261020_OPC-INTEGRATION-001_opc-integration`（标准分支）。
- 任务范围：TASK-W01~W05。
- 工作区既有未提交/未跟踪项（保留不动）：`.codegraph/`、`docs/.DS_Store`、`docs/integration/` 历史文档与本 Loop 治理文档（intent-to-add）。
- 验证命令：pytest、`surfaces/gui` 下 `npm test` 与 build、Tauri build。

## 变更日志

- 2026-10-20 TASK-W01+W02 Keychain 与企业登录（主会话复验 `.venv/bin/pytest` 新增 21 + 既有 58 全过 exit 0；GUI 549 用例与 `npm run build` 由子代理验证通过）：`coworker/enterprise/`（store 协议 + auth 服务：PKCE/一次性 state/INT-101 对接/refresh 轮换/复用清上下文/EnterpriseContext 键隔离与旧键作废可观察）；sidecar 六端点（callback 为唯一新增 tokenless 路径）；`coworker/config.py` 加 `opc_platform_base`（默认空=企业登录返回 ENTERPRISE_NOT_CONFIGURED）；Rust `enterprise_secret_put/get/delete`（keyring 3，service com.openworker.desktop.enterprise）；GUI `enterprise.ts` 四态状态机 + Sidebar 企业登录入口 + 双 locale 键；假平台 `coworker/testing/fake_opc.py` 支撑真实 TCP 往返测试。未 commit/push。遗留：本机无 Rust 工具链，Cargo.lock 未更新（待 cargo check 环境验证 keyring 3.x）；W03 的上下文→会话/审批绑定挂接点已预留（orphaned_context_keys）。
- 2026-10-20 TASK-W03+W04 上下文隔离与企业工具（主会话复验新增 42 用例全过 exit 0；子代理全量回归 855 passed/1 环境性失败（slack-bolt 未装，既有））：`coworker/enterprise/skills.py`（受管目录 opc/<tenant>/<publish_id>、zip 安全解包对齐 BND-140、manifest 校验含 K03 payload-digest 语义、cloud_only 拒装、授权刷新失败关闭、delisted skip 不删已装）；`coworker/enterprise/tools.py`（knowledge_search/read 走 INT-120、mcp_invoke 走 INT-150、202→Inbox 企业审批（data.enterprise+invocation_id+参数摘要）→decision→轮询终态、orphaned/superseded 不发决定、grant 词汇服务端重校验精神、超时 unknown_outcome 不重发）；manager 层 sync_enterprise_context 切换钩子（orphan 审批/清缓存/关连接/引擎重建排队）；`agent.py` 新增 `enterprise_skill_dirs` 尾插参数（**修正任务前提：extra_skill_dirs 在前会被个人同名遮蔽——SkillLoader 后写覆盖，故尾插保证企业优先**，测试断言）；会话表 enterprise 列；GUI Inbox/ApprovalCard 企业标记与文案。未 commit/push。遗留：检索会话无本地状态可分片（经平台无状态往返，docstring 记录）；W05 构建证据已由全量回归覆盖，正式归 verification.md。
- 2026-10-21 DMG 构建与契约修复（构建代理 + 主会话；commit 9591f63）：Rust 1.99.1 + cmake 安装；keyring 3.x 需显式 apple-native feature（`Cargo.toml` 一行构建配置，运行时 Keychain 探针验证读写删）；OpenWorker_0.3.4_aarch64.dmg（148MB，sha256 81c80845…，未签名开发包）首版构建并装 /Applications 冒烟。冒烟发现跨仓契约偏差：sidecar redirect_uri 为 /v1/enterprise/oauth/callback 而平台 authorize 只收 127.0.0.1:<port>/callback → 已修（双路径注册 + tokenless 补 /callback），pytest 69 过；重打 DMG 中。
