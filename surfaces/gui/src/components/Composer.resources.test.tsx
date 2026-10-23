// Composer resource selections (WORK-RESOURCES-001, BND-004): the 添加知识 chip,
// removal, bare-scope send disable, and the 4th onSend arg only with selections.

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { Composer } from "./Composer";

const props = (extra: Partial<Parameters<typeof Composer>[0]> = {}): any => ({
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

const baseProps = props();

const scope = {
  tenant_id: "T1",
  knowledge_base_id: "kb-a",
  context_id: "cid",
  label: "A（T1）",
};

function type(textarea: HTMLElement, value: string) {
  fireEvent.change(textarea, { target: { value } });
}

describe("Composer resource selections", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (globalThis as any).__COWORKER_HTTP__ = "http://sidecar.test";
  });
  afterEach(() => {
  delete (globalThis as any).__COWORKER_HTTP__;
  cleanup();
});

  it("a scope-only message cannot be sent (BND-004.3)", () => {
    const onSend = vi.fn();
    render(
      <Composer
        {...props({ knowledgeScope: scope, onKnowledgeScopeChange: vi.fn(), onSend })}
      />,
    );
    const textarea = screen.getByRole("textbox");
    type(textarea, "   ");
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled(); // 门逻辑真实生效
  });

  it("an enterprise-skill-only message cannot be sent either (M1)", () => {
    const onSend = vi.fn();
    render(
      <Composer
        {...props({
          skillRef: {
            source: "catalog",
            tenant_id: "T1",
            catalog_id: "cat-1",
            bundle_sha256: "a".repeat(64),
            context_id: "cid",
            label: "s",
          },
          onSkillRefChange: vi.fn(),
          onSend,
        })}
      />,
    );
    const textarea = screen.getByRole("textbox");
    type(textarea, "   ");
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("M2: picker open — ArrowDown moves highlight, Enter picks, Enter never sends", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        status: 200,
        ok: true,
        json: async () => ({
          items: [
            { resource_kind: "knowledge_base", tenant_id: "T1", tenant_name: "T1", knowledge_base_id: "kb-a", name: "A", availability: "available" },
            { resource_kind: "knowledge_base", tenant_id: "T1", tenant_name: "T1", knowledge_base_id: "kb-b", name: "B", availability: "available" },
          ],
          total: 2, offset: 0, limit: 50, complete: true, failures: [], context_id: "cid",
        }),
      })),
    );
    const onScope = vi.fn();
    const onSend = vi.fn();
    render(<Composer {...props({ onKnowledgeScopeChange: onScope, onSend })} />);
    const textarea = screen.getByRole("textbox");
    type(textarea, "先写点正文");
    fireEvent.click(screen.getByTestId("composer-add-knowledge"));
    const picker = await screen.findByTestId("knowledge-scope-picker");
    expect(picker).toBeTruthy();
    // 行加载后 ArrowDown 移到第二行，Enter 选中且不发送消息
    await screen.findByText(/B（T1）/);
    fireEvent.keyDown(textarea, { key: "ArrowDown" });
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onScope).toHaveBeenCalledWith(
      expect.objectContaining({ knowledge_base_id: "kb-b" }),
    );
    expect(onSend).not.toHaveBeenCalled(); // Enter 选中，不是发送
    vi.unstubAllGlobals();
  });

  it("M2: Escape closes the picker without picking (BND-004.1)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        status: 200,
        ok: true,
        json: async () => ({
          items: [
            { resource_kind: "knowledge_base", tenant_id: "T1", tenant_name: "T1", knowledge_base_id: "kb-a", name: "A", availability: "available" },
          ],
          total: 1, offset: 0, limit: 50, complete: true, failures: [], context_id: "cid",
        }),
      })),
    );
    const onScope = vi.fn();
    render(<Composer {...props({ onKnowledgeScopeChange: onScope })} />);
    const textarea = screen.getByRole("textbox");
    fireEvent.click(screen.getByTestId("composer-add-knowledge"));
    await screen.findByTestId("knowledge-scope-picker");
    fireEvent.keyDown(textarea, { key: "Escape" });
    expect(screen.queryByTestId("knowledge-scope-picker")).toBeNull();
    expect(onScope).not.toHaveBeenCalled(); // 不改选择
    vi.unstubAllGlobals();
  });

  it("sending with text + scope passes the 4th arg, then clears the selection", () => {
    const onScope = vi.fn();
    const onSend = vi.fn();
    render(
      <Composer
        {...props({ knowledgeScope: scope, onKnowledgeScopeChange: onScope, onSend })}
      />,
    );
    type(screen.getByRole("textbox"), "整理卖点");
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith(
      "整理卖点",
      [],
      undefined,
      expect.objectContaining({ knowledgeScope: scope }),
    );
    expect(onScope).toHaveBeenCalledWith(null); // 接受后清选择
  });

  it("plain sends keep the legacy 3-arg call", () => {
    render(<Composer {...baseProps} />);
    type(screen.getByRole("textbox"), "hi");
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    expect(baseProps.onSend).toHaveBeenCalledWith("hi", [], undefined);
  });

  it("the chip renders the label and the remove button clears it", () => {
    const onScope = vi.fn();
    render(<Composer {...props({ knowledgeScope: scope, onKnowledgeScopeChange: onScope })} />);
    expect(screen.getByTestId("composer-resource-chips")).toBeTruthy();
    fireEvent.click(screen.getByTestId("remove-knowledge-scope"));
    expect(onScope).toHaveBeenCalledWith(null);
  });

  it("添加知识 opens the picker (which lists accessible KBs)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        status: 200,
        ok: true,
        json: async () => ({
          items: [
            {
              resource_kind: "knowledge_base",
              tenant_id: "T1",
              tenant_name: "T1",
              knowledge_base_id: "kb-a",
              name: "A",
              availability: "available",
            },
          ],
          total: 1,
          offset: 0,
          limit: 50,
          complete: true,
          failures: [],
          context_id: "cid",
        }),
      })),
    );
    const onScope = vi.fn();
    render(<Composer {...props({ onKnowledgeScopeChange: onScope })} />);
    fireEvent.click(screen.getByTestId("composer-add-knowledge"));
    expect(await screen.findByTestId("knowledge-scope-picker")).toBeTruthy();
    vi.unstubAllGlobals();
  });
});
