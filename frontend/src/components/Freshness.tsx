// How fresh the bank data is and what reading it costs: the chain from the bank to this screen, how often the app
// reads, every call it made in the last thirty days, the free plan's logins used, and a forecast at any price.
import { TriangleAlert } from "lucide-react";
import { useRef, useState } from "react";
import { api, useApi } from "../api";
import { monthDay, money, parseMoney, shortDate, when } from "../format";
import { Failed, Loading, Said, useWidth } from "./State";
import { StatusPill } from "./Status";

interface Freshness {
  sync_every_hours: number;
  choices: number[];
  links: { id: number; provider: string; institution: string; last_sync_at: string | null; next_sync_at: string | null; newest_transaction: string | null; error: string | null; needs_login: boolean }[];
  plaid: { environment: string; items_made: number; items_active: number; trial_limit: number };
  calls: { by_day: { date: string; plaid: number; teller: number }[]; month: string; by_endpoint: Record<string, number> };
  cost: { price_per_item: number | null; monthly: number | null; yearly: number | null; plan_note: string; teller_note: string };
  chain: string;
}

function ago(iso: string | null): string {
  if (!iso) return "not yet";
  // The server writes UTC; a time without a zone is read as UTC.
  const zoned = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`;
  const mins = Math.round((Date.now() - new Date(zoned).getTime()) / 60000);
  if (Number.isNaN(mins)) return "";
  if (mins < 0) return `in ${Math.abs(mins) < 60 ? `${Math.abs(mins)} min` : `${Math.round(Math.abs(mins) / 60)} h`}`;
  return mins < 60 ? `${mins} min ago` : mins < 48 * 60 ? `${Math.round(mins / 60)} h ago` : `${Math.round(mins / 1440)} days ago`;
}

// A call to Plaid or Teller in words: "plaid:/transactions/sync" is "Plaid, new transactions".
function callWord(key: string): string {
  const [provider, ...rest] = key.split(":");
  const path = rest.join(":");
  const who = provider === "plaid" ? "Plaid" : provider === "teller" ? "Teller" : provider;
  const what = path.includes("transactions") ? "new transactions"
    : path.includes("balances") ? "balances"
    : path.includes("holdings") ? "investment holdings"
    : path.includes("link/token") ? "opening a bank sign-in"
    : path.includes("public_token") ? "finishing a bank link"
    : path.includes("item/remove") ? "removing a link"
    : path.includes("webhook") ? "update alerts"
    : path.includes("accounts") ? "the account list"
    : path.replace(/^\//, "");
  return `${who}, ${what}`;
}

function Chain({ hours }: { hours: number }) {
  const nodes = [{ t: "Your bank", s: "posts it, often as pending" }, { t: "Plaid or Teller", s: "collects a few times a day" }, { t: "This app", s: `reads every ${hours} h, or Sync now` }];
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
      {nodes.map((n, i) => (
        <div key={n.t} style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <div style={{ border: "1px solid var(--hairline)", borderRadius: 10, padding: "6px 10px", background: "var(--surface)" }}>
            <div style={{ fontSize: 13, fontWeight: 800 }}>{n.t}</div>
            <div style={{ fontSize: 11, color: "var(--ink-2)" }}>{n.s}</div>
          </div>
          {i < nodes.length - 1 && <span aria-hidden style={{ color: "var(--muted)", fontWeight: 800 }}>→</span>}
        </div>
      ))}
    </div>
  );
}

// One drawing unit is one pixel, so the dates under the bars stay readable on a phone.
function Calls({ days }: { days: Freshness["calls"]["by_day"] }) {
  const box = useRef<HTMLDivElement>(null);
  const W = Math.max(240, Math.min(640, useWidth(box, 600)));
  const H = 70;
  const max = Math.max(1, ...days.map((d) => d.plaid + d.teller));
  const bw = W / days.length;
  const total = days.reduce((t, d) => t + d.plaid + d.teller, 0);
  return (
    <div ref={box} style={{ width: "100%", maxWidth: 640 }}>
    <svg viewBox={`0 0 ${W} ${H + 16}`} width={W} height={H + 16} role="img" aria-label={`${total} calls to the bank services in the last ${days.length} days`} style={{ maxWidth: "100%", display: "block" }}>
      {days.map((d, i) => {
        const h = ((d.plaid + d.teller) / max) * H;
        return (
          <g key={d.date}>
            <title>{`${shortDate(d.date)}: ${d.plaid} to Plaid${d.teller ? `, ${d.teller} to Teller` : ""}`}</title>
            {h > 0 && <rect x={i * bw + 1} y={H - h} width={Math.max(1, bw - 2)} height={h} rx={Math.min(3, bw / 3)} fill="var(--paycheck)" />}
          </g>
        );
      })}
      <line x1={0} x2={W} y1={H} y2={H} stroke="var(--hairline)" />
      <text x={0} y={H + 13} fontSize={11} fill="var(--muted)">{days[0] ? monthDay(days[0].date) : ""}</text>
      <text x={W} y={H + 13} fontSize={11} fill="var(--muted)" textAnchor="end">today</text>
    </svg>
    </div>
  );
}

export function Freshness() {
  const state = useApi<Freshness>("/api/v1/banks/freshness");
  const { data, reload } = state;
  const [price, setPrice] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ at: "every" | "price"; text: string; tone: "info" | "error" } | null>(null);
  const box = { border: "1px solid var(--hairline)", borderRadius: 12, padding: 12, display: "flex", flexDirection: "column", gap: 10 } as const;
  const title = <h3 style={{ margin: 0, fontSize: 14, fontWeight: 800 }}>How fresh, and what it costs</h3>;
  if (!data) {
    return (
      <section style={box}>
        {title}
        {state.error ? <Failed error={state.error} what="how fresh the bank data is" onRetry={reload} card={false} /> : <Loading what="how fresh the bank data is" card={false} />}
      </section>
    );
  }
  async function put(at: "every" | "price", body: Record<string, unknown>, done: string) {
    setBusy(true);
    setNote(null);
    try {
      await api("/api/v1/banks/freshness", { method: "PUT", body: JSON.stringify(body) });
      setNote({ at, text: done, tone: "info" });
      reload();
    } catch (e) {
      setNote({ at, text: (e as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  }
  function forecast() {
    const value = parseMoney(price);
    if (value === null || value < 0) {
      setNote({ at: "price", text: "Type an amount like 0.30 or 1.50", tone: "error" });
      return;
    }
    put("price", { price_per_item: value }, "Forecast worked out.");
    setPrice("");
  }
  // Calls by what they did, in words; Teller's per-account paths fold into one line each.
  const month = Object.entries(data.calls.by_endpoint).reduce<Record<string, number>>((out, [k, n]) => ({ ...out, [callWord(k)]: (out[callWord(k)] ?? 0) + n }), {});
  const said = (at: "every" | "price") => (note?.at === at ? <Said text={note.text} tone={note.tone} /> : null);
  return (
    <section style={box}>
      {title}
      <Chain hours={data.sync_every_hours} />
      <span style={{ fontSize: 13, color: "var(--ink-2)", maxWidth: "80ch" }}>{data.chain}</span>
      <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, flexWrap: "wrap" }}>
        Read the banks every
        <select className="input" value={data.sync_every_hours} disabled={busy} onChange={(e) => put("every", { sync_every_hours: Number(e.target.value) }, "Saved.")} style={{ minHeight: 32 }}>
          {data.choices.map((c) => <option key={c} value={c}>{c} hour{c === 1 ? "" : "s"}</option>)}
        </select>
        {said("every")}
        <span style={{ color: "var(--ink-2)" }}>More often costs nothing more; the bank's own data still changes only a few times a day.</span>
      </label>
      {data.links.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table className="ledger">
            <thead><tr><th>Bank</th><th>Last read</th><th>Next read</th><th>Newest transaction</th></tr></thead>
            <tbody>
              {data.links.map((l) => (
                <tr key={l.id}>
                  <td>
                    {l.institution || l.provider}
                    {l.needs_login
                      ? <div style={{ marginTop: 4 }}><StatusPill status="crit">Needs a new sign-in</StatusPill></div>
                      : l.error ? <div style={{ display: "flex", gap: 4, alignItems: "flex-start", fontSize: 11, color: "var(--warning-ink)", marginTop: 2 }}><TriangleAlert size={12} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} /><span>Last read failed: {l.error.slice(0, 80)}</span></div> : null}
                  </td>
                  <td title={l.last_sync_at ? when(l.last_sync_at) : undefined}>{ago(l.last_sync_at)}</td>
                  <td title={l.next_sync_at ? when(l.next_sync_at) : undefined}>{ago(l.next_sync_at)}</td>
                  <td style={{ whiteSpace: "nowrap" }}>{l.newest_transaction ? when(l.newest_transaction) : "none yet"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span style={{ fontSize: 12, fontWeight: 700 }}>Calls to the bank services, last 30 days</span>
        <Calls days={data.calls.by_day} />
        {Object.keys(month).length > 0 && <span style={{ fontSize: 12, color: "var(--ink-2)" }}>This month: {Object.entries(month).map(([k, n]) => `${n} × ${k}`).join(" · ")}</span>}
      </div>
      <div style={{ fontSize: 13, color: "var(--ink-2)", display: "flex", flexDirection: "column", gap: 6, maxWidth: "80ch" }}>
        {data.plaid.environment === "production" && <span><strong>Plaid's free Trial:</strong> {data.plaid.items_made} of {data.plaid.trial_limit} bank logins used; removing one does not give it back.</span>}
        <span>{data.cost.plan_note}</span>
        <span>{data.cost.teller_note}</span>
        <label style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          If you move to a paid plan, its price per bank login a month: $
          <input className="input num" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} placeholder={data.cost.price_per_item !== null ? String(data.cost.price_per_item) : "from Plaid's dashboard"} style={{ width: 150, minHeight: 32 }} />
          <button className="btn" type="button" disabled={busy || price.trim() === ""} onClick={forecast}>Forecast</button>
          {data.cost.price_per_item !== null && <button className="btn" type="button" disabled={busy} onClick={() => put("price", { clear_price: true }, "Price cleared.")}>Clear</button>}
          {said("price")}
        </label>
        {data.cost.monthly !== null && (
          <span><strong>Forecast:</strong> {money(data.cost.monthly, { cents: true })} a month, {money(data.cost.yearly ?? 0, { cents: true })} a year, for the {data.plaid.items_active} bank login{data.plaid.items_active === 1 ? "" : "s"} connected now, however often the app reads.</span>
        )}
      </div>
    </section>
  );
}
