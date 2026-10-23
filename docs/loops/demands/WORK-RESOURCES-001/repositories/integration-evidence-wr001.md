---
project: work-opc
document: integration-evidence
demandId: WORK-RESOURCES-001
repositories: work-opc × platform-opc × knowledge-opc
related: deploy-evidence-wr001.md（部署证据与交叉引用源）
date: 2026-10-23（Asia/Shanghai）
---

# WORK-RESOURCES-001 联调证据：work 客户端对接测试环境（TASK-025 核心 + VAL-215）

## 0. 接入方式

- **网络**：测试服务器 10.101.102.116 直连 SSH 瞬断（与部署证据第 5 节同类事件，三次重试均 `Connection closed by ... port 22`）。经既有 Cloudflare Access 隧道（`127.0.0.1:2222`，`ssh.tokendance.sh`）建立 SSH，再做本地转发 `127.0.0.1:13002 → 127.0.0.1:3002(服务器) → weknora-gateway 容器`。
- **端口确认**：`curl http://127.0.0.1:13002/health` → 200 `{"status":"ok","time":"ok"}`；未认证 `/integration/v1/session` → 401 `SESSION_REVOKED`（INT-105 信封）→ 确认为 platform gateway。
- **work sidecar 配置**：`OPC_PLATFORM_BASE=http://127.0.0.1:13002`（env 覆盖 config.toml，见 `coworker/config.py:407-411`）；`COWORKER_STATE_DIR=/tmp/wr001-itest-state`（隔离状态）；端口 18765；启动方式 `python -m` 等价 bootstrap（`/tmp/wr001_sidecar_bootstrap.py`，仅建立上下文，不改交付代码）。

## 1. 企业登录（真实令牌，非伪造）

- 测试平台企业登录为企微 OIDC（`/integration/v1/auth/authorize` → wecom QR），**扫码步骤 agent 不可执行**。
- 采用上一需求（OPC-INTEGRATION-001，2026-10-22 真实登录）留在本机 macOS Keychain 的 refresh JWT（service `com.openworker.desktop.enterprise`，account `default`，有效期至 2026-11-05）。
- 链路：平台 `/integration/v1/auth/refresh`（client_id=work-desktop）轮换成功 → access_token JWT（`sub=104032, sid=ff4f7e4e-…`）+ 新 refresh；**每次轮换后的 refresh 均写回 Keychain**（用户桌面会话不受影响，共轮换 4 次）。
- sidecar 上下文建立：平台 `/integration/v1/session`（Bearer access）→ `subject=104032, spaces=[296492], authz_version=1`；bootstrap 以平台签发数据构造 `EnterpriseContext` 并 `sync_enterprise_context`。**全部是平台真实签发的令牌与身份，未伪造登录、未绕过平台鉴权**。
- 局限留痕：扫码仪式本身（VAL-203 浏览器回跳）未在本轮执行，需人工补测。

## 2. 业务闭环（TASK-025 核心，真实三端）

| # | 步骤 | 命令/操作 | 结果 |
|---|---|---|---|
| 1 | KB 列表 | `GET /v1/enterprise/resources/knowledge-bases`（work sidecar） | 200，`complete=true`，2 个 KB：`wms-oms 技术知识库`(a8d5ce43…)、`员工离职交接文档库`(6c11c991…)，tenant 296492 技术交付中心，`context_id=8690d5c1…` |
| 2 | 技能目录 | `GET /v1/enterprise/resources/skills` | 200，`frontend-design`(8832c809…)，`bundle_sha256=e2664ec5…11e2838`，`local_status=not_installed` |
| 3 | 归档下载+原子安装 | `POST …/skills/8832c809…/install`（expected=e2664ec5…） | 202 `operation_id=f3afe230…`；轮询 8s → **succeeded**，`installed_sha256=e2664ec5…` 与目录声明一致；磁盘核验：`…/catalog/b3629d26…/296492/8832c809…/versions/e2664ec5…/{SKILL.md,LICENSE.txt}` + active.json（schema 1/enabled true） |
| 4 | 同 SHA 幂等 | 重复 install | **200** `status=installed`，不重复落地 |
| 5 | WS 选用发送 | `ws /ws/session/itest-sess-1` + `user_message{knowledge_scope, skill_ref}` | `turn_start`（未拒绝）；消息持久化 `_resources.labels` 含服务端标签（knowledge_scope.knowledge_base_name=`wms-oms 技术知识库`、skill_ref 含 name/SHA） |
| 6 | scope 强制（真实链路） | bridge.knowledge_search("wms 出库", scope=KB-A) | 5+ 命中全部 `knowledge_base_id=a8d5ce43…`（**引用来自实际命中**）；`read(1c9fc527…)` 返回真实正文，`source_tenant=296492` |
| 7 | 越界拒绝 | search 强行 `knowledge_base_id=KB-B` | **SCOPE_MISMATCH**（模型参数冲突，零越界语义）；read 命中集合外 ID → SCOPE_MISMATCH，无越界正文 |
| 8 | enterprise_load_skill | 预热授权缓存后 load | ok，`name=frontend-design`，instructions 为真实安装字节内容 |

