---
name: opc-app-adapter
description: 按照四步标准化流程（1. 深度读取剖析应用与账密提取解密 -> 2. Dockerfile 模板化改造与数据库/基础服务自动迁移 -> 3. 生成内置授权凭据的应用技能 -> 4. 用户确认与部署核对）分析并改造用户上传的应用源码，生成可部署容器包及无授权阻碍的业务技能。
metadata:
  version: 0.3.0
---

# OPC 应用四步标准化改造技能

本技能由当前 AI Agent 执行源码剖析、技术改造、数据迁移与业务技能生成。请使用清晰易懂的中文向用户汇报四个步骤的完整结论。

---

## 第一步：读取用户上传的应用源码，完整了解应用 (Step 1: Deep Discovery)

首先通过只读工具（`list_source`、`read_source`、`inspect_credentials_and_decrypt`、`inspect_local_storage`、`read_go_dependency`）完整扫描并阅读源码，查明并在 `four_step_report.step1_discovery` 与 `summary` 中清晰列出以下全部事项：

1. **应用是干啥的**：应用的核心业务定位、主要功能模块与目标场景。
2. **开发语言与版本**：使用什么语言、运行时版本（如 Python 3.11、Node.js 20、Go 1.22、Java 17）及核心框架版本。
3. **有哪些接口 (APIs)**：完整盘点路由路径、HTTP 方法、请求参数/正文、响应结构及副作用类型（`read` / `write` / `destructive` / `external`）。
4. **有哪些页面 (Pages)**：完整盘点前端页面路由、页面用途及可执行的关键交互操作。
5. **页面是否有权限验证**：访问各个页面是否需要登录或特定角色权限。
6. **是否有管理员账号及账密解密**：
   - 通过应用的配置文件、初始化脚本、种子数据（seed）、本地数据库或源码逻辑，查找本地或远端记录的管理员/默认登录账号与密码。
   - **若密码经过加密、编码或哈希**（如 Base64、Hex、AES、自定义可逆加密或已知默认密码哈希），请调用 `inspect_credentials_and_decrypt` 工具并结合应用源码中的加解密/校验函数逆向还原出明文密码，明确输出管理员账号与明文密码。
7. **调用接口是否需要授权**：API 是否需要 Bearer Token、Session Cookie、API Key 或 Basic Auth。
8. **能否拿到登录的账号密码**：明确说明是否已拿到可用登录账密或固定 Token；若拿到，直接输出账号密码是什么。
9. **数据存在哪里**：
   - **本地 SQLite**（如 `.sqlite` / `.db` 文件）
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

## 第二步：明确并执行要改造哪些内容 (Step 2: Smart Transformation & Auto-Migration)

在 `four_step_report.step2_transformation` 与 `changes` 中落实以下改造规则：

1. **`Dockerfile` 改造或生成**：
   - 先调用 `fetch_dockerfile_template(language, version)` 从 `platform-opc`（`deploy-opc` 数据库）获取同语言、同版本的标准 `Dockerfile` 模板与基础镜像。
   - **若原应用已有 `Dockerfile`**：检查其端口、监听地址（`0.0.0.0`）、构建依赖与国内镜像源是否需要改造；需要则提交修改。
   - **若原应用没有 `Dockerfile`**：基于 `platform-opc` 返回的基础镜像与模板，结合当前应用的真实入口与依赖文件生成完整 `Dockerfile`（配置国内镜像加速如阿里云 pip / npmmirror / goproxy.cn，并补齐 `/healthz` 健康检查）。
