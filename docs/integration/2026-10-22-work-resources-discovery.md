---
document: discovery
status: superseded
proposedDemandId: WORK-RESOURCES-001
updatedAt: 2026-10-22
---

# work-opc 知识库与 Skills 轻量客户端：需求发现与联合确认卡

本文件保留模式确认前的调研快照。2026-10-22 用户以“确认”批准联合卡后，唯一活跃需求已转入 `docs/loops/demands/WORK-RESOURCES-001/loop-1/requirements.md`，参与范围见同需求 `repositories.json`；下文的“待确认/建议”均为当时记录，不代表当前仍未批准联合卡。本文件不是已批准的 Architecture、Specification 或 Tasks。主仓为 work-opc；旧 OPC-INTEGRATION-001 已关闭，不恢复其 Loop。

## 已确认的产品方向

用户原始目标：“我们开一个新的迭代，重点 1，知识库显示，和使用，你来设计一下。2，skills 的显示，下载和使用。你来做一下设计，先做 work-opc 这一端”。

用户补充：“可以，关于知识库，技能的详情和上传，客户端点击后，可以打开浏览器，去 knowledge-web 去上传，客户端制作简单的显示”。随后回复“确认，继续”。确认日期：2026-10-22；确认者：当前对话用户；确认版本：上述轻客户端边界。该确认不等于尚未展示的跨仓范围、交付模式或技术基线已批准。

### 客户端交互

- 知识库：独立轻量列表、按名称搜索、刷新；展示名称、简介、所属空间。点击详情或上传，系统浏览器打开 knowledge-web。客户端不建设文档详情、上传表单或知识管理后台。
- 会话：输入框 @ 或“添加知识”选择知识库，以可移除标签显示；本条消息附带明确范围，发送后在消息中保留范围。指定范围不得静默扩大；回答保留来源引用，完整内容在网页查看。
- Skills：企业技能与已安装视图；展示名称、简介、来源和安装状态；支持下载并安装、手动更新、停用及卸载。详情与上传转到 knowledge-web。
- “在会话中使用”只准备消息，发送后执行；复用现有 /skill 行为。浏览不自动安装，安装不自动执行。企业授权失效应明确显示不可用；不能通过落地为个人技能绕过撤权。
- 浏览器采用自身登录和权限，不通过 URL 传递客户端凭据。页面可配置地址必须与实际 knowledge-web 部署匹配。

### 建议的首版边界（待联合确认固定）

知识选择以知识库为粒度，不含文件/标签选择；不导出任意 ZIP；不扩展 code-opc；不做 MCP @ 入口、云端 Agent 委托、组织自动同步。未指定知识库时保持现有按需检索行为。网页上传完成后，用户回到客户端刷新即可看到有权访问的资源；技能的原始归档必须就绪且通过校验才可安装。

## 当前源码事实与缺口

以下为 2026-10-22 的本地 release 源码检查；各仓 HEAD 与当次 ls-remote 的 release SHA 相同。没有执行线上联调，源码存在不代表当前网页部署已支持。

| 范围 | 已验证事实 | 本轮需要解决 |
|---|---|---|
| work 知识工具 | `coworker/enterprise/tools.py` 的 knowledge_search 接受 space 和 knowledge_base_id，knowledge_read 读取来源 | GUI 列表与选择器、消息级范围在执行层约束，防止模型扩大范围 |
| work 技能 | `coworker/enterprise/skills.py` 有归档校验和受管安装；`server/manager.py` 的 _schedule_catalog_sync 会自动安装新增 release | 拆开浏览与安装，停止新目录的自动安装；保持授权、版本隔离及原有安装兼容 |
| work 会话 | `surfaces/gui/src/components/Composer.tsx` 已有 /skill 选择及独立消息字段 | 企业技能身份区分、安装后刷新、不可用状态及回归验证 |
| platform 知识 | `main.go` 暴露检索、内容和文件下载；`pkg/handler/knowledge_upstream.go` 内部已有可见知识库列表调用 | 给已认证客户端提供授权过滤后的知识库浏览接口 |
| platform 技能 | `pkg/handler/skills.go` 仅聚合 published release；下载以 publish_id 定位 | 上传目录与 release 不是同一列表，不能仅加 UI 就承诺上传即安装 |
| knowledge 技能 | `internal/router/routes_agent.go` 的 catalog 读取为 Viewer+，上传/文件访问/发布为 Admin+；release 下载为 Viewer+ | 提供安全的目录到可下载版本映射；保留归档校验和既有权限，不能让普通客户端替管理员发布 |
| knowledge 网页 | `frontend/src/router/index.ts` 有 `/platform/knowledge-bases/:kbId` 和 `/platform/toolbox/:section?`；`Toolbox.vue` 的 skills 分区使用 SkillSettings | 知识详情路径可复用；Skills 页面当前缺少按 catalog id 打开详情、直接触发上传的已验证深链 |
| 网页上传 | `views/settings/SkillSettings.vue` 已有上传/目录操作；知识库页已有上传入口及 knowledge_id 查询参数 | 评估小型深链增强；不虚构现成的详情或 upload URL |

