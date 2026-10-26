import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppsView } from "./AppsView";
import { appDeliveryRequest, checkAppSlug, listDeployedApps, publishAppToPlatform, transformApp } from "../api";

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
  it("allows selecting new source for the same failed application without treating its slug as a new collision", async () => {
    const original = await listDeployedApps();
    vi.mocked(listDeployedApps).mockResolvedValueOnce({ apps: original.apps.map(app => ({ ...app, status: "failed" })) });
    vi.mocked(checkAppSlug).mockResolvedValueOnce({ available: false, slug: "expense-app", app_url: "http://expense-app.apps.opc.intra", skill_name: "app-expense-app", message: "occupied" });
    vi.mocked(transformApp).mockResolvedValueOnce({ ok: true, job_id: "new-job" });
    render(<AppsView />);
    const repair = await screen.findByRole("button", { name: /修复.*部署|重新部署/ });
    fireEvent.click(repair);
    const path = await screen.findByRole("textbox", { name: "或输入本机 ZIP 完整路径" });
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
    fireEvent.click(await screen.findByLabelText("已核对最终修改、配置及资源连接，确认上传同一交付包"));
    fireEvent.click(screen.getByRole("button", { name: "确认并上传部署" }));
    fireEvent.click(await screen.findByRole("button", { name: "仅重试业务验证" }, { timeout: 5000 }));
    await waitFor(() => expect(screen.getByText("应用健康与只读业务验证通过，本地技能已启用")).toBeTruthy());
    expect(verifies).toBe(2);
    expect(publishAppToPlatform).toHaveBeenCalledTimes(1);
    vi.mocked(appDeliveryRequest).mockResolvedValue({ jobs: [] });
  });

  it("masks database connection strings in the configuration drawer until explicitly revealed", async () => {
    const original = await listDeployedApps();
    vi.mocked(listDeployedApps).mockResolvedValueOnce({ apps: original.apps.map(app => ({ ...app, app_config: [
      { key: "DB_DSN", value: "OPC_TEST_DSN_ONLY" },
      { key: "MONGODB_URI", value: "OPC_TEST_MONGO_ONLY" },
    ] })) });
    render(<AppsView />);
    fireEvent.click(await screen.findByText("报销审批系统"));
    expect((await screen.findByDisplayValue("OPC_TEST_DSN_ONLY")).getAttribute("type")).toBe("password");
    expect(screen.getByDisplayValue("OPC_TEST_MONGO_ONLY").getAttribute("type")).toBe("password");
    fireEvent.click(screen.getAllByRole("button", { name: "显示" })[0]);
    expect(screen.getByDisplayValue("OPC_TEST_DSN_ONLY").getAttribute("type")).toBe("text");
  });
});
