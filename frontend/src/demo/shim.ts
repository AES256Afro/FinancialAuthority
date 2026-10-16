// The static demo: the real screens over the engine's recorded answers for the synthetic household.
// Nothing here computes a figure; every number came out of the engine when `demo-data.json` was captured.
// Writes are acknowledged and forgotten, and the page says so.

export const DEMO = import.meta.env.VITE_STATIC_DEMO === "1";

type Json = unknown;
interface DemoData {
  captured_at: string;
  responses: Record<string, Json>;
  checks: Record<string, Json>;
  chat: { intro: string; brief: string[] };
}

let loaded: Promise<DemoData> | null = null;
const memory: { approvals: Record<string, unknown>[]; nextId: number } = { approvals: [], nextId: 9000 };

function load(): Promise<DemoData> {
  if (!loaded) {
    loaded = fetch(new URL("demo-data.json", document.baseURI).toString()).then((r) => r.json() as Promise<DemoData>);
  }
  return loaded;
}

export function flash(message: string) {
  window.dispatchEvent(new CustomEvent("fa-demo-flash", { detail: message }));
}

function json(body: Json, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function split(path: string): [string, URLSearchParams] {
  const i = path.indexOf("?");
  return i < 0 ? [path, new URLSearchParams()] : [path.slice(0, i), new URLSearchParams(path.slice(i + 1))];
}

function includes(value: unknown, needle: string): boolean {
  return typeof value === "string" && value.toLowerCase().includes(needle);
}

function nearestCheck(data: DemoData, amount: number, installments: string | null): Json {
  const step = amount <= 1000 ? 10 : 50;
  const rounded = Math.min(3000, Math.max(0, Math.round(amount / step) * step));
  const key = `${rounded}:${installments === "4" ? 4 : 0}`;
  return data.checks[key] ?? data.checks["0:0"];
}

async function get(path: string): Promise<Response> {
  const data = await load();
  const exact = data.responses[`GET ${path}`];
  if (exact !== undefined) return json(exact);
  const [base, params] = split(path);
  if (base === "/api/v1/transactions/search") {
    const all = data.responses["GET /api/v1/transactions/search?limit=500"] as { transactions: Record<string, unknown>[] };
    const text = (params.get("text") ?? "").toLowerCase();
    const category = params.get("category");
    const limit = Number(params.get("limit") ?? 50);
    const rows = all.transactions.filter((t) => (!text || includes(t.merchant, text) || includes(t.description, text) || includes(t.note, text)) && (!category || t.category === category)).slice(0, limit);
    return json({ returned: rows.length, total_amount: Math.round(rows.reduce((s, t) => s + Number(t.amount), 0) * 100) / 100, transactions: rows });
  }
  if (base === "/api/v1/mail/messages") {
    const all = data.responses["GET /api/v1/mail/messages?limit=200"] as { messages: Record<string, unknown>[] };
    const text = (params.get("q") ?? params.get("text") ?? "").toLowerCase();
    const sender = (params.get("sender") ?? "").toLowerCase();
    const kind = params.get("kind");
    const rows = all.messages.filter((m) => (!text || includes(m.subject, text) || includes(m.sender, text) || includes(m.sender_name, text) || includes(m.snippet, text)) && (!sender || includes(m.sender, sender)) && (!kind || m.kind === kind));
    return json({ returned: rows.length, messages: rows });
  }
  if (base === "/api/v1/check") return json(nearestCheck(data, Number(params.get("amount") ?? 0), params.get("installments")));
  if (base === "/api/v1/reports/spending") {
    const months = params.get("months") ?? "6";
    const by = params.get("by") ?? "category";
    return json(data.responses[`GET /api/v1/reports/spending?months=${months}&by=${by}`] ?? data.responses["GET /api/v1/reports/spending?months=6&by=category"]);
  }
  if (base === "/api/v1/projections/where") {
    const amounts = [25, 50, 75, 100, 150, 200, 300, 500];
    const years = [5, 10, 20, 25, 30];
    // A share of the paycheck: the demo household takes home 2,240 a check; the nearest captured amount answers.
    const wantAmount = params.has("percent") ? (Number(params.get("percent") ?? 0) / 100) * 2240 : Number(params.get("amount") ?? 50);
    if (params.has("percent")) flash("In the demo, the comparison uses the nearest captured amount.");
    const wantYears = Number(params.get("years") ?? 10);
    const amount = amounts.reduce((a, b) => (Math.abs(b - wantAmount) < Math.abs(a - wantAmount) ? b : a));
    const year = years.reduce((a, b) => (Math.abs(b - wantYears) < Math.abs(a - wantYears) ? b : a));
    const scenario = ["low", "mid", "high"].includes(params.get("scenario") ?? "") ? params.get("scenario") : "mid";
    return json(data.responses[`GET /api/v1/projections/where?amount=${amount}&years=${year}&scenario=${scenario}`] ?? data.responses["GET /api/v1/projections/where?amount=50&years=10&scenario=mid"]);
  }
  if (base === "/api/v1/bills/suggestions") return json(data.responses["GET /api/v1/bills/suggestions"]);
  if (base === "/api/v1/reviews") return json(data.responses["GET /api/v1/reviews?limit=3"]);
  if (base === "/api/v1/approvals") {
    const captured = data.responses["GET /api/v1/approvals?status=all&limit=30"] as { approvals: Record<string, unknown>[]; pending: number; allowances: string[] };
    const approvals = [...memory.approvals, ...captured.approvals];
    return json({ ...captured, approvals, pending: approvals.filter((a) => a.status === "pending").length });
  }
  if (base === "/api/v1/models/catalog") return json({ models: [], error: "The demo has no model server." });
  if (base.startsWith("/api/v1/mail/messages/")) return json({ detail: "This message is not in the demo." }, 404);
  // A page asked with other choices than the recorded ones gets the recorded answer for that page.
  const plain = data.responses[`GET ${base}`];
  if (plain !== undefined) return json(plain);
  return json({ detail: "Not part of the demo." }, 404);
}

function sse(events: Json[]): Response {
  const encoder = new TextEncoder();
  let i = 0;
  const stream = new ReadableStream({
    pull(controller) {
      if (i >= events.length) {
        controller.close();
        return;
      }
      controller.enqueue(encoder.encode(`data: ${JSON.stringify(events[i++])}\n\n`));
      return new Promise((resolve) => setTimeout(resolve, 120));
    },
  });
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

async function write(path: string, method: string, body: unknown): Promise<Response> {
  const data = await load();
  const [base] = split(path);
  if (base === "/api/auth/login" || base === "/api/auth/logout") return json({ ok: true });
  if (base === "/api/v1/models/catalog") return json({ models: [], error: "The demo has no model server." });
  if (base === "/api/v1/setup/test") return json({ ...(data.responses["GET /api/v1/setup/check"] as object), tests: [] });
  if (base === "/api/v1/chat") {
    const lines = data.chat.brief.map((b) => `• ${b}`).join("\n");
    const chunks = (data.chat.intro + "\n" + lines).match(/[^\n]*\n?/g)?.filter(Boolean) ?? [];
    return sse([
      ...chunks.map((text) => ({ type: "text", text })),
      { type: "done", unverified: [], used: [{ name: "brief_today", arguments: {}, rows: data.chat.brief.length }], usage: { input: 0, output: 0 }, model: "engine", provider: "engine" },
    ]);
  }
  if (base === "/api/v1/approvals" && method === "POST") {
    const b = (body ?? {}) as Record<string, unknown>;
    const row = { id: memory.nextId++, kind: "send", status: "pending", to: b.to, subject: b.subject, body: b.body, reason: b.reason ?? "", source: b.source ?? "", created_by: "owner", created_kind: "owner", created_at: new Date().toISOString(), decided_by: "", decided_at: null, sent_at: null, error: "", account_id: null };
    memory.approvals.unshift(row);
    flash("Queued for approval. In the demo nothing is ever sent.");
    return json(row);
  }
  const decision = base.match(/^\/api\/v1\/approvals\/(\d+)\/(approve|deny)$/);
  if (decision) {
    const row = memory.approvals.find((a) => a.id === Number(decision[1]));
    if (row) {
      row.status = decision[2] === "approve" ? "sent" : "denied";
      row.decided_by = "owner";
      row.sent_at = decision[2] === "approve" ? new Date().toISOString() : null;
      flash(decision[2] === "approve" ? "Marked as sent. In the demo nothing actually leaves." : "Denied.");
      return json(row);
    }
    return json({ detail: "Decisions on recorded approvals are not kept in the demo." }, 409);
  }
  if (base.endsWith("/test") && base.startsWith("/api/v1/models/")) return json({ ok: false, error: "The demo has no model server. The live app talks to Ollama, an OpenAI-compatible server, or Claude by API." });
  if (base === "/api/v1/push/test" || base === "/api/v1/push/brief") return json({ ok: false, skipped: true, reason: "Push is off in the demo." });
  if (base === "/api/v1/keys" && method === "POST") {
    flash("In the demo, keys are not kept.");
    return json({ token: "fa_demo_key_not_real_0000000000000000" });
  }
  if (base === "/api/v1/reviews" && method === "POST") {
    flash("In the demo the reviewer does not run; this is its recorded review.");
    const captured = data.responses["GET /api/v1/reviews?limit=3"] as { reviews: Record<string, unknown>[] };
    return json(captured.reviews[0] ?? { ok: false, error: "No recorded review." });
  }
  if (base.startsWith("/api/v1/models") && method === "PUT") {
    flash("In the demo, settings are not kept.");
    return json(data.responses["GET /api/v1/models"]);
  }
  if (base === "/api/v1/rules" && method === "PUT") {
    flash("In the demo, rules are not kept.");
    return json(data.responses["GET /api/v1/rules"]);
  }
  if (base === "/api/v1/projections/assumptions") {
    flash("In the demo, assumptions are not kept.");
    const captured = data.responses["GET /api/v1/projections"] as Record<string, unknown>;
    return json({ assumptions: captured.assumptions, outlook: captured });
  }
  if (base.startsWith("/api/v1/goals")) {
    flash("In the demo, goals are not kept.");
    return json(data.responses["GET /api/v1/goals"]);
  }
  if (base === "/api/v1/mail/accounts/test") return json({ ok: false, error: "The demo reaches no mailbox." });
  if (base.startsWith("/api/v1/imports/")) return json({ detail: "The demo imports nothing. The live app reads the file, shows its first rows and imports them under an account." }, 400);
  if (base.startsWith("/api/v1/banks/plaid/link_token")) return json({ detail: "The demo has no Plaid keys. The live app opens the bank's own login here." }, 409);
  if (/^\/api\/v1\/(banks\/(plaid|teller)\/settings|mail\/accounts|push|models\/secrets)/.test(base)) {
    flash("This is the demo: keys are not saved here.");
    return json({ detail: "This is the demo, so nothing you paste here is saved anywhere. Paste keys only in your own FinancialAuthority, on its Settings › Connectors page." }, 409);
  }
  flash("In the demo, changes are not kept.");
  return json({ ok: true });
}

export async function demoFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase();
  if (method === "GET") return get(path);
  let body: unknown = null;
  if (typeof init.body === "string") {
    try {
      body = JSON.parse(init.body);
    } catch {
      body = null;
    }
  }
  return write(path, method, body);
}
