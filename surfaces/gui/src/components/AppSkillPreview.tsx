import { useEffect, useState } from "react";
import { appDeliveryRequest, type AppDeliveryJob } from "../api";
import { Icon } from "./Icon";

type Capability = { id?: string; purpose: string; method?: string; path: string; effect?: string };
export function AppSkillPreview({ job }: { job: AppDeliveryJob }) {
  const [packageData, setPackageData] = useState<{
    markdown: string;
    contract: { apis: Capability[]; pages: Capability[]; workflows?: unknown[] };
    sha256: string;
  } | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    setPackageData(null);
    setError("");
    if (job.status === "ready" && job.artifact_hash) {
      appDeliveryRequest<typeof packageData>(`jobs/${job.id}/skill-preview`)
        .then(data => {
          if (alive && data?.contract && data.markdown) setPackageData(data);
        })
        .catch(e => {
          if (alive) setError(String(e));
        });
    }
    return () => {
      alive = false;
    };
  }, [job.id, job.status, job.artifact_hash]);

  const contract = packageData?.contract || job.proposal;
  const apis = contract?.apis || [];
  const pages = contract?.pages || [];
  const purposes = [...new Set([...apis.filter(api => api.path !== job.proposal?.health_path), ...pages].map(c => c.purpose))];

  return (
    <section className="space-y-4 text-ui" aria-label="应用业务技能预览">
      {/* Header Summary Card */}
      <div className="rounded-xl border border-line bg-paper/50 p-4 flex flex-wrap items-center justify-between gap-3">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-ink text-sm">部署后，AI 能帮你做什么</span>
            <code className="px-2 py-0.5 rounded-md bg-accentSoft text-accent text-xs font-mono">
              app-{job.slug}
            </code>
          </div>
          <p className="text-xs text-muted">
            {packageData
              ? "已生成业务技能包。部署并完成业务验证后，可在任意对话中直接调用。"
              : error
              ? "实际技能包暂时无法读取，不能以分析计划代替最终技能。"
              : "正在读取实际技能包…"}
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <span className="px-2.5 py-1 rounded-lg border border-line bg-panel text-ink font-medium">
            {apis.length} 项接口操作
          </span>
          <span className="px-2.5 py-1 rounded-lg border border-line bg-panel text-ink font-medium">
            {pages.length} 个业务页面
          </span>
        </div>
      </div>

      {error && (
        <p role="alert" className="text-danger text-xs rounded-xl border border-danger/25 bg-danger/5 p-3">
          {error}
        </p>
      )}

      {/* Core Capabilities Overview */}
      {!error && purposes.length > 0 && (
        <div className="rounded-xl border border-line bg-panel p-4 space-y-2.5">
          <div className="text-xs font-medium text-muted">核心业务能力概览</div>
          <ul className="grid sm:grid-cols-2 gap-2">
            {purposes.map(purpose => (
              <li
                key={purpose}
                className="flex items-start gap-2 rounded-lg border border-line/60 bg-paper/60 px-3 py-2 text-xs text-ink"
              >
                <Icon name="sparkle" size={13} className="text-accent shrink-0 mt-0.5" />
                <span>{purpose}</span>
              </li>
            ))}
          </ul>
          {!!apis.length && (
            <p className="text-xs text-muted pt-1">
              💡 调用方式：在对话中直接说「使用 <code>app-{job.slug}</code> 帮我……」。原有登录要求保留；涉及修改或外部写操作时 AI 会先向你确认。
            </p>
          )}
        </div>
      )}

      {/* Detailed API & Page Capabilities */}
      <details className="rounded-xl border border-line bg-panel p-3.5 text-xs">
        <summary className="cursor-pointer font-medium text-accent select-none">
          查看全部业务能力与使用条件（{apis.length} 项接口操作，{pages.length} 个页面）
        </summary>
        <div className="divide-y divide-line mt-3 pt-2 border-t border-line">
          {apis.map(api => (
            <article key={api.id || api.path} className="py-3 space-y-1.5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="font-medium text-ui text-ink">{api.purpose}</p>
                <span
                  className={`px-2 py-0.5 rounded-full text-[11px] ${
                    api.effect === "read" ? "bg-okSoft text-ok" : "bg-warnSoft text-warnInk"
                  }`}
                >
                  {api.effect === "read"
                    ? job.verification?.checks.some(check => check.operation === api.id && check.passed)
                      ? "只读查询 · 已验证"
                      : "只读查询 · 待业务验证"
                    : "写操作 · 执行前需确认"}
                </span>
              </div>
              <p className="font-mono text-muted break-all">
                {api.method} {job.app_url}{api.path}
              </p>
              <details className="text-muted">
                <summary className="cursor-pointer hover:text-ink">技术信息：参数、返回结果与认证</summary>
                <pre className="whitespace-pre-wrap break-all max-h-60 overflow-auto bg-paper p-2.5 rounded-lg mt-1.5 font-mono">
                  {JSON.stringify(api, null, 2)}
                </pre>
              </details>
            </article>
          ))}
          {pages.map((page, i) => (
            <article key={`${page.path}-${i}`} className="py-3 space-y-1.5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="font-medium text-ui text-ink">{page.purpose}</p>
                <span className="px-2 py-0.5 rounded-full bg-paper border border-line text-[11px] text-muted">
                  页面交互 · 路径 {page.path}
                </span>
              </div>
              <p className="text-muted">需要浏览器能力；如应用要求登录，保留原登录方式。</p>
              <details className="text-muted">
                <summary className="cursor-pointer hover:text-ink">查看页面操作说明</summary>
                <pre className="whitespace-pre-wrap bg-paper p-2.5 rounded-lg mt-1.5 font-mono">
                  {JSON.stringify(page, null, 2)}
                </pre>
              </details>
            </article>
          ))}
        </div>
      </details>

      {packageData && (
        <details className="rounded-xl border border-line bg-paper/40 p-3.5 text-xs text-muted">
          <summary className="cursor-pointer hover:text-ink select-none">查看完整技能文件 (SKILL.md)</summary>
          <pre className="whitespace-pre-wrap bg-panel p-3 rounded-lg mt-2 max-h-72 overflow-auto font-mono text-ink">
            {packageData.markdown}
          </pre>
          <p className="break-all mt-2 font-mono text-[11px]">文件校验：{packageData.sha256}</p>
        </details>
      )}
    </section>
  );
}
