# 交付契约与工具

Python 3.11+ 标准库即可运行。Docker 仅用于 smoke。辅助工具只做机械检查，AI 必须真正读代码、形成方案、等待用户确认并修改源码。

## 工作目录

`source/` 为解包后的隔离源码；`inventory.json` 保存来源 SHA、文件摘要、疑似秘密位置；AI 编写 `analysis.md`、`review.json`、`app-contract.json`。smoke 写实际 `verification.json`，不得由模型伪造。

review.json 示例（只在确实收到确认后写）：

```json
{"kind":"human","source":"用户在当前对话确认 P01/P02，保留原数据库","approved_issue_ids":["P01","P02"],"unresolved":[]}
```

工具不能证明审批真伪，Agent 应如实记录。生成的回归样例用 `kind: fixture`，来源注明用户已授权生成测试工程，不代表真实应用人工验收。

## app-contract.json 示例

```json
{
  "schema_version":1,
  "slug":"stock-demo",
  "name":"库存查询",
  "description":"查询现有商品库存",
  "base_url":"http://opc-adapter-stock-demo.orb.local:8080",
  "runtime":{"language":"python","version":"3.11","port":8080},
  "build_context":["Dockerfile","server.py"],
  "health":{"path":"/healthz","status":200,"json_contains":{"service":"stock-demo"}},
  "configs":[{"key":"PORT","default":"8080","secret":false,"required":true,"description":"监听端口"}],
  "resources":[],
  "pages":[],
  "apis":[{
    "id":"get-stock","method":"GET","path":"/api/stock/{sku}",
    "purpose":"查询一个商品的可用库存","evidence":["server.py:42"],
    "parameters":[{"name":"sku","in":"path","type":"string","required":true,"description":"商品编码"}],
    "request_body":null,
    "responses":{"200":{"description":"库存结果","schema":{"type":"object","properties":{"available":{"type":"integer"}}}},"404":{"description":"商品不存在"}},
    "auth":{"type":"none"},"effect":"read","confirmation_required":false
  }],
  "workflows":[{"name":"查询库存","steps":["调用 get-stock，传入用户给出的 sku，返回 available"]}],
  "verification":{"cases":[{"operation":"get-stock","params":{"sku":"DEMO-1"},"status":200,"json_contains":{"available":7}}]},
  "unresolved":[]
}
```

- URL 由用户在分析前确定，与 prepare 输入严格一致；不含凭据、路径前缀、query 或 fragment。内网 HTTP/HTTPS 按目标环境选择，不绑定 Cloudflare。
- build_context 是源码文件/目录白名单，包含真实锁文件、构建与运行资产，不含 VCS、原 ZIP、报告、`.env` 或秘密。示例配置必须不含真实秘密。
- configs 仅有非秘密 default；secret:true 的 default 必须空。秘密由受保护 env-file 或平台资源注入。检测只是启发式检查，不代表完整秘密审计。
- secret/required 必须是明确的布尔值，default 是字符串，key 唯一。常见凭据字段（如 API_KEY、PASSWORD、TOKEN、DSN）不能标为非秘密；任何默认值都不能夹带带凭据 URL 或 Password=... 连接串。不要把秘密改个名字或将 secret 改为 false 来绕过。
- apis/pages 均显式填写数组；至少一个非空。纯页面应用无需伪造 API，但必须提供浏览器验证计划。
- 每个 API 必须含 purpose、源码 evidence、实际 parameters/request_body/responses、auth、effect。request_body 为 null 或包含 required、description、schema 的对象；完整 schema 与错误说明应保留，工具不声称完整实现 JSON Schema 验证。API 错误可能包含 HTTP 200 下的业务错误码，业务流程必须明确成功判断，不把 HTTP 成功等同业务成功。
- auth.type 为 none、bearer 或 header；非 none 必须有 env 环境变量名，header 还要提供 header。技能不内置凭据。
- 非只读 effect 必须 confirmation_required:true。不能仅按 HTTP 方法判断，POST 搜索也可以明确标为 read；GET 也可能需要确认。
- pages 每项含 path、purpose、evidence、actions；无页面就填空数组，浏览器操作需要调用方提供浏览器工具。
- 0.2.0 内置调用器只处理普通 HTTP/JSON 与标量 path/query/header 参数；参数名必须唯一。multipart 上传、二进制下载、流式/WebSocket、同名不同位置的参数及复杂数组参数编码需单独脚本并验证；先报告限制，请用户决定是否扩展，不能默默丢弃 API 或宣称已可调用。
- verification.cases 是已核实无副作用的业务探针，包含 operation、params、可选 body、预期 status 和 json_contains。测试数据只能来自明确的测试环境，不擅自生成真实业务记录。
- json_contains 对象按字段递归匹配；数组须长度及顺序相同，元素可递归匹配部分字段。空结果、不同条数或错误类型不能通过。多行顺序不稳定时先用获准条件过滤唯一测试记录，或提供专用验证。
- verification.browser_cases 是页面操作验证计划，例如 `[{"page":"/orders","steps":["输入查询条件","执行只读搜索"],"expected":"显示符合条件的真实订单"}]`。page 必须在 pages 中；该计划不是执行证据。没有 API 探针时必须填写浏览器计划。

