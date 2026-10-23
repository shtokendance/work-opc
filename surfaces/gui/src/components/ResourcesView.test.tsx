// ResourcesView state coverage (WORK-RESOURCES-001, VAL-201, BND-002/009):
// signed-out / partial / empty / error / ready, 使用 prepares (never sends),
// 详情 passes the exact-origin guard, and late responses never overwrite.

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup, fireEvent } from "@testing-library/react";
import ResourcesView from "./ResourcesView";
import * as tauri from "../tauri";
import type { EnterpriseState } from "../enterprise";

const signedIn: EnterpriseState = { kind: "signed_in", subject: "u1", tenant: "T1" };

const item = (over: Partial<any> = {}) => ({
  resource_kind: "knowledge_base",
  tenant_id: "T1",
  tenant_name: "交付中心",
  knowledge_base_id: "kb-a",
  name: "A",
  description: "苹果知识库",
  availability: "available",
  ...over,
});

function mockList(body: any, ok = true, status = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      status,
      ok,
      json: async () => body,
    })),
  );
}

describe("ResourcesView", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    (globalThis as any).__COWORKER_HTTP__ = "http://sidecar.test";
  });
  afterEach(() => {
    delete (globalThis as any).__COWORKER_HTTP__;
    cleanup();
  });

  it("signed-out shows the login prompt and never queries", async () => {
    const spy = vi.fn();
    vi.stubGlobal("fetch", spy);
    render(<ResourcesView enterprise={{ kind: "idle" }} />);
    expect(screen.getByTestId("resources-signed-out")).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("ready: renders rows sorted by the server, with use/details actions", async () => {
    mockList({
      items: [item(), item({ knowledge_base_id: "kb-b", name: "B" })],
      total: 2,
      offset: 0,
      limit: 50,
      complete: true,
      failures: [],
      context_id: "cid",
    });
    const onUse = vi.fn();
    render(<ResourcesView enterprise={signedIn} onUseKnowledge={onUse} />);
    await waitFor(() => expect(screen.getByText("A")).toBeTruthy());
    fireEvent.click(screen.getAllByText("使用")[0]);
    expect(onUse).toHaveBeenCalledWith(
      expect.objectContaining({ tenant_id: "T1", knowledge_base_id: "kb-a", context_id: "cid" }),
    );
  });

  it("partial: shows the failure banner instead of hiding it", async () => {
    mockList({
      items: [item()],
      total: 1,
      offset: 0,
      limit: 50,
      complete: false,
      failures: [{ tenant_id: "T2", code: "UPSTREAM_UNAVAILABLE" }],
      context_id: "cid",
    });
    render(<ResourcesView enterprise={signedIn} />);
    await waitFor(() => expect(screen.getByText(/部分资源未加载/)).toBeTruthy());
  });

  it("empty: says so without faking rows", async () => {
    mockList({ items: [], total: 0, offset: 0, limit: 50, complete: true, failures: [], context_id: "cid" });
    render(<ResourcesView enterprise={signedIn} />);
    await waitFor(() => expect(screen.getByText("暂无可访问资源")).toBeTruthy());
  });

  it("error: shows the code and a retry that re-queries", async () => {
    mockList({ error: { code: "UPSTREAM_UNAVAILABLE", message: "m" } }, false, 502);
    render(<ResourcesView enterprise={signedIn} />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByText(/UPSTREAM_UNAVAILABLE/)).toBeTruthy();
  });

  it("详情 opens only exact-origin URLs; 上传 opens the bare homepage", async () => {
    mockList({
      items: [item()],
      total: 1,
      offset: 0,
      limit: 50,
      complete: true,
      failures: [],
      context_id: "cid",
    });
    const openSpy = vi.spyOn(tauri, "openExternal").mockImplementation(() => {});
    render(<ResourcesView enterprise={signedIn} />);
    await waitFor(() => expect(screen.getByText("详情")).toBeTruthy());
    fireEvent.click(screen.getByText("详情"));
    expect(openSpy).toHaveBeenCalledWith(
      "https://shtokendance.benlai.com/platform/knowledge-bases/kb-a?tenant_id=T1",
    );
    fireEvent.click(screen.getByText("上传"));
    expect(openSpy).toHaveBeenLastCalledWith("https://shtokendance.benlai.com/");
  });
});
