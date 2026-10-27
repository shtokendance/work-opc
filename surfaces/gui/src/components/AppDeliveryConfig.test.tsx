import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AppDeliveryConfig } from "./AppDeliveryConfig";
import type { AppConfigItem } from "../api";

afterEach(cleanup);
const originals = [{ key: "DB_DSN", value: "reader:fixture@tcp(original:3306)/business", required: true, secret: true, value_source: "original" }, { key: "ALY_ACCESS_KEY_SECRET", value: "fixture-original", value_source: "original" }];
const resources = [{ id: "a", name: "服务器数据库 A", type: "mysql", description: "", env_keys: ["DB_DSN", "ALY_ACCESS_KEY_SECRET"], env_preview: { DB_DSN: "[REDACTED]" } }, { id: "b", name: "服务器数据库 B", type: "mysql", description: "", env_keys: ["DB_DSN"] }, { id: "pg", name: "不兼容的数据库", type: "postgres", description: "", env_keys: ["DB_DSN"] }];

it("shows readable originals beside compatible choices and changes only the confirmed field", () => {
  const saved = vi.fn();
  function Editor() {
    const [items, setItems] = useState<AppConfigItem[]>(originals);
    return <AppDeliveryConfig configs={items} originals={originals} resources={resources} onChange={next => { setItems(next); saved(next); }} />;
  }
  render(<Editor />);
  expect((screen.getByLabelText("DB_DSN 原始值") as HTMLTextAreaElement).value).toContain("fixture");
  expect(screen.queryByText("不兼容的数据库")).toBeNull();
  expect(saved).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "使用服务器数据库 A替换 DB_DSN" }));
  let items = saved.mock.lastCall![0] as AppConfigItem[];
  expect(items[0]).toMatchObject({ value: "", resource_id: "a" });
  expect(items[1]).toEqual(originals[1]);
  expect((screen.getByLabelText("DB_DSN 原始值") as HTMLTextAreaElement).value).toBe(originals[0].value);
  fireEvent.click(screen.getByRole("button", { name: "使用服务器数据库 B替换 DB_DSN" }));
  expect(saved.mock.lastCall![0][0].resource_id).toBe("b");
  fireEvent.click(screen.getByRole("button", { name: "恢复原值" }));
  expect(saved.mock.lastCall![0][0].value).toBe(originals[0].value);
  expect(saved.mock.lastCall![0][0].resource_id).toBeUndefined();
  fireEvent.change(screen.getByLabelText("DB_DSN 配置值"), { target: { value: "my-own-value" } });
  items = saved.mock.lastCall![0];
  expect(items[0]).toMatchObject({ value: "my-own-value", value_source: "manual" });
  expect(items[1]).toEqual(originals[1]);
});
