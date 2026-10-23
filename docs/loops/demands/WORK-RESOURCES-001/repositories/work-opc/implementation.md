---
project: work-opc
document: implementation
demandId: WORK-RESOURCES-001
repositoryId: work-opc
loop: 1
status: draft
updatedAt: 2026-10-22
---

# work-opc 实施记录

## 基线与分支（TASK-001）

- remote：`https://github.com/Roger0808/work-opc.git`
- 固定基线：release @ `7be2e4cec8b91f6bb430a332241629500d7bf5ff`（固定 SHA 经 git rev-parse 核验一致）
- 标准分支：`feature/261022_WORK-RESOURCES-001_resource-entry`，从固定基线新建（disposition=created）
- 隔离方式：专用 worktree，从固定 commit 检出；原工作区四个用户暂存改动（coworker/agent.py、coworker/headless/runner.py、docs/headless.md、tests/test_headless_run.py）保留在原工作区，不带入本工作树
- 本轮权威文档（需求目录、docs/architecture/overview.md、docs/backlog.md、docs/integration/2026-10-22-work-resources-discovery.md）按字节拷贝带入本工作树；workflow.lock.json / workflow.audit.jsonl 与主工作区拷贝逐字节一致
- 任务范围：TASK-002 + TASK-015～024；主仓协调 TASK-001/002/025～030
- 实施期间环境异常（如实记录）：本机存在周期性清理进程多次删除隔离工作树内容（worktree、/tmp 与 $HOME 下的副本均受影响，金丝雀目录与少文件目录不受影响）。应对措施：全部代码内容经会话工具记录回放恢复；以 `chflags -R uchg` 防删除窗口完成测试。交付内容与本记录以最终工作树为准。

## 实施过程

跨仓依赖：platform 新 resources 家族与 knowledge 目录归档路由（只读参考 `/Users/yangjing/code/platform-opc.wr001/pkg/handler/resources.go`、`knowledge.go`）；契约两端错误信封按 INT-105。

