---
demandId: OPC-INTEGRATION-001
repositoryId: work-opc
document: tasks
---

# work-opc 任务（总体 tasks.md 的本仓视图；主仓）

基线：main / `5bf0d2fb8c728932fdbc6b49a24113aebc2a4ddd`；分支 `feature/261020_OPC-INTEGRATION-001_opc-integration`。验证：pytest、`npm test`、Vite build、Tauri build。

- TASK-W01 macOS Keychain 适配。
- TASK-W02 企业登录（系统浏览器 + 回环回调 + PKCE）。
- TASK-W03 上下文隔离与受管技能目录。
- TASK-W04 企业工具与审批桥。
- TASK-W05 构建与测试。

前置：platform TASK-P04 完成（登录兑换依赖真实接口；单元层可先用假端）。
