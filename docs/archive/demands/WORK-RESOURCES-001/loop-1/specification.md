---
project: work-opc
document: specification
demandId: WORK-RESOURCES-001
loop: 1
version: "1.0"
status: archived
updatedAt: 2026-10-22
---

# work-opc 知识库与技能轻量入口：行为规格

依据：[Requirements v1.2](requirements.md)、[Architecture v1.0](architecture.md)，均已确认。用户在架构批准后的独立消息“Continue”授权编写本稿。本稿定义目标行为与验收，不表示已经实现或测试通过；未创建 Tasks，不开始编码或迁移。

## BND-001 适用范围和规则优先级

work-opc GUI/Tauri、本地 sidecar、platform 与 knowledge API/网页构成本轮闭环。code-opc 不改、不测试、不作为旧技能 release 删除的兼容约束。沿用既定 Git release SHA，技能发布 release 与 Git 分支无关。

身份失效、资源撤权、scope 越界拦截高于安装状态、任务版本固定及缓存命中；当前请求权限高于历史可见性。所有“保留旧版本”均以当前授权仍有效为前提。本轮不新增业务发布审批、云端 Agent 委托、MCP @ 调用或文件/标签级知识选择。

## DATA-101 资源身份与只读列表

所属：enterprise resources；上游为 platform/knowledge，GUI 仅展示。issuer 和 subject 由 sidecar 当前企业上下文取得，不允许客户端字段覆盖。

| 字段 | 类型/约束 | 列表展示 | 可筛选 | 详情展示 | 客户端新增/编辑 |
|---|---|---|---|---|---|
| resource_kind | knowledge_base 或 skill_catalog | 分类入口 | 是，页签 | 否 | 否/否 |
| context_id | sidecar 生成的不含 Token 的身份上下文标记，随身份/空间切换失效 | 否 | 否 | 否 | 否/否 |
| tenant_id / tenant_name | 授权空间 ID / 显示名称；名称缺失显示 ID | 名称 | 是 | 网页 | 否/否 |
| knowledge_base_id | 知识库稳定 ID，仅 knowledge_base | 否 | 否 | 网页 | 否/否 |
| catalog_id | 目录稳定 ID，仅 skill_catalog | 否 | 否 | 网页 | 否/否 |
| name / description | 服务端只读文本；简介空值不伪造 | 是 | 名称搜索 | 网页 | 否/否 |
| version | 技能原目录显示版本，可空；不作为唯一版本标识 | 技能列表 | 否 | 网页 | 否/否 |
| bundle_sha256 | 归档字节的 64 位小写十六进制 SHA-256，仅归档已知时非空 | 状态依据，不显示长串 | 否 | 否 | 否/否 |
| availability | available / not_ready；无权资源不返回 | 状态 | 否 | 否 | 否/否 |
| unavailable_reason | 非敏感原因码；例如 ARCHIVE_NOT_READY | 状态提示 | 否 | 否 | 否/否 |
| detail_url | 同受信 origin 的资源详情链接 | 行操作 | 否 | 打开网页 | 否/否 |

资源唯一键为当前 issuer/subject + tenant_id + knowledge_base_id 或 catalog_id；GUI 可用 context_id 代表当前主体边界。不同空间相同名称不得合并。显示文本和 detail_url 均不构成授权。

客户端提交的 tenant/KB/catalog ID 必须为非空、最长 128 字符的单一 ID，禁止路径分隔符、控制字符和 ..；SHA 必须是 64 位十六进制并规范为小写。context_id 为不透明字符串且必须精确匹配当前上下文；资源名称/简介按纯文本渲染，不作为 HTML 执行。

## DATA-102 消息、草稿与执行上下文

所属：Composer → sidecar → turn executor → JSONL。表中编辑仅指发消息前的选择，不是编辑资源。

| 字段 | 类型/取值/必填性 | 新增页/编辑页 | 列表展示 | 可筛选 | 详情展示 |
|---|---|---|---|---|---|
| knowledge_scope | null/缺省或单对象；tenant_id、knowledge_base_id、context_id 必填 | 输入框选择/发送前可换或删 | 已发消息标签 | 否 | 历史消息标签 |
| skill_ref | null/缺省或企业对象；source=catalog、tenant_id、catalog_id、bundle_sha256、context_id 必填 | 技能选择器/发送前可换或删 | 已发消息标签 | 否 | 历史消息标签 |
| skill | 既有个人技能 name；与非空 skill_ref 互斥 | 既有 /skill 选择/发送前可编辑 | 既有消息显示 | 否 | 既有历史 |
| resource_labels | sidecar 验证后生成的名称、空间名快照；不接受前端作为权威 | 否/否 | 历史标签 | 否 | 历史标签 |
| turn_id | 接收消息时生成，绑定身份、scope、固定技能版本 | 否/否 | 否 | 否 | 否 |

knowledge_scope 只允许一个对象，数组、多对象或不完整字段返回 INVALID_REQUEST。知识范围与技能可以同时选择。草稿保存这些可选字段；旧草稿/历史无字段时按缺省读取。scope/skill_ref 的内部字段不会直接传入模型 provider 标准消息结构；供模型理解的文字由服务端生成。

## DATA-103 本地安装记录

所属：受管 installer；上游为目录原始归档，下游为有效技能 registry。企业记录与个人技能分开。

