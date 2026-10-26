import { describe, expect, it } from "vitest";
import type { Item } from "./types";
import { updateLastTool } from "./updateToolResult";

describe("live tool result correlation", () => {
  const calls = (): Item[] => [
    { kind: "tool", id: "row-get", toolCallId: "get", name: "run_shell", args: { command: "GET" }, status: "…" },
    { kind: "tool", id: "row-post", toolCallId: "post", name: "run_shell", args: { command: "POST" }, status: "…" },
  ];
  const finish = (items: Item[], id: string, result: string) => updateLastTool(items, "run_shell", "ok", result, undefined, undefined, undefined, undefined, "user", undefined, id);
  it("keeps same-named results attached to their own calls regardless of completion order", () => {
    for (const order of [["get", "post"], ["post", "get"]]) {
      let items = calls();
      for (const id of order) items = finish(items, id, `${id} result`);
      expect(items.map(it => it.kind === "tool" && [it.toolCallId, it.preview, it.approvalOrigin])).toEqual([
        ["get", "get result", "user"], ["post", "post result", "user"],
      ]);
    }
  });
  it("does not attach an unknown or duplicate completion to another tracked call", () => {
    expect(finish(calls(), "unknown", "wrong")).toEqual(calls());
    const once = finish(calls(), "get", "get result");
    expect(finish(once, "get", "duplicate")).toEqual(once);
  });
  it("supports an older server that has no proposal call ID", () => {
    const legacy: Item[] = [{ kind: "tool", id: "legacy", name: "run_shell", args: {}, status: "…" }];
    expect(finish(legacy, "old-server", "result")[0]).toMatchObject({ status: "ok", preview: "result" });
  });
});
