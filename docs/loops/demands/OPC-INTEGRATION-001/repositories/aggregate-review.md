---
demandId: OPC-INTEGRATION-001
document: aggregate-review
status: draft
---

# 跨仓聚合审查（OPC-INTEGRATION-001 Loop 1）

四仓独立审查均 READY_FOR_HUMAN_REVIEW 后进行。结论：**聚合通过，可进入候选冻结**（待用户授权提交与部署）。

## 契约两端核对

| 契约 | 两端 | 核对结果 |
| --- | --- | --- |
| code_verifier 接力 | platform code-exchange 必传并 S256 复核（先验后消费）↔ code begin/exchange 转发桌面 verifier（client_test 断言 verifier-1 到达平台） | ✅ |
| 租约 JWT 键名 | platform signLeaseToken `task_id/runtime_id/tenant_id` ↔ code ParseLease/validatePresentedLease | ✅（第三轮修正后对齐） |
| 调用上下文 | platform `sub`=knowledge 用户 id、`user`=immutable_subject、`aud=platform-integration`、`args_digest`=canonical JSON SHA-256、jti 一次性 ↔ knowledge validateContext 全项校验（aud 缺失拒绝、digest 等值、jti 唯一） | ✅（aud 部署配对已写入清单） |
| 技能 pin 词汇 | knowledge 列表 hash=bundle_sha256（整包字节）；work 钉整包 sha256；code 钉 skillbundle manifest hash 并以 X-SHA256 交叉验整包字节 | ✅ 字节级一致；两客户端 pin 词汇不同已记录（各自自洽） |
| INT-142/150/120 转发 | platform 桥 token 120s + 上下文转发 ↔ knowledge 路由校验（Viewer+、目标租户/角色、发布状态、双身份） | ✅（审查轮实测） |
| 撤权推送 | platform Notifier CodeInvalidateChannel（CODE_BASE_URL/CODE_SERVICE_TOKEN，30s 重试）↔ code POST /api/internal/enterprise/invalidate（服务 token 比对、落库单调守卫、缓存失效、WS 关连） | ✅ |

## 数据链路与部署顺序

- 数据链路：platform statedb（17 张迁移）↔ knowledge WeKnora 兼容（auth_tokens 插入/删除收窄）；code 企业列 + workspace link（590）；knowledge 三张新表（129-132）。互不写对方业务表 ✅。
- 发布顺序与依赖（清单 1–7 步）：备份 → knowledge 迁移 → platform（statedb+密钥）→ code → work → 单部门灰度 → 扩大；每步有验证点与回滚 ✅。
- 失败回滚：三仓 down 迁移齐备；回滚顺序 客户端→code→platform→knowledge 扩展，保留新表；旧凭据不复活 ✅。
- 可观测：platform audit_events / knowledge 调用记录 / work orphaned keys / code enterprise_rejections + enterprise_lease_lost ✅。

## 兼容窗口与遗留

- 旧客户端/Daemon：无 enterprise-protocol-v1 能力不领企业任务（BND-161）；旧 PAT 快照缺版本 → subject-only 降级（注释标注）；无 ArgsKey 时审批/OAuth 等待失败关闭（503，运维需配 WEKNORA_INTEGRATION_ARGS_KEY）。
- 已知缺口（均记 backlog，不阻断）：code 桌面企业 MCP 审批界面（work 已覆盖）、WS PAT 版本快照穿线、Hermes 企业门控、Codex thread/resume、同步异步化等。
- 存量测试失败：knowledge 41+1、code 少量 flake——全部经 pristine 对照证实与本轮无关（环境性）。

## 机器收口前置

- 四仓 readiness receipt 已生成（本目录 repositories/<id>/repository-receipt.json）。
- 待用户授权：四仓提交（trace 导出需要参与仓 commit）→ trace 导出校验 → `sdd-loop solo check --write-receipt`。
