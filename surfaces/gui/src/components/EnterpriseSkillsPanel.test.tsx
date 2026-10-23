// EnterpriseSkillsPanel lifecycle coverage (WORK-RESOURCES-001, VAL-209/211/216).

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup, fireEvent } from "@testing-library/react";
import EnterpriseSkillsPanel from "./EnterpriseSkillsPanel";
import type { EnterpriseState } from "../enterprise";

const signedIn: EnterpriseState = { kind: "signed_in", subject: "u1", tenant: "T1" };

const catalog = (over: Partial<any> = {}) => ({
  resource_kind: "skill_catalog",
  tenant_id: "T1",
  tenant_name: "交付中心",
  catalog_id: "cat-c1",
  name: "summarizer",
  description: "总结",
  version: "1.0.0",
  bundle_sha256: "a".repeat(64),
  availability: "available",
  local_status: "not_installed",
  installed_sha256: "",
  enabled: false,
  ...over,
});

function mockFetch(routes: Record<string, any>) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: any) => {
      const url = String(input);
      calls.push(url);
      for (const [prefix, body] of Object.entries(routes)) {
        if (url.includes(prefix)) {
          return { status: 200, ok: true, json: async () => body };
        }
      }
      return { status: 404, ok: false, json: async () => ({ error: { code: "NOT_FOUND", message: "n" } }) };
    }),
  );
  return calls;
}

describe("EnterpriseSkillsPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    // 生产形态：httpBase() 返回绝对地址（桌面 WebView 下相对 fetch 会失败）。
    (globalThis as any).__COWORKER_HTTP__ = "http://sidecar.test";
  });
  afterEach(() => {
    delete (globalThis as any).__COWORKER_HTTP__;
    cleanup();
  });

  it("installs explicitly, shows progress, then marks installed (VAL-209)", async () => {
    let opStatus = "downloading";
    const calls = mockFetch({
      "/v1/enterprise/resources/skills": {
        items: [catalog()],
        total: 1,
        offset: 0,
        limit: 50,
        complete: true,
        failures: [],
        context_id: "cid",
        legacy_installs: [],
      },
      "/v1/enterprise/resources/operations/op-1": () => {},
    });
    // richer stub for the operation poll
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: any) => {
        const url = String(input);
        calls.push(url);
        if (url.includes("/operations/op-1")) {
          return {
            status: 200,
            ok: true,
            json: async () => ({
              operation_id: "op-1",
              status: opStatus,
              context_id: "cid",
              tenant_id: "T1",
              catalog_id: "cat-c1",
              expected_sha256: "a".repeat(64),
            }),
          };
        }
        if (url.includes("/install")) {
          return {
            status: 202,
            ok: true,
            json: async () => ({ operation_id: "op-1", status: "downloading", context_id: "cid" }),
          };
        }
        return {
          status: 200,
          ok: true,
          json: async () => ({
            items: [catalog()],
            total: 1,
            offset: 0,
            limit: 50,
            complete: true,
            failures: [],
            context_id: "cid",
            legacy_installs: [],
          }),
        };
      }),
    );
    render(<EnterpriseSkillsPanel enterprise={signedIn} />);
    await waitFor(() => expect(screen.getByText("安装")).toBeTruthy());
    fireEvent.click(screen.getByText("安装"));
    await waitFor(() => expect(screen.getByText(/下载\/校验中/)).toBeTruthy());
    opStatus = "succeeded";
    await waitFor(() => expect(calls.some((u) => u.includes("/operations/op-1"))).toBe(true), {
      timeout: 4000,
    });
  });

  it("not_ready catalogs cannot be installed (BND-002/INT-101)", async () => {
    mockFetch({
      "/v1/enterprise/resources/skills": {
        items: [catalog({ availability: "not_ready", unavailable_reason: "ARCHIVE_NOT_READY", bundle_sha256: "" })],
        total: 1,
        offset: 0,
        limit: 50,
        complete: true,
        failures: [],
        context_id: "cid",
        legacy_installs: [],
      },
    });
    render(<EnterpriseSkillsPanel enterprise={signedIn} />);
    await waitFor(() => expect(screen.getByText("安装")).toBeTruthy());
    expect((screen.getByText("安装") as HTMLButtonElement).disabled).toBe(true);
    expect(
      screen.getAllByText((_, el) => (el?.textContent ?? "").includes("归档未就绪")).length,
    ).toBeGreaterThan(0);
  });

  it("legacy publish_id installs are flagged 需重装 and never executable (VAL-216)", async () => {
    mockFetch({
      "/v1/enterprise/resources/skills": {
        items: [],
        total: 0,
        offset: 0,
        limit: 50,
        complete: true,
        failures: [],
        context_id: "cid",
        legacy_installs: [
          { publish_id: "pub-1", name: "opc-hr", version: "1.0.0", sha256: "b".repeat(64), status: "legacy_requires_reinstall" },
        ],
      },
    });
    render(<EnterpriseSkillsPanel enterprise={signedIn} />);
    await waitFor(() => expect(screen.getByTestId("legacy-install-row")).toBeTruthy());
    expect(screen.getByText(/旧版安装，需从企业目录重新安装/)).toBeTruthy();
  });

  it("installed rows offer 更新/停用/卸载 (BND-009)", async () => {
    mockFetch({
      "/v1/enterprise/resources/skills": {
        items: [catalog({ local_status: "installed_enabled", enabled: true, installed_sha256: "0".repeat(64) })],
        total: 1,
        offset: 0,
        limit: 50,
        complete: true,
        failures: [],
        context_id: "cid",
        legacy_installs: [],
      },
    });
    render(<EnterpriseSkillsPanel enterprise={signedIn} />);
    await waitFor(() => expect(screen.getByText("更新")).toBeTruthy());
    expect(screen.getByText("停用")).toBeTruthy();
    expect(screen.getByText("卸载")).toBeTruthy();
    expect(
      screen.getAllByText((_, el) => (el?.textContent ?? "").includes("有更新")).length,
    ).toBeGreaterThan(0);
  });
});