1. **TASK-002 夹具**：`coworker/testing/fake_opc.py` 新增 mint_token/entitled 身份模型（U1/U2/U3、T1/T2）、KB A/B/A2、C1/C2 同名目录、h1/h2 真实字节包（`build_skill_package`+`sha256_hex`，哈希由包字节计算）、scope 模式检索/读取（权威归属校验）、目录移动/归档替换/超时/断连/坏 hash/部分空间失败注入；旧 release/publish 路由 404 且记入 tripwire（`assert_no_release_calls`）。
2. **TASK-016**：新 `enterprise/installer.py`。标准 SKILL.md 原始归档；256MiB/100k 条目/20k 文件/32MiB/512MiB 上限；拒绝绝对路径、盘符/UNC、..、反斜杠、符号链接、规范化重复路径、多技能根；同盘 staging → 版本目录 → `active.json` 原子指针（os.replace）；任何失败旧 active 完整保留；首次安装 enabled=true、更新保留 enabled、同 SHA 幂等；崩溃残留 staging 可清理且永不被读成安装；写 active 失败不触碰旧记录；不执行包内脚本。
3. **TASK-017**：新 `enterprise/registry.py`。结构化 SkillRef（主体分根/tenant/catalog/SHA）稳定定位，个人 name 行为不变、无 name 后写覆盖；turn 通过 `acquire` 固定 SHA，更新/卸载后运行中 turn 仍解析钉住版本（版本目录在盘即可），引用归零回收；`authorize` 回调每次 load 复核（撤权 > 钉版）；停用/卸载立即禁止新启动；身份切换 drop_identity。
4. **TASK-015/020**：新 `enterprise/resources.py`。platform 资源家族客户端（列表 15s、归档独立预算 120s/连接 15s、三向 hash 校验）；结构性 404 → 501 RESOURCE_API_UNSUPPORTED，单资源 404 保持 RESOURCE_NOT_ACCESSIBLE；`ResourceAuthz` ≤30s 缓存绑定 context key/tenant/资源/版本，失效/未知/失败关闭，切换立即清理；`OperationService` 同主体+tenant+catalog 互斥、同 SHA 幂等 202、不同 SHA 409、5 分钟预算、下载并发上限 2、迟到响应在激活前复核 context（不跨身份落地）、操作归属当前身份；安装授权钉 expected SHA（版本漂移即 409）。操作在守护线程跑完（TestClient 门户拆除不吞任务）。
5. **TASK-018**：新 `enterprise/scope.py` + `tools.py` 改造。bridge 按 session 存 turn scope（非进程全局）；search 省略参数补固定值、冲突参数 SCOPE_MISMATCH 且零上游请求；read 限本 turn 命中集合 + URL 携带 scope + 平台权威归属复核（元数据/正文同次读取校验，移动即拒）；结束/取消释放；下一无 scope 消息恢复缺省；身份切换 scope 立即失效。
6. **TASK-019**：`server/app.py` user_message 接收 knowledge_scope/skill_ref（形状/长度/互斥/context_id/可见性/安装状态/版本/授权全校验，拒绝带 INT-105 码 + keep_draft，不启动 turn）；`engine.run` 新增 `resources` 参数，`_resources` sidecar 持久化并在 `_outbound_messages` 剥离（provider 永不收到内部字段）；服务端生成标签（`resource_labels` + 显示 chip）；发送后清选择；历史无字段按原样。
7. **TASK-023**：删除自动发布同步与 publish_id 新安装路径（`sync_enterprise_catalog`/release fetch/install 全删）；登录/刷新/列表零下载（测试断言）；旧 publish_id 目录保留并显示 legacy_requires_reinstall，不进 registry、不扫描为新 catalog；个人目录不动。
8. **TASK-021/022（GUI）**：新 `enterpriseResources.ts`（类型/客户端/精确同源门/轮询）、`useEnterpriseState.ts`、`ResourcesView.tsx`（知识库列表五态/搜索/分页/刷新/使用仅准备/详情/上传首页）、`EnterpriseSkillsPanel.tsx`（企业技能/已安装视图，安装进度、更新/停用/卸载、not_ready 禁用、legacy 提示）、`KnowledgeScopePicker.tsx`（@ 添加知识单选，Esc 不改选择，键盘上下/Enter）、`ResourceChips.tsx`（消息标签）；`api.ts` userMessage 带 scope/skill_ref；`Composer.tsx` 标签 chip/发送规则/草稿持久化；`App.tsx` resources surface + 发送接线；`Sidebar.tsx` 知识库入口；`Transcript.tsx`/`itemsFromMessages.ts`/`types.ts` 标签渲染；i18n zh/en。
9. **TASK-024**：9 个企业测试文件 + 4 个 GUI 测试文件；P1～P4 固定 seed 属性；个人技能/persona/session 回归。

## Change surface

代码/配置（相对基线，共 35 文件）：

- 新增：`coworker/enterprise/installer.py`、`coworker/enterprise/registry.py`、`coworker/enterprise/resources.py`、`coworker/enterprise/scope.py`；`tests/test_enterprise_resources.py`、`tests/test_enterprise_scope.py`、`tests/test_enterprise_installer.py`、`tests/test_enterprise_registry.py`、`tests/test_enterprise_ws.py`；`surfaces/gui/src/enterpriseResources.ts`、`surfaces/gui/src/useEnterpriseState.ts`、`surfaces/gui/src/components/{ResourcesView,EnterpriseSkillsPanel,KnowledgeScopePicker,ResourceChips}.tsx` 及 4 个测试文件、`surfaces/gui/src/enterpriseResources.test.ts`
- 修改：`coworker/testing/fake_opc.py`、`coworker/enterprise/{auth,tools,skills,__init__}.py`、`coworker/server/{app,manager}.py`、`coworker/engine.py`、`tests/test_enterprise_{tools,context,skills}.py`、`surfaces/gui/src/{api.ts,itemsFromMessages.ts,types.ts,useEnterpriseState.ts}`、`surfaces/gui/src/components/{Composer,Sidebar,Transcript,composerDraft}.tsx(ts)`、`surfaces/gui/src/{App.tsx,locales/zh.json,locales/en.json}`
- 删除：旧 `enterprise/skills.py` 中 publish_id 安装/自动同步/release 客户端（约 400 行）；`enterprise/__init__.py` 对应导出
- 数据：无 SQL/对象存储变更（本仓）；会话 JSONL 新增 `_resources` sidecar（旧记录无字段按缺省）
- 接口：新增 7 个 sidecar 资源路由（INT-101/103）；WS user_message 扩展 knowledge_scope/skill_ref（INT-102）；provider 消息结构不变
- 部署：无新增服务/端口；资源域名以受信常量进 GUI（`TRUSTED_WEB_ORIGIN`），不由项目目录覆盖
- 文档：本文件 + `verification.md`（新建）

