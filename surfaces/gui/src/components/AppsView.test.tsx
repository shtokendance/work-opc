import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AppsView } from "./AppsView";
import { appDeliveryRequest, checkAppSlug, getAppDeliveryJob, listDeployedApps, publishAppToPlatform, recheckAppHealth, transformApp, updateDeployedAppConfig } from "../api";

vi.mock("../tauri", () => ({ chooseFolder: vi.fn().mockResolvedValue("/tmp/source-folder") }));

vi.mock("../api", () => ({
  platformBase: vi.fn().mockReturnValue("http://10.101.102.116:3002"),
  getPlatformConfig: vi.fn().mockResolvedValue({
    base_url: "http://10.101.102.116:3002",
    configured: true,
    source: "default",
  }),
  updatePlatformConfig: vi.fn().mockResolvedValue({
    base_url: "http://10.101.102.116:3002",
    configured: true,
    source: "runtime_override",
  }),
  getModelControls: vi.fn().mockResolvedValue({
    thinking: { support: "supported", default: true },
    reasoning: { support: "supported", levels: ["low", "medium", "high"], default: "medium" },
  }),
  listAppVersions: vi.fn().mockResolvedValue({ versions: [] }),
  rollbackAppVersion: vi.fn().mockResolvedValue({ message: "ok" }),
  recheckAppHealth: vi.fn().mockResolvedValue({ ok: true, status: "running", message: "应用健康检查通过" }),
  getAppVersionArtifactUrl: vi.fn().mockReturnValue("http://10.101.102.116:3002/api/v1/apps/app-1/versions/1/artifacts/app.tar"),
  getAppRuntimeLogs: vi.fn().mockResolvedValue({ logs: "" }),
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
  getOpcConfig: vi.fn().mockResolvedValue({
    platform_base: "http://10.101.102.116:3002",
    deploy_base: "http://10.101.102.116:3002",
    web_origin: "http://10.101.102.116:3002",
    app_domain_root: "10.101.102.116.nip.io:8080",
    app_default_port: 8080,
    effective_platform_base: "http://10.101.102.116:3002",
    effective_deploy_base: "http://10.101.102.116:3002",
    effective_web_origin: "http://10.101.102.116:3002",
    effective_app_domain_root: "10.101.102.116.nip.io:8080",
    effective_app_default_port: 8080,
    default_platform_base: "https://shtokendance.benlai.com",
    default_app_domain_root: "10.101.102.116.nip.io:8080",
    default_app_default_port: 8080,
  }),
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
  it("shows shared applications without offering management actions", async () => {
    const original = await listDeployedApps();
    vi.mocked(listDeployedApps).mockResolvedValueOnce({
      apps: original.apps.map(app => ({ ...app, can_manage: false })),
    });
    render(<AppsView />);
    expect(await screen.findByText("共享应用")).toBeTruthy();
    expect(screen.getByRole("link", { name: "http://expense-app.apps.opc.intra" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "升级新版本" })).toBeNull();
    fireEvent.click(screen.getByText("报销审批系统"));
    expect(await screen.findByText("环境变量与运行参数")).toBeTruthy();
    expect(screen.getByText("只读模式")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /保存配置/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "添加变量" })).toBeNull();
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
    vi.mocked(getAppDeliveryJob).mockResolvedValue(job);
    render(<AppsView />);
    fireEvent.click(screen.getByRole("button", { name: "新建应用" }));
    const textboxes = screen.getAllByRole("textbox");
    fireEvent.change(textboxes.find(e => e.getAttribute("placeholder")?.includes("报销审批"))!, { target: { value: "Stock" } });
    fireEvent.change(textboxes.find(e => e.getAttribute("placeholder")?.includes("devops-aly"))!, { target: { value: "stock" } });
    fireEvent.change(screen.getByLabelText("或输入源码文件夹 / ZIP 的完整路径"), { target: { value: "/tmp/stock.zip" } });
    fireEvent.click(screen.getByRole("button", { name: "下一步 (AI 改造)" }));
    fireEvent.click(await screen.findByRole("button", { name: /下一步.*核对部署内容/ }));
    const value = await screen.findByLabelText("DATABASE_URL 配置值") as HTMLInputElement;
    expect(value.value).toBe("OPC_TEST_KEEP_ORIGINAL");
    expect(screen.getByLabelText("DATABASE_URL 原始值").getAttribute("readonly")).not.toBeNull();
    expect(screen.getByRole("button", { name: "使用内网公共 PostgreSQL替换 DATABASE_URL" })).toBeTruthy();
    expect(value.value).toBe("OPC_TEST_KEEP_ORIGINAL");
    fireEvent.click(screen.getByRole("button", { name: "使用内网公共 PostgreSQL替换 DATABASE_URL" }));
    expect(value.value).toBe("");
    fireEvent.click(screen.getByRole("button", { name: "恢复原值" }));
    expect(value.value).toBe("OPC_TEST_KEEP_ORIGINAL");
    fireEvent.click(screen.getByRole("tab", { name: /应用技能/ }));
    expect(screen.getByLabelText("生成的业务技能")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: /改造与检查/ }));
    expect(screen.getByLabelText("本次改造摘要")).toBeTruthy();
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
      if (path.startsWith("jobs?")) return { jobs: [{ id: "ready-job", artifact_hash: "hash", proposal: { summary: "Approved", changes: [], apis: [{id:"lookup",auth:{type:"header",profile:"account"}}], verification_credential_fields: [{key:"APP_USER",kind:"username"},{key:"APP_PASSWORD",kind:"password"}] } }] } as T;
      if (path === "enable-skill" && ++verifies === 1) throw new Error("search business fields mismatch");
      return { confirmation_token: "confirmed", ok: true } as T;
    });
    render(<AppsView />);
    fireEvent.click(await screen.findByRole("button", { name: /修复.*部署|重新部署/ }));
    fireEvent.click(await screen.findByRole("button", { name: "确认并上传部署" }));
    const username = await screen.findByLabelText("业务验证凭据 APP_USER", {}, { timeout: 3500 });
    const password = screen.getByLabelText("业务验证凭据 APP_PASSWORD");
    expect(username.getAttribute("type")).toBe("text");
    expect(password.getAttribute("type")).toBe("password");
    fireEvent.change(username, { target: { value: "test-user" } });
    fireEvent.change(password, { target: { value: "test-password" } });
    fireEvent.click(await screen.findByRole("button", { name: "仅重试业务验证" }, { timeout: 5000 }));
    await waitFor(() => expect(screen.getByText("应用验证通过，本地技能已启用")).toBeTruthy());
    expect(verifies).toBe(2);
    expect(appDeliveryRequest).toHaveBeenCalledWith("enable-skill", expect.objectContaining({verification_credentials:{APP_USER:"test-user",APP_PASSWORD:"test-password"}}));
    expect(publishAppToPlatform).toHaveBeenCalledTimes(1);
    vi.mocked(appDeliveryRequest).mockResolvedValue({ jobs: [] });
  });

  it("shows the address for a page app and waits for page confirmation before presenting the skill", async () => {
    const original = await listDeployedApps();
    vi.mocked(listDeployedApps).mockResolvedValueOnce({ apps: original.apps.map(app => ({ ...app, status: "failed" })) });
    vi.mocked(appDeliveryRequest).mockImplementation(async <T,>(path: string, body?: unknown) => {
      if (path.startsWith("jobs?")) return { jobs: [{ id: "page-job", artifact_hash: "hash" }] } as T;
      if (path === "enable-skill") {
        const confirmed = (body as { confirm_page_verification?: boolean }).confirm_page_verification;
        return { ok: true, verification: { status: confirmed ? "verified-deployed-page" : "awaiting_page_verification", skill_enabled: confirmed === true } } as T;
      }
      return { confirmation_token: "confirmed", ok: true } as T;
    });
    render(<AppsView />);
    fireEvent.click(await screen.findByRole("button", { name: /修复.*部署|重新部署/ }));
    fireEvent.click(await screen.findByRole("button", { name: "确认并上传部署" }));
    const confirm = await screen.findByRole("button", { name: "页面可用，启用应用技能" }, { timeout: 4000 });
    expect(within(screen.getByRole("region", { name: "应用访问地址" })).getByRole("link", { name: /expense-app/ }).getAttribute("href")).toContain("expense-app");
    expect(screen.queryByText(/本地技能已启用/)).toBeNull();
    fireEvent.click(confirm);
    await screen.findByText("应用验证通过，本地技能已启用");
    expect(appDeliveryRequest).toHaveBeenCalledWith("enable-skill", expect.objectContaining({ confirm_page_verification: true }));
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

  it("allows rechecking health for a failed app and recovers running status", async () => {
    vi.mocked(listDeployedApps).mockResolvedValueOnce({
      apps: [
        {
          id: "app-failed",
          name: "自动报销系统",
          slug: "smart-expense",
          description: "报销系统",
          app_url: "http://smart-expense.10.101.102.116.nip.io:8080",
          container_port: 8080,
          status: "failed",
          app_config: [],
          resource_ids: [],
          skill_name: "app-smart-expense",
          skill_synced: false,
          created_at: "2026-10-21T16:00:00Z",
          updated_at: "2026-10-21T16:00:00Z",
        },
      ],
    });
    render(<AppsView />);
    const recheckBtn = await screen.findByRole("button", { name: /重新检查/ });
    expect(recheckBtn).toBeTruthy();
    expect(screen.getByRole("button", { name: /重新部署/ })).toBeTruthy();
    fireEvent.click(recheckBtn);
    expect(recheckAppHealth).toHaveBeenCalledWith("smart-expense", "http://smart-expense.10.101.102.116.nip.io:8080");
  });

  it("shows redeploy and recheck entrances in details view for a failed app", async () => {
    vi.mocked(listDeployedApps).mockResolvedValueOnce({
      apps: [
        {
          id: "app-failed",
          name: "自动报销系统",
          slug: "smart-expense",
          description: "报销系统",
          app_url: "http://smart-expense.10.101.102.116.nip.io:8080",
          container_port: 8080,
          status: "failed",
          can_manage: false,
          app_config: [],
          resource_ids: [],
          skill_name: "app-smart-expense",
          skill_synced: false,
          created_at: "2026-10-21T16:00:00Z",
          updated_at: "2026-10-21T16:00:00Z",
        },
      ],
    });
    render(<AppsView />);
    fireEvent.click(await screen.findByText("自动报销系统"));
    expect(await screen.findByText("该应用上次部署未成功，容器未正常运行")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /重新检查健康状态/ }).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByRole("button", { name: "重新部署" }).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("button", { name: "重新部署此应用" })).toBeTruthy();
  });

  it("automatically generates pinyin slug from app name and handles IME composition in slug field", async () => {
    render(<AppsView />);
    fireEvent.click(screen.getByRole("button", { name: "新建应用" }));

    const nameInput = screen.getByPlaceholderText(/报销审批助手/);
    const slugInput = screen.getByPlaceholderText(/devops-aly（仅限/);

    // 1. Typing Chinese name automatically generates pinyin slug
    fireEvent.change(nameInput, { target: { value: "自动报销系统" } });
    expect((slugInput as HTMLInputElement).value).toBe("zi-dong-bao-xiao-xi-tong");

    // 2. Simulating IME composition in slug input does not disrupt or duplicate
    fireEvent.compositionStart(slugInput);
    fireEvent.change(slugInput, { target: { value: "baoxiao" } });
    // Still not clobbering while composing
    fireEvent.compositionEnd(slugInput, { target: { value: "报销" } });
    expect((slugInput as HTMLInputElement).value).toBe("bao-xiao");

    // 3. User can click reset to re-derive from name
    const resetBtn = screen.getByRole("button", { name: "根据应用名称重新生成" });
    fireEvent.click(resetBtn);
    expect((slugInput as HTMLInputElement).value).toBe("zi-dong-bao-xiao-xi-tong");
  });
});