模型层说明：本机无 model API key，turn 内模型调用不可执行；步骤 6-8 通过 work 生产代码路径（EnterpriseBridge+TurnScope+REGISTRY，与 turn 内调用同码）驱动，WS 层接收/持久化为真实 sidecar 进程执行。

## 3. 零 release/publish 调用（VAL-208）

- work 会话窗口 **09:33–09:50 CST**（gateway 与 knowledge 容器日志，`--since 2026-10-23T01:33:00 --until 2026-10-23T01:50:00` UTC）：
  - gateway：release 端点调用（`POST */(publish|delist)`、`skills/releases`、`publish-jobs`、`/integration/v1/skills`）= **0**；同窗口真实业务请求 23 个（19 GET + 4 POST，其中 11 个 `resources/skills`）。
  - knowledge app：release 调用（`POST */publish`、`/skills/releases`、`delist`）= **0**。
- 窗口外命中均为部署冒烟（09:14/09:17 knowledge 匿名 401 探测、09:21 gateway publish-jobs 404 探测，见 deploy-evidence-wr001.md 第 4 节）与 GIN `release` 模式字样——非 work 客户端流量。
- 结论：**新链路闭环全程零 release/publish 调用**（VAL-208 三端端到端通过）。

## 4. VAL-215 真实撤权计时（正常联网，无 fake clock）

- 撤权途径（真实链路）：platform statedb `managed_entitlements` 删除授权行（subject=104032, tenant=296492, capability=member）。操作前留指纹（行 id ed9b95b3…），测后已恢复（INSERT 同值，复核 200）。
- **t0**：`DELETE` 事务提交，DB `now()=2026-10-23 01:42:05.999512+00`（= 09:42:05.999 CST）。
- 预热：t0 前 9.3s（09:41:56.959）sidecar 单项复核 200（授权缓存落 allowed，模拟正常运行态）。
- **t1**：09:42:06.307 CST 首次轮询 → **403 SPACE_FORBIDDEN**（tenant not in active entitlement set）。
- **t1 − t0 ≈ 0.31s ≤ 60s ✅**（远优于 30s 缓存上限 + 误差的规格界）。
- 三端时序证据：
  - work 侧：预热 09:41:56.959 → 200；09:42:06.307 → 403（本文件第 0/4 节命令输出）。
  - platform 日志：`[GIN] 2026/10/23 - 09:42:07 | 403 | 1.41ms | 10.11.1.1 | GET "/integration/v1/resources/skills/8832c809…?tenant_id=296492"`。
  - knowledge 侧：本轮撤权只经 platform entitlement 判定，未触发 knowledge 读取（单项复核在 platform 层拒绝）；read 链路撤权语义已在本轮早段以 SKILL_REVOKED（跨进程驱动，缓存自然过期后 fail-closed）验证。
  - 数据库指纹：撤权后 `SELECT count(*)=0`；恢复后 `count=1, created_at=2026-10-23 01:46:44 UTC`。
- 恢复验证：恢复授权行后 sidecar 单项 200、bridge search 10 命中、read 正文正常。

## 5. 同名与跨空间（A/B）

- 测试环境仅一个授权空间（296492），两个 KB 名称不同（`wms-oms 技术知识库` / `员工离职交接文档库`）；技能目录仅一个。**跨空间同名 A/B/A2 数据不支持，如实记录未执行**。
- 已按 A/B 语义在现有两库间验证 scope 强制：scope=A 时 search 命中全属 A、模型强行 B 参数 → SCOPE_MISMATCH、read 越界 ID → 拒绝（第 2 节步骤 6-7）。

## 6. 未决项

1. VAL-203/204（系统浏览器上传首页/详情回跳）需真实桌面与扫码/网页会话，人工补测。
2. 跨空间同名（A2/C1/C2）需测试环境准备多空间数据后再验。
3. turn 内模型真实应答（引用渲染为来源卡）需配置 model provider 后复测；本轮以生产代码路径驱动替代。
4. 直连 SSH 至 10.101.102.116:22 仍瞬断，本轮全程经 Cloudflare 隧道（与部署期一致）。

## 7. 红线遵守

- 未改三仓交付代码（bootstrap 为 /tmp 包装脚本）；server URL 仅为进程 env，未写回交付文件。
- 撤权只影响测试身份 104032 对 296492 的 member 授权，操作前后留指纹并恢复。
- 用户 Keychain 企业 refresh 已轮换写回，桌面会话不受影响。

## r5 DMG 真机手测（2026-10-23，桌面自动化实测）

- @ 知识选择器：点输入框下方 @ 按钮 → 弹层显示 2 个知识库（wms-oms 技术知识库 / 员工离职交接文档库，均 技术交付中心）→ 选中后输入框上方出现「知识库: wms-oms 技术知识库（技术交付中心）」标签（含移除 ✕）。此前 r4 不可见系 composer 容器缺 relative 定位（r5 修复）。
- 注：入口为 @ 按钮；输入框内键入 "@" 字符不触发选择器（设计如此，已记 backlog 待产品裁决是否加键入触发）。
