// Enterprise skills panel (WORK-RESOURCES-001, BND-002/006/009/010) — the
// 企业技能 + 已安装 views: explicit install/update with real progress, enable
// /disable/uninstall with feedback, legacy publish_id installs surfaced as
// 需重装 (never executable). 浏览不安装；未成功不显示可用。

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  assertSafeExternalUrl,
  buildResourceWebUrl,
  enterpriseResources,
  pollOperation,
  type LegacyInstall,
  type OperationView,
  type ResourceItem,
  type ResourceListEnvelope,
  type ResourceRequestError,
} from "../enterpriseResources";
import { openExternal } from "../tauri";
import type { EnterpriseState } from "../enterprise";
import { useEnterpriseState } from "../useEnterpriseState";

const PAGE_SIZE = 50;

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; envelope: ResourceListEnvelope; legacy: LegacyInstall[] }
  | { kind: "empty"; envelope: ResourceListEnvelope; legacy: LegacyInstall[] }
  | { kind: "error"; message: string };

/** Per-catalog client-side operation tracking (progress display only — the
 * server owns operation truth; page refresh re-polls by nothing here, the
 * server keeps terminal states). */
type ProgressMap = Record<string, { status: string; error?: string }>;

export function EnterpriseSkillsPanel({ enterprise: enterpriseProp }: { enterprise?: EnterpriseState }) {
  const hooked = useEnterpriseState(30_000, enterpriseProp === undefined);
  const enterprise = enterpriseProp ?? hooked;
  const { t } = useTranslation();
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [contextId, setContextId] = useState("");
  const [progress, setProgress] = useState<ProgressMap>({});
  const generation = useRef(0);
  const pollAbort = useRef<AbortController | null>(null);

  const signedIn = enterprise.kind === "signed_in";
  const subject = signedIn ? enterprise.subject : "";
  const tenant = signedIn ? enterprise.tenant : "";

  const load = useCallback(
    async (opts: { q?: string; offset?: number } = {}) => {
      const mine = ++generation.current;
      setState({ kind: "loading" });
      try {
        const envelope = await enterpriseResources.listSkills({
          q: opts.q ?? q,
          offset: opts.offset ?? offset,
          limit: PAGE_SIZE,
          tenantId: tenant || undefined,
        });
        if (mine !== generation.current) return; // 迟到响应丢弃
        setContextId(envelope.context_id);
        const legacy = envelope.legacy_installs || [];
        if (!envelope.items.length && !legacy.length) {
          setState({ kind: "empty", envelope, legacy });
        } else {
          setState({ kind: "ready", envelope, legacy });
        }
      } catch (err) {
        if (mine !== generation.current) return;
        setState({
          kind: "error",
          message: (err as ResourceRequestError).code || "UPSTREAM_UNAVAILABLE",
        });
      }
    },
    [q, offset, tenant],
  );

  useEffect(() => {
    if (signedIn) {
      setQ("");
      setOffset(0);
      setProgress({});
      void load({ q: "", offset: 0 });
    }
    return () => {
      generation.current += 1;
      pollAbort.current?.abort(); // 离页取消轮询（INT-105）
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signedIn, subject, tenant]);

  const keyOf = (item: ResourceItem) => `${item.tenant_id}:${item.catalog_id}`;

  const track = (key: string, patch: { status: string; error?: string }) =>
    setProgress((prev) => ({ ...prev, [key]: patch }));

  const install = async (item: ResourceItem) => {
    const key = keyOf(item);
    if (!item.bundle_sha256 || item.availability !== "available") return; // not_ready 禁用
    if (progress[key] && progress[key].status === "running") return; // 防重复点击
    track(key, { status: "running" });
    try {
      const accepted = await enterpriseResources.installSkill({
        tenantId: item.tenant_id,
        catalogId: item.catalog_id || "",
        expectedSha256: item.bundle_sha256,
        contextId: contextId,
        name: item.name,
        version: item.version,
      });
      if ((accepted as OperationView).operation_id) {
        const op = accepted as OperationView;
        const controller = new AbortController();
        pollAbort.current = controller;
        const final = await pollOperation(op.operation_id, { signal: controller.signal });
        if (final.status === "succeeded") {
          track(key, { status: "done" });
        } else {
          // 失败保留旧 active（服务端保证），行内显示错误码
          track(key, { status: "failed", error: final.error?.code || "INSTALL_FAILED" });
        }
      } else {
        track(key, { status: "done" }); // 200 幂等：已安装同 SHA
      }
      await load({}); // 刷新本地状态列
    } catch (err) {
      track(key, {
        status: "failed",
        error: (err as ResourceRequestError).code || "INSTALL_FAILED",
      });
    }
  };

  const setEnabled = async (item: ResourceItem, enabled: boolean) => {
    try {
      await enterpriseResources.setSkillEnabled({
        tenantId: item.tenant_id,
        catalogId: item.catalog_id || "",
        contextId: contextId,
        enabled,
      });
      await load({});
    } catch {
      await load({});
    }
  };

  const uninstall = async (item: ResourceItem) => {
    try {
      await enterpriseResources.uninstallSkill({
        tenantId: item.tenant_id,
        catalogId: item.catalog_id || "",
        contextId: contextId,
      });
      await load({});
    } catch {
      await load({});
    }
  };

  const openDetail = (item: ResourceItem) => {
    const url = buildResourceWebUrl("skill_catalog", item.catalog_id || "", item.tenant_id);
    const safe = url && assertSafeExternalUrl(url);
    if (safe) openExternal(safe);
  };

  if (!signedIn) {
    return (
      <div className="p-6 text-ui text-muted" data-testid="eskills-signed-out">
        {t("resources.sign_in_required", "登录企业账号以查看企业技能")}
      </div>
    );
  }

  const statusText = (item: ResourceItem): string => {
    const local = item.local_status || "not_installed";
    const running = progress[keyOf(item)];
    if (running?.status === "running")
      return t("eskills.installing", "下载/校验中…");
    if (running?.status === "failed")
      return t("eskills.install_failed", "安装失败（{{code}}）", { code: running.error || "" });
    if (local === "installed_enabled")
      return item.installed_sha256 === item.bundle_sha256
        ? t("eskills.installed", "已安装")
        : t("eskills.update_available", "有更新");
    if (local === "installed_disabled") return t("eskills.disabled", "已停用");
    return item.availability === "not_ready"
      ? t("eskills.not_ready", "归档未就绪")
      : t("eskills.not_installed", "未安装");
  };

  return (
    <div className="flex flex-col gap-3 p-4" data-testid="enterprise-skills-panel">
      <div className="flex items-center gap-2">
        <input
          className="w-full min-w-0 px-3 py-2 rounded-lg border border-line bg-paper text-ui text-ink outline-none focus:border-accent"
          placeholder={t("eskills.search_placeholder", "搜索技能名称")}
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setOffset(0);
            void load({ q: e.target.value, offset: 0 });
          }}
          aria-label={t("eskills.search_placeholder", "搜索技能名称")}
        />
        <button
          className="text-ui px-3 py-2 rounded-lg border border-line bg-paper shrink-0"
          onClick={() => void load({})}
        >
          {t("common.refresh", "刷新")}
        </button>
      </div>

      {state.kind === "loading" && <div className="text-ui text-muted">{t("common.loading", "加载中…")}</div>}
      {state.kind === "error" && (
        <div className="text-ui text-danger" role="alert">
          {t("resources.load_failed", "加载失败（{{code}}），请重试", { code: state.message })}
          <button className="ml-2 underline" onClick={() => void load({})}>
            {t("common.retry", "重试")}
          </button>
        </div>
      )}
      {state.kind === "empty" && (
        <div className="text-ui text-muted">{t("resources.empty", "暂无可访问资源")}</div>
      )}

      {(state.kind === "ready" || state.kind === "empty") && state.envelope.complete === false && (
        <div className="text-ui text-warning" role="status">
          {t("resources.partial", "部分资源未加载，可重试")}
        </div>
      )}

      {(state.kind === "ready" || state.kind === "empty") && (
        <ul className="flex flex-col gap-2">
          {state.legacy.map((legacy) => (
            <li
              key={`legacy:${legacy.publish_id}`}
              className="rounded-xl2 border border-line bg-panel p-3 flex items-center gap-3"
              data-testid="legacy-install-row"
            >
              <div className="min-w-0 flex-1">
                <div className="text-ui font-medium text-ink truncate">
                  {legacy.name}
                  <span className="ml-2 text-label text-warning">
                    {t("eskills.legacy_requires_reinstall", "旧版安装，需从企业目录重新安装")}
                  </span>
                </div>
              </div>
            </li>
          ))}
          {state.envelope.items.map((item) => {
            const key = keyOf(item);
            const running = progress[key];
            const local = item.local_status || "not_installed";
            const installed = local !== "not_installed";
            const updateAvailable =
              installed && item.installed_sha256 !== item.bundle_sha256;
            return (
              <li
                key={key}
                className="rounded-xl2 border border-line bg-panel p-3 flex items-center gap-3"
                data-testid={`skill-row:${key}`}
              >
                <div className="min-w-0 flex-1">
                  <div className="text-ui font-medium text-ink truncate">
                    {item.name}
                    <span className="ml-2 text-label text-muted">
                      {item.tenant_name || item.tenant_id}
                    </span>
                  </div>
                  <div className="text-label text-muted truncate">
                    {item.description || ""} · {statusText(item)}
                  </div>
                </div>
                {!installed && (
                  <button
                    className="text-ui px-3 py-1.5 rounded-lg bg-accent text-white shrink-0 disabled:opacity-40"
                    disabled={
                      item.availability !== "available" ||
                      (running?.status === "running")
                    }
                    onClick={() => void install(item)}
                  >
                    {t("eskills.install", "安装")}
                  </button>
                )}
                {installed && (
                  <>
                    <button
                      className="text-ui px-3 py-1.5 rounded-lg border border-line bg-paper shrink-0"
                      disabled={running?.status === "running"}
                      onClick={() => void install(item)} // 更新=同接口，expected 用当前 SHA
                    >
                      {updateAvailable
                        ? t("eskills.update", "更新")
                        : t("eskills.reinstall", "重装")}
                    </button>
                    <button
                      className="text-ui px-3 py-1.5 rounded-lg border border-line bg-paper shrink-0"
                      onClick={() => void setEnabled(item, local !== "installed_enabled")}
                    >
                      {local === "installed_enabled"
                        ? t("eskills.disable", "停用")
                        : t("eskills.enable", "启用")}
                    </button>
                    <button
                      className="text-ui px-3 py-1.5 rounded-lg border border-line bg-paper shrink-0"
                      disabled={running?.status === "running"}
                      onClick={() => void uninstall(item)}
                    >
                      {t("eskills.uninstall", "卸载")}
                    </button>
                  </>
                )}
                <button
                  className="text-ui px-3 py-1.5 rounded-lg border border-line bg-paper shrink-0"
                  onClick={() => openDetail(item)}
                >
                  {t("resources.details", "详情")}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export default EnterpriseSkillsPanel;
