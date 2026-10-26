---
name: app-transformer
description: 分析用户提供的应用源码包，自动完成容器化改造（补齐 Dockerfile、/healthz、环境变量监听）、提取配置清单与线上资源依赖，并生成双端可用的应用技能包。
---
# 应用容器化改造与转技能指令指南

当用户通过 `work-opc`「新建应用」向导上传应用源码并触发分析改造时，你作为本地智能体，负责对临时工作区源码进行全自动的分析、改造、提取与生成。

## 核心任务

### 1. 容器化适配（生成 Dockerfile 与 captain-definition）
- 识别项目语言栈（Python / Node.js / Go / Java / .NET / 前端静态等）；
- 确保根目录存在 `captain-definition`：
  ```json
  {
    "schemaVersion": 2,
    "dockerfilePath": "./Dockerfile"
  }
  ```
- 确保根目录存在可独立构建运行的 `Dockerfile`；
- 确保容器进程监听 `0.0.0.0`，且端口优先读取环境变量 `PORT`（默认 `8080`）；
- 确保应用具备健康检查接口 `GET /healthz`，成功启动时返回 HTTP `200 OK`。

### 2. 提取应用配置项清单 (`app-manifest.json`)
分析源码中的环境变量读取（如 `process.env`、`os.environ`、`os.Getenv`、`System.getenv`、`.env.example`、`config.yaml`、`application.yml` 等），并在工作区根目录输出 `app-manifest.json`：
```json
{
  "container_port": 8080,
  "detected_resource_types": ["postgres", "redis"],
  "configs": [
    {
      "key": "PORT",
      "value": "8080",
      "description": "应用监听端口",
      "required": true
    },
    {
      "key": "APP_ENV",
      "value": "production",
      "description": "运行环境标识",
      "required": false
    }
  ],
  "apis": [
    {
      "method": "GET",
      "path": "/healthz",
      "summary": "健康检查"
    },
    {
      "method": "POST",
      "path": "/api/v1/resource",
      "summary": "业务接口"
    }
  ]
}
```

### 3. 生成应用技能包 (`skill_out/SKILL.md` & `skill_out/scripts/invoke_api.py`)
在临时工作区的 `skill_out/` 目录下生成两个文件：
1. `SKILL.md`：
   ```markdown
   ---
   name: app-<slug>
   description: <应用名称>（<应用描述>）。当用户需要查询、创建或处理该应用相关业务时使用此技能。
   ---
   # <应用名称> 技能指南
   内网访问地址: `<app_url>`
   ```
2. `scripts/invoke_api.py`：使用 Python 标准库 `urllib.request` 实现的命令行调用脚本，支持 `--method`, `--path`, `--query`, `--body`，基准地址默认为 `<app_url>`。
