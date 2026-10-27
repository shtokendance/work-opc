import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppsView } from "./AppsView";
import { appDeliveryRequest, checkAppSlug, getAppDeliveryJob, listDeployedApps, publishAppToPlatform, transformApp, updateDeployedAppConfig } from "../api";

vi.mock("../tauri", () => ({ chooseFolder: vi.fn().mockResolvedValue("/tmp/source-folder") }));

vi.mock("../api", () => ({
  listDeployedApps: vi.fn().mockResolvedValue({
    apps: [
      {
        id: "app-1",
        name: "报销审批系统",
        slug: "expense-app",
        description: "提供内网报销单审批服务",
        app_url: "http://expense-app.apps.opc.intra",
        container_port: 8080,
        status: "running",
        app_config: [{ key: "PORT", value: "8080" }],
        resource_ids: ["shared-pg"],
        skill_name: "app-expense-app",
        skill_synced: true,
        created_at: "2026-10-21T16:00:00Z",
        updated_at: "2026-10-21T16:00:00Z",
      },
    ],
  }),
  listOnlineResources: vi.fn().mockResolvedValue({
    resources: [
      {
        id: "shared-pg",
        name: "内网公共 PostgreSQL",
        type: "postgres",
        description: "公司共享数据库",
        env_keys: ["DATABASE_URL"],
      },
    ],
  }),
  checkAppSlug: vi.fn().mockResolvedValue({
    available: true,
    slug: "expense-app",
    app_url: "http://expense-app.apps.opc.intra",
    skill_name: "app-expense-app",
  }),
  transformApp: vi.fn().mockResolvedValue({ ok: true }),
  appDeliveryRequest: vi.fn().mockResolvedValue({ jobs: [] }),
  getAppDeliveryJob: vi.fn().mockResolvedValue({ status: "analyzing", events: [] }),
  publishAppToPlatform: vi.fn().mockResolvedValue({ ok: true }),
  getDeployedApp: vi.fn().mockResolvedValue({
    status: "running",
    build_log: "Build success",
  }),
  enableLocalSkill: vi.fn().mockResolvedValue({ ok: true }),
  updateDeployedAppConfig: vi.fn().mockResolvedValue({ message: "ok" }),
  getSettings: vi.fn().mockResolvedValue({
    model: "qwen2.5:7b",
    models: ["qwen2.5:7b", "gpt-4o", "deepseek-r1:8b"],
  }),
}));

afterEach(cleanup);

