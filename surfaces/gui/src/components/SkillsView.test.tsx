import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { SkillsView } from "./SkillsView";

vi.mock("./EnterpriseSkillsPanel", () => ({
  EnterpriseSkillsPanel: () => <div data-testid="mock-enterprise-panel">企业技能面板</div>,
}));

vi.mock("./AppSkillsPanel", () => ({
  AppSkillsPanel: () => <div data-testid="mock-app-skills-panel">应用技能面板</div>,
}));

vi.mock("./SkillsTab", () => ({
  SkillsTab: () => <div data-testid="mock-skills-tab">我的技能面板</div>,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SkillsView", () => {
  it("renders with enterprise skills tab active by default", () => {
    render(<SkillsView />);
    expect(screen.getByTestId("skills-tab-enterprise")).toBeTruthy();
    expect(screen.getByTestId("skills-tab-app")).toBeTruthy();
    expect(screen.getByTestId("skills-tab-personal")).toBeTruthy();
    expect(screen.getByTestId("mock-enterprise-panel")).toBeTruthy();
  });

  it("switches to app skills tab when clicked", () => {
    render(<SkillsView />);
    fireEvent.click(screen.getByTestId("skills-tab-app"));
    expect(screen.getByTestId("mock-app-skills-panel")).toBeTruthy();
    expect(screen.queryByTestId("mock-enterprise-panel")).toBeNull();
  });

  it("switches to my skills tab when clicked", () => {
    render(<SkillsView />);
    fireEvent.click(screen.getByTestId("skills-tab-personal"));
    expect(screen.getByTestId("mock-skills-tab")).toBeTruthy();
    expect(screen.queryByTestId("mock-enterprise-panel")).toBeNull();
  });
});
