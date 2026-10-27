// Enterprise sign-in state for resource views (WORK-RESOURCES-001). The full
// login flow lives in Sidebar's enterprise session; the resource pages only
// need the signed-in/out VIEW — polled straight from the sidecar so they work
// standalone (and in tests, where the prop can inject the state directly).

import { useEffect, useState } from "react";
import { httpBase } from "./api";
import { normalizeSpaces, type EnterpriseState } from "./enterprise";

export const ENTERPRISE_STATE_CHANGED = "opc:enterprise-changed";

const launchToken = (): string =>
  (globalThis as any).__COWORKER_API_TOKEN__ ||
  (import.meta as any).env?.VITE_COWORKER_API_TOKEN ||
  "";

export function useEnterpriseState(pollMs = 15_000, enabled = true): EnterpriseState {
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
            spaces: normalizeSpaces(body),
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
    const onFocusOrChange = () => void sync();
    window.addEventListener("focus", onFocusOrChange);
    window.addEventListener(ENTERPRISE_STATE_CHANGED, onFocusOrChange);
    return () => {
      dead = true;
      clearInterval(timer);
      window.removeEventListener("focus", onFocusOrChange);
      window.removeEventListener(ENTERPRISE_STATE_CHANGED, onFocusOrChange);
    };
  }, [pollMs, enabled]);
  return state;
}
