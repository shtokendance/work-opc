// Unsent-draft persistence for the composer.
//
// UX: a half-typed message / picked file should survive navigation — switching to
// another conversation, or to Settings / Inbox / another surface and back — instead of
// being wiped the moment the active session changes. Without this, Composer.resetKey
// (= sessionId) cleared the box unconditionally and the draft was gone.
//
// The draft is keyed per session and lives in localStorage, so it also survives a reload
// and restoring an evicted run. It is cleared only once the message is actually sent.
// Mirrors the App.tsx localStorage conventions (best-effort, try/catch around JSON).
import type { Attachment } from "../types";
import type { SessionSkillRow } from "../api";

const DRAFT_KEY_PREFIX = "coworker:composer-draft:v1:";

export interface ComposerDraft {
  text: string;
  attachments: Attachment[];
  // The picked "/" force-run skill (SKILLS-SPEC §4.1 #3): the visible "/name " prefix is
  // UI state derived from this, so restoring it keeps the pending skill intact.
  skill: SessionSkillRow | null;
  // WORK-RESOURCES-001 (DATA-102): optional per-message resource selections.
  // Old drafts simply lack the fields — never backfill guesses.
  knowledgeScope?: KnowledgeScopeDraft | null;
  skillRef?: SkillRefDraft | null;
}

/** Mirror of enterpriseResources' selection shapes, kept draft-local so this
 * module stays dependency-free. */
export interface KnowledgeScopeDraft {
  tenant_id: string;
  knowledge_base_id: string;
  context_id: string;
  label: string;
}

export interface SkillRefDraft {
  source: "catalog";
  tenant_id: string;
  catalog_id: string;
  bundle_sha256: string;
  context_id: string;
  label: string;
}

export function draftKey(sessionId: string): string {
  return DRAFT_KEY_PREFIX + sessionId;
}

/** Best-effort read of the saved draft for a session; undefined when none. */
export function loadDraft(sessionId: string | undefined): ComposerDraft | undefined {
  if (!sessionId) return undefined;
  try {
    const raw = localStorage.getItem(draftKey(sessionId));
    if (!raw) return undefined;
    const parsed = JSON.parse(raw) as ComposerDraft;
    // Defensive: only trust structurally-sane payloads.
    if (typeof parsed !== "object" || parsed === null || typeof parsed.text !== "string") {
      return undefined;
    }
    return {
      text: parsed.text,
      attachments: Array.isArray(parsed.attachments) ? parsed.attachments : [],
      skill: parsed.skill ?? null,
      knowledgeScope: parsed.knowledgeScope ?? null,
      skillRef: parsed.skillRef ?? null,
    };
  } catch {
    return undefined;
  }
}

/** Save the draft for a session. An empty draft is pruned rather than stored. */
export function saveDraft(sessionId: string, draft: ComposerDraft): void {
  try {
    const empty =
      !draft.text.trim() &&
      draft.attachments.length === 0 &&
      !draft.skill &&
      !draft.knowledgeScope &&
      !draft.skillRef;
    if (empty) {
      localStorage.removeItem(draftKey(sessionId));
      return;
    }
    localStorage.setItem(draftKey(sessionId), JSON.stringify(draft));
  } catch {
    /* localStorage may be unavailable; draft persistence is best effort. */
  }
}

/** Drop the saved draft for a session (called after a successful send). */
export function clearDraft(sessionId: string): void {
  try {
    localStorage.removeItem(draftKey(sessionId));
  } catch {
    /* best effort */
  }
}
