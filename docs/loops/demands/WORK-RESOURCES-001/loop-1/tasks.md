---
project: work-opc
document: tasks
demandId: WORK-RESOURCES-001
loop: 1
version: "1.0"
status: confirmed
updatedAt: 2026-10-22
---

# work-opc 资源入口：实施与验证任务

依据已确认的 [Requirements v1.2](requirements.md)、[Architecture v1.0](architecture.md)、[Specification v1.0](specification.md)。本任务文档待用户确认；所有任务均未执行，不把源代码勘察视为实现通过。用户此次 Continue 仅授权任务拆解。

唯一总体任务在此维护。分仓 tasks 位于本需求 `../repositories/<repositoryId>/tasks.md`，仅是主仓内本仓任务投影，不在参与仓另开 Loop。后续实施/验证/审查文档到实际阶段再建，不预写 receipt 或通过结论。

## 范围与勘察事实

本次对 work 的 SessionManager/effective_skill_names/sync_enterprise_context 与 app user_message、knowledge 的目录归档/路由做 CodeGraph 定位，对 platform main.go、skills handler/upstream 和三仓测试脚本做只读核对。源代码位置相对各仓根；三仓固定 remote/commit 仍以 ../repositories.json 为准，不重新固定基线。

- work 当前在 release，已有四个用户暂存路径：coworker/agent.py、coworker/headless/runner.py、docs/headless.md、tests/test_headless_run.py；本轮文档另行保留。不得通过 stash/reset/全量暂存搬走它们。
- platform 当前 release，有未跟踪 AGENTS.md、CLAUDE.md、docs/loops/；不得清理或计入交付。
- knowledge 当前 release；新目录下载必须严格解析真实 catalog，不能复用 resolveCatalog 的旧安装投影或 catalogBundleArchive 的旧安装回退，导致已删目录仍能下载。
- 当前迁移最大序号：platform 000016；knowledge versioned 000132、SQLite 000046。计划新清理迁移分别采用 000017、000133、000047；实施前若已被其他工作占用，递增到实际空闲编号并同步文档/测试，不覆盖已有迁移。
- knowledge frontend 脚本为 type-check、build、test（tsx --test）；work GUI 为 test（Vitest）、build（tsc+Vite）、e2e（Playwright）。Go 侧测试依赖实际环境，不把 missing dependency/skip 算作通过。

## 执行顺序

按 TASK-001 隔离 → TASK-002 验收样例 → knowledge 任务 003～008 → platform 任务 009～014 → work 任务 015～024 → 跨仓任务 025～030 顺序推进；每项具体依赖另列。该顺序是单会话逐仓实施，不自动启动并行 Agent。

总体发布顺序 knowledge → platform → work；SQL/旧写入停用时点受规格 BND-011 约束。code-opc 不纳入任务或验证范围。未授权提交、推送、合并、生产操作或共享环境部署；任务批准不自动代表上述操作授权。遇到需要这些动作时，先完成可评审材料，再按现有授权执行。

## 工作区与共享验收基础

### TASK-001 实施前隔离与基线登记

- 归属：主仓协调，三仓各自记录；依赖：Tasks 批准后的独立继续授权。
- 引用：REQ-009、ADR-001/002、规格 BND-001/011。
- 位置：repositories.json、各仓 Git 工作区、本需求各 repository unit 的 implementation.md。
- 完成条件：从已固定 release SHA 准备标准分支 `feature/261022_WORK-RESOURCES-001_resource-entry` 或有证据的实际分支例外；优先复用适合本需求的隔离工作树。保留原工作区暂存/未跟踪文件；主仓本轮文档通过明确路径转入本需求隔离工作区，不带用户四个无关改动。逐仓登记真实基线、分支与任务范围，无绝对工作树路径写入文档。
- 验证方法：逐仓 git status、rev-parse HEAD、branch 与固定基线比对；核对原工作区索引/文件指纹未受影响，清单 actual/disposition 不再为空。隔离不安全时停止相关实施，不擅自 stash。

### TASK-002 建立可区分资源与故障样例

