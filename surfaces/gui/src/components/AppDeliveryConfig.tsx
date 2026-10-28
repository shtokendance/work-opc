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
    return "💡 迁移提醒：切换后将连接服务器新库，本机存量数据不会自动迁移；请确保应用具备启动自动建表逻辑或已初始化表结构。";
  }
  if (/redis|memcache/.test(type)) {
    return "✅ 迁移提醒：缓存服务可直接切换，无需迁移本机历史数据。";
  }
  if (/s3|minio|oss|cos|storage/.test(type)) {
    return "💡 迁移提醒：切换后新上传文件将持久保存在服务器存储桶中。";
  }
  return "";
}

export function AppDeliveryConfig({ configs, originals, resources, onChange }: {
  configs: AppConfigItem[]; originals: AppConfigItem[]; resources: ResourceSummary[];
  onChange: (items: AppConfigItem[]) => void;
}) {
  const update = (index: number, value: AppConfigItem) => onChange(configs.map((item, i) => i === index ? value : item));
  const replacements = configs.filter(item => item.resource_id || (item.value_source === "manual" && item.value !== originals.find(o => o.key === item.key)?.value));
  return <section className="space-y-4" aria-label="应用配置对照">
    <div className="rounded-xl border border-line bg-paper/50 p-4 space-y-1.5">
      <h3 className="font-semibold text-ink">核对最终部署配置与服务器资源</h3>
      <p className="text-ui text-muted">
        每一项配置都展示了<b>最终部署生效的值</b>。如果应用原本连接的是本机数据库或缓存，可在右侧一键切换为服务器现成的基础服务；不需要更换的配置保持默认即可。
      </p>
      <p className="text-xs text-muted">原值仅在本机显示核对；密码和密钥不会发给 AI，也不会写进业务技能包。</p>
    </div>
    {configs.length === 0 && <p className="text-muted text-ui">这个应用暂时没有需要填写的配置，可以继续部署。</p>}
    {configs.map((item, index) => {
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
        ? `AI 适配推荐值 (${item.value})`
        : original
        ? "保持原样"
        : "请填写或选择";

      return <article key={index} className="rounded-xl border border-line overflow-hidden bg-panel" aria-label={configName(item)}>
        <header className="bg-paper px-4 py-3 flex flex-wrap items-center justify-between gap-2 border-b border-line">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="font-semibold">{configName(item)}</h4>
            <code className="text-xs px-1.5 py-0.5 rounded bg-panel border border-line text-muted font-mono">{item.key || "未命名"}</code>
            {item.required && <span className="text-xs text-warnInk font-medium">必填</span>}
          </div>
          <span className={`text-xs font-medium px-2 py-0.5 rounded ${changed ? "bg-accentSoft/40 text-accent" : "bg-panel border border-line text-muted"}`}>
            {statusLabel}
          </span>
        </header>

        {/* 直接可见的最终生效配置输入区 */}
        <div className="p-4 border-b border-line bg-panel space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <label className="text-xs font-semibold text-ink">
              部署时最终生效的值
              {selected && <span className="text-accent font-normal ml-2">（已绑定「{selected.name}」，部署时由服务器自动注入；如手动输入将改为使用输入值）</span>}
            </label>
            {changed && original && (
              <button
                type="button"
                className="text-xs text-accent underline"
                onClick={() => update(index, { ...original, resource_id: undefined, value_source: original.value_source || "manual" })}
              >
                恢复原值
              </button>
            )}
          </div>
          {!original && (
            <input
              aria-label="新增配置名称"
              value={item.key}
              placeholder="例如 SERVICE_URL"
              className="w-full border border-line rounded-lg p-2 mb-2 bg-paper text-ui font-mono"
              onChange={e => update(index, { ...item, key: e.target.value, resource_id: undefined, value_source: "manual" })}
            />
          )}
          <input
            aria-label={`${item.key} 配置值`}
            type="text"
            value={item.value}
            placeholder={selected ? `已选择服务器资源「${selected.name}」自动注入；在此输入会改用你填写的值` : "填写后，部署时将使用这个值"}
            className="w-full border border-line rounded-lg px-3 py-2 bg-paper text-ink text-ui font-mono"
            onChange={e => update(index, { ...item, value: e.target.value, resource_id: undefined, value_source: "manual" })}
          />
          {item.description && <p className="text-xs text-muted break-words">{item.description}</p>}
        </div>

        {/* 左右对照：原项目值 vs 服务器可用替换资源 */}
        <div className="grid grid-cols-1 sm:grid-cols-2 divide-y sm:divide-y-0 sm:divide-x divide-line text-xs">
          <div className="p-3.5 space-y-1.5 min-w-0 bg-paper/30">
            <p className="font-medium text-muted">
              {hasOriginalFileSource ? `原项目中的值${original?.source_label ? `（来自 ${original.source_label}）` : ""}` : "默认基准值"}
            </p>
            <textarea
              aria-label={`${item.key} 原始值`}
              readOnly
              rows={2}
              value={original?.value || ""}
              placeholder="原应用未单独配置此项"
              className="w-full resize-y rounded-lg border border-line bg-paper px-3 py-1.5 text-xs font-mono break-all text-muted"
            />
          </div>
          <div className="p-3.5 space-y-2 min-w-0 bg-paper/30">
            <p className="font-medium text-muted">服务器上可替换的现成基础服务</p>
            {candidates.length === 0 ? (
              <p className="text-muted">该配置项属于应用自身参数，无需绑定服务器外部资源。</p>
            ) : candidates.map(resource => {
              const preview = resource.env_preview?.[item.key];
              const active = item.resource_id === resource.id;
              const migrationNote = resourceMigrationNote(resource);
              return <div key={resource.id} className={`rounded-lg border p-3 space-y-1.5 ${active ? "border-accent bg-accentSoft/30" : "border-line bg-panel"}`}>
                <div className="flex items-center justify-between gap-2">
                  <p className="text-ui font-medium text-ink">{resource.name}</p>
                  <button
                    type="button"
                    aria-label={`使用${resource.name}替换 ${item.key}`}
                    disabled={active}
                    className="px-2.5 py-1 rounded-md border border-accent/40 bg-accentSoft/20 text-xs text-accent font-medium hover:bg-accentSoft/40 disabled:opacity-60"
                    onClick={() => update(index, { ...item, value: "", value_source: "resource", resource_id: resource.id })}
                  >
                    {active ? "✓ 已选择这项" : "改用这项"}
                  </button>
                </div>
                {resource.description && <p className="text-xs text-muted break-words">{resource.description}</p>}
                <p className="text-xs text-muted break-all whitespace-pre-wrap font-mono">
                  {preview && preview !== "[REDACTED]" ? preview : "连接地址与凭据由服务器在部署时自动注入，无需手动填写"}
                </p>
                {migrationNote && <p className="text-xs text-ink bg-paper/80 rounded p-2 border border-line leading-relaxed">{migrationNote}</p>}
              </div>;
            })}
          </div>
        </div>

        {/(?:\blocalhost\b|\b127\.\d+\.\d+\.\d+\b|\[::1\])/.test(item.value) && (
          <p role="note" className="px-4 py-3 border-t border-line text-xs text-warnInk bg-warnSoft/40">
            ⚠️ 这个地址（<code>127.0.0.1</code> / <code>localhost</code>）在服务器上指向容器自己，无法连回你电脑上的本地服务。如果是服务监听地址建议改为 <code>0.0.0.0</code>；如果是数据库或缓存连接，请在右侧选择服务器现成资源或填写内网可达地址。
          </p>
        )}
      </article>;
    })}
    <button type="button" className="text-ui text-accent font-medium" onClick={() => onChange([...configs, { key: "", value: "", description: "", value_source: "manual" }])}>＋ 添加其他环境变量配置</button>
    <div className="rounded-xl border border-line bg-paper p-4 text-ui" aria-label="配置更改汇总">
      <p className="font-medium">部署前配置汇总</p>
      <p className="text-muted mt-1">{replacements.length ? `你调整了 ${replacements.length} 项配置或服务器资源，其余配置使用默认或原值。` : "当前全部采用推荐默认值或应用原值，未绑定额外替换资源。"}</p>
      {replacements.map(item => <p key={item.key} className="mt-2 text-xs font-medium text-ink">• {configName(item)} ({item.key}) → {resources.find(r => r.id === item.resource_id)?.name || `自定义值 (${item.value})`}</p>)}
    </div>
  </section>;
}
