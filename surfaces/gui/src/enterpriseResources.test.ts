// enterpriseResources URL safety + message fields (WORK-RESOURCES-001, BND-003).
// The exact-origin guard is the security chokepoint for every external open.

import { describe, expect, it } from "vitest";
import {
  assertSafeExternalUrl,
  buildResourceWebUrl,
  uploadHomeUrl,
  TRUSTED_WEB_ORIGIN,
  remoteSessionResourceBlock,
  sendResourceMessage,
} from "./enterpriseResources";

describe("assertSafeExternalUrl", () => {
  it("accepts the exact trusted https origin", () => {
    expect(assertSafeExternalUrl("https://shtokendance.benlai.com")).toBe(
      "https://shtokendance.benlai.com/",
    );
    expect(
      assertSafeExternalUrl("https://shtokendance.benlai.com/platform/knowledge-bases/kb-1"),
    ).toContain("/platform/knowledge-bases/kb-1");
  });

  it("rejects other schemes, hosts, userinfo, and non-default ports", () => {
    expect(assertSafeExternalUrl("http://shtokendance.benlai.com")).toBeNull();
    expect(assertSafeExternalUrl("https://evil.example.com")).toBeNull();
    expect(assertSafeExternalUrl("https://sub.shtokendance.benlai.com")).toBeNull();
    expect(assertSafeExternalUrl("https://shtokendance.benlai.com.evil.com")).toBeNull();
    expect(assertSafeExternalUrl("https://user:pass@shtokendance.benlai.com")).toBeNull();
    expect(assertSafeExternalUrl("https://shtokendance.benlai.com:8443/x")).toBeNull();
    expect(assertSafeExternalUrl("javascript:alert(1)")).toBeNull();
    expect(assertSafeExternalUrl("file:///etc/passwd")).toBeNull();
    expect(assertSafeExternalUrl("data:text/html,x")).toBeNull();
    expect(assertSafeExternalUrl("not a url")).toBeNull();
  });
});

describe("buildResourceWebUrl", () => {
  it("encodes the KB id as a single path segment", () => {
    const url = buildResourceWebUrl("knowledge_base", "kb 甲/乙");
    expect(url).toBeNull(); // IDs must be single segments; refuse rather than mangle
    const ok = buildResourceWebUrl("knowledge_base", "kb-a", "T1")!;
    expect(ok).toBe("https://shtokendance.benlai.com/platform/knowledge-bases/kb-a?tenant_id=T1");
  });

  it("builds the catalog detail URL with an encoded query id", () => {
    const url = buildResourceWebUrl("skill_catalog", "cat c1", "T2")!;
    expect(url).toContain("/platform/toolbox/skills?");
    expect(url).toContain("catalog_id=cat+c1"); // URLSearchParams 空格编码
    expect(url).toContain("tenant_id=T2");
  });

  it("never emits a non-trusted URL", () => {
    for (const id of ["a", "../etc", "a?b", "a#b", "a\\b", ""]) {
      const url = buildResourceWebUrl("knowledge_base", id);
      if (url !== null) expect(url.startsWith(TRUSTED_WEB_ORIGIN)).toBe(true);
    }
  });
});

describe("uploadHomeUrl", () => {
  it("is always exactly the confirmed homepage — no deep link, no query", () => {
    expect(uploadHomeUrl()).toBe("https://shtokendance.benlai.com");
    expect(uploadHomeUrl()).not.toContain("?");
  });
});

describe("sendResourceMessage", () => {
  it("rides knowledge_scope and skill_ref on the user_message frame", () => {
    const sent: object[] = [];
    const session = { send: (p: object) => sent.push(p) };
    sendResourceMessage(session, {
      text: "整理卖点",
      knowledgeScope: {
        tenant_id: "T1",
        knowledge_base_id: "kb-a",
        context_id: "cid",
        label: "A（T1）",
      },
      skillRef: {
        source: "catalog",
        tenant_id: "T1",
        catalog_id: "cat-1",
        bundle_sha256: "a".repeat(64),
        context_id: "cid",
        label: "s",
      },
    });
    expect(sent).toHaveLength(1);
    const msg = sent[0] as any;
    expect(msg.type).toBe("user_message");
    expect(msg.knowledge_scope).toEqual({
      tenant_id: "T1",
      knowledge_base_id: "kb-a",
      context_id: "cid",
    });
    expect(msg.skill_ref.bundle_sha256).toBe("a".repeat(64));
    expect(msg.text).toBe("整理卖点");
  });

  it("omits absent selections entirely", () => {
    const sent: object[] = [];
    sendResourceMessage({ send: (p: object) => sent.push(p) }, { text: "hi" });
    const msg = sent[0] as any;
    expect("knowledge_scope" in msg).toBe(false);
    expect("skill_ref" in msg).toBe(false);
  });
});

describe("remoteSessionResourceBlock (M7, BND-004.2)", () => {
  it("blocks resource selections on remote-machine sessions", () => {
    const machines = new Map([["sess-remote", "m-1"]]);
    const block = remoteSessionResourceBlock(
      "sess-remote",
      (sid) => machines.get(sid) ?? null,
      { knowledgeScope: { tenant_id: "T1" } },
    );
    expect(block).toContain("Remote sessions cannot use");
    const blockRef = remoteSessionResourceBlock(
      "sess-remote",
      (sid) => machines.get(sid) ?? null,
      { skillRef: { catalog_id: "c" } },
    );
    expect(blockRef).toContain("local session");
  });

  it("allows local sessions and selection-free sends", () => {
    expect(
      remoteSessionResourceBlock("sess-local", () => null, {
        knowledgeScope: { tenant_id: "T1" },
      }),
    ).toBeNull();
    expect(
      remoteSessionResourceBlock("sess-remote", () => "m-1", null),
    ).toBeNull();
    expect(remoteSessionResourceBlock(null, () => "m-1", { skillRef: {} })).toBeNull();
  });
});