describe("AppsView Component", () => {
  it("releases a per-field resource binding when the user types their own replacement", async () => {
    const original = await listDeployedApps();
    vi.mocked(listDeployedApps).mockResolvedValueOnce({ apps: original.apps.map(app => ({ ...app, app_config: [{ key: "DATABASE_URL", value: "", resource_id: "shared-pg", has_value: true, value_source: "resource", source_label: "内网公共 PostgreSQL" }] })) });
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    render(<AppsView />);
    fireEvent.click(await screen.findByText("报销审批系统"));
    fireEvent.change(await screen.findByPlaceholderText("已配置：由资源注入，留空保留现有值"), { target: { value: "OPC_FIXTURE_MANUAL" } });
    fireEvent.click(screen.getByRole("button", { name: /保存配置/ }));
    await waitFor(() => expect(updateDeployedAppConfig).toHaveBeenCalledWith("expense-app", expect.objectContaining({ app_config: [expect.objectContaining({ value: "OPC_FIXTURE_MANUAL", value_source: "manual", resource_id: undefined })] })));
    alert.mockRestore();
  });
  it("accepts a native-selected folder as the source without requiring a ZIP", async () => {
    vi.mocked(transformApp).mockResolvedValueOnce({ ok: true, job_id: "folder-job" });
    render(<AppsView />);
    fireEvent.click(screen.getByRole("button", { name: "新建应用" }));
    fireEvent.change(screen.getByPlaceholderText(/报销审批助手/), { target: { value: "Folder" } });
    fireEvent.change(screen.getByPlaceholderText(/devops-aly（仅限/), { target: { value: "folder-app" } });
    fireEvent.click(screen.getByRole("button", { name: "选择源码文件夹" }));
    expect(await screen.findByText(/已选择：\/tmp\/source-folder/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "下一步 (AI 改造)" }));
    await waitFor(() => expect(transformApp).toHaveBeenCalledWith(expect.objectContaining({ source_path: "/tmp/source-folder", data_b64: undefined })));
  });
  it("shows resource-injected credentials as already configured and keeps their binding when saved", async () => {
    const original = await listDeployedApps();
    vi.mocked(listDeployedApps).mockResolvedValueOnce({ apps: original.apps.map(app => ({ ...app, app_config: [{ key: "DATABASE_URL", value: "", has_value: true, value_source: "resource", source_label: "内网公共 PostgreSQL" }] })) });
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    render(<AppsView />);
    fireEvent.click(await screen.findByText("报销审批系统"));
    expect(await screen.findByPlaceholderText("已配置：由资源注入，留空保留现有值")).toBeTruthy();
    expect(screen.getByText(/输入框为空表示沿用资源值/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /保存配置/ }));
    await waitFor(() => expect(updateDeployedAppConfig).toHaveBeenCalledWith("expense-app", expect.objectContaining({ resource_ids: ["shared-pg"], app_config: [expect.objectContaining({ key: "DATABASE_URL", value: "" })] })));
    alert.mockRestore();
  });

  it("carries original configuration into the next step and requires an explicit replacement", async () => {
    vi.mocked(transformApp).mockResolvedValueOnce({ ok: true, job_id: "preserved-job" });
    const job = { id: "preserved-job", slug: "stock", name: "Stock", model: "configured-model", app_url: "http://stock.apps.localhost", status: "ready", events: [], artifact_hash: "hash", manifest: { container_port: 8080, configs: [{ key: "DATABASE_URL", value: "" }] }, runtime_configs: [{ key: "DATABASE_URL", value: "OPC_TEST_KEEP_ORIGINAL", value_source: "original", secret: true, required: true }], proposal: { summary: "保留配置", changes: [], issues: [], configs: [{ key: "DATABASE_URL", secret: true, required: true }], apis: [], pages: [], unresolved: [] } };
    vi.mocked(getAppDeliveryJob).mockResolvedValueOnce(job);
    render(<AppsView />);
    fireEvent.click(screen.getByRole("button", { name: "新建应用" }));
    const textboxes = screen.getAllByRole("textbox");
    fireEvent.change(textboxes.find(e => e.getAttribute("placeholder")?.includes("报销审批"))!, { target: { value: "Stock" } });
    fireEvent.change(textboxes.find(e => e.getAttribute("placeholder")?.includes("devops-aly"))!, { target: { value: "stock" } });
    fireEvent.change(screen.getByLabelText("或输入源码文件夹 / ZIP 的完整路径"), { target: { value: "/tmp/stock.zip" } });
    fireEvent.click(screen.getByRole("button", { name: "下一步 (AI 改造)" }));
    fireEvent.click(await screen.findByRole("button", { name: /进入配置，核对原值/ }));
    const value = screen.getByLabelText("DATABASE_URL 配置值") as HTMLInputElement;
    expect(value.value).toBe("OPC_TEST_KEEP_ORIGINAL");
    expect(screen.getByLabelText("DATABASE_URL 原始值").getAttribute("readonly")).not.toBeNull();
    expect(screen.getByRole("button", { name: "使用内网公共 PostgreSQL替换 DATABASE_URL" })).toBeTruthy();
    expect(value.value).toBe("OPC_TEST_KEEP_ORIGINAL");
    fireEvent.click(screen.getByRole("button", { name: "使用内网公共 PostgreSQL替换 DATABASE_URL" }));
    expect(value.value).toBe("");
    fireEvent.click(screen.getByRole("button", { name: "恢复原值" }));
    expect(value.value).toBe("OPC_TEST_KEEP_ORIGINAL");
  });
  it("allows selecting new source for the same failed application without treating its slug as a new collision", async () => {
    const original = await listDeployedApps();
    vi.mocked(listDeployedApps).mockResolvedValueOnce({ apps: original.apps.map(app => ({ ...app, status: "failed" })) });
    vi.mocked(checkAppSlug).mockResolvedValueOnce({ available: false, slug: "expense-app", app_url: "http://expense-app.apps.opc.intra", skill_name: "app-expense-app", message: "occupied" });
    vi.mocked(transformApp).mockResolvedValueOnce({ ok: true, job_id: "new-job" });
    render(<AppsView />);
    const repair = await screen.findByRole("button", { name: /修复.*部署|重新部署/ });
    fireEvent.click(repair);
    const path = await screen.findByRole("textbox", { name: "或输入源码文件夹 / ZIP 的完整路径" });
    fireEvent.change(path, { target: { value: "/tmp/repaired.zip" } });
    fireEvent.click(screen.getByRole("button", { name: "下一步 (AI 改造)" }));
    await waitFor(() => expect(transformApp).toHaveBeenCalledWith(expect.objectContaining({ slug: "expense-app", source_path: "/tmp/repaired.zip" })));
    expect(screen.queryByText("occupied")).toBeNull();
  });

  it("renders app list header and items", async () => {
    render(<AppsView />);

    expect(screen.getByText("应用中心")).toBeTruthy();
    expect(screen.getByText("新建应用")).toBeTruthy();

    const appItem = await screen.findByText("报销审批系统");
    expect(appItem).toBeTruthy();
    expect(screen.getByText("运行中")).toBeTruthy();
  });

  it("retries business verification without rebuilding an already running application", async () => {
    const original = await listDeployedApps();
    vi.mocked(listDeployedApps).mockResolvedValueOnce({ apps: original.apps.map(app => ({ ...app, status: "failed" })) });
    vi.mocked(publishAppToPlatform).mockClear();
    let verifies = 0;
    vi.mocked(appDeliveryRequest).mockImplementation(async <T,>(path: string) => {
      if (path.startsWith("jobs?")) return { jobs: [{ id: "ready-job", artifact_hash: "hash", proposal: { summary: "Approved", changes: [] } }] } as T;
      if (path === "enable-skill" && ++verifies === 1) throw new Error("search business fields mismatch");
      return { confirmation_token: "confirmed", ok: true } as T;
    });
    render(<AppsView />);
    fireEvent.click(await screen.findByRole("button", { name: /修复.*部署|重新部署/ }));
    fireEvent.click(await screen.findByLabelText("我已核对以上配置，同意上传并部署"));
    fireEvent.click(screen.getByRole("button", { name: "确认并上传部署" }));
    fireEvent.click(await screen.findByRole("button", { name: "仅重试业务验证" }, { timeout: 5000 }));
    await waitFor(() => expect(screen.getByText("应用健康与只读业务验证通过，本地技能已启用")).toBeTruthy());
    expect(verifies).toBe(2);
    expect(publishAppToPlatform).toHaveBeenCalledTimes(1);
    vi.mocked(appDeliveryRequest).mockResolvedValue({ jobs: [] });
  });

  it("shows original configuration values directly with an optional hide control", async () => {
    const original = await listDeployedApps();
    vi.mocked(listDeployedApps).mockResolvedValueOnce({ apps: original.apps.map(app => ({ ...app, app_config: [
      { key: "DB_DSN", value: "OPC_TEST_DSN_ONLY" },
      { key: "MONGODB_URI", value: "OPC_TEST_MONGO_ONLY" },
    ] })) });
    render(<AppsView />);
    fireEvent.click(await screen.findByText("报销审批系统"));
    expect((await screen.findByDisplayValue("OPC_TEST_DSN_ONLY")).getAttribute("type")).toBe("text");
    expect(screen.getByDisplayValue("OPC_TEST_MONGO_ONLY").getAttribute("type")).toBe("text");
    fireEvent.click(screen.getAllByRole("button", { name: "隐藏" })[0]);
    expect(screen.getByDisplayValue("OPC_TEST_DSN_ONLY").getAttribute("type")).toBe("password");
  });
});
