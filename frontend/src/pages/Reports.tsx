// Reports: spending by month beside income, the categories with this month against the usual, the top
// merchants; then a report you build and save, groups of categories, the year in review and tax time.
// Every number is the engine's; the charts draw them and add nothing.
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../api";
import { ReportBuilder, TaxTime, YearInReview } from "../components/ReportBuilder";
import { Failed, Loading, useJumpToHash, useWidth } from "../components/State";
import { money } from "../format";

const JUMPS = [
  { id: "build", label: "Build a report" },
  { id: "groups", label: "Groups of categories" },
  { id: "year", label: "The year in review" },
  { id: "tax", label: "Tax time" },
];

interface Spending {
  as_of: string;
  from: string;
  by: "category" | "merchant";
  months: { month: string; spent: number; income: number; count: number; partial: boolean }[];
  groups: { name: string; total: number; count: number; this_month: number; average: number; share: number; over_usual: boolean }[];
  top_merchants: { name: string; total: number; count: number }[];
  totals: { spent: number; income: number; kept: number; savings_rate: number | null; months: number; full_months: number };
  not_advice: string;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function monthName(key: string): string {
  return MONTHS[Number(key.slice(5, 7)) - 1];
}

// The same months as the bars, as rows: readable on a phone and by a screen reader.
function MonthTable({ months }: { months: Spending["months"] }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="ledger">
        <thead><tr><th>Month</th><th style={{ textAlign: "right" }}>Income</th><th style={{ textAlign: "right" }}>Spent</th></tr></thead>
        <tbody>
          {months.map((m) => (
            <tr key={m.month}><td>{monthName(m.month)} {m.month.slice(0, 4)}{m.partial ? " (so far)" : ""}</td><td className="num" style={{ textAlign: "right" }}>{money(m.income)}</td><td className="num" style={{ textAlign: "right" }}>{money(m.spent)}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MonthBars({ months }: { months: Spending["months"] }) {
  // Drawn one unit per pixel of the box it sits in, so the month names and the figures read on a phone.
  const box = useRef<HTMLDivElement>(null);
  const w = Math.max(280, Math.min(720, useWidth(box, 640)));
  const h = 200;
  const pad = { l: 8, r: 8, t: 26, b: 26 };
  const max = Math.max(1, ...months.map((m) => Math.max(m.spent, m.income)));
  const slot = (w - pad.l - pad.r) / months.length;
  const bar = Math.min(28, slot * 0.32);
  const y = (v: number) => pad.t + (h - pad.t - pad.b) * (1 - v / max);
  const last = months.length - 1;
  return (
    <div ref={box} style={{ width: "100%", maxWidth: 720 }}>
    <svg viewBox={`0 0 ${w} ${h}`} width={w} height={h} role="img" aria-label="Spent and income by month" style={{ display: "block", maxWidth: "100%" }}>
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
    </div>
  );
}

export function Reports() {
  const [months, setMonths] = useState(6);
  const [by, setBy] = useState<"category" | "merchant">("category");
  const { data, error, reload, loading } = useApi<Spending>(`/api/v1/reports/spending?months=${months}&by=${by}`);
  // A new choice keeps the old figures on screen until the new report arrives; they are dimmed and say so.
  const stale = loading && data !== null;
  const [table, setTable] = useState(false);
  const groupMax = Math.max(1, ...(data?.groups.map((g) => Math.max(g.this_month, g.average)) ?? [1]));
  const jump = (id: string) => document.getElementById(id)?.scrollIntoView({ block: "start", behavior: "smooth" });
  useJumpToHash();
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 980 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Reports</h1>
        <div style={{ flexGrow: 1 }} />
        <label style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span className="lab">Show the last</span>
          <select className="input" value={months} onChange={(e) => setMonths(Number(e.target.value))} style={{ minHeight: 36, padding: "4px 10px" }}>
            {[3, 6, 12, 24].map((n) => <option key={n} value={n}>{n} months</option>)}
          </select>
        </label>
        <div role="group" aria-label="Group spending" style={{ display: "flex", gap: 4 }}>
          {(["category", "merchant"] as const).map((k) => <button key={k} type="button" aria-pressed={by === k} className={`btn${by === k ? " dark" : ""}`} style={{ minHeight: 36, padding: "4px 12px" }} onClick={() => setBy(k)}>By {k}</button>)}
        </div>
      </div>
      <nav aria-label="On this page" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", fontSize: 13 }}>
        <span className="lab">Jump to</span>
        {JUMPS.map((j) => (
          <Link key={j.id} to={{ hash: `#${j.id}` }} onClick={() => jump(j.id)} className="btn" style={{ minHeight: 32, padding: "4px 12px", textDecoration: "none", display: "inline-flex", alignItems: "center" }}>{j.label}</Link>
        ))}
      </nav>
      {error && !data && <Failed error={error} what="the report" onRetry={reload} />}
      {!data && !error && <Loading what="the report" />}
      {stale && <span role="status" style={{ fontSize: 13, color: "var(--ink-2)" }}>Loading the last {months} months by {by}… the figures below are the ones before.</span>}
      {error && data && !loading && <Failed error={error} what="the new report" onRetry={reload} />}
      {data && data.groups.length === 0 && data.totals.income === 0 && data.totals.spent === 0 && (
        <section className="card" style={{ padding: 16, fontSize: 13, color: "var(--ink-2)" }}>
          No spending or income in the last {months} months yet. Reports are drawn from your transactions: <Link to="/settings/connectors#import" style={{ fontWeight: 700 }}>import your bank's file</Link> or <Link to="/settings/connectors#banks" style={{ fontWeight: 700 }}>link the bank</Link>, and the months fill in here.
        </section>
      )}
      {data && !(data.groups.length === 0 && data.totals.income === 0 && data.totals.spent === 0) && (
        <div aria-busy={stale} style={{ display: "flex", flexDirection: "column", gap: 14, opacity: stale ? 0.5 : 1, transition: "opacity .15s" }}>
          <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "baseline" }}>
              <h2 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>Spent and income, by month</h2>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--ink-2)" }}><span className="sw" style={{ background: "var(--paycheck)", margin: 0 }} />Income</span>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--ink-2)" }}><span className="sw" style={{ background: "var(--bill)", margin: 0 }} />Spent</span>
              <span style={{ fontSize: 12, color: "var(--muted)" }}>· the paler bars are this month, still running</span>
              <button type="button" className="btn" aria-pressed={table} style={{ marginLeft: "auto", minHeight: 32, padding: "4px 10px" }} onClick={() => setTable((t) => !t)}>{table ? "Show the chart" : "Show as a table"}</button>
            </div>
            {table ? <MonthTable months={data.months} /> : <MonthBars months={data.months} />}
            <div style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 13 }}>
              <span>Income <strong className="num">{money(data.totals.income)}</strong></span>
              <span>Spent <strong className="num">{money(data.totals.spent)}</strong></span>
              <span>Kept <strong className="num" style={{ color: data.totals.kept < 0 ? "var(--critical-ink)" : "var(--safe-ink)" }}>{money(data.totals.kept)}</strong>{data.totals.savings_rate !== null ? <span style={{ color: "var(--ink-2)" }}> · {(data.totals.savings_rate * 100).toFixed(0)}% of income</span> : null}</span>
            </div>
          </section>

