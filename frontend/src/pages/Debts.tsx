// Debts: the mountain. Every balance as a slice of the whole, the minimums due each month, the interest the
// month costs, and the two classic orders. The engine computed all of it; this page draws it.
import React, { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { money } from "../format";

interface DebtItem {
  id: number;
  name: string;
  kind: string;
  balance: number | null;
  apr: number | null;
  minimum: number | null;
  share: number;
  source: string;
  credit_limit?: number | null;
  utilisation?: number | null;
  statement_day?: number | null;
  due_day?: number | null;
  pay_in_full?: boolean;
  finance_charge?: number;
  ends?: string;
}

function Terms({ d, onSaved }: { d: DebtItem; onSaved: () => void }) {
  const [apr, setApr] = useState(d.apr !== null && d.apr !== undefined ? String(d.apr) : "");
  const [limit, setLimit] = useState(d.credit_limit ? String(d.credit_limit) : "");
  const [statement, setStatement] = useState(d.statement_day ? String(d.statement_day) : "");
  const [due, setDue] = useState(d.due_day ? String(d.due_day) : "");
  const [minimum, setMinimum] = useState(d.minimum ? String(d.minimum) : "");
  const [full, setFull] = useState(Boolean(d.pay_in_full));
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    try {
      await api(`/api/v1/debts/${d.id}`, { method: "PUT", body: JSON.stringify({ apr: apr ? Number(apr) : undefined, credit_limit: limit ? Number(limit) : undefined, statement_day: statement ? Number(statement) : undefined, due_day: due ? Number(due) : undefined, minimum: minimum ? Number(minimum) : undefined, pay_in_full: full }) });
      onSaved();
    } finally {
      setBusy(false);
    }
  }
  return (
    <tr>
      <td colSpan={6} style={{ paddingTop: 0 }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end", fontSize: 12 }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 2 }}><span className="lab">APR %</span><input className="input num" style={{ width: 80 }} value={apr} onChange={(e) => setApr(e.target.value)} inputMode="decimal" /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 2 }}><span className="lab">Line</span><input className="input num" style={{ width: 90 }} value={limit} onChange={(e) => setLimit(e.target.value)} inputMode="decimal" /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 2 }}><span className="lab">Statement day</span><input className="input num" style={{ width: 70 }} value={statement} onChange={(e) => setStatement(e.target.value)} inputMode="numeric" /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 2 }}><span className="lab">Due day</span><input className="input num" style={{ width: 70 }} value={due} onChange={(e) => setDue(e.target.value)} inputMode="numeric" /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 2 }}><span className="lab">Minimum</span><input className="input num" style={{ width: 80 }} value={minimum} onChange={(e) => setMinimum(e.target.value)} inputMode="decimal" /></label>
          <label style={{ display: "inline-flex", gap: 6, alignItems: "center", paddingBottom: 8 }}><input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} />paid in full each month</label>
          <button className="btn dark" type="button" disabled={busy} onClick={save}>Save terms</button>
          {d.utilisation !== null && d.utilisation !== undefined && <span style={{ color: d.utilisation > 0.3 ? "var(--critical)" : "var(--safe-ink)", fontWeight: 700 }}>{(d.utilisation * 100).toFixed(0)}% of the line in use</span>}
        </div>
      </td>
    </tr>
  );
}

interface Mountain {
  as_of: string;
  total: number;
  items: DebtItem[];
  minimums_month: number;
  interest_month: number;
  order: { avalanche: string[]; snowball: string[] };
  not_advice: string;
}

const KIND_TONE: Record<string, string> = { card: "var(--debt)", loan: "var(--debt-ink)", bnpl: "var(--bill)", advance: "var(--warning)", medical: "var(--paycheck)" };
const KIND_WORD: Record<string, string> = { card: "card", loan: "loan", bnpl: "installment plan", advance: "advance", medical: "medical" };

function MountainChart({ items, total }: { items: DebtItem[]; total: number }) {
  const w = 640;
  const h = 150;
  const base = h - 24;
  let x = 0;
  const gap = 2;
  const usable = w - gap * Math.max(0, items.length - 1);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" role="img" aria-label="Debts as slices of the whole" style={{ display: "block", maxWidth: 720 }}>
      {items.map((d) => {
        const width = Math.max(2, ((d.balance ?? 0) / Math.max(1, total)) * usable);
        const height = 30 + 90 * ((d.balance ?? 0) / Math.max(1, total));
        const left = x;
        x += width + gap;
        const showLabel = width > 54;
        return (
          <g key={d.source}>
            <title>{`${d.name}: ${money(d.balance ?? 0)}${d.apr ? `, ${d.apr}% APR` : ""}, ${(d.share * 100).toFixed(0)}% of the whole`}</title>
            <rect x={left} y={base - height} width={width} height={height} rx={4} fill={KIND_TONE[d.kind] ?? "var(--muted)"} />
            {showLabel && <text x={left + width / 2} y={base - height - 6} textAnchor="middle" fontSize={11} fontWeight={700} fill="var(--ink-2)">{d.name.length > Math.floor(width / 7) ? d.name.slice(0, Math.max(3, Math.floor(width / 7) - 1)) + "…" : d.name}</text>}
            {showLabel && <text x={left + width / 2} y={h - 6} textAnchor="middle" fontSize={11} fill="var(--muted)" className="num">{money(d.balance ?? 0)}</text>}
          </g>
        );
      })}
      <line x1={0} x2={w} y1={base} y2={base} stroke="var(--ink-2)" strokeWidth={1} />
    </svg>
  );
}

