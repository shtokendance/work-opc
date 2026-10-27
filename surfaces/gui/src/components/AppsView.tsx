import { useEffect, useRef, useState } from "react";
import {
  checkAppSlug,
  appDeliveryRequest,
  type AppDeliveryJob,
  getDeployedApp,
  getSettings,
  listDeployedApps,
  listOnlineResources,
  publishAppToPlatform,
  transformApp,
  updateDeployedAppConfig,
  type AppConfigItem,
  type DeployedApp,
  type ResourceSummary,
} from "../api";
import { Icon } from "./Icon";
import { AppDeliveryConfig, configName } from "./AppDeliveryConfig";
import { AppDeliveryReview } from "./AppDeliveryReview";
import { AppDeliveryArtifacts } from "./AppDeliveryArtifacts";
import { chooseFolder } from "../tauri";

interface Props {
  onBack?: () => void;
}

export function AppsView({ onBack }: Props) {
  const [apps, setApps] = useState<DeployedApp[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // New App Wizard Modal state
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
  const wizardBody = useRef<HTMLDivElement>(null);
  useEffect(() => { if (wizardBody.current) wizardBody.current.scrollTop = 0; }, [step, isModalOpen]);

  // Wizard Step 1 form fields
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [sourcePath, setSourcePath] = useState("");
  const [selectedModel, setSelectedModel] = useState("default");
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [appUrl, setAppUrl] = useState("");
  const [containerPort, setContainerPort] = useState(8080);
  const [slugError, setSlugError] = useState<string | null>(null);
  const [redeploySlug, setRedeploySlug] = useState("");

  // Wizard Step 2 & 3 state
  const [transforming, setTransforming] = useState(false);
  const [jobId, setJobId] = useState("");
  const [deliveryJob, setDeliveryJob] = useState<AppDeliveryJob | null>(null);
  const [finalConfirmed, setFinalConfirmed] = useState(false);
  const readyArtifact = useRef("");
  const [transformReport, setTransformReport] = useState<any | null>(null);
  const [onlineResources, setOnlineResources] = useState<ResourceSummary[]>([]);
  const [selectedResourceIds, setSelectedResourceIds] = useState<string[]>([]);
  const [configs, setConfigs] = useState<AppConfigItem[]>([]);
  const [lastDraft, setLastDraft] = useState<AppDeliveryJob | null>(null);

  // Wizard Step 4 state
  const [deploying, setDeploying] = useState(false);
  const [deployStatus, setDeployStatus] = useState<string>("building");
  const [deploymentReachedRunning, setDeploymentReachedRunning] = useState(false);
  const [buildLogs, setBuildLogs] = useState<string>("");
  const [pollTimer, setPollTimer] = useState<any>(null);
  const logContainerRef = useRef<HTMLPreElement>(null);

  // Drawer detail state
  const [selectedApp, setSelectedApp] = useState<DeployedApp | null>(null);
  const [drawerConfigs, setDrawerConfigs] = useState<AppConfigItem[]>([]);
  const [drawerResourceIds, setDrawerResourceIds] = useState<string[]>([]);
  const [savingConfig, setSavingConfig] = useState(false);
  const [drawerTab, setDrawerTab] = useState<"configs" | "artifacts" | "logs">("configs");
  const [copyFeedback, setCopyFeedback] = useState<string | null>(null);
  const [showSecrets, setShowSecrets] = useState<{ [key: string]: boolean }>({});

  const toggleShowSecret = (key: string) => {
    setShowSecrets((prev) => ({ ...prev, [key]: !(prev[key] ?? true) }));
  };

  const copyToClipboard = (text: string, label: string) => {
    if (!text) return;
    navigator.clipboard.writeText(text);
    setCopyFeedback(label);
    setTimeout(() => setCopyFeedback(null), 2000);
  };

  const refreshApps = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listDeployedApps();
      setApps(data.apps || []);
      const drafts = await appDeliveryRequest<{ jobs?: AppDeliveryJob[] }>("jobs").catch(() => ({ jobs: [] }));
      const seen = new Set<string>();
      const latest = drafts.jobs?.filter(job => {
        if (seen.has(job.slug)) return false;
        seen.add(job.slug);
        return true;
      });
      setLastDraft(latest?.find(job => ["review", "failed", "analyzing", "ready"].includes(job.status) && !job.published) || null);
    } catch (err: any) {
      setError(err?.message || "Failed to load deployed apps");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refreshApps();
    listOnlineResources().then((res) => {
      setOnlineResources(res.resources || []);
    }).catch(() => {});

    getSettings().then((s) => {
      if (s?.models && s.models.length > 0) {
        setAvailableModels(s.models);
        if (s.model && s.models.includes(s.model)) {
          setSelectedModel(s.model);
        } else {
          setSelectedModel(s.models[0]);
        }
      }
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [buildLogs]);

  // Step 1: Validate and Check Slug
  const handleProceedToStep2 = async () => {
    if (!name.trim() || !slug.trim() || (!selectedFile && !sourcePath.trim())) {
      alert("请填写应用名称、唯一标识 slug 并选择源码文件夹或 ZIP 包");
      return;
    }
    setSlugError(null);
    try {
      const slugRes = await checkAppSlug(slug.trim());
      if (!slugRes.available && redeploySlug !== slug.trim()) {
        setSlugError(slugRes.message || "该 slug 已被占用，请更换唯一标识");
        return;
      }
      setAppUrl(slugRes.app_url);
      setStep(2);
      startTransform(slugRes.app_url);
    } catch (err: any) {
      setSlugError(err?.message || "校验 slug 失败");
    }
  };

  // Start a read-only real Agent; source changes require the separate approval endpoint.
  const startTransform = async (targetAppUrl: string) => {
    if (!selectedFile && !sourcePath.trim()) return;
    setTransforming(true); setTransformReport(null); setDeliveryJob(null); setJobId("");
    readyArtifact.current = ""; setConfigs([]); setSelectedResourceIds([]); setFinalConfirmed(false);
    try {
      const data_b64 = selectedFile ? await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
        reader.onerror = reject;
        reader.readAsDataURL(selectedFile);
      }) : undefined;
      const result = await transformApp({ slug: slug.trim(), name: name.trim(), description: description.trim(), app_url: targetAppUrl, container_port: containerPort, model: selectedModel, data_b64, source_path: selectedFile ? undefined : sourcePath.trim(), resources: onlineResources });
      if (!result.ok || !result.job_id) throw new Error(result.error || "无法启动 AI 分析");
      setJobId(result.job_id);
    } catch (e) {
      setError(String(e)); setStep(1);
    } finally { setTransforming(false); }
  };

  const deliveryReady = (job: AppDeliveryJob) => {
    setDeliveryJob(job);
    if (readyArtifact.current === job.artifact_hash) return;
    readyArtifact.current = job.artifact_hash || "";
    setTransformReport({ summary: job.proposal?.summary });
    setConfigs((job.runtime_configs || job.manifest?.configs || []).map(c => ({ ...c, secret: !!job.proposal?.configs.find(p => p.key === c.key)?.secret, value_source: c.value_source || "manual" })));
    setSelectedResourceIds([]);
    if (job.manifest?.container_port) setContainerPort(job.manifest.container_port);
  };

  useEffect(() => { setFinalConfirmed(false); }, [configs, selectedResourceIds, jobId]);

  const verifyDeployedSkill = async (logs: string) => {
    setDeploying(true);
    setDeployStatus("verifying");
    try {
      await appDeliveryRequest("enable-skill", { slug: slug.trim(), job_id: jobId });
      setDeployStatus("running");
      setBuildLogs(logs + "\n业务验证通过，应用技能已启用。");
    } catch (e) {
      setDeployStatus("failed");
      setBuildLogs(logs + "\n应用已启动，但业务验证或技能启用失败：" + String(e));
    } finally {
      setDeploying(false);
      refreshApps();
    }
  };

  // Step 3: Confirm and Upload
  const handlePublish = async () => {
    if (!jobId || !deliveryJob?.artifact_hash || !finalConfirmed) {
      setError("请核对最终修改、配置与资源，并勾选部署确认");
      return;
    }
    setDeploying(true);
    setStep(4);
    setDeployStatus("building");
    setDeploymentReachedRunning(false);
    setBuildLogs("正在预装本地技能并将应用包上传至平台...");

    try {
      const confirmation = await appDeliveryRequest<{ confirmation_token: string }>(`jobs/${jobId}/confirm-deployment`, {
        artifact_hash: deliveryJob.artifact_hash, app_config: configs, resource_ids: selectedResourceIds,
      });
      const pubRes = await publishAppToPlatform({
        job_id: jobId,
        confirmation_token: confirmation.confirmation_token,
        slug: slug.trim(),
        app_config: configs,
        resource_ids: selectedResourceIds,
      });

      if (!pubRes.ok) {
        setDeployStatus("failed");
        setBuildLogs(`上传失败: ${pubRes.error}`);
        setDeploying(false);
        return;
      }

      // Start polling status
      const timer = setInterval(async () => {
        try {
          const appData = await getDeployedApp(slug.trim());
          if (appData) {
            setDeployStatus(appData.status);
            setBuildLogs(appData.build_log || appData.error_message || "");

            if (appData.status === "running") {
              clearInterval(timer);
              setDeploymentReachedRunning(true);
              await verifyDeployedSkill(appData.build_log || "");
            } else if (appData.status === "failed") {
              clearInterval(timer);
              setDeploying(false);
              refreshApps();
            }
          }
        } catch {
          // ignore transient poll error
        }
      }, 2000);
      setPollTimer(timer);
    } catch (err: any) {
      setDeployStatus("failed");
      setBuildLogs(`发布异常: ${err?.message}`);
      setDeploying(false);
    }
  };

  const handleFixDeploy = async (app: DeployedApp) => {
    const previous = await appDeliveryRequest<{ jobs: AppDeliveryJob[] }>(`jobs?slug=${encodeURIComponent(app.slug)}`).catch(() => ({ jobs: [] }));
    const candidate = previous.jobs.find(job => job.artifact_hash);
    if (!candidate) {
      closeWizard(); setRedeploySlug(app.slug); setName(app.name); setSlug(app.slug); setError("本机缺少此应用的交付记录，请选择原始源码重新分析"); setIsModalOpen(true); return;
    }
    readyArtifact.current = candidate.artifact_hash || ""; setJobId(candidate.id); setDeliveryJob(candidate); setTransformReport({ summary: candidate.proposal?.summary }); setFinalConfirmed(false);
    setName(app.name);
    setRedeploySlug(app.slug);
    setSlug(app.slug);
    setDescription(app.description || "");
    setAppUrl(app.app_url);
    setContainerPort(app.container_port || 8080);
    const previousConfigs = [...(app.app_config || [])];
    for (const resource of onlineResources.filter(r => (app.resource_ids || []).includes(r.id))) {
      for (const key of resource.env_keys) {
        if (!previousConfigs.some(c => c.key === key)) previousConfigs.push({ key, value: "", resource_id: resource.id, value_source: "resource" });
      }
    }
    setConfigs(previousConfigs.map(c => {
      const resource = !c.value && onlineResources.find(r => (app.resource_ids || []).includes(r.id) && r.env_keys.includes(c.key));
      return c.resource_id ? c : resource ? { ...c, resource_id: resource.id, value_source: "resource" } : { ...c, value_source: "manual" };
    }));
    setSelectedResourceIds(app.resource_ids || []);
    setDeployStatus("building");
    setBuildLogs(app.build_log || app.error_message || "");
    setStep(3);
    setIsModalOpen(true);
  };

  const closeWizard = () => {
    if (jobId && !deliveryJob?.artifact_hash) void appDeliveryRequest(`jobs/${jobId}/cancel`, {}).catch(() => {});
    if (pollTimer) clearInterval(pollTimer);
    setIsModalOpen(false);
    setStep(1);
    setName("");
    setSlug("");
    setRedeploySlug("");
    setDescription("");
    setSelectedFile(null);
    setSourcePath("");
    setJobId(""); setDeliveryJob(null); setFinalConfirmed(false);
    readyArtifact.current = "";
    setConfigs([]);
    setSelectedResourceIds([]);
    setBuildLogs("");
    setTransformReport(null);
    refreshApps();
  };

  // Drawer handlers
  const openDrawer = (app: DeployedApp, initialTab: "configs" | "artifacts" | "logs" = "configs") => {
    setSelectedApp(app);
    setDrawerConfigs((app.app_config || []).map(c => ({ ...c })));
    setDrawerResourceIds(app.resource_ids || []);
    setDrawerTab(app.status === "failed" && initialTab === "configs" ? "logs" : initialTab);
  };

  const resumeDraft = (job: AppDeliveryJob) => {
    setName(job.name); setSlug(job.slug); setDescription(job.description || "");
    setContainerPort(job.port || 8080); setAppUrl(job.app_url); setSelectedModel(job.model);
    setJobId(job.id); setDeliveryJob(null); setTransformReport(null); readyArtifact.current = "";
    setFinalConfirmed(false); setConfigs([]); setSelectedResourceIds([]);
    setStep(2); setIsModalOpen(true);
  };

  const saveDrawerConfig = async () => {
    if (!selectedApp) return;
    setSavingConfig(true);
    try {
      await updateDeployedAppConfig(selectedApp.slug, {
        container_port: selectedApp.container_port,
        app_config: drawerConfigs,
        resource_ids: drawerConfigs.length ? drawerResourceIds : [],
      });
      alert("配置已更新，容器已秒级重启！");
      refreshApps();
      const updated = await getDeployedApp(selectedApp.slug);
      setSelectedApp(updated);
      setDrawerConfigs((updated.app_config || []).map(c => ({ ...c })));
    } catch (err: any) {
      alert(`保存失败: ${err?.message}`);
    } finally {
      setSavingConfig(false);
    }
  };

  const missingConfigs = configs.filter(c => c.required && !c.value && !c.resource_id).map(configName);
  const changeConfigs = (items: AppConfigItem[]) => {
    setConfigs(items);
    setSelectedResourceIds([...new Set(items.map(item => item.resource_id).filter((id): id is string => !!id))]);
  };
  const plan = deliveryJob?.proposal;
  const hasBusinessProbe = plan?.verification_plan?.cases?.some(c => plan.apis.some(api => api.id === c.operation && api.effect === "read" && api.path !== plan.health_path));

  return (
    <div className="flex-1 flex flex-col h-full bg-canvas overflow-hidden select-none">
      {/* Top Header Bar */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-line bg-panel">
        <div className="flex items-center gap-3">
          {onBack && (
            <button
              onClick={onBack}
              className="p-1.5 rounded-lg hover:bg-chromeHover text-muted hover:text-ink transition-colors"
              title="返回"
            >
              <Icon name="arrowLeft" size={16} />
            </button>
          )}
          <div>
            <h1 className="text-lg font-semibold text-ink">应用中心</h1>
            <p className="text-xs text-muted">
              分析源码、确认改造与配置，部署到内网并生成可调用的应用技能
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={refreshApps}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-line text-xs font-medium text-muted hover:text-ink hover:bg-chromeHover transition-colors"
          >
            <Icon name="refresh" size={13} className={loading ? "animate-spin" : ""} />
            刷新
          </button>
          <button
            onClick={() => {
              setStep(1);
              setIsModalOpen(true);
            }}
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-accent hover:opacity-90 text-white text-xs font-medium shadow-xs transition-colors"
          >
            <Icon name="plus" size={13} />
            新建应用
          </button>
        </div>
      </div>

      {/* Main Content Area */}
      <div className="flex-1 overflow-y-auto p-6">
        {error && (
          <div className="mb-4 p-3 rounded-lg bg-danger/10 text-danger text-xs border border-danger/25">
            {error}
          </div>
        )}

        {apps.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center h-80 text-center border-2 border-dashed border-line rounded-xl2 p-8 bg-panel">
            <div className="w-12 h-12 rounded-full bg-accentSoft text-accent flex items-center justify-center mb-3">
              <Icon name="workList" size={24} />
            </div>
            <h3 className="text-ui font-semibold text-ink mb-1">暂无部署应用</h3>
            <p className="text-meta text-muted max-w-sm mb-4">
              点击右上角「新建应用」，选择单应用源码文件夹或 ZIP，由 AI 分析部署差距。确认修改与配置后，生成部署包和业务技能。
            </p>
            <button
              onClick={() => setIsModalOpen(true)}
              className="px-4 py-2 rounded-lg bg-accent hover:opacity-90 text-white text-xs font-medium"
            >
              新建第一个应用
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {apps.map((app) => (
              <div
                key={app.id}
                onClick={() => openDrawer(app)}
                className="group rounded-xl2 border border-line bg-panel p-5 hover:border-accent/40 hover:shadow-md transition-all duration-200 cursor-pointer flex flex-col justify-between relative overflow-hidden"
              >
                <div>
                  {/* Header: Icon + Title/Slug + Status Tag */}
                  <div className="flex items-start justify-between gap-3 mb-3">
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="w-10 h-10 rounded-xl bg-accentSoft/70 border border-accent/20 flex items-center justify-center text-accent shrink-0 shadow-2xs group-hover:scale-105 transition-transform">
                        <Icon name="workList" size={20} />
                      </div>
                      <div className="min-w-0">
                        <h3 className="font-semibold text-ui text-ink group-hover:text-accent transition-colors truncate">
                          {app.name}
                        </h3>
                        <p className="text-label text-muted font-mono truncate">
                          {app.slug}
                        </p>
                      </div>
                    </div>

                    {/* Status Badge (标签) */}
                    <div className="shrink-0">
                      {app.status === "running" ? (
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-label font-medium bg-okSoft text-ok border border-okLine shadow-2xs">
                          <span className="relative flex h-2 w-2">
                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-ok opacity-60"></span>
                            <span className="relative inline-flex rounded-full h-2 w-2 bg-ok"></span>
                          </span>
                          运行中
                        </span>
                      ) : app.status === "failed" ? (
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-label font-medium bg-danger/10 text-danger border border-danger/25">
                          <span className="w-2 h-2 rounded-full bg-danger"></span>
                          部署失败
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-label font-medium bg-warnSoft text-warnInk border border-warnInk/25 animate-pulse">
                          <span className="w-2 h-2 rounded-full bg-warnInk"></span>
                          构建中
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Description */}
                  <p className="text-meta text-muted line-clamp-2 mb-4 leading-relaxed min-h-[36px]">
                    {app.description || "暂无应用描述"}
                  </p>
                </div>

                <div>
                  {/* Meta Details: URL + Skill */}
                  <div className="space-y-2 pt-3 border-t border-line/60">
                    <div className="flex items-center justify-between gap-2 px-3 py-2 rounded-lg bg-paper border border-line text-meta">
                      <span className="text-muted flex items-center gap-1.5 shrink-0">
                        <Icon name="cloud" size={13} className="text-muted" />
                        内网地址
                      </span>
                      <a
                        href={app.app_url}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="text-accent hover:underline font-mono text-label truncate max-w-[190px]"
                        title={app.app_url}
                      >
                        {app.app_url}
                      </a>
                    </div>

                    <div className="flex items-center justify-between gap-2 px-3 py-2 rounded-lg bg-paper border border-line text-meta">
                      <span className="text-muted flex items-center gap-1.5 shrink-0">
                        <Icon name="sparkle" size={13} className="text-accent" />
                        本地技能
                      </span>
                      <span className="font-mono text-label text-ink px-1.5 py-0.5 rounded bg-panel border border-line truncate max-w-[190px]">
                        {app.skill_name || `app-${app.slug}`}
                      </span>
                    </div>
                  </div>

                  {/* Card Footer: hover cue & actions */}
                  <div className="mt-3.5 pt-2.5 border-t border-line/40 flex items-center justify-between text-label text-muted">
                    {app.status === "failed" ? (
                      <div className="flex items-center gap-2 w-full justify-between" onClick={(e) => e.stopPropagation()}>
                        <button
                          onClick={() => openDrawer(app, "logs")}
                          className="px-2.5 py-1 rounded-lg border border-line bg-paper hover:bg-chromeHover text-muted hover:text-ink text-label font-medium transition-colors flex items-center gap-1"
                        >
                          <Icon name="file" size={11} />
                          失败日志
                        </button>
                        <button
                          onClick={() => handleFixDeploy(app)}
                          className="px-2.5 py-1 rounded-lg bg-danger hover:opacity-90 text-white text-label font-medium transition-opacity flex items-center gap-1"
                        >
                          <Icon name="refresh" size={11} />
                          向导修复部署
                        </button>
                      </div>
                    ) : (
                      <>
                        <span>产物路径、配置与日志</span>
                        <span className="flex items-center gap-0.5 text-accent font-medium group-hover:translate-x-0.5 transition-transform">
                          详情 <Icon name="chevronRight" size={12} />
                        </span>
                      </>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {lastDraft && !isModalOpen && <div className="mx-6 mb-4 p-3 rounded-xl border border-line bg-paper flex items-center justify-between gap-3"><span className="text-ui">还有一份可继续的应用改造：{lastDraft.name} <code className="text-xs text-muted">{lastDraft.slug}</code></span><button className="shrink-0 text-ui text-accent" onClick={() => resumeDraft(lastDraft)}>继续上次分析</button></div>}
      {/* 4-Step New App Modal */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-panel w-full max-w-3xl rounded-xl2 shadow-2xl border border-line flex flex-col max-h-[90vh] overflow-hidden animate-in fade-in zoom-in-95 duration-150">
            {/* Modal Header & Stepper */}
            <div className="px-6 py-4 border-b border-line bg-paper/50 shrink-0">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2.5">
                  <div className="w-7 h-7 rounded-lg bg-accentSoft text-accent flex items-center justify-center font-bold">
                    <Icon name="plus" size={15} />
                  </div>
                  <div>
                    <h2 className="text-ui font-semibold text-ink">新建并部署应用</h2>
                    <p className="text-meta text-muted">
                      选择源码，审阅 AI 的修改建议，确认后生成部署包和业务技能
                    </p>
                  </div>
                </div>
                <button
                  onClick={closeWizard}
                  className="w-7 h-7 rounded-lg flex items-center justify-center text-muted hover:text-ink hover:bg-paper transition-colors"
                  title="关闭"
                >
                  <Icon name="x" size={16} />
                </button>
              </div>

              {/* Step indicator tabs */}
              <div className="grid grid-cols-4 gap-2 pt-2">
                {[
                  { s: 1, title: "1. 源码信息" },
                  { s: 2, title: "2. AI 改造" },
                  { s: 3, title: "3. 配置资源" },
                  { s: 4, title: "4. 部署上线" },
                ].map(({ s, title }) => (
                  <div
                    key={s}
                    className={`py-1.5 px-2 rounded-lg text-label text-center font-medium border transition-all ${
                      step === s
                        ? "bg-accentSoft text-accent border-accent/40 shadow-2xs font-semibold"
                        : step > s
                        ? "bg-okSoft text-ok border-okLine"
                        : "bg-paper/40 text-faint border-line"
                    }`}
                  >
                    {step > s ? `✓ ${title.slice(3)}` : title}
                  </div>
                ))}
              </div>
            </div>

            {/* Modal Body */}
            <div ref={wizardBody} className="flex-1 overflow-y-auto p-6 space-y-4">
              {redeploySlug && redeploySlug === slug.trim() && <p className="border-l-2 border-warning pl-3 text-ui">正在重新发布现有应用 {redeploySlug}。重新分析、审批和上传后会替换它的部署；配置与资源仍需再次核对。</p>}
              {/* STEP 1 */}
              {step === 1 && (
                <div className="space-y-4 text-ui">
                  <div>
                    <label className="block text-meta font-medium text-ink mb-1.5">
                      应用名称 <span className="text-danger">*</span>
                    </label>
                    <input
                      type="text"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      placeholder="例如：报销审批助手 / 阿里云效流水线"
                      className="w-full px-3.5 py-2 rounded-xl border border-line bg-paper text-ink placeholder:text-muted/60 focus:outline-accent focus:border-accent"
                    />
                  </div>

                  <div>
                    <label className="block text-meta font-medium text-ink mb-1.5">
                      唯一标识 (slug) <span className="text-danger">*</span>
                    </label>
                    <input
                      type="text"
                      value={slug}
                      onChange={(e) => setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))}
                      placeholder="例如：devops-aly（仅限小写字母、数字和中划线）"
                      className="w-full px-3.5 py-2 rounded-xl border border-line bg-paper text-ink font-mono placeholder:text-muted/60 focus:outline-accent focus:border-accent"
                    />
                    {slugError && <p className="text-label text-danger mt-1">{slugError}</p>}
                  </div>

                  <div>
                    <label className="block text-meta font-medium text-ink mb-1.5">应用描述</label>
                    <textarea
                      rows={2}
                      value={description}
                      onChange={(e) => setDescription(e.target.value)}
                      placeholder="简述该应用功能，将自动用于 AI 技能描述与调用匹配"
                      className="w-full px-3.5 py-2 rounded-xl border border-line bg-paper text-ink placeholder:text-muted/60 focus:outline-accent focus:border-accent"
                    />
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] gap-4">
                    <div>
                      <label className="block text-meta font-medium text-ink mb-1.5">容器监听端口</label>
                      <input
                        type="number"
                        value={containerPort}
                        onChange={(e) => setContainerPort(Number(e.target.value))}
                        className="w-full px-3.5 py-2 rounded-xl border border-line bg-paper text-ink font-mono focus:outline-accent focus:border-accent"
                      />
                    </div>
                    <div>
                      <label htmlFor="app-adapter-model" className="block text-meta font-medium text-ink mb-1.5">AI 改造模型</label>
                      <div className="relative">
                      <select
                        id="app-adapter-model"
                        value={selectedModel}
                        title={selectedModel}
                        onChange={(e) => setSelectedModel(e.target.value)}
                        className="block appearance-none w-full min-w-0 min-h-[40px] pl-3.5 pr-9 py-2 rounded-xl border border-line bg-paper text-ink text-ui focus:outline-accent focus:border-accent cursor-pointer"
                      >
                        {availableModels.length > 0 ? (
                          availableModels.map((m) => (
                            <option key={m} value={m}>
                              {m}
                            </option>
                          ))
                        ) : (
                          <option value="default">当前接入的默认智能体</option>
                        )}
                      </select>
                      <Icon name="chevronDown" size={14} className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none text-muted" />
                      </div>
                    </div>
                  </div>

                  <div>
                    <label className="block text-meta font-medium text-ink mb-1.5">
                      选择应用源码（文件夹或 ZIP） <span className="text-danger">*</span>
                    </label>
                    <button type="button" className="mb-3 px-4 py-2 rounded-lg border border-line hover:bg-paper text-ui" onClick={async () => { const path = await chooseFolder(); if (path) { setSourcePath(path); setSelectedFile(null); } }}>选择源码文件夹</button>
                    {sourcePath && <p className="mb-3 text-xs text-muted break-all">已选择：{sourcePath}。分析时会创建副本，不改动原文件夹。</p>}
                    <div className="relative">
                      <input
                        type="file"
                        id="app-zip-file-input"
                        accept=".zip"
                        onChange={(e) => { setSelectedFile(e.target.files?.[0] || null); setSourcePath(""); }}
                        className="sr-only"
                      />
                      {!selectedFile ? (
                        <label
                          htmlFor="app-zip-file-input"
                          className="flex flex-col items-center justify-center p-6 border-2 border-dashed border-line hover:border-accent/60 bg-paper/60 hover:bg-paper rounded-xl2 cursor-pointer transition-all group text-center"
                        >
                          <div className="w-10 h-10 rounded-xl bg-panel border border-line flex items-center justify-center text-muted group-hover:text-accent group-hover:scale-105 transition-all mb-2">
                            <Icon name="folderPlus" size={20} />
                          </div>
                          <span className="text-ui font-medium text-ink group-hover:text-accent">
                            点击选择或拖拽源码压缩包 (.zip)
                          </span>
                          <span className="text-meta text-muted mt-1">
                            支持 Go、Python、Node.js、Java、容器化 C# 等单应用；部署前检查实际依赖与构建条件
                          </span>
                        </label>
                      ) : (
                        <div className="flex items-center justify-between p-3.5 rounded-xl border border-okLine bg-okSoft/30 text-ink">
                          <div className="flex items-center gap-3 min-w-0">
                            <div className="w-9 h-9 rounded-lg bg-okSoft text-ok flex items-center justify-center shrink-0">
                              <Icon name="file" size={18} />
                            </div>
                            <div className="min-w-0">
                              <div className="font-semibold text-ui truncate">{selectedFile.name}</div>
                              <div className="text-meta text-muted">
                                {(selectedFile.size / 1024).toFixed(1)} KB · 已准备就绪
                              </div>
                            </div>
                          </div>
                          <label
                            htmlFor="app-zip-file-input"
                            className="px-3 py-1.5 rounded-lg border border-line bg-panel hover:bg-paper text-label font-medium text-ink cursor-pointer shrink-0 transition-colors"
                          >
                            更换文件
                          </label>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {step === 1 && <div className="border-t border-line pt-3"><label className="text-ui font-medium block mb-2" htmlFor="app-source-path">或输入源码文件夹 / ZIP 的完整路径</label><input id="app-source-path" className="w-full rounded-md border border-line bg-panel px-3 py-2 text-ui font-mono" value={sourcePath} onChange={e => { setSourcePath(e.target.value); setSelectedFile(null); }} placeholder="/path/to/my-app 或 /path/to/application.zip" /></div>}

              {/* STEP 2 */}
              {step === 2 && (jobId
                ? <AppDeliveryReview jobId={jobId} onReady={deliveryReady} onReplaceSource={() => setStep(1)} />
                : <div className="p-6 text-muted">正在安全解包并连接所选 AI…</div>)}

              {step === 3 && (
                <div className="space-y-5">
                  <section className="border-b border-line pb-4 mb-4 space-y-2">
                    <h3 className="font-semibold">确认后，应用就会部署到这里</h3>
                    <p className="text-ui">{name} · 已确认 {deliveryJob?.proposal?.changes.length || 0} 项修改。下面只需要决定配置是否更换。</p>
                    <div className="text-xs font-mono break-all">{appUrl}</div>
                    <details className="text-xs text-muted"><summary className="cursor-pointer">查看交付校验信息</summary><p className="break-all mt-2">{deliveryJob?.artifact_hash}</p></details>
                    {plan?.verification_plan && !hasBusinessProbe && <p className="text-xs text-warnInk">部署后的业务验证仍待补充：当前没有实际业务查询探针。若需自动启用业务技能，请返回改造报告补充只读查询条件和预期结果；健康检查不能代替业务验证。</p>}
                    <details className="text-xs"><summary className="cursor-pointer">查看已确认的 {deliveryJob?.proposal?.changes.length || 0} 项修改</summary>{deliveryJob?.proposal?.changes.map(change => <details key={change.id} className="mt-2"><summary className="cursor-pointer break-words">{change.reason}</summary><p className="text-muted break-all mt-1">{change.path}</p><pre className="whitespace-pre-wrap break-all max-h-48 overflow-auto bg-paper p-2">{change.diff}</pre></details>)}</details>
                  </section>
                  {missingConfigs.length > 0 && <p role="alert" className="text-ui text-danger">还需要填写或选择：{missingConfigs.join("、")}。完成后就可以上传部署。</p>}
                  <AppDeliveryConfig configs={configs} originals={deliveryJob?.runtime_configs || []} resources={onlineResources} onChange={changeConfigs} />
                </div>
              )}

              {/* STEP 4 */}
              {step === 4 && (
                <div className="space-y-4">
                  <div className="flex items-center gap-3 p-3.5 rounded-xl border bg-paper/70 border-line">
                    <span
                      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-label font-medium ${
                        deployStatus === "running"
                          ? "bg-okSoft text-ok border border-okLine"
                          : deployStatus === "failed"
                          ? "bg-danger/10 text-danger border border-danger/25"
                          : "bg-warnSoft text-warnInk border border-warnInk/25 animate-pulse"
                      }`}
                    >
                      <span className={`w-2 h-2 rounded-full ${deployStatus === "running" ? "bg-ok" : deployStatus === "failed" ? "bg-danger" : "bg-warnInk"}`} />
                      {deployStatus === "running" ? "部署成功" : deployStatus === "failed" ? "交付未通过" : deployStatus === "verifying" ? "正在验证业务..." : "正在构建部署..."}
                    </span>
                    <span className="text-meta text-muted">
                      {deployStatus === "running"
                        ? "应用健康与只读业务验证通过，本地技能已启用"
                        : deployStatus === "failed"
                        ? "构建、健康检查或业务验证未通过，请查看下方实际日志"
                        : deployStatus === "verifying" ? "正在核对交付版本并执行只读业务验证…" : "deploy-opc 正在执行内网容器构建与实时编译..."}
                    </span>
                  </div>

                  {deployStatus === "failed" && (
                    <div className="p-4 rounded-xl bg-danger/10 border border-danger/25 text-danger space-y-2.5">
                      <div className="font-semibold text-ui flex items-center gap-1.5">
                        <Icon name="warning" size={16} />
                        {deploymentReachedRunning ? "应用已启动，业务验证未通过，技能仍未启用" : "应用交付未通过：请查看构建、健康与业务验证结果"}
                      </div>
                      <p className="text-meta text-ink leading-relaxed">
                        {deploymentReachedRunning ? "可先仅重试业务验证，无需重新构建。若配置或源码需要调整，再确认修改并重新部署。" : "可能由于代码构建错误、依赖缺失、端口不一致或应用启动异常导致。请参考下方控制台日志定位问题。"}
                      </p>
                      <div className="flex flex-wrap items-center gap-3 pt-1">
                        {deploymentReachedRunning && <button onClick={() => void verifyDeployedSkill(buildLogs)} className="px-3.5 py-1.5 rounded-lg bg-accent text-white text-meta font-medium hover:opacity-90 transition-opacity">仅重试业务验证</button>}
                        <button
                          onClick={() => setStep(3)}
                          className="px-3.5 py-1.5 rounded-lg bg-panel border border-line text-ink text-meta font-medium hover:bg-paper transition-colors flex items-center gap-1.5"
                        >
                          <Icon name="arrowLeft" size={13} />
                          返回第 3 步核对配置
                        </button>
                        <button
                          onClick={handlePublish}
                          className="px-3.5 py-1.5 rounded-lg bg-accent text-white text-meta font-medium hover:opacity-90 transition-opacity flex items-center gap-1.5"
                        >
                          <Icon name="refresh" size={13} />
                          重新触发构建与部署
                        </button>
                        <button
                          onClick={() => { setRedeploySlug(slug.trim()); setSlugError(null); setStep(1); }}
                          className="px-3 py-1.5 rounded-lg border border-line text-muted hover:text-ink text-meta font-medium transition-colors"
                        >
                          重新上传源码
                        </button>
                      </div>
                    </div>
                  )}

                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <label className="text-meta font-medium text-ink">实时构建与部署日志</label>
                      <span className="text-label text-muted font-mono">
                        {deploying ? "实时流式输出中..." : "日志已归档"}
                      </span>
                    </div>
                    <pre
                      ref={logContainerRef}
                      className="p-3.5 rounded-xl bg-neutral-950 text-emerald-400 font-mono text-label h-56 overflow-y-auto whitespace-pre-wrap leading-relaxed selection:bg-emerald-900 selection:text-white"
                    >
                      {buildLogs || "等待输出..."}
                    </pre>
                  </div>

                  {deployStatus === "running" && (
                    <div className="p-4 rounded-xl bg-okSoft/40 border border-okLine text-ink space-y-2">
                      <div className="font-semibold text-ui text-ok flex items-center gap-1.5">
                        🎉 应用已成功上线！
                      </div>
                      <div className="text-meta">
                        内网访问地址:{" "}
                        <a href={appUrl} target="_blank" rel="noreferrer" className="underline font-mono text-accent">
                          {appUrl}
                        </a>
                      </div>
                      <div className="text-label text-muted">
                        提示: 现在可以在任意对话窗口中直接调用 <code>app-{slug}</code> 技能！
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Modal Footer */}
            <div className="flex items-center justify-between px-6 py-4 border-t border-line bg-paper/60 shrink-0">
              <button
                onClick={closeWizard}
                disabled={deploying}
                className="px-4 py-2 rounded-xl border border-line text-ui font-medium text-muted hover:text-ink hover:bg-paper disabled:opacity-40 transition-colors"
              >
                {deploying ? "部署中..." : "取消关闭"}
              </button>

              <div className="flex items-center gap-2.5">
                {step === 1 && (
                  <button
                    onClick={handleProceedToStep2}
                    className="px-5 py-2 rounded-xl bg-accent hover:opacity-90 text-white text-ui font-medium shadow-xs transition-all"
                  >
                    下一步 (AI 改造)
                  </button>
                )}
                {step === 2 && (
                  <>
                    <button
                      onClick={() => setStep(1)}
                      disabled={transforming}
                      className="px-4 py-2 rounded-xl border border-line text-ui font-medium text-muted hover:text-ink hover:bg-paper disabled:opacity-40 transition-colors"
                    >
                      ← 重新选择源码
                    </button>
                    {transformReport ? <button
                      onClick={() => setStep(3)}
                      disabled={transforming || !transformReport}
                      className="px-5 py-2 rounded-xl bg-accent hover:opacity-90 text-white text-ui font-medium shadow-xs disabled:opacity-50 transition-all flex items-center gap-1.5"
                    >
                      {transforming ? (
                        <>
                          <Icon name="refresh" size={14} className="animate-spin" />
                          AI 改造分析中...
                        </>
                      ) : (
                        <>进入配置，核对原值 (下一步) →</>
                      )}
                    </button> : <span className="max-w-xs text-xs text-muted">请先完成提案中的待回答问题、逐项审批并生成交付包</span>}
                  </>
                )}
                {step === 3 && (
                  <>
                    <label className="flex items-start gap-2 max-w-xs text-xs"><input type="checkbox" checked={finalConfirmed} onChange={e => setFinalConfirmed(e.target.checked)} /><span>我已核对以上配置，同意上传并部署</span></label>
                    <button
                      onClick={() => setStep(2)}
                      className="px-4 py-2 rounded-xl border border-line text-ui font-medium text-muted hover:text-ink hover:bg-paper transition-colors"
                    >
                      ← 查看改造报告
                    </button>
                    <button
                      onClick={handlePublish}
                      disabled={!finalConfirmed || !deliveryJob || missingConfigs.length > 0}
                      className="px-5 py-2 rounded-xl bg-accent hover:opacity-90 text-white text-ui font-medium shadow-xs transition-all"
                    >
                      确认并上传部署
                    </button>
                  </>
                )}
                {step === 4 && deployStatus === "failed" && (
                  <>
                    <button
                      onClick={() => setStep(3)}
                      className="px-4 py-2 rounded-xl border border-line text-ui font-medium text-ink hover:bg-paper transition-colors"
                    >
                      返回配置修改
                    </button>
                    <button
                      onClick={handlePublish}
                      className="px-5 py-2 rounded-xl bg-accent hover:opacity-90 text-white text-ui font-medium shadow-xs transition-all flex items-center gap-1.5"
                    >
                      <Icon name="refresh" size={13} />
                      重试部署
                    </button>
                  </>
                )}
                {step === 4 && deployStatus === "running" && (
                  <button
                    onClick={closeWizard}
                    className="px-5 py-2 rounded-xl bg-accent hover:opacity-90 text-white text-ui font-medium shadow-xs transition-all"
                  >
                    完成
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Drawer: App Details and Config Edit */}
      {selectedApp && (
        <div className="fixed inset-0 z-40 bg-black/40 backdrop-blur-xs flex justify-end">
          <div className="bg-panel w-full max-w-xl h-full shadow-2xl border-l border-line flex flex-col animate-in slide-in-from-right duration-200">
            {/* Drawer Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-line bg-panel shrink-0">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-accentSoft text-accent flex items-center justify-center font-bold">
                  <Icon name="sliders" size={16} />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-ui font-semibold text-ink">{selectedApp.name}</h2>
                    <span className="font-mono text-[11px] px-2 py-0.5 rounded bg-paper text-muted border border-line">
                      {selectedApp.slug}
                    </span>
                  </div>
                  <p className="text-meta text-muted line-clamp-1 mt-0.5">
                    {selectedApp.description || "暂无应用描述"}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span
                  className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-label font-medium ${
                    selectedApp.status === "running"
                      ? "bg-okSoft text-ok"
                      : selectedApp.status === "failed"
                      ? "bg-danger/10 text-danger border border-danger/25"
                      : "bg-warnSoft text-warnInk"
                  }`}
                >
                  <span
                    className={`w-1.5 h-1.5 rounded-full ${
                      selectedApp.status === "running"
                        ? "bg-ok animate-pulse"
                        : selectedApp.status === "failed"
                        ? "bg-danger"
                        : "bg-warnInk"
                    }`}
                  />
                  {selectedApp.status === "running"
                    ? "运行正常"
                    : selectedApp.status === "failed"
                    ? "部署失败"
                    : selectedApp.status}
                </span>
                <button
                  onClick={() => setSelectedApp(null)}
                  className="w-8 h-8 rounded-lg flex items-center justify-center text-muted hover:text-ink hover:bg-paper transition-colors"
                  title="关闭"
                >
                  <Icon name="x" size={16} />
                </button>
              </div>
            </div>

            {/* Failure Alert Banner if failed */}
            {selectedApp.status === "failed" && (
              <div className="mx-6 mt-4 p-3.5 rounded-xl bg-danger/10 border border-danger/25 flex items-center justify-between shrink-0">
                <div className="flex items-center gap-2.5 text-danger text-meta">
                  <Icon name="warning" size={16} />
                  <span>该应用上次部署未成功，容器未正常运行</span>
                </div>
                <button
                  onClick={() => {
                    handleFixDeploy(selectedApp);
                    setSelectedApp(null);
                  }}
                  className="px-3 py-1.5 rounded-lg bg-danger hover:opacity-90 text-white text-label font-medium flex items-center gap-1.5 transition-opacity"
                >
                  <Icon name="refresh" size={12} />
                  进入向导修复部署
                </button>
              </div>
            )}

            {/* Top Quick Info Card */}
            <div className="px-6 py-3.5 bg-paper/50 border-b border-line shrink-0">
              <div className="grid grid-cols-2 gap-3 text-meta">
                <div className="p-2.5 rounded-lg bg-panel border border-line/80 flex flex-col justify-between">
                  <span className="text-label text-muted">内网直连地址</span>
                  <div className="flex items-center justify-between gap-1.5 mt-1 font-mono">
                    <a
                      href={selectedApp.app_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-accent hover:underline truncate text-label"
                      title={selectedApp.app_url}
                    >
                      {selectedApp.app_url}
                    </a>
                    <button
                      onClick={() => copyToClipboard(selectedApp.app_url, "内网直连地址")}
                      className="text-muted hover:text-ink p-1 rounded hover:bg-paper"
                      title="复制地址"
                    >
                      <Icon name="copy" size={13} />
                    </button>
                  </div>
                </div>

                <div className="p-2.5 rounded-lg bg-panel border border-line/80 flex flex-col justify-between">
                  <span className="text-label text-muted">对应本地技能</span>
                  <div className="flex items-center justify-between gap-1.5 mt-1 font-mono">
                    <span className="text-ink truncate font-medium text-label">
                      {selectedApp.skill_name || `app-${selectedApp.slug}`}
                    </span>
                    <button
                      onClick={() => copyToClipboard(selectedApp.skill_name || `app-${selectedApp.slug}`, "技能名称")}
                      className="text-muted hover:text-ink p-1 rounded hover:bg-paper"
                      title="复制技能名"
                    >
                      <Icon name="copy" size={13} />
                    </button>
                  </div>
                </div>
              </div>
            </div>

            {/* Drawer Tabs */}
            <div className="flex items-center gap-1 px-6 border-b border-line bg-panel shrink-0">
              <button
                onClick={() => setDrawerTab("artifacts")}
                className={`py-3 px-3 text-ui font-medium border-b-2 transition-colors flex items-center gap-2 ${
                  drawerTab === "artifacts"
                    ? "border-accent text-accent"
                    : "border-transparent text-muted hover:text-ink"
                }`}
              >
                <Icon name="folder" size={14} />
                <span>产物与文件目录</span>
                <span className="text-[10px] px-1.5 py-0.2 rounded-full bg-accentSoft text-accent font-semibold">
                  核心
                </span>
              </button>

              <button
                onClick={() => setDrawerTab("configs")}
                className={`py-3 px-3 text-ui font-medium border-b-2 transition-colors flex items-center gap-2 ${
                  drawerTab === "configs"
                    ? "border-accent text-accent"
                    : "border-transparent text-muted hover:text-ink"
                }`}
              >
                <Icon name="sliders" size={14} />
                <span>环境变量配置</span>
                <span className="text-[10px] px-1.5 py-0.2 rounded-full bg-paper text-muted border border-line">
                  {drawerConfigs.length}
                </span>
              </button>

              <button
                onClick={() => setDrawerTab("logs")}
                className={`py-3 px-3 text-ui font-medium border-b-2 transition-colors flex items-center gap-2 ${
                  drawerTab === "logs"
                    ? "border-accent text-accent"
                    : "border-transparent text-muted hover:text-ink"
                }`}
              >
                <Icon name="code" size={14} />
                <span>最近部署日志</span>
              </button>
            </div>

            {/* Tab Body */}
            <div className="flex-1 overflow-y-auto p-6 space-y-4">
              {/* TAB 1: 产物与文件目录 */}
              {drawerTab === "artifacts" && <AppDeliveryArtifacts slug={selectedApp.slug} />}

              {/* TAB 2: 环境变量配置 */}
              {drawerTab === "configs" && (
                <div className="space-y-4">
                  <div className="p-3 rounded-xl bg-paper/80 border border-line flex items-center justify-between">
                    <div>
                      <div className="font-semibold text-ui text-ink">环境变量与运行参数</div>
                      <p className="text-label text-muted mt-0.5">
                        修改配置后点击下方保存，将自动秒级重启容器使新变量生效。
                      </p>
                    </div>
                    <button
                      onClick={() => {
                        setDrawerConfigs([...drawerConfigs, { key: "", value: "", description: "自定义变量" }]);
                      }}
                      className="px-2.5 py-1.5 rounded-lg border border-line bg-panel hover:bg-paper text-label font-medium text-ink flex items-center gap-1.5 transition-colors"
                    >
                      <Icon name="plus" size={12} />
                      添加变量
                    </button>
                  </div>

                  {drawerConfigs.length === 0 ? (
                    <div className="text-center py-8 border border-dashed border-line rounded-xl text-muted text-meta">
                      暂无环境变量配置项
                    </div>
                  ) : (
                    <div className="space-y-2.5">
                      {drawerConfigs.map((cfg, idx) => {
                        const isSecretKey = /PASS|SECRET|KEY|TOKEN|AUTH|CREDENTIAL|DSN|CONNECTION.?STRING|DATABASE_URL|REDIS_URL|MONGO(?:DB)?_(?:URL|URI)/i.test(cfg.key)
                          || /:\/\/[^/\s:]+:[^@\s]+@|(?:password|pwd)\s*=/i.test(cfg.value || "");
                        const isRevealed = showSecrets[cfg.key] ?? true;

                        return (
                          <div
                            key={idx}
                            className="p-3 rounded-xl border border-line bg-paper/50 space-y-2 hover:border-lineStrong transition-colors"
                          >
                            <div className="flex items-center justify-between gap-2">
                              <input
                                type="text"
                                value={cfg.key}
                                onChange={(e) => {
                                  const next = drawerConfigs.map(item => ({ ...item }));
                                  next[idx].key = e.target.value;
                                  if (next[idx].resource_id) { next[idx].resource_id = undefined; next[idx].value_source = "manual"; }
                                  setDrawerConfigs(next);
                                }}
                                placeholder="KEY_NAME"
                                className="font-mono text-ui font-semibold text-ink bg-transparent focus:outline-none flex-1 border-b border-transparent focus:border-accent"
                              />
                              <div className="flex items-center gap-1.5">
                                {isSecretKey && (
                                  <button
                                    onClick={() => toggleShowSecret(cfg.key)}
                                    className="px-2 py-0.5 rounded text-[10px] text-muted hover:text-ink bg-panel border border-line"
                                    title={isRevealed ? "隐藏密钥" : "显示明文"}
                                  >
                                    {isRevealed ? "隐藏" : "显示"}
                                  </button>
                                )}
                                <button
                                  onClick={() => {
                                    setDrawerConfigs(drawerConfigs.filter((_, i) => i !== idx));
                                  }}
                                  className="text-muted hover:text-danger p-1 rounded hover:bg-paper"
                                  title="删除此配置"
                                >
                                  <Icon name="trash" size={13} />
                                </button>
                              </div>
                            </div>

                            <div>
                              <input
                                type={isSecretKey && !isRevealed ? "password" : "text"}
                                value={cfg.value}
                                onChange={(e) => {
                                  const next = drawerConfigs.map(item => ({ ...item }));
                                  next[idx].value = e.target.value;
                                  if (next[idx].resource_id) { next[idx].resource_id = undefined; next[idx].value_source = "manual"; }
                                  setDrawerConfigs(next);
                                }}
                                placeholder={cfg.has_value && cfg.value_source === "resource" ? "已配置：由资源注入，留空保留现有值" : "输入配置值..."}
                                className="w-full px-3 py-1.5 rounded-lg border border-line bg-panel text-ui font-mono text-ink focus:outline-accent"
                              />
                              {cfg.has_value && cfg.value_source === "resource" && <p className="text-xs text-muted mt-2">已配置 · 由“{cfg.source_label}”注入。输入框为空表示沿用资源值，不是配置被清空；填写新值才会覆盖。</p>}
                            </div>

                            {cfg.description && (
                              <p className="text-label text-muted leading-tight">
                                {cfg.description}
                              </p>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}

              {/* TAB 3: 最近部署日志 */}
              {drawerTab === "logs" && (
                <div className="space-y-3">
                  <div className="flex items-center justify-between text-meta text-muted">
                    <span>容器构建与上线运行日志</span>
                    <button
                      onClick={() => copyToClipboard(selectedApp.build_log || "", "部署日志")}
                      className="px-2.5 py-1 rounded-lg border border-line bg-paper hover:bg-chromeHover text-label text-ink flex items-center gap-1 transition-colors"
                    >
                      <Icon name="copy" size={12} />
                      复制完整日志
                    </button>
                  </div>
                  <pre className="p-3.5 rounded-xl bg-neutral-950 text-emerald-400 font-mono text-label leading-relaxed h-[420px] overflow-y-auto whitespace-pre-wrap selection:bg-emerald-900 selection:text-white">
                    {selectedApp.build_log || "暂无日志输出"}
                  </pre>
                </div>
              )}
            </div>

            {/* Drawer Footer */}
            <div className="flex items-center justify-between px-6 py-4 border-t border-line bg-panel shrink-0">
              <div className="flex items-center gap-3">
                <button
                  onClick={() => setSelectedApp(null)}
                  className="px-4 py-2 rounded-xl border border-line text-ui font-medium text-muted hover:text-ink hover:bg-paper transition-colors"
                >
                  关闭
                </button>
                {copyFeedback && (
                  <span className="text-label text-ok flex items-center gap-1 font-medium">
                    ✓ {copyFeedback} 已复制到剪贴板
                  </span>
                )}
              </div>

              <div className="flex items-center gap-3">
                <span className="text-label text-muted hidden sm:inline">
                  修改后将触发热更新重启
                </span>
                <button
                  disabled={savingConfig}
                  onClick={saveDrawerConfig}
                  className="px-5 py-2 rounded-xl bg-accent hover:opacity-90 text-white text-ui font-medium shadow-xs disabled:opacity-50 flex items-center gap-2 transition-all"
                >
                  {savingConfig ? (
                    <>
                      <Icon name="refresh" size={14} className="animate-spin" />
                      正在应用配置并重启...
                    </>
                  ) : (
                    <>
                      <Icon name="sparkle" size={14} />
                      保存配置并秒级重启
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
