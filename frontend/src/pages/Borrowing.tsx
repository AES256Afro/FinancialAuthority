// Borrowing: what advances and installments cost, the cycle they run in, what stands outstanding, what the
// same money would become, and the steps that end it. The engine computed every figure; this page draws it.
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { money, shortDate } from "../format";
import { palette } from "../looks";
import { getPrefs } from "../prefs";

interface Provider {
  provider: string;
  advances: number;
  advanced: number;
  fees: number;
  outstanding: number;
  typical_advance: number;
  fee_share: number;
  typical_days: number | null;
  annual_rate: number | null;
}

interface Cycle {
  payday: string;
  advances: number;
  advanced: number;
  repaid_on_payday: number;
  fees: number;
}

interface Stream {
  provider: string;
  amount: number;
  cadence_days: number;
  cadence_assumed: boolean;
  payments_seen: number;
  last_date: string;
  next_date: string;
  active: boolean;
  paid_so_far: number;
  status: "plan" | "bill" | "pending" | "unknown";
  plan_id: number | null;
  bill_id: number | null;
  series_id: number | null;
  remaining: number | null;
  ends: string | null;
  per_month: number;
}

interface Borrowing {
  as_of: string;
  window_days: number;
  cost: { fees_90d: number; fees_month: number; fees_year: number; advanced_90d: number; advances_90d: number; tips_90d: number; installments_month: number; card_balance: number; card_interest_month: number | null; affirm_interest_month?: number; affirm_interest_ytd?: number };
  statement?: { period_start?: string; period_end?: string; interest_period?: number; interest_ytd?: number; total_closing?: number; at?: string };
  credit: { account_id: number; debt_id: number | null; name: string; balance: number; credit_limit: number | null; utilisation: number | null; apr: number | null; pay_in_full: boolean | null; next_statement: string | null; next_due: string | null; interest_month_if_carried: number }[];
  providers: Provider[];
  outstanding: { provider: string; amount: number; taken_on: string; repay_date: string }[];
  outstanding_total: number;
  cycle: Cycle[];
  typical_per_period: number;
  next_payday: string | null;
  installments: Stream[];
  loans: { id: number; provider: string; merchant: string; loan_id: string; loan_amount: number | null; total: number; finance_charge: number; installment: number; count: number; remaining: number; next_date: string; ends: string; left_to_pay: number; interest_left: number | null; from_lender: boolean; balance?: number | null; balance_at?: string | null; apr?: number | null; interest_paid?: number }[];
  loans_left_to_pay: number;
  loans_finance_charge: number;
  becomes: { per_paycheck: number; years: number; name: string; after_tax: number; guaranteed: boolean } | null;
  steps: { n: number; text: string; action: string; link: string }[];
  settings: { cards_pay_from: string };
  not_advice: string;
}

const CADENCE: Record<number, string> = { 7: "weekly", 14: "every two weeks", 30: "monthly" };

function CycleChart({ cycle }: { cycle: Cycle[] }) {
  const c = palette(getPrefs().look);
  const w = 720;
  const h = 170;
  const base = h - 30;
  const max = Math.max(1, ...cycle.map((x) => Math.max(x.advanced, x.repaid_on_payday)));
  const slot = cycle.length ? (w - 40) / cycle.length : w;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" role="img" aria-label="Advances taken before each payday and repaid on it, with the fee" style={{ display: "block", maxWidth: 820 }}>
      {cycle.map((x, i) => {
        const left = 20 + i * slot;
        const bw = Math.max(8, Math.min(28, slot / 3));
        const ha = (x.advanced / max) * 100;
        const hr = (x.repaid_on_payday / max) * 100;
        return (
          <g key={x.payday}>
            <title>{`${shortDate(x.payday)}: ${x.advances} advance${x.advances !== 1 ? "s" : ""} taken before it (${money(x.advanced)}), ${money(x.repaid_on_payday)} repaid on it, ${money(x.fees)} of that fees`}</title>
            <rect x={left} y={base - ha} width={bw} height={ha} rx={4} fill={c.debt} />
            <rect x={left + bw + 2} y={base - hr} width={bw} height={hr} rx={4} fill={c.bill} />
            {x.fees > 0 && <text x={left + bw + 1} y={base - Math.max(ha, hr) - 6} textAnchor="middle" fontSize={10} fontWeight={800} fill={c.ink}>{money(x.fees)} fees</text>}
            <text x={left + bw + 1} y={h - 10} textAnchor="middle" fontSize={11} fontWeight={700} fill={c.ink2}>{shortDate(x.payday).slice(0, 6)}</text>
          </g>
        );
      })}
      <line x1={10} x2={w - 10} y1={base} y2={base} stroke={c.buffer} strokeWidth={1.5} />
      <g fontSize={11} fontWeight={700} fill={c.ink2}>
        <rect x={20} y={6} width={10} height={10} rx={3} fill={c.debt} /><text x={36} y={15}>advanced before the payday</text>
        <rect x={220} y={6} width={10} height={10} rx={3} fill={c.bill} /><text x={236} y={15}>repaid on it, fee included</text>
      </g>
    </svg>
  );
}

