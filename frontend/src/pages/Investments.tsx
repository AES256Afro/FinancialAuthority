// Investing: every 401(k), IRA, brokerage account and HSA with its vendor, balance and holdings, the balance
// over time, what the paychecks put in, and what the holdings have gained against their cost where the vendor gives
// it. Added by signing in at the vendor through Plaid, or by hand.
import { Info, Minus, TrendingDown, TrendingUp } from "lucide-react";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, useApi } from "../api";
import { ConfirmButton } from "../components/ConfirmButton";
import { Failed, Loading, Said } from "../components/State";
import { StatusPill } from "../components/Status";
import { money, parseMoney, shortDate, when } from "../format";
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
// Series colours from the validated palette; green, amber and red are kept for status and never used here.
const KIND_TONE: Record<string, string> = { equity: "var(--paycheck)", etf: "var(--paycheck-ink)", "mutual fund": "var(--debt)", "fixed income": "var(--saving)", cash: "var(--buffer)", cryptocurrency: "var(--bill)", derivative: "var(--later)", other: "var(--muted)" };

/** When a balance is from. A statement's balance is kept at midnight UTC of its day, so it is shown as that day;
 * a balance read from a bank link is a real moment, shown in local time. */
function balanceWhen(at: string): string {
  return /T00:00:00(\.0+)?(Z|\+00:00)?$/.test(at) ? shortDate(at.slice(0, 10)) : when(at);
}

type Note = { text: string; tone: "info" | "error" } | null;

// What each account's holdings have gained against what was paid for them (GET /api/v1/assets, "returns").
interface Gain {
  account_id: number;
  name: string;
  known: boolean;
  value: number;
  cost: number | null;
  gain: number | null;
  gain_share: number | null;
  holdings_without_cost: number;
}
interface Returns {
  accounts: Gain[];
  gain_total: number;
  cost_total: number;
  note: string;
}

// Up or down as an icon and a word, never colour alone.
function Direction({ gain, share }: { gain: number; share: number | null }) {
  const up = gain > 0;
  const down = gain < 0;
  const Icon = up ? TrendingUp : down ? TrendingDown : Minus;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontWeight: 800, color: up ? "var(--safe-ink)" : down ? "var(--critical-ink)" : "var(--ink-2)" }}>
      <Icon size={15} aria-hidden="true" />
      {up ? "up" : down ? "down" : "even"} <span className="num">{money(Math.abs(gain))}</span>
      {share !== null && share !== 0 ? <span className="num">({(Math.abs(share) * 100).toFixed(1)}%)</span> : null}
    </span>
  );
}

// The picture: what was paid and what it is worth now, as two bars on one scale.
function PaidAndWorth({ cost, value }: { cost: number; value: number }) {
  const top = Math.max(cost, value);
  const rows = [
    { word: "Paid", amount: cost, tone: "var(--muted)" },
    { word: "Worth now", amount: value, tone: "var(--paycheck)" },
  ];
  return (
    <div role="img" aria-label={`Paid ${money(cost)}, worth ${money(value)} now`} style={{ display: "grid", gridTemplateColumns: "auto minmax(0, 1fr) auto", gap: "4px 8px", alignItems: "center", fontSize: 12, maxWidth: 460 }}>
      {rows.map((r) => (
        <Fragment key={r.word}>
          <span style={{ color: "var(--ink-2)" }}>{r.word}</span>
          <div style={{ height: 10, background: "var(--surface-2)", borderRadius: 4 }}>
            <div style={{ width: top > 0 ? `${Math.max(0, r.amount / top) * 100}%` : 0, height: "100%", background: r.tone, borderRadius: 4 }} />
          </div>
          <span className="num" style={{ fontWeight: 700, textAlign: "right" }}>{money(r.amount)}</span>
        </Fragment>
      ))}
    </div>
  );
}

function GainLine({ r, loading }: { r: Gain | undefined; loading: boolean }) {
  if (loading) return <div style={{ fontSize: 12, color: "var(--muted)" }}>Gain against cost: loading…</div>;
  if (!r) return null;
  if (!r.known || r.cost === null || r.gain === null) {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, color: "var(--ink-2)" }}>
        <Info size={14} aria-hidden="true" style={{ flexShrink: 0 }} />
        <span><span className="lab">Gain against cost</span> The vendor does not give the cost.</span>
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <PaidAndWorth cost={r.cost} value={r.value} />
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", fontSize: 13 }}>
        <span className="lab">Gain against cost</span>
        <Direction gain={r.gain} share={r.gain_share} />
        {r.holdings_without_cost > 0 && <span style={{ fontSize: 12, color: "var(--muted)" }}>{r.holdings_without_cost} holding{r.holdings_without_cost === 1 ? "" : "s"} without a cost {r.holdings_without_cost === 1 ? "is" : "are"} left out.</span>}
      </div>
    </div>
  );
}

