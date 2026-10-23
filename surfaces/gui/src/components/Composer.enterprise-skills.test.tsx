// WORK-RESOURCES-001 增量：企业技能并入 Composer「/」菜单（BND-009）。
// 企业行渲染「企业」徽标（同名可辨）；选中企业项产生 skill_ref 标签而非文本注入；
// 未安装目录不出现；同名个人技能与企业技能并存。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Composer } from "./Composer";

const MENU = {
  skills: [
    { name: "greet", description: "says hello", scope: "project", enabled: true },
    // 同名个人 + 企业并存
    { name: "summarizer", description: "个人总结", scope: "global", enabled: true },
    {
      name: "summarizer",
      description: "企业·T1",
      scope: "enterprise",
      enabled: true,
      enterprise: {
        tenant_id: "T1",
        catalog_id: "cat-c1",
        bundle_sha256: "a".repeat(64),
        version: "1.0.0",
        context_id: "cid-1",
      },
    },
    { name: "frontend-design", description: "企业·T2", scope: "enterprise", enabled: true,
      enterprise: {
        tenant_id: "T2", catalog_id: "cat-c2", bundle_sha256: "b".repeat(64), context_id: "cid-1",
      } },
    { name: "disabled-ent", description: "企业·T1", scope: "enterprise", enabled: false,
      enterprise: {
        tenant_id: "T1", catalog_id: "cat-c3", bundle_sha256: "c".repeat(64), context_id: "cid-1",
      } },
  ],
};

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/skills")) return { ok: true, json: async () => MENU } as Response;
      return { ok: true, json: async () => ({}) } as Response;
    }),
  );
}

const props = (extra: Partial<Parameters<typeof Composer>[0]> = {}) => ({
  mode: "interactive",
  model: "gpt-5.6-sol",
  running: false,
  connected: true,
  sessionId: "s1",
  onSend: vi.fn(),
  onInterrupt: vi.fn(),
  onModeChange: vi.fn(),
  onModelChange: vi.fn(),
  ...extra,
});

const box = () => screen.getByPlaceholderText(/Ask the coworker/);

beforeEach(() => {
  // 生产形态：httpBase() 绝对地址（桌面 WebView 下相对 fetch 会失败）。
  (globalThis as any).__COWORKER_HTTP__ = "http://sidecar.test";
});
afterEach(() => {
  delete (globalThis as any).__COWORKER_HTTP__;
  cleanup();
  vi.unstubAllGlobals();
});

describe("Composer / enterprise skills", () => {
  it("enterprise rows render the 企业 badge and coexist with a same-named personal skill", async () => {
    stubFetch();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "/sum" } });
    await waitFor(() => expect(screen.getAllByText(/summarizer/).length).toBeGreaterThan(0));
    // 同名两行：个人（scope 徽标）+ 企业（企业徽标——测试环境 en 资源为 "Enterprise"）
    const badges = screen.getAllByText(/^(企业|Enterprise)$/);
    expect(badges.length).toBeGreaterThanOrEqual(1);
    // 名称过滤对企业名同样生效
    expect(screen.getAllByText(/summarizer/).length).toBeGreaterThanOrEqual(2);
  });

  it("picking an enterprise row sets skill_ref (no text injection)", async () => {
    stubFetch();
    const onSkillRefChange = vi.fn();
    render(<Composer {...props({ onSkillRefChange })} />);
    fireEvent.change(box(), { target: { value: "/front" } });
    await waitFor(() => expect(screen.getByText("/frontend-design")).toBeTruthy());
    fireEvent.click(screen.getByText("/frontend-design"));
    expect(onSkillRefChange).toHaveBeenCalledWith({
      source: "catalog",
      tenant_id: "T2",
      catalog_id: "cat-c2",
      bundle_sha256: "b".repeat(64),
      context_id: "cid-1",
      label: "frontend-design",
    });
    // 不注入文本；弹窗关闭
    expect((box() as HTMLTextAreaElement).value).toBe("");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("picking a personal row keeps the legacy /name text injection", async () => {
    stubFetch();
    const onSkillRefChange = vi.fn();
    render(<Composer {...props({ onSkillRefChange })} />);
    fireEvent.change(box(), { target: { value: "/gree" } });
    await waitFor(() => expect(screen.getByText("/greet")).toBeTruthy());
    fireEvent.click(screen.getByText("/greet"));
    expect((box() as HTMLTextAreaElement).value).toBe("/greet ");
    expect(onSkillRefChange).not.toHaveBeenCalled();
  });

  it("disabled enterprise rows are filtered out of the executable menu", async () => {
    stubFetch();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "/dis" } });
    await waitFor(() => expect(screen.getByText(/No matching skills|没有匹配的技能/)).toBeTruthy());
    expect(screen.queryByText("/disabled-ent")).toBeNull();
  });
});
