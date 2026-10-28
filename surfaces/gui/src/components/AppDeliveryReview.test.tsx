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
  it("copies the local AI handoff and offers returning to source selection without approval", async () => {
    const copy = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: copy } });
    vi.mocked(appDeliveryRequest).mockResolvedValue({ prompt: "请按原业务改造，保留原配置。" });
    const replace = vi.fn();
    render(<AppDeliveryReview jobId="job" onReady={vi.fn()} onReplaceSource={replace} />);
    fireEvent.click(await screen.findByRole("button", { name: "复制交接提示词" }));
    await waitFor(() => expect(copy).toHaveBeenCalledWith("请按原业务改造，保留原配置。"));
    expect(appDeliveryRequest).toHaveBeenCalledWith("jobs/job/handoff");
    fireEvent.click(screen.getByRole("button", { name: "已经改好了，重新选择源码" }));
    expect(replace).toHaveBeenCalledOnce();
    expect(appDeliveryRequest).not.toHaveBeenCalledWith("jobs/job/approve", expect.anything());
  });
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
    await screen.findByText(/Which MongoDB resource should be used/);
    screen.getAllByRole("checkbox").forEach(box => fireEvent.click(box));
    expect((screen.getByRole("button", { name: "批准所选修改并生成交付包" }) as HTMLButtonElement).disabled).toBe(true);
    expect(appDeliveryRequest).not.toHaveBeenCalled();
  });

  it("explains the blocker and submits answers tied to each question for a new analysis", async () => {
    vi.mocked(getAppDeliveryJob).mockResolvedValue({ ...plan, proposal: { ...plan.proposal, changes: [], unresolved: ["保留原 Redis 吗？", "监听端口使用哪个？"] } });
    render(<AppDeliveryReview jobId="job" onReady={vi.fn()} />);
    await screen.findByText("分析完成，需要你补充信息");
    expect(screen.getByText(/还有 2 个问题需要回答并重新分析/)).toBeTruthy();
    expect(screen.getByText(/模型尚未给出可执行的文件修改/)).toBeTruthy();
    const retry = screen.getByRole("button", { name: "根据回答重新分析" }) as HTMLButtonElement;
    expect(retry.disabled).toBe(true);
    fireEvent.change(screen.getByRole("textbox", { name: "问题 1 的回答" }), { target: { value: "保留原地址和密码" } });
    fireEvent.change(screen.getByRole("textbox", { name: "问题 2 的回答" }), { target: { value: "8080" } });
    fireEvent.click(retry);
    await waitFor(() => expect(appDeliveryRequest).toHaveBeenCalledWith("jobs/job/analyze", { feedback: "问题：保留原 Redis 吗？\n用户回答：保留原地址和密码\n\n问题：监听端口使用哪个？\n用户回答：8080" }));
  });

  it("describes an original masked value as preserved instead of requiring re-entry", async () => {
    vi.mocked(getAppDeliveryJob).mockResolvedValue({ ...plan, proposal: { ...plan.proposal, configs: [{ key: "DB_PASSWORD", secret: true, required: true, has_original_value: true, source: { path: "config.yaml", keys: ["db", "password"] } }] } });
    render(<AppDeliveryReview jobId="job" onReady={vi.fn()} />);
    expect(await screen.findByText("保留原配置值")).toBeTruthy();
    expect(screen.queryByText("秘密值：运行时提供")).toBeNull();
  });

  it("shows server foundation service migration guidance and allows bypassing non-blocking questions when changes exist", async () => {
    const onReady = vi.fn();
    vi.mocked(getAppDeliveryJob).mockResolvedValue({
      ...plan,
      resources: [
        { id: "pg-main", name: "平台共享 PostgreSQL", type: "postgres", description: "内网共享数据库", env_keys: ["DATABASE_URL"] },
        { id: "redis-main", name: "平台共享 Redis", type: "redis", description: "内网共享缓存", env_keys: ["REDIS_HOST"] },
      ],
      proposal: {
        ...plan.proposal,
        unresolved: ["是否仅用于受控试点？"],
      },
    });
    vi.mocked(appDeliveryRequest).mockResolvedValue({ ...plan, status: "ready", artifact_hash: "artifact" });
    render(<AppDeliveryReview jobId="job" onReady={onReady} />);
    expect(await screen.findByText(/平台共享 PostgreSQL/)).toBeTruthy();
    expect(screen.getByText(/本机原有数据不会自动搬迁/)).toBeTruthy();
    expect(screen.getByText(/缓存数据属于临时状态，无需迁移/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "一键全选修改并确认方案" }));
    const approveBtn = screen.getByRole("button", { name: "批准所选修改并生成交付包" }) as HTMLButtonElement;
    expect(approveBtn.disabled).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "这些问题不影响，直接按默认方案继续" }));
    expect(approveBtn.disabled).toBe(false);
    fireEvent.click(approveBtn);
    await waitFor(() => expect(appDeliveryRequest).toHaveBeenCalledWith("jobs/job/approve", {
      proposal_hash: "fixed-plan",
      approved_changes: ["C1"],
      confirm_config_changes: true,
      ignore_unresolved: true,
    }));
  });
});
