// Reports: spending by month beside income, the categories with this month against the usual, the top
// merchants. Every number is the engine's; the charts draw them and add nothing.
import { useState } from "react";
import { useApi } from "../api";
import { money } from "../format";

interface Spending {
  as_of: string;
  from: string;
  by: "category" | "merchant";
  months: { month: string; spent: number; income: number; count: number; partial: boolean }[];
  groups: { name: string; total: number; count: number; this_month: number; average: number; share: number }[];
  top_merchants: { name: string; total: number; count: number }[];
  totals: { spent: number; income: number; savings_rate: number | null; months: number; full_months: number };
  not_advice: string;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function monthName(key: string): string {
  return MONTHS[Number(key.slice(5, 7)) - 1];
}

function MonthBars({ months }: { months: Spending["months"] }) {
  const w = 640;
  const h = 200;
  const pad = { l: 8, r: 8, t: 26, b: 26 };
  const max = Math.max(1, ...months.map((m) => Math.max(m.spent, m.income)));
  const slot = (w - pad.l - pad.r) / months.length;
  const bar = Math.min(28, slot * 0.32);
  const y = (v: number) => pad.t + (h - pad.t - pad.b) * (1 - v / max);
  const last = months.length - 1;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" role="img" aria-label="Spent and income by month" style={{ display: "block", maxWidth: 720 }}>
      {[0.5, 1].map((f) => <line key={f} x1={pad.l} x2={w - pad.r} y1={y(max * f)} y2={y(max * f)} stroke="var(--hairline)" strokeWidth={1} />)}
      {months.map((m, i) => {
        const cx = pad.l + slot * i + slot / 2;
        return (
          <g key={m.month}>
            <title>{`${monthName(m.month)}: spent ${money(m.spent)}, income ${money(m.income)}${m.partial ? " (so far)" : ""}`}</title>
            <rect x={cx - bar - 1} y={y(m.income)} width={bar} height={Math.max(0, y(0) - y(m.income))} rx={4} fill="var(--paycheck)" opacity={m.partial ? 0.55 : 1} />
            <rect x={cx + 1} y={y(m.spent)} width={bar} height={Math.max(0, y(0) - y(m.spent))} rx={4} fill="var(--bill)" opacity={m.partial ? 0.55 : 1} />
            {i === last && (
              <>
                <text x={cx - 3} y={y(m.income) - 6} textAnchor="end" fontSize={11} fontWeight={700} fill="var(--paycheck-ink)" className="num">{money(m.income)}</text>
                <text x={cx + 3} y={y(m.spent) - 6} textAnchor="start" fontSize={11} fontWeight={700} fill="var(--bill-ink)" className="num">{money(m.spent)}</text>
              </>
            )}
            <text x={cx} y={h - 8} textAnchor="middle" fontSize={11} fill="var(--muted)">{monthName(m.month)}{m.partial ? "·" : ""}</text>
          </g>
        );
      })}
      <line x1={pad.l} x2={w - pad.r} y1={y(0)} y2={y(0)} stroke="var(--ink-2)" strokeWidth={1} />
    </svg>
  );
}

