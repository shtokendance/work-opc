import { useEffect, useState } from "react";
import { appDeliveryRequest, downloadAppArtifact, type AppDeliveryJob } from "../api";
import { Icon, type IconName } from "./Icon";

interface ArtifactMeta {
  title: string;
  badge: string;
  badgeClass: string;
  icon: IconName;
  description: string;
}

function getArtifactMeta(name: string): ArtifactMeta {
  if (name.endsWith(".tar") || name.includes("app.tar")) {
    return {
      title: "应用容器部署包",
      badge: "Docker 源码镜像",
      badgeClass: "bg-tealSoft text-tealInk border-tealLine/30",
      icon: "archive",
      description: "包含应用全部源码、运行时依赖、Dockerfile 与 CapRover 启动定义，用于服务器自动化构建与容器部署。",
    };
  }
  if (name.endsWith(".zip") || name.includes("skill")) {
    return {
      title: "智能体业务技能包",
      badge: "AI 技能契约",
      badgeClass: "bg-accentSoft text-accent border-accent/25",
      icon: "sparkle",
      description: "包含 SKILL.md、业务视图、只读与写入 API 契约定义，用于本地工作流智能体直接发现与免密安全调用。",
    };
  }
  if (name.includes("manifest")) {
    return {
      title: "应用契约元数据",
      badge: "部署清单",
      badgeClass: "bg-panel text-ink border-lineStrong/40",
      icon: "fileCode",
      description: "记录应用端口、环境变量模板、数据库卷映射与基础服务依赖的标准清单，保障多环境一致交付。",
    };
  }
  if (name.includes("summary")) {
    return {
      title: "分析与改造摘要",
      badge: "构建报告",
      badgeClass: "bg-warnSoft text-warnInk border-warnInk/25",
      icon: "file",
      description: "记录 AI 全景摸底结果、代码架构识别、依赖库替换与交付物哈希快照的结构化摘要报告。",
    };
  }
  return {
    title: name,
    badge: "交付产物",
    badgeClass: "bg-panel text-muted border-line",
    icon: "diamond",
    description: "平台应用交付产物与运行时支撑资源文件。",
  };
}