## 验证摘要

见 [verification.md](verification.md)：pytest 162 passed；GUI 定向 22 passed；PBT P1～P4 固定 seed 全 PASS；Playwright/真实浏览器/DMG/60s 撤权计时未执行（列明原因）。

## 独立 Review 修复轮（2026-10-23，CHANGES_REQUIRED → 修复完成）

### Blocker
- **B1** installer：staging 哨兵改 `None`（原 `Path()` 使 `str()=='.'`，finally 的 rmtree 会清空进程 CWD）；新增 `_rmtree_staging_guarded`——resolve 后前缀校验 + 拒绝非 managed-root/非 `.staging-*` 路径。测试：成功/失败/更新安装后 CWD canary 存活；守卫拒绝 CWD 与 versions 目录。
- **B2** registry：授权门传 `ref.bundle_sha256`（原 `""` 与落键的具体 current_sha 永不命中 → 生产 enterprise_load_skill 必得 SKILL_REVOKED）；`ResourceAuthz.check` 增加通配回退、成功授权双键落账。端到端测试：WS 接收 scope/skill_ref → 门控 turn 内 `enterprise_load_skill` 真实成功（此前该路径从未被测）。

### Major
- **M1** Composer：发送门恢复"无正文且无附件仅个人技能可发送"；scope-only / skillRef-only 均拒发；测试接到真实组件实例的 onSend（原断言从未接入的 baseProps.onSend，空转）。
- **M2** picker 键盘提升到 Composer 层：textarea onKeyDown 拦截 ArrowUp/Down/Enter/Esc（Enter 选中不发送、Esc 关闭不改选择）；picker 改为受控 highlight + onRows 上报。
- **M3** retry 恢复：`_last_turn_meta` 记忆 + 断线后从引擎 `_resources` sidecar 重建 scope/ref（含钉住 SHA）；身份切换时 turn_scope 的 key 校验再次拒绝。
- **M4** `_display` 只保留用户正文（原拼标签导致刷新后气泡只剩标签）；标签全由 ResourceChips 从 `_resources` 渲染（顺带解决服务端硬编码中文）。
- **M5** PATCH enabled 增加 OPERATION_IN_PROGRESS 409；`installer.set_enabled` 与 install 共用 `_install_lock`（读-改-写不再复活旧 active_sha256），测试用慢 unpack 线程验证等待与作用对象。
- **M6** 引用计数接通：`load_enterprise_skill` 按 ref 去重 acquire 并记录 `_turn_pins`；turn 结束 `clear_turn_ref` 逐一 release；卸载前 `pinned_versions` 非空 → 409，引用归零后卸载成功且目录回收。
- **M7** 远端会话守卫：`remoteSessionResourceBlock()`（纯函数）拦截"远端执行会话携带本机 scope/技能"，App.send 展示提示且不发送；单测覆盖远/本/空三态。WS 接收侧：sidecar 仅本机 GUI 接入 user_message，远端执行指令不经此通道（记入留痕）。

