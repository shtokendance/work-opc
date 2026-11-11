import { useEffect, useRef, useState } from "react";
import { appDeliveryRequest, getAppDeliveryJob, type AppDeliveryJob, type AppConfigItem, type ResourceSummary } from "../api";
import { AppDeliveryConfig, needsConnectionDecision } from "./AppDeliveryConfig";
import { Icon, type IconName } from "./Icon";

type DimensionMeta = {
  label: string;
  subTitle: string;
  icon: IconName;
  badgeClass: string;
};

function getDimensionMeta(tag: string): DimensionMeta {
  const t = tag.trim();
  if (/应用定位|业务用途|项目用途|定位/.test(t)) {
    return {
      label: "应用定位",
      subTitle: "业务用途与定位",
      icon: "sparkle",
      badgeClass: "bg-accentSoft text-accent border border-accent/25",
    };
  }
  if (/运行环境|技术栈|开发语言|运行时|环境/.test(t)) {
    return {
      label: "运行环境",
      subTitle: "开发语言与技术栈",
      icon: "code",
      badgeClass: "bg-tealSoft text-tealInk border border-tealLine/30",
    };
  }
  if (/接口与页面|页面与接口|页面与业务视图|视图与路由/.test(t)) {
    return {
      label: "接口与页面",
      subTitle: "全景契约与视图",
      icon: "table",
      badgeClass: "bg-warnSoft text-warnInk border border-warnInk/25",
    };
  }
  if (/后端\s*API|API\s*接口|服务接口|接口清单/.test(t)) {
    return {
      label: "后端接口",
      subTitle: "API 接口全景",
      icon: "plug",
      badgeClass: "bg-accentSoft text-accent border border-accent/25",
    };
  }
  if (/页面与视图|页面清单|业务视图/.test(t)) {
    return {
      label: "页面视图",
      subTitle: "业务视图全景",
      icon: "table",
      badgeClass: "bg-warnSoft text-warnInk border border-warnInk/25",
    };
  }
  if (/登录与鉴权|鉴权与安全|安全凭据|权限与凭据/.test(t)) {
    return {
      label: "登录与鉴权",
      subTitle: "权限与安全策略",
      icon: "shield",
      badgeClass: "bg-okSoft text-ok border border-okLine/40",
    };
  }
  if (/数据与存储|存储与数据库|持久化|数据库/.test(t)) {
    return {
      label: "数据与存储",
      subTitle: "数据存储与持久化",
      icon: "archive",
      badgeClass: "bg-panel text-ink border border-lineStrong/40",
    };
  }
  return {
    label: t,
    subTitle: "分析明细",
    icon: "diamond",
    badgeClass: "bg-panel text-muted border border-line",
  };
}

