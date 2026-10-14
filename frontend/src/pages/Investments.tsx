// Investing: every 401(k), IRA, brokerage account and HSA with its vendor, balance and holdings, the balance
// over time, and what the paychecks put in. Added by signing in at the vendor through Plaid, or by hand.
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, useApi } from "../api";
import { money, shortDate } from "../format";
import { openPlaidLink } from "../plaid";

interface HoldingRow {
  id: number;
  name: string;
  ticker: string;
  kind: string;
  quantity: number | null;
  price: number | null;
  value: number | null;
  share: number;
  as_of: string | null;
}

interface InvestmentAccount {
  id: number;
  name: string;
  institution: string;
  subtype: string;
  what: string;
  retirement: boolean;
  balance: number | null;
  balance_at: string | null;
  stale_days: number | null;
  connector: string;
  holdings: HoldingRow[];
  holdings_total: number;
  history: { date: string; balance: number }[];
}

interface Overview {
  as_of: string;
  total: number;
  retirement: number;
  taxable: number;
  items: InvestmentAccount[];
  allocation: { kind: string; value: number; share: number }[];
  history: { date: string; balance: number }[];
  contributions: { per_paycheck: number; items: { name: string; last: number; average: number; paystubs: number }[] };
  stale: string[];
  subtypes: string[];
  vendors: { name: string; via: "plaid" | "manual" }[];
  not_advice: string;
}

const SUBTYPE_WORD: Record<string, string> = { "401k": "401(k)", "403b": "403(b)", "457b": "457(b)", ira: "IRA", roth_ira: "Roth IRA", sep_ira: "SEP IRA", simple_ira: "SIMPLE IRA", tsp: "TSP", pension: "pension", brokerage: "brokerage", hsa: "HSA", "529": "529 plan", crypto: "crypto", stock_plan: "stock plan", other: "other" };
const KIND_TONE: Record<string, string> = { equity: "var(--paycheck)", etf: "var(--paycheck-ink)", "mutual fund": "var(--debt)", "fixed income": "var(--safe)", cash: "var(--safe-ink)", cryptocurrency: "var(--bill)", derivative: "var(--warning)", other: "var(--muted)" };

