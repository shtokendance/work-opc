import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppDeliveryReview } from "./AppDeliveryReview";
import { appDeliveryRequest, getAppDeliveryJob } from "../api";
vi.mock("../api", () => ({ getAppDeliveryJob: vi.fn(), appDeliveryRequest: vi.fn() }));
const plan = {
  id: "job",
  slug: "stock",
  name: "Stock",
  status: "review",
  model: "configured",
  app_url: "http://stock.test",
  events: [],
  proposal_hash: "v1",
  discovery_report: {
    project_purpose: "保留业务",
    tech_stack: { language: "Python", runtime_version: "3.11", syntax_requirements: ["StrEnum (Python 3.11+)"], container_port: 8000 },
    storage_and_database: { storage_type: "sqlite", storage_migration_plan: "迁移到平台 PostgreSQL", can_migrate_to_platform_pg: true },
    pages_overview: { total: 2, items: [{ path: "/", purpose: "首页" }, { path: "/admin", purpose: "管理后台" }] },
    apis_overview: { total: 2, modules: { auth: 1, stock: 1 }, read_count: 1, write_count: 1, items: [{ id: "login", method: "POST", path: "/api/auth/login", purpose: "登录", effect: "write" }, { id: "stock", method: "GET", path: "/api/stock", purpose: "查询库存", effect: "read" }] },
    planned_adaptations: ["改造 settings.py：支持服务器启动"],
  },
  proposal: {
    summary: "保留业务",
    unresolved: [] as string[],
    issues: [],
    changes: [{ id: "C1", path: "settings.py", reason: "支持服务器启动", diff: "-before\n+after" }],
    configs: [],
    apis: [],
    pages: [],
  },
};
const ready = {
  ...plan,
  status: "ready",
  artifact_hash: "a",
  transformation_report: {
    headline: "已完成改造：新增 2 个平台交付文件，改造 1 个源码文件，删除 0 个文件。",
    added_files: [{ path: "Dockerfile", reason: "容器构建文件" }, { path: "captain-definition", reason: "平台部署配置" }],
    modified_files: [{ path: "settings.py", reason: "支持服务器启动" }],
    deleted_files: [],
    storage_summary: "数据库已配置为 postgresql（已纳入 3 张表结构与数据迁移）",
    skill_summary: "已生成完整应用业务技能（包含 2 个页面视图、2 个后端接口）",
  },
};
afterEach(cleanup);
beforeEach(() => { vi.clearAllMocks(); vi.mocked(getAppDeliveryJob).mockResolvedValue(plan); vi.mocked(appDeliveryRequest).mockResolvedValue(ready); });
it("shows Step 1 discovery report and prepares Step 2 only after user clicks confirmation, then renders Step 2 report", async () => {
  const onReady=vi.fn(), next=vi.fn();
  render(<AppDeliveryReview jobId="job" onReady={onReady} onNextStep={next} />);
  expect(await screen.findByLabelText("第一步：AI 深度分析结果")).toBeTruthy();
  expect(screen.getByText(/StrEnum \(Python 3\.11\+\)/)).toBeTruthy();
  expect(appDeliveryRequest).not.toHaveBeenCalledWith("jobs/job/prepare", expect.anything());
  const confirmBtn = await screen.findByRole("button", { name: "确认分析结果，执行第二步改造" });
  fireEvent.click(confirmBtn);
  await waitFor(()=>expect(appDeliveryRequest).toHaveBeenCalledWith("jobs/job/prepare",{proposal_hash:"v1"}));
  expect(await screen.findByLabelText("第二步：应用改造完成报告")).toBeTruthy();
  expect(screen.getByText(/新增了什么（2 项）/)).toBeTruthy();
  expect(screen.getByText(/改造了什么（1 项）/)).toBeTruthy();
  const advance=await screen.findByRole("button",{name:"下一步：核对部署内容"});
  expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
  expect(screen.getAllByRole("textbox")).toHaveLength(1);
  expect(onReady).not.toHaveBeenCalled();
  vi.mocked(getAppDeliveryJob).mockResolvedValue(ready);
  fireEvent.click(advance);
  await waitFor(()=>expect(next).toHaveBeenCalledOnce());
  expect(onReady).toHaveBeenCalledWith(ready);
});
it("keeps one composer, permits partial answers and never prepares while questions remain", async () => {
  vi.mocked(getAppDeliveryJob).mockResolvedValue({...plan,proposal:{...plan.proposal,unresolved:["数据库怎么连接？","是否迁移旧数据？"],decisions:[{id:"db",question:"数据库怎么连接？",impact:"原连接指向电脑",options:["保留 MySQL"]}]}});
  render(<AppDeliveryReview jobId="job" onReady={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button",{name:"保留 MySQL"}));
  expect(appDeliveryRequest).not.toHaveBeenCalled();
  expect(screen.getAllByRole("textbox")).toHaveLength(1);
  fireEvent.change(screen.getByLabelText("给 AI 的回答或要求"),{target:{value:"保留 MySQL。先解释迁移的影响，我还没同意迁移。"}});
  fireEvent.click(screen.getByRole("button",{name:"发送"}));
  await waitFor(()=>expect(appDeliveryRequest).toHaveBeenCalledWith("jobs/job/analyze",{feedback:"保留 MySQL。先解释迁移的影响，我还没同意迁移。"}));
  expect(appDeliveryRequest).not.toHaveBeenCalledWith("jobs/job/prepare",expect.anything());
});
it("blocks next for unsent changes and invalidates old parent readiness on reanalysis", async () => {
  vi.mocked(getAppDeliveryJob).mockResolvedValue(ready);
  const invalidate=vi.fn();
  render(<AppDeliveryReview jobId="job" onReady={vi.fn()} onInvalidate={invalidate} />);
  await screen.findByText("应用已准备好，可以核对部署内容");
  fireEvent.change(screen.getByLabelText("给 AI 的回答或要求"),{target:{value:"保留原数据库"}});
  expect((screen.getByRole("button",{name:"下一步：核对部署内容"}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button",{name:"发送"}));
  await waitFor(()=>expect(invalidate).toHaveBeenCalledOnce());
});
it("rejects next when the server package changes between polling and clicking", async () => {
  vi.mocked(getAppDeliveryJob).mockResolvedValue(ready);
  const next=vi.fn();
  render(<AppDeliveryReview jobId="job" onReady={vi.fn()} onNextStep={next} />);
  const button=await screen.findByRole("button",{name:"下一步：核对部署内容"});
  vi.mocked(getAppDeliveryJob).mockResolvedValue({...ready,artifact_hash:"new"});
  fireEvent.click(button);
  await screen.findByRole("alert");
  expect(next).not.toHaveBeenCalled();
});
it("keeps loopback decisions on the preparation page and saves configuration outside AI feedback", async () => {
  const configs=[{key:"DATABASE_URL",value:"mysql://localhost/app",required:true,secret:true}];
  const pending={...ready, runtime_configs:configs, configuration_problems:[{key:"DATABASE_URL",reason:"连接指向容器自己"}]};
  vi.mocked(getAppDeliveryJob).mockResolvedValue(pending);
  render(<AppDeliveryReview jobId="job" onReady={vi.fn()} />);
  fireEvent.change(await screen.findByLabelText("DATABASE_URL 配置值"),{target:{value:"mysql://internal-db/app"}});
  expect(screen.queryByRole("button",{name:"下一步：核对部署内容"})).toBeNull();
  vi.mocked(getAppDeliveryJob).mockResolvedValue({...pending,configuration_problems:[],config_draft:{artifact_hash:"a",configs:[{...configs[0],value:"mysql://internal-db/app"}]}});
  fireEvent.click(screen.getByRole("button",{name:"确认配置并检查"}));
  await screen.findByRole("button",{name:"下一步：核对部署内容"});
  expect(appDeliveryRequest).toHaveBeenCalledWith("jobs/job/config-draft",expect.objectContaining({artifact_hash:"a",configs:[expect.objectContaining({value:"mysql://internal-db/app"})]}));
  expect(appDeliveryRequest).not.toHaveBeenCalledWith("jobs/job/analyze",expect.anything());
});
it("exports versioned external AI handoff without deployment approval", async () => {
  const copy=vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator,"clipboard",{configurable:true,value:{writeText:copy}});
  vi.mocked(getAppDeliveryJob).mockResolvedValue(ready);
  vi.mocked(appDeliveryRequest).mockResolvedValue({prompt:"保留原配置",source_path:"/tmp/handoff-v1"});
  render(<AppDeliveryReview jobId="job" onReady={vi.fn()} />);
  await screen.findByText("应用已准备好，可以核对部署内容");
  fireEvent.click(screen.getByRole("button",{name:"复制交接说明"}));
  await waitFor(()=>expect(copy).toHaveBeenCalledWith("保留原配置"));
  expect(screen.getByText(/handoff-v1/)).toBeTruthy();
  expect(appDeliveryRequest).not.toHaveBeenCalledWith("jobs/job/confirm-deployment",expect.anything());
});

it("uses a short editable answer and replaces a previous suggestion for the same question", async () => {
  vi.mocked(getAppDeliveryJob).mockResolvedValue({...plan,proposal:{...plan.proposal,unresolved:["数据库怎么连接？"],decisions:[{id:"db",question:"数据库怎么连接？",impact:"需要明确目标",options:["保留原值","使用测试库"]}]}});
  render(<AppDeliveryReview jobId="job" onReady={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button",{name:"保留原值"}));
  expect((screen.getByLabelText("给 AI 的回答或要求") as HTMLTextAreaElement).value).toBe("保留原值");
  fireEvent.click(screen.getByRole("button",{name:"使用测试库"}));
  expect((screen.getByLabelText("给 AI 的回答或要求") as HTMLTextAreaElement).value).toBe("使用测试库");
  expect(appDeliveryRequest).not.toHaveBeenCalled();
});

it("displays multi-round conversation as chat messages and sends AI fix request directly on click", async () => {
  vi.mocked(getAppDeliveryJob).mockResolvedValue({
    ...plan,
    status: "failed",
    revision: 2,
    error: "possible secret in build input: package.json",
    conversation: [
      { role: "assistant", text: "第一轮分析结论", revision: 1 },
      { role: "user", text: "不切换保留现有存储", revision: 1 },
      { role: "assistant", text: "第二轮分析结论", revision: 2 },
    ],
  });
  render(<AppDeliveryReview jobId="job" onReady={vi.fn()} />);
  expect(await screen.findByText("第一轮分析结论")).toBeTruthy();
  expect(screen.getByText("不切换保留现有存储")).toBeTruthy();
  expect(screen.getByText("第二轮分析结论")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "请 AI 帮我处理" }));
  await waitFor(() =>
    expect(appDeliveryRequest).toHaveBeenCalledWith("jobs/job/analyze", {
      feedback: "请处理这次未完成的问题，保留原业务和已确认的选择：\npossible secret in build input: package.json",
    })
  );
});

it("renders AI progress steps in conversation timeline and omits redundant details dropdown", async () => {
  vi.mocked(getAppDeliveryJob).mockResolvedValue({
    ...plan,
    status: "analyzing",
    events: [
      "🚀 开始分析应用源码与技术栈架构…",
      "📖 正在深入分析源码文件: package.json",
      "🐳 匹配官方适配 Dockerfile 容器模板 (nodejs)",
    ],
  });
  render(<AppDeliveryReview jobId="job" onReady={vi.fn()} />);
  expect(screen.queryByText("查看检查与执行详情")).toBeNull();
  expect(await screen.findByText("AI 正在检查与改造应用…")).toBeTruthy();
  expect(screen.getByText(/正在深入分析源码文件: package.json/)).toBeTruthy();
  expect(screen.getByText(/匹配官方适配 Dockerfile 容器模板/)).toBeTruthy();
});

it("formats long Step 1 summaries into structured blocks and supports external footer next button", async () => {
  vi.mocked(getAppDeliveryJob).mockResolvedValue({
    ...ready,
    conversation: [
      {
        role: "assistant",
        revision: 1,
        text: "第一步发现：这是供应商审厂管理系统，包含供应商档案与审厂任务。第二步拟新增容器启动文件，保留原有 JSON 数据机制。第三步契约纳入扫描发现的全部页面与接口。",
      },
    ],
  });
  const onStepState = vi.fn();
  const next = vi.fn();
  render(
    <AppDeliveryReview
      jobId="job"
      onReady={vi.fn()}
      onNextStep={next}
      footerMode="external"
      onStepStateChange={onStepState}
    />
  );
  expect(await screen.findByText("第一步发现")).toBeTruthy();
  expect(screen.getByText(/这是供应商审厂管理系统/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "下一步：核对部署内容" })).toBeNull();
  expect(screen.getByText(/请点击右下角「下一步」/)).toBeTruthy();
  await waitFor(() => expect(onStepState).toHaveBeenCalledWith(expect.objectContaining({ canContinue: true })));
});

it("submits deployment failures in explicit repair mode and explains verification limits", async () => {
  render(<AppDeliveryReview jobId="job" onReady={vi.fn()} repairMode initialFeedback="SyntaxError: Illegal return statement" />);
  const input = await screen.findByLabelText("给 AI 的回答或要求");
  fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });
  await waitFor(() => expect(appDeliveryRequest).toHaveBeenCalledWith("jobs/job/analyze", {
    feedback: "SyntaxError: Illegal return statement", mode: "repair",
  }));
  expect(screen.getByText(/分析提案不等于修复通过/)).toBeTruthy();
});
