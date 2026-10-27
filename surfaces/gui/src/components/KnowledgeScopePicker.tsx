// Knowledge scope picker (WORK-RESOURCES-001, BND-004.1) — the "@ / 添加知识"
// selector: single-select, lists ONLY currently accessible KBs. Keyboard is
// handled at the COMPOSER layer (M2): the picker exposes its rows + highlight
// so ArrowUp/Down / Enter / Esc work while the textarea has focus — Enter
// picks a row instead of sending the message.

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  enterpriseResources,
  type KnowledgeScopeSelection,
  type ResourceRequestError,
} from "../enterpriseResources";

export interface PickerRow {
  id: string;
  label: string;
  selection: KnowledgeScopeSelection;
}

export function KnowledgeScopePicker({
  highlight,
  onHighlightChange,
  onRows,
  onPick,
  tenantId,
}: {
  highlight: number;
  onHighlightChange: (n: number) => void;
  /** Publishes the loaded rows to the Composer for keyboard navigation. */
  onRows?: (rows: PickerRow[]) => void;
  onPick: (selection: KnowledgeScopeSelection) => void;
  tenantId?: string;
}) {
  // Esc 关闭由 Composer 层键盘拦截处理（M2）；picker 不再自持 onClose。
  const { t } = useTranslation();
  const [items, setItems] = useState<
    | { kind: "loading" }
    | { kind: "ready"; rows: PickerRow[] }
    | { kind: "error"; code: string }
  >({ kind: "loading" });

  useEffect(() => {
    let dead = false;
    setItems({ kind: "loading" });
    (async () => {
      try {
        const env = await enterpriseResources.listKnowledgeBases({ limit: 100, tenantId: tenantId || undefined });
        if (dead) return;
        const rows = env.items
          .filter((i) => i.availability === "available")
          .map((i) => ({
            id: `${i.tenant_id}:${i.knowledge_base_id}`,
            label: `${i.name}（${i.tenant_name || i.tenant_id}）`,
            selection: {
              tenant_id: i.tenant_id,
              knowledge_base_id: i.knowledge_base_id || "",
              context_id: env.context_id,
              label: `${i.name}（${i.tenant_name || i.tenant_id}）`,
            },
          }));
        if (dead) return;
        setItems({ kind: "ready", rows });
        onRows?.(rows);
      } catch (err) {
        if (dead) return;
        setItems({ kind: "error", code: (err as ResourceRequestError).code || "ERROR" });
      }
    })();
    return () => {
      dead = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId]);

  // Esc closes without changing the selection (BND-004.1) — handled at the
  // Composer layer too; this button-level Escape keeps mouse-only flows sane.
  return (
    <div
      className="absolute bottom-full mb-2 left-0 w-72 rounded-xl2 border border-line bg-panel shadow-lg z-30"
      role="listbox"
      aria-label={t("composer.add_knowledge", "添加知识")}
      data-testid="knowledge-scope-picker"
    >
      <div className="px-3 py-2 text-label text-muted border-b border-line">
        {t("composer.pick_knowledge", "选择一个知识库（单选）")}
      </div>
      {items.kind === "loading" && (
        <div className="px-3 py-2 text-ui text-muted">{t("common.loading", "加载中…")}</div>
      )}
      {items.kind === "error" && (
        <div className="px-3 py-2 text-ui text-danger" role="alert">
          {t("resources.load_failed", "加载失败（{{code}}）", { code: items.code })}
        </div>
      )}
      {items.kind === "ready" &&
        (items.rows.length === 0 ? (
          <div className="px-3 py-2 text-ui text-muted">
            {t("resources.empty", "暂无可访问资源")}
          </div>
        ) : (
          <ul>
            {items.rows.map((row, idx) => (
              <li key={row.id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={idx === highlight}
                  className={`w-full text-left px-3 py-2 text-ui ${
                    idx === highlight ? "bg-accent/10 text-ink" : "text-ink"
                  }`}
                  onMouseEnter={() => onHighlightChange(idx)}
                  onClick={() => onPick(row.selection)}
                >
                  {row.label}
                </button>
              </li>
            ))}
          </ul>
        ))}
    </div>
  );
}

export default KnowledgeScopePicker;
