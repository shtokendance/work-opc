// Resource label chips on conversation messages (WORK-RESOURCES-001, DATA-102,
// BND-009) — the persisted `_resources` sidecar renders as read-only tags:
// knowledge scope and enterprise skill with their SERVER-GENERATED labels.
// History without the field renders nothing (no guessed backfill).

import { useTranslation } from "react-i18next";

export interface ResourceLabels {
  knowledge_scope?: {
    tenant_id: string;
    knowledge_base_id: string;
    tenant_name?: string;
    knowledge_base_name?: string;
  };
  skill_ref?: {
    source: string;
    tenant_id: string;
    catalog_id: string;
    bundle_sha256: string;
    name?: string;
  };
}

export function labelsFromMessage(message: unknown): ResourceLabels | null {
  if (!message || typeof message !== "object") return null;
  const meta = (message as { _resources?: { labels?: ResourceLabels } })._resources;
  if (!meta || typeof meta !== "object" || !meta.labels) return null;
  return meta.labels;
}

export function ResourceChips({ labels }: { labels: ResourceLabels }) {
  const { t } = useTranslation();
  if (!labels.knowledge_scope && !labels.skill_ref) return null;
  return (
    <span className="inline-flex flex-wrap gap-1 align-middle" data-testid="resource-chips">
      {labels.knowledge_scope && (
        <span className="text-label px-2 py-0.5 rounded-full border border-line bg-paper text-muted">
          {t("message.knowledge_scope", "知识库")}:{" "}
          {labels.knowledge_scope.knowledge_base_name || labels.knowledge_scope.knowledge_base_id}
          {labels.knowledge_scope.tenant_name
            ? `（${labels.knowledge_scope.tenant_name}）`
            : ""}
        </span>
      )}
      {labels.skill_ref && (
        <span className="text-label px-2 py-0.5 rounded-full border border-line bg-paper text-muted">
          {t("message.skill_ref", "企业技能")}:{" "}
          {labels.skill_ref.name || labels.skill_ref.catalog_id}
        </span>
      )}
    </span>
  );
}

export default ResourceChips;
