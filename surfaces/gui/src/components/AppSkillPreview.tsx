import { useEffect, useState } from "react";
import { appDeliveryRequest, type AppDeliveryJob } from "../api";

type Capability = { id?: string; purpose: string; method?: string; path: string; effect?: string };
export function AppSkillPreview({job}: {job: AppDeliveryJob}) {
  const [packageData, setPackageData] = useState<{markdown:string; contract:{apis:Capability[]; pages:Capability[]; workflows?:unknown[]}; sha256:string} | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true; setPackageData(null); setError("");
    if (job.status === "ready" && job.artifact_hash) appDeliveryRequest<typeof packageData>(`jobs/${job.id}/skill-preview`).then(data => { if (alive && data?.contract && data.markdown) setPackageData(data); }).catch(e => { if (alive) setError(String(e)); });
    return () => { alive = false; };
  }, [job.id, job.status, job.artifact_hash]);
  const contract = packageData?.contract || job.proposal;
  const apis = contract?.apis || [];
  const pages = contract?.pages || [];
  const purposes = [...new Set([...apis.filter(api => api.path !== job.proposal?.health_path), ...pages].map(c => c.purpose))];
  return <section className="space-y-4 text-ui" aria-label="应用业务技能预览">
    <p className="text-muted">{packageData ? "已生成业务技能。部署并完成相应验证后，可在对话中使用。" : error ? "实际技能包暂时无法读取，不能以分析计划代替最终技能。" : "正在读取实际技能包…"}</p>
    {error && <p role="alert" className="text-danger">{error}</p>}
    {!error && <ul className="space-y-2 list-disc pl-5">{purposes.slice(0, 3).map(purpose => <li key={purpose}>{purpose}</li>)}</ul>}
    {!!apis.length && <p className="text-xs text-muted">可对 AI 说：“使用 app-{job.slug}，帮我……”并说明具体业务需求。原有登录要求保留；修改或外部操作需要另外确认。</p>}
    <details className="text-xs"><summary className="cursor-pointer text-accent">查看全部业务能力与使用条件（{apis.length} 项接口操作，{pages.length} 个页面）</summary><div className="divide-y divide-line mt-3">
      {apis.map(api => <article key={api.id} className="py-4 space-y-2"><p className="font-medium text-ui">{api.purpose}</p><p className="text-muted">{api.effect === "read" ? (job.verification?.checks.some(check => check.operation === api.id && check.passed) ? "查询操作 · 已通过只读业务验证" : "查询操作 · 待业务验证") : "执行前需要确认目标和影响 · 尚未验证"}</p><details><summary className="cursor-pointer">技术信息：参数、返回结果与认证</summary><p className="font-mono my-2 break-all">{api.method} {job.app_url}{api.path}</p><pre className="whitespace-pre-wrap break-all max-h-72 overflow-auto">{JSON.stringify(api, null, 2)}</pre></details></article>)}
      {pages.map((page, i) => <article key={`${page.path}-${i}`} className="py-4 space-y-2"><p className="font-medium text-ui">{page.purpose}</p><p className="text-muted">需要浏览器能力；如应用要求登录，保留原登录方式。页面操作待验证。</p><details><summary className="cursor-pointer">查看页面操作说明</summary><pre className="whitespace-pre-wrap">{JSON.stringify(page, null, 2)}</pre></details></article>)}
    </div></details>
    {packageData && <details className="text-xs text-muted"><summary className="cursor-pointer">查看完整技能文件</summary><pre className="whitespace-pre-wrap bg-paper p-3 mt-2 max-h-80 overflow-auto">{packageData.markdown}</pre><p className="break-all mt-2">文件校验：{packageData.sha256}</p></details>}
  </section>;
}
