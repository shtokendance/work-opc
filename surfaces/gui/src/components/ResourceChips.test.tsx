// ResourceChips: history messages with/without `_resources` (DATA-102, VAL-219).

import { describe, expect, it } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { ResourceChips, labelsFromMessage } from "./ResourceChips";
import { afterEach } from "vitest";

describe("labelsFromMessage", () => {
  it("extracts labels from the _resources sidecar", () => {
    const msg = {
      role: "user",
      content: "hi",
      _resources: {
        context_id: "cid",
        turn_id: "t1",
        labels: {
          knowledge_scope: { tenant_id: "T1", knowledge_base_id: "kb-a", knowledge_base_name: "A" },
        },
      },
    };
    expect(labelsFromMessage(msg)?.knowledge_scope?.knowledge_base_name).toBe("A");
  });

  it("returns null for history without the field (no guessed backfill)", () => {
    expect(labelsFromMessage({ role: "user", content: "hi" })).toBeNull();
    expect(labelsFromMessage(null)).toBeNull();
    expect(labelsFromMessage("x")).toBeNull();
  });
});

describe("ResourceChips", () => {
  afterEach(cleanup);

  it("renders knowledge + skill labels from the server snapshot", () => {
    render(
      <ResourceChips
        labels={{
          knowledge_scope: {
            tenant_id: "T1",
            knowledge_base_id: "kb-a",
            tenant_name: "交付中心",
            knowledge_base_name: "A",
          },
          skill_ref: {
            source: "catalog",
            tenant_id: "T1",
            catalog_id: "cat-c1",
            bundle_sha256: "a".repeat(64),
            name: "summarizer",
          },
        }}
      />,
    );
    expect(screen.getByTestId("resource-chips")).toBeTruthy();
    expect(screen.getByText(/A（交付中心）/)).toBeTruthy();
    expect(screen.getByText(/summarizer/)).toBeTruthy();
  });

  it("renders nothing when both selections are absent", () => {
    const { container } = render(<ResourceChips labels={{}} />);
    expect(container.firstChild).toBeNull();
  });
});
