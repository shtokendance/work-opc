import { useState } from "react";
import { useTranslation } from "react-i18next";
import { EnterpriseSkillsPanel } from "./EnterpriseSkillsPanel";
import { AppSkillsPanel } from "./AppSkillsPanel";
import { SkillsTab } from "./SkillsTab";

export function SkillsView({
  onNavigateToApps,
}: {
  onNavigateToApps?: () => void;
}) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<"enterprise" | "app" | "personal">("enterprise");

  return (
    <div className="max-w-4xl mx-auto py-4 px-2" data-testid="skills-view">
      <div className="flex items-center gap-2 px-4 pb-3 border-b border-line mb-3">
        <button
          data-testid="skills-tab-enterprise"
          className={
            "text-ui px-3.5 py-1.5 rounded-lg border font-medium transition-colors " +
            (tab === "enterprise"
              ? "border-accent bg-accent/10 text-accent font-semibold"
              : "border-line text-muted hover:text-ink hover:border-lineStrong")
          }
          onClick={() => setTab("enterprise")}
        >
          {t("skills.tab_enterprise", "企业技能")}
        </button>
        <button
          data-testid="skills-tab-app"
          className={
            "text-ui px-3.5 py-1.5 rounded-lg border font-medium transition-colors " +
            (tab === "app"
              ? "border-accent bg-accent/10 text-accent font-semibold"
              : "border-line text-muted hover:text-ink hover:border-lineStrong")
          }
          onClick={() => setTab("app")}
        >
          {t("skills.tab_app", "应用技能")}
        </button>
        <button
          data-testid="skills-tab-personal"
          className={
            "text-ui px-3.5 py-1.5 rounded-lg border font-medium transition-colors " +
            (tab === "personal"
              ? "border-accent bg-accent/10 text-accent font-semibold"
              : "border-line text-muted hover:text-ink hover:border-lineStrong")
          }
          onClick={() => setTab("personal")}
        >
          {t("skills.tab_personal", "我的技能")}
        </button>
      </div>

      <div>
        {tab === "enterprise" && <EnterpriseSkillsPanel />}
        {tab === "app" && <AppSkillsPanel onNavigateToApps={onNavigateToApps} />}
        {tab === "personal" && <SkillsTab />}
      </div>
    </div>
  );
}

export default SkillsView;
