import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppDeliveryReview } from "./AppDeliveryReview";
import { appDeliveryRequest, getAppDeliveryJob } from "../api";

vi.mock("../api", () => ({ getAppDeliveryJob: vi.fn(), appDeliveryRequest: vi.fn() }));
const plan = {
  id: "job", slug: "stock", name: "Stock", status: "review", model: "configured-model", app_url: "http://stock.apps.localhost", events: ["read_source"], proposal_hash: "fixed-plan",
  proposal: { summary: "Preserve business behavior", unresolved: [], issues: [{ id: "P1", impact: "Localhost connection cannot reach another container", recommendation: "Choose an approved resource", evidence: "settings.py:3" }], changes: [{ id: "C1", path: "settings.py", reason: "Use confirmed Redis environment input", diff: "-127.0.0.1\n+REDIS_HOST" }], configs: [{ key: "REDIS_HOST", secret: false, required: true, description: "Redis connection" }], apis: [], pages: [] },
};
afterEach(cleanup);
beforeEach(() => { vi.clearAllMocks(); vi.mocked(getAppDeliveryJob).mockResolvedValue(plan); });

describe("App delivery approval", () => {
  it("requires individual file approval and explicit infrastructure confirmation", async () => {
    const onReady = vi.fn();
    vi.mocked(appDeliveryRequest).mockResolvedValue({ ...plan, status: "ready", artifact_hash: "artifact" });
    render(<AppDeliveryReview jobId="job" onReady={onReady} />);
    const button = await screen.findByRole("button", { name: "批准所选修改并生成交付包" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    const boxes = screen.getAllByRole("checkbox");
    fireEvent.click(boxes[0]);
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(boxes[1]);
    expect((button as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(button);
    await waitFor(() => expect(appDeliveryRequest).toHaveBeenCalledWith("jobs/job/approve", { proposal_hash: "fixed-plan", approved_changes: ["C1"], confirm_config_changes: true }));
    await waitFor(() => expect(onReady).toHaveBeenCalled());
  });

  it("does not allow approval while questions remain unresolved", async () => {
    vi.mocked(getAppDeliveryJob).mockResolvedValue({ ...plan, proposal: { ...plan.proposal, unresolved: ["Which MongoDB resource should be used?"] } });
    render(<AppDeliveryReview jobId="job" onReady={vi.fn()} />);
    await screen.findByText("Which MongoDB resource should be used?");
    screen.getAllByRole("checkbox").forEach(box => fireEvent.click(box));
    expect((screen.getByRole("button", { name: "批准所选修改并生成交付包" }) as HTMLButtonElement).disabled).toBe(true);
    expect(appDeliveryRequest).not.toHaveBeenCalled();
  });
});
