import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  appDeliveryRequest,
  downloadAppArtifact,
  listSkills,
  updateSkill,
  type AppDeliveryJob,
  type SkillRow,
} from "../api";
import { Icon } from "./Icon";

export function AppSkillsPanel({
  onNavigateToApps,
}: {
  onNavigateToApps?: () => void;
}) {
  const { t } = useTranslation();
  const [jobs, setJobs] = useState<AppDeliveryJob[]>([]);
  const [skills, setSkills] = useState<SkillRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [q, setQ] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [acting, setActing] = useState<Record<string, boolean>>({});
  const [notice, setNotice] = useState<{ id: string; text: string; tone: "ok" | "error" } | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [jobsRes, skillsRes] = await Promise.allSettled([
        appDeliveryRequest<{ jobs: AppDeliveryJob[] }>("jobs"),
        listSkills(),
      ]);

      if (jobsRes.status === "fulfilled") {
        setJobs(jobsRes.value.jobs || []);
      } else {
        setJobs([]);
      }

      if (skillsRes.status === "fulfilled") {
        setSkills(skillsRes.value || []);
      } else {
        setSkills([]);
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const toggleExpand = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleToggleSkill = async (skillName: string, currentEnabled: boolean) => {
    setActing((prev) => ({ ...prev, [skillName]: true }));
    setNotice(null);
    try {
      const res = await updateSkill(skillName, { enabled: !currentEnabled });
      if (res.ok === false) {
        setNotice({ id: skillName, text: res.error || t("skills.error_generic"), tone: "error" });
      } else {
        setSkills((prev) =>
          prev.map((s) => (s.name === skillName ? { ...s, enabled: !currentEnabled } : s)),
        );
        setNotice({
          id: skillName,
          text: !currentEnabled ? "已启用该应用技能" : "已停用该应用技能",
          tone: "ok",
        });
      }
    } catch (err) {
      setNotice({ id: skillName, text: String(err), tone: "error" });
    } finally {
      setActing((prev) => ({ ...prev, [skillName]: false }));
    }
  };

  const handleVerifyAndEnable = async (job: AppDeliveryJob) => {
    setActing((prev) => ({ ...prev, [job.id]: true }));
    setNotice({ id: job.id, text: "正在校验实际部署及只读业务能力…", tone: "ok" });
    try {
      await appDeliveryRequest("enable-skill", { job_id: job.id });
      setNotice({ id: job.id, text: "业务验证通过，本地应用技能已启用", tone: "ok" });
      await loadData();
    } catch (err) {
      setNotice({ id: job.id, text: `启用失败：${String(err)}`, tone: "error" });
    } finally {
      setActing((prev) => ({ ...prev, [job.id]: false }));
    }
  };

  const handleExport = async (job: AppDeliveryJob) => {
    try {
      await downloadAppArtifact(job, "skill.zip");
      setNotice({ id: job.id, text: "应用技能包已开始下载", tone: "ok" });
    } catch (err) {
      setNotice({ id: job.id, text: `导出失败：${String(err)}`, tone: "error" });
    }
  };

  // Map jobs to application skills, plus any standalone app-* skills in skill store
  // 1. Filter jobs: only keep jobs that have actually been published to the platform
  // and deduplicate by slug (latest job per slug).
  const publishedJobsBySlug = new Map<string, AppDeliveryJob>();
  for (const j of jobs) {
    if (!j.published) continue; // Skip un-published drafts, failed analyses, local in-review tasks
    if (!publishedJobsBySlug.has(j.slug)) {
      publishedJobsBySlug.set(j.slug, j);
    }
  }

  // 2. Identify installed app skills from coworker skill store
  const installedAppSkills = skills.filter((s) => s.name.startsWith("app-"));

  const appSkillRows: {
    key: string;
    job?: AppDeliveryJob;
    skill?: SkillRow;
    name: string;
    slug: string;
    description: string;
    appUrl: string;
    apis: { id: string; purpose?: string; method: string; path: string; effect: string }[];
  }[] = [];

  const seenSlugs = new Set<string>();

  // A. All installed application skills in coworker
  for (const s of installedAppSkills) {
    const slug = s.name.replace(/^app-/, "");
    const job = publishedJobsBySlug.get(slug);
    seenSlugs.add(slug);
    appSkillRows.push({
      key: `skill:${s.name}`,
      job,
      skill: s,
      name: job?.name || s.name,
      slug,
      description: s.description || job?.description || "由应用中心自动化改造生成的应用业务技能",
      appUrl: job?.app_url || "",
      apis: job?.proposal?.apis || [],
    });
  }

  // B. Published & deployed jobs on the platform that are running or have verified artifacts
  for (const [slug, job] of publishedJobsBySlug) {
    if (seenSlugs.has(slug)) continue;
    // Only surface published jobs if they have deployed artifacts or are running
    if (!job.artifact_hash && job.status !== "running") continue;
    seenSlugs.add(slug);
    const skillName = `app-${slug}`;
    const matchedSkill = skills.find((s) => s.name === skillName || s.name === slug);
    appSkillRows.push({
      key: `job:${job.id}`,
      job,
      skill: matchedSkill,
      name: job.name || skillName,
      slug,
      description: job.description || matchedSkill?.description || "由应用中心自动化改造生成的应用业务技能",
      appUrl: job.app_url || "",
      apis: job.proposal?.apis || [],
    });
  }

  const filtered = appSkillRows.filter((item) => {
    if (!q) return true;
    const term = q.toLowerCase();
    return (
      item.name.toLowerCase().includes(term) ||
      item.slug.toLowerCase().includes(term) ||
      item.description.toLowerCase().includes(term)
    );
  });

  return (
    <div className="flex flex-col gap-3 p-4" data-testid="app-skills-panel">
      <div className="flex items-center gap-2">
        <input
          className="w-full min-w-0 px-3 py-2 rounded-lg border border-line bg-paper text-ui text-ink outline-none focus:border-accent"
          placeholder="搜索应用技能名称、slug 或描述"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="搜索应用技能"
        />
        <button
          className="text-ui px-3 py-2 rounded-lg border border-line bg-paper hover:border-lineStrong shrink-0"
          onClick={() => void loadData()}
        >
          {t("common.refresh", "刷新")}
        </button>
      </div>

      {loading && <div className="text-ui text-muted">{t("common.loading", "加载中…")}</div>}

      {error && (
        <div className="text-ui text-danger" role="alert">
          {error}
          <button className="ml-2 underline" onClick={() => void loadData()}>
            {t("common.retry", "重试")}
          </button>
        </div>
      )}

      {!loading && !error && filtered.length === 0 && (
        <div className="rounded-xl2 border border-line bg-panel p-8 text-center flex flex-col items-center gap-3">
          <div className="w-12 h-12 rounded-full bg-faint/20 flex items-center justify-center text-muted">
            <Icon name="workList" size={24} />
          </div>
          <div className="text-ui font-semibold text-ink">暂无可用的应用技能</div>
          <p className="text-ui text-muted max-w-md">
            在应用中心完成应用源码改造、测试与容器化部署后，系统将自动生成并启用专属业务应用技能，同事即可在会话中直接调用其真实接口。
          </p>
          {onNavigateToApps && (
            <button
              className="mt-2 text-ui px-4 py-2 rounded-lg bg-accent text-white hover:opacity-90 transition-opacity font-medium"
              onClick={onNavigateToApps}
            >
              前往应用中心
            </button>
          )}
        </div>
      )}

      {!loading && !error && filtered.length > 0 && (
        <ul className="flex flex-col gap-3">
          {filtered.map((item) => {
            const skillName = item.skill?.name || `app-${item.slug}`;
            const isInstalled = Boolean(item.skill);
            const isEnabled = item.skill?.enabled ?? false;
            const isBusy = acting[skillName] || (item.job ? acting[item.job.id] : false);
            const itemNotice =
              notice && (notice.id === skillName || (item.job && notice.id === item.job.id))
                ? notice
                : null;
            const isExpanded = expanded.has(item.key);

            return (
              <li
                key={item.key}
                className="rounded-xl2 border border-line bg-panel p-4 flex flex-col gap-3"
                data-testid={`app-skill-row-${item.slug}`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-ui font-semibold text-ink">{item.name}</span>
                      <span className="text-label px-2 py-0.5 rounded-full border border-line bg-paper text-muted font-mono">
                        app-{item.slug}
                      </span>
                      {isEnabled ? (
                        <span className="text-label px-2 py-0.5 rounded-full bg-accent/15 text-accent font-medium">
                          已启用
                        </span>
                      ) : isInstalled ? (
                        <span className="text-label px-2 py-0.5 rounded-full bg-faint/40 text-muted">
                          已停用
                        </span>
                      ) : (
                        <span className="text-label px-2 py-0.5 rounded-full bg-warnInk/15 text-warnInk font-medium">
                          待验证启用
                        </span>
                      )}
                      {item.appUrl && (
                        <span className="text-label text-muted truncate max-w-xs font-mono" title={item.appUrl}>
                          {item.appUrl}
                        </span>
                      )}
                    </div>
                    <p className="text-ui text-muted mt-1 leading-snug">{item.description}</p>
                    {item.apis.length > 0 && (
                      <div className="text-label text-muted mt-1.5 flex items-center gap-2">
                        <span>包含 {item.apis.length} 个业务操作接口</span>
                        <button
                          className="underline hover:text-ink text-label"
                          onClick={() => toggleExpand(item.key)}
                        >
                          {isExpanded ? "收起接口" : "查看接口"}
                        </button>
                      </div>
                    )}
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    {item.job?.artifacts?.["skill.zip"] && (
                      <button
                        className="text-ui px-3 py-1.5 rounded-lg border border-line bg-paper hover:border-lineStrong text-ink flex items-center gap-1.5 text-xs"
                        onClick={() => item.job && void handleExport(item.job)}
                        title="导出技能压缩包"
                      >
                        <Icon name="archive" size={14} />
                        <span>导出产物</span>
                      </button>
                    )}

                    {isInstalled ? (
                      <button
                        className={
                          "text-ui px-3.5 py-1.5 rounded-lg border text-xs font-medium transition-colors disabled:opacity-40 " +
                          (isEnabled
                            ? "border-line bg-paper text-danger hover:border-danger"
                            : "border-accent bg-accent text-white hover:opacity-90")
                        }
                        disabled={isBusy}
                        onClick={() => void handleToggleSkill(skillName, isEnabled)}
                      >
                        {isBusy ? "处理中…" : isEnabled ? "停用技能" : "启用技能"}
                      </button>
                    ) : item.job ? (
                      <button
                        className="text-ui px-3.5 py-1.5 rounded-lg bg-accent text-white text-xs font-medium hover:opacity-90 disabled:opacity-40"
                        disabled={isBusy}
                        onClick={() => item.job && void handleVerifyAndEnable(item.job)}
                      >
                        {isBusy ? "校验中…" : "验证并启用技能"}
                      </button>
                    ) : null}
                  </div>
                </div>

                {itemNotice && (
                  <div
                    className={
                      "text-xs px-3 py-1.5 rounded-lg " +
                      (itemNotice.tone === "ok"
                        ? "bg-accent/10 text-accent border border-accent/20"
                        : "bg-danger/10 text-danger border border-danger/20")
                    }
                  >
                    {itemNotice.text}
                  </div>
                )}

                {isExpanded && item.apis.length > 0 && (
                  <div className="rounded-lg border border-line bg-paper/60 p-3 mt-1 space-y-2 text-xs font-mono">
                    <div className="font-semibold text-ink text-ui pb-1 border-b border-line">
                      可用业务接口清单
                    </div>
                    {item.apis.map((api) => (
                      <div key={api.id} className="flex items-baseline gap-2 py-0.5">
                        <span
                          className={
                            "px-1.5 py-0.5 rounded text-[10px] font-bold shrink-0 " +
                            (api.method === "GET"
                              ? "bg-accent/20 text-accent"
                              : "bg-warnInk/20 text-warnInk")
                          }
                        >
                          {api.method}
                        </span>
                        <span className="font-semibold text-ink shrink-0">{api.path}</span>
                        {api.purpose && <span className="text-muted font-sans truncate">{api.purpose}</span>}
                      </div>
                    ))}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export default AppSkillsPanel;