## 使用

```sh
python3 scripts/delivery.py prepare app.zip --work-dir /tmp/app-review --slug stock-demo --url http://opc-adapter-stock-demo.orb.local:8080
python3 scripts/delivery.py view /tmp/app-review server.py
# AI 分析、用户确认、AI 改造并编写契约后：
python3 scripts/delivery.py check /tmp/app-review
python3 scripts/delivery.py bundle /tmp/app-review --out /tmp/app-delivery
python3 scripts/delivery.py smoke /tmp/app-review --image opc-adapter/stock-demo:review --container opc-adapter-stock-demo --keep
```

现版 smoke 仅处理本机 Docker：URL 必须是该容器的 OrbStack 域名，或事先固定的回环端口。它从实际生成的 app.tar 解包构建，而非另做不同权限的构建目录；创建无外网的专用运行网络、只读文件系统和 /tmp 临时盘，验证真实健康身份和业务 JSON。app.tar 与业务技能 ZIP 规范化时间/权限，避免文件时间或 Python 版本使摘要漂移。构建阶段可能下载应用依赖；它不配置 DNS、Nginx、证书、外部数据库或连接生产服务。

有数据库等依赖的应用，保持原依赖并报告阻塞，另在用户授权的独立测试网络验证，不自动换库。`--env-file <path>` 只接收 chmod 600 的用户指定文件，语法为 Docker 的 KEY=value，不支持 shell 展开或仅 KEY 的宿主环境继承。非秘密默认值传入容器，env-file 优先；必填值与 PORT 一致性预先检查。API auth.env 可从同一文件用于本次只读探针，不写入持久报告。已有同名容器不会覆盖。`--keep` 留给手测；`delivery.py cleanup <工作目录>` 仅删除本次所有权标记匹配的容器/网络。

业务技能使用：

```sh
python3 <技能目录>/scripts/invoke_api.py --operation get-stock --params '{"sku":"DEMO-1"}'
```

敏感参数用 --params-file，正文用 --body-file，文件为 - 时读 stdin；凭据从声明的环境变量读。脚本拒绝缺少必填参数、跨站重定向和未批准的写操作。它验证部分常见参数/必填结构，不代替完整 schema 或服务端业务校验。

--approve-write 是技术闸门，不是人类授权证明。没有用户明确确认，不得添加该标志。

## 状态

bundle 的状态是 packaged-not-deployed。无页面且健康/只读 API 探针通过后，smoke 写 verified-local；有页面写 awaiting-browser-verification（仅保留实际完成的构建/健康/API 证据）。工具不执行浏览器步骤，也不伪造浏览器成功证据。构建/启动/业务测试没有实际执行的项目一律写未验证；只读成功不能声称全部业务已验收。技能 ZIP 生成时已包含完整 URL，但部署成功前不启用为正式技能。
