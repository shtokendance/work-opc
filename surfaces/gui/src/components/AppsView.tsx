import { useEffect, useState } from "react";
import {
  checkAppSlug,
  enableLocalSkill,
  getDeployedApp,
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

  // Wizard Step 1 form fields
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [selectedModel, setSelectedModel] = useState("default");
  const [appUrl, setAppUrl] = useState("");
  const [containerPort, setContainerPort] = useState(8080);
  const [slugError, setSlugError] = useState<string | null>(null);

  // Wizard Step 2 & 3 state
  const [transforming, setTransforming] = useState(false);
  const [onlineResources, setOnlineResources] = useState<ResourceSummary[]>([]);
  const [selectedResourceIds, setSelectedResourceIds] = useState<string[]>([]);
  const [configs, setConfigs] = useState<AppConfigItem[]>([]);

  // Wizard Step 4 state
  const [deploying, setDeploying] = useState(false);
  const [deployStatus, setDeployStatus] = useState<string>("building");
  const [buildLogs, setBuildLogs] = useState<string>("");
  const [pollTimer, setPollTimer] = useState<any>(null);

  // Drawer detail state
  const [selectedApp, setSelectedApp] = useState<DeployedApp | null>(null);
  const [drawerConfigs, setDrawerConfigs] = useState<AppConfigItem[]>([]);
  const [drawerResourceIds, setDrawerResourceIds] = useState<string[]>([]);
  const [savingConfig, setSavingConfig] = useState(false);

  const refreshApps = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listDeployedApps();
      setApps(data.apps || []);
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
  }, []);

  // Step 1: Validate and Check Slug
  const handleProceedToStep2 = async () => {
    if (!name.trim() || !slug.trim() || !selectedFile) {
      alert("请填写应用名称、唯一标识 slug 并选择源码 zip 包");
      return;
    }
    setSlugError(null);
    try {
      const slugRes = await checkAppSlug(slug.trim());
      if (!slugRes.available) {
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

  // Step 2: Run AI Transform
  const startTransform = async (targetAppUrl: string) => {
    if (!selectedFile) return;
    setTransforming(true);
    try {
      // Read file to base64
      const reader = new FileReader();
      reader.onload = async (e) => {
        const base64Data = (e.target?.result as string)?.split(",")?.[1] || "";
        const res = await transformApp({
          slug: slug.trim(),
          name: name.trim(),
          description: description.trim(),
          app_url: targetAppUrl,
          container_port: containerPort,
          data_b64: base64Data,
        });

        if (!res.ok) {
          alert(`AI 改造失败: ${res.error}`);
          setStep(1);
          setTransforming(false);
          return;
        }

        const manifest = res.manifest || {};
        setConfigs(manifest.configs || []);

        // Auto-match detected resource types to online resources
        const detected = manifest.detected_resource_types || [];
        const matchedIds = onlineResources
          .filter((r) => detected.includes(r.type))
          .map((r) => r.id);
        setSelectedResourceIds(matchedIds);

        setTransforming(false);
        setStep(3);
      };
      reader.readAsDataURL(selectedFile);
    } catch (err: any) {
      alert(`AI 改造异常: ${err?.message}`);
      setTransforming(false);
      setStep(1);
    }
  };

  // Step 3: Confirm and Upload
  const handlePublish = async () => {
    setDeploying(true);
    setStep(4);
    setDeployStatus("building");
    setBuildLogs("正在预装本地技能并将应用包上传至平台...");

    try {
      const pubRes = await publishAppToPlatform({
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
              setDeploying(false);
              // Auto-enable local skill
              await enableLocalSkill(slug.trim()).catch(() => {});
              refreshApps();
            } else if (appData.status === "failed") {
              clearInterval(timer);
              setDeploying(false);
            }
          }
        } catch {
          // ignore transient poll error
        }
      }, 2500);
      setPollTimer(timer);
    } catch (err: any) {
      setDeployStatus("failed");
      setBuildLogs(`发布异常: ${err?.message}`);
      setDeploying(false);
    }
  };

  const closeWizard = () => {
    if (pollTimer) clearInterval(pollTimer);
    setIsModalOpen(false);
    setStep(1);
    setName("");
    setSlug("");
    setDescription("");
    setSelectedFile(null);
    setConfigs([]);
    setSelectedResourceIds([]);
    setBuildLogs("");
    refreshApps();
  };

  // Drawer handlers
  const openDrawer = (app: DeployedApp) => {
    setSelectedApp(app);
    setDrawerConfigs(app.app_config || []);
    setDrawerResourceIds(app.resource_ids || []);
  };

  const saveDrawerConfig = async () => {
    if (!selectedApp) return;
    setSavingConfig(true);
    try {
      await updateDeployedAppConfig(selectedApp.slug, {
        container_port: selectedApp.container_port,
        app_config: drawerConfigs,
        resource_ids: drawerResourceIds,
      });
      alert("配置已更新，容器已秒级重启！");
      refreshApps();
      const updated = await getDeployedApp(selectedApp.slug);
      setSelectedApp(updated);
    } catch (err: any) {
      alert(`保存失败: ${err?.message}`);
    } finally {
      setSavingConfig(false);
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full bg-chromeBg overflow-hidden select-none">
      {/* Top Header Bar */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-line bg-card">
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
            <h1 className="text-lg font-semibold text-ink">应用中心与技能管理</h1>
            <p className="text-xs text-muted">
              一键容器化改造、纯内网 HTTP 部署与 AI 技能生成，双端开箱即用
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
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-xs font-medium shadow-sm transition-colors"
          >
            <Icon name="plus" size={13} />
            新建应用
          </button>
        </div>
      </div>

      {/* Main Content Area */}
      <div className="flex-1 overflow-y-auto p-6">
        {error && (
          <div className="mb-4 p-3 rounded-lg bg-red-50 text-red-700 text-xs border border-red-200">
            {error}
          </div>
        )}

        {apps.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center h-80 text-center border-2 border-dashed border-line rounded-xl p-8">
            <div className="w-12 h-12 rounded-full bg-blue-50 text-blue-600 flex items-center justify-center mb-3">
              <Icon name="workList" size={24} />
            </div>
            <h3 className="text-sm font-semibold text-ink mb-1">暂无部署应用</h3>
            <p className="text-xs text-muted max-w-sm mb-4">
              点击右上角「新建应用」，上传任意应用源码 zip 包，AI 自动完成容器化改造并转化为可调用的 AI 技能。
            </p>
            <button
              onClick={() => setIsModalOpen(true)}
              className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-xs font-medium"
            >
              新建第一个应用
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {apps.map((app) => (
              <div
                key={app.id}
                onClick={() => openDrawer(app)}
                className="bg-card border border-line rounded-xl p-4.5 hover:shadow-md transition-all cursor-pointer flex flex-col justify-between"
              >
                <div>
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <h3 className="font-semibold text-sm text-ink truncate">{app.name}</h3>
                    <span
                      className={`px-2 py-0.5 rounded-full text-[11px] font-medium shrink-0 ${
                        app.status === "running"
                          ? "bg-green-100 text-green-700"
                          : app.status === "failed"
                          ? "bg-red-100 text-red-700"
                          : "bg-amber-100 text-amber-700 animate-pulse"
                      }`}
                    >
                      {app.status === "running" ? "运行中" : app.status === "failed" ? "部署失败" : "构建中"}
                    </span>
                  </div>

                  <p className="text-xs text-muted line-clamp-2 mb-3">
                    {app.description || "暂无描述"}
                  </p>
                </div>

                <div className="pt-3 border-t border-line/60 space-y-1.5 text-[11px]">
                  <div className="flex items-center justify-between">
                    <span className="text-muted">内网地址:</span>
                    <a
                      href={app.app_url}
                      target="_blank"
                      rel="noreferrer"
                      onClick={(e) => e.stopPropagation()}
                      className="text-blue-600 hover:underline truncate max-w-[180px]"
                    >
                      {app.app_url}
                    </a>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-muted">本地技能:</span>
                    <span className="font-mono text-ink">{app.skill_name}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 4-Step New App Modal */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 bg-black/40 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-card w-full max-w-2xl rounded-2xl shadow-xl border border-line flex flex-col max-h-[85vh] overflow-hidden">
            {/* Modal Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-line">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold px-2 py-0.5 rounded bg-blue-100 text-blue-700">
                  Step {step} / 4
                </span>
                <h2 className="text-sm font-semibold text-ink">
                  {step === 1 && "填写基本信息与源码"}
                  {step === 2 && "AI 自动分析与改造中..."}
                  {step === 3 && "配置确认与线上资源选择"}
                  {step === 4 && "部署进展与技能激活"}
                </h2>
              </div>
              <button
                onClick={closeWizard}
                className="text-muted hover:text-ink text-sm p-1 rounded-md"
              >
                ✕
              </button>
            </div>

            {/* Modal Body */}
            <div className="flex-1 overflow-y-auto p-6 space-y-4">
              {/* STEP 1 */}
              {step === 1 && (
                <div className="space-y-4 text-xs">
                  <div>
                    <label className="block font-medium text-ink mb-1">
                      应用名称 <span className="text-red-500">*</span>
                    </label>
                    <input
                      type="text"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      placeholder="例如：报销审批助手"
                      className="w-full px-3 py-2 rounded-lg border border-line bg-chromeBg text-ink focus:outline-blue-600"
                    />
                  </div>

                  <div>
                    <label className="block font-medium text-ink mb-1">
                      唯一标识 (slug) <span className="text-red-500">*</span>
                    </label>
                    <input
                      type="text"
                      value={slug}
                      onChange={(e) => setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))}
                      placeholder="例如：expense-system（仅限小写字母、数字和中划线）"
                      className="w-full px-3 py-2 rounded-lg border border-line bg-chromeBg text-ink font-mono focus:outline-blue-600"
                    />
                    {slugError && <p className="text-red-600 mt-1">{slugError}</p>}
                  </div>

                  <div>
                    <label className="block font-medium text-ink mb-1">应用描述</label>
                    <textarea
                      rows={2}
                      value={description}
                      onChange={(e) => setDescription(e.target.value)}
                      placeholder="简述该应用功能，将自动用于 AI 技能描述"
                      className="w-full px-3 py-2 rounded-lg border border-line bg-chromeBg text-ink focus:outline-blue-600"
                    />
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block font-medium text-ink mb-1">容器监听端口</label>
                      <input
                        type="number"
                        value={containerPort}
                        onChange={(e) => setContainerPort(Number(e.target.value))}
                        className="w-full px-3 py-2 rounded-lg border border-line bg-chromeBg text-ink focus:outline-blue-600"
                      />
                    </div>
                    <div>
                      <label className="block font-medium text-ink mb-1">AI 改造模型</label>
                      <select
                        value={selectedModel}
                        onChange={(e) => setSelectedModel(e.target.value)}
                        className="w-full px-3 py-2 rounded-lg border border-line bg-chromeBg text-ink focus:outline-blue-600"
                      >
                        <option value="default">当前接入的默认智能体</option>
                      </select>
                    </div>
                  </div>

                  <div>
                    <label className="block font-medium text-ink mb-1">
                      上传源码包 (.zip) <span className="text-red-500">*</span>
                    </label>
                    <input
                      type="file"
                      accept=".zip"
                      onChange={(e) => setSelectedFile(e.target.files?.[0] || null)}
                      className="w-full px-3 py-2 border border-dashed border-line rounded-lg bg-chromeBg text-ink cursor-pointer"
                    />
                  </div>
                </div>
              )}

              {/* STEP 2 */}
              {step === 2 && (
                <div className="py-8 flex flex-col items-center justify-center text-center space-y-4">
                  <div className="w-12 h-12 rounded-full border-4 border-blue-600 border-t-transparent animate-spin" />
                  <div className="space-y-1">
                    <h3 className="font-semibold text-sm text-ink">
                      {transforming ? "AI 正在改造并提取应用信息..." : "改造完成，准备就绪"}
                    </h3>
                    <p className="text-xs text-muted max-w-sm">
                      1. 补齐 Dockerfile、/healthz 与环境变量绑定<br />
                      2. 生成直连内网的标准应用技能包<br />
                      3. 提取应用配置项与依赖资源清单
                    </p>
                  </div>
                </div>
              )}

              {/* STEP 3 */}
              {step === 3 && (
                <div className="space-y-4 text-xs">
                  <div>
                    <h3 className="font-semibold text-ink mb-2">1. 勾选要绑定的线上现有资源 (内网)</h3>
                    {onlineResources.length === 0 ? (
                      <p className="text-muted">暂无预设资源</p>
                    ) : (
                      <div className="grid grid-cols-2 gap-2">
                        {onlineResources.map((res) => {
                          const checked = selectedResourceIds.includes(res.id);
                          return (
                            <label
                              key={res.id}
                              className={`flex items-start gap-2.5 p-2.5 rounded-lg border cursor-pointer transition-colors ${
                                checked
                                  ? "border-blue-600 bg-blue-50/50"
                                  : "border-line bg-chromeBg hover:bg-chromeHover"
                              }`}
                            >
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={(e) => {
                                  if (e.target.checked) {
                                    setSelectedResourceIds([...selectedResourceIds, res.id]);
                                  } else {
                                    setSelectedResourceIds(
                                      selectedResourceIds.filter((id) => id !== res.id)
                                    );
                                  }
                                }}
                                className="mt-0.5 rounded text-blue-600"
                              />
                              <div>
                                <div className="font-semibold text-ink">{res.name}</div>
                                <div className="text-[11px] text-muted">{res.description}</div>
                              </div>
                            </label>
                          );
                        })}
                      </div>
                    )}
                  </div>

                  <div>
                    <h3 className="font-semibold text-ink mb-2">2. 确认应用配置项 (环境变量)</h3>
                    <div className="border border-line rounded-lg overflow-hidden">
                      <table className="w-full text-left">
                        <thead className="bg-chromeBg border-b border-line text-[11px] text-muted">
                          <tr>
                            <th className="py-2 px-3">配置项 Key</th>
                            <th className="py-2 px-3">配置值 Value</th>
                            <th className="py-2 px-3">说明</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-line/60">
                          {configs.map((c, idx) => (
                            <tr key={idx}>
                              <td className="py-1.5 px-3 font-mono font-medium text-ink">{c.key}</td>
                              <td className="py-1.5 px-3">
                                <input
                                  type="text"
                                  value={c.value}
                                  onChange={(e) => {
                                    const next = [...configs];
                                    next[idx].value = e.target.value;
                                    setConfigs(next);
                                  }}
                                  className="w-full px-2 py-1 rounded border border-line bg-chromeBg text-xs"
                                />
                              </td>
                              <td className="py-1.5 px-3 text-muted text-[11px]">{c.description || "-"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              )}

              {/* STEP 4 */}
              {step === 4 && (
                <div className="space-y-4 text-xs">
                  <div className="flex items-center gap-3 p-3 rounded-lg bg-chromeBg border border-line">
                    <span
                      className={`px-2 py-1 rounded text-xs font-semibold ${
                        deployStatus === "running"
                          ? "bg-green-100 text-green-700"
                          : deployStatus === "failed"
                          ? "bg-red-100 text-red-700"
                          : "bg-amber-100 text-amber-700 animate-pulse"
                      }`}
                    >
                      {deployStatus === "running" ? "部署成功" : deployStatus === "failed" ? "部署失败" : "正在构建部署..."}
                    </span>
                    <span className="text-muted">
                      {deployStatus === "running"
                        ? "本地技能已激活，云端技能池已完成同步！"
                        : "deploy-opc 正在执行内网容器构建与启动探测..."}
                    </span>
                  </div>

                  <div>
                    <label className="block font-medium text-ink mb-1">构建与部署日志</label>
                    <pre className="p-3 rounded-lg bg-black text-green-400 font-mono text-[11px] h-48 overflow-y-auto whitespace-pre-wrap">
                      {buildLogs || "等待输出..."}
                    </pre>
                  </div>

                  {deployStatus === "running" && (
                    <div className="p-3 rounded-lg bg-green-50 border border-green-200 text-green-800 space-y-1">
                      <div className="font-semibold">🎉 应用已成功上线！</div>
                      <div>内网访问地址: <a href={appUrl} target="_blank" rel="noreferrer" className="underline font-mono">{appUrl}</a></div>
                      <div className="text-[11px] text-green-700">
                        提示: 现在可以在任意对话窗口中直接调用 <code>app-{slug}</code> 技能！
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Modal Footer */}
            <div className="flex items-center justify-between px-6 py-3 border-t border-line bg-chromeBg">
              <button
                onClick={closeWizard}
                disabled={deploying}
                className="px-3.5 py-1.5 rounded-lg border border-line text-xs font-medium text-muted hover:text-ink disabled:opacity-40"
              >
                {deploying ? "部署中..." : "关闭"}
              </button>

              <div className="flex items-center gap-2">
                {step === 1 && (
                  <button
                    onClick={handleProceedToStep2}
                    className="px-4 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-xs font-medium"
                  >
                    下一步 (AI 改造)
                  </button>
                )}
                {step === 3 && (
                  <button
                    onClick={handlePublish}
                    className="px-4 py-1.5 rounded-lg bg-green-600 hover:bg-green-700 text-white text-xs font-medium"
                  >
                    确认并上传部署
                  </button>
                )}
                {step === 4 && deployStatus === "running" && (
                  <button
                    onClick={closeWizard}
                    className="px-4 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-xs font-medium"
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
        <div className="fixed inset-0 z-40 bg-black/30 backdrop-blur-xs flex justify-end">
          <div className="bg-card w-full max-w-lg h-full shadow-2xl border-l border-line flex flex-col">
            <div className="flex items-center justify-between px-6 py-4 border-b border-line">
              <h2 className="text-sm font-semibold text-ink">应用详情与配置维护</h2>
              <button
                onClick={() => setSelectedApp(null)}
                className="text-muted hover:text-ink text-sm p-1 rounded-md"
              >
                ✕
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-6 space-y-4 text-xs">
              <div className="space-y-1">
                <div className="text-base font-semibold text-ink">{selectedApp.name}</div>
                <div className="text-muted font-mono">{selectedApp.slug}</div>
                <p className="text-muted mt-1">{selectedApp.description || "暂无描述"}</p>
              </div>

              <div className="p-3 rounded-lg bg-chromeBg border border-line space-y-2">
                <div className="flex justify-between">
                  <span className="text-muted">当前状态:</span>
                  <span className="font-semibold text-ink">{selectedApp.status}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted">内网地址:</span>
                  <a
                    href={selectedApp.app_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-blue-600 hover:underline font-mono"
                  >
                    {selectedApp.app_url}
                  </a>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted">对应技能名:</span>
                  <span className="font-mono text-ink">{selectedApp.skill_name}</span>
                </div>
              </div>

              <div>
                <h3 className="font-semibold text-ink mb-2">修改配置项 (环境变量)</h3>
                <div className="space-y-2">
                  {drawerConfigs.map((cfg, idx) => (
                    <div key={idx} className="flex items-center gap-2">
                      <span className="w-28 font-mono text-[11px] text-muted truncate">{cfg.key}</span>
                      <input
                        type="text"
                        value={cfg.value}
                        onChange={(e) => {
                          const next = [...drawerConfigs];
                          next[idx].value = e.target.value;
                          setDrawerConfigs(next);
                        }}
                        className="flex-1 px-2 py-1 rounded border border-line bg-chromeBg text-xs font-mono"
                      />
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <h3 className="font-semibold text-ink mb-2">最近部署日志</h3>
                <pre className="p-2.5 rounded bg-black text-green-400 font-mono text-[10px] h-32 overflow-y-auto whitespace-pre-wrap">
                  {selectedApp.build_log || "暂无日志"}
                </pre>
              </div>
            </div>

            <div className="flex items-center justify-between px-6 py-3 border-t border-line bg-chromeBg">
              <button
                onClick={() => setSelectedApp(null)}
                className="px-3.5 py-1.5 rounded-lg border border-line text-xs font-medium text-muted hover:text-ink"
              >
                取消
              </button>
              <button
                disabled={savingConfig}
                onClick={saveDrawerConfig}
                className="px-4 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-xs font-medium disabled:opacity-50"
              >
                {savingConfig ? "正在重启..." : "保存配置并秒级重启"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
