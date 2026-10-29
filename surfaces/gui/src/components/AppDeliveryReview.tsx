import { useEffect, useRef, useState } from "react";
import { appDeliveryRequest, getAppDeliveryJob, type AppDeliveryJob, type AppConfigItem, type ResourceSummary } from "../api";
import { AppDeliveryConfig, needsConnectionDecision } from "./AppDeliveryConfig";

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
    void poll(); const timer = setInterval(poll, 1500);
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

  const retry = async () => {
    if (!feedback.trim() || running) return;
    inFlight.current = true; setBusy(true); setError("");
    try {
      await appDeliveryRequest(`jobs/${jobId}/analyze`, { feedback: feedback.trim() });
      onInvalidate?.(); preparing.current = ""; configurationVersion.current = "";
      setJob(j => j ? { ...j, status: "analyzing", proposal: undefined, artifact_hash: undefined, preparation_checks: undefined } : j);
      setFeedback(""); setHandoff(null); setCopied(false);
    } catch (e) { setError(String(e)); } finally { inFlight.current = false; setBusy(false); }
  };
  const prepare = async () => {
    inFlight.current = true; setBusy(true); setError("");
    try {
      const result = await appDeliveryRequest<AppDeliveryJob>(`jobs/${jobId}/prepare`, { proposal_hash: current.current?.proposal_hash });
      accept(result);
      if (result.status === "analyzing") preparing.current = "";
      else if (result.status !== "ready") setError(result.error || "改造未完成，请让 AI 调整方案");
    } catch (e) { setError(String(e)); } finally { inFlight.current = false; setBusy(false); }
  };
  useEffect(() => {
    if (job?.status === "review" && job.proposal && !questions.length && !inFlight.current && preparing.current !== job.proposal_hash) {
      preparing.current = job.proposal_hash || "preparing";
      void prepare();
    }
  }, [job?.status, job?.proposal_hash]);

  const saveConfig = async () => {
    inFlight.current = true; setBusy(true); setError("");
    try {
      await appDeliveryRequest(`jobs/${jobId}/config-draft`, { artifact_hash: job?.artifact_hash, configs });
      const result = await getAppDeliveryJob(jobId);
      accept(result); setConfigEditing(false);
    } catch (e) { setError(String(e)); } finally { inFlight.current = false; setBusy(false); }
  };
  const next = async () => {
    if (!canContinue) return;
    setBusy(true);
    try {
      const result = await getAppDeliveryJob(jobId);
      if (result.status !== "ready" || result.artifact_hash !== job?.artifact_hash || result.configuration_problems?.length) {
        accept(result); throw new Error("应用或配置已变化，请先处理当前问题。");
      }
      onReady(result); onNextStep?.();
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  const button = "rounded-lg border border-line px-4 py-2 text-ui disabled:opacity-40";
  const title = running ? "AI 正在检查和改造" : error || job?.error ? "有一步没有完成" : questions.length ? `有 ${questions.length} 件事需要你决定` : needsConfig ? "补齐连接配置，就可以继续" : ready ? "应用已准备好，可以核对部署内容" : "正在读取应用";

  return <section className="max-w-3xl mx-auto space-y-6 text-ui">
    <header className="space-y-2" aria-live="polite">
      <p className="text-xs text-muted">第 {job?.revision || 1} 轮 · {job?.name || "准备应用"}</p>
      <h3 className="text-lg font-semibold">{title}</h3>
      <p className="text-muted">{running ? "AI 会处理启动和打包等技术问题；需要你决定时会停下来询问。" : ready && !needsConfig ? "已完成改造和交付包检查。服务器构建、启动与业务访问将在部署时验证。" : "在下面直接回答，也可以提出其他想法。不需要读懂或审核代码。"}</p>
    </header>
    {(error || job?.error) && <div role="alert" className="rounded-lg border border-danger/30 p-4 space-y-3"><p>{error || job?.error}</p><button className={button} disabled={running} onClick={() => { setFeedback("请处理这次未完成的问题，保留原业务和已确认的选择：\n" + (error || job?.error)); input.current?.focus(); }}>请 AI 帮我处理</button>{job?.status === "review" && !questions.length && <button className={button} disabled={running} onClick={prepare}>重试准备</button>}</div>}

    <div className="space-y-5" aria-label="讨论记录">
      {(job?.conversation || []).map((m, i, all) => m.role === "user" || i === all.length - 1 ? <article key={i} className={m.role === "user" ? "ml-10 rounded-xl bg-accentSoft p-4" : "space-y-2"}><p className="text-xs text-muted">{m.role === "user" ? "你" : `AI · 第 ${m.revision || job?.revision || 1} 轮`}</p><p className="whitespace-pre-wrap leading-relaxed">{m.text}</p></article> : <details key={i} className="text-muted text-xs"><summary className="cursor-pointer">查看第 {m.revision || 1} 轮分析</summary><p className="whitespace-pre-wrap mt-2 leading-relaxed">{m.text}</p></details>)}
      {!job?.conversation?.length && plan && <p className="whitespace-pre-wrap leading-relaxed">{plan.summary}</p>}
      {!!questions.length && <div className="rounded-xl border border-line p-4 space-y-5" aria-label="需要你决定的事项">{questions.map((q, i) => {
        const question = text(q);
        const decision = plan?.decisions?.find(d => d.question === question);
        return <article key={decision?.id || question} className="space-y-2"><p className="font-medium">{questions.length > 1 ? `${i + 1}. ` : ""}{question}</p>{decision?.impact && <p className="text-muted">{decision.impact}</p>}{!!decision?.options.length && <div className="flex flex-wrap gap-2">{decision.options.map(option => <button key={option} disabled={running} className={button} onClick={() => { setFeedback(f => {
          if (questions.length === 1 && (!f.trim() || decision.options.includes(f.trim()))) return option;
          const prefix = `问题 ${i + 1}：`;
          return [...f.split("\n").filter(line => line.trim() && !line.startsWith(prefix)), prefix + option].join("\n");
        }); input.current?.focus(); }}>{option}</button>)}</div>}</article>;
      })}<p className="text-xs text-muted">可以选一个建议填入下方，也可以用自己的话回答。发送后 AI 会继续检查和改造。</p></div>}
    </div>

    {needsConfig && <section className="space-y-3 rounded-xl border border-line p-4"><h4 className="font-medium">需要你填写或选择的配置</h4><p className="text-muted">这些值只在本机处理，不会放入 AI 对话。其他原配置保持不变。</p>{job?.configuration_problems?.map(p => <p key={p.key} className="text-sm text-warnInk">{p.reason}</p>)}<AppDeliveryConfig compact configs={configs.filter(c => problemKeys.has(c.key) || configProblems.some(p => p.key === c.key))} originals={job?.runtime_configs || []} resources={onlineResources} onChange={items => { setConfigs(all => all.map(c => items.find(i => i.key === c.key) || c)); setConfigEditing(true); onInvalidate?.(); }} /><button className={button} disabled={running || !!configProblems.length} onClick={saveConfig}>确认配置并检查</button></section>}

    <div className="sticky bottom-0 bg-panel pt-3 space-y-3">
      <label htmlFor="app-discussion" className="font-medium">{ready ? "还有想调整的地方？" : "告诉 AI 你的选择或想法"}</label>
      <div className="rounded-xl border border-line p-3 space-y-3 bg-panel"><textarea id="app-discussion" ref={input} aria-label="给 AI 的回答或要求" value={feedback} onChange={e => setFeedback(e.target.value)} rows={3} className="w-full resize-y bg-transparent outline-none" placeholder="例如：保留原来的数据库；请解释这样修改会有什么影响。请不要在这里输入密码。" /><div className="flex justify-between items-center gap-3"><span className="text-xs text-muted">{running ? "正在处理，完成后可以继续发送" : "发出前可修改；原源码始终保留"}</span><button className={button + " bg-accent text-white border-accent"} disabled={running || !feedback.trim()} onClick={retry}>发送</button></div></div>
      {ready && !needsConfig && <div className="flex flex-wrap justify-between items-center gap-3 py-2"><span className="text-xs text-muted">{feedback.trim() ? "请先发送补充要求，或清空输入后继续。" : "下一步会展示最终配置、业务技能和改造摘要。"}</span><button className={button + " bg-accent text-white border-accent"} disabled={!canContinue} onClick={next}>下一步：核对部署内容</button></div>}
    </div>

    <details className="text-xs border-t border-line pt-4"><summary className="cursor-pointer text-muted">更多方式：交给其他 AI 或开发者继续修改</summary><div className="space-y-3 mt-3"><p>复制交接说明，交给你的 Codex、Kimi 或开发者。源码会另存为独立副本；改好后重新导入，Work 会重新检查。</p><button className={button} disabled={running || !plan} onClick={async () => { try { const r = await appDeliveryRequest<{prompt:string; source_path?:string}>(`jobs/${jobId}/handoff`); setHandoff(r); await navigator.clipboard.writeText(r.prompt); setCopied(true); } catch (e) { setError(String(e)); } }}>{copied ? "已复制交接说明" : "复制交接说明"}</button>{handoff && <><p className="break-all">本机独立源码副本：{handoff.source_path}</p><details><summary>查看交接说明</summary><pre className="whitespace-pre-wrap max-h-60 overflow-auto mt-2">{handoff.prompt}</pre></details></>}<button className="block text-accent" disabled={running} onClick={onReplaceSource}>已经改好了，重新导入源码</button></div></details>
    <details className="text-xs text-muted border-t border-line pt-4"><summary className="cursor-pointer">查看检查与执行详情</summary><div className="space-y-2 my-3">{job?.preparation_checks?.map(c => <p key={c.label}>{c.status === "passed" ? "已通过" : "待部署验证"} · {c.label}：{c.detail}</p>)}</div><pre className="whitespace-pre-wrap max-h-44 overflow-auto bg-paper p-3">{job?.events.join("\n")}</pre></details>
  </section>;
}
