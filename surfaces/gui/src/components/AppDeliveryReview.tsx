import { useEffect, useRef, useState } from "react";
import { appDeliveryRequest, getAppDeliveryJob, type AppDeliveryJob, type ResourceSummary } from "../api";

const questionText = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value);
const effects: Record<string, string> = { read: "只读查询", write: "修改数据，调用前确认", destructive: "删除操作，调用前确认", external: "外部操作，调用前确认" };
const changeTitle = (change: { path: string; reason: string }) => {
  if (/(^|\/)Dockerfile(?:\.|$)/i.test(change.path)) return "补齐服务器构建和启动应用的方式";
  if (/(^|\/)\.dockerignore$/.test(change.path)) return "打包时排除无关文件和敏感配置";
  if (/health/i.test(change.path)) return "让平台能够检查应用是否正常运行";
  if (/launcher|entrypoint|start\.sh/i.test(change.path)) return "让应用启动时读取你确认的配置";
  if (/config.*template/i.test(change.path)) return "保留原配置，支持部署时填写需要的值";
  if (/(^|\/)SKILL\.md$/.test(change.path)) return "生成能在对话中操作应用的业务技能";
  if (/(^|\/)analysis\.md$/.test(change.path)) return "保存本次改造说明，方便日后查阅";
  return change.reason;
};

function isLowLevelPurpose(purpose: string, path = "") {
  return /health|存活|会话\s*Cookie|HttpOnly|注销当前会话|读取当前会话/i.test(purpose) || /^\/?(?:api\/)?(?:healthz?|session)$/i.test(path);
}

function migrationHintForResource(resource: ResourceSummary) {
  const type = (resource.type || "").toLowerCase();
  if (/postgres|mysql|mariadb|sqlserver|mssql|mongo/.test(type)) {
    return {
      badge: "数据库服务 · 需注意表结构与存量数据",
      migration: "切换为服务器数据库后将连接新库，本机原有数据不会自动搬迁。若应用启动时会自动建表（如 ORM 自动迁移 / CREATE TABLE IF NOT EXISTS）可直接切换；否则请先初始化表结构，或点击下方按钮让 AI 补充启动自动建表逻辑。",
    };
  }
  if (/redis|memcache/.test(type)) {
    return {
      badge: "缓存服务 · 无需迁移数据",
      migration: "缓存数据属于临时状态，无需迁移本机历史数据，在下一步「配置资源」中可直接一键切换。",
    };
  }
  if (/s3|minio|oss|cos|storage/.test(type)) {
    return {
      badge: "对象存储 · 新文件持久保存",
      migration: "切换后新上传的文件将持久化保存在服务器存储桶中，避免容器重部署丢失；本机历史附件如需保留需另行上传。",
    };
  }
  return {
    badge: "服务器基础服务",
    migration: "可在下一步「配置资源」中按需选择是否替换为该服务器资源。",
  };
}