| 字段 | 类型/约束 | 新增页/编辑页 | 列表展示 | 可筛选 | 详情展示 |
|---|---|---|---|---|---|
| schema_version / source | 1 / catalog | 否/否 | 否 | 否 | 否 |
| identity_hash | SHA-256(规范 issuer、subject 的无歧义序列化)，不含 Token | 否/否 | 否 | 否 | 否 |
| tenant_id / catalog_id | 经过路径安全校验的对象 ID | 否/否 | 来源空间 | 是 | 网页 |
| name / version | 校验后的名称和显示版本；版本可空 | 否/否 | 是 | 名称 | 网页 |
| active_sha256 | 唯一激活的完整归档 SHA；无安装时不存在 | 否/否 | 安装/更新状态 | 否 | 否 |
| enabled | 布尔；成功首次安装为 true | 否/开关 | 是 | 已安装视图 | 否 |
| installed_at | 安装成功时间，不以下载开始时间代替 | 否/否 | 已安装视图 | 否 | 否 |
| last_error | 最近操作错误码与非敏感说明；不覆盖 active 记录 | 否/否 | 行内反馈 | 否 | 否 |

当前有效权限不持久化为永久 enabled=true 授权。安装位置沿用架构的独立 catalog/身份哈希/tenant/catalog/SHA 根，active 指针原子更新；只读元数据不可由通用个人技能编辑接口覆盖。

## BND-002 列表查询与权限刷新

对应 REQ-001/005/008、ADR-003/005/007。

1. 打开页面或显式刷新才查询资源；登录、列表查询、搜索和刷新均不得下载安装。
2. 未登录展示“登录企业账号”；无平台配置展示配置提示；200 空列表展示“暂无可访问资源”；请求失败展示错误和重试，不伪装为空。
3. 列表默认按 tenant_id、资源 name、资源 ID 升序稳定排序；名称用 Unicode NFC 后大小写无关包含匹配，trim 查询首尾空白。offset 从 0 开始，limit 默认 50、范围 1～100，q 最长 200 个 Unicode 字符。空间筛选 tenant_id 可省略，传入无权空间直接拒绝，不返回该空间信息。
4. platform 必须读取当前授权空间，逐空间用个人桥 token 取得可见资源后再筛选排序分页。不得仅从目录全表读出后依靠 GUI 隐藏无权项。
5. 全部空间成功时 complete=true；部分空间失败时 complete=false，响应包含仅限原已授权空间的失败标识与错误码，GUI 显示“部分资源未加载，可重试”。全部失败返回错误。不能以缺失于不完整列表判定资源已删除或永久撤权。
6. 翻页过程中资源变化允许列表位置改变；GUI 按资源复合键去重，刷新重置页码与搜索结果。每个安装/发送操作独立重验身份及具体资源，不信任列表的 total 或旧缓存。
7. 身份/空间切换立即清除旧结果、分页、资源草稿选择和可操作状态；旧 context_id 的迟到响应丢弃。不能串用同租户另一账号已下载的资源。

## INT-101 列表接口

下述为本轮新增接口，不改变无范围知识工具的现有缺省检索。work 入站沿用 sidecar token 与 origin 校验；platform 入站使用当前平台 Bearer，会话与 entitlement 服务端重验；浏览器 GUI 不接收平台 Token。

| 调用方 → 接收方 | 方法与路径 | 查询/返回 |
|---|---|---|
| GUI → work | GET `/v1/enterprise/resources/knowledge-bases` | q、tenant_id、offset、limit；返回知识库项 |
| GUI → work | GET `/v1/enterprise/resources/skills` | 同上；返回目录元数据及当前身份本地安装状态 |
| work → platform | GET `/integration/v1/resources/knowledge-bases` | 同上；只读已授权空间及 KB |
| work → platform | GET `/integration/v1/resources/skills` | 同上；只读上传目录，不查 release/published |
| work → platform | GET `/integration/v1/resources/skills/:catalog_id?tenant_id=...` | 单项授权与当前 hash，用于执行前检查；不存在或不可见统一 RESOURCE_NOT_ACCESSIBLE |

成功 JSON 形状为 `{items, total, offset, limit, complete, failures}`；work 另外返回 context_id，每个技能项附 local_status、installed_sha256、enabled。total 是本次成功读取集合中匹配筛选的条目数，complete=false 时不得展示成全局总数。无部分失败时 failures=[]。单项接口返回 `{item}`。

platform → knowledge 复用 `/api/v1/knowledge-bases`、`/api/v1/skills/catalog`、`/api/v1/skills/catalog/:id`，规范化成功信封。归档缺失/未就绪条目可显示 not_ready，安装按钮禁用，不虚构已完成状态。目录接口不得在查询中创建技能发布记录。

## BND-003 网页上传、详情与引用

对应 REQ-002/004、ADR-001/007。

- 上传入口固定打开 `https://shtokendance.benlai.com`；不自动打开上传弹窗，不传桌面 Token/会话，不模拟上传成功。打开后客户端停留原页；返回客户端由用户刷新。
- 知识库详情为 `/platform/knowledge-bases/<encoded kbId>`，知识来源在同一路径加 knowledge_id；技能详情为 `/platform/toolbox/skills?catalog_id=<encoded id>`。两类链接可加 tenant_id，用途仅为网页请求切换目标空间，必须先校验网页当前用户成员资格，不把参数当授权。
- knowledge-web 在普通登录完成后保留同 origin 的目标路由；空间可访问时切到目标空间再读取资源，无权则显示拒绝。退出或换网页账号后仍重新校验；客户端不能替网页提升权限。
- 客户端只允许 HTTPS、精确 host=shtokendance.benlai.com、无 userinfo、无非默认端口的链接；不跟随任意外域、javascript/file/data URL。ID 使用 URL 编码，不拼接存储路径。服务端返回地址与本地校验均须通过。
- 网页技能 catalog_id 定位为本轮必要小增量；无此参数保持原技能管理行为。目标不存在/不可见时显示错误，不能悄悄打开同名技能。
- 引用由实际检索命中及 read 的资源标识生成；只有合法命中引用可渲染为可信来源卡，模型自行编造的 ID 不成为有效引用。网页无权或已删除时由网页明确提示。外部打开调用立即失败时客户端保留页面并显示“无法打开浏览器”；网站不可达时允许浏览器显示网络失败，不承诺客户端预知浏览器最终加载状态。