// A real button that opens the file picker, so the keyboard reaches it like any other button.
function ImportHoldings({ onFile }: { onFile: (f: File) => void }) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <button type="button" className="btn" style={{ minHeight: 32, padding: "4px 10px", fontSize: 12 }} onClick={() => input.current?.click()}>Import holdings (CSV or OFX)</button>
      <input ref={input} type="file" accept=".csv,.ofx,.qfx,text/csv" tabIndex={-1} aria-hidden="true" style={{ display: "none" }} onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) onFile(f); }} />
    </>
  );
}

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
  const said = `Balance over time: ${shortDate(points[0].date)} ${money(points[0].balance)}, ${shortDate(last.date)} ${money(last.balance)}`;
  return (
    <div style={{ maxWidth: 360, display: "flex", flexDirection: "column", gap: 2 }}>
      <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} role="img" aria-label={said} style={{ display: "block" }}>
        <title>{said}</title>
        <path d={`${d} L${x(points.length - 1).toFixed(1)},${h} L2,${h} Z`} fill="var(--paycheck-soft)" />
        <path d={d} fill="none" stroke="var(--paycheck)" strokeWidth={2} />
        <circle cx={2} cy={y(points[0].balance)} r={3} fill="var(--paycheck)" />
        <circle cx={x(points.length - 1)} cy={y(last.balance)} r={3.5} fill="var(--paycheck)" stroke="var(--surface)" strokeWidth={2} />
      </svg>
      <div aria-hidden="true" style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 11, color: "var(--ink-2)" }}>
        <span>{shortDate(points[0].date)} <strong className="num">{money(points[0].balance)}</strong></span>
        <span>{shortDate(last.date)} <strong className="num">{money(last.balance)}</strong></span>
      </div>
    </div>
  );
}

