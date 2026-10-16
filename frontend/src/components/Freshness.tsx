// How fresh the bank data is and what reading it costs: the chain from the bank to this screen, how often the app
// reads, every call it made in the last thirty days, the free plan's logins used, and a forecast at any price.
import { useState } from "react";
import { api, useApi } from "../api";
import { money } from "../format";

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
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (Number.isNaN(mins)) return "";
  if (mins < 0) return `in ${Math.abs(mins) < 60 ? `${Math.abs(mins)} min` : `${Math.round(Math.abs(mins) / 60)} h`}`;
  return mins < 60 ? `${mins} min ago` : mins < 48 * 60 ? `${Math.round(mins / 60)} h ago` : `${Math.round(mins / 1440)} days ago`;
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

function Calls({ days }: { days: Freshness["calls"]["by_day"] }) {
  const W = 600;
  const H = 70;
  const max = Math.max(1, ...days.map((d) => d.plaid + d.teller));
  const bw = W / days.length;
  const total = days.reduce((t, d) => t + d.plaid + d.teller, 0);
  return (
    <svg viewBox={`0 0 ${W} ${H + 16}`} width="100%" role="img" aria-label={`${total} calls to the bank services in the last ${days.length} days`} style={{ maxWidth: 640, display: "block" }}>
      {days.map((d, i) => {
        const h = ((d.plaid + d.teller) / max) * H;
        return (
          <g key={d.date}>
            <title>{`${d.date}: ${d.plaid} to Plaid${d.teller ? `, ${d.teller} to Teller` : ""}`}</title>
            {h > 0 && <rect x={i * bw + 1} y={H - h} width={Math.max(1, bw - 2)} height={h} rx={Math.min(3, bw / 3)} fill="var(--paycheck)" />}
          </g>
        );
      })}
      <line x1={0} x2={W} y1={H} y2={H} stroke="var(--hairline)" />
      <text x={0} y={H + 13} fontSize={10} fill="var(--muted)">{days[0]?.date.slice(5)}</text>
      <text x={W} y={H + 13} fontSize={10} fill="var(--muted)" textAnchor="end">today</text>
    </svg>
  );
}

export function Freshness() {
  const { data, reload } = useApi<Freshness>("/api/v1/banks/freshness");
  const [price, setPrice] = useState("");
  const [busy, setBusy] = useState(false);
  if (!data) return null;
  async function put(body: Record<string, unknown>) {
    setBusy(true);
    try {
      await api("/api/v1/banks/freshness", { method: "PUT", body: JSON.stringify(body) });
      reload();
    } finally {
      setBusy(false);
    }
  }
  const month = Object.entries(data.calls.by_endpoint);
  return (
    <section style={{ border: "1px solid var(--hairline)", borderRadius: 12, padding: 12, display: "flex", flexDirection: "column", gap: 10 }}>
      <strong>How fresh, and what it costs</strong>
      <Chain hours={data.sync_every_hours} />
      <span style={{ fontSize: 13, color: "var(--ink-2)", maxWidth: "80ch" }}>{data.chain}</span>
      <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, flexWrap: "wrap" }}>
        Read the banks every
        <select className="input" value={data.sync_every_hours} disabled={busy} onChange={(e) => put({ sync_every_hours: Number(e.target.value) })} style={{ minHeight: 32 }}>
          {data.choices.map((c) => <option key={c} value={c}>{c} hour{c === 1 ? "" : "s"}</option>)}
        </select>
        <span style={{ color: "var(--muted)" }}>More often costs nothing more; the bank's own data still changes only a few times a day.</span>
      </label>
      {data.links.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table className="ledger">
            <thead><tr><th>Bank</th><th>Last read</th><th>Next read</th><th>Newest transaction</th></tr></thead>
            <tbody>
              {data.links.map((l) => (
                <tr key={l.id}>
                  <td>{l.institution || l.provider}{l.needs_login ? <div style={{ fontSize: 11, color: "var(--critical)" }}>needs a new sign-in</div> : l.error ? <div style={{ fontSize: 11, color: "var(--warning)" }}>{l.error.slice(0, 80)}</div> : null}</td>
                  <td>{ago(l.last_sync_at)}</td>
                  <td>{ago(l.next_sync_at)}</td>
                  <td>{l.newest_transaction ?? "none yet"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span style={{ fontSize: 12, fontWeight: 700 }}>Calls to the bank services, last 30 days</span>
        <Calls days={data.calls.by_day} />
        {month.length > 0 && <span style={{ fontSize: 12, color: "var(--ink-2)" }}>This month: {month.map(([k, n]) => `${n} × ${k.replace(":", " ")}`).join(" · ")}</span>}
      </div>
      <div style={{ fontSize: 13, color: "var(--ink-2)", display: "flex", flexDirection: "column", gap: 6, maxWidth: "80ch" }}>
        {data.plaid.environment === "production" && <span><strong>Plaid's free Trial:</strong> {data.plaid.items_made} of {data.plaid.trial_limit} bank logins used; removing one does not give it back.</span>}
        <span>{data.cost.plan_note}</span>
        <span>{data.cost.teller_note}</span>
        <label style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          If you move to a paid plan, its price per bank login a month: $
          <input className="input num" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} placeholder={data.cost.price_per_item !== null ? String(data.cost.price_per_item) : "from Plaid's dashboard"} style={{ width: 150, minHeight: 32 }} />
          <button className="btn" type="button" disabled={busy || price.trim() === "" || Number.isNaN(Number(price))} onClick={() => { put({ price_per_item: Number(price) }); setPrice(""); }}>Forecast</button>
          {data.cost.price_per_item !== null && <button className="btn" type="button" disabled={busy} onClick={() => put({ clear_price: true })}>Clear</button>}
        </label>
        {data.cost.monthly !== null && (
          <span><strong>Forecast:</strong> {money(data.cost.monthly, { cents: true })} a month, {money(data.cost.yearly ?? 0, { cents: true })} a year, for the {data.plaid.items_active} bank login{data.plaid.items_active === 1 ? "" : "s"} connected now, however often the app reads.</span>
        )}
      </div>
    </section>
  );
}