export function Reports() {
  const [months, setMonths] = useState(6);
  const [by, setBy] = useState<"category" | "merchant">("category");
  const { data, error, loading } = useApi<Spending>(`/api/v1/reports/spending?months=${months}&by=${by}`);
  const groupMax = Math.max(1, ...(data?.groups.map((g) => Math.max(g.this_month, g.average)) ?? [1]));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 980 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Reports</h1>
        <div style={{ flexGrow: 1 }} />
        <select className="input" value={months} onChange={(e) => setMonths(Number(e.target.value))} aria-label="Months" style={{ minHeight: 36, padding: "4px 10px" }}>
          {[3, 6, 12, 24].map((n) => <option key={n} value={n}>{n} months</option>)}
        </select>
        <div style={{ display: "flex", gap: 4 }}>
          {(["category", "merchant"] as const).map((k) => <button key={k} type="button" className={`btn${by === k ? " dark" : ""}`} style={{ minHeight: 36, padding: "4px 12px" }} onClick={() => setBy(k)}>By {k}</button>)}
        </div>
      </div>
      {error && <div style={{ color: "var(--critical)" }}>{error.message}</div>}
      {loading && !data && <div style={{ color: "var(--muted)" }}>Computing…</div>}
      {data && (
        <>
          <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "baseline" }}>
              <strong style={{ fontSize: 15 }}>Spent and income, by month</strong>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--ink-2)" }}><span className="sw" style={{ background: "var(--paycheck)", margin: 0 }} />Income</span>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--ink-2)" }}><span className="sw" style={{ background: "var(--bill)", margin: 0 }} />Spent</span>
              <span style={{ fontSize: 12, color: "var(--muted)" }}>· the dotted month is still running</span>
            </div>
            <MonthBars months={data.months} />
            <div style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 13 }}>
              <span>Income <strong className="num">{money(data.totals.income)}</strong></span>
              <span>Spent <strong className="num">{money(data.totals.spent)}</strong></span>
              <span>Kept <strong className="num" style={{ color: data.totals.income - data.totals.spent < 0 ? "var(--critical)" : "var(--safe)" }}>{money(data.totals.income - data.totals.spent)}</strong>{data.totals.savings_rate !== null ? <span style={{ color: "var(--ink-2)" }}> · {(data.totals.savings_rate * 100).toFixed(0)}% of income</span> : null}</span>
            </div>
          </section>

          <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "baseline" }}>
              <strong style={{ fontSize: 15 }}>This month against the usual, by {by}</strong>
              <span style={{ fontSize: 12, color: "var(--muted)" }}>the bar is this month so far; the tick is the average of the {data.totals.full_months} full months</span>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {data.groups.slice(0, 14).map((g) => {
                const over = g.this_month > g.average && g.average > 0;
                return (
                  <div key={g.name} style={{ display: "grid", gridTemplateColumns: "minmax(120px, 180px) 1fr 90px 90px", gap: 10, alignItems: "center", fontSize: 13 }}>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={g.name}>{g.name}</span>
                    <div style={{ position: "relative", height: 14, background: "var(--surface-2)", borderRadius: 4 }} title={`${g.name}: ${money(g.this_month)} this month, ${money(g.average)} usual`}>
                      <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${(g.this_month / groupMax) * 100}%`, background: over ? "var(--bill)" : "var(--paycheck)", borderRadius: 4 }} />
                      <div style={{ position: "absolute", left: `${(g.average / groupMax) * 100}%`, top: -3, bottom: -3, width: 2, background: "var(--ink)", borderRadius: 1 }} />
                    </div>
                    <span className="num" style={{ textAlign: "right", fontWeight: 700 }}>{money(g.this_month)}</span>
                    <span className="num" style={{ textAlign: "right", color: "var(--muted)" }}>{money(g.average)}</span>
                  </div>
                );
              })}
            </div>
          </section>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 14 }}>
            <section className="card" style={{ padding: "6px 14px", overflowX: "auto" }}>
              <table className="ledger">
                <thead><tr><th>{by === "category" ? "Category" : "Merchant"}</th><th style={{ textAlign: "right" }}>Total</th><th style={{ textAlign: "right" }}>Share</th><th style={{ textAlign: "right" }}>Rows</th></tr></thead>
                <tbody>
                  {data.groups.map((g) => (
                    <tr key={g.name}><td>{g.name}</td><td className="num" style={{ textAlign: "right" }}>{money(g.total)}</td><td className="num" style={{ textAlign: "right", color: "var(--ink-2)" }}>{(g.share * 100).toFixed(0)}%</td><td className="num" style={{ textAlign: "right", color: "var(--muted)" }}>{g.count}</td></tr>
                  ))}
                </tbody>
              </table>
            </section>
            <section className="card" style={{ padding: "6px 14px", overflowX: "auto" }}>
              <table className="ledger">
                <thead><tr><th>Top merchants</th><th style={{ textAlign: "right" }}>Total</th><th style={{ textAlign: "right" }}>Visits</th></tr></thead>
                <tbody>
                  {data.top_merchants.map((m) => (
                    <tr key={m.name}><td>{m.name}</td><td className="num" style={{ textAlign: "right" }}>{money(m.total)}</td><td className="num" style={{ textAlign: "right", color: "var(--muted)" }}>{m.count}</td></tr>
                  ))}
                </tbody>
              </table>
            </section>
          </div>
          <div style={{ fontSize: 12, color: "var(--muted)" }}>{data.not_advice} Spending leaves out transfers, card payments, income and rows you ignored.</div>
        </>
      )}
    </div>
  );
}