## BND-004 知识库选择与消息接收

对应 REQ-003、ADR-004。

1. @ 面板和“添加知识”打开同一选择器；只列当前有权访问的知识库。选中显示一枚可移除标签，选择另一库替换原标签。Esc 关闭选择器不改选择，键盘上下移动/Enter 选中，标签移除键可聚焦。
2. “在会话中使用”将知识库带入当前本地会话输入框；没有会话则创建本地会话并带入，既有未发送文本保留；不自动发送。远端执行会话不能悄悄使用本机范围或安装文件，应提示在本机会话使用。
3. 发送成功被服务端接收后，历史消息保留服务端验证的范围标签，输入框清空范围，下一消息无范围。输入无正文且仅选知识库时不可发送；已有附件路径按既有规则判定可发送。技能单独选用的既有发送规则保持。
4. 服务端接收前校验 context_id、tenant/KB 可见性及 schema；校验失败拒绝该消息，保留文本、附件和范围草稿，不启动 turn。确认范围后生成 turn_id 并持久化资源元数据。显示名称取服务端，不采用前端传入的标题作身份依据。
5. 同一时刻发生登录切换，按接收时 context_id 拒绝旧选择；不能把草稿静默重新绑定到新账号的同 ID 资源。

## INT-102 WS 消息扩展与历史

既有 `type=user_message`、text、attachments、model 保持。新增可选 knowledge_scope 和 skill_ref（DATA-102）；个人 skill 与企业 skill_ref 同时出现返回 INVALID_REQUEST。所有对象字段类型、长度及身份先校验再启动 turn。

新字段示例：

```json
{
  "type": "user_message",
  "text": "整理产品卖点",
  "knowledge_scope": {
    "tenant_id": "10001",
    "knowledge_base_id": "kb-a",
    "context_id": "opaque-current-context"
  },
  "skill_ref": {
    "source": "catalog",
    "tenant_id": "10001",
    "catalog_id": "cat-summary",
    "bundle_sha256": "<64 lowercase hex>",
    "context_id": "opaque-current-context"
  }
}
```

示例 hash 是格式占位，不能作为测试真实输入。消息接收成功/拒绝沿用现有 WS 事件通道，并为拒绝附 INT-105 错误码；本轮不引入通用消息总线或第二套聊天协议。

重试必须重新校验当前身份、资源权限与完整 scope，保留原消息标签和所选技能 SHA；已更新、停用/卸载或无法恢复原版本时拒绝重试，不能换版本或扩大范围。断线恢复中的待执行任务必须取持久化元数据；不完整元数据的旧任务明确要求重新发送。已完成历史消息无新增字段按原样展示，不回填猜测范围。

## BND-005 知识检索与读取强制范围

对应 ACC-003、ADR-004。接收时固定的 turn scope 必须显式传到知识工具、相关子任务及恢复路径。禁止以 session 全局可变变量覆盖并发 turn 的范围。

- 有 scope 时，knowledge_search 省略模型参数便由服务端补固定 tenant/KB；模型传入不同值立即 SCOPE_MISMATCH，且不发送越界上游请求。不得先越界检索再在答案里过滤。
- 无 scope 时保持现有缺省；下一条无 scope 不隐式继承上一条。scope 是检索权限缩小，不能扩大用户既有访问权。
- knowledge_read 必须属于本 turn 的检索命中集合；同时平台在输出正文前验证文档实际 tenant/KB 与 scope 一致。仅用历史命中文档 ID 或模型构造 ID 不放行。元数据与正文在同一次授权读取结果中校验，避免移动文档后返回越界正文。
- 平台现有 GET knowledge/:id 在受限读取时必须显式 tenant 与 knowledge_base_id，禁止“遍历其他授权空间寻找同 ID”。不受限旧调用维持缺省行为。
- 无结果返回 hits=[]，不虚构来源。scope 对象已删除、失权或服务失败时明确中止当前企业检索，不回退全库或个人共享 Token。
- 已进入历史上下文的文本无法从模型记忆撤回；这里保证本 turn 新发生的企业搜索/读取不越界，不把历史文本存在误报为新的检索命中。

## BND-006 技能安装与包校验

对应 REQ-005/006、ADR-005/006。

安装只接受当前可见 catalog，输入必须带当前 context_id、tenant/catalog、列表中的 expected_sha256。客户端点击后立即显示下载/校验状态；后端重新获取当前元数据并逐字节验证归档 SHA。没有发布记录、专用 opc-client.json 或 published 状态也应正常安装标准技能包。

归档下载和安装均不执行安装脚本。拒绝绝对路径、Windows drive/UNC、.. 逃逸、符号/硬链接、规范化后重复路径、多个不明确技能根和压缩炸弹。允许唯一技能根在一层或多层目录中；只安装该技能对应的标准 SKILL.md 与所属文件，规则与 knowledge 标准解析结果一致；不能因为寻找 SKILL.md 而忽略危险 zip 条目。

