import type { AppConfigItem, ResourceSummary } from "../api";

const names: Record<string, string> = {
  DB_DSN: "业务数据库连接", DATABASE_URL: "业务数据库连接", CONNECTION_STRING: "数据库连接",
  PGHOST: "PostgreSQL 数据库地址", PGPORT: "PostgreSQL 端口", PGUSER: "PostgreSQL 用户名", PGPASSWORD: "PostgreSQL 密码", PGDATABASE: "PostgreSQL 数据库名",
  REDIS_URL: "Redis 缓存连接", REDIS_HOST: "Redis 缓存地址", REDIS_PORT: "Redis 缓存端口",
  MONGODB_URI: "MongoDB 数据库连接", MONGODB_URL: "MongoDB 数据库连接",
  S3_ENDPOINT: "对象存储服务地址", S3_BUCKET: "对象存储桶名称", S3_ACCESS_KEY: "对象存储访问账号", S3_SECRET_KEY: "对象存储访问密钥",
  ALY_ACCESS_KEY_ID: "阿里云访问账号", ALY_ACCESS_KEY_SECRET: "阿里云访问密钥",
  ALY_ORG_ID: "阿里云组织", ALY_REGION_ID: "阿里云区域", ALY_ENDPOINT: "阿里云服务地址",
  HOST: "服务监听地址", PORT: "应用运行端口", NODE_ENV: "运行环境模式",
  ALLOW_PUBLIC_REGISTRATION: "开放公开注册开关",
};
export const configName = (item: AppConfigItem) => names[item.key] || item.description?.split(/[。；\n]/)[0] || item.key || "新增配置";

const CONTAINER_DEFAULT_KEYS = new Set(["PORT", "HOST", "BIND_HOST", "LISTEN_HOST", "NODE_ENV"]);

export function isContainerDefaultConfig(item: AppConfigItem) {
  return CONTAINER_DEFAULT_KEYS.has((item.key || "").toUpperCase()) && !needsConnectionDecision(item);
}

export function needsConnectionDecision(item: AppConfigItem) {
  return !["HOST", "PORT", "BIND_HOST", "LISTEN_HOST"].includes(item.key.toUpperCase()) && /URL|URI|DSN|HOST|ENDPOINT|CONNECTION|SERVER/i.test(item.key)
    && /(?:^|:\/\/|@|tcp\(|(?:host|server)\s*=)(?:localhost|127\.\d+\.\d+\.\d+|\[::1\])(?=[:/,;)]|$)/i.test(item.value.trim()) && !item.local_connection_confirmed;
}

