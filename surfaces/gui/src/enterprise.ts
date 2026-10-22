// Enterprise (OPC platform) sign-in state machine — the sidebar account row's
// "企业登录" entry (TASK-W02).
//
// Three user-visible states: idle → logging in (system-browser flow) →
// signed in | failed. The refresh token NEVER lives in this page's storage:
// the login/rotation responses hand it straight to the desktop shell's
// Keychain command (`enterprise_secret_*`, BND-160), and each refresh presents
// it once to the sidecar. The sidecar holds only the in-memory access token.
// Browser builds degrade to a "desktop app only" failure.

import { httpBase } from "./api";

export const ENTERPRISE_KEYCHAIN_PROFILE = "default";

export type EnterpriseState =
  | { kind: "idle" }
  | { kind: "logging_in" } // browser flow in flight
  | { kind: "signed_in"; subject: string; tenant: string }
  | { kind: "failed"; error: string };

export type EnterpriseErrorBody = { error?: { code?: string; message?: string } };

type Listener = (s: EnterpriseState) => void;

/** The Keychain bridge — in the desktop app these are the Tauri commands. */
export interface EnterpriseSecretBridge {
  put(profile: string, secret: string): Promise<unknown>;
  get(profile: string): Promise<string | null>;
  delete(profile: string): Promise<unknown>;
}

export interface EnterpriseDeps {
  isDesktop(): boolean;
  open(url: string): void;
  secrets: EnterpriseSecretBridge;
  /** Bound-3s polls while the browser flow runs, matching the cloud pattern. */
  pollDelayMs: number;
  maxPolls: number;
  /** Refresh this long BEFORE the access token dies (BND-102/160). */
  refreshSkewMs: number;
}

const errorCode = (body: EnterpriseErrorBody | null, fallback: string): string =>
  body?.error?.code || fallback;

/** The expiry hints the sidecar's session view carries (absent on older builds). */
interface EnterpriseExpiryHint {
  seconds_until_expiry?: number;
  refresh_recommended?: boolean;
}

export interface EnterpriseSession {
  state(): EnterpriseState;
  subscribe(listener: Listener): () => void;
  /** Re-read the sidecar's session (mount / menu open). */
  syncFromServer(): Promise<void>;
  beginLogin(): Promise<void>;
  /** Present the Keychain-held refresh to the sidecar; store the rotated one. */
  refreshSession(): Promise<void>;
  logout(): Promise<void>;
  dispose(): void;
}

