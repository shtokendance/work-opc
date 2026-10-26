import { useEffect, useRef, useState } from "react";
import { appDeliveryRequest, getAppDeliveryJob, type AppDeliveryJob } from "../api";

export function AppDeliveryReview({ jobId, onReady }: { jobId: string; onReady: (job: AppDeliveryJob) => void }) {
  const [job, setJob] = useState<AppDeliveryJob | null>(null);
  const [approved, setApproved] = useState<string[]>([]);
  const [configConfirmed, setConfigConfirmed] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const notified = useRef("");

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const result = await getAppDeliveryJob(jobId);
        if (!alive) return;
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
      const result = await appDeliveryRequest<AppDeliveryJob>(`jobs/${jobId}/approve`, { proposal_hash: job?.proposal_hash, approved_changes: approved, confirm_config_changes: configConfirmed });
      setJob(result);
      if (result.status === "ready") onReady(result);
      else setError(result.error || "改造校验未通过");
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  };
  const retry = async () => {
    setBusy(true); setError(""); setApproved([]); setConfigConfirmed(false);
    try {
      await appDeliveryRequest(`jobs/${jobId}/analyze`, { feedback });
      setJob(j => j ? { ...j, status: "analyzing", proposal: undefined, error: "" } : j);
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  };
  const proposal = job?.proposal;
  const ready = job?.status === "ready";
  return <section className="space-y-4 text-ui text-ink">
    <div className="flex justify-between items-center border-b border-line pb-3">
      <h2 className="font-semibold">{ready ? "已按批准项完成改造" : job?.status === "review" ? "待确认修改提案" : job?.status === "failed" ? "操作未完成" : "AI 正在分析源码"}</h2>
      <span className="text-xs text-muted">{job?.model}</span>
    </div>
    <div className="font-mono text-xs break-all">{job?.app_url}</div>
    {!!job?.events?.length && <details><summary className="cursor-pointer text-muted">执行记录（{job.events.length}）</summary><pre className="text-xs whitespace-pre-wrap max-h-40 overflow-auto">{job.events.join("\n")}</pre></details>}
    {(error || job?.error) && <div role="alert" className="text-danger whitespace-pre-wrap break-words">{error || job?.error}</div>}
    {proposal && <>
      <p className="whitespace-pre-wrap">{proposal.summary}</p>
      <h3 className="font-semibold">问题与建议</h3>
      {proposal.issues.map(issue => <div key={issue.id} className="border-b border-line py-2">
        <div className="font-medium">{issue.id}：{issue.impact}</div>
        <div className="text-muted text-xs break-words">{typeof issue.evidence === "string" ? issue.evidence : JSON.stringify(issue.evidence)}</div>
        <div>{issue.recommendation}</div>
      </div>)}
      {proposal.unresolved?.length > 0 && <div className="border-l-2 border-warning pl-3"><h3 className="font-semibold">待回答问题</h3>{proposal.unresolved.map((q, i) => <p key={i}>{typeof q === "string" ? q : JSON.stringify(q)}</p>)}</div>}
      <h3 className="font-semibold">文件修改（{proposal.changes.length}）</h3>
      {proposal.changes.map(change => <div key={change.id} className="border-b border-line pb-3">
        <label className="flex items-start gap-2"><input type="checkbox" disabled={ready || busy} checked={ready || approved.includes(change.id)} onChange={e => setApproved(previous => e.target.checked ? [...previous, change.id] : previous.filter(id => id !== change.id))} /><span><b>{change.path}</b><br />{change.reason}</span></label>
        <details className="mt-2"><summary className="cursor-pointer text-muted text-xs">查看实际建议差异</summary><pre className="text-xs whitespace-pre-wrap break-all max-h-64 overflow-auto bg-paper p-3">{change.diff}</pre></details>
      </div>)}
      <h3 className="font-semibold">配置契约与连接变更</h3>
      {proposal.configs.map(config => <div key={config.key} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-3 border-b border-line py-2 break-words"><span className="font-mono text-xs">{config.key}{config.required ? " *" : ""}</span><span>{config.secret ? "秘密值：运行时提供" : config.default || "待用户提供"}<br /><span className="text-xs text-muted">{config.description}</span></span></div>)}
      <label className="flex gap-2 items-start"><input type="checkbox" disabled={ready || busy} checked={ready || configConfirmed} onChange={e => setConfigConfirmed(e.target.checked)} /><span>我确认上述配置及所有基础设施连接适配；具体资源和值在上传前再次核对</span></label>
      <h3 className="font-semibold">应用业务技能</h3>
      {proposal.apis.map(api => <div key={api.id} className="text-xs py-1"><code>{api.method} {api.path}</code> · {api.purpose} · {api.effect}</div>)}
      {proposal.pages.map(page => <div key={page.path}>{page.path} · {page.purpose}</div>)}
      {!ready && <button className="px-4 py-2 rounded-md bg-accent text-white disabled:opacity-40" onClick={approve} disabled={busy || job?.status !== "review" || !configConfirmed || approved.length !== proposal.changes.length || !!proposal.unresolved?.length}>{busy ? "校验中…" : "批准所选修改并生成交付包"}</button>}
    </>}
    {!ready && job && ["review", "failed"].includes(job.status) && <div className="space-y-2 border-t border-line pt-4"><label className="block font-medium" htmlFor="app-analysis-feedback">补充信息或调整要求</label><textarea id="app-analysis-feedback" className="w-full bg-paper border border-line rounded-md p-2" rows={3} value={feedback} onChange={e => setFeedback(e.target.value)} /><button className="px-3 py-2 rounded-md border border-line" disabled={busy} onClick={retry}>重新分析，生成新提案</button></div>}
    {ready && <p className="text-muted text-xs">交付包已生成并通过结构校验；容器构建、启动与业务验证将在部署阶段执行。</p>}
  </section>;
}