export function AppDeliveryArtifacts({ slug }: { slug: string }) {
  const [job, setJob] = useState<AppDeliveryJob | null>(null);
  const [message, setMessage] = useState("");
  const [enablingSkill, setEnablingSkill] = useState(false);

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

  const artifacts = Object.entries(job?.artifacts || {});

  return (
    <section className="space-y-4">
      {/* Header Banner */}
      <div className="p-4 rounded-xl bg-paper/90 border border-line shadow-2xs space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-accentSoft text-accent font-semibold text-xs border border-accent/25">
                <Icon name="archive" size={13} />
                <span>实际交付文件</span>
              </span>
              <span className="font-semibold text-sm text-ink tracking-tight">应用核心交付资产</span>
            </div>
            <p className="text-xs text-muted">
              由 AI 自动构建与改造生成的标准平台交付包，包含容器镜像源码包、OpenAPI 业务技能契约与部署元数据。
            </p>
          </div>

          {job && (
            <div className="flex items-center gap-2 shrink-0">
              <button
                type="button"
                disabled={enablingSkill}
                className="px-3.5 py-1.5 rounded-lg bg-accent hover:opacity-90 disabled:opacity-50 text-white font-medium text-xs shadow-2xs flex items-center gap-1.5 transition-all cursor-pointer"
                onClick={async () => {
                  setEnablingSkill(true);
                  setMessage("正在校验实际部署及只读业务能力…");
                  try {
                    await appDeliveryRequest("enable-skill", { job_id: job.id });
                    setMessage("业务验证通过，本地应用技能已成功启用");
                  } catch (e) {
                    setMessage(String(e));
                  } finally {
                    setEnablingSkill(false);
                  }
                }}
              >
                <Icon name={enablingSkill ? "refresh" : "sparkle"} size={13} className={enablingSkill ? "animate-spin" : ""} />
                <span>{enablingSkill ? "正在校验…" : "验证并启用本地技能"}</span>
              </button>
            </div>
          )}
        </div>

        {job?.artifact_hash && (
          <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-line/50 text-xs">
            <span className="text-muted font-medium">交付摘要哈希：</span>
            <button
              type="button"
              onClick={() => {
                void navigator.clipboard.writeText(job.artifact_hash || "");
                setMessage("已复制完整交付摘要哈希");
              }}
              title="点击复制完整哈希"
              className="inline-flex items-center gap-1.5 font-mono text-[11px] px-2 py-0.5 rounded-md bg-panel border border-line text-ink hover:border-lineStrong transition-colors cursor-pointer"
            >
              <Icon name="shield" size={11} className="text-ok" />
              <span>{job.artifact_hash}</span>
              <Icon name="copy" size={11} className="text-muted" />
            </button>
          </div>
        )}
      </div>

      {/* Status Notice Toast */}
      {message && (
        <div role="status" className="flex items-center gap-2.5 p-3 rounded-xl bg-panel border border-line/80 text-xs text-ink shadow-2xs transition-all">
          <Icon name="check" size={14} className="text-ok shrink-0" />
          <span className="flex-1 font-medium break-words leading-relaxed">{message}</span>
          <button
            type="button"
            onClick={() => setMessage("")}
            className="text-muted hover:text-ink p-1 rounded hover:bg-paper transition-colors"
            title="关闭提示"
          >
            <Icon name="x" size={12} />
          </button>
        </div>
      )}

      {/* Artifacts Cards Grid */}
      {artifacts.length > 0 ? (
        <div className="grid grid-cols-1 gap-3">
          {artifacts.map(([name, path]) => {
            const meta = getArtifactMeta(name);
            return (
              <div
                key={name}
                className="rounded-xl border border-line/75 bg-paper/95 p-4 shadow-2xs hover:border-lineStrong/50 hover:shadow-xs transition-all space-y-2.5"
              >
                {/* Card Header */}
                <div className="flex flex-wrap items-center justify-between gap-2 pb-2 border-b border-line/50">
                  <div className="flex items-center gap-2">
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md font-semibold text-[11px] border ${meta.badgeClass}`}>
                      <Icon name={meta.icon} size={12} />
                      <span>{meta.badge}</span>
                    </span>
                    <span className="font-semibold text-xs text-ink">{meta.title}</span>
                    <span className="font-mono text-[11px] text-muted font-normal">({name})</span>
                  </div>

                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      title="复制实际文件完整路径"
                      onClick={() => {
                        void navigator.clipboard.writeText(path);
                        setMessage(`已复制 ${name} 的实际文件路径`);
                      }}
                      className="px-2.5 py-1 rounded-md border border-line bg-panel hover:bg-paper text-muted hover:text-ink text-[11px] font-medium flex items-center gap-1 transition-colors cursor-pointer"
                    >
                      <Icon name="copy" size={12} />
                      <span>复制路径</span>
                    </button>
                    <button
                      type="button"
                      title="导出至本地下载目录并在访达中显示"
                      onClick={() => {
                        if (job) {
                          void downloadAppArtifact(job, name)
                            .then(res => setMessage(`已导出至下载目录：${res?.filename || name}，已在访达中显示`))
                            .catch(e => setMessage(String(e)));
                        }
                      }}
                      className="px-2.5 py-1 rounded-md border border-accent/30 bg-accentSoft/40 hover:bg-accentSoft text-accent text-[11px] font-medium flex items-center gap-1 transition-colors cursor-pointer"
                    >
                      <Icon name="archive" size={12} />
                      <span>导出文件</span>
                    </button>
                  </div>
                </div>

                {/* Description */}
                <p className="text-xs text-muted leading-relaxed font-normal">
                  {meta.description}
                </p>

                {/* Path Bar */}
                <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-panel/60 border border-line/50 font-mono text-[11px] text-muted">
                  <Icon name="folder" size={12} className="shrink-0 text-muted" />
                  <span className="truncate flex-1 select-text" title={path}>{path}</span>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="text-center py-10 border border-dashed border-line rounded-xl text-muted text-xs">
          暂无交付文件快照
        </div>
      )}
    </section>
  );
}
