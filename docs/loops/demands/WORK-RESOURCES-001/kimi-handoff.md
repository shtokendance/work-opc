---
project: work-opc
document: handoff
demandId: WORK-RESOURCES-001
status: confirmed
updatedAt: 2026-10-22
---

# Kimi 接手：work-opc 知识库与 Skills 轻量入口

## 用户指令与接手状态

用户于 2026-10-22 在 Tasks v1.0 全文确认请求后回复：**“确认，后续交接给 kimi 去开发完成”**。需求、架构、规格、任务均已确认。后续由 Kimi 从 work-opc 主仓在同一接手会话中逐仓完成实施、验证和按治理收口，无需用户切到参与仓另开 SDD。

本文件确认的是用户指定的交接内容，不代表 Kimi 已经收到消息或已开始运行。本次只完成文档与批准留痕，尚未实施、运行业务测试/构建、提交、推送、合并、部署或清理数据库。没有可用的独立 Kimi 会话回执，不能把交接材料就绪写成已派发。

本次响应停在 Tasks 批准后，状态 nextPhase=tasks。**Kimi 在用户后续消息中收到本文件和“接手并继续开发”的指令后**，将该真实输入记录为独立 decision，再把 nextPhase 更新为 implementation；不要重问已经批准的范围、架构、规格、Tasks 或交接人。

## 必读的当前权威入口

先读取主仓 AGENTS.md 与其引用的 RTK 指引，所有 shell 命令加 rtk，原始工具命令使用 rtk proxy。存在 .codegraph 时先用 CodeGraph 理解/定位代码；不要自动索引参与仓。

| 内容 | 主仓相对路径 | 当前状态 |
|---|---|---|
| 唯一状态 | docs/loops/demands/WORK-RESOURCES-001/status.md | activeLoop=1、nextPhase=tasks |
| Requirements | docs/loops/demands/WORK-RESOURCES-001/loop-1/requirements.md | v1.2 confirmed |
| Architecture | docs/loops/demands/WORK-RESOURCES-001/loop-1/architecture.md | v1.0 confirmed |
| Specification | docs/loops/demands/WORK-RESOURCES-001/loop-1/specification.md | v1.0 confirmed |
| Tasks | docs/loops/demands/WORK-RESOURCES-001/loop-1/tasks.md | v1.0 confirmed，30 项未执行 |
| 长期架构基线 | docs/architecture/overview.md | v1.2 confirmed；候选实现未上线 |
| 参与清单 | docs/loops/demands/WORK-RESOURCES-001/repositories.json | full，三仓固定 release SHA |
| 工作流锁定/审计 | docs/loops/demands/WORK-RESOURCES-001/loop-1/workflow.lock.json、workflow.audit.jsonl | governance v2，事件链只追加 |
| 用户决定 | docs/loops/demands/WORK-RESOURCES-001/sources/user-decisions.md | 13 条相关输入 |
| 独立需求复核 | docs/loops/demands/WORK-RESOURCES-001/loop-1/requirements-review.md | v1.0/1.1/1.2 独立只读复核通过 |
| 分仓 Tasks | docs/loops/demands/WORK-RESOURCES-001/repositories/<repoId>/tasks.md | 主仓内三仓已确认投影 |
| Backlog | docs/backlog.md | 无关项保留；MCP 显式 @ 不在本轮 |

交接指纹见本文件末尾表。旧 OPC-INTEGRATION-001 已关闭，旧 kimi-handoff 只作历史，不作为本轮任务入口；前期 discovery 已 superseded，不能据其待确认文字重复审批。

## 产品方向：照此实施

1. 客户端知识库轻量列表/搜索/刷新；@ 或“添加知识”选择**一个**知识库，仅本条消息有效，执行层强制搜索与 read 范围；下一消息不继承。回答引用可追溯。
2. 企业技能目录/已安装展示，用户点“下载并安装”才安装，选用只准备消息，发送后才执行；个人和不同空间企业同名技能必须按稳定身份区分。
3. **上传按钮只打开 https://shtokendance.benlai.com**，用户在网页登录、选空间、上传。详情/引用定位网页对象；不新增上传深链、不传桌面 Token、不做客户端完整管理界面。
4. **去掉技能 release 发布依赖，也不能后台自动发布来保留依赖。**直接使用真实 catalog 的原始归档与 SHA；普通包不要求 opc-client.json。上传→刷新可见→手动安装→选用发送必须闭环。
5. 用户明确 code-opc 不用管，上一 Loop 旧技能 release 可直接删除。不修改/测试 code-opc，不因该仓依赖保留旧发布接口；Git release 基线分支不删除、不重选。
6. 新安装按主体/空间/目录/SHA 隔离，更新原子激活，失败保留旧版本；旧本地 publish_id 安装提示重新安装并退出有效菜单，旧目录保留作应用回退。
7. 运行中固定技能版本，停用/卸载禁止新启动，文件待引用结束回收；身份切换与撤权优先，资源级授权最大 30 秒缓存，真实 60 秒撤权实测不能省略。

