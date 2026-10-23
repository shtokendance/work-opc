// Enterprise resources client (WORK-RESOURCES-001, BND-002/003/009, INT-101/103).
//
// The GUI's half of the resource entry: list/inspect knowledge bases and skill
// catalogs, drive install/update/disable/uninstall with operation polling, and
// build the FEW trusted web URLs the client is allowed to open. Everything the
// server returns is treated as display data; IDs are URL-encoded, never path-
// concatenated, and every external URL passes the exact-origin guard before it
// reaches openExternal.

import { httpBase } from "./api";

export const TRUSTED_WEB_ORIGIN = "https://shtokendance.benlai.com";

export type ResourceAvailability = "available" | "not_ready";

export interface ResourceItem {
  resource_kind: "knowledge_base" | "skill_catalog";
  tenant_id: string;
  tenant_name?: string;
  knowledge_base_id?: string;
  catalog_id?: string;
  name: string;
  description?: string;
  version?: string;
  bundle_sha256?: string;
  availability: ResourceAvailability;
  unavailable_reason?: string;
  detail_url?: string;
  // sidecar-attached local install state (INT-101)
  local_status?: "not_installed" | "installed_enabled" | "installed_disabled";
  installed_sha256?: string;
  enabled?: boolean;
}

export interface TenantFailure {
  tenant_id: string;
  code: string;
}

export interface ResourceListEnvelope {
  items: ResourceItem[];
  total: number;
  offset: number;
  limit: number;
  complete: boolean;
  failures: TenantFailure[];
  context_id: string;
}

export interface OperationView {
  operation_id: string;
  status: "downloading" | "verifying" | "activating" | "succeeded" | "failed";
  context_id: string;
  tenant_id: string;
  catalog_id: string;
  expected_sha256: string;
  installed_sha256?: string;
  error?: { code: string; message: string };
}

export interface LegacyInstall {
  publish_id: string;
  name: string;
  version: string;
  sha256: string;
  status: "legacy_requires_reinstall";
}

export interface ResourceError {
  code: string;
  message: string;
}

export class ResourceRequestError extends Error {
  code: string;
  status: number;
  constructor(status: number, body: { code: string; message: string }) {
    super(body.message);
    this.code = body.code;
    this.status = status;
  }
}