function StreamRow({ s, onDone }: { s: Stream; onDone: () => void }) {
  const [remaining, setRemaining] = useState(s.remaining ? String(s.remaining) : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api("/api/v1/borrowing/plan", { method: "POST", body: JSON.stringify({ bill_id: s.bill_id ?? undefined, series_id: s.series_id ?? undefined, remaining: Number(remaining) }) });
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const canSet = s.status === "bill" || s.status === "pending";
  return (
    <tr>
      <td><strong>{s.provider}</strong><div style={{ fontSize: 11, color: "var(--muted)" }}>{CADENCE[s.cadence_days] ?? `every ${s.cadence_days} days`}{s.cadence_assumed ? " (assumed from one payment)" : ""} · {s.payments_seen} paid, {money(s.paid_so_far)} so far</div></td>
      <td className="num" style={{ textAlign: "right" }}>{money(s.amount, { cents: true })}</td>
      <td>{shortDate(s.next_date)}</td>
      <td>
        {s.status === "plan" ? (
          <span>{s.remaining} left · ends {s.ends ? shortDate(s.ends) : "?"}</span>
        ) : canSet ? (
          <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
            <input className="input num" style={{ width: 64 }} value={remaining} onChange={(e) => setRemaining(e.target.value)} inputMode="numeric" placeholder="left" aria-label={`Payments left on ${s.provider} ${money(s.amount)}`} />
            <button className="btn dark" type="button" disabled={busy || !remaining} onClick={save}>Set</button>
            {s.status === "bill" && <span style={{ fontSize: 11, color: "var(--muted)" }}>on the register as a bill, no end date</span>}
          </span>
        ) : (
          <span style={{ fontSize: 11, color: "var(--muted)" }}>not on the register</span>
        )}
        {error && <div style={{ fontSize: 12, color: "var(--critical)" }}>{error}</div>}
      </td>
    </tr>
  );
}

function StatementUpload({ onDone }: { onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  async function upload(f: File) {
    setBusy(true);
    setNote(null);
    try {
      const body = new FormData();
      body.append("file", f);
      const r = await api<{ loans: number; created: number; updated: number; retired: number; period_end: string | null; interest_period: number | null; interest_ytd: number | null; total_closing: number | null }>("/api/v1/borrowing/statement", { method: "POST", body });
      setNote(`${f.name}: ${r.loans} loans read${r.period_end ? ` for the period to ${shortDate(r.period_end)}` : ""}; ${r.created} new, ${r.updated} updated, ${r.retired} closed${r.total_closing !== null ? `; ${money(r.total_closing)} owed in all` : ""}${r.interest_period !== null ? `; ${money(r.interest_period)} of interest this period` : ""}.`);
      onDone();
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", borderTop: "1px solid var(--surface-2)", paddingTop: 8 }}>
      <input className="input" type="file" accept=".pdf,application/pdf" disabled={busy} onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} aria-label="Affirm statement PDF" />
      <span style={{ fontSize: 12, color: "var(--muted)" }}>Affirm's Loans Statement PDF. The same file dropped into the import folder lands by itself.</span>
      {note && <div style={{ fontSize: 13, flexBasis: "100%" }}>{note}</div>}
    </div>
  );
}

export function Borrowing() {
  const { data, reload, error } = useApi<Borrowing>("/api/v1/borrowing");
  const [saving, setSaving] = useState(false);
  if (error) return <div style={{ color: "var(--critical)" }}>{error.message}</div>;
  if (!data) return <div style={{ color: "var(--muted)" }}>Loading…</div>;
  const d = data;
  const active = d.installments.filter((s) => s.active);
  const unknown = active.filter((s) => s.status !== "plan").length;
  async function setCards(mode: string) {
    setSaving(true);
    try {
      await api("/api/v1/borrowing/settings", { method: "PUT", body: JSON.stringify({ cards_pay_from: mode }) });
      reload();
    } finally {
      setSaving(false);
    }
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", gap: 12 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, letterSpacing: "-0.01em" }}>Borrowing from pay</h1>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>advances, pay-later plans and the card, read from the ledger · last {d.window_days} days</div>
      </div>

      <section className="grid grid-cols-2 xl:grid-cols-4" style={{ gap: 12 }}>
        <div className="card" style={{ padding: 14, borderTop: "4px solid var(--critical)" }}>
          <div className="lab">Lost to borrowing, a month</div>
          <div className="num" style={{ fontSize: 28, fontWeight: 800 }}>{money(d.cost.fees_month)}</div>
          <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{money(d.cost.fees_year)} a year at this pace · advance fees {money(d.cost.fees_90d)} in {d.window_days} days over {d.cost.advances_90d} advances{d.cost.affirm_interest_month ? ` · Affirm interest ${money(d.cost.affirm_interest_month)} last statement, ${money(d.cost.affirm_interest_ytd ?? 0)} this year` : ""}</div>
        </div>
        <div className="card" style={{ padding: 14, borderTop: "4px solid var(--warning)" }}>
          <div className="lab">Out against the next check</div>
          <div className="num" style={{ fontSize: 28, fontWeight: 800 }}>{money(d.outstanding_total)}</div>
          <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{d.outstanding.length === 0 ? "nothing outstanding right now" : d.outstanding.map((o) => `${o.provider} ${money(o.amount)} due ${shortDate(o.repay_date)}`).join(" · ")}</div>
        </div>
        <div className="card" style={{ padding: 14, borderTop: "4px solid var(--bill)" }}>
          <div className="lab">Installments a month</div>
          <div className="num" style={{ fontSize: 28, fontWeight: 800 }}>{money(d.cost.installments_month)}</div>
          <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{active.length} stream{active.length !== 1 ? "s" : ""} running{unknown ? `, ${unknown} without an end date yet` : ""}</div>
        </div>
        <div className="card" style={{ padding: 14, borderTop: "4px solid var(--safe)" }}>
          <div className="lab">The fees, saved instead</div>
          {d.becomes ? (
            <>
              <div className="num" style={{ fontSize: 28, fontWeight: 800 }}>{money(d.becomes.after_tax)}</div>
              <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{money(d.becomes.per_paycheck)} a paycheck for {d.becomes.years} years in {d.becomes.name.toLowerCase()}{d.becomes.guaranteed ? ", guaranteed" : ""} · <Link to="/outlook">compare</Link></div>
            </>
          ) : (
            <div style={{ fontSize: 12, color: "var(--ink-2)" }}>no fees in the window</div>
          )}
        </div>
      </section>

      {d.cycle.length > 0 && (
        <section className="card" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}><strong>The cycle, payday by payday</strong><span style={{ fontSize: 12, color: "var(--muted)" }}>what was advanced before each payday and taken back on it</span></div>
          <CycleChart cycle={d.cycle} />
          {d.typical_per_period > 0 && <div style={{ fontSize: 13 }}>About <strong>{money(d.typical_per_period)}</strong> is advanced each pay period. Keeping that much of one check unspent is what ends the cycle; the next period is paid from it instead of from an advance.</div>}
        </section>
      )}

      <section className="grid grid-cols-1 xl:grid-cols-2" style={{ gap: 16, alignItems: "start" }}>
        <div className="card" style={{ padding: "16px 20px" }}>
          <div className="lab" style={{ marginBottom: 6 }}>By app</div>
          <table className="ledger">
            <thead><tr><th>App</th><th style={{ textAlign: "right" }}>Advances</th><th style={{ textAlign: "right" }}>Advanced</th><th style={{ textAlign: "right" }}>Fees</th><th>Cost</th></tr></thead>
            <tbody>
              {d.providers.map((p) => (
                <tr key={p.provider}>
                  <td><strong>{p.provider}</strong><div style={{ fontSize: 11, color: "var(--muted)" }}>usually {money(p.typical_advance)}{p.typical_days ? ` for ${p.typical_days} days` : ""}{p.outstanding ? ` · ${money(p.outstanding)} out now` : ""}</div></td>
                  <td className="num" style={{ textAlign: "right" }}>{p.advances}</td>
                  <td className="num" style={{ textAlign: "right" }}>{money(p.advanced)}</td>
                  <td className="num" style={{ textAlign: "right", color: "var(--critical)", fontWeight: 800 }}>{money(p.fees)}</td>
                  <td style={{ fontSize: 12 }}>{(p.fee_share * 100).toFixed(1)}% per advance{p.annual_rate !== null ? `, about ${Math.round(p.annual_rate * 100)}% a year` : ""}</td>
                </tr>
              ))}
              {d.providers.length === 0 && <tr><td colSpan={5} style={{ color: "var(--muted)" }}>No advance app in the ledger's last {d.window_days} days.</td></tr>}
            </tbody>
          </table>
          {d.cost.tips_90d > 0 && <div style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 6 }}>Tips and membership charges with no advance behind them: {money(d.cost.tips_90d)}, counted in the fees.</div>}
        </div>
        <div className="card" style={{ padding: "16px 20px" }} id="installments">
          <div className="lab" style={{ marginBottom: 6 }}>Pay-later plans</div>
          <table className="ledger">
            <thead><tr><th>Stream</th><th style={{ textAlign: "right" }}>Each</th><th>Next</th><th>Payments left</th></tr></thead>
            <tbody>
              {active.map((s) => <StreamRow key={`${s.provider}-${s.amount}-${s.status}`} s={s} onDone={reload} />)}
              {active.length === 0 && <tr><td colSpan={4} style={{ color: "var(--muted)" }}>No installment lender in the ledger's last {d.window_days} days.</td></tr>}
            </tbody>
          </table>
          <div style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 6 }}>The lender's app shows how many payments are left on each plan; with that, the windows see when it ends and the freed money moves on. Interest on a plan is the lender's own figure and is not guessed here.</div>
        </div>
      </section>

      {d.loans && d.loans.length > 0 && (
        <section className="card" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 8 }} id="loans">
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
            <strong>Pay-later loans, from the lender's own figures</strong>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>{money(d.loans_left_to_pay)} left to pay{d.statement?.period_end ? ` as of the statement to ${shortDate(d.statement.period_end)}` : ""}{d.loans_finance_charge > 0 ? ` · ${money(d.loans_finance_charge)} of finance charges over their lives` : ""}</span>
          </div>
          <table className="ledger" style={{ fontSize: 13 }}>
            <thead><tr><th>Loan</th><th style={{ textAlign: "right" }}>Borrowed</th><th style={{ textAlign: "right" }}>Owed</th><th style={{ textAlign: "right" }}>Rate</th><th style={{ textAlign: "right" }}>Interest so far</th><th style={{ textAlign: "right" }}>Each</th><th>Next</th></tr></thead>
            <tbody>
              {d.loans.map((l) => (
                <tr key={l.id}>
                  <td><strong>{l.merchant || l.provider}</strong><div style={{ fontSize: 11, color: "var(--muted)" }}>{l.provider}{l.loan_id ? ` · ${l.loan_id}` : ""}{l.balance_at ? ` · statement ${shortDate(l.balance_at)}` : l.from_lender ? " · from the confirmation email" : " · typed"}</div></td>
                  <td className="num" style={{ textAlign: "right" }}>{l.loan_amount !== null ? money(l.loan_amount) : money(l.total)}</td>
                  <td className="num" style={{ textAlign: "right", fontWeight: 800 }}>{money(l.balance ?? l.left_to_pay)}</td>
                  <td className="num" style={{ textAlign: "right", color: (l.apr ?? 0) > 0 ? "var(--critical)" : "var(--safe-ink)" }}>{l.apr !== null && l.apr !== undefined ? `${l.apr}%` : l.finance_charge > 0 ? money(l.finance_charge) : "0%"}</td>
                  <td className="num" style={{ textAlign: "right", color: "var(--critical)" }}>{l.interest_paid ? money(l.interest_paid) : l.finance_charge ? `${money(l.finance_charge)} over its life` : "—"}</td>
                  <td className="num" style={{ textAlign: "right" }}>{money(l.installment, { cents: true })}</td>
                  <td>{shortDate(l.next_date)}{l.remaining ? ` · about ${l.remaining} left` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <StatementUpload onDone={reload} />
          <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Affirm's monthly Loans Statement PDF, from Manage on affirm.com, is the lender's own register: upload it and every loan lands with its balance, rate and interest. The confirmation email for each new loan does the same with the exact schedule once a mailbox is connected.</div>
        </section>
      )}

      {d.credit.length > 0 && (
        <section className="card" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}><strong>Credit: the card and its line</strong><span style={{ fontSize: 12, color: "var(--muted)" }}>what gets reported is the balance on the statement day</span></div>
          {d.credit.map((c) => (
            <div key={c.account_id} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap", fontSize: 13 }}>
                <strong>{c.name}</strong>
                <span className="num">{money(c.balance)}{c.credit_limit ? ` of ${money(c.credit_limit)}` : ""}</span>
                {c.utilisation !== null && <span style={{ fontWeight: 800, color: c.utilisation > 0.3 ? "var(--critical)" : "var(--safe-ink)" }}>{(c.utilisation * 100).toFixed(0)}% in use</span>}
                {c.apr !== null && c.apr !== undefined && <span style={{ color: "var(--ink-2)" }}>{c.apr}% APR{c.pay_in_full ? ", paid in full" : `, ${money(c.interest_month_if_carried)} a month if carried`}</span>}
                {c.next_statement && <span style={{ color: "var(--ink-2)" }}>statement closes {shortDate(c.next_statement)}</span>}
                {c.next_due && <span style={{ color: "var(--ink-2)" }}>due {shortDate(c.next_due)}</span>}
                {!c.credit_limit && <Link to="/debts" style={{ fontSize: 12 }}>enter the line and APR →</Link>}
              </div>
              {c.credit_limit && (
                <div style={{ position: "relative", height: 12, background: "var(--surface-2)", borderRadius: 4, overflow: "hidden" }} title={`${money(c.balance)} of a ${money(c.credit_limit)} line`}>
                  <div style={{ width: `${Math.min(100, ((c.utilisation ?? 0) * 100))}%`, height: "100%", background: (c.utilisation ?? 0) > 0.3 ? "var(--critical)" : "var(--safe)" }} />
                  <div style={{ position: "absolute", left: "30%", top: 0, bottom: 0, width: 2, background: "var(--ink)" }} title="30% of the line" />
                </div>
              )}
            </div>
          ))}
          <div style={{ fontSize: 12, color: "var(--ink-2)" }}>The mark is 30% of the line. Scores weigh the share of the line in use on the day the statement closes, and whether every payment is on time; a balance paid before the statement closes reports as low use. Advances and pay-later plans do not report; a missed payment on them can.</div>
        </section>
      )}

      <section className="card" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}><strong>The way out, in order</strong><span style={{ fontSize: 12, color: "var(--muted)" }}>each step is a figure from the ledger and the place to act on it</span></div>
        <ol style={{ margin: 0, paddingLeft: 22, display: "flex", flexDirection: "column", gap: 6, fontSize: 14, lineHeight: 1.5 }}>
          {d.steps.map((s) => <li key={s.n}>{s.text} <Link to={s.link} style={{ whiteSpace: "nowrap" }}>{s.action} →</Link></li>)}
        </ol>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", fontSize: 13, borderTop: "1px solid var(--surface-2)", paddingTop: 8 }}>
          <span>The card balance of <strong>{money(d.cost.card_balance)}</strong> is held back from</span>
          <select className="input" value={d.settings.cards_pay_from} disabled={saving} onChange={(e) => setCards(e.target.value)} aria-label="Which checks pay the card" style={{ minHeight: 36 }}>
            <option value="auto">the next two checks, half each (or its minimum on its due day, when the Debts page knows one)</option>
            <option value="split">the next two checks, half each</option>
            <option value="next">the next check</option>
            <option value="mid">the 15th check</option>
            <option value="end">the end-of-month check</option>
            <option value="minimum">its minimum only, on its due day</option>
            <option value="off">nothing held back</option>
          </select>
          <span style={{ color: "var(--muted)", fontSize: 12 }}>as you pay it; the windows and safe to spend follow.</span>
        </div>
      </section>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{d.not_advice}</div>
    </div>
  );
}
