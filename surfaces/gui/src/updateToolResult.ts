import type { Item } from "./types";

// Correlate concurrent calls by ID; legacy servers without IDs retain name matching.
export function updateLastTool(
  items: Item[], name: string, status: string, preview?: string, hidden?: number,
  standingRule?: string, reviewerReason?: string, allowAnyway?: boolean,
  approvalOrigin?: string, approvalNote?: string, toolCallId?: string,
): Item[] {
  const copy = [...items];
  const exact = toolCallId && copy.some(it => it.kind === "tool" && it.toolCallId === toolCallId);
  for (let i = copy.length - 1; i >= 0; i--) {
    const it = copy[i];
    if (it.kind !== "tool" || it.name !== name || it.status !== "…") continue;
    if (exact ? it.toolCallId !== toolCallId : toolCallId && it.toolCallId) continue;
    copy[i] = {
      ...it, status, preview,
      ...(hidden ? { hidden } : {}),
      ...(standingRule ? { standingRule } : {}),
      ...(reviewerReason ? { reviewerReason } : {}),
      ...(allowAnyway ? { allowAnyway } : {}),
      ...(approvalOrigin ? { approvalOrigin } : {}),
      ...(approvalNote ? { approvalNote } : {}),
    };
    break;
  }
  return copy;
}
