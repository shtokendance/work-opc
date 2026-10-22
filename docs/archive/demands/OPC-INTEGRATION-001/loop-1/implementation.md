---
demandId: OPC-INTEGRATION-001
loop: 1
document: implementation
status: archived
confirmedBy: Roger0808 <yj198788@hotmail.com>
confirmedAt: 2026-10-22
confirmedVersion: "本文件 v1.0（2026-10-22 收口轮）；实施内容以四仓分支 HEAD 为准：work-opc 3f85859、platform-opc 0a543a3、code-opc 2e3883db、knowledge-opc 45fc71d1"
confirmedNote: 用户在 2026-10-22 会话明确"先结束这个 loop 再开新的"并选择 B（撤权计时转后续），授权按已实施内容收口。
---

# OPC-INTEGRATION-001 Loop 1 实施记录（loop 级汇总）

## 基线与分支

- 主仓 work-opc：基线 main `5bf0d2fb8c728932fdbc6b49a24113aebc2a4ddd`；分支 `feature/261020_OPC-INTEGRATION-001_opc-integration`。
- 参与仓基线与分支同 Manifest（`repositories.json`，platform-opc=wecom-opc / code-opc / knowledge-opc=OPC-2.0）。
- 实施期间未提交本机 worktree 绝对路径入档；交付区间以 `sdd-loop provenance check` 为准。

## 实施范围（逐仓明细见 repositories/<repo>/implementation.md）

| 仓 | 范围 | 明细 |
| --- | --- | --- |
| work-opc | TASK-W01~W05 | 企业登录（PKCE/Keychain/INT-101a~e）、上下文隔离与企业技能受管目录、企业工具（INT-120/150）、GUI 企业登录态；本轮追加：兑换 audience 修复、空 tenant 防御、knowledge_search 租户缺省+KB 钉选、GUI 已登录态/企业区姓名邮箱/移除云登录入口 |
| platform-opc（wecom-opc） | TASK-P01~P0x | 安全基线/认证组（INT-101a~g、INT-102a~c、BND-110 撤权机器、BND-111 失效推送）、租约、兼容桥、知识与技能适配（INT-120/142/141）、MCP 转发（INT-150）；本轮追加：work 令牌 tenant/subject/spaces/email/name、code-exchange email/name、INT-120a 跨库合并检索、真 OIDC（企微扫码→unionNo→账户中心→JWT→userinfo）、redirect 白名单 |
| code-opc | TASK-C01~C0x/M系列 | 企业登录兑换（双形态）、EnterpriseVersion 中间件、PAT 590 版本快照、企业工作区镜像；本轮追加：GetAuthz 按 INT-102b subject 键控线形解析（修复扁平误解析导致的版本漂移 401）、企业用户真实姓名/邮箱入库 |
| knowledge-opc | EXT-001/EXT-002 | 技能 catalog 注册/发布 API（EXT-001 发布与幂等）、精确 MCP 服务端（EXT-002）；本轮无代码改动（追溯包刷新提交除外） |

## 2026-10-22 桌面验收修复轮（六轮，全部 retest=PASS）

1. work 兑换补 `audience`（work-opc `179bc06`）
2. work 令牌响应补 tenant/subject/spaces（platform `410405c`→`d15a6b5`）+ 空 tenant 防御（work `55453da`）
3. code GetAuthz 线形解析修复（code-opc `28c513bd`）+ 漂移数据归位（enterprise_authz_version 9→1）
4. 企业用户真实姓名/邮箱（platform `03e0694`/`9d2df28`、code `1386747e`）
5. knowledge_search 契约对齐（platform `e2b79db` 跨库合并、work `9cb4d38` 租户缺省）
6. GUI 三改动（work `87cb6eb`）

部署证据：`/data/backup-opc-integration-20261021/deploy-evidence-opc-int-001.txt`（测试服务器，逐轮记录二进制 sha256/回滚备份/验证 curl）。

## 产品决定（影响范围，不改本文档条款）

- 2026-10-22：技能分发不做发布流水线验收；客户端显性调用交互（skills 浏览安装 / @ 知识范围选择器 / MCP 显性 @ 调用）为新迭代需求（见 backlog 与 workflow 审计 decision 事件）。
- 2026-10-22：撤权 60 秒计时验收转后续（用户选择 B）；精确 MCP 审批卡 E2E 随旧交互模型废止。

## 实施状态

四仓全部提交并推送；追溯包已按当前 HEAD 刷新并通过 `sdd-loop solo check` S1–S5 全绿（`solo-closure-receipt.json`）。