export function Investments() {
  const { data, reload, error } = useApi<Overview>("/api/v1/investments");
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ name: "", institution: "", subtype: "401k", balance: "", as_of: "" });
  const [balances, setBalances] = useState<Record<number, string>>({});
  const [note, setNote] = useState<string | null>(null);
  const [formNote, setFormNote] = useState<string | null>(null);
  const [rowNotes, setRowNotes] = useState<Record<number, Note>>({});
  const [busy, setBusy] = useState(false);
  const rowNote = (id: number, n: Note) => setRowNotes((all) => ({ ...all, [id]: n }));
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
      await openPlaidLink({ purpose: "investments", onNote: (m) => setNote(m), onDone: (m) => { setNote(m); reload(); }, onExit: (m) => { if (m) setNote(m); setBusy(false); } });
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function add(e: React.FormEvent) {
    e.preventDefault();
    const balance = form.balance.trim() === "" ? null : parseMoney(form.balance);
    if (form.balance.trim() !== "" && balance === null) {
      setFormNote("Type an amount like 120 or 1200.50");
      return;
    }
    setFormNote(null);
    try {
      await api("/api/v1/investments", { method: "POST", body: JSON.stringify({ name: form.name, institution: form.institution, subtype: form.subtype, balance, as_of: form.as_of || null }) });
      setForm({ name: "", institution: "", subtype: "401k", balance: "", as_of: "" });
      setAdding(false);
      setNote(`Added ${form.name}.`);
      reload();
    } catch (err) {
      setFormNote(`Couldn't add it: ${(err as Error).message}`);
    }
  }
  async function updateBalance(id: number) {
    const value = parseMoney(balances[id] ?? "");
    if (value === null) {
      rowNote(id, { tone: "error", text: "Type an amount like 120 or 1200.50" });
      return;
    }
    try {
      await api(`/api/v1/investments/${id}`, { method: "PUT", body: JSON.stringify({ balance: value }) });
      setBalances({ ...balances, [id]: "" });
      rowNote(id, { tone: "info", text: `Recorded ${money(value, { cents: true })}.` });
      reload();
    } catch (e) {
      rowNote(id, { tone: "error", text: `Couldn't record it: ${(e as Error).message}` });
    }
  }
  async function remove(id: number) {
    await api(`/api/v1/investments/${id}`, { method: "DELETE" });
    reload();
  }
  async function importHoldings(id: number, file: File) {
    const body = new FormData();
    body.append("file", file);
    rowNote(id, { tone: "info", text: `Reading ${file.name}…` });
    try {
      const r = await api<{ summary: string }>(`/api/v1/imports/holdings/${id}`, { method: "POST", body });
      rowNote(id, { tone: "info", text: r.summary });
      reload();
    } catch (e) {
      rowNote(id, { tone: "error", text: `Couldn't import ${file.name}: ${(e as Error).message}` });
    }
  }

  const gains = useApi<{ returns: Returns }>("/api/v1/assets");
  const returns = gains.data?.returns ?? null;
  const gainOf = useMemo(() => new Map((returns?.accounts ?? []).map((r) => [r.account_id, r] as [number, Gain])), [returns]);
  const anyKnown = (returns?.accounts ?? []).some((r) => r.known);

  const d = data;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 980 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Investing</h1>
        <div style={{ flexGrow: 1 }} />
        <button className="btn dark" type="button" disabled={busy} onClick={connect}>{busy ? "Opening…" : "Connect an account (through Plaid)"}</button>
        <button className="btn" type="button" onClick={() => setAdding(!adding)}>{adding ? "Cancel" : "Add by hand"}</button>
      </div>
      <p style={{ margin: 0, color: "var(--ink-2)", fontSize: 13 }}>
        A 401(k), an IRA, a brokerage account, an HSA: sign in at the vendor and it reads itself, holdings and all, through Plaid's investments product (the vendor's own sign-in page opens; the app holds a read token, never a password). A vendor with no connection is kept by hand and updated from its statements.
        {d && !d.vendors ? null : " "}
        <Link to="/settings/connectors">Plaid keys are on Settings › Connections.</Link> <Link to="/outlook">The Outlook carries these forward.</Link>
      </p>
      {error && !d && <Failed error={error} what="your investments" onRetry={reload} />}
      {gains.error && !gains.data && <Failed error={gains.error} what="the gain against cost" onRetry={gains.reload} />}
      {!d && !error && <Loading what="your investments" />}
      {note && <div className="card" role="status" style={{ padding: 10, fontSize: 13 }}>{note}</div>}
      {adding && (
        <form onSubmit={add} className="card" style={{ padding: 14, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 160px" }}><span className="lab">Name</span><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Work 401(k)" required /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 160px" }}><span className="lab">Vendor</span><input className="input" list="vendors" value={form.institution} onChange={(e) => setForm({ ...form, institution: e.target.value })} placeholder="Fidelity" /><datalist id="vendors">{(d?.vendors ?? []).map((v) => <option key={v.name} value={v.name.split(" (")[0]} />)}</datalist></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">What it is</span><select className="input" value={form.subtype} onChange={(e) => setForm({ ...form, subtype: e.target.value })}>{(d?.subtypes ?? Object.keys(SUBTYPE_WORD)).map((s) => <option key={s} value={s}>{SUBTYPE_WORD[s] ?? s}</option>)}</select></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Balance</span><input className="input num" inputMode="decimal" value={form.balance} onChange={(e) => setForm({ ...form, balance: e.target.value })} placeholder="12,345.67" style={{ width: 130 }} /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">As of</span><input className="input" type="date" value={form.as_of} onChange={(e) => setForm({ ...form, as_of: e.target.value })} /></label>
          <button className="btn dark" type="submit">Keep it</button>
          {formNote && <span style={{ alignSelf: "center" }}><Said text={formNote} tone="error" /></span>}
        </form>
      )}
      {d && (
        <>
          <section className="card" style={{ padding: 16, display: "flex", gap: 24, flexWrap: "wrap", alignItems: "flex-start" }}>
            <div><div className="lab">Invested in all</div><div className="num" style={{ fontSize: 34, fontWeight: 800, letterSpacing: "-0.02em", lineHeight: 1.1 }}>{money(d.total)}</div></div>
            <div><div className="lab">Retirement</div><div className="num" style={{ fontSize: 22, fontWeight: 800, color: "var(--paycheck-ink)" }}>{money(d.retirement)}</div></div>
            <div><div className="lab">Taxable</div><div className="num" style={{ fontSize: 22, fontWeight: 800 }}>{money(d.taxable)}</div></div>
            {d.contributions.per_paycheck > 0 && <div><div className="lab">From each paycheck</div><div className="num" style={{ fontSize: 22, fontWeight: 800, color: "var(--safe-ink)" }}>{money(d.contributions.per_paycheck)}</div><div style={{ fontSize: 11, color: "var(--muted)" }}>{d.contributions.items.map((c) => `${c.name} ${money(c.last)}`).join(" · ")}</div></div>}
            {returns && anyKnown && (
              <div>
                <div className="lab">Gain against cost</div>
                <div style={{ fontSize: 18 }}><Direction gain={returns.gain_total} share={null} /></div>
                <div style={{ fontSize: 11, color: "var(--muted)" }}>on <span className="num">{money(returns.cost_total)}</span> paid</div>
              </div>
            )}
            <div style={{ flex: "1 1 260px" }}><Line points={d.history} /></div>
            {returns && returns.accounts.length > 0 && <div style={{ flexBasis: "100%", fontSize: 12, color: "var(--muted)" }}>{returns.note}</div>}
          </section>
          {d.allocation.length > 0 && (
            <section className="card" style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 8 }}>
              <h2 className="lab" style={{ margin: 0 }}>Allocation, by what the holdings are</h2>
              <div style={{ display: "flex", height: 14, borderRadius: 6, overflow: "hidden", gap: 2 }}>
                {d.allocation.map((a) => <div key={a.kind} title={`${a.kind}: ${money(a.value)} (${(a.share * 100).toFixed(0)}%)`} style={{ width: `${a.share * 100}%`, background: KIND_TONE[a.kind] ?? "var(--muted)" }} />)}
              </div>
              <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12, color: "var(--ink-2)" }}>
                {d.allocation.map((a) => <span key={a.kind} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span className="sw" style={{ background: KIND_TONE[a.kind] ?? "var(--muted)", margin: 0 }} />{a.kind} {(a.share * 100).toFixed(0)}%</span>)}
              </div>
            </section>
          )}
          {d.items.length === 0 && <div className="card" style={{ padding: 18, color: "var(--ink-2)" }}>No investment or retirement account yet. Connect one through Plaid, or add one by hand with its balance from the last statement.</div>}
          {d.items.map((a) => (
            <section key={a.id} className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
                <h2 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>{a.name}</h2>
                <span style={{ color: "var(--ink-2)", fontSize: 13 }}>{a.what}{a.institution ? ` at ${a.institution}` : ""} · {a.connector === "manual" ? "kept by hand" : `through ${a.connector}`}</span>
                <div style={{ flexGrow: 1 }} />
                <span className="num" style={{ fontSize: 20, fontWeight: 800 }}>{a.balance === null ? "—" : money(a.balance)}</span>
                {a.balance_at && a.stale_days !== null && a.stale_days > 35
                  ? <StatusPill status="warn">as of {balanceWhen(a.balance_at)}, over a month old</StatusPill>
                  : <span style={{ fontSize: 12, color: "var(--muted)" }}>{a.balance_at ? `as of ${balanceWhen(a.balance_at)}` : "no balance yet"}</span>}
              </div>
              <GainLine r={gainOf.get(a.id)} loading={!gains.data && !gains.error} />
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
                <>
                <div style={{ display: "flex", gap: 6, alignItems: "flex-end", flexWrap: "wrap" }}>
                  <label htmlFor={`balance-${a.id}`} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                    <span className="lab">New balance from the statement</span>
                    <input id={`balance-${a.id}`} className="input num" inputMode="decimal" value={balances[a.id] ?? ""} onChange={(e) => setBalances({ ...balances, [a.id]: e.target.value })} style={{ minHeight: 32, padding: "4px 8px", fontSize: 12, width: "min(100%, 220px)", ...(wanted === String(a.id) ? { outline: "3px solid var(--warning)", outlineOffset: 2 } : {}) }} />
                  </label>
                  <button className="btn" type="button" style={{ minHeight: 32, padding: "4px 10px", fontSize: 12 }} onClick={() => updateBalance(a.id)}>Record</button>
                  <ImportHoldings onFile={(f) => importHoldings(a.id, f)} />
                  <div style={{ flexGrow: 1 }} />
                  <ConfirmButton label="Remove" question={`Remove ${a.name} and its balance history?`} yes="Yes, remove it" no="Keep it" onConfirm={() => remove(a.id)} style={{ minHeight: 32, padding: "4px 10px", fontSize: 12 }} />
                </div>
                {rowNotes[a.id] && <Said text={rowNotes[a.id]?.text ?? null} tone={rowNotes[a.id]?.tone} />}
                </>
              )}
            </section>
          ))}
          <div style={{ fontSize: 12, color: "var(--muted)" }}>{d.not_advice}</div>
        </>
      )}
    </div>
  );
}
