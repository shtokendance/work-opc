---
project: work-opc
document: verification
demandId: WORK-RESOURCES-001
repositoryId: work-opc
loop: 1
status: draft
updatedAt: 2026-10-22
---

# work-opc 验证记录

范围：TASK-002、TASK-015～024 的本仓自动化验证。VAL 映射以 [Specification v1.0](../../loop-1/specification.md) 为准；工程扩展逐项结论在末节。未执行项如实列出，不标 PASS。

## 自动化验证

环境：worktree 隔离分支 `feature/261022_WORK-RESOURCES-001_resource-entry`（基线 7be2e4cec8b91f6bb430a332241629500d7bf5ff）；Python 3.13 venv（`uv pip install -e ".[dev]"`）；GUI Node deps（`npm ci`）。测试平台 macOS。

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m pytest tests/test_enterprise_tools.py tests/test_enterprise_auth.py tests/test_enterprise_context.py tests/test_enterprise_skills.py tests/test_enterprise_resources.py tests/test_enterprise_scope.py tests/test_enterprise_installer.py tests/test_enterprise_registry.py tests/test_enterprise_ws.py tests/test_skills.py tests/test_skills_api.py tests/test_skills_store.py tests/test_skills_sessions.py tests/test_persona_skills.py -q` | exit 0，**162 passed** |
| `cd surfaces/gui && npx vitest run src/enterpriseResources.test.ts src/components/ResourcesView.test.tsx src/components/EnterpriseSkillsPanel.test.tsx src/components/ResourceChips.test.tsx` | exit 0，**22 passed** |
| `cd surfaces/gui && npx vitest run`（全量回归） | exit 0，**578 passed / 3 failed**；3 个失败均为 `src/enterprise.test.ts` 既有失败（在未改动的基线工作区复现同样失败，与本轮无关） |
| `cd surfaces/gui && npm run build`（tsc + Vite） | exit 0（`✓ built`） |

## VAL 映射（自动化覆盖部分）

| VAL | 证据 |
|---|---|
| VAL-201 列表/分页/partial | test_knowledge_base_list_signed_in、test_knowledge_base_list_partial_failure（ResourcesView.test.tsx partial 状态） |
| VAL-202 未登录 401 | test_lists_require_signin、test_ws_scope_requires_signin |
| VAL-205 scope 强制 | test_scope_search_fills_fixed_kb_and_records_hits、test_scope_conflict_params_refused_without_upstream（断言无越界上游请求）、test_scope_read_requires_hit_and_double_checks_ownership |
| VAL-206 释放/不跨 turn | test_scope_released_and_no_cross_turn_leak |
| VAL-207 移动后拒绝 | test_scope_read_requires_hit_and_double_checks_ownership（move_knowledge 后 SCOPE_MISMATCH） |
| VAL-208 登录/刷新不下载、无 release | test_login_never_downloads_anything、test_new_chain_never_calls_release、fake 全程 `assert_no_release_calls()` |
| VAL-209 安装 202/幂等 | test_install_flow_202_then_succeeded、EnterpriseSkillsPanel.test.tsx 进度交互 |
| VAL-210 版本冲突 | test_install_version_conflict（409 SKILL_VERSION_CHANGED，不静默装 h2） |
| VAL-211 坏包/保留旧 active | test_bad_package_keeps_previous_active、test_unsafe_package_rejected_and_old_active_intact、test_symlink_entry_rejected、test_active_pointer_never_names_broken_version、P4 |
| VAL-212 同名分辨 | test_same_name_two_subjects_and_spaces_resolve_independently、test_personal_load_skill_never_resolves_enterprise_same_name、P2 |
| VAL-213 turn 固定版本 | test_turn_pins_sha_updates_only_affect_new_turns、test_disable_and_uninstall_forbid_new_starts |
| VAL-214 身份竞争 | test_late_response_never_lands_on_new_identity、test_stale_context_id_rejected、test_operation_owner_scope |
| VAL-216 旧路由 404/旧目录需重装 | test_release_routes_404_and_trip、test_legacy_installs_flagged_not_executable、EnterpriseSkillsPanel legacy 行 |
| VAL-218 旧协议 501/单资源 404 | test_unknown_protocol_maps_501、test_single_item_404_distinct |
| VAL-219 历史无字段兼容/个人回归 | test_history_without_fields_untouched、162 passed 中的个人 skills/persona 回归组 |

## PBT（VAL-102，固定 seed，pytest 内确定性生成器）

| Property | seed | case 数 | 结果 |
|---|---|---|---|
| P1 scope 不扩大（允许集合 ⊆ 所选 KB；冲突参数零上游请求） | 20261022 | 600 | PASS（含冲突 case；越界 read 全拒） |
| P2 registry/active 不跨主体、不误选同名对象 | 20261023 | 600 | PASS（同名对 >0；错误 SHA → VERSION_CHANGED） |
| P3 重复事件+写入失败注入，active 只指完整版本 | 20261024 | 500 序列 | PASS（终态无重复可执行对象） |
| P4 归档路径规范化/链接/重复名/边界不逃逸 | 20261025 | 500 | PASS（合法单根含多层目录接受；3..9 类非法全拒） |

## GUI 行为验证（Vitest，jsdom）

- Composer 资源选择（添加知识按钮/单选替换/仅 scope 禁发/发送后清选择/旧 3 参调用兼容，Composer.resources.test.tsx，5 例）。

- 资源列表五态（signed-out/partial/empty/error/ready）+ 使用仅准备消息 + 详情/上传精确同源（ResourcesView.test.tsx，6 例）。
- 企业技能生命周期（安装进度、not_ready 禁用、legacy 需重装、更新/停用/卸载，EnterpriseSkillsPanel.test.tsx，4 例）。
- URL 安全门（scheme/host/userinfo/端口/ID 段校验、上传仅首页，enterpriseResources.test.ts，12 例）。
- 消息 `_resources` 标签渲染与历史无字段（ResourceChips.test.tsx，4 例）。

## 工程扩展

| 扩展 | 结论 | 证据 |
|---|---|---|
| testing | PASS（自动部分） | VAL-201～219 自动化映射上表；未执行项不标 PASS |
| pbt | PASS | P1～P4 seed/case 见上 |
| security | PASS（自动部分） | URL 安全门、zip/路径注入、身份/context_id 校验、scope 零越界请求、旧协议 tripwire；真实 60 秒撤权计时归 TASK-027，未执行 |
| resiliency | PASS（自动部分） | 崩溃残留清理、active 写失败注入、partial、迟到响应不跨身份；真实备份恢复演练归 TASK-026/027，未执行 |

## 未执行项与原因

- Playwright e2e：本环境无 Playwright 浏览器运行时与显示会话，未执行（TASK-024 允许缺环境单列）。
- VAL-203/204 真实系统浏览器/网页登录回跳、VAL-208 三仓端到端、VAL-215 真实 t0+60s 撤权计时、DMG 手测：均属主仓 TASK-025～027 联调/部署阶段，无测试环境部署授权，未执行。
- 迁移/备份恢复（VAL-217）：work 仓本轮无 SQL 变更（清理迁移在 knowledge/platform 仓），本仓 N/A。
- 打包 DMG：未执行（ TASK-027 范畴）；`npm run build` 产物为可构建性证据。

## 本仓指纹

- 分支：`feature/261022_WORK-RESOURCES-001_resource-entry`（基线 7be2e4c）
- 验证时代码：未提交工作树（交付要求禁止 commit）
- fake 平台 tripwire：全部企业测试通过时 `release_violations` 恒为空

## Review 修复轮追加验证（2026-10-23）

运行环境（如实补记）：pytest 均在 worktree 根目录 CWD 下执行（`/Users/yangjing/code/work-opc.wr001`），解释器为 worktree 内 `.venv`（editable 指向 worktree 本树），命令前缀 `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider`；GUI 测试在 `surfaces/gui` 下执行（node_modules 为 `~/nm-cache` 软链）。

| 命令 | 结果 |
|---|---|
| 修复轮受影响文件 pytest（installer/registry/resources/scope/ws） | exit 0，全过（过程中修复 4 个测试自身缺陷） |
| 全量 enterprise 组（9 文件） | exit 0，**94 passed**（含新增 B1/B2/M3/M4/M5/M6/minor 回归 13 例） |
| GUI 全量 `npx vitest run` | exit 0，**583 passed / 3 failed**（同前，基线预存） |
| GUI 定向（enterpriseResources/ResourcesView/EnterpriseSkillsPanel/ResourceChips/Composer.resources） | **32 passed**，复跑 18 passed |
| `npm run build` | exit 0（修复 2 个 TS6133 后） |

新增回归映射：B1→installer 3 例；B2→registry 缓存键 1 例 + ws 端到端 load 1 例；M1→Composer 2 例；M2→Composer 键盘 2 例；M3→ws retry 1 例；M4→ws display 1 例（并更新 TASK-019 旧断言）；M5→resources PATCH 409 1 例 + installer 锁竞争 1 例；M6→registry 卸载 409/回收 1 例；M7→remoteSessionResourceBlock 2 例；minor→scope 字段 1 例、install 错误映射 1 例、坏 zip 条目 1 例、停用钉版 1 例。

## 增量（企业技能并入「/」菜单）验证（2026-10-23）

| 命令 | 结果 |
|---|---|
| `pytest tests/test_enterprise_context.py`（含 5 个新菜单合并用例） | 13 passed |
| 全量 enterprise + skills 回归（12 文件） | exit 0，**137 passed** |
| GUI 定向（Composer.enterprise-skills 4 例 + Composer.skills/draft/resources 回归） | 27 passed |
| GUI 全量 `npx vitest run` | **587 passed / 3 failed**（同基线预存 enterprise.test.ts 3 例） |
| `npm run build` | exit 0 |

新回归覆盖：已装企业行入菜单（元数据完整）/停用行 enabled=false/无上下文不合并/同名并存+个人 mute 不影响/卸载即出菜单；GUI：企业徽标渲染+同名共存、选中企业项产生 skill_ref 无文本注入、个人路径不变、停用行被过滤。

## 纠正轮（httpBase 绝对地址恢复）验证（2026-10-23）

| 命令 | 结果 |
|---|---|
| GUI 定向（enterpriseResources/ResourcesView/EnterpriseSkillsPanel/Composer.resources/Composer.enterprise-skills） | 32 passed |
| GUI 全量 `npx vitest run` | 587 passed / 3 failed（enterprise.test.ts 基线预存 3 例） |
| `npm run build` | exit 0 |
