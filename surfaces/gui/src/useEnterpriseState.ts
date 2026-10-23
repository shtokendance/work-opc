// Enterprise sign-in state for resource views (WORK-RESOURCES-001). The full
// login flow lives in Sidebar's enterprise session; the resource pages only
// need the signed-in/out VIEW — polled straight from the sidecar so they work
// standalone (and in tests, where the prop can inject the state directly).

import { useEffect, useState } from "react";
import { httpBase } from "./api";
import type { EnterpriseState } from "./enterprise";

const launchToken = (): string =>
  (globalThis as any).__COWORKER_API_TOKEN__ ||
  (import.meta as any).env?.VITE_COWORKER_API_TOKEN ||
  "";

export function useEnterpriseState(pollMs = 30_000, enabled = true): EnterpriseState {
  const [state, setState] = useState<EnterpriseState>({ kind: "idle" });
  useEffect(() => {
    if (!enabled) return;
    let dead = false;
    const sync = async () => {
      try {
        const res = await fetch(`${httpBase()}/v1/enterprise/session`, {
          headers: { "x-openworker-token": launchToken() },
        });
        if (dead) return;
        if (res.status === 200) {
          const body = await res.json();
          setState({
            kind: "signed_in",
            subject: body.subject || "",
            tenant: body.tenant || "",
            email: body.email || "",
            name: body.name || "",
          });
        } else {
          setState((prev) => (prev.kind === "signed_in" ? { kind: "idle" } : prev));
        }
      } catch {
        /* sidecar not up — keep the current state */
      }
    };
    void sync();
    const timer = setInterval(() => void sync(), pollMs);
    return () => {
      dead = true;
      clearInterval(timer);
    };
  }, [pollMs, enabled]);
  return state;
}