上限：压缩包不超过 256 MiB（knowledge 若配置更低，以更低者为准）；ZIP 条目不超过 100000、有效文件不超过 20000、单文件不超过 32 MiB、解压总量不超过 512 MiB。三端均执行自身上限，不信任 Content-Length。超限拒绝不激活；这些上限来自当前解析器事实的规格固定，不新增依赖。

安装到同盘 staging → 全量校验 → rename 到 SHA 目录 → 原子替换 active。任何阶段失败，旧 active 不改变；首次安装失败不出现在可执行 registry。身份在下载/校验期间切换，拒绝激活，不能把结果安装给新账号。

首次成功安装 enabled=true；更新必须保留此前 enabled 值，不因更新重新启用已停用技能。同 SHA 重复安装不改变 enabled。UI 希望启用时必须单独发送启用操作并核验当前授权。

## INT-103 技能下载安装接口

| 调用方向 | 方法与路径 | 必须字段/结果 |
|---|---|---|
| GUI → work | POST `/v1/enterprise/resources/skills/:catalog_id/install` | tenant_id、expected_sha256、context_id；首次安装或显式更新共用 |
| GUI → work | PATCH `/v1/enterprise/resources/skills/:catalog_id` | tenant_id、context_id、enabled 布尔；只改本地启用状态 |
| GUI → work | DELETE `/v1/enterprise/resources/skills/:catalog_id?tenant_id=...&context_id=...` | 卸载当前主体的记录；不删网页目录或个人技能 |
| GUI → work | GET `/v1/enterprise/resources/operations/:operation_id` | 返回当前身份的操作阶段和错误；不返回路径/秘密 |
| work → platform | GET `/integration/v1/resources/skills/:catalog_id/archive?tenant_id=...&expected_sha256=...` | 当前个人会话；输出原始 zip |
| platform → knowledge | GET `/api/v1/skills/catalog/:id/archive?expected_sha256=...` | 个人桥 token，当前 tenant 与归档预期哈希 |

安装请求若需下载返回 202 `{operation_id,status:"downloading",context_id}`；已安装同 SHA 且有效时 200 返回现有状态，不重复落地。operation_id 归属当前身份，其他身份不可枚举；刷新页面可恢复当前进行中操作。操作终态为 succeeded/failed，成功必须包含 installed_sha256；不将请求 accepted 当安装成功。

同一主体+tenant+catalog 一次只允许一个安装/更新操作；重复同 SHA 请求返回同一进行中 operation_id，不同 SHA 并发返回 OPERATION_IN_PROGRESS。启用/停用/卸载与写 active 同锁；进行中的安装时上述冲突操作返回 409，界面提示等待本次操作结束。不同技能可独立处理，但必须有有界下载并发。

归档成功 Content-Type=application/zip，返回 X-SHA256、Content-Length（可得时），内容为存储的权威原始字节，不重打包补 manifest。knowledge 对目录权限、归属、有效 SHA 和原始字节重新核验；platform 在代理完整包前/过程中校验，客户端最终再次验证。若流已开始后出错，关闭响应并作为失败处理，不能在 ZIP 尾部拼 JSON。

expected_sha256 与当前目录不一致返回 409 SKILL_VERSION_CHANGED；存储字节不匹配权威元数据返回 422 SKILL_HASH_MISMATCH；归档暂不可取返回 409 ARCHIVE_NOT_READY。冲突后刷新元数据，由用户再次点安装/更新；不静默替换预期 SHA。

## BND-007 技能选择、同名与运行版本

对应 REQ-007、ADR-006。个人技能保持原 /name 行为。企业 registry 的稳定键包含来源、主体、tenant/catalog，不以 SKILL.md name 覆盖个人或别空间技能。选择器显示“个人”或“企业·空间”，同名时同样可选择准确对象。

企业“在会话中使用”准备结构化 skill_ref；选用不执行。发送时校验该版本已安装、启用、当前授权有效，服务端确保实际加载所选目录，而非仅向模型发一段可能被忽略的提示。load_skill 可通过结构化 ref 或等价稳定 registry handle 定位企业技能；既有个人 name 参数不被重新解释为同名企业技能。

自然语言触发企业技能时也必须由有效 registry handle 解析并遵守相同权限/版本约束；不能将未安装目录加入模型的可执行菜单。正在运行 turn 固定已校验目录 SHA，即使用户更新也不换成新文件。资源最新 SHA 改变不自动撤销已安装旧版本，但仍需目录当前可见且未撤权；用户手动更新后新任务使用新 active。

停用/卸载成功后立即禁止新 turn 和未开始的重试，已持有旧版本的 turn 可完成当前已批准版本使用；文件待引用归零后回收。卸载只删除该企业记录和其无引用版本，不删个人技能。撤权及身份切换高于运行引用：下一次企业 load/read/工具访问必须拒绝，已经发送的外部动作不宣称可回滚。

## BND-008 企业授权与 60 秒撤权

对应 REQ-008、ADR-007。已登录 session 不等于某个 catalog 永久授权；安装、启用、发送和每次企业技能加载都需要资源级复核。已安装列表可显示不可用状态，但不能借离线或本地缓存继续启动企业技能。

sidecar 单项资源授权缓存最大 30 秒，刷新成功时间、主体、tenant/catalog、授权版本共同绑定；缓存缺失、超时、失败均不可启动。全局 session 缓存不得绕过更短的资源缓存；身份更换立即清空。服务端每次目录及归档请求重新校验当前会话与空间/目录权限。

