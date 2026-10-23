---
project: work-opc
document: repository-tasks
demandId: WORK-RESOURCES-001
repositoryId: work-opc
version: "1.0"
status: confirmed
updatedAt: 2026-10-22
---

# work-opc 本仓任务投影

本文件保存在主仓，只投影本仓 Tasks；唯一总体任务为 [Tasks v1.0](../../loop-1/tasks.md)。生成来源已确认 Tasks SHA-256：`71edce650f713d8898c6a065026e51bfaadb13f17ffa54d48e4ed42e7676519f`。修改总体任务后重新生成投影，不在参与仓独立编辑需求或改变 activeLoop。

范围：sidecar、installer、registry、scope/WS、GUI 与本仓验证。基线、规范 remote、标准分支以 [repositories.json](../../repositories.json) 为准。以下任务均未实施或验证通过。

共享前置：TASK-001 隔离、TASK-002 fixture。共享交付责任：TASK-001/002 主仓协调并保持用户工作区；TASK-025～030 维护主仓联调、上线清单、证据聚合与最终治理，同时提供本仓真实验证/独立审查/receipt。

规格依据：[Specification v1.0](../../loop-1/specification.md)；当前阶段只维护任务，不预建 implementation/verification/review 或就绪 receipt。

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

## 投影确认

Roger0808 / yj198788@hotmail.com 于 2026-10-22 以“确认，后续交接给 kimi 去开发完成”批准主仓 Tasks v1.0，本仓投影随同确认；任务定义未改，不表示任务已执行。
