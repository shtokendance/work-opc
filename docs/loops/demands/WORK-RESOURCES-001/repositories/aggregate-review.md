---
project: work-opc
document: aggregate-review
demandId: WORK-RESOURCES-001
loop: 1
status: draft
updatedAt: 2026-10-23
---

# 跨仓聚合审查（WORK-RESOURCES-001 Loop 1）

## 结论

**READY_FOR_HUMAN_REVIEW** — 三仓独立审查均 READY_FOR_HUMAN_REVIEW，跨仓契约两端、发布顺序、回滚、receipt 与追溯包校验全部对齐。

## 核对项

| 核对项 | 结果 | 证据 |
|---|---|---|
| 契约两端一致 | PASS | platform resources.go 与 knowledge skill_catalog_archive.go 逐字段对齐（差量复审核实 INT-105 映射）；work 客户端 resources.py/knowledge_upstream 形状一致（D2 联调实测真实请求） |
| 归档链路三 hash | PASS | knowledge 服务端 expected/current/actual 三 SHA（TASK-003）↔ platform 代理三向校验（TASK-010）↔ work 安装器磁盘字节核验（D2：installed_sha256 与目录声明一致） |
| 错误语义一致 | PASS | 401 SESSION_REVOKED / 403 SCOPE_MISMATCH、SPACE_FORBIDDEN / 404 RESOURCE_NOT_ACCESSIBLE / 409 / 422 在 D1 冒烟与 D2 联调两端实测一致；request_id 嵌套形状（m-3 修复）经公网入口实测 |
| 零 release 依赖 | PASS | VAL-208：联调窗口 gateway/knowledge 日志 release 端点调用为 0；三仓源码/DI/路由无残留（各 review.md） |
| 版本兼容与发布顺序 | PASS | knowledge → platform → work 与 release-checklist.md 一致；迁移 000133/000047/000017 均有 down + 备份恢复演练（VAL-217） |
| 数据链路与回滚 | PASS | 部署前 pg_dump 备份 + 恢复演练（deploy-evidence-wr001.md）；回滚点旧镜像 ID 记录 |
| 撤权实测 | PASS | VAL-215：t0→t1 ≈0.31s ≤ 60s，三端时序证据（integration-evidence-wr001.md） |
| 三仓 readiness receipt | PASS | repositories/<repoId>/repository-receipt.json 均 ready=true，ref/tree 指纹与各仓实施 commit 一致 |
| 参与仓追溯包 | PASS | platform participantCommit c988489（package 8f9fc4c2…）；knowledge participantCommit 8520f683（package 5b1029e6…）；均从 commit 重新校验 22 项通过，authority.commit=ba1211aa |
| 上线清单 | PASS | release-checklist.md ready，规范化 hash 已固定；未执行项保持 [ ] 未预勾 |

## 未决与残余

- VAL-203/204 自动化网页用例未执行（用户真机手测通过，账号限制见 verification.md 未执行项）。
- 跨空间同名资源受测试环境单空间限制未实测。
- Playwright e2e 与 GUI 视觉回归未执行（backlog 记录）。
- 参与仓 commit 均为本地提交，合并/推送需用户另行授权。

## 签署信息（待填）

聚合审查版本 / 代码指纹 / 确认人 / 时间：待最终签署时补录。