撤权计时起点 t0 为服务端权威撤权事务成功提交，终点为最后一次仍被允许的新企业 load/read/访问；t0+60 秒及以后不得再有成功的新访问。测试同时记录平台、knowledge 和 work 的实际事件时间及误差，不用模拟 clock 替代唯一实测。断网时明确不可验证授权并失败关闭；不能以断网导致无调用作为撤权计时通过证据。

未登录/未配置/失败时个人技能和普通聊天维持原行为；企业输入选择拒绝后允许用户主动移除选择重新发送，不能自动去掉企业选项并执行个人路径。

## DATA-104 对象状态机及对应权限

状态可为若干正交维度：page_load_status、availability、local_install_status、operation_status、enabled；不要用单个布尔值混淆“装了”和“有权限”。

| 对象/状态 | 转移与守卫 | 允许操作 | 可见/只读字段 |
|---|---|---|---|
| 资源页面 signed_out / not_configured | 登录或配置可转 loading | 登录/配置、打开上传网站 | 无旧账号资源；地址只读 |
| 资源页面 loading | 成功→ready/empty/partial；失败→error | 离开页面、打开上传网站 | 加载标识；结果未验证前不启动新操作 |
| 资源页面 ready / empty / partial | 搜索/刷新/翻页→loading；身份切换→signed_out/loading | 对当前已验证条目操作；partial 可重试 | 元数据只读，搜索框可编辑；empty 无行操作 |
| 资源页面 error | 重试→loading | 重试、打开网站 | 错误只读；旧缓存可提示过期，不据此使用资源 |
| 知识条目 available / not_ready | 权限变化→不可操作；删除→从列表移除 | available 可选用/详情；not_ready 仅详情 | ID/名称/状态只读，客户端无内容编辑 |
| 技能 not_installed | 显式安装且授权→downloading | 安装、网页详情 | 元数据只读 |
| 技能 downloading / verifying / activating | 操作推进→installed/failed | 查看进度；同对象冲突操作拒绝 | 操作阶段只读，无虚假“已安装” |
| 技能 installed_enabled | 停用→disabled；卸载→not_installed；更新→downloading | 使用、更新（有新 SHA）、停用、卸载、详情 | enabled 可改，其余只读 |
| 技能 installed_disabled | 启用且授权→enabled；卸载→not_installed | 启用、更新、卸载、详情 | enabled 可改，不进入有效菜单 |
| 技能 operation_failed | 重试→downloading；有旧 active 时回到原状态并附错误 | 重试、原 active 合法时继续用、详情 | 错误只读；不能把原 active 改成半成品 |
| 技能 unavailable | 可见性恢复并重验→原本地状态 | 刷新、卸载；无授权时不提供资源详情定位 | 本人本地安装快照只读，不泄露其他身份目录 |
| 技能 legacy_requires_reinstall | 显式安装 catalog→新格式已安装 | 提示、前往企业目录重新选择安装 | 旧格式只读，退出执行 registry，不按 name 猜 catalog |
| 消息 draft_selected | 发送前校验；成功接收→accepted；失败保留 draft | 编辑正文、移除/替换选择、发送 | 用户草稿可改；资源身份不可手填 |
| 消息 accepted / running | 固定 scope/版本；完成/失败/取消进入 terminal | 既有停止操作；不修改运行中范围 | 范围与版本标签只读 |
| 消息 terminal | 显式重试需重新核验，否则保留历史 | 查看引用、合法时重试 | 历史标签只读，不回填猜测 |
| 安装操作 succeeded / failed | 终态不继续推进；重试创建/复用合法新操作 | 读取结果；重试由新请求触发 | 操作记录只读 |
| 清理迁移 planned / backed_up / applied / restored | 按 BND-011 顺序，备份不合格不得 applied | 仅发布运维执行，业务成员无入口 | 审计/校验和只读 |

外部资源已无权时，unavailable 高于 installed_enabled；网页上传权限不由本地 enabled 影响。上述所有对象与状态均在本表给出允许操作和只读边界；没有任何客户端角色可直接修改企业目录业务字段。

## DATA-105 服务端权限矩阵

| 操作 | 未认证 | 当前空间 Viewer+ | Admin+ | 越空间/已撤权 |
|---|---|---|---|---|
| 列目录/知识库、读可见详情 | 拒绝 | 仅本人可见资源 | 同样受空间约束 | 拒绝/不返回 |
| catalog 原始归档下载 | 拒绝 | 可见目录且归档就绪时允许 | 同左 | 拒绝 |
| 网页上传/目录变更/云沙箱安装 | 网页登录后重验 | 沿用现有角色规则，不能通过新接口扩大 | 沿用原权限 | 拒绝 |
| Admin 文件预览和文件管理旧接口 | 拒绝 | 保持拒绝 | 原有权限 | 拒绝 |
| work 本地启用/卸载 | sidecar 认证仍必需 | 仅当前主体本地记录；卸载不需要继续具备云资源授权 | 无额外跨主体特权 | 不得修改另一主体记录 |
| 旧发布/发布协调/旧下载路由 | 不可用 | 不可用 | 不可用 | 不可用 |
| SQL/对象存储清理 | 无业务入口 | 无业务入口 | 业务 Admin 身份不自动授权运维 | 不得执行 |