function matches(item: AppConfigItem, resource: ResourceSummary) {
  if (!resource.env_keys.includes(item.key)) return false;
  // A shared key such as DATABASE_URL is not proof that database types are interchangeable.
  if (/DATABASE|DB_DSN|CONNECTION_STRING/i.test(item.key)) {
    const type = `${item.value} ${item.description || ""}`.match(/mysql|postgres(?:ql)?|sqlserver|mongodb/i)?.[0]?.toLowerCase()
      || (/@tcp\(/.test(item.value) ? "mysql" : "");
    if (type && resource.type.toLowerCase().replace("postgresql", "postgres") !== type.replace("postgresql", "postgres")) return false;
  }
  return true;
}

function resourceMigrationNote(resource: ResourceSummary) {
  const type = (resource.type || "").toLowerCase();
  if (/postgres|mysql|mariadb|sqlserver|mssql|mongo/.test(type)) {
    return "💡 迁移提醒：切换后将连接所选数据库，本机存量数据不会自动迁移；需核对目标表结构；初始化和迁移数据需要另外确认。";
  }
  if (/redis|memcache/.test(type)) {
    return "请先确认用途：可重建缓存通常无需迁移；登录会话和业务状态需要另行处理，不能直接丢弃。";
  }
  if (/s3|minio|oss|cos|storage/.test(type)) {
    return "💡 迁移提醒：切换后新上传文件将持久保存在服务器存储桶中。";
  }
  return "";
}

export function AppDeliveryConfig({ configs, originals, resources, onChange, compact = false }: {
  configs: AppConfigItem[]; originals: AppConfigItem[]; resources: ResourceSummary[];
  compact?: boolean;
  onChange: (items: AppConfigItem[]) => void;
}) {
  const update = (index: number, value: AppConfigItem) => onChange(configs.map((item, i) => i === index ? value : item));
  const replacements = configs.filter(item => item.resource_id || (item.value_source === "manual" && item.value !== originals.find(o => o.key === item.key)?.value));

  const renderConfigCard = (item: AppConfigItem, index: number) => {
    const original = originals.find(o => o.key === item.key);
    const candidates = resources.filter(r => matches(original || item, r));
    const selected = resources.find(r => r.id === item.resource_id);
    const changed = !!item.resource_id || (!!original && item.value !== original.value);
    const hasOriginalFileSource = original?.value_source === "original";
    const statusLabel = selected
      ? `将改用：${selected.name}`
      : changed
      ? "将使用你填写的新值"
      : hasOriginalFileSource
      ? "保留原配置值"
      : item.value
      ? "采用 AI 建议值"
      : original
      ? "保持原样"
      : "请填写或选择";

    return (
      <article key={index} className="rounded-xl border border-line p-4 bg-panel space-y-3" aria-label={configName(item)}>
        <header className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <h4 className="font-semibold text-ink">
              {configName(item)}
              {item.required && <span className="text-xs font-normal text-muted ml-1">· 必填</span>}
            </h4>
            <code className="px-1.5 py-0.5 rounded bg-paper border border-line text-[11px] text-muted font-mono">
              {item.key || "未命名"}
            </code>
          </div>
          <span
            className={`px-2 py-0.5 rounded-full text-xs font-medium ${
              changed ? "bg-accentSoft text-accent" : "bg-paper text-muted"
            }`}
          >
            {statusLabel}
          </span>
        </header>

        {!original && (
          <input
            aria-label="新增配置名称"
            value={item.key}
            placeholder="例如 SERVICE_URL"
            className="w-full border border-line rounded-lg px-3 py-2 bg-paper font-mono text-xs"
            onChange={e => update(index, { ...item, key: e.target.value, resource_id: undefined, value_source: "manual" })}
          />
        )}

        <div className="grid md:grid-cols-2 gap-3 text-xs">
          <label className="space-y-1.5">
            <span className="block text-muted">
              {hasOriginalFileSource ? "原应用的值（默认保留）" : "AI 建议的初始值"}
            </span>
            <textarea
              aria-label={`${item.key} 原始值`}
              readOnly
              rows={1}
              value={original?.value || ""}
              placeholder="此项尚无值"
              className="w-full min-h-[38px] border border-line rounded-lg px-3 py-2 bg-paper text-muted font-mono resize-none leading-normal"
            />
          </label>
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="font-medium text-ink">部署时使用</span>
              {changed && original && (
                <button
                  type="button"
                  className="text-accent hover:underline cursor-pointer"
                  onClick={() => update(index, { ...original, resource_id: undefined, value_source: original.value_source || "manual" })}
                >
                  恢复原值
                </button>
              )}
            </div>
            <input
              aria-label={`${item.key} 配置值`}
              type="text"
              value={item.value}
              placeholder={selected ? `由「${selected.name}」提供` : "保留原值，或填写你确认的新值"}
              className="w-full min-h-[38px] border border-line rounded-lg px-3 py-2 bg-panel text-ink font-mono focus:outline-accent focus:border-accent"
              onChange={e => update(index, { ...item, value: e.target.value, resource_id: undefined, local_connection_confirmed: false, value_source: "manual" })}
            />
            {selected && <span className="block text-accent">已选择服务器资源，真实值由服务器注入。</span>}
          </div>
        </div>

        {!!candidates.length && (
          <div className="border-t border-line pt-3 text-xs space-y-2">
            <p className="text-muted">可一键切换为以下在线服务器资源：</p>
            <div className="flex flex-wrap gap-2">
              {candidates.map(resource => (
                <button
                  key={resource.id}
                  type="button"
                  aria-label={`使用${resource.name}替换 ${item.key}`}
                  disabled={item.resource_id === resource.id}
                  className="border border-line rounded-lg px-3 py-1.5 text-accent hover:bg-accentSoft/40 disabled:bg-accentSoft font-medium transition-colors cursor-pointer"
                  onClick={() => update(index, { ...item, value: "", value_source: "resource", resource_id: resource.id })}
                >
                  {item.resource_id === resource.id ? "✓ " : "改用 "}
                  {resource.name}
                </button>
              ))}
            </div>
            {selected && (
              <div className="text-muted space-y-1 bg-paper/60 p-2.5 rounded-lg">
                <p>
                  {selected.env_preview?.[item.key] && selected.env_preview[item.key] !== "[REDACTED]"
                    ? selected.env_preview[item.key]
                    : "连接凭据由服务器安全提供"}
                </p>
                {resourceMigrationNote(selected) && <p>{resourceMigrationNote(selected)}</p>}
              </div>
            )}
          </div>
        )}

        {item.description && (
          <details className="text-xs text-muted">
            <summary className="cursor-pointer hover:text-ink">用途与来源说明</summary>
            <p className="mt-1.5">{item.description}</p>
            {original?.source_label && <p className="mt-1">{original.source_label}</p>}
          </details>
        )}

        {(needsConnectionDecision(item) || item.local_connection_confirmed) && (
          <div role="note" className="text-xs text-warnInk space-y-2 border-t border-line pt-3">
            <p>这个连接指向容器自己，部署后不能连接你电脑上的服务。请选择上面的服务器资源，或填写服务器能访问的地址。</p>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={!!item.local_connection_confirmed}
                onChange={e => update(index, { ...item, local_connection_confirmed: e.target.checked })}
              />
              只有该服务确实和应用放在同一个容器内时，才勾选保留此连接
            </label>
          </div>
        )}
      </article>
    );
  };

  const indexedConfigs = configs.map((item, index) => ({ item, index }));
  const primaryConfigs = compact
    ? indexedConfigs
    : indexedConfigs.filter(({ item }) => !isContainerDefaultConfig(item));
  const containerDefaultConfigs = compact
    ? []
    : indexedConfigs.filter(({ item }) => isContainerDefaultConfig(item));

  return (
    <section className="space-y-3.5" aria-label="应用配置对照">
      {!compact && (
        <div className="rounded-xl border border-line bg-paper/50 px-4 py-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="font-semibold text-ink text-sm">核对最终部署配置与服务器资源</h3>
            <p className="text-xs text-muted mt-0.5">
              左侧为原值/建议值，右侧为最终部署生效值；支持一键绑定匹配的服务器资源。密码与密钥仅在本机核对，不会发给 AI。
            </p>
          </div>
          <button
            type="button"
            className="px-3 py-1.5 rounded-lg border border-line bg-panel hover:bg-paper text-xs text-accent font-medium transition-colors cursor-pointer shrink-0"
            onClick={() => onChange([...configs, { key: "", value: "", description: "", value_source: "manual" }])}
          >
            ＋ 添加环境变量
          </button>
        </div>
      )}

      {configs.length === 0 && (
        <div className="rounded-xl border border-line bg-paper/40 p-5 text-center text-muted text-ui">
          这个应用暂时没有需要填写的配置，可以直接继续部署。
        </div>
      )}

      {configs.length > 0 && primaryConfigs.length === 0 && (
        <div className="rounded-xl border border-okLine bg-okSoft/25 px-4 py-3 text-xs text-ink">
          ✓ 当前应用无需额外配置外部数据库或密钥；容器运行端口与监听地址已自动适配，可直接部署。
        </div>
      )}

      {primaryConfigs.map(({ item, index }) => renderConfigCard(item, index))}

      {containerDefaultConfigs.length > 0 && (
        <details className="rounded-xl border border-line bg-paper/40 p-3.5 text-xs">
          <summary className="cursor-pointer font-medium text-muted hover:text-ink select-none">
            容器基础参数（已自动适配，通常无需修改 · {containerDefaultConfigs.length} 项：
            {containerDefaultConfigs.map(({ item }) => `${item.key}=${item.value}`).join("、")}）
          </summary>
          <div className="space-y-3 mt-3 pt-3 border-t border-line">
            {containerDefaultConfigs.map(({ item, index }) => renderConfigCard(item, index))}
          </div>
        </details>
      )}

      {!compact && configs.length > 0 && (
        <div className="rounded-xl border border-line bg-paper px-4 py-3 text-xs" aria-label="配置更改汇总">
          <div className="flex items-center justify-between gap-2">
            <span className="font-medium text-ink">部署前配置汇总</span>
            <span className="text-muted">
              {replacements.length
                ? `已调整 ${replacements.length} 项配置或服务器资源`
                : "全部采用推荐默认值或应用原值"}
            </span>
          </div>
          {replacements.map(item => (
            <p key={item.key} className="mt-1.5 font-medium text-ink">
              • {configName(item)} ({item.key}) → {resources.find(r => r.id === item.resource_id)?.name || `自定义值 (${item.value})`}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}