const launchToken = (): string =>
  (globalThis as any).__COWORKER_API_TOKEN__ ||
  (import.meta as any).env?.VITE_COWORKER_API_TOKEN ||
  "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // 绝对地址（与 api.ts 一致）：桌面 WebView 下相对 fetch 解析失败被静默吞掉
  // （r1 DMG 实测知识库/技能视图永远停在未登录），必须走 httpBase()。
  const res = await fetch(`${httpBase()}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      "x-openworker-token": launchToken(),
      ...(init?.headers || {}),
    },
  });
  let body: any = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) {
    const err = (body?.error || {}) as Partial<ResourceError>;
    throw new ResourceRequestError(res.status, {
      code: err.code || "UPSTREAM_UNAVAILABLE",
      message: err.message || `request failed (${res.status})`,
    });
  }
  return body as T;
}

function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export const enterpriseResources = {
  listKnowledgeBases(args: {
    q?: string;
    tenantId?: string;
    offset?: number;
    limit?: number;
  } = {}): Promise<ResourceListEnvelope> {
    return request(
      `/v1/enterprise/resources/knowledge-bases${qs({
        q: args.q,
        tenant_id: args.tenantId,
        offset: args.offset,
        limit: args.limit,
      })}`,
    );
  },

  listSkills(args: {
    q?: string;
    tenantId?: string;
    offset?: number;
    limit?: number;
  } = {}): Promise<ResourceListEnvelope & { legacy_installs?: LegacyInstall[] }> {
    return request(
      `/v1/enterprise/resources/skills${qs({
        q: args.q,
        tenant_id: args.tenantId,
        offset: args.offset,
        limit: args.limit,
      })}`,
    );
  },

  installSkill(args: {
    tenantId: string;
    catalogId: string;
    expectedSha256: string;
    contextId: string;
    name?: string;
    version?: string;
  }): Promise<OperationView | { status: "installed"; installed_sha256: string }> {
    return request(`/v1/enterprise/resources/skills/${encodeURIComponent(args.catalogId)}/install`, {
      method: "POST",
      body: JSON.stringify({
        tenant_id: args.tenantId,
        expected_sha256: args.expectedSha256,
        context_id: args.contextId,
        name: args.name || "",
        version: args.version || "",
      }),
    });
  },

  setSkillEnabled(args: {
    tenantId: string;
    catalogId: string;
    contextId: string;
    enabled: boolean;
  }): Promise<{ record: { enabled: boolean } }> {
    return request(`/v1/enterprise/resources/skills/${encodeURIComponent(args.catalogId)}`, {
      method: "PATCH",
      body: JSON.stringify({
        tenant_id: args.tenantId,
        context_id: args.contextId,
        enabled: args.enabled,
      }),
    });
  },

  uninstallSkill(args: {
    tenantId: string;
    catalogId: string;
    contextId: string;
  }): Promise<{ removed: boolean }> {
    return request(
      `/v1/enterprise/resources/skills/${encodeURIComponent(args.catalogId)}${qs({
        tenant_id: args.tenantId,
        context_id: args.contextId,
      })}`,
      { method: "DELETE" },
    );
  },

  getOperation(operationId: string): Promise<OperationView> {
    return request(`/v1/enterprise/resources/operations/${encodeURIComponent(operationId)}`);
  },
};

// -- trusted web URLs (BND-003) ------------------------------------------------

/** Build the knowledge-web detail URL for a KB or skill catalog. Only ever
 * produces a same-origin https URL on the trusted host with an encoded ID —
 * anything else returns null and the caller must not open anything. */
export function buildResourceWebUrl(
  kind: "knowledge_base" | "skill_catalog",
  id: string,
  tenantId?: string,
): string | null {
  if (!id || /[/\\?#]/.test(id)) return null; // IDs are single segments
  const encoded = encodeURIComponent(id);
  let path: string;
  if (kind === "knowledge_base") {
    path = `/platform/knowledge-bases/${encoded}`;
  } else {
    path = `/platform/toolbox/skills?catalog_id=${encoded}`;
  }
  const url = new URL(`${TRUSTED_WEB_ORIGIN}${path}`);
  if (tenantId) {
    if (/[/\\?#]/.test(tenantId)) return null;
    url.searchParams.set("tenant_id", tenantId);
  }
  return assertSafeExternalUrl(url.toString());
}

/** The upload entry is ALWAYS just the confirmed homepage — no deep link, no
 * token, no query (BND-003). */
export function uploadHomeUrl(): string {
  return TRUSTED_WEB_ORIGIN;
}

/** Exact-origin guard shared by every external open (BND-003): https only,
 * exact trusted host, no userinfo, no non-default port. Returns the URL when
 * safe, null otherwise. */
export function assertSafeExternalUrl(raw: string): string | null {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (url.protocol !== "https:") return null;
  if (url.host !== "shtokendance.benlai.com") return null; // host includes the port
  if (url.username || url.password) return null;
  return url.toString();
}

/** Poll an operation until a terminal state (min interval 1s per INT-105;
 * the caller stops polling on unmount via the AbortSignal). */
export async function pollOperation(
  operationId: string,
  options: { signal?: AbortSignal; intervalMs?: number; timeoutMs?: number } = {},
): Promise<OperationView> {
  const interval = Math.max(1000, options.intervalMs ?? 1000);
  const deadline = Date.now() + (options.timeoutMs ?? 5 * 60_000);
  for (;;) {
    const view = await enterpriseResources.getOperation(operationId);
    if (view.status === "succeeded" || view.status === "failed") return view;
    if (Date.now() >= deadline) {
      return { ...view, status: "failed", error: { code: "TIMEOUT", message: "operation polling timed out" } };
    }
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
}

// -- composer message fields (DATA-102) ----------------------------------------

export interface KnowledgeScopeSelection {
  tenant_id: string;
  knowledge_base_id: string;
  context_id: string;
  /** display only — the server generates authoritative labels */
  label: string;
}

export interface SkillRefSelection {
  source: "catalog";
  tenant_id: string;
  catalog_id: string;
  bundle_sha256: string;
  context_id: string;
  label: string;
}

/** Send a user_message carrying optional resource selections. Kept separate
 * from api.ts's userMessage so existing callers are untouched; the session's
 * private `send` is the same transport the composer's userMessage uses. */
export function sendResourceMessage(
  session: { send?: (payload: object) => void; userMessage?: (...args: unknown[]) => void },
  args: {
    text: string;
    model?: string;
    knowledgeScope?: KnowledgeScopeSelection | null;
    skillRef?: SkillRefSelection | null;
  },
): void {
  const send = (session as any).send?.bind(session);
  if (typeof send !== "function") throw new Error("session is not connected");
  send({
    type: "user_message",
    text: args.text,
    ...(args.model ? { model: args.model } : {}),
    ...(args.knowledgeScope
      ? {
          knowledge_scope: {
            tenant_id: args.knowledgeScope.tenant_id,
            knowledge_base_id: args.knowledgeScope.knowledge_base_id,
            context_id: args.knowledgeScope.context_id,
          },
        }
      : {}),
    ...(args.skillRef
      ? {
          skill_ref: {
            source: "catalog",
            tenant_id: args.skillRef.tenant_id,
            catalog_id: args.skillRef.catalog_id,
            bundle_sha256: args.skillRef.bundle_sha256,
            context_id: args.skillRef.context_id,
          },
        }
      : {}),
  });
}

/** M7（BND-004.2）：远端执行会话（会话跑在已接入机器上）禁止使用本机资源
 * 选择。返回应展示的提示文案，null 表示可发送。纯函数以便单测。 */
export function remoteSessionResourceBlock(
  sessionId: string | null | undefined,
  machineOf: (sid: string) => string | null,
  resources?: { knowledgeScope?: unknown; skillRef?: unknown } | null,
): string | null {
  if (!resources?.knowledgeScope && !resources?.skillRef) return null;
  if (!sessionId) return null;
  if (!machineOf(sessionId)) return null;
  return (
    "Remote sessions cannot use this machine's knowledge scope or enterprise " +
    "skills — use them in a local session."
  );
}