- 归属：主仓协调，各仓测试 fixture；依赖：TASK-001。
- 引用：规格 DATA-101～103、VAL-101/102、VAL-201～219。
- 位置：work tests/coworker/testing/fake_opc.py、platform pkg/handler 测试夹具、knowledge catalog/router 测试夹具。
- 完成条件：U1/U2/U3、T1/T2、A/B/A2、个人/C1/C2 同名样例与 h1/h2 真实字节 hash 可复现；原始包无 release/opc-client.json。可注入身份切换、目录移动、归档替换、超时、active 写失败、部分空间失败。每仓夹具真实验证自己边界，不把别仓 fake 成功当联调通过。
- 验证方法：运行 fixture 自身覆盖的关键行为与后续消费测试；h1/h2 必须由包字节计算，不能伪造固定 hash。

## knowledge-opc

### TASK-003 实现严格目录原始归档读取

- 依赖：TASK-002；引用：REQ-005/006、ADR-005、规格 BND-006、INT-103/105。
- 位置：internal/application/service/tenant_skill_catalog.go、对应接口与归档测试。
- 完成条件：按 tenant+catalog_id 查真实未删除目录，读取其权威 BundleRef，核验 expected/current/actual SHA，返回原始字节。禁止旧安装投影、同名包、沙箱运行文件或 release 回退；没有专用 manifest 的标准包可下载。缺失/坏包/竞争更新返回规格错误，内部 BundleRef 不外泄。
- 验证方法：VAL-208/210/211；真实 catalog 与仅存在旧安装投影分别测试，删除目录后缓存/旧安装均不能复活下载，包字节不被重新打包。

### TASK-004 暴露目录归档路由并保持权限边界

- 依赖：TASK-003；引用：REQ-008、ADR-007、规格 INT-103/105、DATA-105。
- 位置：internal/handler/skill_catalog.go、skill_handler.go、internal/router/routes_agent.go、router/handler 测试。
- 完成条件：新增 Viewer+ 目录归档 GET，校验当前空间成员及资源归属；无权/不存在一致处理，正确 Content-Type/X-SHA256/大小限制。Admin 文件预览、上传和云沙箱安装权限保持，API key 路由能力声明与实际权限一致。
- 验证方法：VAL-202/209/211；Viewer 下载可见目录成功，跨空间、撤权、伪造参数、普通用户访问管理文件接口均拒绝；服务错误不被包装成 ZIP 成功。

### TASK-005 网页详情定位与登录返回

- 依赖：TASK-002；引用：REQ-002/004、ADR-001/007、规格 BND-003/009。
- 位置：frontend/src/router/index.ts、views/toolbox/Toolbox.vue、views/settings/SkillSettings.vue、KB 页与 auth/空间状态相关模块。
- 完成条件：技能 catalog_id 精确打开目标详情，tenant_id 只用于通过成员校验后的空间选择；KB/引用路由同样保留资源目标。未登录后登录可返回同 origin 目标；无权/删除明确失败，不按同名替代。不为上传首页新造上传深链。
- 验证方法：VAL-203/204；网页路由/状态测试及后续实际浏览器手测，包含不同网页账号、跨空间同名资源、恶意 return URL。frontend `npm run type-check`、`npm run build`；定向 `npm test -- <实际测试文件>`。

### TASK-006 删除 knowledge 技能 release 代码链路

- 依赖：TASK-004；引用：REQ-009、ADR-008、规格 BND-010/011。
- 位置：internal/handler/skill_release.go、application/service/skill_release.go、repository/skill_release.go、types 的 release 类型、container/container.go、router 注册及 catalog 删除判断；实际引用清单以实施定位为准。
- 完成条件：删除发布、release 列表/详情/archive/delist、DI 与 release 专属测试；目录删除不再检查 release 账本。保留 catalog 上传/云沙箱安装、通用归档工具、MCP 和原有共享权限测试；旧路由确实 404。历史迁移与旧审计不改写。
- 验证方法：VAL-216；对旧路径逐个断言 404，Go 编译及 catalog/MCP/RBAC 定向回归；源码/DI 搜索没有可执行 release 发布依赖，历史 SQL/归档里的术语不要求删除。

### TASK-007 knowledge 清理迁移与恢复工具