网页基地址、当前线上前端是否对应此版本、跨空间页面定位与登录后返回目标页，需要 Architecture 阶段核实。不能把工作区的 web 构建产物当成部署证据。

## 参与仓与远程基线建议

仓库都没有远程 master，默认分支为 main。本轮建议承接已合入旧迭代的远程 release；需用户确认这个分支例外。以下提交来自本轮实时 ls-remote。

| repositoryId | remote | 默认 main | 建议 release | 本轮职责 |
|---|---|---|---|---|
| work-opc | https://github.com/Roger0808/work-opc.git | 7bac273885d77f5794416ba7da5ad4ab4ceafd5f | 7be2e4cec8b91f6bb430a332241629500d7bf5ff | 主仓，客户端及执行层 |
| platform-opc | https://github.com/Roger0808/wecom-opc.git | d6a07bf02fd7aeded69fca810c9c7e4f2f07d74c | 27de3c77de6be0fb08e8147450b3a449d296dd16 | 浏览与分发适配 |
| knowledge-opc | https://github.com/Roger0808/OPC-2.0.git | 6c3730a6adfd9e77e3472c3f0e3d10fcd805bda6 | 071fd96c4295d0925244615f7d8a73e9544f26d0 | 目录归档能力、网页详情/上传深链的必要增量 |

code-opc 排除实施。上述三个仓库可读取，没有不可访问候选；部署环境未核实。

work 已有四个暂存改动：coworker/agent.py、coworker/headless/runner.py、docs/headless.md、tests/test_headless_run.py。platform 有未跟踪的 AGENTS.md、CLAUDE.md、docs/loops/。保留这些内容；实施前从已确认远程基线隔离工作，不 stash、不清理、不把已有改动算成本轮交付。

## 推荐交付方式

建议 `full` + governance v2 `loop`。理由不是仓库数量，而是“上传后可安装”改变了现有管理员发布后分发的生命周期，需要独立确认目录归档授权、版本固定、更新和撤权兼容方案；已有自动安装也要改成手动安装。这些不能只用 UI 改动掩盖。若改为只浏览现有已发布 release，才可重新评估轻量模式，但这会缩小已讨论的上传可用目标。

当前 `workflow show --route loop` 返回 builtin:loop：requirements → architecture → specification → tasks → implementation → deployment → manual_test → verification → final_test → reconciliation → review → signoff → close；review required，signoff submitter。尚未启动或锁定 run。最终应对新 demand 再读取并锁定，不能借旧 demand 启动。

建议命名：WORK-RESOURCES-001 / work-opc 知识库与技能轻量入口。
建议聊天名称：[SDD Solo][WORK-RESOURCES-001] work-opc 知识库与技能轻量入口。
建议标准分支：feature/261022_WORK-RESOURCES-001_resource-entry。

## 可观察验收建议

1. 登录后，只列出该账号有权访问的知识库和技能；切换账号/空间不残留上一身份的可操作资源。
2. 客户端详情、上传按钮打开正确 knowledge-web 页面；浏览器未登录时可完成网页登录，不能泄露客户端 Token。
3. 指定知识库提问，检索严格落在指定范围，回答引用可追溯；无结果、撤权、服务失败有明确提示。
4. 网页成功上传且归档就绪的技能，在客户端刷新后可见；不需要用户再手动操作另一套发布流水线。
5. 点击下载安装后可选用；未安装不执行；安装失败不出现假成功，更新失败保留原可用版本。
6. 安装后选用技能，再发送消息触发；个人技能、企业技能同名可区分；撤权不因本地已有文件失效。
7. 原有个人 Skills、聊天、附件和未指定知识范围的路径保持可用。登录回跳、企业技能优先级及相关契约说明纳入定向核对。

## Backlog 处理

用户接受上一条逐项建议，并进一步缩小客户端详情/上传范围。已选技能浏览安装、知识库选择将在正式 Requirements 创建时移入；合并待办中的 MCP 部分继续保留。其他不做项保持原文；本调研阶段尚未改动 backlog，避免在新 demand 尚未固定前丢失归属。

## 待联合确认的新增决策

- 纳入 work-opc、platform-opc、knowledge-opc；work 为主要交付端，后两仓仅做必要接口和网页增量。
- 使用已验证的远程 release 基线，采用上述新需求名与标准分支。
- 采用 full + loop；先创建正式 Requirements，不提前创建 Architecture/Specification/Tasks 或编写代码。

产品轻客户端方向无需重复确认。需要确认的是本次代码勘察才确定的跨仓范围、基线和治理模式。
