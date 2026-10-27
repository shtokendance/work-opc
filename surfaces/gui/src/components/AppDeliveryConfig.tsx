import type { AppConfigItem, ResourceSummary } from "../api";

const names: Record<string, string> = {
  DB_DSN: "业务数据库连接", DATABASE_URL: "业务数据库连接", CONNECTION_STRING: "数据库连接",
  REDIS_URL: "Redis 缓存连接", MONGODB_URI: "MongoDB 数据库连接", MONGODB_URL: "MongoDB 数据库连接",
  ALY_ACCESS_KEY_ID: "阿里云访问账号", ALY_ACCESS_KEY_SECRET: "阿里云访问密钥",
  ALY_ORG_ID: "阿里云组织", ALY_REGION_ID: "阿里云区域", ALY_ENDPOINT: "阿里云服务地址", PORT: "应用运行端口",
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

export function AppDeliveryConfig({ configs, originals, resources, onChange }: {
  configs: AppConfigItem[]; originals: AppConfigItem[]; resources: ResourceSummary[];
  onChange: (items: AppConfigItem[]) => void;
}) {
  const update = (index: number, value: AppConfigItem) => onChange(configs.map((item, i) => i === index ? value : item));
  const replacements = configs.filter(item => item.resource_id || (item.value_source === "manual" && item.value !== originals.find(o => o.key === item.key)?.value));
  return <section className="space-y-4" aria-label="应用配置对照">
    <div>
      <h3 className="font-semibold text-ink">这些配置要不要更换？</h3>
      <p className="text-ui text-muted mt-1">左边是应用原来的值，右边是服务器上可用的替换项。不需要更换就保持原样；每次更换都由你决定。</p>
      <p className="text-xs text-muted mt-2">原值直接显示，方便核对；密码和密钥不会交给 AI，也不会写进业务技能。</p>
    </div>
    {configs.length === 0 && <p className="text-muted text-ui">这个应用暂时没有需要填写的配置，可以继续部署。</p>}
    {configs.map((item, index) => {
      const original = originals.find(o => o.key === item.key);
      const candidates = resources.filter(r => matches(original || item, r));
      const selected = resources.find(r => r.id === item.resource_id);
      const changed = !!item.resource_id || (!!original && item.value !== original.value);
      return <article key={index} className="rounded-xl border border-line overflow-hidden" aria-label={configName(item)}>
        <header className="bg-paper px-4 py-3 flex flex-wrap items-center justify-between gap-2">
          <h4 className="font-semibold">{configName(item)}{item.required && <span className="text-xs text-muted font-normal ml-2">需要有值</span>}</h4>
          <span className={`text-xs ${changed ? "text-accent" : "text-muted"}`}>{selected ? `将改用：${selected.name}` : changed ? "将使用你填写的新值" : original ? "保持原样" : "请填写或选择"}</span>
        </header>
        <div className="grid grid-cols-1 sm:grid-cols-2 divide-y sm:divide-y-0 sm:divide-x divide-line">
          <div className="p-4 space-y-2 min-w-0">
            <p className="text-xs font-medium text-muted">原来的值</p>
            <textarea aria-label={`${item.key} 原始值`} readOnly rows={2} value={original?.value || ""} placeholder="原应用没有提供这个值" className="w-full resize-y rounded-lg border border-line bg-paper px-3 py-2 text-ui break-all" />
            {changed && original && <button className="text-xs text-accent underline" onClick={() => update(index, { ...original, resource_id: undefined, value_source: original.value_source || "manual" })}>恢复原值</button>}
          </div>
          <div className="p-4 space-y-2 min-w-0">
            <p className="text-xs font-medium text-muted">服务器上可替换的配置</p>
            {candidates.length === 0 ? <p className="text-ui text-muted">没有找到对应配置，可以保留原值。</p> : candidates.map(resource => {
              const preview = resource.env_preview?.[item.key];
              const active = item.resource_id === resource.id;
              return <div key={resource.id} className={`rounded-lg border p-3 space-y-2 ${active ? "border-accent bg-accentSoft/30" : "border-line"}`}>
                <p className="text-ui font-medium">{resource.name}</p>
                {resource.description && <p className="text-xs text-muted break-words">{resource.description}</p>}
                <p className="text-xs text-muted break-all whitespace-pre-wrap">{preview && preview !== "[REDACTED]" ? preview : "由服务器提供完整值，不需要手动填写"}</p>
                <button aria-label={`使用${resource.name}替换 ${item.key}`} disabled={active} className="text-xs text-accent underline disabled:no-underline disabled:opacity-60" onClick={() => update(index, { ...item, value: "", value_source: "resource", resource_id: resource.id })}>{active ? "已选择这项" : "改用这项"}</button>
              </div>;
            })}
          </div>
        </div>
        <details className="px-4 py-3 border-t border-line text-xs text-muted">
          <summary className="cursor-pointer">自己填写其他值 / 查看技术信息</summary>
          <p className="mt-3 mb-2 break-all">配置名称：{item.key || "未命名"}{original?.source_label ? ` · 来自 ${original.source_label}` : ""}</p>
          {!original && <input aria-label="新增配置名称" value={item.key} placeholder="例如 SERVICE_URL" className="w-full border border-line rounded-lg p-2 mb-2 bg-paper" onChange={e => update(index, { ...item, key: e.target.value, resource_id: undefined, value_source: "manual" })} />}
          <input aria-label={`${item.key} 配置值`} type="text" value={item.value} placeholder={selected ? "正在使用上方服务器配置；填写会改用你输入的值" : "填写后，部署时将使用这个值"} className="w-full border border-line rounded-lg p-2 bg-paper text-ink" onChange={e => update(index, { ...item, value: e.target.value, resource_id: undefined, value_source: "manual" })} />
          {item.description && <p className="mt-2 break-words">{item.description}</p>}
        </details>
        {/(?:\blocalhost\b|\b127\.\d+\.\d+\.\d+\b|\[::1\])/.test(item.value) && <p role="note" className="px-4 py-3 border-t border-line text-xs text-warnInk">这个地址指向应用所在的容器自己。如果你要连接的是服务器上另一个服务，请选择右侧配置或让管理员提供地址；若确实是应用自身的服务，可以保留。</p>}
      </article>;
    })}
    <button className="text-ui text-accent" onClick={() => onChange([...configs, { key: "", value: "", description: "", value_source: "manual" }])}>＋ 添加其他配置</button>
    <div className="rounded-xl border border-line bg-paper p-4 text-ui" aria-label="配置更改汇总">
      <p className="font-medium">部署前再看一眼</p>
      <p className="text-muted mt-1">{replacements.length ? `你更改了 ${replacements.length} 项，其余配置保持原样。` : "你还没有更换配置，将保留应用原来的值。"}</p>
      {replacements.map(item => <p key={item.key} className="mt-2">{configName(item)} → {resources.find(r => r.id === item.resource_id)?.name || "你填写的新值"}</p>)}
    </div>
  </section>;
}
