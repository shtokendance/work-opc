import { useEffect, useRef, useState } from "react";
import { appDeliveryRequest, getAppDeliveryJob, type AppDeliveryJob } from "../api";

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

export function AppDeliveryReview({ jobId, onReady, onReplaceSource }: { jobId: string; onReady: (job: AppDeliveryJob) => void; onReplaceSource?: () => void }) {
  const [job, setJob] = useState<AppDeliveryJob | null>(null);
  const [approved, setApproved] = useState<string[]>([]);
  const [configConfirmed, setConfigConfirmed] = useState(false);
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
          setApproved([]); setConfigConfirmed(false); setAnswers({});
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
      const result = await appDeliveryRequest<AppDeliveryJob>(`jobs/${jobId}/approve`, { proposal_hash: job?.proposal_hash, approved_changes: approved, confirm_config_changes: configConfirmed });
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
      setApproved([]); setConfigConfirmed(false);
      setJob(j => j ? { ...j, status: "analyzing", proposal: undefined, error: "" } : j);
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
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
  const remaining = proposal?.changes.filter(c => !approved.includes(c.id)).length || 0;
  const blockers = ready ? [] : [
    ...(questions.length ? [`还有 ${questions.length} 个问题需要回答并重新分析，当前尚不能生成交付包。`] : []),
    ...(remaining ? [`还有 ${remaining} 项文件修改未批准。`] : []),
    ...(!configConfirmed && proposal ? ["请确认配置处理方式；具体配置值和资源在下一步核对。"] : []),
  ];
  return <section className="space-y-4 text-ui text-ink">
    <div className="rounded-xl border border-line bg-paper/50 p-4 space-y-2">
      <h2 className="font-semibold">{ready ? "改造完成，可以进入配置" : job?.status === "review" ? questions.length ? "分析完成，需要你补充信息" : "分析完成，请审阅修改" : job?.status === "failed" ? "操作未完成，请查看原因" : "AI 正在分析源码"}</h2>
      <p className="text-xs text-muted">{ready ? "已生成部署包和业务技能；下一步保留或调整配置，再确认上传。" : "原始源码保持不变。只有你批准后，才会在独立副本中应用修改。"}</p>
      <div className="font-mono text-xs break-all">{job?.app_url}</div>
      <div className="text-xs text-muted break-all">分析模型：{job?.model || "连接中"}</div>
    </div>
    {(error || job?.error) && <div role="alert" className="rounded-lg border border-danger p-3 text-danger whitespace-pre-wrap break-words">{error || job?.error}</div>}
    {proposal && <>
      <div className="rounded-xl border border-line p-4 space-y-3">
        <h3 className="font-semibold">这个应用能帮你做什么</h3>
        <p className="leading-relaxed">{job?.name}：部署成功并验证后，你可以通过业务技能在对话中使用这些功能。</p>
        {proposal.apis.length + proposal.pages.length > 0 ? <ul className="list-disc pl-5 space-y-1">{[...proposal.pages.map(p => p.purpose), ...proposal.apis.map(a => a.purpose)].slice(0, 4).map((purpose, i) => <li key={i}>{purpose}</li>)}</ul> : <p className="text-muted">具体业务功能还需要根据源码补充。</p>}
        <p className="text-ui text-muted">为让应用在服务器上运行，AI 建议修改 {proposal.changes.length} 个文件。先看下面的修改说明；原文件和业务配置都会保留，确认后才会修改副本。</p>
        <details className="text-xs text-muted"><summary className="cursor-pointer">查看完整分析说明</summary><p className="whitespace-pre-wrap leading-relaxed mt-2">{proposal.summary}</p></details>
      </div>
      <details className="rounded-xl border border-line p-4" aria-label="交给自己的 AI 修改">
        <summary className="font-medium cursor-pointer">想让自己的 AI 来改？复制交接提示词</summary>
        <p className="text-ui text-muted mt-3">复制后，粘贴给你电脑上的 Codex、Kimi 等 AI。它会根据建议检查和修改；改好后回到这里重新选择文件夹或 ZIP，再分析、确认和部署。</p>
        <button className="px-3 py-2 mt-3 border border-line rounded-lg" onClick={copyHandoff}>{copied ? "已复制交接提示词" : "复制交接提示词"}</button>
        {handoff && <textarea aria-label="交接提示词" readOnly value={handoff} rows={8} className="w-full mt-3 bg-paper border border-line rounded-lg p-3 text-xs" />}
        {onReplaceSource && <button className="block mt-3 text-accent underline text-ui" onClick={onReplaceSource}>已经改好了，重新选择源码</button>}
      </details>
      {questions.length > 0 && <section className="rounded-xl border border-warnInk bg-warnSoft p-4 space-y-3" aria-label="待回答问题">
        <h3 className="font-semibold">先回答这 {questions.length} 个问题</h3>
        <p className="text-xs text-muted">在下方逐项回答，再点击“根据回答重新分析”。已有密码、密钥不需要在这里填写。</p>
        {questions.map((q, i) => <label key={i} className="block space-y-2"><span className="block font-medium">{i + 1}. {questionText(q)}</span><textarea aria-label={`问题 ${i + 1} 的回答`} className="w-full bg-panel border border-line rounded-lg p-2 text-ui" rows={2} value={answers[i] || ""} onChange={e => setAnswers(previous => ({ ...previous, [i]: e.target.value }))} placeholder="填写你的选择或补充说明；连接地址和凭据在下一步确认" /></label>)}
        <button className="px-4 py-2 rounded-lg bg-accent text-white disabled:opacity-40" disabled={busy || !Object.values(answers).some(a => a.trim())} onClick={retry}>{busy ? "正在提交…" : "根据回答重新分析"}</button>
      </section>}
      <section className="rounded-xl border border-line p-4 space-y-3" aria-label="文件修改审批">
        <h3 className="font-semibold">需要你同意的修改 · {proposal.changes.length} 项{!ready && proposal.changes.length > 0 ? `，已批准 ${approved.length} 项` : ""}</h3>
        {proposal.changes.length === 0 && <p className="text-muted text-xs">{questions.length ? "模型尚未给出可执行的文件修改。先回答上方问题，再生成完整方案。" : "无需修改源码，将校验现有部署文件并生成交付包。"}</p>}
        {proposal.changes.map(change => <div key={change.id} className="border-t border-line pt-3">
          <label className="flex items-start gap-2"><input type="checkbox" disabled={ready || busy} checked={ready || approved.includes(change.id)} onChange={e => setApproved(previous => e.target.checked ? [...previous, change.id] : previous.filter(id => id !== change.id))} /><span className="min-w-0"><b className="break-words font-medium">{changeTitle(change)}</b><span className="block text-xs text-muted font-mono break-all mt-1">涉及文件：{change.path}</span></span></label>
          <details className="mt-2"><summary className="cursor-pointer text-muted text-xs">查看修改前后差异</summary><p className="text-xs text-muted mt-2">{change.reason}</p><pre className="text-xs whitespace-pre-wrap break-all max-h-64 overflow-auto bg-paper p-3 mt-2 rounded-lg">{change.diff}</pre></details>
        </div>)}
        <label className="flex gap-2 items-start border-t border-line pt-3"><input type="checkbox" disabled={ready || busy} checked={ready || configConfirmed} onChange={e => setConfigConfirmed(e.target.checked)} /><span>我确认上述改造方案；未要求变更的原配置保持原值，所有连接替换在下一步由我选择确认</span></label>
        {!ready && <>
          {blockers.length > 0 && <ul className="text-xs text-muted space-y-1 list-disc pl-5" aria-label="继续前需要完成">{blockers.map(b => <li key={b}>{b}</li>)}</ul>}
          <button className="px-4 py-2 rounded-lg bg-accent text-white disabled:opacity-40" onClick={approve} disabled={busy || job?.status !== "review" || blockers.length > 0}>{busy ? "正在校验并生成…" : "批准所选修改并生成交付包"}</button>
        </>}
      </section>
      <details className="rounded-xl border border-line p-4"><summary className="cursor-pointer font-medium">问题与建议 · {proposal.issues.length} 项</summary><div className="mt-3 space-y-3">{proposal.issues.map(issue => <div key={issue.id} className="border-t border-line pt-3"><div className="font-medium">{issue.id}：{issue.impact}</div><p className="mt-1">{issue.recommendation}</p><p className="text-muted text-xs break-words mt-1">依据：{questionText(issue.evidence)}</p></div>)}</div></details>
      <details className="rounded-xl border border-line p-4"><summary className="cursor-pointer font-medium">原配置会怎么处理 · {proposal.configs.length} 项</summary><p className="mt-3 text-xs text-muted">原值由本机保留。下一步会并排展示原值和服务器可用配置，由你决定要不要换。</p>{proposal.configs.map(config => <div key={config.key} className="border-t border-line py-3 mt-2 break-words"><div className="font-mono text-xs font-medium">{config.key}{config.required ? " · 必填" : ""}</div><p className="mt-1 text-xs">{config.has_original_value ? "保留原配置值" : config.secret ? "原值尚未关联，下一步确认；不会从分析文本中读取凭据" : config.default || "下一步填写"}</p>{config.source && <p className="text-xs text-muted break-all">来源：{config.source.path} / {config.source.keys.join(".")}</p>}<p className="text-xs text-muted mt-1">{config.description}</p></div>)}</details>
      <details className="rounded-xl border border-line p-4"><summary className="cursor-pointer font-medium">生成的业务技能 · {proposal.apis.length} 个接口，{proposal.pages.length} 个页面</summary><p className="text-xs text-muted mt-3">部署期间仅验证明确只读的操作，不执行写入、删除或外部流水线。</p>{proposal.apis.map(api => <div key={api.id} className="text-xs py-3 border-b border-line"><code className="break-all">{api.method} {api.path}</code><p className="mt-1">{api.purpose}</p><p className="text-muted mt-1">{effects[api.effect] || api.effect}</p></div>)}{proposal.pages.map(page => <div key={page.path} className="text-xs py-2">{page.path} · {page.purpose}</div>)}</details>
    </>}
    {!ready && job && ["review", "failed"].includes(job.status) && <details className="rounded-xl border border-line p-4" open={job.status === "failed"}><summary className="cursor-pointer font-medium">补充说明或要求调整方案</summary><p className="text-xs text-muted mt-3">说明要保留或调整的行为，不要填写密码和密钥。</p><textarea id="app-analysis-feedback" aria-label="补充信息或调整要求" className="w-full bg-panel border border-line rounded-lg p-2 mt-2" rows={3} value={feedback} onChange={e => setFeedback(e.target.value)} /><button className="px-3 py-2 rounded-lg border border-line mt-2 disabled:opacity-40" disabled={busy} onClick={retry}>重新分析，生成新提案</button></details>}
    {!!job?.events?.length && <details className="text-xs text-muted"><summary className="cursor-pointer">查看分析执行记录（{job.events.length}）</summary><pre className="whitespace-pre-wrap max-h-40 overflow-auto mt-2">{job.events.join("\n")}</pre></details>}
    {ready && <p className="text-muted text-xs">结构校验已通过；容器构建、启动与业务验证将在部署阶段执行。</p>}
  </section>;
}