function renderFormattedSummary(rawText: string) {
  const trimmed = (rawText || "").trim();
  if (!trimmed) return null;

  // Short single-sentence text (e.g. unit test fixtures or brief notes): render directly
  if (
    trimmed.length <= 50 &&
    !trimmed.includes("\n") &&
    !trimmed.includes("【") &&
    !/第[一二三四]步/.test(trimmed)
  ) {
    return <span>{trimmed}</span>;
  }

  // Split by newlines first; if a line is still a jammed paragraph, split by step markers or sentences
  const rawLines = trimmed
    .split(/\n+/)
    .map(l => l.trim())
    .filter(Boolean);

  const segments: string[] = [];
  for (const line of rawLines) {
    if (!line.startsWith("【") && (/第[一二三四]步/.test(line) || line.length > 60)) {
      const byStep = line
        .split(/(?=(?:第一步(?:发现)?[：:]?|第二步(?:拟|改造)?[：:]?|第三步(?:契约)?[：:]?|第四步[：:]?|请确认))/)
        .map(s => s.trim())
        .filter(Boolean);
      if (byStep.length > 1) {
        for (const part of byStep) {
          if (part.length > 110) {
            const subParts = part
              .split(/(?<=[。；])\s*/)
              .map(s => s.trim())
              .filter(Boolean);
            segments.push(...subParts);
          } else {
            segments.push(part);
          }
        }
        continue;
      }
      const bySentence = line
        .split(/(?<=[。；])\s*/)
        .map(s => s.trim())
        .filter(Boolean);
      if (bySentence.length > 1) {
        segments.push(...bySentence);
        continue;
      }
    }
    segments.push(line);
  }

  if (segments.length <= 1 && !trimmed.includes("【")) {
    return <span>{trimmed}</span>;
  }

  // Group items into blocks: structured bracket cards vs normal text/step items
  type Block =
    | { type: "cards"; items: { tag: string; content: string }[] }
    | { type: "step"; label: string; text: string }
    | { type: "bullet"; text: string };

  const blocks: Block[] = [];
  let pendingCards: { tag: string; content: string }[] = [];

  const flushCards = () => {
    if (pendingCards.length > 0) {
      blocks.push({ type: "cards", items: pendingCards });
      pendingCards = [];
    }
  };

  for (const seg of segments) {
    const bracketMatch = seg.match(/^【([^】]+)】\s*(.*)$/);
    if (bracketMatch) {
      pendingCards.push({ tag: bracketMatch[1], content: bracketMatch[2] });
      continue;
    }
    flushCards();

    const stepMatch = seg.match(/^(第[一二三四]步[^：:，。]{0,6}|请确认)[：:]?\s*(.*)$/);
    if (stepMatch && stepMatch[2]) {
      blocks.push({ type: "step", label: stepMatch[1], text: stepMatch[2] });
      continue;
    }
    blocks.push({ type: "bullet", text: seg.replace(/^[•\-*]\s*/, "") });
  }
  flushCards();

  return (
    <div className="space-y-3 text-xs leading-relaxed">
      {blocks.map((block, bIdx) => {
        if (block.type === "cards") {
          return (
            <div key={bIdx} className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 pt-1">
              {block.items.map((item, itemIdx) => {
                const meta = getDimensionMeta(item.tag);
                const isWide = item.content.length > 110 || (block.items.length % 2 !== 0 && itemIdx === block.items.length - 1);
                return (
                  <div
                    key={itemIdx}
                    className={`rounded-xl border border-line/75 bg-paper/95 p-3.5 shadow-2xs hover:border-lineStrong/50 hover:shadow-xs transition-all space-y-2 flex flex-col justify-between ${
                      isWide ? "sm:col-span-2" : ""
                    }`}
                  >
                    <div className="flex items-center justify-between pb-1.5 border-b border-line/50">
                      <div className="flex items-center gap-1.5">
                        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md font-semibold text-[11px] ${meta.badgeClass}`}>
                          <Icon name={meta.icon} size={12} />
                          <span>{meta.label}</span>
                        </span>
                        <span className="font-semibold text-xs text-ink">{meta.subTitle}</span>
                      </div>
                    </div>
                    <p className="text-xs text-ink/90 leading-relaxed font-normal whitespace-pre-wrap">
                      {item.content}
                    </p>
                  </div>
                );
              })}
            </div>
          );
        }

        if (block.type === "step") {
          return (
            <div key={bIdx} className="flex items-start gap-2.5 rounded-xl bg-paper/90 border border-line/70 p-3 shadow-2xs">
              <span className="shrink-0 inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-accentSoft text-accent font-semibold text-[11px] border border-accent/20">
                <Icon name="sparkle" size={11} />
                <span>{block.label}</span>
              </span>
              <span className="text-ink flex-1 leading-relaxed">{block.text}</span>
            </div>
          );
        }

        return (
          <div key={bIdx} className="flex items-start gap-2 pl-1">
            <span className="text-accent font-bold shrink-0 mt-0.5">•</span>
            <span className="text-ink flex-1 leading-relaxed">{block.text}</span>
          </div>
        );
      })}
    </div>
  );
}

export function AppDeliveryReview({
  jobId,
  onReady,
  onReplaceSource,
  onNextStep,
  onInvalidate,
  initialFeedback = "",
  repairMode = false,
  onlineResources = [],
  footerMode = "inline",
  onStepStateChange,
}: {
  jobId: string;
  onReady: (job: AppDeliveryJob) => void;
  onReplaceSource?: () => void;
  onNextStep?: () => void;
  onlineResources?: ResourceSummary[];
  onInvalidate?: () => void;
  initialFeedback?: string;
  repairMode?: boolean;
  footerMode?: "inline" | "external";
  onStepStateChange?: (state: { canContinue: boolean; running: boolean; triggerNext: () => void }) => void;
}) {
  const [job, setJob] = useState<AppDeliveryJob | null>(null);
  const [feedback, setFeedback] = useState(initialFeedback);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [handoff, setHandoff] = useState<{ prompt: string; source_path?: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [configs, setConfigs] = useState<AppConfigItem[]>([]);
  const [configEditing, setConfigEditing] = useState(false);
  const configurationVersion = useRef("");
  const preparing = useRef("");
  const inFlight = useRef(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const current = useRef(job);
  current.current = job;

  const accept = (result: AppDeliveryJob) => {
    setJob(result);
    if (result.status === "ready" && result.artifact_hash !== configurationVersion.current) {
      configurationVersion.current = result.artifact_hash || "";
      setConfigs(result.config_draft && result.config_draft.artifact_hash === result.artifact_hash ? result.config_draft.configs : result.runtime_configs || []);
      setConfigEditing(false);
    }
  };

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      if (inFlight.current) return;
      try {
        const result = await getAppDeliveryJob(jobId);
        if (alive && !inFlight.current) accept(result);
      } catch (e) { if (alive) setError(String(e)); }
    };
    void poll();
    const timer = setInterval(poll, 1500);
    return () => { alive = false; clearInterval(timer); };
  }, [jobId]);

  const plan = job?.proposal;
  const ready = job?.status === "ready";
  const running = busy || ["created", "analyzing", "applying"].includes(job?.status || "");
  const questions = plan?.unresolved || [];
  const text = (q: unknown) => typeof q === "string" ? q : JSON.stringify(q);
  const problemKeys = new Set((job?.configuration_problems || []).map(p => p.key));
  const configProblems = configs.filter(c => (c.required && !c.value && !c.resource_id) || needsConnectionDecision(c) || (c.key === "PORT" && c.value && c.value !== String(job?.port || 8080)));
  const needsConfig = ready && (configEditing || problemKeys.size > 0 || configProblems.length > 0);
  const canContinue = ready && !running && !questions.length && !needsConfig && !feedback.trim() && !error;
  const activeError = error || job?.error || "";

  const sendFeedback = async (customText?: string) => {
    const content = (customText ?? feedback).trim();
    if (!content || running) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      await appDeliveryRequest(`jobs/${jobId}/analyze`, { feedback: content, ...((repairMode || job?.analysis_mode === "repair") ? { mode: "repair" } : {}) });
      onInvalidate?.();
      preparing.current = "";
      configurationVersion.current = "";
      setJob(j => j ? {
        ...j,
        status: "analyzing",
        error: "",
        discovery_report: j.proposal?.discovery_report || j.discovery_report,
        proposal: undefined,
        artifact_hash: undefined,
        preparation_checks: undefined,
        conversation: [...(j.conversation || []), { role: "user", text: content, revision: j.revision || 1 }],
      } : j);
      if (customText === undefined) setFeedback("");
      setHandoff(null);
      setCopied(false);
    } catch (e) {
      setError(String(e));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };

  const retry = () => sendFeedback();

  const handleAiFixError = () => {
    if (!activeError || running) return;
    const prompt = "请处理这次未完成的问题，保留原业务和已确认的选择：\n" + activeError;
    void sendFeedback(prompt);
  };

  const prepare = async () => {
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await appDeliveryRequest<AppDeliveryJob>(`jobs/${jobId}/prepare`, { proposal_hash: current.current?.proposal_hash });
      accept(result);
      if (result.status === "analyzing") preparing.current = "";
      else if (result.status !== "ready") setError(result.error || "改造未完成，请让 AI 调整方案");
    } catch (e) {
      setError(String(e));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };

  const awaitingDiscoveryConfirm = job?.status === "review" && !!plan && !questions.length && !activeError && !running;
  const discovery = plan?.discovery_report || job?.discovery_report;
  const transformation = plan?.transformation_report || job?.transformation_report;
  const autoPreparedHashRef = useRef("");

  // When user has already answered AI questions in a previous round (revision > 1) and the updated proposal has 0 unresolved questions,
  // automatically execute Step 2 technical preparation so the user does not have to confirm Step 1 a second time.
  useEffect(() => {
    const rev = job?.revision || 1;
    const hasUserReply = (job?.conversation || []).some(m => m.role === "user");
    const pHash = job?.proposal_hash || "";
    if (
      awaitingDiscoveryConfirm &&
      rev > 1 &&
      hasUserReply &&
      pHash &&
      autoPreparedHashRef.current !== pHash
    ) {
      autoPreparedHashRef.current = pHash;
      void prepare();
    }
  }, [awaitingDiscoveryConfirm, job?.revision, job?.proposal_hash, job?.conversation]);

  const saveConfig = async () => {
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      await appDeliveryRequest(`jobs/${jobId}/config-draft`, { artifact_hash: job?.artifact_hash, configs });
      const result = await getAppDeliveryJob(jobId);
      accept(result);
      setConfigEditing(false);
    } catch (e) {
      setError(String(e));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };

  const next = async () => {
    if (!canContinue) return;
    setBusy(true);
    try {
      const result = await getAppDeliveryJob(jobId);
      if (result.status !== "ready" || result.artifact_hash !== job?.artifact_hash || result.configuration_problems?.length) {
        accept(result);
        throw new Error("应用或配置已变化，请先处理当前问题。");
      }
      onReady(result);
      onNextStep?.();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const nextRef = useRef(next);
  nextRef.current = next;
  useEffect(() => {
    onStepStateChange?.({
      canContinue: Boolean(canContinue),
      running: Boolean(running),
      triggerNext: () => {
        void nextRef.current();
      },
    });
  }, [canContinue, running, job?.artifact_hash, onStepStateChange]);

  const button = "rounded-lg border border-line px-3.5 py-1.5 text-ui transition-colors disabled:opacity-40 cursor-pointer";
  const title = running
    ? "AI 正在检查和改造"
    : activeError
    ? "有一步没有完成"
    : questions.length
    ? `有 ${questions.length} 件事需要你决定`
    : awaitingDiscoveryConfirm
    ? "第一步分析完成，请确认分析结果后执行改造"
    : needsConfig
    ? "补齐连接配置，就可以继续"
    : ready
    ? "应用已准备好，可以核对部署内容"
    : "正在读取应用";

  const conversation = job?.conversation || [];
  const events = job?.events || [];
  const chatScrollRef = useRef<HTMLDivElement>(null);
  const eventsScrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll chat container to bottom when messages or steps update
  useEffect(() => {
    const scrollChat = () => {
      if (chatScrollRef.current) {
        if (typeof chatScrollRef.current.scrollTo === "function") {
          chatScrollRef.current.scrollTo({
            top: chatScrollRef.current.scrollHeight,
            behavior: "smooth",
          });
        } else {
          chatScrollRef.current.scrollTop = chatScrollRef.current.scrollHeight;
        }
      }
    };
    scrollChat();
    const timer = setTimeout(scrollChat, 40);
    return () => clearTimeout(timer);
  }, [events.length, conversation.length, running]);

  // Auto-scroll internal AI progress timeline to latest step
  useEffect(() => {
    const scrollEvents = () => {
      if (eventsScrollRef.current) {
        if (typeof eventsScrollRef.current.scrollTo === "function") {
          eventsScrollRef.current.scrollTo({
            top: eventsScrollRef.current.scrollHeight,
            behavior: "smooth",
          });
        } else {
          eventsScrollRef.current.scrollTop = eventsScrollRef.current.scrollHeight;
        }
      }
    };
    scrollEvents();
    const timer = setTimeout(scrollEvents, 40);
    return () => clearTimeout(timer);
  }, [events.length, events[events.length - 1], running]);

  return (
    <section className="w-full max-w-4xl mx-auto flex-1 flex flex-col min-h-0 text-ui rounded-2xl border border-line bg-paper/60 p-4 sm:p-5 shadow-2xs overflow-hidden">
      {(repairMode || job?.analysis_mode === "repair") && (
        <aside aria-label="部署失败修复状态" className="mb-3 rounded-lg border border-line bg-panel p-3 text-meta">
          <strong>{running ? "正在诊断失败并核对修复" : ready ? "修复包已生成，等待部署验证" : "部署失败专项修复"}</strong>
          <p className="mt-1 text-muted">对照失败制品与原始源码，保留业务和已有配置。提案、语法检查、服务器启动和业务验证分别记录。</p>
          {job?.failed_artifact_sha && <p className="mt-1 text-muted break-all">失败制品指纹：{job.failed_artifact_sha.slice(0, 16)}…</p>}
        </aside>
      )}
      {/* Top Status Bar */}
      <header
        className="flex items-center justify-between gap-3 pb-3 mb-3 border-b border-line shrink-0"
        aria-live="polite"
      >
        <div className="flex items-center gap-2.5">
          <span
            className={`w-2.5 h-2.5 rounded-full shrink-0 ${
              running
                ? "bg-accent animate-pulse"
                : activeError
                ? "bg-danger"
                : questions.length || needsConfig
                ? "bg-warnInk"
                : ready
                ? "bg-ok"
                : "bg-muted"
            }`}
          />
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-semibold text-ink">{title}</h3>
              <span className="px-2 py-0.5 rounded-full bg-paper border border-line text-xs text-muted">
                第 {job?.revision || 1} 轮 · {job?.name || "准备应用"}
              </span>
            </div>
            <p className="text-xs text-muted mt-0.5">
              {running
                ? "AI 会自动处理启动与打包等技术问题；需要你决定时会像聊天一样向你确认。"
                : ready && !needsConfig
                ? "已完成改造和交付包检查。服务器构建、启动与业务访问将在部署时验证。"
                : "直接在下方对话框回答或提出想法，无需阅读或审核代码。"}
            </p>
          </div>
        </div>
      </header>

      {/* Chat Messages Stream */}
      <div ref={chatScrollRef} className="flex-1 overflow-y-auto min-h-0 space-y-4 pr-1 pb-4" aria-label="讨论记录">
        {conversation.map((m, i) => {
          const isUser = m.role === "user";
          if (isUser) {
            return (
              <article key={i} className="flex flex-col items-end">
                <span className="text-xs text-muted mb-1 px-1">你</span>
                <div className="max-w-[85%] rounded-2xl rounded-tr-xs bg-accentSoft border border-accent/20 px-4 py-3 text-ink whitespace-pre-wrap leading-relaxed">
                  {m.text}
                </div>
              </article>
            );
          }
          return (
            <article key={i} className="flex items-start gap-3">
              <div className="w-7 h-7 rounded-lg bg-accent/10 border border-accent/20 text-accent flex items-center justify-center shrink-0 mt-0.5">
                <Icon name="sparkle" size={14} />
              </div>
              <div className="flex-1 min-w-0 space-y-1">
                <p className="text-xs text-muted">
                  AI 改造助手 · 第 {m.revision || job?.revision || 1} 轮{((m.revision || 1) > 1) ? "确认与改造" : "分析"}
                </p>
                <div className="rounded-2xl rounded-tl-xs border border-line bg-paper/80 px-4 py-3.5 text-ink whitespace-pre-wrap leading-relaxed">
                  {renderFormattedSummary(m.text)}
                </div>
              </div>
            </article>
          );
        })}

        {!conversation.length && plan && (
          <article className="flex items-start gap-3">
            <div className="w-7 h-7 rounded-lg bg-accent/10 border border-accent/20 text-accent flex items-center justify-center shrink-0 mt-0.5">
              <Icon name="sparkle" size={14} />
            </div>
            <div className="flex-1 min-w-0 space-y-1">
              <p className="text-xs text-muted">AI 改造助手 · 第 {job?.revision || 1} 轮分析</p>
              <div className="rounded-2xl rounded-tl-xs border border-line bg-paper/80 px-4 py-3.5 text-ink whitespace-pre-wrap leading-relaxed">
                {renderFormattedSummary(plan.summary)}
              </div>
            </div>
          </article>
        )}

        {/* Step 1 Fixed-Format Deep Discovery Card */}
        {(discovery || (plan && (plan.four_step_report || plan.auth_profile || plan.apis?.length || plan.pages?.length))) && (
          <section
            aria-label="第一步：AI 深度分析结果"
            className="rounded-2xl border border-line bg-paper/95 p-4 sm:p-5 space-y-3.5 text-xs shadow-xs"
          >
            <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-line/70">
              <div className="flex items-center gap-2.5">
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-accentSoft text-accent font-semibold text-xs border border-accent/25">
                  <Icon name="sparkle" size={13} />
                  <span>第一步 · 应用深度分析报告</span>
                </span>
                <span className="font-semibold text-sm text-ink tracking-tight">
                  {job?.name || job?.slug} 全景摸底结果
                </span>
              </div>
              <div className="flex items-center gap-2 text-xs">
                <span className="px-2 py-0.5 rounded-md bg-panel border border-line font-mono text-muted text-[11px]">
                  页面 {discovery?.pages_overview?.total ?? plan?.pages?.length ?? 0} 个
                </span>
                <span className="px-2 py-0.5 rounded-md bg-panel border border-line font-mono text-muted text-[11px]">
                  接口 {discovery?.apis_overview?.total ?? plan?.apis?.length ?? 0} 个
                </span>
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              {/* 1. 项目定位 */}
              <div className="rounded-xl border border-line/75 bg-paper/95 p-3.5 shadow-2xs hover:border-lineStrong/50 hover:shadow-xs transition-all space-y-2">
                <div className="flex items-center justify-between pb-1.5 border-b border-line/50">
                  <div className="flex items-center gap-1.5">
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md font-semibold text-[11px] bg-accentSoft text-accent border border-accent/25">
                      <Icon name="sparkle" size={12} />
                      <span>应用定位</span>
                    </span>
                    <span className="font-semibold text-xs text-ink">业务用途与定位</span>
                  </div>
                </div>
                <p className="text-muted leading-relaxed text-xs">
                  {discovery?.project_purpose || plan?.summary || "已完成应用源码结构与核心业务分析"}
                </p>
              </div>

              {/* 2. 语言版本与技术栈 */}
              <div className="rounded-xl border border-line/75 bg-paper/95 p-3.5 shadow-2xs hover:border-lineStrong/50 hover:shadow-xs transition-all space-y-2">
                <div className="flex items-center justify-between pb-1.5 border-b border-line/50">
                  <div className="flex items-center gap-1.5">
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md font-semibold text-[11px] bg-tealSoft text-tealInk border border-tealLine/30">
                      <Icon name="code" size={12} />
                      <span>运行环境</span>
                    </span>
                    <span className="font-semibold text-xs text-ink">开发语言与技术栈</span>
                  </div>
                </div>
                <div className="text-muted space-y-1.5 text-xs">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-ink font-medium">运行时：</span>
                    <span className="font-mono text-ink px-1.5 py-0.5 rounded bg-panel border border-line text-[11px]">
                      {discovery?.tech_stack?.language || plan?.four_step_report?.step1_discovery?.language || "自动识别"}{" "}
                      {discovery?.tech_stack?.runtime_version || plan?.four_step_report?.step1_discovery?.runtime_version || ""}
                    </span>
                    <span className="text-muted text-[11px]">（容器端口 {discovery?.tech_stack?.container_port || job?.port || 8080}）</span>
                  </div>
                  {!!discovery?.tech_stack?.frameworks?.length && (
                    <div>
                      <span className="text-ink font-medium">核心框架：</span>
                      <span>{discovery.tech_stack.frameworks.join(" + ")}</span>
                    </div>
                  )}
                  {!!discovery?.tech_stack?.syntax_requirements?.length && (
                    <div className="text-warnInk">
                      <span className="font-medium">版本特性要求：</span>
                      <span>{discovery.tech_stack.syntax_requirements.join("、")}</span>
                    </div>
                  )}
                </div>
              </div>

              {/* 3. 页面与功能视图全景 */}
              <div className="rounded-xl border border-line/75 bg-paper/95 p-3.5 shadow-2xs hover:border-lineStrong/50 hover:shadow-xs transition-all space-y-2">
                <div className="flex items-center justify-between pb-1.5 border-b border-line/50">
                  <div className="flex items-center gap-1.5">
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md font-semibold text-[11px] bg-warnSoft text-warnInk border border-warnInk/25">
                      <Icon name="table" size={12} />
                      <span>页面视图</span>
                    </span>
                    <span className="font-semibold text-xs text-ink">
                      业务视图（共 {discovery?.pages_overview?.total ?? plan?.pages?.length ?? 0} 个）
                    </span>
                  </div>
                </div>
                {(discovery?.pages_overview?.items || plan?.pages || []).length > 0 ? (
                  <div className="space-y-1.5 max-h-36 overflow-y-auto pr-1">
                    {(discovery?.pages_overview?.items || plan?.pages || []).map((pg, idx) => (
                      <div key={`${pg.path}-${idx}`} className="flex items-start gap-1.5 text-[11px]">
                        <span className="font-mono px-1.5 py-0.5 rounded bg-panel border border-line text-ink shrink-0">
                          {pg.path}
                        </span>
                        <span className="text-muted truncate pt-0.5" title={pg.purpose}>
                          {pg.purpose}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-muted text-xs">纯后端 API 服务（无独立前端页面）</p>
                )}
              </div>

              {/* 4. API 接口全景统计 */}
              <div className="rounded-xl border border-line/75 bg-paper/95 p-3.5 shadow-2xs hover:border-lineStrong/50 hover:shadow-xs transition-all space-y-2">
                <div className="flex items-center justify-between pb-1.5 border-b border-line/50">
                  <div className="flex items-center gap-1.5">
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md font-semibold text-[11px] bg-accentSoft text-accent border border-accent/25">
                      <Icon name="plug" size={12} />
                      <span>后端接口</span>
                    </span>
                    <span className="font-semibold text-xs text-ink">
                      API 接口全景（共 {discovery?.apis_overview?.total ?? plan?.apis?.length ?? 0} 个）
                    </span>
                  </div>
                  {discovery?.apis_overview && (
                    <span className="text-[11px] font-mono text-muted">
                      只读 {discovery.apis_overview.read_count ?? 0} · 写操作 {discovery.apis_overview.write_count ?? 0}
                    </span>
                  )}
                </div>
                {discovery?.apis_overview?.modules && Object.keys(discovery.apis_overview.modules).length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {Object.entries(discovery.apis_overview.modules).map(([mod, cnt]) => (
                      <span key={mod} className="px-1.5 py-0.5 rounded bg-panel border border-line font-mono text-[11px] text-ink">
                        {mod}: {cnt}
                      </span>
                    ))}
                  </div>
                )}
                {(discovery?.apis_overview?.items || plan?.apis || []).length > 0 && (
                  <details className="pt-1">
                    <summary className="cursor-pointer text-accent hover:underline select-none text-xs font-medium">
                      展开查看全部 {(discovery?.apis_overview?.items || plan?.apis || []).length} 个接口清单
                    </summary>
                    <div className="mt-1.5 space-y-1 max-h-44 overflow-y-auto pr-1 border-t border-line/50 pt-1.5">
                      {(discovery?.apis_overview?.items || plan?.apis || []).map((api, idx) => (
                        <div key={`${api.id}-${idx}`} className="flex items-start gap-1.5 text-[11px]">
                          <span className="font-mono font-semibold px-1 rounded bg-panel border border-line text-ink shrink-0">
                            {api.method}
                          </span>
                          <span className="font-mono text-ink shrink-0">{api.path}</span>
                          <span className="text-muted truncate pt-0.5" title={api.purpose}>
                            · {api.purpose}
                          </span>
                        </div>
                      ))}
                    </div>
                  </details>
                )}
              </div>

              {/* 5. 数据存储与数据库 */}
              <div className="rounded-xl border border-line/75 bg-paper/95 p-3.5 shadow-2xs hover:border-lineStrong/50 hover:shadow-xs transition-all space-y-2">
                <div className="flex items-center justify-between pb-1.5 border-b border-line/50">
                  <div className="flex items-center gap-1.5">
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md font-semibold text-[11px] bg-panel text-ink border border-lineStrong/40">
                      <Icon name="archive" size={12} />
                      <span>数据存储</span>
                    </span>
                    <span className="font-semibold text-xs text-ink">数据存储与持久化</span>
                  </div>
                </div>
                <div className="text-muted space-y-1.5 text-xs">
                  <div className="flex items-center gap-1.5">
                    <span className="text-ink font-medium">存储类型：</span>
                    <span className="font-mono text-ink px-1.5 py-0.5 rounded bg-panel border border-line text-[11px]">
                      {discovery?.storage_and_database?.storage_type || plan?.four_step_report?.step1_discovery?.storage_type || "本地/内置存储"}
                    </span>
                  </div>
                  {discovery?.storage_and_database?.sqlite_databases?.map(db => (
                    <div key={db.path} className="text-[11px] bg-panel/60 rounded-lg p-2 border border-line/60 space-y-1">
                      <div className="font-mono text-ink">
                        {db.path} ({db.size_mb} MB · {db.table_count} 张表 · {db.total_rows} 条记录)
                      </div>
                      <details>
                        <summary className="cursor-pointer text-accent hover:underline select-none">
                          查看全部 {db.table_names.length} 张数据表及行数明细
                        </summary>
                        <div className="mt-1.5 flex flex-wrap gap-1 max-h-36 overflow-y-auto pr-1">
                          {(db.tables && db.tables.length > 0
                            ? db.tables
                            : db.table_names.map(n => ({ name: n, row_count: undefined, column_count: undefined }))
                          ).map(t => (
                            <span
                              key={t.name}
                              className="px-1.5 py-0.5 rounded bg-paper border border-line/70 font-mono text-[10px] text-ink"
                            >
                              {t.name}
                              {typeof t.row_count === "number" ? ` (${t.row_count}行)` : ""}
                            </span>
                          ))}
                        </div>
                      </details>
                    </div>
                  ))}
                  {!!discovery?.storage_and_database?.storage_mounts?.length && (
                    <div className="flex flex-wrap gap-1.5 py-0.5">
                      {discovery.storage_and_database.storage_mounts.map((m) => (
                        <span
                          key={m.container_path}
                          title={m.purpose || m.container_path}
                          className="rounded border border-accent/25 bg-accentSoft/30 px-1.5 py-0.5 font-mono text-[11px] text-ink"
                        >
                          📁 {m.container_path} → {m.volume_name || m.name}
                          {typeof m.seed_files_count === "number" && m.seed_files_count > 0 ? ` (种子文件:${m.seed_files_count})` : ""}
                        </span>
                      ))}
                    </div>
                  )}
                  <div>{discovery?.storage_and_database?.storage_migration_plan}</div>
                </div>
              </div>

              {/* 6. 权限验证与容器化现状 */}
              <div className="rounded-xl border border-line/75 bg-paper/95 p-3.5 shadow-2xs hover:border-lineStrong/50 hover:shadow-xs transition-all space-y-2">
                <div className="flex items-center justify-between pb-1.5 border-b border-line/50">
                  <div className="flex items-center gap-1.5">
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md font-semibold text-[11px] bg-okSoft text-ok border border-okLine/40">
                      <Icon name="shield" size={12} />
                      <span>登录鉴权</span>
                    </span>
                    <span className="font-semibold text-xs text-ink">权限与安全策略</span>
                  </div>
                </div>
                <div className="text-muted space-y-1.5 text-xs">
                  <div>
                    <span className="text-ink font-medium">页面/接口鉴权：</span>
                    <span>
                      {discovery?.auth_and_credentials?.page_auth || "免登访问"} · {discovery?.auth_and_credentials?.api_auth || "无特殊鉴权"}
                    </span>
                  </div>
                  {plan?.auth_profile && (plan.auth_profile.username || plan.auth_profile.token) && (
                    <div className="text-ok font-mono bg-okSoft/40 border border-okLine/30 px-2 py-1 rounded-md">
                      已提取内置凭据：{plan.auth_profile.username ? `账号=${plan.auth_profile.username}` : ""}
                      {plan.auth_profile.password ? ` 密码=${plan.auth_profile.password}` : ""}
                      {plan.auth_profile.token ? ` Token已就绪` : ""}
                    </div>
                  )}
                  <div>
                    <span className="text-ink font-medium">Dockerfile 策略：</span>
                    <span>{discovery?.foundation_and_container?.dockerfile_action || "自动生成标准容器构建配置"}</span>
                  </div>
                </div>
              </div>
            </div>

            {/* 拟执行改造动作预览 */}
            {!!discovery?.planned_adaptations?.length && (
              <div className="rounded-xl border border-line/70 bg-panel/30 px-3.5 py-2.5 space-y-1.5">
                <div className="font-semibold text-ink flex items-center gap-1.5">
                  <Icon name="sparkle" size={12} className="text-accent" />
                  <span>第二步拟执行的改造项：</span>
                </div>
                <ul className="list-disc list-inside text-muted space-y-0.5">
                  {discovery.planned_adaptations.map((act, idx) => (
                    <li key={idx}>{act}</li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        )}

        {/* Real-time AI Process Timeline Bubble */}
        {(running || !!events.length) && (
          <article className="flex items-start gap-3">
            <div
              className={`w-7 h-7 rounded-lg border flex items-center justify-center shrink-0 mt-0.5 ${
                running
                  ? "bg-accent/10 border-accent/30 text-accent animate-pulse"
                  : activeError
                  ? "bg-danger/10 border-danger/30 text-danger"
                  : "bg-okSoft border-okLine text-ok"
              }`}
            >
              <Icon name={running ? "sparkle" : activeError ? "warning" : "check"} size={14} />
            </div>
            <div className="flex-1 min-w-0 rounded-2xl rounded-tl-xs border border-line bg-paper/90 p-4 space-y-3 shadow-xs">
              <div className="flex items-center justify-between border-b border-line/60 pb-2.5">
                <div className="flex items-center gap-2">
                  {running ? (
                    <span className="relative flex h-2 w-2">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent opacity-75" />
                      <span className="relative inline-flex rounded-full h-2 w-2 bg-accent" />
                    </span>
                  ) : activeError ? (
                    <span className="inline-block w-2 h-2 rounded-full bg-danger" />
                  ) : (
                    <span className="inline-block w-2 h-2 rounded-full bg-ok" />
                  )}
                  <span className="font-semibold text-xs text-ink">
                    {running
                      ? "AI 正在检查与改造应用…"
                      : activeError
                      ? "AI 改造处理过程（执行中断）"
                      : "AI 改造处理过程记录"}
                  </span>
                </div>
                <span className="text-[11px] text-muted font-mono">
                  {running
                    ? `正在执行第 ${events.length || 1} 步`
                    : activeError
                    ? `执行到第 ${events.length} 步`
                    : `共完成 ${events.length} 个处理步骤`}
                </span>
              </div>

              {/* Step Items List */}
              <div
                ref={eventsScrollRef}
                className="space-y-1.5 max-h-60 overflow-y-auto pr-1 pb-1 text-xs scroll-smooth"
              >
                {events.map((ev, idx) => {
                  const isLatest = idx === events.length - 1;
                  const isFailedStep = !running && activeError && isLatest;
                  return (
                    <div
                      key={idx}
                      className={`flex items-start gap-2.5 rounded-lg px-2.5 py-1.5 transition-colors ${
                        running && isLatest
                          ? "bg-accentSoft border border-accent/30 text-accent font-medium shadow-2xs"
                          : isFailedStep
                          ? "bg-danger/10 border border-danger/25 text-danger font-medium"
                          : "bg-panel/40 text-muted"
                      }`}
                    >
                      <span className="shrink-0 text-[11px] mt-0.5">
                        {running && isLatest ? "▶" : isFailedStep ? "✕" : "✓"}
                      </span>
                      <span className="break-all flex-1 leading-relaxed">
                        {ev}
                      </span>
                    </div>
                  );
                })}
                {!events.length && running && (
                  <div className="flex items-center gap-2 px-2.5 py-1.5 text-xs text-accent">
                    <span className="animate-spin">⏳</span>
                    <span>正在连接 AI 改造引擎与代码沙箱…</span>
                  </div>
                )}
              </div>
            </div>
          </article>
        )}

        {/* Unresolved Decision Questions Bubble */}
        {!!questions.length && (
          <div className="flex items-start gap-3">
            <div className="w-7 h-7 rounded-lg bg-warnSoft border border-warnInk/25 text-warnInk flex items-center justify-center shrink-0 mt-0.5">
              <Icon name="sparkle" size={14} />
            </div>
            <div
              className="flex-1 rounded-2xl rounded-tl-xs border border-line bg-paper p-4 space-y-4"
              aria-label="需要你决定的事项"
            >
              <p className="text-xs font-medium text-warnInk">
                需要你确认以下问题（可点击选项填入下方对话框，或直接打字回答）：
              </p>
              {questions.map((q, i) => {
                const question = text(q);
                const decision = plan?.decisions?.find(d => d.question === question);
                return (
                  <article key={decision?.id || question} className="space-y-2 pt-2 first:pt-0 border-t first:border-t-0 border-line/60">
                    <p className="font-medium text-ink">
                      {questions.length > 1 ? `${i + 1}. ` : ""}
                      {question}
                    </p>
                    {decision?.impact && <p className="text-xs text-muted">{decision.impact}</p>}
                    {!!decision?.options.length && (
                      <div className="flex flex-wrap gap-2 pt-1">
                        {decision.options.map(option => (
                          <button
                            key={option}
                            type="button"
                            disabled={running}
                            className="px-3 py-1.5 rounded-xl border border-accent/30 bg-accentSoft/50 hover:bg-accentSoft text-ink text-xs font-medium transition-colors cursor-pointer disabled:opacity-40"
                            onClick={() => {
                              setFeedback(f => {
                                if (questions.length === 1 && (!f.trim() || decision.options.includes(f.trim()))) return option;
                                const prefix = `问题 ${i + 1}：`;
                                return [...f.split("\n").filter(line => line.trim() && !line.startsWith(prefix)), prefix + option].join("\n");
                              });
                              input.current?.focus();
                            }}
                          >
                            {option}
                          </button>
                        ))}
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          </div>
        )}

        {/* Configuration Needed Bubble */}
        {needsConfig && (
          <div className="flex items-start gap-3">
            <div className="w-7 h-7 rounded-lg bg-warnSoft border border-warnInk/25 text-warnInk flex items-center justify-center shrink-0 mt-0.5">
              <Icon name="sliders" size={14} />
            </div>
            <section className="flex-1 space-y-3 rounded-2xl rounded-tl-xs border border-line bg-paper p-4">
              <h4 className="font-medium text-ink">需要你填写或选择的配置</h4>
              <p className="text-xs text-muted">这些值只在本机处理，不会放入 AI 对话。其他原配置保持不变。</p>
              {job?.configuration_problems?.map(p => (
                <p key={p.key} className="text-sm text-warnInk">
                  {p.reason}
                </p>
              ))}
              <AppDeliveryConfig
                compact
                configs={configs.filter(c => problemKeys.has(c.key) || configProblems.some(p => p.key === c.key))}
                originals={job?.runtime_configs || []}
                resources={[...new Map([...onlineResources, ...(job?.resources || [])].map(r => [r.id, r])).values()]}
                onChange={items => {
                  setConfigs(all => all.map(c => items.find(i => i.key === c.key) || c));
                  setConfigEditing(true);
                  onInvalidate?.();
                }}
              />
              <button
                type="button"
                className={button + " bg-accent text-white border-accent"}
                disabled={running || !!configProblems.length}
                onClick={saveConfig}
              >
                确认配置并检查
              </button>
            </section>
          </div>
        )}

        {/* Error / Unfinished Step Bubble in Chat Stream */}
        {activeError && (
          <div className="flex items-start gap-3">
            <div className="w-7 h-7 rounded-lg bg-danger/10 border border-danger/30 text-danger flex items-center justify-center shrink-0 mt-0.5">
              <Icon name="warning" size={14} />
            </div>
            <div
              role="alert"
              className="flex-1 rounded-2xl rounded-tl-xs border border-danger/30 bg-danger/5 p-4 space-y-3"
            >
              <div className="text-xs font-medium text-danger">技术准备未通过。你已回答的选择会保留，无需重复确认；AI 需要修正下面的检查问题。</div>
              <p className="text-ink whitespace-pre-wrap leading-relaxed font-mono text-xs bg-panel/80 p-3 rounded-xl border border-danger/20">
                {activeError}
              </p>
              {job?.status === "review" && !questions.length && (
                <div className="flex flex-wrap gap-2">
                  <button type="button" className={button + " bg-panel"} disabled={running} onClick={prepare}>
                    重试准备
                  </button>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Step 1 Confirmation Action Bubble */}
        {awaitingDiscoveryConfirm && !needsConfig && (
          <div className="flex items-start gap-3">
            <div className="w-7 h-7 rounded-lg bg-accentSoft border border-accentLine text-accent flex items-center justify-center shrink-0 mt-0.5">
              <Icon name="sparkle" size={14} />
            </div>
            <div className="flex-1 rounded-2xl rounded-tl-xs border border-accentLine bg-accentSoft/20 px-4 py-3 text-ink space-y-2.5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="font-medium text-sm text-ink">第一步深度分析已完成，请确认后进入第二步改造</div>
                  <div className="text-xs text-muted mt-0.5">
                    核对上方「一～六」项分析结果无误后，点击右侧按钮开始执行改造与生成应用技能；如需调整策略，可在下方对话框直接输入要求。
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {discovery?.storage_and_database?.can_migrate_to_platform_pg && (
                    <button
                      type="button"
                      className={button + " bg-panel border-accentLine text-accent font-medium"}
                      disabled={running}
                      onClick={() =>
                        void sendFeedback(
                          "请将本地 SQLite 数据库完整迁移到平台提供的 PostgreSQL 服务端数据库，并导入原有表结构与数据后执行改造。",
                        )
                      }
                    >
                      迁移 SQLite 到平台 PostgreSQL 并改造
                    </button>
                  )}
                  <button
                    type="button"
                    className={button + " bg-accent text-white border-accent font-medium shadow-xs"}
                    disabled={running}
                    onClick={prepare}
                  >
                    确认分析结果，执行第二步改造
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Step 2 Concise Transformation Report & Ready Status Bubble */}
        {ready && !needsConfig && !questions.length && !activeError && !running && (
          <div className="flex items-start gap-3">
            <div className="w-7 h-7 rounded-lg bg-okSoft border border-okLine text-ok flex items-center justify-center shrink-0 mt-0.5">
              <Icon name="check" size={14} />
            </div>
            <div className="flex-1 rounded-2xl rounded-tl-xs border border-okLine bg-okSoft/30 px-4 py-3.5 text-ink space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="font-medium text-sm text-ok">第二步：改造与第三步应用技能生成已完成</div>
                  <div className="text-xs text-muted mt-0.5">
                    {transformation?.headline || (feedback.trim()
                      ? "请先发送输入框中的补充要求，或清空输入后继续。"
                      : "可以点击右下角「下一步」按钮进入部署核对，或在下方对话框继续提要求。")}
                  </div>
                </div>
                {footerMode === "external" ? (
                  <span className="px-2.5 py-1 rounded-full bg-okSoft text-ok border border-okLine text-xs font-medium shrink-0">
                    ✓ 已就绪 · 请点击右下角「下一步」
                  </span>
                ) : (
                  <button
                    type="button"
                    className={button + " bg-accent text-white border-accent font-medium shadow-xs"}
                    disabled={!canContinue}
                    onClick={next}
                  >
                    下一步：核对部署内容
                  </button>
                )}
              </div>

              {transformation && (
                <div
                  aria-label="第二步：应用改造完成报告"
                  className="rounded-xl border border-okLine/60 bg-paper/90 p-3 space-y-2.5 text-xs"
                >
                  <div className="font-medium text-ink">📋 改造简报（增减与改动一览）</div>
                  <div className="grid gap-2 md:grid-cols-2">
                    <div className="rounded-lg border border-line bg-panel/50 p-2.5 space-y-1">
                      <div className="font-medium text-ink">
                        ➕ 新增了什么（{transformation.added_files?.length || 0} 项）
                      </div>
                      {transformation.added_files?.length ? (
                        <ul className="space-y-1 text-muted">
                          {transformation.added_files.map(item => (
                            <li key={item.path}>
                              <span className="font-mono text-ink">{item.path}</span> · {item.reason}
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <div className="text-muted">无新增文件</div>
                      )}
                    </div>
                    <div className="rounded-lg border border-line bg-panel/50 p-2.5 space-y-1">
                      <div className="font-medium text-ink">
                        🔧 改造了什么（{transformation.modified_files?.length || 0} 项）
                      </div>
                      {transformation.modified_files?.length ? (
                        <ul className="space-y-1 text-muted">
                          {transformation.modified_files.map(item => (
                            <li key={item.path}>
                              <span className="font-mono text-ink">{item.path}</span> · {item.reason}
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <div className="text-muted">未改动业务源码（零侵入交付）</div>
                      )}
                    </div>
                  </div>
                  {!!transformation.deleted_files?.length && (
                    <div className="rounded-lg border border-line bg-panel/50 p-2 space-y-1">
                      <div className="font-medium text-ink">
                        🗑️ 删减了什么（{transformation.deleted_files.length} 项）
                      </div>
                      <div className="text-muted font-mono">
                        {transformation.deleted_files.map(d => (typeof d === "string" ? d : `${d.path} (${d.reason})`)).join("、")}
                      </div>
                    </div>
                  )}
                  <div className="grid gap-2 md:grid-cols-2 pt-1">
                    {transformation.storage_summary && (
                      <div className="rounded-lg border border-line bg-panel/40 px-2.5 py-1.5 text-muted">
                        <span className="font-medium text-ink">🗄️ 数据与存储：</span>
                        {transformation.storage_summary}
                      </div>
                    )}
                    {transformation.skill_summary && (
                      <div className="rounded-lg border border-line bg-panel/40 px-2.5 py-1.5 text-muted">
                        <span className="font-medium text-ink">🧩 应用技能：</span>
                        {typeof transformation.skill_summary === "string"
                          ? transformation.skill_summary
                          : `${transformation.skill_summary.skill_name || `app-${job?.slug}`}（含 ${transformation.skill_summary.page_count ?? 0} 个页面、${transformation.skill_summary.api_count ?? 0} 个接口）`}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {!!job?.preparation_checks?.length && (
                <div className="pt-2 border-t border-okLine/40 space-y-1 text-xs">
                  {job.preparation_checks.map(c => (
                    <div key={c.label} className="text-muted flex items-center gap-1.5">
                      <span>{c.status === "passed" ? "✅" : "⏳"}</span>
                      <span className="text-ink font-medium">{c.label}</span>
                      <span>· {c.detail}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Unified Bottom Chat Composer Dock */}
      <div className="shrink-0 pt-3 border-t border-line space-y-2.5 bg-paper/95">
        {/* Handoff Result Banner (if triggered) */}
        {handoff && (
          <div className="rounded-xl border border-line bg-paper p-3 text-xs space-y-1.5">
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium text-ink">已生成交接说明并复制到剪贴板</span>
              <button
                type="button"
                className="text-muted hover:text-ink"
                onClick={() => setHandoff(null)}
              >
                关闭
              </button>
            </div>
            <p className="text-muted break-all">本机独立源码副本：{handoff.source_path}</p>
            <details className="text-muted">
              <summary className="cursor-pointer hover:text-ink">查看交接说明</summary>
              <pre className="whitespace-pre-wrap max-h-40 overflow-auto mt-1.5 p-2 rounded-lg bg-panel font-mono">
                {handoff.prompt}
              </pre>
            </details>
          </div>
        )}

        {/* Quick Actions Toolbar above Composer */}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            {activeError && (
              <button
                type="button"
                disabled={running}
                onClick={handleAiFixError}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-accent text-white text-xs font-medium shadow-xs hover:opacity-90 transition-opacity disabled:opacity-40 cursor-pointer"
              >
                <Icon name="sparkle" size={13} />
                请 AI 帮我处理
              </button>
            )}
            <button
              type="button"
              disabled={running || !plan}
              onClick={async () => {
                try {
                  const r = await appDeliveryRequest<{ prompt: string; source_path?: string }>(`jobs/${jobId}/handoff`);
                  setHandoff(r);
                  await navigator.clipboard.writeText(r.prompt);
                  setCopied(true);
                } catch (e) {
                  setError(String(e));
                }
              }}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl border border-line bg-paper hover:bg-panel text-xs text-ink transition-colors disabled:opacity-40 cursor-pointer"
            >
              <Icon name="copy" size={12} />
              {copied ? "已复制交接说明" : "复制交接说明"}
            </button>
            {onReplaceSource && (
              <button
                type="button"
                disabled={running}
                onClick={onReplaceSource}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl border border-line bg-paper hover:bg-panel text-xs text-muted hover:text-ink transition-colors disabled:opacity-40 cursor-pointer"
              >
                <Icon name="refresh" size={12} />
                已经改好了，重新导入源码
              </button>
            )}
          </div>
          <span className="text-xs text-muted">
            {running ? "正在处理，完成后可以继续发送" : "发出前可修改；原源码始终保留"}
          </span>
        </div>

        {/* Chat Input Box */}
        <div className="rounded-2xl border border-line bg-paper focus-within:border-accent p-3 space-y-2 transition-colors shadow-2xs">
          <label htmlFor="app-discussion" className="sr-only">
            {ready ? "还有想调整的地方？" : "告诉 AI 你的选择或想法"}
          </label>
          <textarea
            id="app-discussion"
            ref={input}
            aria-label="给 AI 的回答或要求"
            value={feedback}
            onChange={e => setFeedback(e.target.value)}
            onKeyDown={e => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && feedback.trim() && !running) {
                e.preventDefault();
                void retry();
              }
            }}
            rows={2}
            className="w-full resize-y bg-transparent outline-none text-ink placeholder:text-muted/60 text-ui"
            placeholder={
              repairMode ? "补充故障现象；将检查失败制品并验证修改。请勿输入密码。" : ready
                ? "还有想调整的地方？直接输入告诉 AI（例如：保留原来的数据库）…"
                : "告诉 AI 你的选择或想法（例如：保留原来的数据库；请解释这样修改的影响。请勿输入密码）…"
            }
          />
          <div className="flex justify-between items-center gap-3 pt-1">
            <span className="text-[11px] text-muted">
              {repairMode ? "失败修复模式：分析提案不等于修复通过，检查通过后仍需重新部署验证。" : "支持直接输入要求或点选上方快捷按钮"} · 快捷键 ⌘/Ctrl + Enter 发送
            </span>
            <button
              type="button"
              className="px-4 py-1.5 rounded-xl bg-accent text-white text-ui font-medium hover:opacity-90 transition-opacity disabled:opacity-40 cursor-pointer"
              disabled={running || !feedback.trim()}
              onClick={retry}
            >
              发送
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