- 依赖：TASK-006；引用：ADR-008/DATA-001、规格 BND-011、VAL-217。
- 位置：migrations/versioned、migrations/sqlite、迁移测试与定向备份/恢复脚本。
- 完成条件：新前向迁移只 drop skill_release 及其专属索引，配套结构 down；不触动 catalog/tenant_skills/MCP/身份表。备份 schema/行/archive_ref 清单、校验恢复；release 专用对象清理有白名单与共享引用保护，默认不在迁移中递归删对象。
- 验证方法：临时 PostgreSQL/SQLite 空库和代表性旧行库分别 up/down/恢复，验证行数/hash/引用、事务失败恢复、共享归档保留；SQLite combined 000046 的 MCP 表前后不变。没有恢复证据不能运行实际目标库清理。

### TASK-008 knowledge 构建与本仓验证证据

- 依赖：TASK-003～007；引用：ACC-001/002/004/005、规格 VAL-202/204/208/210/211/216/217、VAL-102。
- 位置：本仓 Go/前端测试及主仓 repository unit verification.md。
- 完成条件：归档大小/路径边界、权限、目录更新/删除竞争、网页定位、旧链路删除和迁移恢复有逐项证据；测试实际跑过，skip/缺环境单列。包解析属性输入复用规格 P4，记录 seed/case。
- 验证方法：`go test ./internal/handler ./internal/router ./internal/application/service ./internal/application/repository` 按实际依赖运行，必要时先定向 `-run` 定位后完整受影响包；frontend type-check/build/定向 test。记录命令、环境、退出码、未覆盖项及本仓指纹。

## platform-opc

### TASK-009 资源列表与单项目录接口

- 依赖：TASK-002/004；引用：REQ-001/005/008、ADR-003/005、规格 DATA-101、BND-002、INT-101/105。
- 位置：pkg/handler 新资源 adapter、knowledge_upstream.go、main.go。
- 完成条件：实现 KB/catalog 列表和 catalog 单项，按当前 entitlement 的个人桥查询；名称搜索、排序分页、complete/failures 与 partial 提示依据正确。丢失空间与请求失败区分；不查 release 或发布状态，不返回存储位置/沙箱配置。
- 验证方法：VAL-201/202/208；多空间同名、无权过滤、分页去重、部分/全部失败与资源删除；Fake upstream 明确禁止收到 release/publish 调用。

### TASK-010 原始归档下载代理

- 依赖：TASK-004/009；引用：REQ-006/008、ADR-005/007、规格 INT-103/105、BND-006。
- 位置：pkg/handler resources adapter、knowledge_upstream.go、对应测试。
- 完成条件：携个人身份调用新 catalog archive，转发 expected SHA，三种 hash 保持一致；流中断/超限不返回假 ZIP。版本冲突、未就绪与无权按规格映射，支持实际下载预算，不复用 15 秒元数据超时误杀全部大包。
- 验证方法：VAL-209/210/211/218，包含不可信 Content-Length、途中断连、坏 hash 和归档字节完全一致。

### TASK-011 平台受限知识读取

- 依赖：TASK-002；引用：REQ-003/004/008、ADR-004、规格 BND-005、INT-104。
- 位置：pkg/handler/knowledge.go、knowledge_upstream.go 及 knowledge_test.go。
- 完成条件：显式 scope 模式只用指定 tenant/KB；取正文的权威结果校验实际归属后再输出，scope 错配不泄露正文。当前无范围调用保持既有行为；不能遍历其他空间找到同 ID 补结果。
- 验证方法：VAL-205/207；文档移动、不同空间相同标识、KB 删除/撤权、元数据与正文归属变化；断言越界正文未出响应且无额外检索。

### TASK-012 删除发布协调与旧 release 分发

- 依赖：TASK-009/010；引用：REQ-009、ADR-008、规格 BND-010。
- 位置：main.go、pkg/handler/skills.go、knowledge_upstream.go、发布 job model/storage 与专属测试。
- 完成条件：删除旧 skills/internal skills 发布聚合、publish_id 下载、admin publish-jobs；保留共享 resolveAccessSession/entitlement/error/bridge 能力供新 adapter，旧路由 404。code-opc 不改、不保留兼容。
- 验证方法：VAL-216；旧路径全表路由断言、新 resource 契约测试，以及知识/MCP/登录鉴权定向回归；删除后 Go 编译无悬空引用。