新目录下载不得返回云沙箱 env、个人凭据、内部 BundleRef、安装会话或其他用户配置。资源不存在与不可见对外统一 404 RESOURCE_NOT_ACCESSIBLE；缺失会话用 401，显式无权空间用 403 SPACE_FORBIDDEN。

## INT-104 受限知识读取

复用 POST `/integration/v1/knowledge/search`，有 scope 必传 tenant_id 与 knowledge_base_id；模型参数冲突由 work 提前拒绝，platform 仍重新校验 KB 可访问性。搜索返回命中保留 knowledge_id/chunk_id/title/source_tenant/knowledge_base_id；不能省略实际来源而让 work 用请求参数补造。

GET `/integration/v1/knowledge/:id?tenant=<scope tenant>&knowledge_base_id=<scope KB>` 在 scope 模式下检查获取的权威文档归属后才输出正文。所有命中若源标识与 scope 不一致，对本次调用返回 SCOPE_MISMATCH，不把不可信命中加入 allowed-read 集合。scope 外读取的错误不包含正文/片段。

原始文件下载不因本轮 Viewer 预览/引用而放宽为下载授权；本轮客户端完整内容仍打开网页查看。

## INT-105 错误、超时与重试

JSON 失败统一 `{error:{code,message,request_id}}`，不得以 200 空 items 吞失败；工具层可额外带 ok=false。message 可理解且不含上游原始 Token、内部 URL、文件路径或正文。以下新增码与既有平台 SESSION_REVOKED/SPACE_FORBIDDEN/UPSTREAM_UNAVAILABLE 衔接。

| HTTP / 错误码 | 含义 | 客户端动作 |
|---|---|---|
| 400 INVALID_REQUEST | 类型/长度/字段/互斥/范围个数非法 | 保留草稿或操作上下文，提示修正 |
| 401 SESSION_REVOKED | 平台企业会话无效/过期且刷新失败 | 清企业操作状态，提示登录 |
| 409 CONTEXT_CHANGED | 选择或操作属于旧身份 | 不重绑；清旧选择，要求当前身份重新选择 |
| 403 SPACE_FORBIDDEN | 显式空间不在授权范围 | 移除可操作性，不自动换空间 |
| 404 RESOURCE_NOT_ACCESSIBLE | 资源不存在或当前不可见 | 刷新；禁止同名替代 |
| 403 SCOPE_MISMATCH | 搜索参数/实际来源或 read 越界 | 拒绝工具调用，不返回越界数据 |
| 409 SKILL_VERSION_CHANGED | expected SHA 与当前目录/所选 active 冲突 | 刷新后由用户重新选择 |
| 409 SKILL_NOT_AVAILABLE | 未安装、停用、旧格式需重装、运行版本不可恢复 | 显示准确 local_status，不自动安装 |
| 409 ARCHIVE_NOT_READY | 当前目录归档缺失或暂未就绪 | 显示未就绪，允许稍后刷新 |
| 409 OPERATION_IN_PROGRESS | 同资源不同写操作冲突 | 等待当前操作，不并发改 active |
| 413 SKILL_PACKAGE_TOO_LARGE | 大小/条目/解压上限 | 安装失败，旧 active 保留 |
| 422 SKILL_HASH_MISMATCH / SKILL_PACKAGE_UNSAFE | 字节哈希/路径/技能格式非法 | 终止操作，不自动重试同包 |
| 502 UPSTREAM_UNAVAILABLE | 上游异常、网络超时或无效信封 | 可显式重试；不降级共享身份 |
| 501 RESOURCE_API_UNSUPPORTED | 新客户端连接缺少新协议的旧服务 | 明确升级服务端；不得回落 release |
| 500 INSTALL_FAILED | 本地落地或原子 active 写入失败 | 保留旧 active；清 staging，允许显式重试 |

上游不存在新 resources 路由的结构性 404 由 sidecar 映射为 RESOURCE_API_UNSUPPORTED，资源单项 404 仍是 RESOURCE_NOT_ACCESSIBLE，不混淆两者。

列表/单项请求超时 15 秒；归档下载总预算 120 秒、连接/无进度等待不超过 15 秒；本地安装操作不得无限挂起，5 分钟未结束转失败并清理未激活 staging，已完成 active 写入则按 succeeded 恢复。任何返回前校验当前 context，迟到成功不能覆盖新身份状态。

本轮无无限自动重试；用户显式重试，轮询间隔至少 1 秒且离页取消轮询，后台操作归服务端管理。下载重试从头校验，不拼接未验证残片；更新失败不改变 active。停用/卸载成功返回 200；对同一主体未安装项重复卸载视为成功，对其他主体资源统一不可访问。

## BND-009 页面清单与行为

| 页面名称 | 入口 | 默认展示/排序 | 行操作或提交后行为 |
|---|---|---|---|
| 知识库列表页 | 左侧“知识库” | 按 BND-002；名称、简介、空间、状态 | 使用→准备输入框范围；详情→浏览器；上传→首页；刷新→重查 |
| 企业技能页 | Skills 中“企业技能”页签 | 按 BND-002；名称、简介、空间、安装/更新状态 | 未装→下载安装；已装→使用/停用/更新/卸载；详情→网页 |
| 已安装技能页 | Skills 中“已安装”页签 | 个人与企业来源清晰；名称、来源、版本及可用性；名称/来源/稳定 ID 升序 | 个人功能沿用；企业生命周期走专用接口；旧格式提示重装 |
| 会话输入框知识与技能选择器 | @/添加知识、/skill/使用按钮 | 当前可用资源，可名称过滤；来源与同名区分 | 选择后显示标签，发送前可删换，不自动发送 |
| 会话消息与引用区 | 发送后及历史 | 真实 scope/技能标签、来源引用 | 标签只读；来源打开正确网页；无结果不伪造引用 |
| knowledge-web 资源详情页 | 客户端“详情”或引用 | 当前网页身份及目标资源；保留原完整管理能力 | 登录后返回/空间重验；无权或删除明确失败；上传不增加新入口协议 |

