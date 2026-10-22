import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { AppsView } from "./AppsView";

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
  publishAppToPlatform: vi.fn().mockResolvedValue({ ok: true }),
  getDeployedApp: vi.fn().mockResolvedValue({
    status: "running",
    build_log: "Build success",
  }),
  enableLocalSkill: vi.fn().mockResolvedValue({ ok: true }),
  updateDeployedAppConfig: vi.fn().mockResolvedValue({ message: "ok" }),
}));

afterEach(cleanup);

describe("AppsView Component", () => {
  it("renders app list header and items", async () => {
    render(<AppsView />);

    expect(screen.getByText("应用中心与技能管理")).toBeTruthy();
    expect(screen.getByText("新建应用")).toBeTruthy();

    const appItem = await screen.findByText("报销审批系统");
    expect(appItem).toBeTruthy();
    expect(screen.getByText("运行中")).toBeTruthy();
  });
});
