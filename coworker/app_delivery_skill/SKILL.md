---
name: opc-app-adapter
description: 按照四步标准化流程（1. 分析源码、认证与存储 -> 2. 容器化适配与本地数据持久化卷 -> 3. 生成带自动认证及验证计划的应用技能 -> 4. 汇总确认与部署核对）处理应用源码；保留原数据库引擎和业务逻辑，不自动迁库，以实际部署验证确认可用性。
metadata:
  version: 0.3.0
---

# OPC 应用四步标准化改造技能

本技能由当前 AI Agent 执行源码分析、容器适配、持久化卷配置、自动认证及业务技能生成；不执行数据库迁移。请使用清晰易懂的中文向用户汇报四个步骤的完整结论。

---

## 第一步：读取用户上传的应用源码，完整了解应用 (Step 1: Deep Discovery)

首先通过只读工具（`scan_routes_and_pages`、`list_source`、`read_source`、`inspect_credentials_and_decrypt`、`inspect_local_storage`、`read_go_dependency`）完整扫描并阅读源码，查明并在 `four_step_report.step1_discovery` 与 `summary` 中按固定结构简洁明了地向用户输出以下全部内容（用户在界面上核对并确认第一步分析结果后，再进入第二步执行改造）：

1. **应用是干啥的**：应用的核心业务定位、主要功能模块与目标场景。
2. **开发语言与版本**：使用什么语言、精确运行时版本要求（如使用 `StrEnum` 必须锁定 Python 3.11+、Node.js 20、Go 1.22、Java 17）及核心前后端框架版本。
3. **有哪些接口 (APIs，零遗漏全量盘点)**：基于 `scan_routes_and_pages` 扫描结果与源码阅读，完整盘点全部路由路径、HTTP 方法、请求参数/正文、响应结构及副作用类型（`read` / `write` / `destructive` / `external`），不得遗漏任何一个接口。
4. **有哪些页面与业务视图 (Pages，零遗漏全量盘点)**：完整盘点前端所有独立页面路由及单页应用（SPA）内部的业务子面板/视图组件、页面用途及可执行的关键交互操作。
5. **页面是否有权限验证**：访问各个页面是否需要登录或特定角色权限。
6. **是否有管理员账号及账密解密**：
   - 通过应用的配置文件、初始化脚本、种子数据（seed）、本地数据库或源码逻辑，查找本地或远端记录的管理员/默认登录账号与密码。
   - **若密码经过加密、编码或哈希**（如 Base64、Hex、AES、自定义可逆加密或已知默认密码哈希），请调用 `inspect_credentials_and_decrypt` 工具并结合应用源码中的加解密/校验函数验证已知凭据。哈希不可逆，不能承诺还原；只有编码或具备密钥的可逆加密可以解码/解密。不得在摘要或日志输出明文。
7. **调用接口是否需要授权**：API 是否需要 Bearer Token、Session Cookie、API Key、`X-Admin-Token` 或 Basic Auth。
8. **能否拿到登录的账号密码**：明确说明是否已拿到可用登录账密或固定 Token；仅报告凭据来源与待验证状态，不展示明文。
9. **数据存在哪里**：
   - **本地 SQLite**（如 `.sqlite` / `.sqlite3` / `.db` 文件，说明表数量与数据行数）
   - **本地 JSON 或其他本地文件**（如 `.json` / `.csv` / 本地文件目录）
   - **远端数据库实例**（非本机回环地址的外部/内网数据库）
   - **本地数据库实例**（连接地址为 `127.0.0.1` / `localhost` 的 MySQL、PostgreSQL、SQLServer 等）
10. **使用了哪些基础服务**：是否使用了 Redis、MQ 消息队列（RabbitMQ/Kafka/RocketMQ）、对象存储（MinIO/OSS）等，以及连接的是本地实例还是远端实例。
11. **是否有 `Dockerfile`**：原工程是否自带 `Dockerfile`，以及现有 `Dockerfile` 是否满足容器化运行要求。
12. **是否有 `README.md`**：概述 `README.md` 的核心内容（如启动方式、默认账密、环境变量说明、构建步骤），并评估对后续改造是否有帮助。
13. **补充工程关键项**：
    - 服务监听地址是否支持 `0.0.0.0` 与动态端口（容器端口 `container_port`）
    - 是否存在前后端同仓构建顺序或静态资源托管要求
    - 是否依赖特定操作系统原生库（如 `gcc`、`libpq-dev`、`ffmpeg`）
    - 健康检查接口 `/healthz` 是否已具备

---

## 第二步：明确并执行要改造哪些内容，输出简明改造报告 (Step 2: Smart Transformation & Report)

在用户确认第一步分析结果后，执行改造并在 `four_step_report.step2_transformation` 与 `changes` 中生成**简明易懂的改造报告**（清晰告诉用户新增了什么、修改了什么、删减了什么、数据库怎么处理的，无需堆砌复杂代码细节）：

