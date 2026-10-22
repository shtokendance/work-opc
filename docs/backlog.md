# 后续待办

- 云端 Agent 委托：来源 OPC-INTEGRATION-001 / REQ-008，包括个人云端任务、会话恢复及委托流程对应验证 C；用户 2026-10-20 明确本轮不做，先完成双端知识、技能和精确 MCP 使用闭环。MCP 自身必要授权/审批仍在本轮。
- 组织自动同步：来源 OPC-INTEGRATION-001 / REQ-006，企微事件驱动、定时及自动重试调度；用户 2026-10-20 选择本轮仅提供手动触发接口，后续按需要决定是否自动化。

## OPC-INTEGRATION-001 Loop 1 实施期发现（2026-10-21 记录，本轮不修）

- code-opc Hermes 后端企业门控未加（C04 仅覆盖 Kimi ACP；Hermes 走 ACP 方言但 selector 强制覆盖未验证覆盖到 hermes.go 路径）。
- code-opc Codex `thread/resume` 未传受管 approvalPolicy/sandbox（C04 仅 `thread/start`）。
- code-opc 两个 dashboard 测试午夜边界 flake（基线既有，0 点前后窗口数学必现；白天恢复）。
- platform POST /sync/runs 同步执行至终态（大组织会持有 HTTP 请求；handler 已断连解耦，可后续异步轮询化）。
- platform `config.Sync.EnableCron` 配置项已无消费者（cron 已删），Admin 大盘仍展示，待清理。
- platform P01 期间对 WeKnora 主库跑过迁移的环境会遗留 audit_events 孤表（切换时运维确认清理）。
- knowledge `skill_release` 列表过滤未下推 JSONB 查询（先全表读再内存过滤，发布量大后优化）。
- knowledge SQLite 迁移链未加 000129-000131（versioned 主链已覆盖；本地 SQLite 模式发布技能需补链）。
- knowledge swag docs 未重新生成（新路由注解已写）。
- knowledge EXT-002 参数加密：IntegrationConfig ArgsKey 未配置时参数明文存储（digest 绑定完整性；如需强制加密可改无密钥拒 Ask 路径）。
- work `agent.py` 的 `extra_skill_dirs` 顺序语义（后写覆盖）已用 `enterprise_skill_dirs` 尾插参数规避——后续若重构 SkillLoader 需保持企业优先测试。
- work sidecar 回跳路径违反 INT-101a：桌面 DMG 冒烟（2026-10-21，对测试平台 http://10.101.102.116:3002 实测）`POST /v1/enterprise/login/start` 被平台以 `AUTH_STATE_MISMATCH`（redirect_uri must be http://127.0.0.1:<port>/callback）拒绝；`coworker/server/app.py:1935` 的 `_enterprise_redirect_uri` 构造的是 `http://127.0.0.1:<port>/v1/enterprise/oauth/callback`，与规格/平台 allowlist 不符，企业登录全链路因此未通。修复方向：sidecar 回跳改 `/callback` 并同步调整回调路由，修后需重出桌面包复测。
- 规格措辞待对账澄清两处：BND-140 manifest.sha256=payload digest（zip 内 manifest 无法哈希整包）；BND-150 调用上下文 sub=knowledge 用户 id、immutable_subject 入 user claim（按 knowledge validateContext 事实裁决）。
- code 桌面企业 MCP 人工审批界面：来源 OPC-INTEGRATION-001 / REQ-009；work 端复用既有 Inbox 审批卡，code 桌面本轮未建精确 MCP 审批 UI（企业任务遇需审批写工具结构性拒绝执行，fail-closed 设计）。若试点出现"需在 code 桌面人工批准 MCP 调用"的真实场景，补一块审批界面（knowledge Decide 端点已就绪）。
- code WS 的 PAT 连接未穿授权版本快照：来源 OPC-INTEGRATION-001 / C-4（审查 R3-2）；HTTP Auth 与 DaemonAuth 的 PAT 已比对 590 版本快照，WebSocket 的 PAT 连接（ResolveTokenEnterprise）仅比对 subject，30s 周期重校验复用同一 checker。纵深防御缺口（推送路径与中间件重锚已覆盖主链路），不阻断本轮；后续把版本快照穿到 WS checker。
- 客户端企业技能浏览与手动安装界面（新需求，OPC-INTEGRATION-001 关闭后另开）：来源 OPC-INTEGRATION-001 验收阶段产品决定（2026-10-22）。期望交互：技能上传服务器后，work 客户端提供 skills 浏览界面（可见空间内/有权限的技能列表），用户点击安装后即可在会话中使用；不走"平台发布 release → 受管目录自动安装"的发布流水线。涉及：work 侧car/GUI 新增技能浏览+安装交互（安装落点与受管目录 opc/<tenant>/<publish_id> 的兼容策略需新需求内裁决）、平台列表接口复用性评估、与 BND-142 分发授权/60 秒撤权语义的关系重定。本 loop 已实现的 INT-142 列表/下载/受管安装代码保留，新需求决定沿用或改造。
- 客户端 @ 知识范围选择器（新需求，建议与"技能浏览安装"合并为一个客户端交互迭代）：来源 OPC-INTEGRATION-001 验收阶段用户提议（2026-10-22，附参考截图：@ 面板列出知识库/标签/文件，选中后随消息提交）。期望交互：会话输入框 @ 唤起企业资源面板（可见知识库列表/标签/文件），用户选中后该范围随本条消息传给 Agent（实现上可映射为 knowledge_search 的 space/knowledge_base_id 钉选 + 引用回填）。本 loop 已具备后端能力（INT-120a 租户缺省+KB 钉选、knowledge_read 引用链），缺的是客户端选择器 UI 与消息级范围传递。