export function AppDeliveryReview({
  jobId,
  onReady,
  onReplaceSource,
  onNextStep,
  onlineResources,
}: {
  jobId: string;
  onReady: (job: AppDeliveryJob) => void;
  onReplaceSource?: () => void;
  onNextStep?: () => void;
  onlineResources?: ResourceSummary[];
}) {
  const [job, setJob] = useState<AppDeliveryJob | null>(null);
  const [approved, setApproved] = useState<string[]>([]);
  const [configConfirmed, setConfigConfirmed] = useState(false);
  const [ignoreUnresolved, setIgnoreUnresolved] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [handoff, setHandoff] = useState("");
  const [copied, setCopied] = useState(false);
  const notified = useRef("");
  const proposalHash = useRef("");

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const result = await getAppDeliveryJob(jobId);
        if (!alive) return;
        if (proposalHash.current !== result.proposal_hash) {
          proposalHash.current = result.proposal_hash || "";
          setApproved([]); setConfigConfirmed(false); setIgnoreUnresolved(false); setAnswers({});
          setHandoff(""); setCopied(false);
        }
        setJob(result);
        if (result.status === "ready" && notified.current !== result.artifact_hash) {
          notified.current = result.artifact_hash || "ready";
          onReady(result);
        }
      } catch (e) { if (alive) setError(String(e)); }
    };
    void poll();
    const timer = setInterval(poll, 1200);
    return () => { alive = false; clearInterval(timer); };
  }, [jobId]);

  const approve = async () => {
    setBusy(true); setError("");
    try {
      const payload: Record<string, unknown> = {
        proposal_hash: job?.proposal_hash,
        approved_changes: approved,
        confirm_config_changes: configConfirmed,
      };
      if (ignoreUnresolved) payload.ignore_unresolved = true;
      const result = await appDeliveryRequest<AppDeliveryJob>(`jobs/${jobId}/approve`, payload);
      setJob(result);
      if (result.status === "ready") onReady(result);
      else setError(result.error || "改造校验未通过");
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  };

  const retry = async () => {
    const replies = (job?.proposal?.unresolved || []).map((q, i) => answers[i]?.trim() ? `问题：${questionText(q)}\n用户回答：${answers[i].trim()}` : "").filter(Boolean);
    setBusy(true); setError("");
    try {
      await appDeliveryRequest(`jobs/${jobId}/analyze`, { feedback: [...replies, feedback.trim()].filter(Boolean).join("\n\n") });
      setApproved([]); setConfigConfirmed(false); setIgnoreUnresolved(false);
      setJob(j => j ? { ...j, status: "analyzing", proposal: undefined, error: "" } : j);
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  };

  const applyQuickFeedback = (text: string) => {
    setFeedback(prev => prev.trim() ? (prev.includes(text) ? prev : `${prev.trim()}\n${text}`) : text);
    const el = document.getElementById("app-analysis-feedback");
    if (el) el.focus();
  };

  const proposal = job?.proposal;
  const copyHandoff = async () => {
    setError(""); setCopied(false);
    try {
      const result = await appDeliveryRequest<{ prompt: string }>(`jobs/${jobId}/handoff`);
      setHandoff(result.prompt);
      try { await navigator.clipboard.writeText(result.prompt); setCopied(true); }
      catch { setError("自动复制未成功，请在下方选中并复制提示词。"); }
    } catch (e) { setError(String(e)); }
  };

  const ready = job?.status === "ready";
  const questions = proposal?.unresolved || [];
  const effectiveQuestionsCount = ignoreUnresolved ? 0 : questions.length;
  const remaining = proposal?.changes.filter(c => !approved.includes(c.id)).length || 0;
  const blockers = ready ? [] : [
    ...(effectiveQuestionsCount ? [`还有 ${questions.length} 个问题需要回答并重新分析，当前尚不能生成交付包。`] : []),
    ...(remaining ? [`还有 ${remaining} 项文件修改未批准。`] : []),
    ...(!configConfirmed && proposal ? ["请确认配置处理方式；具体配置值和资源在下一步核对。"] : []),
  ];

  const availableResources = (onlineResources && onlineResources.length > 0 ? onlineResources : job?.resources) || [];
  const allPurposes = proposal
    ? [
        ...proposal.pages.map(p => ({ purpose: p.purpose, low: isLowLevelPurpose(p.purpose, p.path) })),
        ...proposal.apis.map(a => ({ purpose: a.purpose, low: isLowLevelPurpose(a.purpose, a.path) })),
      ]
    : [];
  const businessPurposes = allPurposes.filter(item => !item.low).map(item => item.purpose);
  const displayPurposes = (businessPurposes.length > 0 ? businessPurposes : allPurposes.map(item => item.purpose)).slice(0, 5);

  const issueTextCombined = proposal
    ? `${proposal.summary} ${proposal.issues.map(i => `${i.impact} ${i.recommendation} ${questionText(i.evidence)}`).join(" ")}`
    : "";
  const hasLocalFileStorageWarning = /JSON\s*文件|本地文件|文件存储|容器替换可能丢失|uploads|sqlite/i.test(issueTextCombined);

  const allChangesSelected = !!proposal && proposal.changes.every(c => approved.includes(c.id)) && configConfirmed;
  const toggleSelectAll = () => {
    if (!proposal) return;
    if (allChangesSelected) {
      setApproved([]);
      setConfigConfirmed(false);
    } else {
      setApproved(proposal.changes.map(c => c.id));
      setConfigConfirmed(true);
    }
  };

  return <section className="space-y-4 text-ui text-ink">
    {/* 顶部状态与流程引导卡 */}
    <div className="rounded-xl border border-line bg-paper/50 p-4 space-y-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold text-base">
          {ready
            ? "改造完成，可以进入配置"
            : job?.status === "review"
            ? questions.length
              ? "分析完成，需要你补充信息"
              : "分析完成，请审阅修改"
            : job?.status === "failed"
            ? "操作未完成，请查看原因"
            : "AI 正在分析源码"}
        </h2>
        <span className="text-xs px-2.5 py-1 rounded-full bg-panel border border-line text-muted">
          分析模型：{job?.model || "连接中"}
        </span>
      </div>
      <p className="text-xs text-muted">
        {ready
          ? "已生成部署包和业务技能；下一步保留或调整配置，再确认上传。"
          : "原始源码保持不变。只有你批准后，才会在独立副本中应用修改。"}
      </p>
      <div className="font-mono text-xs break-all text-muted">目标部署地址：{job?.app_url}</div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 pt-1 text-xs">
        <div className={`rounded-lg px-3 py-2 border ${!ready ? "border-accent/40 bg-accentSoft/20 text-ink font-medium" : "border-line bg-panel text-muted"}`}>
          ① 审阅改造与基础服务建议
        </div>
        <div className={`rounded-lg px-3 py-2 border ${!ready ? "border-accent/40 bg-accentSoft/20 text-ink font-medium" : "border-line bg-panel text-muted"}`}>
          ② 勾选批准或提要求重新分析
        </div>
        <div className={`rounded-lg px-3 py-2 border ${ready ? "border-okLine bg-okSoft/40 text-ok font-medium" : "border-line bg-panel text-muted"}`}>
          ③ 下一步核对配置值与资源替换
        </div>
      </div>
    </div>

    {(error || job?.error) && <div role="alert" className="rounded-lg border border-danger p-3 text-danger whitespace-pre-wrap break-words">{error || job?.error}</div>}

    {proposal && <>
      {/* 卡片 1：应用功能概览 */}
      <div className="rounded-xl border border-line p-4 space-y-3">
        <h3 className="font-semibold">1. 这个应用能帮你做什么</h3>
        <p className="leading-relaxed">{job?.name}：部署成功并验证后，你可以通过网页访问它，或在对话中直接让 AI 调用以下业务能力：</p>
        {displayPurposes.length > 0 ? (
          <ul className="list-disc pl-5 space-y-1">
            {displayPurposes.map((purpose, i) => <li key={i}>{purpose}</li>)}
          </ul>
        ) : (
          <p className="text-muted">具体业务功能还需要根据源码补充。</p>
        )}
        <p className="text-ui text-muted">
          为让应用在服务器上正常运行，AI 建议修改 <b>{proposal.changes.length}</b> 个文件，并整理了 <b>{proposal.configs.length}</b> 项运行配置。原文件和你的本地密码/配置都会安全保留。
        </p>
        <details className="text-xs text-muted">
          <summary className="cursor-pointer hover:text-ink">查看 AI 完整分析说明</summary>
          <p className="whitespace-pre-wrap leading-relaxed mt-2 p-3 rounded-lg bg-paper border border-line text-ink">{proposal.summary}</p>
        </details>
      </div>

      {/* 待回答问题（如有） */}
      {questions.length > 0 && <section className="rounded-xl border border-warnInk bg-warnSoft p-4 space-y-3" aria-label="待回答问题">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-semibold">先回答这 {questions.length} 个问题</h3>
          {proposal.changes.length > 0 && (
            <button
              type="button"
              onClick={() => setIgnoreUnresolved(prev => !prev)}
              className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${
                ignoreUnresolved
                  ? "bg-okSoft text-ok border-okLine font-medium"
                  : "bg-panel text-ink border-line hover:bg-paper"
              }`}
            >
              {ignoreUnresolved ? "✓ 已选择忽略疑问，按 AI 默认方案继续" : "这些问题不影响，直接按默认方案继续"}
            </button>
          )}
        </div>
        <p className="text-xs text-muted">在下方逐项回答，再点击“根据回答重新分析”。如果你觉得 AI 当前默认改造已经够用，也可以点击右上角按钮直接继续审批。已有密码、密钥不需要在这里填写。</p>
        {questions.map((q, i) => (
          <label key={i} className="block space-y-2">
            <span className="block font-medium">{i + 1}. {questionText(q)}</span>
            <textarea
              aria-label={`问题 ${i + 1} 的回答`}
              className="w-full bg-panel border border-line rounded-lg p-2 text-ui"
              rows={2}
              value={answers[i] || ""}
              onChange={e => setAnswers(previous => ({ ...previous, [i]: e.target.value }))}
              placeholder="填写你的选择或补充说明；连接地址和凭据在下一步确认"
            />
          </label>
        ))}
        <button
          className="px-4 py-2 rounded-lg bg-accent text-white disabled:opacity-40"
          disabled={busy || !Object.values(answers).some(a => a.trim())}
          onClick={retry}
        >
          {busy ? "正在提交…" : "根据回答重新分析"}
        </button>
      </section>}

      {/* 卡片 2：服务器基础服务替换与数据迁移指南 */}
      {(availableResources.length > 0 || hasLocalFileStorageWarning) && (
        <section className="rounded-xl border border-line bg-paper/40 p-4 space-y-3" aria-label="服务器基础服务与迁移指南">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-semibold">2. 服务器现成基础服务与数据迁移说明</h3>
            <span className="text-xs text-muted">无需在此处填密码 · 在下一步「3. 配置资源」中一键选择</span>
          </div>
          <p className="text-xs text-muted leading-relaxed">
            平台服务器已内置现成的共享基础服务。如果应用原本连接的是你电脑本机的数据库/缓存（如 <code>127.0.0.1</code>），部署到服务器后将无法连回你电脑，建议在下一步切换为服务器现成服务，并留意以下数据与表结构迁移事项：
          </p>

          {hasLocalFileStorageWarning && (
            <div className="rounded-lg border border-warnInk/40 bg-warnSoft/50 p-3 space-y-2 text-xs">
              <div className="font-semibold text-ink">⚠️ 检测到本地文件 / JSON 存储：重新部署时容器内文件会重置</div>
              <p className="text-muted leading-relaxed">
                当前应用把业务数据或上传文件保存在容器本地目录中。<b>如果仅做功能演示或短期试点</b>，可直接批准下方修改并继续；<b>如果要正式长期使用</b>，建议让 AI 将存储改造为平台现成的共享 PostgreSQL 数据库或 MinIO 对象存储。
              </p>
              {!ready && (
                <div className="flex flex-wrap gap-2 pt-1">
                  <button
                    type="button"
                    onClick={() => applyQuickFeedback("请把本地 JSON/文件存储改造为使用平台共享 PostgreSQL 数据库（通过 DATABASE_URL 环境变量连接），并在应用启动时自动创建所需表结构；上传附件支持使用 S3/MinIO。")}
                    className="px-2.5 py-1 rounded-md border border-accent/40 bg-accentSoft/30 text-accent hover:bg-accentSoft/50 font-medium"
                  >
                    + 让 AI 改用服务器数据库/对象存储并自动建表
                  </button>
                </div>
              )}
            </div>
          )}

          {availableResources.length > 0 && (
            <div className="grid grid-cols-1 gap-2.5">
              {availableResources.map(res => {
                const hint = migrationHintForResource(res);
                const matchedKeys = proposal.configs.filter(c => res.env_keys.includes(c.key)).map(c => c.key);
                return (
                  <div key={res.id} className="rounded-lg border border-line bg-panel p-3 space-y-1.5 text-xs">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-semibold text-ink">{res.name} <code className="text-muted font-normal">({res.type})</code></span>
                      <span className="px-2 py-0.5 rounded bg-paper border border-line text-muted">{hint.badge}</span>
                    </div>
                    {res.description && <p className="text-muted">{res.description}</p>}
                    <p className="text-ink leading-relaxed"><b>迁移与使用提醒：</b>{hint.migration}</p>
                    <p className="text-muted">
                      {matchedKeys.length > 0
                        ? `✓ 已匹配当前应用配置项：${matchedKeys.join("、")}（可在下一步直接一键替换）`
                        : `支持注入环境变量：${res.env_keys.slice(0, 4).join("、")}${res.env_keys.length > 4 ? " 等" : ""}`}
                    </p>
                  </div>
                );
              })}
            </div>
          )}

          {!ready && (
            <div className="flex flex-wrap items-center gap-2 pt-1 text-xs">
              <span className="text-muted">需要 AI 协助处理表结构或连接适配？点击填入下方要求框：</span>
              <button
                type="button"
                onClick={() => applyQuickFeedback("请检查数据库初始化逻辑：确保切换到服务器空数据库时，应用启动能自动创建所需表结构（CREATE TABLE IF NOT EXISTS 或自动执行迁移），避免连上新库后报错。")}
                className="px-2.5 py-1 rounded-md border border-line bg-panel hover:bg-paper text-ink"
              >
                + 让 AI 补充启动自动建表逻辑
              </button>
              <button
                type="button"
                onClick={() => applyQuickFeedback("请支持通过环境变量 DATABASE_URL、REDIS_URL、S3_ENDPOINT 读取数据库、缓存和对象存储配置，方便下一步直接绑定服务器共享资源。")}
                className="px-2.5 py-1 rounded-md border border-line bg-panel hover:bg-paper text-ink"
              >
                + 让 AI 对齐服务器资源环境变量
              </button>
            </div>
          )}
        </section>
      )}

      {/* 卡片 3：需要同意的修改与运行配置预览 */}
      <section className="rounded-xl border border-line p-4 space-y-3" aria-label="文件修改审批">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-semibold">
            3. 需要你同意的修改 · {proposal.changes.length} 项
            {!ready && proposal.changes.length > 0 ? `，已批准 ${approved.length} 项` : ""}
          </h3>
          {!ready && (proposal.changes.length > 0 || !configConfirmed) && (
            <button
              type="button"
              onClick={toggleSelectAll}
              className="text-xs px-3 py-1 rounded-lg border border-accent/40 bg-accentSoft/20 text-accent hover:bg-accentSoft/40 font-medium"
            >
              {allChangesSelected ? "取消全选" : "一键全选修改并确认方案"}
            </button>
          )}
        </div>

        {proposal.changes.length === 0 && (
          <p className="text-muted text-xs">
            {questions.length
              ? "模型尚未给出可执行的文件修改。先回答上方问题，再生成完整方案。"
              : "无需修改源码，将校验现有部署文件并生成交付包。"}
          </p>
        )}

        {proposal.changes.map(change => (
          <div key={change.id} className="border-t border-line pt-3">
            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="checkbox"
                disabled={ready || busy}
                checked={ready || approved.includes(change.id)}
                onChange={e => setApproved(previous => e.target.checked ? [...previous, change.id] : previous.filter(id => id !== change.id))}
              />
              <span className="min-w-0">
                <b className="break-words font-medium">{changeTitle(change)}</b>
                <span className="block text-xs text-muted mt-0.5">{change.reason}</span>
                <span className="block text-xs text-muted font-mono break-all mt-1">涉及文件：{change.path}</span>
              </span>
            </label>
            <details className="mt-2">
              <summary className="cursor-pointer text-muted text-xs hover:text-ink">查看修改前后代码差异</summary>
              <pre className="text-xs whitespace-pre-wrap break-all max-h-64 overflow-auto bg-paper p-3 mt-2 rounded-lg">{change.diff}</pre>
            </details>
          </div>
        ))}

        {/* 配置适配预览（直观展示哪些保留原值、哪些由 AI 调整为服务器运行默认值） */}
        {proposal.configs.length > 0 && (
          <div className="border-t border-line pt-3 space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs font-semibold text-ink">服务器运行配置预览（共 {proposal.configs.length} 项 · 下一步可核对和修改最终值）</span>
              <span className="text-xs text-muted">原密码/连接默认保留，服务器不兼容项已自动适配</span>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {proposal.configs.map(config => (
                <div key={config.key} className="rounded-lg border border-line bg-paper/60 p-2.5 text-xs break-words space-y-1">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono font-semibold text-ink">{config.key}{config.required ? " · 必填" : ""}</span>
                    <span className={`px-1.5 py-0.5 rounded text-[11px] ${config.has_original_value ? "bg-okSoft text-ok" : "bg-panel border border-line text-muted"}`}>
                      {config.has_original_value
                        ? "保留原配置值"
                        : config.secret
                        ? "原值尚未关联，下一步确认；不会从分析文本中读取凭据"
                        : config.default
                        ? `AI 适配默认: ${config.default}`
                        : "下一步填写"}
                    </span>
                  </div>
                  {config.description && <p className="text-muted">{config.description}</p>}
                  {config.source && <p className="text-muted break-all">来源：{config.source.path} / {config.source.keys.join(".")}</p>}
                </div>
              ))}
            </div>
          </div>
        )}

        <label className="flex gap-2 items-start border-t border-line pt-3 cursor-pointer">
          <input
            type="checkbox"
            disabled={ready || busy}
            checked={ready || configConfirmed}
            onChange={e => setConfigConfirmed(e.target.checked)}
          />
          <span>我确认上述改造方案；未要求变更的原配置保持原值，所有连接替换在下一步由我选择确认</span>
        </label>

        {!ready && <>
          {blockers.length > 0 && (
            <ul className="text-xs text-muted space-y-1 list-disc pl-5" aria-label="继续前需要完成">
              {blockers.map(b => <li key={b}>{b}</li>)}
            </ul>
          )}
          <div className="flex flex-wrap items-center gap-3 pt-1">
            <button
              className="px-4 py-2 rounded-lg bg-accent text-white font-medium disabled:opacity-40"
              onClick={approve}
              disabled={busy || job?.status !== "review" || blockers.length > 0}
            >
              {busy ? "正在校验并生成…" : "批准所选修改并生成交付包"}
            </button>
          </div>
        </>}

        {ready && (
          <div className="rounded-lg border border-okLine bg-okSoft/40 p-3 flex flex-wrap items-center justify-between gap-3">
            <div className="text-xs text-ok font-medium">
              ✓ 交付包与业务技能已生成就绪！接下来请核对最终生效的配置值与服务器资源。
            </div>
            {onNextStep && (
              <button
                type="button"
                onClick={onNextStep}
                className="px-4 py-1.5 rounded-lg bg-accent text-white text-xs font-medium hover:opacity-90"
              >
                前往第 3 步：配置资源 →
              </button>
            )}
          </div>
        )}
      </section>

      {/* 卡片 4：补充说明或要求调整方案（直接展开，方便用户随时提要求重分析） */}
      {!ready && job && ["review", "failed"].includes(job.status) && (
        <section className="rounded-xl border border-line p-4 space-y-2.5" aria-label="补充要求重新分析">
          <h3 className="font-semibold text-sm">4. 对方案有其他要求？告诉 AI 重新分析</h3>
          <p className="text-xs text-muted">
            你可以用大白话告诉 AI 需要调整什么（例如：“改用服务器的 PostgreSQL 数据库并自动建表”、“把默认端口改成 8080”），AI 会在保留之前确认结论的基础上重新生成方案。请勿在此填写密码和密钥。
          </p>
          <textarea
            id="app-analysis-feedback"
            aria-label="补充信息或调整要求"
            className="w-full bg-panel border border-line rounded-lg p-2.5 text-ui"
            rows={3}
            placeholder="例如：请改用服务器上的共享 PostgreSQL 数据库，并在启动时自动初始化表结构…"
            value={feedback}
            onChange={e => setFeedback(e.target.value)}
          />
          <button
            className="px-3.5 py-2 rounded-lg border border-line bg-paper hover:bg-panel text-ink text-xs font-medium disabled:opacity-40"
            disabled={busy}
            onClick={retry}
          >
            重新分析，生成新提案
          </button>
        </section>
      )}

      {/* 折叠区：更多技术详情与外部 AI 交接 */}
      <div className="space-y-2 pt-1">
        <p className="text-xs font-medium text-muted px-1">更多技术详情（可选查看）</p>
        <details className="rounded-xl border border-line p-4">
          <summary className="cursor-pointer font-medium text-xs">部署运行须知与问题详情 · {proposal.issues.length} 项</summary>
          <div className="mt-3 space-y-3 text-xs">
            {proposal.issues.map(issue => (
              <div key={issue.id} className="border-t border-line pt-3">
                <div className="font-medium">{issue.id}：{issue.impact}</div>
                <p className="mt-1">{issue.recommendation}</p>
                <p className="text-muted break-words mt-1">依据：{questionText(issue.evidence)}</p>
              </div>
            ))}
          </div>
        </details>

        <details className="rounded-xl border border-line p-4">
          <summary className="cursor-pointer font-medium text-xs">生成的业务技能 · {proposal.apis.length} 个接口，{proposal.pages.length} 个页面</summary>
          <p className="text-xs text-muted mt-3">部署期间仅验证明确只读的操作，不执行写入、删除或外部流水线。</p>
          {proposal.apis.map(api => (
            <div key={api.id} className="text-xs py-3 border-b border-line">
              <code className="break-all">{api.method} {api.path}</code>
              <p className="mt-1">{api.purpose}</p>
              <p className="text-muted mt-1">{effects[api.effect] || api.effect}</p>
            </div>
          ))}
          {proposal.pages.map(page => (
            <div key={page.path} className="text-xs py-2">{page.path} · {page.purpose}</div>
          ))}
        </details>

        <details className="rounded-xl border border-line p-4" aria-label="交给自己的 AI 修改">
          <summary className="font-medium cursor-pointer text-xs">想让自己的 AI 来改？复制交接提示词</summary>
          <p className="text-xs text-muted mt-3">复制后，粘贴给你电脑上的 Codex、Kimi 等 AI。它会根据建议检查和修改；改好后回到这里重新选择文件夹或 ZIP，再分析、确认和部署。</p>
          <button className="px-3 py-2 mt-3 border border-line rounded-lg text-xs" onClick={copyHandoff}>{copied ? "已复制交接提示词" : "复制交接提示词"}</button>
          {handoff && <textarea aria-label="交接提示词" readOnly value={handoff} rows={8} className="w-full mt-3 bg-paper border border-line rounded-lg p-3 text-xs" />}
          {onReplaceSource && <button className="block mt-3 text-accent underline text-xs" onClick={onReplaceSource}>已经改好了，重新选择源码</button>}
        </details>
      </div>
    </>}

    {!proposal && !ready && job && ["review", "failed"].includes(job.status) && (
      <details className="rounded-xl border border-line p-4" open={job.status === "failed"}>
        <summary className="cursor-pointer font-medium">补充说明或要求调整方案</summary>
        <p className="text-xs text-muted mt-3">说明要保留或调整的行为，不要填写密码和密钥。</p>
        <textarea id="app-analysis-feedback" aria-label="补充信息或调整要求" className="w-full bg-panel border border-line rounded-lg p-2 mt-2" rows={3} value={feedback} onChange={e => setFeedback(e.target.value)} />
        <button className="px-3 py-2 rounded-lg border border-line mt-2 disabled:opacity-40" disabled={busy} onClick={retry}>重新分析，生成新提案</button>
      </details>
    )}

    {!!job?.events?.length && (
      <details className="text-xs text-muted">
        <summary className="cursor-pointer">查看分析执行记录（{job.events.length}）</summary>
        <pre className="whitespace-pre-wrap max-h-40 overflow-auto mt-2">{job.events.join("\n")}</pre>
      </details>
    )}
    {ready && <p className="text-muted text-xs">结构校验已通过；容器构建、启动与业务验证将在部署阶段执行。</p>}
  </section>;
}