2. **数据存储调整与自动迁移**：
   - **本地文件或 JSON**：**不用改动任何代码或配置**，将本地文件或 JSON 保持原样列入 `build_context` 打入容器镜像内，继续按原路径作为本地数据存储使用。
   - **本地 SQLite 等嵌入式数据库**：当平台提供服务端数据库资源且需要将 SQLite 迁移至服务端数据库时，调用 `inspect_local_storage` 读取表结构与数据，并通过 `migrate_storage_to_platform(source_type="sqlite", ...)` 将表结构及存量数据自动迁移到 `platform-opc` 提供的服务端数据库（自动创建独立库 `app_<slug>`），同时改造应用的数据库连接代码与配置，使其连接到服务端数据库。（若无需切换或测试环境指定保留原样，则保留原文件打入镜像）。
   - **远端数据库实例**：默认服务器端能够连接，保留原连接配置，**不用改造**。
   - **本地数据库实例（如 `127.0.0.1` 的 MySQL / PostgreSQL / SQLServer）**：匹配 `platform-opc` 接口获取到的同类型服务端数据库资源（MySQL 对 MySQL、PostgreSQL 对 PostgreSQL、SQLServer 对 SQLServer），通过应用代码内的配置读取当前数据库的所有表结构与数据，调用 `migrate_storage_to_platform` 迁移至服务端数据库，并调整应用配置直接使用服务端数据库。
3. **外部不可达接口识别与本地 AI 改造提示词**：
   - 检查应用调用的外部 HTTP/RPC 接口。若有指向 `127.0.0.1` / `localhost` 的外部第三方服务接口（在服务器上必定调不通），在 `four_step_report.step2_transformation.unreachable_external_apis` 中明确列出，并生成一段**可直接复制给用户本地 AI 的改造提示词**（指导用户如何将其改造成可配置地址或将相关依赖服务一并部署）。
4. **基础服务（Redis、MQ 等）本地实例迁移**：
   - 若使用了本地 Redis / MQ 实例，切换至 `platform-opc` 提供的服务端对应基础服务上；由 AI 判断本地实例中的数据是否需要迁移（如业务状态数据需迁移，纯临时缓存可无需迁移），需要迁移时调用 `migrate_storage_to_platform` 将数据一并迁移至服务端对应服务。
5. **其他必要容器化适配**：
   - 监听地址改为 `0.0.0.0` 并监听目标 `container_port`。
   - 补齐无副作用的 `GET /healthz` 端点（返回 HTTP 200 且包含 `{"service": "<slug>"}`）。

---

## 第三步：生成无授权阻碍的应用技能 (Step 3: Auth-Ready App Skill Generation)

生成的应用技能（`app-<slug>`）**绝不能因为登录或接口授权问题，导致后续 AI 在调用该技能获取页面或 API 数据时受阻**：

1. **写入 `contract.auth_profile`**：
   - 若应用存在页面登录或 API 鉴权，将第一步中获取/解密并验证过的管理员账号、明文密码、登录接口路径（`login_url`）、Token 字段路径（`token_json_path`）或固定授权码完整填入 `contract.auth_profile`。
2. **访问页面需要登录**：
   - 打包器会自动把 `contract.auth_profile` 中的管理员账号密码写入生成的 `SKILL.md`「登录与接口授权凭据（已内置）」章节，直接告诉调用该技能的 AI 使用技能内记录的账密完成页面登录。
3. **调用 API 需要授权**：
   - 在 `contract.apis[].auth` 中声明认证方式，同时配套的 `scripts/invoke_api.py` 会自动读取 `references/operations.json` 中的 `auth_profile`：当外部未传入环境变量时，自动使用内置授权码或自动调用应用的登录接口换取 Token / Cookie 并按应用认可的方式传入请求头。

---

## 第四步：汇总确认与进入部署信息核对 (Step 4: User Confirmation & Next Step)

1. 上述三个步骤全部完成后，检查是否存在**必须由用户确认**的事项（例如：指向用户电脑 `127.0.0.1` 的外部依赖服务如何处理、影响真实业务行为的改动等）。
2. 若有必须用户确认的事项：将其写入 `unresolved` 与 `decisions` 明确告知用户；待用户在对话框确认后，按用户决定执行对应修改。
3. 当全部改造完成且没有任何待用户确认的阻塞项（`unresolved: []`）时，Work OPC 界面会自动完成隔离副本打包并亮起「下一步：确认部署信息」按钮，让用户核对部署信息并一键发布上线。