          <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "baseline" }}>
              <h2 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>This month against the usual, by {by}</h2>
              <span style={{ fontSize: 12, color: "var(--muted)" }}>the bar is this month so far; the black tick is the usual, the average of the {data.totals.full_months} full months</span>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <div aria-hidden="true" style={{ display: "grid", gridTemplateColumns: "minmax(0, 180px) minmax(40px, 1fr) 72px 72px", gap: 10, alignItems: "end" }}>
                <span /><span />
                <span className="lab" style={{ textAlign: "right" }}>This month</span>
                <span className="lab" style={{ textAlign: "right" }}>Usual</span>
              </div>
              {data.groups.slice(0, 14).map((g) => {
                const over = g.over_usual;
                return (
                  <div key={g.name} style={{ display: "grid", gridTemplateColumns: "minmax(0, 180px) minmax(40px, 1fr) 72px 72px", gap: 10, alignItems: "center", fontSize: 13 }}>
                    <span style={{ minWidth: 0, display: "flex", flexDirection: "column" }}>
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={g.name}>{g.name}</span>
                      {over && <span style={{ fontSize: 11, fontWeight: 800, color: "var(--bill-ink)" }}><span aria-hidden="true">▲ </span>over the usual</span>}
                    </span>
                    <div style={{ position: "relative", height: 14, background: "var(--surface-2)", borderRadius: 4 }} title={`${g.name}: ${money(g.this_month)} this month, ${money(g.average)} usual`}>
                      <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${(g.this_month / groupMax) * 100}%`, background: over ? "var(--bill)" : "var(--paycheck)", borderRadius: 4 }} />
                      <div style={{ position: "absolute", left: `${(g.average / groupMax) * 100}%`, top: -3, bottom: -3, width: 2, background: "var(--ink)", borderRadius: 1 }} />
                    </div>
                    <span className="num" style={{ textAlign: "right", fontWeight: 700 }}><span className="sr-only">This month </span>{money(g.this_month)}</span>
                    <span className="num" style={{ textAlign: "right", color: "var(--muted)" }}><span className="sr-only">Usual </span>{money(g.average)}</span>
                  </div>
                );
              })}
            </div>
          </section>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(300px, 100%), 1fr))", gap: 14 }}>
            <section className="card" style={{ padding: "6px 14px", overflowX: "auto" }}>
              <table className="ledger">
                <thead><tr><th>{by === "category" ? "Category" : "Merchant"}</th><th style={{ textAlign: "right" }}>Total</th><th style={{ textAlign: "right" }}>Share</th><th style={{ textAlign: "right" }}>Purchases</th></tr></thead>
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
        </div>
      )}

      <ReportBuilder />
      <YearInReview />
      <TaxTime />
    </div>
  );
}
