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
  return <section className="space-y-4" aria-label="应用配置对照">
    {!compact && <div className="rounded-xl border border-line bg-paper/50 p-4 space-y-1.5">
      <h3 className="font-semibold text-ink">核对最终部署配置与服务器资源</h3>
      <p className="text-ui text-muted">
        每一项配置都展示了<b>最终部署生效的值</b>。左边是原值，右边是部署时使用的值；有匹配的服务器资源时会列在下方。不需要更换的保持原样即可。
      </p>
      <p className="text-xs text-muted">原值仅在本机显示核对；密码和密钥不会发给 AI，也不会写进业务技能包。</p>
    </div>}
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
        ? "采用 AI 建议值"
        : original
        ? "保持原样"
        : "请填写或选择";

      return <article key={index} className="rounded-xl border border-line p-4 bg-panel space-y-3" aria-label={configName(item)}>
        <header className="flex flex-wrap justify-between gap-2">
          <div><h4 className="font-semibold">{configName(item)} {item.required && <span className="text-xs text-muted">· 必填</span>}</h4><code className="text-xs text-muted">{item.key || "未命名"}</code></div>
          <span className={`text-xs ${changed ? "text-accent" : "text-muted"}`}>{statusLabel}</span>
        </header>
        {!original && <input aria-label="新增配置名称" value={item.key} placeholder="例如 SERVICE_URL" className="w-full border border-line rounded p-2" onChange={e => update(index, {...item, key:e.target.value, resource_id:undefined, value_source:"manual"})} />}
        <div className="grid md:grid-cols-2 gap-4 text-xs">
          <label className="space-y-2"><span className="block text-muted">{hasOriginalFileSource ? "原应用的值（默认保留）" : "AI 建议的初始值"}</span><textarea aria-label={`${item.key} 原始值`} readOnly rows={2} value={original?.value || ""} placeholder="此项尚无值" className="w-full border border-line rounded-lg p-2 bg-paper font-mono resize-y" /></label>
          <label className="space-y-2"><span className="block font-medium">部署时使用</span><input aria-label={`${item.key} 配置值`} type="text" value={item.value} placeholder={selected ? `由「${selected.name}」提供` : "保留原值，或填写你确认的新值"} className="w-full border border-line rounded-lg p-2 bg-panel font-mono" onChange={e => update(index, {...item, value:e.target.value, resource_id:undefined, local_connection_confirmed:false, value_source:"manual"})} />
            {selected && <span className="block text-accent">已选择服务器资源，真实值由服务器注入。</span>}
            {changed && original && <button type="button" className="text-accent underline" onClick={() => update(index, {...original, resource_id:undefined, value_source:original.value_source || "manual"})}>恢复原值</button>}
          </label>
        </div>
        {!!candidates.length && <div className="border-t border-line pt-3 text-xs space-y-2"><p className="text-muted">也可以换成以下服务器资源（点击才选择）：</p><div className="flex flex-wrap gap-2">{candidates.map(resource => <button key={resource.id} type="button" aria-label={`使用${resource.name}替换 ${item.key}`} disabled={item.resource_id === resource.id} className="border border-line rounded-lg px-3 py-2 text-accent disabled:bg-accentSoft" onClick={() => update(index, {...item, value:"", value_source:"resource", resource_id:resource.id})}>{item.resource_id === resource.id ? "✓ " : "改用 "}{resource.name}</button>)}</div>
          {selected && <div className="text-muted space-y-1"><p>{selected.env_preview?.[item.key] && selected.env_preview[item.key] !== "[REDACTED]" ? selected.env_preview[item.key] : "连接凭据由服务器提供"}</p><p>{resourceMigrationNote(selected)}</p></div>}
        </div>}
        {item.description && <details className="text-xs text-muted"><summary className="cursor-pointer">用途与来源说明</summary><p className="mt-2">{item.description}</p>{original?.source_label && <p>{original.source_label}</p>}</details>}
        {(needsConnectionDecision(item) || item.local_connection_confirmed) && <div role="note" className="text-xs text-warnInk space-y-2 border-t border-line pt-3"><p>这个连接指向容器自己，部署后不能连接你电脑上的服务。请选择上面的服务器资源，或填写服务器能访问的地址。</p><label className="flex gap-2"><input type="checkbox" checked={!!item.local_connection_confirmed} onChange={e => update(index, {...item, local_connection_confirmed:e.target.checked})} />只有该服务确实和应用放在同一个容器内时，才勾选保留此连接</label></div>}
      </article>;
    })}
    {!compact && <details className="text-xs text-muted"><summary className="cursor-pointer">高级配置</summary><button type="button" className="text-ui text-accent font-medium mt-3" onClick={() => onChange([...configs, { key: "", value: "", description: "", value_source: "manual" }])}>＋ 添加其他环境变量配置</button></details>}
    {!compact && <div className="rounded-xl border border-line bg-paper p-4 text-ui" aria-label="配置更改汇总">
      <p className="font-medium">部署前配置汇总</p>
      <p className="text-muted mt-1">{replacements.length ? `你调整了 ${replacements.length} 项配置或服务器资源，其余配置使用默认或原值。` : "当前全部采用推荐默认值或应用原值，未绑定额外替换资源。"}</p>
      {replacements.map(item => <p key={item.key} className="mt-2 text-xs font-medium text-ink">• {configName(item)} ({item.key}) → {resources.find(r => r.id === item.resource_id)?.name || `自定义值 (${item.value})`}</p>)}
    </div>}
  </section>;
}