所有列表搜索输入可键盘操作，翻页与刷新有可见 loading；安装期间同一技能按钮防重复。列表不嵌套完整文档树或技能编辑表单。错误不清空用户正在输入的普通聊天文本和附件；身份切换仅清企业资源选择。

## BND-010 旧本地安装与旧路由下线

按架构确认：旧 publish_id 目录在新版本中显示 legacy_requires_reinstall，不进入有效 registry；不按同名合并或自动安装。用户从企业目录选择新 catalog 后手动安装；旧文件保留为应用回退材料，新安装不覆盖旧根。没有可见 catalog 时显示原因，不复活旧发布下载。

以下旧路由从应用注册中删除，匹配旧路径返回 404，不重定向，不暗中创建/查 release：

- platform `/integration/v1/skills`、`/integration/v1/skills/:publish_id/download`、`/integration/v1/internal/skills`、admin `/skills/publish-jobs` 及 job 详情。
- knowledge `/api/v1/skills/catalog/:id/publish`、`/api/v1/skills/releases` 及其详情/archive/delist。

删除相应 handler/service/repository/model/DI、自动 sync 和发布专属测试；保留归档解析、目录上传/云沙箱安装/文件管理、身份/MCP 与个人功能测试。历史迁移、旧 Loop 审计和归档不改写；必要共享工具不因文件名含 skill 就整块删除。

## BND-011 旧表与对象清理、回滚条件

对应 ADR-008/DATA-001。目标严格限定 knowledge `skill_release` 及其专属索引、platform statedb `skill_publish_jobs`；SQLite 存在同名表时定向删除。新增前向迁移和配套结构恢复，不修改已执行的旧迁移；SQLite combined 000046 的 MCP 表不受影响。

迁移前停止旧写入并导出 schema、全部旧行、归档引用清单和校验和；备份须完成恢复演练。空表和代表性存量行均验证；无备份或校验不符不得 drop。SQL 中途失败回滚事务，不以忽略错误继续发布。不得手工删 schema_migrations 的历史版本来伪装新安装。

归档对象清理独立于 SQL，只有证明属于 release 专用且没有 catalog/沙箱引用的对象才可删除；共享对象保留，不能递归清空 tenant-skills。新链路验收前只盘点/备份，不删除对象。删除对象需有可恢复副本，清单写 object 引用及 hash，不写秘密。

回滚顺序：停止新客户端企业操作 → 恢复旧表结构/行与必要对象 → 校验行数、hash、引用 → 回退 knowledge/platform 构建 → 回退 work。down 重建空表不是恢复通过。恢复后必须再次验证旧构建可读对应数据，且目录/MCP/身份数据未受损。

发布顺序固定 knowledge → platform → work；只有在旧写入已停且备份可恢复后执行关联清理迁移。实际环境、构建 ID、SQL 路径、操作者和观察期完成证据在后续上线清单中固定；此规格不授权直接执行生产数据库操作。

## BND-012 审批和审计

本轮知识浏览/选择、目录下载和本地安装无额外业务发布审批流；管理员网页上传沿用原权限。技能内后续触发的高风险工具/MCP 行为仍走原有人工许可，不因安装或网页角色跳过。审批节点/操作集沿用现有 Inbox/工具许可，当前规格不新增或重写它们。

审计保存身份摘要、tenant/资源 ID、scope/版本 hash、操作、结果、耗时和 request/turn/operation ID。下载包、知识正文、技能指令、Token、内部对象路径不写入普通日志/前端错误。迁移备份本身放受控位置，阶段文档只记位置引用和校验。

## VAL-101 样例与场景验收

固定数据：用户 U1 可访问空间 T1、T2；U2 同属 T1 但身份不同；U3 无 T1 权限。T1 有知识库 A（只含“苹果型号”）、B（只含“香蕉型号”），T2 有同名 A2。T1 的技能 C1 与个人技能同名，T2 有同名 C2；C1 初始原始包 h1，更新包 h2，均有标准 SKILL.md、没有 opc-client.json，也从未生成 release。h1/h2 在测试中以真实字节计算，禁止使用假 hash。