### TASK-013 platform 旧账本清理与恢复

- 依赖：TASK-012；引用：ADR-008/DATA-001、规格 BND-011、VAL-217。
- 位置：migrations 新序号、pkg/storage 迁移测试、备份/恢复工具。
- 完成条件：仅删除 statedb.skill_publish_jobs；新迁移不改历史 000011，不改 knowledge 主库、session/entitlement/audit 表。空库和有任务行环境均可恢复，迁移失败停止发布，备份元数据不含秘密。
- 验证方法：临时真实 PostgreSQL up/down 与从备份恢复，校验旧行/约束和其他关键表；不是只跑 empty down 创建空表。

### TASK-014 platform 测试、属性与构建

- 依赖：TASK-009～013；引用：规格 VAL-201/202/205/207/208/210/216/217/218、VAL-102。
- 位置：pkg/handler、pkg/storage、main.go 相关测试；本仓 verification.md。
- 完成条件：个人授权、KB scope、资源归档与错误映射、旧路由退役、存量迁移恢复有证据；属性 P1/P2 使用固定 seed，测试不放宽原权限断言。
- 验证方法：`go test ./pkg/handler ./pkg/storage ./pkg/kbridge`，受影响包通过后 `go test ./...` 与 `go build ./...`；外部依赖缺失如实记录，不能算通过。

## work-opc

### TASK-015 sidecar 资源 API 与操作服务

- 依赖：TASK-002/009/010；引用：REQ-001/005/006、ADR-003、规格 INT-101/103/105、DATA-104。
- 位置：coworker/enterprise/resources.py（新增）、server/app.py 路由与 manager 编排、enterprise API 测试。
- 完成条件：实现列表/单项授权/安装请求/启用卸载/操作查询，sidecar token/origin 与 context_id 强校验；同资源操作互斥/同 SHA 幂等、不同 SHA 冲突；202 不作成功，有界轮询/恢复，迟到响应不跨身份落地。
- 验证方法：VAL-201/209/210/214/218，含未知新协议 501、单资源 404 区分、操作所有者与超时恢复；新增 FastAPI/资源服务定向 pytest。

### TASK-016 标准技能包校验与原子安装

- 依赖：TASK-015；引用：REQ-006/007、ADR-005/006、规格 DATA-103、BND-006、INT-103。
- 位置：enterprise/skills.py 或独立 installer、受管文件记录和测试。
- 完成条件：支持标准 SKILL.md 原始归档，不依赖 opc-client.json/publish_id；严格 zip/path/size 校验、身份分根、SHA 版本目录、同盘 staging 与 active 原子切换。首次安装启用、更新保留 enabled；任何失败保留旧 active，不先 rmtree 原目录；不执行包内脚本。
- 验证方法：VAL-209/210/211/214、P3/P4；对 rename/active 写失败、崩溃残留、恶意全部条目、磁盘失败和超限做注入；真实 h1/h2 包逐字节核验。

### TASK-017 稳定技能 registry 与版本引用

- 依赖：TASK-016；引用：REQ-007、ADR-006、规格 BND-007、DATA-102/103/104。
- 位置：skills/base.py、enterprise registry、agent.py/manager 的实际注入点（隔离工作树中仅修改本轮差异）。
- 完成条件：个人 name 行为保持，企业稳定 ref 不以 name 后写覆盖；同租户不同主体/空间同名都可正确选择。load_skill 真实定位指定对象；运行 turn 固定 SHA，更新只影响新任务，停用/卸载禁止新启动，引用结束才回收目录。
- 验证方法：VAL-212/213、P2/P3；个人与 C1/C2 同名逐一加载准确内容，更新/卸载并发不混版本；回归现有 skills/persona/session mute 测试。

### TASK-018 turn 知识范围与 read 允许集合