### Minor
- install 错误映射表：SESSION_REVOKED→401 / UPSTREAM_UNAVAILABLE→502 不再压成 404（INT-105）。
- `unpack_safe` 条目级 BadZipFile → SKILL_PACKAGE_UNSAFE（422 家族）而非 500。
- `validate_scope_object` 拒绝未知额外字段（规格字段固定）。
- 服务端 chips 硬编码中文移除（随 M4）。
- registry：运行中 turn 在停用后可完成钉住版本（active 分支仍拒新启动）；撤权仍最高优先。

### 修复轮验证
- `pytest tests/test_enterprise_*.py`（9 文件，worktree 内 .venv，CWD=worktree 根）：**94 passed**，exit 0。
- GUI：`npx vitest run` 全量 **583 passed / 3 failed**（3 个为基线预存 enterprise.test.ts 失败，已核实非本轮引入）；定向（含新增 M1/M2/M7 用例）**32+18 全过**；`npm run build` exit 0。

## 增量：企业技能并入 Composer「/」菜单（2026-10-23，用户批准后实施）

- **后端** `manager.session_skills_view`：已登录企业上下文时并入已安装企业技能（`scope="enterprise"`，含构造 skill_ref 的 tenant_id/catalog_id/bundle_sha256/version/context_id 元数据）；同名个人技能与企业技能并存不做覆盖；个人 Settings 禁用与 session mute 不影响企业行；未安装目录不进菜单；空上下文保持原行为；合并列表按名称混排（sorted 稳定）。**`effective_skill_names` 有意不合并**：它是 name 键控的 load_skill/WS `skill` 校验路径，混入企业名会让个人 name 参数被重新解释为同名企业技能（BND-007 禁止「既有个人 name 参数不被重新解释为同名企业技能」）——与任务意图（菜单数据源）的偏差在此显式记录。
- **前端** `Composer.tsx`：slash 弹窗企业行渲染「企业」徽标（i18n zh/en，同名可辨）；pickSkill 分叉——个人不变（`/name ` 文本注入），企业走 `onSkillRefChange` 设置 skill_ref 标签（与企业技能面板「使用」同一已验证链路），不注入文本；选择后清查询关弹窗；弹窗每次打开重新 fetch（企业上下文变化后自然刷新）。`SessionSkillRow` 扩展 enterprise 元数据字段。
- **附带修复**：`enterpriseResources.request`/`useEnterpriseState` 的 fetch 恢复相对路径（M7 轮误改为 httpBase 绝对路径，破坏既有测试 mock 约定；浏览器按 origin 解析等价）。
- 红线遵守：未动 WS 协议、执行层（enterprise_load_skill 链路不动）、旧客户端/旧安装目录。

### 纠正记录（2026-10-23，httpBase 回退错误已修正）

上一增量把 `enterpriseResources.request`/`useEnterpriseState` 的 fetch 回退为相对路径，理由「M7 轮引入的绝对路径回归」是**错误判断**，已纠正：

- **事实**（r1 DMG 真机联调，用户证据）：桌面 WebView 中相对路径 fetch 解析失败被 catch 吞掉，sidecar 日志零记录，知识库/企业技能视图永远停在未登录；`httpBase()` 绝对地址才是生产正确行为（与 api.ts 全量请求一致）。
- **修正**：两文件恢复 `import { httpBase } from "./api"` + 绝对地址 fetch（KnowledgeScopePicker 经 enterpriseResources 间接请求，无需改动）。
- **测试假设修正**（不再迁就测试破坏生产）：`EnterpriseSkillsPanel.test`/`ResourcesView.test`/`Composer.resources.test`/`Composer.enterprise-skills.test` 的 beforeEach 注入 `(globalThis).__COWORKER_HTTP__ = "http://sidecar.test"`（afterEach 清除），panel mock 的 URL 匹配改为 `includes`。新增教训：**测试 mock 的 URL 形态假设必须对齐生产 httpBase 行为**。
- 验证：GUI 定向 32 passed；GUI 全量 587 passed / 3 failed（enterprise.test.ts 基线预存 3 例）；`npm run build` exit 0。