function Line({ points }: { points: { date: string; balance: number }[] }) {
  if (points.length < 2) return null;
  const w = 320;
  const h = 64;
  const min = Math.min(...points.map((p) => p.balance));
  const max = Math.max(...points.map((p) => p.balance));
  const x = (i: number) => (i / (points.length - 1)) * (w - 4) + 2;
  const y = (v: number) => (max === min ? h / 2 : h - 4 - ((v - min) / (max - min)) * (h - 8));
  const d = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.balance).toFixed(1)}`).join(" ");
  const last = points[points.length - 1];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} role="img" aria-label="Balance over time" style={{ maxWidth: 360, display: "block" }}>
      <title>{`${shortDate(points[0].date)} ${money(points[0].balance)} to ${shortDate(last.date)} ${money(last.balance)}`}</title>
      <path d={`${d} L${x(points.length - 1).toFixed(1)},${h} L2,${h} Z`} fill="var(--paycheck-soft)" />
      <path d={d} fill="none" stroke="var(--paycheck)" strokeWidth={2} />
      <circle cx={x(points.length - 1)} cy={y(last.balance)} r={3.5} fill="var(--paycheck)" stroke="var(--surface)" strokeWidth={2} />
    </svg>
  );
}

export function Investments() {
  const { data, reload, error } = useApi<Overview>("/api/v1/investments");
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ name: "", institution: "", subtype: "401k", balance: "", as_of: "" });
  const [balances, setBalances] = useState<Record<number, string>>({});
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [params] = useSearchParams();
  const wanted = params.get("update");
  useEffect(() => {
    if (wanted && data) {
      const box = document.getElementById(`balance-${wanted}`) as HTMLInputElement | null;
      box?.scrollIntoView({ block: "center", behavior: "smooth" });
      box?.focus();
    }
  }, [wanted, data]);

  async function connect() {
    setBusy(true);
    setNote(null);
    try {
      await openPlaidLink({ purpose: "investments", onDone: (m) => { setNote(m); reload(); }, onExit: () => setBusy(false) });
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function add(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api("/api/v1/investments", { method: "POST", body: JSON.stringify({ name: form.name, institution: form.institution, subtype: form.subtype, balance: form.balance === "" ? null : Number(form.balance), as_of: form.as_of || null }) });
      setForm({ name: "", institution: "", subtype: "401k", balance: "", as_of: "" });
      setAdding(false);
      reload();
    } catch (err) {
      setNote((err as Error).message);
    }
  }
  async function updateBalance(id: number) {
    const value = balances[id];
    if (!value) return;
    await api(`/api/v1/investments/${id}`, { method: "PUT", body: JSON.stringify({ balance: Number(value) }) });
    setBalances({ ...balances, [id]: "" });
    reload();
  }
  async function remove(id: number) {
    await api(`/api/v1/investments/${id}`, { method: "DELETE" });
    reload();
  }
  async function importHoldings(id: number, file: File) {
    const body = new FormData();
    body.append("file", file);
    try {
      const r = await api<{ summary: string }>(`/api/v1/imports/holdings/${id}`, { method: "POST", body });
      setNote(r.summary);
      reload();
    } catch (e) {
      setNote((e as Error).message);
    }
  }

  const d = data;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 980 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Investing</h1>
        <div style={{ flexGrow: 1 }} />
        <button className="btn dark" type="button" disabled={busy} onClick={connect}>{busy ? "Opening…" : "Sign in at the vendor"}</button>
        <button className="btn" type="button" onClick={() => setAdding(!adding)}>{adding ? "Cancel" : "Add by hand"}</button>
      </div>
      <p style={{ margin: 0, color: "var(--ink-2)", fontSize: 13 }}>
        A 401(k), an IRA, a brokerage account, an HSA: sign in at the vendor and it reads itself, holdings and all, through Plaid's investments product (the vendor's own sign-in page opens; the app holds a read token, never a password). A vendor with no connection is kept by hand and updated from its statements.
        {d && !d.vendors ? null : " "}
        <Link to="/settings/connectors">Plaid keys are on Connectors.</Link> <Link to="/outlook">The Outlook carries these forward.</Link>
      </p>
      {error && <div style={{ color: "var(--critical)" }}>{error.message}</div>}
      {note && <div className="card" style={{ padding: 10, fontSize: 13 }}>{note}</div>}
      {adding && (
        <form onSubmit={add} className="card" style={{ padding: 14, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 160px" }}><span className="lab">Name</span><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Work 401(k)" required /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 160px" }}><span className="lab">Vendor</span><input className="input" list="vendors" value={form.institution} onChange={(e) => setForm({ ...form, institution: e.target.value })} placeholder="Fidelity" /><datalist id="vendors">{(d?.vendors ?? []).map((v) => <option key={v.name} value={v.name.split(" (")[0]} />)}</datalist></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">What it is</span><select className="input" value={form.subtype} onChange={(e) => setForm({ ...form, subtype: e.target.value })}>{(d?.subtypes ?? Object.keys(SUBTYPE_WORD)).map((s) => <option key={s} value={s}>{SUBTYPE_WORD[s] ?? s}</option>)}</select></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Balance</span><input className="input num" inputMode="decimal" value={form.balance} onChange={(e) => setForm({ ...form, balance: e.target.value })} placeholder="12,345.67" style={{ width: 130 }} /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">As of</span><input className="input" type="date" value={form.as_of} onChange={(e) => setForm({ ...form, as_of: e.target.value })} /></label>
          <button className="btn dark" type="submit">Keep it</button>
        </form>
      )}
      {d && (
        <>
          <section className="card" style={{ padding: 16, display: "flex", gap: 24, flexWrap: "wrap", alignItems: "flex-start" }}>
            <div><div className="lab">Invested in all</div><div className="num" style={{ fontSize: 34, fontWeight: 800, letterSpacing: "-0.02em", lineHeight: 1.1 }}>{money(d.total)}</div></div>
            <div><div className="lab">Retirement</div><div className="num" style={{ fontSize: 22, fontWeight: 800, color: "var(--paycheck)" }}>{money(d.retirement)}</div></div>
            <div><div className="lab">Taxable</div><div className="num" style={{ fontSize: 22, fontWeight: 800 }}>{money(d.taxable)}</div></div>
            {d.contributions.per_paycheck > 0 && <div><div className="lab">From each paycheck</div><div className="num" style={{ fontSize: 22, fontWeight: 800, color: "var(--safe)" }}>{money(d.contributions.per_paycheck)}</div><div style={{ fontSize: 11, color: "var(--muted)" }}>{d.contributions.items.map((c) => `${c.name} ${money(c.last)}`).join(" · ")}</div></div>}
            <div style={{ flex: "1 1 260px" }}><Line points={d.history} /></div>
          </section>
          {d.allocation.length > 0 && (
            <section className="card" style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 8 }}>
              <div className="lab">Allocation, by what the holdings are</div>
              <div style={{ display: "flex", height: 14, borderRadius: 6, overflow: "hidden", gap: 2 }}>
                {d.allocation.map((a) => <div key={a.kind} title={`${a.kind}: ${money(a.value)} (${(a.share * 100).toFixed(0)}%)`} style={{ width: `${a.share * 100}%`, background: KIND_TONE[a.kind] ?? "var(--muted)" }} />)}
              </div>
              <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12, color: "var(--ink-2)" }}>
                {d.allocation.map((a) => <span key={a.kind} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span className="sw" style={{ background: KIND_TONE[a.kind] ?? "var(--muted)", margin: 0 }} />{a.kind} {(a.share * 100).toFixed(0)}%</span>)}
              </div>
            </section>
          )}
          {d.items.length === 0 && <div className="card" style={{ padding: 18, color: "var(--ink-2)" }}>No investment or retirement account yet. Sign in at the vendor, or add one by hand with its balance from the last statement.</div>}
          {d.items.map((a) => (
            <section key={a.id} className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
                <strong style={{ fontSize: 15 }}>{a.name}</strong>
                <span style={{ color: "var(--ink-2)", fontSize: 13 }}>{a.what}{a.institution ? ` at ${a.institution}` : ""} · {a.connector === "manual" ? "kept by hand" : `through ${a.connector}`}</span>
                <div style={{ flexGrow: 1 }} />
                <span className="num" style={{ fontSize: 20, fontWeight: 800 }}>{a.balance === null ? "—" : money(a.balance)}</span>
                <span style={{ fontSize: 12, color: a.stale_days !== null && a.stale_days > 35 ? "var(--warning)" : "var(--muted)" }}>{a.balance_at ? `as of ${shortDate(a.balance_at.slice(0, 10))}` : "no balance yet"}</span>
              </div>
              {a.history.length > 1 && <Line points={a.history} />}
              {a.holdings.length > 0 && (
                <div style={{ overflowX: "auto" }}>
                  <table className="ledger">
                    <thead><tr><th>Holding</th><th>Kind</th><th style={{ textAlign: "right" }}>Units</th><th style={{ textAlign: "right" }}>Price</th><th style={{ textAlign: "right" }}>Value</th><th style={{ textAlign: "right" }}>Share</th></tr></thead>
                    <tbody>
                      {a.holdings.map((h) => (
                        <tr key={h.id}>
                          <td><strong>{h.ticker || h.name}</strong>{h.ticker ? <span style={{ color: "var(--muted)" }}> · {h.name}</span> : null}</td>
                          <td style={{ color: "var(--ink-2)" }}>{h.kind}</td>
                          <td className="num" style={{ textAlign: "right", color: "var(--ink-2)" }}>{h.quantity ?? "—"}</td>
                          <td className="num" style={{ textAlign: "right", color: "var(--ink-2)" }}>{h.price === null ? "—" : money(h.price, { cents: true })}</td>
                          <td className="num" style={{ textAlign: "right", fontWeight: 700 }}>{h.value === null ? "—" : money(h.value)}</td>
                          <td className="num" style={{ textAlign: "right", color: "var(--muted)" }}>{(h.share * 100).toFixed(0)}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {a.connector === "manual" && (
                <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                  <input id={`balance-${a.id}`} className="input num" inputMode="decimal" value={balances[a.id] ?? ""} onChange={(e) => setBalances({ ...balances, [a.id]: e.target.value })} placeholder="new balance from the statement" style={{ minHeight: 32, padding: "4px 8px", fontSize: 12, width: 220, ...(wanted === String(a.id) ? { outline: "3px solid var(--warning)", outlineOffset: 2 } : {}) }} aria-label={`New balance for ${a.name}`} />
                  <button className="btn" type="button" style={{ minHeight: 32, padding: "4px 10px", fontSize: 12 }} onClick={() => updateBalance(a.id)}>Record</button>
                  <label className="btn" style={{ minHeight: 32, padding: "4px 10px", fontSize: 12, display: "inline-flex", alignItems: "center", cursor: "pointer" }}>
                    Import holdings (CSV or OFX)
                    <input type="file" accept=".csv,.ofx,.qfx,text/csv" style={{ display: "none" }} onChange={(e) => e.target.files?.[0] && importHoldings(a.id, e.target.files[0])} />
                  </label>
                  <div style={{ flexGrow: 1 }} />
                  <button className="btn" type="button" style={{ minHeight: 32, padding: "4px 10px", fontSize: 12 }} onClick={() => remove(a.id)}>Remove</button>
                </div>
              )}
            </section>
          ))}
          <div style={{ fontSize: 12, color: "var(--muted)" }}>{d.not_advice}</div>
        </>
      )}
    </div>
  );
}