- 依赖：TASK-011/015；引用：REQ-003/004、ADR-004、规格 BND-004/005、INT-104。
- 位置：enterprise/scope.py（新增）、tools.py、turn/子任务/恢复入口。
- 完成条件：单 turn 绑定身份/KB，模型省略参数补固定值，冲突直接拒绝；knowledge_read 受本 turn 命中集合和平台实际 KB 双重验证；取消/结束后释放，下一无 scope 消息恢复缺省，并发 session/turn 不串用。
- 验证方法：VAL-205/206/207、P1；断言越界上游请求没有发生，失败/重试/子任务不会丢范围，历史命中不能跨 turn 读取。

### TASK-019 WS、草稿与 JSONL 元数据

- 依赖：TASK-017/018；引用：REQ-003/007、ADR-004/006、规格 DATA-102、INT-102、BND-004。
- 位置：server/app.py user_message/claim_turn、conversations.py、provider 消息投影、GUI api/types/composerDraft 等实际入口。
- 完成条件：接收/持久化 scope 与 skill_ref，字段/长度/互斥/context_id 校验；服务端生成标签；失败保留草稿，接受后清选择；恢复任务可取完整原约束。历史无字段兼容，provider 不收到未知内部字段，不能自动把企业选择移除后发送。
- 验证方法：VAL-206/212/214/219；WS 拒绝与接受、重连/重启/重试、附件保留、无字段历史、provider 请求内容均有断言。

### TASK-020 资源级授权和撤权缓存

- 依赖：TASK-009/015/017/018；引用：REQ-008、ADR-007、规格 BND-008、DATA-105。
- 位置：enterprise auth/grant/resource service 与 registry/工具执行门控。
- 完成条件：目录级复核，不以 session 存活代替资源授权；缓存最多 30 秒且绑定身份/版本；失效、未知、断网失败关闭，切换立即清理。运行中下一次企业 load/read/访问受撤权拦截；个人聊天/技能仍可用，不宣称抹除历史指令。
- 验证方法：VAL-202/207/214/215；单元模拟边界 + TASK-027 的真实 60 秒计时，后者不能用 fake clock 或断网代替。

### TASK-021 客户端资源列表与生命周期操作

- 依赖：TASK-015/016/020；引用：REQ-001/005/006/007、ADR-003、规格 BND-002/009、DATA-104。
- 位置：GUI Sidebar/App 路由、知识库列表组件、SkillsTab、api、i18n 与对应 UI 测试。
- 完成条件：知识库入口、企业技能/已安装视图、名称搜索/分页/刷新、partial/空/错误/未登录状态；准确安装进度、更新/停用/卸载反馈、旧 active 保留。浏览不安装，未成功不显示可用；保留原个人管理功能。
- 验证方法：VAL-201/209/211/213/216/218；Vitest 渲染/事件测试与后续 Playwright 主要流程，键盘/焦点可操作，跨身份迟到响应不覆盖当前界面。

### TASK-022 Composer 选择与安全网页打开

- 依赖：TASK-019/021/005；引用：REQ-002/003/004/007、ADR-001/004/007、规格 BND-003/004/007/009。
- 位置：Composer、消息/引用渲染、tauri openExternal 包装、API 类型与本地导航。
- 完成条件：@/添加知识单选标签、同名技能来源可辨、选用只准备消息；无会话创建本地会话，远端会话不偷用本地文件。上传只打开确认首页，详情/引用编码 ID 并校验精确同源，拒绝任意外域/协议与 userinfo；无登录态传递。
- 验证方法：VAL-203/204/205/212/219；键盘/删除替换/发送禁用/草稿保留、恶意 URL 与真实浏览器打开，引用只来自实际命中。

### TASK-023 删除自动发布同步并隔离旧安装

- 依赖：TASK-016/017/021；引用：REQ-005/009、ADR-008、规格 BND-010。
- 位置：manager sync hook、enterprise/skills.py 的 release 客户端与 publish_id 新安装路径、旧目录识别 UI。
- 完成条件：登录/刷新不自动下载；新链路无发布/release/published/专用 manifest 依赖；旧格式显示需重装且退出有效菜单。保留旧目录作应用回退，不扫描为新 catalog，不删个人目录或用户已有四个改动。
- 验证方法：VAL-208/216；fake platform 遇旧 release 请求立即测试失败，旧目录即使完整/同名仍不可执行；个人加载路径回归。