| 用例 | 数据/事件 | 必须结果 | 映射/测试层 |
|---|---|---|---|
| VAL-201 | U1 搜索 A，翻页，某授权空间请求失败 | 可见资源正确，partial 明示；不把失败当空或撤权 | ACC-001；GUI+platform 集成 |
| VAL-202 | 未登录/U3 请求 T1 列表和归档 | 401/403/不可访问；无资源正文或存储引用泄露 | ACC-001/005；鉴权负向 |
| VAL-203 | U1 点上传按钮 | 仅系统浏览器打开指定 HTTPS 首页，无 Token；客户端可返回刷新 | ACC-002；GUI+实际桌面 |
| VAL-204 | 详情跨空间、网页未登录、登录后/无权账号 | 目标保留且重验；无权拒绝；不按同名打开 A2/C2 | ACC-002；网页路由+手测 |
| VAL-205 | scope=A 问香蕉，模型强行 B；read B 文档 | 空结果或 SCOPE_MISMATCH；无 B 的上游搜索/正文输出 | ACC-003；工具+服务集成 |
| VAL-206 | 下一消息无 scope；两会话并发 A/B；中断重试 | 缺省恢复、会话不串范围；重试仍核验原范围 | ACC-003；WS+并发集成 |
| VAL-207 | scope=A 后文档移动到 B、范围删除或撤权 | 输出正文前拒绝，不按历史命中放行，不扩大范围 | ACC-003/005；权限边界 |
| VAL-208 | 网页上传 C1 h1，刷新、停留列表 | 客户端可见但不安装；服务器没有 release 写入/查询/发布调用 | ACC-004；三仓端到端 |
| VAL-209 | 点击 C1 安装，同 SHA 重复点击；选用但未发送 | 一次有效操作；成功前不可用；未发送不执行 | ACC-004；GUI+installer |
| VAL-210 | 上传后变 h2，安装请求仍 h1 | SKILL_VERSION_CHANGED；刷新后用户再操作，不静默装 h2 | ACC-004；契约竞争测试 |
| VAL-211 | 坏包/超限/链接/重复路径；写 active 故障 | 拒绝、清 staging；旧 active h1 完整保留 | ACC-004；安全/故障注入 |
| VAL-212 | 个人/C1/C2 同名，各自选用 | 加载各自指定对象与目录；不后写覆盖或误调 | ACC-004；registry+会话集成 |
| VAL-213 | 任务使用 h1，用户更新 h2/停用/卸载 | 当前任务固定 h1，后续启动按新 active/停用/无安装；引用归零回收 | ACC-004；并发/恢复 |
| VAL-214 | 下载安装期间切 U2；U1 本地已有包 | U1 迟到结果不激活为 U2；U2 不能继承 U1 enabled | ACC-005；身份竞争 |
| VAL-215 | 权威撤权 t0，持续请求，网络正常 | t0+60s 后新企业访问均失败，有真实时序；个人功能可用 | ACC-005；实际计时 |
| VAL-216 | 旧 release 本地目录/路由仍被尝试 | 需重新安装；旧路由 404，不自动映射 catalog | ACC-004；升级/路由回归 |
| VAL-217 | 两库旧表有行且归档含共享对象；迁移再恢复 | 只删目标对象，目录/MCP 保留；备份恢复行数/hash 一致 | DATA-001；迁移/存量/回滚演练 |
| VAL-218 | 旧服务端/超时/下载中断/终态恢复 | 不支持或明确失败，无假成功；已激活完整版本可恢复 | NFR-002/004；故障集成 |
| VAL-219 | 个人技能、聊天附件、无范围知识、登录回跳 | 原行为回归，桌面登录全链路有证据；失败作为闭环阻断处理 | NFR-004；定向回归+DMG 手测 |

## VAL-102 属性与工程扩展映射

| 扩展 | Property / 输入域 | 验证标准 |
|---|---|---|
| testing | VAL-201～219 的正常、失败、边界、权限与 UI 流程 | 规格→具体测试/手测证据，未执行不标 PASS |
| pbt | P1：任意 scope/模型参数/文档归属，允许访问集合始终是所选 KB 的子集 | 固定 seed=20261022，至少 500 case；反例缩减固化 |
| pbt | P2：任意主体/tenant/catalog/SHA/同名组合，registry 与 active 不跨主体或误选对象 | seed=20261023，至少 500 case |
| pbt | P3：重复 install/update/disable/uninstall/切换事件及写入失败，active 只指完整已校验版本 | seed=20261024，至少 500 事件序列；重复操作不产生重复可执行对象 |
| pbt | P4：归档路径规范化、链接、重复名与大小边界 | seed=20261025，至少 500 case；输出永不逃逸安装根 |
| security | 身份/目录级授权、Viewer 管理权限不扩张、链接与归档攻击、真实撤权计时 | 自动负向与真实权限证据；不能 N/A |
| resiliency | 超时、版本竞争、partial、staging/active 崩溃恢复、表与对象备份恢复 | 故障注入及存量迁移恢复；不能用空库替代全部验证 |

沿用现有 pytest/Go/Vitest，简单属性用测试目录内确定性生成器，不新增 PBT 依赖。失败记录 seed、case 序号和输入；人工缩减后增加普通回归例。Property 不能只断言实现函数返回自己构造的结果。

## 规格完成边界

本稿没有待用户补答的产品范围问题；接口、错误码、页行为和数据清理规则为本次待批准的具体化。网页登录跨空间/返回目标、实际部署版本与 DMG 登录仍需实施验证，不当作已通过事实。遇到必须改变已确认架构、权限语义或交付范围的新事实，停止相关实施并修改文档重新确认。

本规格待用户确认全文后才进入 Tasks；工程执行、独立代码审查和发布验证均尚未开始。

## Specification v1.0 确认记录

- 确认者：Roger0808 / yj198788@hotmail.com（当前 Git 治理身份）。
- 确认日期：2026-10-22；记录时间：2026-10-22T18:40:02.776595+08:00。
- 用户原文：“确认”，紧接 Specification v1.0 全文评审请求。
- 批准草稿 SHA-256：`6766fca233840a9bd9e0337fda4fd16b0fccff26a062439d0e5307a2c0741647`。
- 本次只修改确认状态并追加本记录，规格条款不变；上文草稿/待批准描述保留为编写时历史说明。
- 本次停在规格批准后，nextPhase 保持 specification；等待后续独立 Continue 进入 Tasks，未创建 Tasks 或实施代码。
