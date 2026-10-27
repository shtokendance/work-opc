// 知识库列表页 (WORK-RESOURCES-001, BND-002/009) — the lightweight entry:
// search / refresh / pagination, partial + empty + error + signed-out states,
// per-row 使用 (prepares the composer scope, never sends) / 详情 (system
// browser, exact-origin only) / 上传 (homepage only). Browsing NEVER installs.

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  assertSafeExternalUrl,
  buildResourceWebUrl,
  enterpriseResources,
  uploadHomeUrl,
  type ResourceItem,
  type ResourceListEnvelope,
  type ResourceRequestError,
} from "../enterpriseResources";
import { openExternal } from "../tauri";
import type { EnterpriseState } from "../enterprise";
import { useEnterpriseState } from "../useEnterpriseState";

const PAGE_SIZE = 50;

type PageState =
  | { kind: "loading" }
  | { kind: "ready"; envelope: ResourceListEnvelope }
  | { kind: "empty"; envelope: ResourceListEnvelope }
  | { kind: "error"; message: string };

export function ResourcesView({
  enterprise: enterpriseProp,
  onUseKnowledge,
}: {
  enterprise?: EnterpriseState;
  /** "使用" → prepare the composer (BND-004.2): carries the KB into the
   * composer of the current LOCAL session; never sends. */
  onUseKnowledge?: (selection: {
    tenant_id: string;
    knowledge_base_id: string;
    context_id: string;
    label: string;
  }) => void;
}) {
  const hooked = useEnterpriseState(30_000, enterpriseProp === undefined);
  const enterprise = enterpriseProp ?? hooked;
  const { t } = useTranslation();
  const [page, setPage] = useState<PageState>({ kind: "loading" });
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [contextId, setContextId] = useState("");
  const generation = useRef(0);

  const signedIn = enterprise.kind === "signed_in";
  const tenant = enterprise.kind === "signed_in" ? enterprise.tenant : "";

  const load = useCallback(
    async (opts: { q?: string; offset?: number } = {}) => {
      const mine = ++generation.current;
      setPage({ kind: "loading" });
      try {
        const envelope = await enterpriseResources.listKnowledgeBases({
          q: opts.q ?? q,
          offset: opts.offset ?? offset,
          limit: PAGE_SIZE,
          tenantId: tenant || undefined,
        });
        if (mine !== generation.current) return; // 迟到响应丢弃（BND-002.7）
        setContextId(envelope.context_id);
        if (!envelope.items.length && envelope.total === 0) {
          setPage({ kind: "empty", envelope });
        } else {
          setPage({ kind: "ready", envelope });
        }
      } catch (err) {
        if (mine !== generation.current) return;
        const code = (err as ResourceRequestError).code || "UPSTREAM_UNAVAILABLE";
        setPage({ kind: "error", message: code });
      }
    },
    [q, offset, tenant],
  );

  const subject = enterprise.kind === "signed_in" ? enterprise.subject : "";
  useEffect(() => {
    if (signedIn) {
      setQ("");
      setOffset(0);
      void load({ q: "", offset: 0 });
    }
    // 身份切换立即清除旧结果（BND-002.7）；迟到响应由 generation 丢弃。
    return () => {
      generation.current += 1;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signedIn, subject, tenant]);

  const search = (value: string) => {
    setQ(value);
    setOffset(0);
    void load({ q: value, offset: 0 });
  };

  const gotoPage = (next: number) => {
    setOffset(next);
    void load({ offset: next });
  };

  const openUrlSafely = (url: string | null) => {
    if (!url) return;
    const safe = assertSafeExternalUrl(url);
    if (!safe) return; // 永不打开非法地址（BND-003）
    openExternal(safe);
  };

  if (!signedIn) {
    return (
      <div className="p-6 text-ui text-muted" data-testid="resources-signed-out">
        {t("resources.sign_in_required", "登录企业账号以查看知识库")}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 p-4" data-testid="resources-view">
      <div className="flex items-center gap-2">
        <input
          className="w-full min-w-0 px-3 py-2 rounded-lg border border-line bg-paper text-ui text-ink outline-none focus:border-accent"
          placeholder={t("resources.search_placeholder", "搜索知识库名称")}
          value={q}
          onChange={(e) => search(e.target.value)}
          aria-label={t("resources.search_placeholder", "搜索知识库名称")}
        />
        <button
          className="text-ui px-3 py-2 rounded-lg border border-line bg-paper hover:border-lineStrong shrink-0"
          onClick={() => void load({})}
        >
          {t("common.refresh", "刷新")}
        </button>
        <button
          className="text-ui px-3 py-2 rounded-lg bg-accent text-white shrink-0"
          onClick={() => openUrlSafely(uploadHomeUrl())}
        >
          {t("resources.upload", "上传")}
        </button>
      </div>

      {page.kind === "loading" && <div className="text-ui text-muted">{t("common.loading", "加载中…")}</div>}

      {page.kind === "error" && (
        <div className="text-ui text-danger" role="alert">
          {t("resources.load_failed", "知识库加载失败（{{code}}），请重试", { code: page.message })}
          <button className="ml-2 underline" onClick={() => void load({})}>
            {t("common.retry", "重试")}
          </button>
        </div>
      )}

      {page.kind !== "loading" && page.kind !== "error" && page.envelope.complete === false && (
        <div className="text-ui text-warning" role="status">
          {t("resources.partial", "部分资源未加载，可重试")}
          {page.envelope.failures.map((f) => (
            <span key={f.tenant_id} className="ml-2 text-muted">
              {f.tenant_id}: {f.code}
            </span>
          ))}
        </div>
      )}

      {page.kind === "empty" && (
        <div className="text-ui text-muted">{t("resources.empty", "暂无可访问资源")}</div>
      )}

      {page.kind === "ready" && (
        <>
          <ul className="flex flex-col gap-2">
            {page.envelope.items.map((item: ResourceItem) => (
              <li
                key={`${item.tenant_id}:${item.knowledge_base_id}`}
                className="rounded-xl2 border border-line bg-panel p-3 flex items-center gap-3"
              >
                <div className="min-w-0 flex-1">
                  <div className="text-ui font-medium text-ink truncate">{item.name}</div>
                  <div className="text-label text-muted truncate">
                    {item.tenant_name || item.tenant_id}
                    {item.description ? ` · ${item.description}` : ""}
                  </div>
                </div>
                <button
                  className="text-ui px-3 py-1.5 rounded-lg border border-line bg-paper shrink-0"
                  disabled={item.availability !== "available"}
                  onClick={() =>
                    onUseKnowledge?.({
                      tenant_id: item.tenant_id,
                      knowledge_base_id: item.knowledge_base_id || "",
                      context_id: contextId,
                      label: `${item.name}（${item.tenant_name || item.tenant_id}）`,
                    })
                  }
                >
                  {t("resources.use", "使用")}
                </button>
                <button
                  className="text-ui px-3 py-1.5 rounded-lg border border-line bg-paper shrink-0"
                  onClick={() =>
                    openUrlSafely(
                      buildResourceWebUrl("knowledge_base", item.knowledge_base_id || "", item.tenant_id),
                    )
                  }
                >
                  {t("resources.details", "详情")}
                </button>
              </li>
            ))}
          </ul>
          <div className="flex items-center gap-2 text-ui text-muted">
            <button
              className="px-2 py-1 rounded border border-line disabled:opacity-40"
              disabled={page.envelope.offset === 0}
              onClick={() => gotoPage(Math.max(0, page.envelope.offset - PAGE_SIZE))}
            >
              ←
            </button>
            <span>
              {page.envelope.offset + 1}–
              {Math.min(page.envelope.offset + PAGE_SIZE, page.envelope.total)} /{" "}
              {page.envelope.complete ? page.envelope.total : `${page.envelope.total}+`}
            </span>
            <button
              className="px-2 py-1 rounded border border-line disabled:opacity-40"
              disabled={page.envelope.offset + PAGE_SIZE >= page.envelope.total}
              onClick={() => gotoPage(page.envelope.offset + PAGE_SIZE)}
            >
              →
            </button>
          </div>
        </>
      )}
    </div>
  );
}

export default ResourcesView;
