// The static demo's answers to what a screen asks: a write whose answer a screen shows gets a sentence or a count
// that says it is the demo, one that would need a thing made gets a refusal the screen's own error line shows, and
// a report asked with other choices gets the recorded report.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { demoFetch } from "./shim";

const data = {
  captured_at: "2026-10-28",
  responses: {
    "GET /api/v1/reports/custom?rows=category&period=last_365": { rows: ["recorded"] },
    "GET /api/v1/reports/custom/year?year=2026": { year: 2026 },
    "GET /api/v1/housing": { homes: [] },
    "GET /api/v1/taxes": { year: 2026 },
    "GET /api/v1/mail/messages?limit=200": { messages: [{ id: 1, account: "Proton", folder: "INBOX", sender: "billing@example-water.test", sender_name: "Example Water", subject: "Your bill is ready", snippet: "Amount due $64.00 by 11/02/2026.", has_attachments: true, attachment_names: "statement.pdf", kind: "bill_due" }] },
    "GET /api/v1/facts?status=pending": { facts: [{ id: 9, kind: "bill_due", message: { id: 1 } }, { id: 10, kind: "document_terms", message: null }] },
  },
  checks: {},
  chat: { intro: "", brief: [] },
};

beforeEach(() => {
  vi.stubGlobal("document", { baseURI: "http://demo.test/" });
  vi.stubGlobal("window", { dispatchEvent: () => true });
  vi.stubGlobal("CustomEvent", class { constructor(public type: string, public init?: unknown) {} });
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(data), { status: 200 })));
});
afterEach(() => vi.unstubAllGlobals());

describe("demoFetch", () => {
  it("answers a sentence a screen shows back", async () => {
    const r = await demoFetch("/api/v1/suggestions/answer", { method: "POST", body: "{}" });
    expect(r.status).toBe(200);
    expect(await r.json()).toEqual({ said: "In the demo, changes are not kept." });
  });

  it("answers counts and summaries with zeros, never an empty object", async () => {
    expect(await (await demoFetch("/api/v1/transactions/bulk", { method: "POST", body: "{}" })).json()).toEqual({ changed: 0 });
    expect(await (await demoFetch("/api/v1/needs-you/facts", { method: "POST", body: "{}" })).json()).toEqual({ done: [], failed: [], said: "In the demo, changes are not kept." });
    const run = await (await demoFetch("/api/v1/jobs/sync/run", { method: "POST" })).json();
    expect(run.ok).toBe(true);
    expect(typeof run.summary).toBe("string");
  });

  it("refuses, in words, a write whose answer would be a thing made", async () => {
    for (const path of ["/api/v1/restore-points", "/api/v1/scenarios/run", "/api/v1/documents", "/api/v1/move/plan/letter", "/api/v1/calendar/feed"]) {
      const r = await demoFetch(path, { method: "POST", body: "{}" });
      expect(r.status, path).toBe(409);
      expect((await r.json()).detail).toMatch(/^Not part of the demo/);
    }
  });

  it("still acknowledges a plain write, and gives a recorded page back where the screen shows it", async () => {
    expect(await (await demoFetch("/api/v1/bills/3", { method: "DELETE" })).json()).toEqual({ ok: true });
    expect(await (await demoFetch("/api/v1/housing/3", { method: "PUT", body: "{}" })).json()).toEqual({ homes: [] });
  });

  it("gives the recorded report for any choice of rows, period or year", async () => {
    expect(await (await demoFetch("/api/v1/reports/custom?rows=merchant&period=last_90&include_income=true")).json()).toEqual({ rows: ["recorded"] });
    expect(await (await demoFetch("/api/v1/reports/custom/year?year=2024")).json()).toEqual({ year: 2026 });
  });

  it("opens a message from its recorded row, with the facts taken from it, and says what the demo leaves out", async () => {
    const r = await demoFetch("/api/v1/mail/messages/1");
    expect(r.status).toBe(200);
    const opened = await r.json();
    expect(opened.subject).toBe("Your bill is ready");
    expect(opened.boxed).toContain("=== Untrusted message demo from Example Water (Proton).");
    expect(opened.boxed).toContain("Amount due $64.00 by 11/02/2026.");
    expect(opened.boxed).toContain("statement.pdf");
    expect(opened.attachments).toEqual([]);
    expect(opened.facts.map((f: { id: number }) => f.id)).toEqual([9]);
    expect((await demoFetch("/api/v1/mail/messages/2")).status).toBe(404);
    expect((await demoFetch("/api/v1/mail/messages/1/attachments/statement.pdf")).status).toBe(404);
  });

  it("answers an unrecorded tax year with the recorded one, and a join link with a refusal in words", async () => {
    expect(await (await demoFetch("/api/v1/taxes/2031")).json()).toEqual({ year: 2026 });
    const r = await demoFetch("/api/auth/join/check", { method: "POST", body: "{}" });
    expect(r.status).toBe(409);
    expect((await r.json()).detail).toMatch(/^Not part of the demo/);
  });

  it("says when a page is not part of the demo", async () => {
    const r = await demoFetch("/api/v1/borrowing/exit");
    expect(r.status).toBe(404);
    expect((await r.json()).detail).toBe("Not part of the demo.");
  });
});
