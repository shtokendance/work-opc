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
  type ResourceItem,
  type ResourceListEnvelope,
  type ResourceRequestError,
} from "../enterpriseResources";
import { openExternal } from "../tauri";
import type { EnterpriseState } from "../enterprise";
import { useEnterpriseState } from "../useEnterpriseState";
import { Icon } from "./Icon";

const PAGE_SIZE = 50;

type PageState =
  | { kind: "loading" }
  | { kind: "ready"; envelope: ResourceListEnvelope }
  | { kind: "empty"; envelope: ResourceListEnvelope }
  | { kind: "error"; message: string };

export function ResourcesView({
  enterprise: enterpriseProp,
  onUseKnowledge,
  onBack,
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
  onBack?: () => void;
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
      <div className="flex-1 flex flex-col h-full bg-canvas items-center justify-center p-8 select-none" data-testid="resources-view">
        <div
          className="flex flex-col items-center justify-center max-w-sm text-center border-2 border-dashed border-line rounded-2xl p-8 bg-panel"
          data-testid="resources-signed-out"
        >
          <div className="w-12 h-12 rounded-2xl bg-accentSoft text-accent flex items-center justify-center mb-4">
            <Icon name="book" size={24} />
          </div>
          <h3 className="text-ui font-semibold text-ink mb-1">
            {t("resources.sign_in_required", "登录企业账号以查看知识库")}
          </h3>
          <p className="text-xs text-muted">
            {t("resources.sign_in_tip", "知识库资产受企业安全策略保护，登录后即可检索并在会话中引用")}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col h-full bg-canvas overflow-hidden select-none" data-testid="resources-view">
      {/* Top Header Bar */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-line bg-panel shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          {onBack && (
            <button
              onClick={onBack}
              className="p-1.5 rounded-lg hover:bg-chromeHover text-muted hover:text-ink transition-colors shrink-0"
              title="返回"
            >
              <Icon name="arrowLeft" size={16} />
            </button>
          )}
          <div className="w-10 h-10 rounded-xl bg-accentSoft/80 border border-accent/20 flex items-center justify-center text-accent shrink-0 shadow-2xs">
            <Icon name="book" size={20} />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="text-lg font-semibold text-ink leading-tight">
                {t("resources.title", "知识库")}
              </h1>
              {tenant && (
                <span className="px-2 py-0.5 rounded-full text-2xs font-medium bg-accentSoft/60 text-accent border border-accent/20">
                  {tenant}
                </span>
              )}
            </div>
            <p className="text-xs text-muted truncate">
              {t("resources.subtitle", "浏览与检索当前空间下的知识库资产，支持在对话中通过 @ 引用查询")}
            </p>
          </div>
        </div>

        {/* Toolbar: Search input + Refresh button + Upload button */}
        <div className="flex items-center gap-2.5 shrink-0">
          <div className="relative w-56 md:w-64">
            <Icon
              name="search"
              size={14}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-muted pointer-events-none"
            />
            <input
              className="w-full pl-8 pr-7 py-1.5 rounded-lg border border-line bg-paper text-xs text-ink placeholder:text-muted outline-none focus:border-accent transition-colors"
              placeholder={t("resources.search_placeholder", "搜索知识库名称")}
              value={q}
              onChange={(e) => search(e.target.value)}
              aria-label={t("resources.search_placeholder", "搜索知识库名称")}
            />
            {q && (
              <button
                onClick={() => search("")}
                className="absolute right-2 top-1/2 -translate-y-1/2 p-0.5 rounded text-muted hover:text-ink hover:bg-chromeHover"
                title="清除"
              >
                <Icon name="x" size={12} />
              </button>
            )}
          </div>

          <button
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-line text-xs font-medium text-muted hover:text-ink hover:bg-chromeHover bg-paper transition-colors shrink-0"
            onClick={() => void load({})}
          >
            <Icon
              name="refresh"
              size={13}
              className={page.kind === "loading" ? "animate-spin" : ""}
            />
            <span>{t("common.refresh", "刷新")}</span>
          </button>
        </div>
      </div>

      {/* Main Content Area */}
      <div className="flex-1 overflow-y-auto p-6">
        {page.kind === "error" && (
          <div
            className="mb-4 p-3.5 rounded-xl bg-danger/10 text-danger text-xs border border-danger/25 flex items-center justify-between"
            role="alert"
          >
            <div className="flex items-center gap-2">
              <Icon name="warning" size={15} className="shrink-0" />
              <span>
                {t("resources.load_failed", "知识库加载失败（{{code}}），请重试", {
                  code: page.message,
                })}
              </span>
            </div>
            <button
              className="px-2.5 py-1 rounded-md bg-danger/15 hover:bg-danger/25 text-danger font-medium transition-colors"
              onClick={() => void load({})}
            >
              {t("common.retry", "重试")}
            </button>
          </div>
        )}

        {page.kind !== "loading" && page.kind !== "error" && page.envelope.complete === false && (
          <div
            className="mb-4 p-3 rounded-xl bg-warning/10 text-warning text-xs border border-warning/25 flex items-center gap-2"
            role="status"
          >
            <Icon name="warning" size={15} className="shrink-0" />
            <span>{t("resources.partial", "部分资源未加载，可重试")}</span>
            <div className="flex flex-wrap gap-1.5 ml-2">
              {page.envelope.failures.map((f) => (
                <span
                  key={f.tenant_id}
                  className="px-1.5 py-0.5 rounded bg-warning/15 text-2xs font-mono text-muted"
                >
                  {f.tenant_id}: {f.code}
                </span>
              ))}
            </div>
          </div>
        )}

        {page.kind === "loading" && (
          <div className="space-y-4">
            <div className="text-xs text-muted flex items-center gap-2">
              <Icon name="refresh" size={12} className="animate-spin text-accent" />
              <span>{t("common.loading", "加载中…")}</span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
              {[1, 2, 3, 4, 5, 6].map((i) => (
                <div
                  key={i}
                  className="rounded-2xl border border-line bg-panel p-4 animate-pulse flex flex-col justify-between h-40"
                >
                  <div className="flex items-start gap-3">
                    <div className="w-10 h-10 rounded-xl bg-paper shrink-0" />
                    <div className="flex-1 space-y-2">
                      <div className="h-4 bg-paper rounded w-3/4" />
                      <div className="h-3 bg-paper rounded w-1/2" />
                    </div>
                  </div>
                  <div className="space-y-1.5 my-2">
                    <div className="h-3 bg-paper rounded w-full" />
                    <div className="h-3 bg-paper rounded w-4/5" />
                  </div>
                  <div className="pt-2 border-t border-line/40 flex justify-between gap-2">
                    <div className="h-7 bg-paper rounded-lg flex-1" />
                    <div className="h-7 bg-paper rounded-lg w-16" />
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {page.kind === "empty" && (
          <div className="flex flex-col items-center justify-center min-h-[340px] text-center border-2 border-dashed border-line rounded-2xl p-8 bg-panel/40">
            <div className="w-12 h-12 rounded-2xl bg-accentSoft text-accent flex items-center justify-center mb-3 shadow-2xs">
              <Icon name="book" size={24} />
            </div>
            <h3 className="text-ui font-semibold text-ink mb-1">
              {t("resources.empty", "暂无可访问资源")}
            </h3>
            <p className="text-xs text-muted max-w-sm">
              {q
                ? "未检索到匹配的知识库，请尝试更换关键词"
                : "当前空间下暂无可访问的知识库资产，可联系管理员在知识库系统进行创建与授权。"}
            </p>
          </div>
        )}

        {page.kind === "ready" && (
          <div className="flex flex-col justify-between min-h-full">
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
              {page.envelope.items.map((item: ResourceItem) => {
                const isAvail = item.availability === "available";
                return (
                  <article
                    key={`${item.tenant_id}:${item.knowledge_base_id}`}
                    className="group rounded-2xl border border-line bg-panel p-4 hover:border-accent/40 hover:shadow-md transition-all duration-200 flex flex-col justify-between relative overflow-hidden"
                  >
                    <div>
                      {/* Top Header: Icon + Name/Tenant + Availability Pill */}
                      <div className="flex items-start justify-between gap-2.5 mb-2.5">
                        <div className="flex items-center gap-2.5 min-w-0">
                          <div className="w-10 h-10 rounded-xl bg-accentSoft/80 border border-accent/20 flex items-center justify-center text-accent shrink-0 group-hover:scale-105 transition-transform">
                            <Icon name="book" size={20} />
                          </div>
                          <div className="min-w-0">
                            <h3
                              className="font-semibold text-ui text-ink group-hover:text-accent transition-colors truncate"
                              title={item.name}
                            >
                              {item.name}
                            </h3>
                            <div className="flex items-center gap-1.5 mt-0.5">
                              <span className="inline-flex items-center px-1.5 py-0.5 rounded text-2xs bg-paper border border-line text-muted truncate max-w-[130px]">
                                {item.tenant_name || item.tenant_id}
                              </span>
                            </div>
                          </div>
                        </div>

                        <span
                          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-2xs font-medium shrink-0 ${
                            isAvail
                              ? "bg-emerald-500/10 text-emerald-600 border border-emerald-500/20"
                              : "bg-warning/10 text-warning border border-warning/20"
                          }`}
                        >
                          <span
                            className={`w-1.5 h-1.5 rounded-full ${
                              isAvail ? "bg-emerald-500" : "bg-warning"
                            }`}
                          />
                          {isAvail ? t("resources.available", "可用") : t("resources.not_ready", "未就绪")}
                        </span>
                      </div>

                      {/* Description */}
                      <p
                        className="text-xs text-muted line-clamp-2 min-h-[2.25rem] leading-relaxed mb-3"
                        title={item.description || ""}
                      >
                        {item.description || "企业知识库资产"}
                      </p>
                    </div>

                    {/* Footer Actions: 使用 + 详情 */}
                    <div className="pt-3 border-t border-line/60 flex items-center gap-2">
                      <button
                        className="flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent/10 hover:bg-accent hover:text-white text-accent font-medium text-xs transition-colors disabled:opacity-40 disabled:pointer-events-none"
                        disabled={!isAvail}
                        onClick={() =>
                          onUseKnowledge?.({
                            tenant_id: item.tenant_id,
                            knowledge_base_id: item.knowledge_base_id || "",
                            context_id: contextId,
                            label: `${item.name}（${item.tenant_name || item.tenant_id}）`,
                          })
                        }
                      >
                        <Icon name="chat" size={13} />
                        <span>{t("resources.use", "使用")}</span>
                      </button>

                      <button
                        className="px-3 py-1.5 rounded-lg border border-line bg-paper hover:bg-chromeHover text-muted hover:text-ink text-xs font-medium flex items-center gap-1 transition-colors shrink-0"
                        onClick={() =>
                          openUrlSafely(
                            buildResourceWebUrl(
                              "knowledge_base",
                              item.knowledge_base_id || "",
                              item.tenant_id,
                            ),
                          )
                        }
                      >
                        <Icon name="arrowRight" size={12} />
                        <span>{t("resources.details", "详情")}</span>
                      </button>
                    </div>
                  </article>
                );
              })}
            </div>

            {/* Pagination Controls */}
            <div className="flex items-center justify-between mt-6 pt-4 border-t border-line text-xs text-muted">
              <div>
                共 {page.envelope.total} 个知识库
              </div>
              <div className="flex items-center gap-2.5">
                <button
                  className="px-2.5 py-1 rounded-lg border border-line bg-paper hover:bg-chromeHover text-ink disabled:opacity-30 disabled:pointer-events-none transition-colors"
                  disabled={page.envelope.offset === 0}
                  onClick={() => gotoPage(Math.max(0, page.envelope.offset - PAGE_SIZE))}
                >
                  ←
                </button>
                <span className="font-mono">
                  {page.envelope.offset + 1}–
                  {Math.min(page.envelope.offset + PAGE_SIZE, page.envelope.total)} /{" "}
                  {page.envelope.complete ? page.envelope.total : `${page.envelope.total}+`}
                </span>
                <button
                  className="px-2.5 py-1 rounded-lg border border-line bg-paper hover:bg-chromeHover text-ink disabled:opacity-30 disabled:pointer-events-none transition-colors"
                  disabled={page.envelope.offset + PAGE_SIZE >= page.envelope.total}
                  onClick={() => gotoPage(page.envelope.offset + PAGE_SIZE)}
                >
                  →
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default ResourcesView;