未做文件/标签选择、ZIP 导出、云委托、自动组织同步、MCP @ 新入口。发现新产品/权限选择或架构事实冲突，按当前文档规则处理，不能静默扩大。

## 三仓基线与工作区保护

统一标准分支：`feature/261022_WORK-RESOURCES-001_resource-entry`；分支日期已经固定，不随接手日期重算。

| repositoryId | remote | 固定 release commit | 本仓任务 |
|---|---|---|---|
| work-opc（主仓） | https://github.com/Roger0808/work-opc.git | 7be2e4cec8b91f6bb430a332241629500d7bf5ff | TASK-015～024，主仓协调 TASK-001/002/025～030 |
| platform-opc | https://github.com/Roger0808/wecom-opc.git | 27de3c77de6be0fb08e8147450b3a449d296dd16 | TASK-009～014 |
| knowledge-opc | https://github.com/Roger0808/OPC-2.0.git | 071fd96c4295d0925244615f7d8a73e9544f26d0 | TASK-003～008 |

基线已核实并获批，无需重新询问 main/master/release 选择。接手时检查实际 HEAD/工作区漂移，若已变化则保留真实状态并说明，不静默更改固定基线。通过当前环境的工作区目录绑定这三个 repositoryId，本文件不把本机绝对路径写入审计或清单。

目前各仓都在 release，尚未创建本需求实施分支；manifest.actual=null/disposition=pending 是真实未执行状态，不能直接填 reused/created。TASK-001 要先隔离再修改。

**work 原来就存在的四个暂存改动必须保留：**coworker/agent.py、coworker/headless/runner.py、docs/headless.md、tests/test_headless_run.py。本轮文档也在暂存区，不能一并提交或用 git add -A。agent.py 与将来任务可能相交，应从固定 commit 的隔离工作树做本轮差异，避免把旧改动夹带进交付。

**platform 未跟踪内容必须保留：**AGENTS.md、CLAUDE.md、docs/loops/。knowledge 交接时工作树干净。接手再检查后续漂移，不 stash/reset/clean；不删除无关内容。

本轮权威文档尚未提交，隔离时必须将本需求目录、docs/architecture/overview.md、docs/backlog.md 与本次 superseded discovery 的确切内容安全带入本需求工作树，保持已经锁定的 workflow/audit 原字节；不要重新 workflow start、生成另一个 run 或丢失审计链。只迁移本轮明确路径，不搬走原来四个用户文件。

## 第一轮接手动作

1. 加载用户指定来源的 sdd-solo，读取当前状态和已锁定 workflow，不重新选择路线。
2. 运行 `rtk proxy sdd-loop capabilities --require solo@1 --host agents` 和 `rtk proxy sdd-loop capabilities --require governance@2 --host agents`；通过后 `rtk proxy sdd-loop check --demand WORK-RESOURCES-001`。身份必须读当前 Git user.name/email，按状态角色核验。
3. 确认四阶段 confirmed、review 指纹与本需求锁定目录完整；保留文档原确认及用户来源，不重复访谈。
4. 收到用户独立接手指令后，用临时 event JSON 写 decision，summary 说明 Kimi 继续实施、confirmation 保存真实输入，调用 `rtk proxy sdd-loop workflow record --run docs/loops/demands/WORK-RESOURCES-001/loop-1 --event-json <临时文件>`。不得编造 Continue 原话。
5. 将状态 nextPhase 更新为 implementation；执行 TASK-001，逐仓准备安全分支/工作树，记录真实 implementation 基线后从 TASK-002 开始。仅在实际实施阶段创建实施/验证文件。

## 代码与验证容易遗漏的点

