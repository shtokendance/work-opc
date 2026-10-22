// State-machine tests for the sidebar's enterprise sign-in (TASK-W02).
// The refresh token must reach the Keychain bridge and nothing else; reuse
// detection drops the local entry and context; the browser build degrades.

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createEnterpriseSession,
  ENTERPRISE_KEYCHAIN_PROFILE,
  type EnterpriseDeps,
  type EnterpriseState,
} from "./enterprise";

function makeDeps(overrides: Partial<EnterpriseDeps> = {}) {
  const opened: string[] = [];
  const secrets = {
    values: new Map<string, string>(),
    put: vi.fn(async (profile: string, secret: string) => {
      secrets.values.set(profile, secret);
    }),
    get: vi.fn(async (profile: string) => secrets.values.get(profile) ?? null),
    delete: vi.fn(async (profile: string) => {
      secrets.values.delete(profile);
    }),
  };
  const deps: EnterpriseDeps = {
    isDesktop: () => true,
    open: (url: string) => opened.push(url),
    secrets,
    pollDelayMs: 10,
    maxPolls: 5,
    refreshSkewMs: 120_000,
    ...overrides,
  };
  return { deps, secrets, opened };
}

function trackStates(session: ReturnType<typeof createEnterpriseSession>) {
  const states: EnterpriseState[] = [session.state()];
  session.subscribe((s) => states.push(s));
  return states;
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("enterprise sign-in state machine", () => {
  it("degrades to desktop-only in the browser build", async () => {
    const { deps } = makeDeps({ isDesktop: () => false });
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const session = createEnterpriseSession(deps);
    const states = trackStates(session);

    await session.beginLogin();

    expect(states[states.length - 1]).toEqual({ kind: "failed", error: "desktop-only" });
    expect(fetchMock).not.toHaveBeenCalled();
    session.dispose();
  });

  it("walks idle → logging_in → signed_in and stores the refresh in the bridge", async () => {
    const { deps, secrets, opened } = makeDeps();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/v1/enterprise/login/start")) {
          return jsonResponse(200, {
            ok: true,
            authorize_url: "http://opc.example.com/wecom/qr?txn=1",
            login_txn_id: "txn-1",
          });
        }
        if (url.includes("/v1/enterprise/login/result")) {
          return jsonResponse(200, {
            ok: true,
            state: "succeeded",
            refresh_token: "opc-refresh-1",
            context: { subject: "yangjing", tenant: "技术交付中心" },
          });
        }
        return jsonResponse(404, {});
      }),
    );
    const session = createEnterpriseSession(deps);
    const states = trackStates(session);

    await session.beginLogin();

    expect(states.map((s) => s.kind)).toEqual(["idle", "logging_in", "signed_in"]);
    expect(states[states.length - 1]).toEqual({
      kind: "signed_in",
      subject: "yangjing",
      tenant: "技术交付中心",
      email: "",
      name: "",
    });
    expect(opened).toEqual(["http://opc.example.com/wecom/qr?txn=1"]);
    // The refresh token lands ONLY in the Keychain bridge.
    expect(secrets.put).toHaveBeenCalledWith(ENTERPRISE_KEYCHAIN_PROFILE, "opc-refresh-1");
    expect(secrets.values.get(ENTERPRISE_KEYCHAIN_PROFILE)).toBe("opc-refresh-1");
    session.dispose();
  });

  it("surfaces the platform error code when start fails", async () => {
    const { deps } = makeDeps();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/v1/enterprise/login/start")) {
          return jsonResponse(502, {
            error: { code: "UPSTREAM_UNAVAILABLE", message: "platform unreachable" },
          });
        }
        return jsonResponse(404, {});
      }),
    );
    const session = createEnterpriseSession(deps);
    const states = trackStates(session);

    await session.beginLogin();

    expect(states[states.length - 1]).toEqual({ kind: "failed", error: "UPSTREAM_UNAVAILABLE" });
    session.dispose();
  });

  it("failed browser flows land on failed with the BND-180 code", async () => {
    const { deps } = makeDeps();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/v1/enterprise/login/start")) {
          return jsonResponse(200, {
            ok: true,
            authorize_url: "http://opc.example.com/wecom/qr?txn=2",
            login_txn_id: "txn-2",
          });
        }
        if (url.includes("/v1/enterprise/login/result")) {
          return jsonResponse(404, {
            ok: false,
            state: "failed",
            error: { code: "SUBJECT_NOT_PROVISIONED" },
          });
        }
        return jsonResponse(404, {});
      }),
    );
    const session = createEnterpriseSession(deps);
    const states = trackStates(session);

    await session.beginLogin();

    expect(states[states.length - 1]).toEqual({ kind: "failed", error: "SUBJECT_NOT_PROVISIONED" });
    session.dispose();
  });

  it("rotates the refresh through the bridge on refresh", async () => {
    const { deps, secrets } = makeDeps();
    secrets.values.set(ENTERPRISE_KEYCHAIN_PROFILE, "opc-refresh-old");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/v1/enterprise/session/refresh")) {
          return jsonResponse(200, { ok: true, refresh_token: "opc-refresh-new" });
        }
        return jsonResponse(404, {});
      }),
    );
    const session = createEnterpriseSession(deps);

    await session.refreshSession();

    expect(secrets.put).toHaveBeenCalledWith(ENTERPRISE_KEYCHAIN_PROFILE, "opc-refresh-new");
    expect(secrets.values.get(ENTERPRISE_KEYCHAIN_PROFILE)).toBe("opc-refresh-new");
    expect(session.state()).toEqual({ kind: "idle" }); // was never signed in here
    session.dispose();
  });

  it("REFRESH_REUSE_DETECTED deletes the entry and drops to idle", async () => {
    const { deps, secrets } = makeDeps();
    secrets.values.set(ENTERPRISE_KEYCHAIN_PROFILE, "opc-refresh-stale");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/v1/enterprise/session/refresh")) {
          return jsonResponse(401, {
            error: { code: "REFRESH_REUSE_DETECTED", message: "family revoked" },
          });
        }
        return jsonResponse(404, {});
      }),
    );
    const session = createEnterpriseSession(deps);
    const states = trackStates(session);

    await session.refreshSession();

    expect(secrets.delete).toHaveBeenCalledWith(ENTERPRISE_KEYCHAIN_PROFILE);
    expect(secrets.values.has(ENTERPRISE_KEYCHAIN_PROFILE)).toBe(false);
    expect(states[states.length - 1]).toEqual({ kind: "idle" });
    session.dispose();
  });

  it("logout tells the sidecar, deletes the entry, and returns to idle", async () => {
    const { deps, secrets } = makeDeps();
    secrets.values.set(ENTERPRISE_KEYCHAIN_PROFILE, "opc-refresh-1");
    const fetchMock = vi.fn(async (url: string) => {
      if (url.endsWith("/v1/enterprise/logout")) return jsonResponse(200, { ok: true });
      return jsonResponse(404, {});
    });
    vi.stubGlobal("fetch", fetchMock);
    const session = createEnterpriseSession(deps);
    const states = trackStates(session);

    await session.logout();

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/v1/enterprise/logout"),
      expect.objectContaining({ method: "POST" }),
    );
    expect(secrets.delete).toHaveBeenCalledWith(ENTERPRISE_KEYCHAIN_PROFILE);
    expect(states[states.length - 1]).toEqual({ kind: "idle" });
    session.dispose();
  });

  it("hydrates from the sidecar session on boot", async () => {
    const { deps } = makeDeps();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/v1/enterprise/session")) {
          return jsonResponse(200, { subject: "yangjing", tenant: "t1" });
        }
        return jsonResponse(404, {});
      }),
    );
    const session = createEnterpriseSession(deps);
    const states = trackStates(session);

    await session.syncFromServer();

    expect(states[states.length - 1]).toEqual({ kind: "signed_in", subject: "yangjing", tenant: "t1", email: "", name: "" });
    session.dispose();
  });

  // BND-102/160: the production refresh path — while signed in, refresh fires
  // BEFORE the 15-minute access token dies, driven by the sidecar's own
  // expiry hints; a rotation reschedules against the fresh session view.

  it("refreshes before the access token expires and reschedules", async () => {
    vi.useFakeTimers();
    const { deps, secrets } = makeDeps(); // refreshSkewMs 120s
    secrets.values.set(ENTERPRISE_KEYCHAIN_PROFILE, "opc-refresh-1");
    const fetchMock = vi.fn(async (url: string) => {
      if (url.endsWith("/v1/enterprise/session/refresh")) {
        return jsonResponse(200, { ok: true, refresh_token: "opc-refresh-2" });
      }
      if (url.endsWith("/v1/enterprise/session")) {
        return jsonResponse(200, {
          subject: "yangjing",
          tenant: "t1",
          seconds_until_expiry: 900,
          refresh_recommended: false,
        });
      }
      return jsonResponse(404, {});
    });
    vi.stubGlobal("fetch", fetchMock);
    const session = createEnterpriseSession(deps);

    await session.syncFromServer();
    expect(session.state().kind).toBe("signed_in");
    const refreshes = () =>
      fetchMock.mock.calls.filter((c) => String(c[0]).includes("session/refresh"));
    expect(refreshes()).toHaveLength(0);

    await vi.advanceTimersByTimeAsync(779_000); // not yet inside the skew window
    expect(refreshes()).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1_000); // 780s mark: refresh fires

    expect(refreshes()).toHaveLength(1);
    expect(secrets.values.get(ENTERPRISE_KEYCHAIN_PROFILE)).toBe("opc-refresh-2");
    // The rotation re-read the session view and stayed signed in.
    expect(session.state()).toEqual({ kind: "signed_in", subject: "yangjing", tenant: "t1", email: "", name: "" });
    session.dispose();
  });

  it("a scheduled refresh hitting REFRESH_REUSE_DETECTED drops to idle and deletes the entry", async () => {
    vi.useFakeTimers();
    const { deps, secrets } = makeDeps();
    secrets.values.set(ENTERPRISE_KEYCHAIN_PROFILE, "opc-refresh-old");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/v1/enterprise/session/refresh")) {
          return jsonResponse(401, {
            error: { code: "REFRESH_REUSE_DETECTED", message: "family revoked" },
          });
        }
        if (url.endsWith("/v1/enterprise/session")) {
          return jsonResponse(200, {
            subject: "yangjing",
            tenant: "t1",
            seconds_until_expiry: 60, // already inside the skew window
            refresh_recommended: true,
          });
        }
        return jsonResponse(404, {});
      }),
    );
    const session = createEnterpriseSession(deps);
    const states = trackStates(session);

    await session.syncFromServer();
    expect(session.state().kind).toBe("signed_in");

    await vi.advanceTimersByTimeAsync(1); // delay clamped to 0 → fires at once

    expect(secrets.delete).toHaveBeenCalledWith(ENTERPRISE_KEYCHAIN_PROFILE);
    expect(secrets.values.has(ENTERPRISE_KEYCHAIN_PROFILE)).toBe(false);
    expect(states[states.length - 1]).toEqual({ kind: "idle" });
    session.dispose();
  });

  it("honors refresh_recommended when the view carries no precise expiry", async () => {
    const { deps, secrets } = makeDeps();
    secrets.values.set(ENTERPRISE_KEYCHAIN_PROFILE, "opc-refresh-1");
    let sessionReads = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/v1/enterprise/session/refresh")) {
          return jsonResponse(200, { ok: true, refresh_token: "opc-refresh-2" });
        }
        if (url.endsWith("/v1/enterprise/session")) {
          sessionReads += 1;
          return jsonResponse(200, {
            subject: "yangjing",
            tenant: "t1",
            refresh_recommended: sessionReads === 1, // only the first read asks
          });
        }
        return jsonResponse(404, {});
      }),
    );
    const session = createEnterpriseSession(deps);

    await session.syncFromServer();
    await vi.waitFor(() => {
      expect(secrets.values.get(ENTERPRISE_KEYCHAIN_PROFILE)).toBe("opc-refresh-2");
    });
    expect(session.state().kind).toBe("signed_in");
    session.dispose();
  });
});
