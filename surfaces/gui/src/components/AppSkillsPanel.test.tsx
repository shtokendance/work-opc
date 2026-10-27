import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppSkillsPanel } from "./AppSkillsPanel";

type Call = { url: string; method: string; body: any };

function stubFetch(routes: { match: string; method?: string; json: any }[]) {
  const calls: Call[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const method = (init?.method || "GET").toUpperCase();
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    for (const r of routes) {
      if (url.includes(r.match) && (!r.method || r.method === method)) {
        return { ok: true, json: async () => r.json } as Response;
      }
    }
    return { ok: true, json: async () => ({}) } as Response;
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("AppSkillsPanel", () => {
  it("renders empty state when there are no jobs and no app skills", async () => {
    stubFetch([
      { match: "/v1/apps/jobs", method: "GET", json: { jobs: [] } },
      { match: "/v1/skills", method: "GET", json: { skills: [] } },
    ]);
    const onNavigate = vi.fn();

    render(<AppSkillsPanel onNavigateToApps={onNavigate} />);

    expect(await screen.findByText("暂无可用的应用技能")).toBeTruthy();
    const btn = screen.getByText("前往应用中心");
    fireEvent.click(btn);
    expect(onNavigate).toHaveBeenCalledTimes(1);
  });

  it("renders app skills list when jobs and skills exist", async () => {
    stubFetch([
      {
        match: "/v1/apps/jobs",
        method: "GET",
        json: {
          jobs: [
            {
              id: "job-1",
              slug: "order-query",
              name: "订单查询服务",
              description: "查询订单业务明细及履约状态",
              app_url: "http://10.101.102.116:30001",
              status: "running",
              model: "openai/gpt-4o",
              events: [],
              artifacts: { "skill.zip": "/path/to/skill.zip" },
              proposal: {
                summary: "order service",
                issues: [],
                unresolved: [],
                changes: [],
                configs: [],
                apis: [
                  { id: "get-orders", purpose: "查询订单列表", method: "GET", path: "/api/orders", effect: "read" },
                ],
                pages: [],
              },
            },
          ],
        },
      },
      {
        match: "/v1/skills",
        method: "GET",
        json: {
          skills: [
            {
              name: "app-order-query",
              description: "查询订单业务明细及履约状态",
              instructions: "Call invoke_api",
              scope: "global",
              source: "local",
              enabled: true,
              path: "/path/to/skills/app-order-query",
            },
          ],
        },
      },
    ]);

    render(<AppSkillsPanel />);

    expect(await screen.findByText("订单查询服务")).toBeTruthy();
    expect(screen.getByText("app-order-query")).toBeTruthy();
    expect(screen.getByText("已启用")).toBeTruthy();
    expect(screen.getByText("停用技能")).toBeTruthy();
    expect(screen.getByText("包含 1 个业务操作接口")).toBeTruthy();

    // Toggle expand APIs
    fireEvent.click(screen.getByText("查看接口"));
    expect(screen.getByText("/api/orders")).toBeTruthy();
    expect(screen.getByText("查询订单列表")).toBeTruthy();
  });

  it("handles toggling skill enabled/disabled", async () => {
    const calls = stubFetch([
      { match: "/v1/apps/jobs", method: "GET", json: { jobs: [] } },
      {
        match: "/v1/skills",
        method: "GET",
        json: {
          skills: [
            {
              name: "app-test",
              description: "测试应用技能",
              instructions: "",
              scope: "global",
              source: "local",
              enabled: true,
              path: "",
            },
          ],
        },
      },
      {
        match: "/v1/skills/app-test",
        method: "PATCH",
        json: { ok: true },
      },
    ]);

    render(<AppSkillsPanel />);

    const toggleBtn = await screen.findByText("停用技能");
    fireEvent.click(toggleBtn);

    await waitFor(() => {
      const patch = calls.find((c) => c.method === "PATCH" && c.url.includes("/v1/skills/app-test"));
      expect(patch).toBeTruthy();
      expect(patch?.body).toEqual({ enabled: false });
    });
  });
});