export function createEnterpriseSession(deps: EnterpriseDeps): EnterpriseSession {
  let state: EnterpriseState = { kind: "idle" };
  const listeners = new Set<Listener>();
  let pollTimer: ReturnType<typeof setTimeout> | null = null;
  let refreshTimer: ReturnType<typeof setTimeout> | null = null;
  let disposed = false;

  const setState = (next: EnterpriseState) => {
    if (disposed) return;
    state = next;
    listeners.forEach((l) => l(state));
  };

  const stopPolling = () => {
    if (pollTimer !== null) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  };

  const stopRefreshTimer = () => {
    if (refreshTimer !== null) {
      clearTimeout(refreshTimer);
      refreshTimer = null;
    }
  };

  // 认证头与 api.ts 同一来源：企业端点也在 sidecar token 门后（联调实测：
  // 缺 x-openworker-token 一律 401，前端读到无 code 的响应体而报 start failed）。
  const launchToken = (): string =>
    (globalThis as any).__COWORKER_API_TOKEN__ ||
    (import.meta as any).env?.VITE_COWORKER_API_TOKEN ||
    "";
  const post = async (path: string, body?: unknown): Promise<Response> =>
    fetch(`${httpBase()}${path}`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-openworker-token": launchToken(),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });

  const readError = async (res: Response, fallback: string): Promise<string> => {
    try {
      return errorCode((await res.json()) as EnterpriseErrorBody, fallback);
    } catch {
      return fallback;
    }
  };

  const finishLogin = async (loginTxnId: string, polls: number): Promise<void> => {
    if (disposed) return;
    if (polls >= deps.maxPolls) {
      setState({ kind: "failed", error: "timeout" });
      return;
    }
    try {
      const res = await fetch(`${httpBase()}/v1/enterprise/login/result?login_txn_id=${loginTxnId}`, {
        headers: { "x-openworker-token": launchToken() },
      });
      if (res.status === 404) {
        // Unknown/failed terminal state — the body carries the BND-180 code.
        setState({ kind: "failed", error: await readError(res, "login failed") });
        return;
      }
      const body = (await res.json()) as {
        state?: string;
        error?: { code?: string };
        refresh_token?: string;
        context?: ({ subject?: string; tenant?: string } & EnterpriseExpiryHint) | null;
      };
      if (body.state === "succeeded") {
        if (body.refresh_token) {
          // One-time handoff → straight into the Keychain; never page storage.
          await deps.secrets.put(ENTERPRISE_KEYCHAIN_PROFILE, body.refresh_token);
        }
        setState({
          kind: "signed_in",
          subject: body.context?.subject || "",
          tenant: body.context?.tenant || "",
        });
        scheduleRefresh(body.context);
        return;
      }
      if (body.state === "failed") {
        setState({ kind: "failed", error: body.error?.code || "login failed" });
        return;
      }
      pollTimer = setTimeout(
        () => void finishLogin(loginTxnId, polls + 1),
        deps.pollDelayMs,
      );
    } catch {
      pollTimer = setTimeout(
        () => void finishLogin(loginTxnId, polls + 1),
        deps.pollDelayMs,
      );
    }
  };

  const syncFromServer = async (): Promise<void> => {
    try {
      const res = await fetch(`${httpBase()}/v1/enterprise/session`, {
        headers: { "x-openworker-token": launchToken() },
      });
      if (res.status === 200) {
        const body = (await res.json()) as {
          subject?: string;
          tenant?: string;
        } & EnterpriseExpiryHint;
        setState({ kind: "signed_in", subject: body.subject || "", tenant: body.tenant || "" });
        scheduleRefresh(body);
      } else if (state.kind === "signed_in") {
        stopRefreshTimer();
        setState({ kind: "idle" });
      }
    } catch {
      /* sidecar not up yet — keep the current state */
    }
  };

  const refreshSession = async (): Promise<void> => {
    let refresh = "";
    try {
      refresh = (await deps.secrets.get(ENTERPRISE_KEYCHAIN_PROFILE)) || "";
    } catch {
      refresh = "";
    }
    if (!refresh) {
      // Nothing to present — the Keychain entry is gone.
      stopRefreshTimer();
      setState({ kind: "idle" });
      return;
    }
    try {
      const res = await post("/v1/enterprise/session/refresh", { refresh_token: refresh });
      if (res.status === 200) {
        const body = (await res.json()) as { refresh_token?: string };
        if (body.refresh_token) {
          await deps.secrets.put(ENTERPRISE_KEYCHAIN_PROFILE, body.refresh_token);
        }
        return; // stay signed in
      }
      stopRefreshTimer();
      const code = await readError(res, "refresh failed");
      if (code === "REFRESH_REUSE_DETECTED") {
        // The family is revoked: drop the local entry and context.
        await deps.secrets.delete(ENTERPRISE_KEYCHAIN_PROFILE).catch(() => {});
        setState({ kind: "idle" });
        return;
      }
      setState({ kind: "failed", error: code });
    } catch {
      stopRefreshTimer();
      setState({ kind: "failed", error: "sidecar-unreachable" });
    }
  };

  // The production refresh path (BND-102/160): while signed in, fire a refresh
  // shortly BEFORE the access token dies, using the sidecar's own expiry hint;
  // a successful rotation re-reads the session view and reschedules.
  const refreshAndReschedule = async (): Promise<void> => {
    refreshTimer = null;
    await refreshSession();
    if (disposed || state.kind !== "signed_in") return;
    await syncFromServer();
  };

  function scheduleRefresh(hint?: EnterpriseExpiryHint | null): void {
    stopRefreshTimer();
    if (disposed || state.kind !== "signed_in") return;
    const secs = hint?.seconds_until_expiry;
    if (typeof secs === "number") {
      refreshTimer = setTimeout(
        () => void refreshAndReschedule(),
        Math.max(0, secs * 1000 - deps.refreshSkewMs),
      );
    } else if (hint?.refresh_recommended) {
      // No precise lifetime (older sidecar) but it asked for a refresh: do it now.
      void refreshAndReschedule();
    }
  }

  return {
    state: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },

    syncFromServer,

    async beginLogin() {
      if (!deps.isDesktop()) {
        setState({ kind: "failed", error: "desktop-only" });
        return;
      }
      setState({ kind: "logging_in" });
      let loginTxnId = "";
      let authorizeUrl = "";
      try {
        const res = await post("/v1/enterprise/login/start");
        const body = (await res.json()) as {
          ok?: boolean;
          authorize_url?: string;
          login_txn_id?: string;
          error?: { code?: string };
        };
        if (!body.ok || !body.authorize_url || !body.login_txn_id) {
          setState({ kind: "failed", error: body.error?.code || "start failed" });
          return;
        }
        loginTxnId = body.login_txn_id;
        authorizeUrl = body.authorize_url;
      } catch {
        setState({ kind: "failed", error: "sidecar-unreachable" });
        return;
      }
      // The system browser does the WeCom half; the loopback callback lands in
      // the sidecar and we poll the login result (same shape as cloud sign-in).
      deps.open(authorizeUrl);
      await finishLogin(loginTxnId, 0);
    },

    refreshSession,

    async logout() {
      stopPolling();
      stopRefreshTimer();
      let refresh = "";
      try {
        refresh = (await deps.secrets.get(ENTERPRISE_KEYCHAIN_PROFILE)) || "";
      } catch {
        refresh = "";
      }
      try {
        await post("/v1/enterprise/logout", { refresh_token: refresh });
      } catch {
        /* local cleanup proceeds regardless */
      }
      await deps.secrets.delete(ENTERPRISE_KEYCHAIN_PROFILE).catch(() => {});
      setState({ kind: "idle" });
    },

    dispose() {
      disposed = true;
      stopPolling();
      stopRefreshTimer();
      listeners.clear();
    },
  };
}
