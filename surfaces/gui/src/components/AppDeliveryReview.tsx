import { useEffect, useRef, useState } from "react";
import { appDeliveryRequest, getAppDeliveryJob, type AppDeliveryJob, type AppConfigItem, type ResourceSummary } from "../api";
import { AppDeliveryConfig, needsConnectionDecision } from "./AppDeliveryConfig";
import { Icon } from "./Icon";

export function AppDeliveryReview({ jobId, onReady, onReplaceSource, onNextStep, onInvalidate, initialFeedback = "", onlineResources = [] }: {
  jobId: string; onReady: (job: AppDeliveryJob) => void; onReplaceSource?: () => void;
  onNextStep?: () => void; onlineResources?: ResourceSummary[]; onInvalidate?: () => void; initialFeedback?: string;
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
      await appDeliveryRequest(`jobs/${jobId}/analyze`, { feedback: content });
      onInvalidate?.();
      preparing.current = "";
      configurationVersion.current = "";
      setJob(j => j ? {
        ...j,
        status: "analyzing",
        error: "",
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

  useEffect(() => {
    if (job?.status === "review" && job.proposal && !questions.length && !inFlight.current && preparing.current !== job.proposal_hash) {
      preparing.current = job.proposal_hash || "preparing";
      void prepare();
    }
  }, [job?.status, job?.proposal_hash]);

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

  const button = "rounded-lg border border-line px-3.5 py-1.5 text-ui transition-colors disabled:opacity-40 cursor-pointer";
  const title = running
    ? "AI 正在检查和改造"
    : activeError
    ? "有一步没有完成"
    : questions.length
    ? `有 ${questions.length} 件事需要你决定`
    : needsConfig
    ? "补齐连接配置，就可以继续"
    : ready
    ? "应用已准备好，可以核对部署内容"
    : "正在读取应用";

  const conversation = job?.conversation || [];
  const latestEvent = job?.events?.length ? job.events[job.events.length - 1] : "";

  return (
    <section className="max-w-3xl mx-auto flex flex-col min-h-[460px] text-ui">
      {/* Top Status Bar */}
      <header
        className="flex flex-wrap items-center justify-between gap-3 pb-3 mb-4 border-b border-line"
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

        {(job?.preparation_checks?.length || job?.events?.length) ? (
          <details className="text-xs text-muted relative">
            <summary className="cursor-pointer px-2.5 py-1 rounded-lg border border-line bg-paper hover:text-ink select-none">
              查看检查与执行详情
            </summary>
            <div className="mt-2 p-3 rounded-xl border border-line bg-paper shadow-md space-y-2 max-w-xl">
              {job?.preparation_checks?.map(c => (
                <p key={c.label}>
                  {c.status === "passed" ? "✅ 已通过" : "⏳ 待部署验证"} · {c.label}：{c.detail}
                </p>
              ))}
              {!!job?.events?.length && (
                <pre className="whitespace-pre-wrap max-h-40 overflow-auto bg-panel p-2.5 rounded-lg font-mono text-[11px]">
                  {job.events.join("\n")}
                </pre>
              )}
            </div>
          </details>
        ) : null}
      </header>

      {/* Chat Messages Stream */}
      <div className="flex-1 space-y-4 pb-6" aria-label="讨论记录">
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
                  AI 改造助手 · 第 {m.revision || job?.revision || 1} 轮分析
                </p>
                <div className="rounded-2xl rounded-tl-xs border border-line bg-paper/80 px-4 py-3.5 text-ink whitespace-pre-wrap leading-relaxed">
                  {m.text}
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
                {plan.summary}
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
                resources={onlineResources}
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

        {/* Running / Thinking Bubble */}
        {running && (
          <div className="flex items-start gap-3">
            <div className="w-7 h-7 rounded-lg bg-accent/10 border border-accent/20 text-accent flex items-center justify-center shrink-0 mt-0.5 animate-pulse">
              <Icon name="sparkle" size={14} />
            </div>
            <div className="rounded-2xl rounded-tl-xs border border-line bg-paper/60 px-4 py-3 text-muted space-y-1">
              <div className="flex items-center gap-2 text-ink font-medium text-xs">
                <span className="inline-block w-2 h-2 rounded-full bg-accent animate-ping" />
                AI 正在检查与改造应用…
              </div>
              {latestEvent && <p className="text-xs text-muted">{latestEvent}</p>}
            </div>
          </div>
        )}

        {/* Ready Status Bubble */}
        {ready && !needsConfig && !questions.length && !activeError && !running && (
          <div className="flex items-start gap-3">
            <div className="w-7 h-7 rounded-lg bg-okSoft border border-okLine text-ok flex items-center justify-center shrink-0 mt-0.5">
              <Icon name="check" size={14} />
            </div>
            <div className="flex-1 rounded-2xl rounded-tl-xs border border-okLine bg-okSoft/30 px-4 py-3 text-ink flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="font-medium text-sm text-ok">改造与交付检查已完成</div>
                <div className="text-xs text-muted mt-0.5">
                  {feedback.trim()
                    ? "请先发送输入框中的补充要求，或清空输入后继续。"
                    : "可以点击右侧按钮进入部署核对，或在下方对话框继续提要求。"}
                </div>
              </div>
              <button
                type="button"
                className={button + " bg-accent text-white border-accent font-medium shadow-xs"}
                disabled={!canContinue}
                onClick={next}
              >
                下一步：核对部署内容
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Unified Bottom Chat Composer Dock */}
      <div className="sticky bottom-0 bg-panel pt-3 pb-1 border-t border-line space-y-2.5">
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
              ready
                ? "还有想调整的地方？直接输入告诉 AI（例如：保留原来的数据库）…"
                : "告诉 AI 你的选择或想法（例如：保留原来的数据库；请解释这样修改的影响。请勿输入密码）…"
            }
          />
          <div className="flex justify-between items-center gap-3 pt-1">
            <span className="text-[11px] text-muted">
              支持直接输入要求或点选上方快捷按钮 · 快捷键 ⌘/Ctrl + Enter 发送
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