### TASK-024 work 定向测试、属性与构建

- 依赖：TASK-015～023；引用：规格 VAL-201～219、VAL-102，NFR-001～004。
- 位置：tests/test_enterprise_*、skills API/session/persona 测试、GUI Vitest/Playwright 与打包入口；work repository unit verification.md。
- 完成条件：P1～P4 各自 seed/至少 500 case/序列证据；异常、并发、身份切换、scope、标准包安装和个人功能回归完整，最小反例固化。形成可供实际 DMG 测试的构建来源，不把源码存在当登录通过。
- 验证方法：受影响 pytest，包括 enterprise、skills、conversations/WS/provider 投影；GUI `npm test -- <实际定向文件>`、`npm run build` 与 Playwright。只在新变化/失败时扩大或重跑，不无依据反复全量；缺环境列为未执行。

## 跨仓联调、发布与收口

### TASK-025 三仓契约与业务闭环测试

- 归属：主仓协调，各仓采集自己的证据；依赖：TASK-008/014/024。
- 引用：REQ-001～009、ACC-001～005、规格 INT-101～105、VAL-201～219。
- 完成条件：真实 knowledge 上传目录经 platform 到 work 展示/下载安装/选用发送，完全没有技能 release 记录或调用；scope 对 A/B 强制有效，引用正确。契约两端的 hash/错误/身份/资源状态一致；假的单仓响应不替代联调。
- 验证方法：用实际三服务及浏览器/桌面完成上传→刷新→安装→发送，记录各仓版本/请求 ID/字节 hash/无发布调用证据；正常与失败、无权、同名、竞争更新均覆盖。

### TASK-026 固定上线清单与迁移回退方案

- 归属：主仓唯一 release-checklist.md；依赖：TASK-007/013/025。
- 引用：ADR-008/009、规格 BND-011/012、sdd-solo release-checklist 协议。
- 完成条件：形成 knowledge→platform→work 的构建/配置/SQL/备份/对象清单/验证/回滚，逐项固定实际测试环境、脚本、负责人和回退条件；FilerUI/新 Secrets 若不涉及写具体 N/A。每个 participant 有发布动作，未执行不勾选。
- 验证方法：检查无占位、路径/版本真实可读、备份恢复已演练；最终候选前置 ready 并计算规范化 hash。清单 ready 只表示计划完整，不表示实际发布授权或已执行。

### TASK-027 测试环境部署、DMG 手测与真实撤权

- 归属：主仓协调三仓；依赖：TASK-026、对应测试环境部署授权与可用访问条件。
- 引用：规格 VAL-203/204/208/215/217/219、BND-008/011；锁定 workflow deployment/manual_test。
- 完成条件：按已授权准确目标部署匹配版本，执行备份/迁移前置，登录回跳与网页详情实际可用；手测知识与技能闭环。正常联网下真实 t0+60 秒撤权测量有三端时序；失败记 cycle 并定向修复/复测，不自动关闭问题或新产品决定。
- 验证方法：DMG 系统浏览器登录 `/callback`、上传/详情跳转、实际包 hash 与容器/二进制来源、知识 A/B/同名技能及计时证据。源码 HEAD 或 health 200 仅作辅助；迁移恢复依代表性存量数据演练。

### TASK-028 正式 Verification、架构对账与 change surface

- 归属：各仓真实验证，主仓汇总；依赖：TASK-027 的手测稳定。
- 引用：ADR-009/VAL-001、规格 VAL-102、锁定 workflow verification。
- 完成条件：记录每仓命令/退出码/版本与未完成项，四扩展逐项 PASS/FAIL/有业务理由的 N/A，失败不伪造通过；按最终实现回写长期基线，形成代码/接口/数据/部署/测试/文档的真实差异面。未达标返回对应实施任务。
- 验证方法：验收矩阵逐项追溯，PBT case/seed 可复核；对账不把目标方案写为已部署。先在各仓生成 verification，再汇总主仓，不能用一仓测试代表另一仓。

### TASK-029 分仓独立审查、receipt 与只读追溯导出

