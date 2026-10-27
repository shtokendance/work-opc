# 框架检查索引

只读匹配项目的部分。这是检查线索，不是固定改造模板。

## 共同检查

先复现原构建/运行方式，确定服务边界、版本和架构。保留锁文件，检查私有依赖、证书、动态库及网络；不可达时报告阻塞，不用空壳程序替代。

确认配置的文件/环境/命令行优先级。`PORT` 只是一种约定，必须有代码真正消费。健康由真实服务提供并返回服务标识，不用目录页面、Nginx 默认页或另起的假服务代替。数据库就绪条件由所有者决定。

保持未要求变更的配置字段、CLI 子命令、帮助和版本行为。桥接运行配置时按结构解析 YAML/JSON 并安全序列化，不用字符串展开拼接秘密；密码中的冒号、井号、引号或换行不能改变配置结构。临时文件权限收紧，并按框架实际路径规则计算位置。

加载器没有环境变量覆盖时，只要已核实原 CLI 能加载指定配置文件，可以提议“去秘密模板 + 容器内启动包装器”：保留所有非秘密原配置，以已保留的运行时环境值结构化填入秘密字段，写权限受限的临时配置再 exec 原入口。须把模板、包装器及解析依赖明确列入修改和构建输入，不能凭空假设平台提供宿主文件挂载，也不能因模型看不到秘密值就宣称无法保留。Work 的原值来源绑定见 integration.md。

核实部署引擎的构建能力。传统 Docker builder 不提供 BuildKit 的隐式 BUILDPLATFORM/TARGETARCH；未确认支持时采用兼容的普通 Dockerfile，按已确认的原生架构构建，CGO 交叉编译需另行核实工具链。

区分日志、上传、缓存、数据库文件与持久数据；测试临时盘不等于生产持久卷。迁移沿用原流程，未授权不运行。

## Java / JVM

- 识别 Maven/Gradle、Java toolchain、Spring Boot/Quarkus/原生 HTTP 等真实框架。
- 核对 profile、application 配置、环境替换、JAR 主类/启动器、资源、JVM 内存与 CA。
- WAR 不强行改为 Spring Boot；使用 Actuator 时保留鉴权，不开放全部管理端点。

## C# / .NET

- 识别 TargetFramework、SDK/运行时与 framework-dependent/self-contained 发布。
- 核对 ASPNETCORE_URLS/HTTP ports/Kestrel、appsettings 的 `__` 环境覆盖；开发 launchSettings 不一定生效。
- 验证 DLL、原生依赖、ICU/Globalization、证书、架构。Windows 专属依赖报告不兼容，不伪装支持 Linux。

## Go

- 核对 main 包路径、toolchain、构建标签、CGO、embed、动态库、私有模块与 vendor。
- 按真实 CLI 保留子命令和参数，不默认 `./server` 即可启动。
- 不按依赖名注入同一套 api/model、数据库表或演示记录。运行层只复制存在且必需的目录。
- Alpine 编译的 CGO 二进制不一定能在 Debian 运行。
- 依赖 SDK 的 CLI、配置展开与间接 CGO 不能凭应用 go.mod 猜测。若依赖代码未包含在 ZIP 且当前工具无法读取，列出未知项，请用户提供可核实的依赖源码片段；不能声称已阅读它。`CGO_ENABLED=0` 可能排除依赖中包含 `import "C"` 的整个文件。
- YAML 中的 `${VAR}` 不保证会展开，必须核实配置加载器；单纯把 DSN 改成占位符可能导致启动失败。保留数据库类型与原业务前缀；不把 SDK 的根命令帮助输出当作成功启动服务。

## Python

- 核对 pyproject/requirements/锁文件、Python 版本、包布局与实际模块入口。
- Flask/FastAPI/Django/WSGI/ASGI 遵循原生产入口，不能默认 main.py 或用静态服务器替代 API。
- 验证 native wheel、系统依赖、工作目录、资源与迁移。

## Node / Web

- 遵循 packageManager、锁文件、engines 和 build/start；构建依赖通常含 devDependencies，不能提前全裁掉。
- 区分静态 SPA、SSR、API 与同仓前后端，核对构建输出和运行资产。
- 默认独立子域；若用 path，再检查资源 base、Router、API、重定向与 WebSocket。
- localhost 的含义由代码执行位置决定，不机械全局替换。

## 业务证据

从路由与 DTO 核对路径、参数位置/类型/必填、JSON 包装、错误、认证和副作用。OpenAPI 的 servers/basePath 与 mount prefix 可能不同，递归 schema 引用不能丢失。

同一路径的 GET/POST 等方法分别登记；共享 Handler/DTO 时不能遗漏别名方法或丢掉其中一个的返回结构。只读探针除状态/业务码外，应核验获准测试记录的实际字段；空结果不能冒充业务验证成功。未知真实数据时先取得获准验证条件，不编造结果。

从 Router、导航、组件和表单确认页面用途，注明登录/数据前提及浏览器工具要求。源码字符串匹配不等于已验证的全量页面清单。
