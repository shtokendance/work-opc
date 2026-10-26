import { useEffect, useState } from "react";
import { appDeliveryRequest, downloadAppArtifact, type AppDeliveryJob } from "../api";
import { Icon } from "./Icon";

export function AppDeliveryArtifacts({ slug }: { slug: string }) {
  const [job, setJob] = useState<AppDeliveryJob | null>(null);
  const [message, setMessage] = useState("");
  useEffect(() => {
    let alive = true;
    appDeliveryRequest<{ jobs: AppDeliveryJob[] }>(`jobs?slug=${encodeURIComponent(slug)}`).then(data => {
      if (alive) {
        setJob(data.jobs.find(j => j.artifact_hash) || null);
        if (!data.jobs.some(j => j.artifact_hash)) setMessage("本机尚无该应用的交付包记录");
      }
    }).catch(e => { if (alive) setMessage(String(e)); });
    return () => { alive = false; };
  }, [slug]);
  return <section className="space-y-3">
    <h3 className="font-semibold">实际交付文件</h3>
    {message && <p role="status" className="text-ui text-muted break-words">{message}</p>}
    {Object.entries(job?.artifacts || {}).map(([name, path]) => <div key={name} className="border-b border-line py-3 space-y-2">
      <div className="flex items-center justify-between"><b className="text-ui">{name}</b><div className="flex gap-1"><button title="复制实际路径" className="p-2 hover:bg-paper rounded" onClick={() => { void navigator.clipboard.writeText(path); setMessage("已复制实际文件路径"); }}><Icon name="copy" size={16} /></button><button title="下载产物" className="p-2 hover:bg-paper rounded flex gap-1 items-center text-ui" onClick={() => { if (job) void downloadAppArtifact(job, name).catch(e => setMessage(String(e))); }}><Icon name="archive" size={16} />导出</button></div></div>
      <p className="text-xs font-mono break-all select-text">{path}</p>
    </div>)}
    {job && <><div className="text-xs font-mono break-all">交付摘要：{job.artifact_hash}</div><button className="border border-line rounded-md px-3 py-2 text-ui" onClick={async () => {
      setMessage("正在校验实际部署及只读业务能力…");
      try { await appDeliveryRequest("enable-skill", { job_id: job.id }); setMessage("业务验证通过，本地应用技能已启用"); }
      catch (e) { setMessage(String(e)); }
    }}>验证并启用本地技能</button></>}
  </section>;
}