- 归属：主仓协调三仓；依赖：TASK-028、用户选择的独立 reviewer 路径，以及必要追溯提交授权。
- 引用：REQ-009、规格 BND-001/012、sdd-solo repository-unit/trace-export 协议。
- 完成条件：加载 sdd-review 并按规则确认 reviewer 路径，当前 Agent 本体不自审；每仓 review READY_FOR_HUMAN_REVIEW 后才生成真实 readiness receipt。修复后补测并重审。冻结主仓权威来源，向两个非主仓导出本仓只读追溯，取得承载包的实际提交，从该提交校验每文件 hash/来源/本仓 receipt，traceExport=verified。
- 验证方法：每仓独立 review 指纹与 receipt 对齐；参与仓包不含主状态、主审计、主 Baseline、别仓证据或绝对本机路径。没有实际提交和从提交校验就不能填 verified；提交未经授权时停在完整可评审差异，不冒充最终收口。

### TASK-030 聚合、机器凭据、候选与最终签署关闭

- 归属：主仓；依赖：TASK-029、上线清单 ready、锁定 workflow 相应依赖。
- 引用：REQ-009、规格 BND-011/012、sdd-solo full 收口与锁定 workflow。
- 完成条件：跨仓聚合核对契约/发布与回滚/receipt/追溯 commit，结论 READY_FOR_HUMAN_REVIEW；closure=ready 后运行 solo check --write-receipt 成功，随后记录 candidate。依锁定图执行 final_test→reconciliation→review→signoff→close；最终确认绑定当前候选，归档文档与状态在同一变更闭合。不自作主张合并或推送。
- 验证方法：机器凭据、manifest/清单 hash、候选源码集合及参与仓 commit 一致；final_test 采集当前候选的新证据，已通过测试只在版本与变更面仍适用时重用。候选后改代码/关键证据必须重新固定并补测/对账/独立审查。关闭执行 provenance check；任何缺项保留未完成，不靠缩小真实交付范围变绿。

## 验收和扩展覆盖索引

| 覆盖项 | 主要任务 |
|---|---|
| ACC-001 列表与授权 | TASK-004/009/015/020/021/025 |
| ACC-002 上传/详情/引用 | TASK-005/011/018/022/025/027 |
| ACC-003 scope 强制与恢复 | TASK-011/018/019/024/025 |
| ACC-004 无 release 技能闭环 | TASK-003/010/016/017/021/023/025 |
| ACC-005 撤权与回归 | TASK-004/014/020/024/027 |
| Testing | TASK-008/014/024/025/027/028/030 |
| PBT P1/P2/P3/P4 | TASK-014/018/020，TASK-017/024，TASK-016/024，TASK-008/016/024 |
| Security | TASK-003/004/005/009/010/011/016/018/020/022 |
| Resiliency/迁移回滚 | TASK-007/013/015/016/019/025/026/027 |

## Tasks 确认边界

本稿共 30 项任务，完成条件与方法均为后续要求，没有预勾完成。Tasks 全文确认后先记录阶段批准，后续独立 Continue 才准备工作区并实施。提交、推送、合并与共享环境部署仍按真实授权执行，不以任务批准冒充。

需在实施阶段取得的运行条件是测试环境访问/部署授权与真实测试账号；这些不影响任务定义，但缺少时必须如实报告相应任务未完成。遇到 scope/身份/旧表清理的架构事实变化时回到已确认文档处理，不静默扩大。

## Tasks v1.0 确认与交接记录

- 确认者：Roger0808 / yj198788@hotmail.com（当前 Git 治理身份）。
- 确认日期：2026-10-22；记录时间：2026-10-22T18:50:32.834685+08:00。
- 用户原文：“确认，后续交接给 kimi 去开发完成”。
- 批准草稿 SHA-256：`d33c13e3085bb6f68bb2dd463909bcdcb561c82d9eda79879cf11fd5b727285f`。本次只更新状态并追加留痕，30 项任务定义不变。
- 后续实施交给 Kimi，接手资料见需求根 kimi-handoff.md；本 Agent 不开始编码。nextPhase 暂保持 tasks，Kimi 收到用户后续接手/继续指令后记录独立 decision 并进入 implementation。
- 任务批准不预先宣称实现、测试、提交、部署或收口完成；正文 draft/待确认说明保留为历史。
