import { describe, expect, it } from "vitest";
import { toAppSlug } from "./appSlug";

describe("toAppSlug", () => {
  it("converts pure Chinese words to pinyin slug", () => {
    expect(toAppSlug("报销")).toBe("bao-xiao");
    expect(toAppSlug("自动报销系统")).toBe("zi-dong-bao-xiao-xi-tong");
    expect(toAppSlug("供应商")).toBe("gong-ying-shang");
  });

  it("handles mixed Chinese and English", () => {
    expect(toAppSlug("devops 阿里云")).toBe("devops-a-li-yun");
    expect(toAppSlug("smart 报销")).toBe("smart-bao-xiao");
  });

  it("handles standard English and numbers", () => {
    expect(toAppSlug("devops-aly")).toBe("devops-aly");
    expect(toAppSlug("My Cool App 2026")).toBe("my-cool-app-2026");
  });

  it("prefixes app- when input starts with a number", () => {
    expect(toAppSlug("123-test")).toBe("app-123-test");
    expect(toAppSlug("996")).toBe("app-996");
  });

  it("collapses multiple spaces, underscores, and dashes", () => {
    expect(toAppSlug("  hello___world---test  ")).toBe("hello-world-test");
  });

  it("handles allowTrailingDash option during typing", () => {
    expect(toAppSlug("devops-", { allowTrailingDash: true })).toBe("devops-");
    expect(toAppSlug("devops-", { allowTrailingDash: false })).toBe("devops");
  });

  it("caps maximum length at 40 characters", () => {
    const longInput = "a".repeat(50);
    expect(toAppSlug(longInput).length).toBe(40);
  });

  it("returns empty string on empty input", () => {
    expect(toAppSlug("")).toBe("");
  });
});