- knowledge resolveCatalog 会投影旧安装，catalogBundleArchive 会找旧安装包。新归档下载必须严格真实 catalog + 权威归档，不沿用这种回退让已删目录复活。
- 旧 work installer 注释称 atomic，但实际先 rmtree 再 move，不能当作失败保留旧版的实现依据；要同盘 staging/SHA 目录/active 原子切换并测崩溃。
- 旧授权刷新只查 session，不能证明单个 catalog 未撤权；新链路需要资源级复核，身份/租户/对象/版本缓存隔离。
- 不能只用 prompt 约束知识范围；search 参数与 read 实际文档归属都校验，turn/子任务/重试/恢复携带同一范围；scope 不写共享 session 全局变量。
- 技能同名不能靠 SkillLoader 的 name 后写覆盖；企业 skill_ref 实际加载正确路径，个人 /name 保持原行为。
- 正常 Viewer 的新目录归档下载不等于放开 Admin 文件预览/上传/沙箱环境变量权限。
- 页面 200/health/源码 SHA 不证明网页登录、上传、DMG `/callback` 或撤权闭环。spec 的 VAL-201～219 与 PBT 四属性要有真实对应证据。
- knowledge 当前最大迁移 versioned=000132、SQLite=000046；platform=000016。实施前核对空闲新号，只做新增前向清理，不改旧迁移；SQLite 000046 同含 MCP，不能整体 down。
- 旧表仅 skill_release 与 platform statedb.skill_publish_jobs；删归档须先做专属对象与共享引用清单，备份和代表性存量数据恢复演练。不能清空 tenant-skills 前缀，也不能用空表 down 假装数据恢复。

完整任务顺序与完成条件以 Tasks 为准。运行每仓自己测试及构建；基本入口：work pytest + GUI Vitest/tsc/Vite/Playwright；platform go test/go build；knowledge Go 定向包与 frontend type-check/build/tsx tests。不要把已验证的测试无理由反复全量重跑，失败/新增差异驱动补测。

## 后续治理、授权和交付

锁定 workflow 顺序：implementation → deployment → manual_test → verification → final_test → reconciliation → review → signoff → close。所有事件在当前 run 的 workflow.audit.jsonl，只通过 CLI 追加，不修改旧 JSONL。

- 用户已选定 Kimi 负责后续开发，不需要再次选实施者。但独立代码 reviewer 路径仍按 sdd-review 在正式审查前确认，本体不得自审；不可用则 NOT_REVIEWABLE_SAFELY。
- 用户尚未授权 Git 提交/推送/合并或实际共享/生产环境部署、数据库清理执行。先完成可评审代码/验证/上线清单，需相应外部动作时依据准确目标与既有授权处理；不能把本交接当全环境授权。
- 三仓 repository unit 的 implementation/verification/review/receipt 写真实本仓事实；未就绪不填 ready=true。四工程扩展需证据，skip/环境缺失不算 PASS。
- 维护唯一 release-checklist，SQL/配置/顺序/回滚实际落实后才 ready，未执行不勾选。knowledge→platform→work 顺序与旧写入停用/备份条件一致。
- Solo full 收口：每仓独立 review/receipt → 冻结主仓来源 → 非主仓追溯包提交并从提交校验 → 跨仓聚合审查 → solo check --write-receipt → candidate → 锁定图的 final_test/reconciliation/review/signoff/close。必要提交需实际授权后才能做，不可虚构追溯 commit。
- 关闭前使用真实 delivery 范围与 provenance 检查，候选后修改补测/复审；不缩范围消红，不把尚未上线写成已部署。旧 Loop 不重开或篡改。

最终向用户明确区分已编码、已测试、已提交、已部署、待授权与未完成项。当前交接时只有四阶段文档已确认，其余 TASK-001～030 均未完成。

## 交接时文件指纹

以下为交接时 SHA-256；后续合法实施或文档更新须按治理重新固定证据，不能把变化后的文件冒充本表版本。

| 文件 | SHA-256 |
|---|---|
| Requirements v1.2 | `bfb4eb8234de5584bb338293736dd8100f4acb65cebb987ba3515a86fbc4e484` |
| Architecture v1.0 | `69a665b1cf96f543d211b36fe262acb481f206ff357b9e61fde7e4d86ca75e74` |
| Specification v1.0 | `7c02280789bf0fa324e376f8eb4bcaec5d263d5d2fc62198dc4e79cc65002f83` |
| Tasks v1.0 | `71edce650f713d8898c6a065026e51bfaadb13f17ffa54d48e4ed42e7676519f` |
| Baseline v1.2 | `a275ff536e75808c672b9f0550117737227d9b341b80f23f6a50da80c1b3210b` |
| repositories.json | `5dc10ed4378d7cf586d200b21e27c2be44cdf83aa56e1e81f7967533432f56e6` |
| workflow.lock.json | `b737807138d278abc760db11bc37685d45dbb35b7d3481a49b2af26d2fbad28c` |