1. **`Dockerfile` 改造或生成**：
   - 先调用 `fetch_dockerfile_template(language, version)` 从 `platform-opc`（`deploy-opc` 数据库）获取同语言、同版本的标准 `Dockerfile` 模板与基础镜像。
   - **若原应用已有 `Dockerfile`**：检查其端口、监听地址（`0.0.0.0`）、构建依赖与国内镜像源是否需要改造；需要则提交修改。
   - **若原应用没有 `Dockerfile`**：基于 `platform-opc` 返回的基础镜像与模板，结合当前应用的真实入口与依赖文件生成完整 `Dockerfile`（配置国内镜像加速如阿里云 pip / npmmirror / goproxy.cn，并补齐 `/healthz` 健康检查）。
2. **本地数据统一使用持久化卷**：
   - SQLite 等嵌入式文件数据库、JSON、上传文件、报告和备份，保留原有引擎、表结构、业务 SQL、数据及读写方式。识别真实数据路径并声明 storage_mounts；不询问迁移到线上数据库，不修改数据库驱动，不执行建库建表或数据导入。
   - SQLite 挂载整个数据目录，包含 WAL/SHM/journal 等伴随文件；初始数据库作为种子仅在目标文件缺失时补齐，不覆盖线上数据库。不得只把数据库烘焙在镜像中就宣称持久化完成。
   - 数据文件与程序混放时，不可盲目把整个程序根目录挂成空卷；优先利用应用已有数据路径配置隔离数据目录。无法安全确定映射时明确技术阻塞，不能改用 PostgreSQL 逃避问题。
   - 原本连接远程数据库的应用保留其连接配置；本机 MySQL/PostgreSQL 等独立数据库服务不等于 SQLite 文件，单靠应用目录挂载不能替代数据库服务，需要单独确认其运行环境，不自动迁库。

3. **外部不可达接口识别与本地 AI 改造提示词**：
   - 检查应用调用的外部 HTTP/RPC 接口。若有指向 `127.0.0.1` / `localhost` 的外部第三方服务接口（在服务器上必定调不通），在 `four_step_report.step2_transformation.unreachable_external_apis` 中明确列出，并生成一段**可直接复制给用户本地 AI 的改造提示词**（指导用户如何将其改造成可配置地址或将相关依赖服务一并部署）。
4. **基础服务（Redis、MQ 等）本地实例迁移**：
   - 若使用了本地 Redis / MQ 实例，切换至 `platform-opc` 提供的服务端对应基础服务上；由 AI 判断本地实例中的数据是否需要迁移（如业务状态数据需迁移，纯临时缓存可无需迁移），涉及状态迁移时作为独立任务处理，不在应用改造流程中自动搬迁。
5. **其他必要容器化适配**：
   - 监听地址改为 `0.0.0.0` 并监听目标 `container_port`。
   - 补齐无副作用的 `GET /healthz` 端点（返回 HTTP 200 且包含 `{"service": "<slug>"}`）。

---

## 第三步：自动认证与应用技能

目标：用户无需理解 Token/Cookie 技术细节，由系统分析并接通原有认证，保留角色权限。配置生成不代表已验证。

1. 扫描结果 auth.type=unknown 是待分析，不是公开。阅读登录处理、中间件、会话提取和角色检查。禁止根据 admin/permission 名称猜管理令牌；旧提案不能替代当前源码证据。
2. auth_rules 声明 target=api/page、path 或 prefix、可选 method、auth 和 evidence。精确路径优先，公共登录入口等必须作为例外。接口和页面分开分析。
3. auth_profiles 按 id 配置登录方案，auth.profile 引用它。兼容旧 auth_profile。Cookie 使用 type=header/header=Cookie；Bearer 使用 type=bearer；固定密钥必须使用源码规定的头。roles 记录真实权限范围，不得扩大权限。
4. 自动登录支持同源 login_url、login_method、token_json_path、login_body_template。模板顶层值可使用 ${username}/${password}。优先 username_env/password_env/token_env 引用已有受控运行配置；兼容旧内置凭据但不在摘要、日志或技能 Markdown 展示。每次调用重新登录获取会话，避免复用过期会话。不同 profile 的凭据不能混用。
5. 真正缺少外部授权或凭据时，auth_blockers 提供 question、evidence、affected、action、reason（external_authorization/missing_credential/role_change）。用户只需一次“连接应用账号”等明确操作。无证据的认证猜测返回 AI 修正，不反复询问用户。
6. 每种受保护 API 身份/权限范围配置只读业务验证用例；公开健康检查不能代替认证验证。页面需要独立浏览器验证。现有调用器未通用支持 SSO/MFA/复杂 CSRF，遇到时设计专用适配并明确未完成范围，不取消原有认证。
7. 保持四步部署流程。生成 app.tar/skill.zip 后按原流程确认部署。部署、健康、业务认证、页面验证分别报告；自动登录不等于授权写业务数据。

---

## 第四步：汇总确认与进入部署信息核对 (Step 4: User Confirmation & Next Step)

1. 上述三个步骤全部完成后，检查是否存在**必须由用户确认**的事项（例如：指向用户电脑 `127.0.0.1` 的外部依赖服务如何处理、影响真实业务行为的改动等）。
2. 若有必须用户确认的事项：将其写入 `unresolved` 与 `decisions` 明确告知用户；待用户在对话框确认后，按用户决定执行对应修改。
3. 当全部改造完成且没有任何待用户确认的阻塞项（`unresolved: []`）时，Work OPC 界面会自动完成隔离副本打包并亮起「下一步：确认部署信息」按钮，让用户核对部署信息并一键发布上线。