export function Debts() {
  const { data, error, reload } = useApi<Mountain>("/api/v1/debts");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 980 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Debts</h1>
      {error && <div style={{ color: "var(--critical)" }}>{error.message}</div>}
      {data && (
        <>
          <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "flex", gap: 22, flexWrap: "wrap", alignItems: "baseline" }}>
              <div><div className="lab">Owed in all</div><div className="num" style={{ fontSize: 34, fontWeight: 800, letterSpacing: "-0.02em", lineHeight: 1.1 }}>{money(data.total)}</div></div>
              <div><div className="lab">Minimums each month</div><div className="num" style={{ fontSize: 22, fontWeight: 800 }}>{money(data.minimums_month)}</div></div>
              <div><div className="lab">Interest this month costs</div><div className="num" style={{ fontSize: 22, fontWeight: 800, color: "var(--bill)" }}>{money(data.interest_month)}</div></div>
            </div>
            <MountainChart items={data.items} total={data.total} />
            <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12, color: "var(--ink-2)" }}>
              {Object.entries(KIND_WORD).filter(([k]) => data.items.some((d) => d.kind === k)).map(([k, word]) => (
                <span key={k} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span className="sw" style={{ background: KIND_TONE[k], margin: 0 }} />{word}</span>
              ))}
            </div>
          </section>

          {data.items.some((d) => d.kind === "bnpl") && (
            <section className="card" style={{ padding: 14, display: "flex", gap: 22, flexWrap: "wrap", alignItems: "baseline" }}>
              <div><div className="lab">Pay-later plans, still to pay</div><div className="num" style={{ fontSize: 22, fontWeight: 800 }}>{money(data.items.filter((d) => d.kind === "bnpl").reduce((t, d) => t + (d.balance ?? 0), 0))}</div></div>
              <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{data.items.filter((d) => d.kind === "bnpl").length} plans · the lender's own schedule where the confirmation email was read, else the payments left you entered · <Link to="/borrowing#loans">see each loan</Link></div>
            </section>
          )}
          <section className="card" style={{ padding: "6px 14px", overflowX: "auto" }}>
            <table className="ledger">
              <thead><tr><th>Debt</th><th>Kind</th><th style={{ textAlign: "right" }}>Balance</th><th style={{ textAlign: "right" }}>APR</th><th style={{ textAlign: "right" }}>Minimum</th><th style={{ textAlign: "right" }}>Share</th></tr></thead>
              <tbody>
                {data.items.map((d) => (
                  <React.Fragment key={d.source}>
                    <tr>
                      <td><strong>{d.name}</strong>{d.credit_limit ? <div style={{ fontSize: 11, color: "var(--muted)" }}>line {money(d.credit_limit)}{d.statement_day ? ` · statement day ${d.statement_day}` : ""}{d.due_day ? ` · due day ${d.due_day}` : ""}</div> : null}{d.kind === "bnpl" ? <div style={{ fontSize: 11, color: "var(--muted)" }}>{d.ends ? `ends ${d.ends}` : ""}{d.finance_charge ? ` · ${money(d.finance_charge)} finance charge over its life` : " · 0% as far as the ledger shows"}</div> : null}</td>
                      <td style={{ color: "var(--ink-2)" }}>{KIND_WORD[d.kind] ?? d.kind}</td>
                      <td className="num" style={{ textAlign: "right" }}>{money(d.balance ?? 0)}</td>
                      <td className="num" style={{ textAlign: "right", color: "var(--ink-2)" }}>{d.apr !== null && d.apr !== undefined ? `${d.apr}%` : "—"}</td>
                      <td className="num" style={{ textAlign: "right", color: "var(--ink-2)" }}>{d.minimum ? money(d.minimum) : "—"}</td>
                      <td className="num" style={{ textAlign: "right", color: "var(--muted)" }}>{(d.share * 100).toFixed(0)}%</td>
                    </tr>
                    {d.source.startsWith("debt:") && (d.kind === "card" || d.kind === "loan") && <Terms key={`${d.source}-${d.apr}-${d.credit_limit}-${d.statement_day}-${d.due_day}-${d.minimum}-${String(d.pay_in_full)}`} d={d} onSaved={reload} />}
                  </React.Fragment>
                ))}
                {data.items.length === 0 && <tr><td colSpan={6} style={{ color: "var(--muted)" }}>No debts on record. Cards, loans, installment plans and advances appear here as they are confirmed.</td></tr>}
              </tbody>
            </table>
          </section>

          {(data.order.avalanche.length > 0 || data.order.snowball.length > 0) && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 14 }}>
              <section className="card" style={{ padding: 14 }}>
                <strong>Highest rate first</strong>
                <div style={{ fontSize: 12, color: "var(--ink-2)", marginBottom: 6 }}>Pays the least interest over time.</div>
                <ol style={{ margin: 0, paddingLeft: 20, fontSize: 13, lineHeight: 1.7 }}>{data.order.avalanche.map((n) => <li key={n}>{n}</li>)}</ol>
              </section>
              <section className="card" style={{ padding: 14 }}>
                <strong>Smallest balance first</strong>
                <div style={{ fontSize: 12, color: "var(--ink-2)", marginBottom: 6 }}>Clears whole debts sooner, which keeps people going.</div>
                <ol style={{ margin: 0, paddingLeft: 20, fontSize: 13, lineHeight: 1.7 }}>{data.order.snowball.map((n) => <li key={n}>{n}</li>)}</ol>
              </section>
            </div>
          )}
          <div style={{ fontSize: 12, color: "var(--muted)" }}>{data.not_advice} Installment plans count what is left to pay; advances count the amount with its fee.</div>
        </>
      )}
    </div>
  );
}
