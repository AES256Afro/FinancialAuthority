// Cover: what each account needs before each of the next two paydays, against what it has. Three ways to show
// the same engine result: a gauge per account, the payday sheet to tick off, and needs-against-has columns.
// Every figure is the engine's; the pictures only draw them. The app never moves money; the sheet says what
// you would move.
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { money, monthDay, shortDate } from "../format";
import type { Cover, CoverAccount, SheetRow } from "../guide";
import { palette } from "../looks";
import { getPrefs } from "../prefs";
import { StatusPill } from "./Status";

export type CoverMode = "gauges" | "sheet" | "columns";
export const COVER_MODES: { id: CoverMode; name: string }[] = [
  { id: "gauges", name: "Gauges" },
  { id: "sheet", name: "Payday sheet" },
  { id: "columns", name: "Needs against has" },
];

function dayBefore(iso: string): string {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() - 1);
  return d.toISOString().slice(0, 10);
}

function Meter({ value, max, marks, tone, label }: { value: number; max: number; marks: { at: number; text: string }[]; tone: "fill" | "short" | "card"; label: string }) {
  const scale = Math.max(1, max);
  const fill = tone === "short" ? "var(--critical)" : tone === "card" ? "var(--debt)" : "var(--safe)";
  return (
    <div role="img" aria-label={label} style={{ position: "relative", height: 14, borderRadius: 7, background: "var(--surface-2)", marginTop: 18 }}>
      <div style={{ position: "absolute", inset: "0 auto 0 0", width: `${Math.min(100, Math.max(0, (value / scale) * 100))}%`, borderRadius: 7, background: fill }} />
      {marks.map((m) => {
        const pct = Math.min(100, Math.max(0, (m.at / scale) * 100));
        const right = pct > 55;
        return (
          <div key={m.text} style={{ position: "absolute", top: -4, bottom: -4, left: `${pct}%`, width: 2, background: "var(--ink)" }}>
            <span style={{ position: "absolute", top: -17, [right ? "right" : "left"]: -2, whiteSpace: "nowrap", fontSize: 10.5, fontWeight: 700, color: "var(--ink-2)" }}>{m.text}</span>
          </div>
        );
      })}
    </div>
  );
}

function SpendGauge({ a, paydays }: { a: CoverAccount; paydays: [string, string] }) {
  return (
    <>
      {(a.windows ?? []).map((w, k) => {
        const has = w.has_total;
        const label = k === 0 ? `Before ${monthDay(paydays[0])}` : `${monthDay(w.start)} to ${monthDay(w.end)}`;
        return (
          <div key={w.start} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <Meter value={has} max={Math.max(has, w.needs) * 1.05} marks={[{ at: w.needs, text: `needs ${money(w.needs)}` }]} tone={w.left < 0 ? "short" : "fill"} label={`${label}: has ${money(has)}, needs ${money(w.needs)}`} />
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap", fontSize: 12 }}>
              <span style={{ fontWeight: 700 }}>{label}</span>
              <span className="num" style={{ color: "var(--ink-2)" }}>
                has {money(w.has)}{w.inflow ? ` + ${money(w.inflow)} paycheck` : ""} · bills {money(w.bills)} · everyday {money(w.everyday)}{w.buffer ? ` · buffer ${money(w.buffer)}` : ""} · <strong style={{ color: w.left < 0 ? "var(--critical)" : "var(--ink)" }}>{w.left < 0 ? "short" : "over"} {money(Math.abs(w.left))}</strong>
              </span>
            </div>
          </div>
        );
      })}
    </>
  );
}

function CardGauge({ a }: { a: CoverAccount }) {
  const owed = a.balance ?? 0;
  if (a.limit) {
    const marks = [{ at: a.limit * 0.3, text: "30% of the line" }];
    if (a.statement_balance !== null && a.statement_balance !== undefined && a.statement_on) marks.push({ at: a.statement_balance, text: `${monthDay(a.statement_on)} statement ${money(a.statement_balance)}` });
    return (
      <>
        <Meter value={owed} max={a.limit} marks={marks} tone="card" label={`${money(owed)} owed of a ${money(a.limit)} line`} />
        <div style={{ fontSize: 12, color: "var(--ink-2)" }} className="num">
          {money(owed)} owed of {money(a.limit)} · {Math.round((a.utilisation ?? 0) * 100)}% used now{a.statement_utilisation !== null && a.statement_utilisation !== undefined ? ` · ${Math.round(a.statement_utilisation * 100)}% on the statement` : ""}
          {a.pay_by && (a.to_target ?? 0) > 0 ? ` · pay ${money(a.to_target ?? 0)} by ${monthDay(a.pay_by)}` : ""}
        </div>
      </>
    );
  }
  return (
    <div style={{ fontSize: 12, color: "var(--ink-2)", display: "flex", flexDirection: "column", gap: 2 }}>
      <span className="num">{money(owed)} owed · no line on file, so utilisation is unknown (add the line on Debts)</span>
      {(a.payments ?? []).slice(0, 2).map((p, i) => <span key={`${p.date}-${i}`} className="num">{money(p.amount)} on {shortDate(p.date)}</span>)}
    </div>
  );
}

function SaveGauge({ a }: { a: CoverAccount }) {
  const r = a.rung;
  if (!r) return <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Kept, not spent.</div>;
  return (
    <>
      <Meter value={a.balance ?? 0} max={r.target} marks={[{ at: r.target, text: `rung ${r.n} · ${money(r.target)}` }]} tone={(a.short ?? 0) > 0 && (a.balance ?? 0) <= 0 ? "short" : "fill"} label={`${money(a.balance ?? 0)} saved of ${money(r.target)}`} />
      <div style={{ fontSize: 12, color: "var(--ink-2)" }}><Link to="/saving">The ladder</Link> · {r.eta ? `reached ${shortDate(r.eta)} at the chosen share` : "not reached within six years at this share"}</div>
    </>
  );
}

function Gauges({ cover }: { cover: Cover }) {
  const shown = cover.accounts.filter((a) => a.role !== "other");
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 320px), 1fr))", gap: 12 }}>
      {shown.map((a) => (
        <div key={a.id} style={{ display: "flex", flexDirection: "column", gap: 8, padding: 12, border: "1px solid var(--hairline)", borderRadius: 12, background: "var(--surface)", minWidth: 0, overflow: "hidden" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
            <strong>{a.name}{a.deposit ? <span style={{ fontWeight: 500, color: "var(--muted)" }}> · paycheck lands here</span> : null}</strong>
            <StatusPill status={a.status} />
          </div>
          <div style={{ fontSize: 13 }}>{a.headline}</div>
          {a.role === "spend" && <SpendGauge a={a} paydays={cover.paydays} />}
          {a.role === "card" && <CardGauge a={a} />}
          {a.role === "save" && <SaveGauge a={a} />}
        </div>
      ))}
    </div>
  );
}

function Sheet({ rows, onTick }: { rows: SheetRow[]; onTick?: (row: SheetRow, done: boolean) => void }) {
  if (!rows.length) return <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Nothing to move before the next two paydays.</div>;
  return (
    <>
    <ul className="md:hidden" style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 8 }}>
      {rows.map((r) => (
        <li key={r.id} style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: 10, borderRadius: 10, background: "var(--surface-2)", opacity: r.done ? 0.55 : 1 }}>
          <input type="checkbox" checked={r.done} disabled={!onTick} onChange={(e) => onTick?.(r, e.target.checked)} aria-label={`Done: ${r.action}`} style={{ width: 22, height: 22, marginTop: 2, accentColor: "var(--safe)", flexShrink: 0 }} />
          <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0, flexGrow: 1 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
              <strong style={{ textDecoration: r.done ? "line-through" : "none", color: r.kind === "short" ? "var(--critical)" : "var(--ink)" }}>{r.action}</strong>
              <strong className="num" style={{ whiteSpace: "nowrap" }}>{money(r.amount, { cents: true })}</strong>
            </div>
            <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{shortDate(r.date)} · so that {r.so_that}</span>
          </div>
        </li>
      ))}
    </ul>
    <div className="hidden md:block" style={{ overflowX: "auto" }}>
      <table className="ledger">
        <thead><tr><th style={{ width: 36 }}><span className="sr-only">Done</span></th><th>When</th><th>Do</th><th style={{ textAlign: "right" }}>Amount</th><th>So that</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} style={{ opacity: r.done ? 0.55 : 1 }}>
              <td><input type="checkbox" checked={r.done} disabled={!onTick} onChange={(e) => onTick?.(r, e.target.checked)} aria-label={`Done: ${r.action}`} style={{ width: 18, height: 18, accentColor: "var(--safe)" }} /></td>
              <td className="num" style={{ whiteSpace: "nowrap" }}>{shortDate(r.date)}</td>
              <td style={{ textDecoration: r.done ? "line-through" : "none" }}>{r.kind === "short" ? <strong style={{ color: "var(--critical)" }}>{r.action}</strong> : r.action}</td>
              <td className="num" style={{ textAlign: "right", fontWeight: 800 }}>{money(r.amount, { cents: true })}</td>
              <td style={{ color: "var(--ink-2)" }}>{r.so_that}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
    </>
  );
}

function Columns({ cover }: { cover: Cover }) {
  const c = palette(getPrefs().look);
  const [table, setTable] = useState(false);
  const rows = cover.accounts
    .filter((a) => a.role === "spend" || a.role === "card")
    .map((a) => {
      if (a.role === "spend") return { name: a.name, w1: a.windows?.[0]?.needs ?? 0, w2: a.windows?.[1]?.needs ?? 0, has: (a.balance ?? 0) };
      return { name: a.name, w1: a.per_window?.[0] ?? 0, w2: a.per_window?.[1] ?? 0, has: null as number | null };
    });
  const series = [
    { key: "w1" as const, label: `needs before ${monthDay(cover.paydays[0])}`, fill: c.bill },
    { key: "w2" as const, label: `needs before ${monthDay(cover.paydays[1])}`, fill: c.debt },
    { key: "has" as const, label: "has now", fill: c.safe },
  ];
  const max = Math.max(1, ...rows.flatMap((r) => [r.w1, r.w2, r.has ?? 0]));
  const W = 640;
  const H = 230;
  const padL = 52;
  const padB = 40;
  const padT = 22;
  const gw = (W - padL - 8) / Math.max(1, rows.length);
  const bw = Math.min(34, gw / 4);
  const y = (v: number) => padT + (H - padT - padB) * (1 - v / max);
  const ticks = [0.5, 1].map((f) => Math.round((max * f) / 100) * 100).filter((v) => v > 0);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", fontSize: 12, color: "var(--ink-2)", alignItems: "center" }}>
        {series.map((s) => <span key={s.key}><span className="sw" style={{ background: s.fill }} />{s.label}</span>)}
        <button type="button" className="btn" style={{ marginLeft: "auto", minHeight: 30, padding: "4px 10px" }} onClick={() => setTable((t) => !t)}>{table ? "Show the chart" : "Show as a table"}</button>
      </div>
      {table ? (
        <table className="ledger">
          <thead><tr><th>Account</th>{series.map((s) => <th key={s.key} style={{ textAlign: "right" }}>{s.label}</th>)}</tr></thead>
          <tbody>{rows.map((r) => <tr key={r.name}><td>{r.name}</td><td className="num" style={{ textAlign: "right" }}>{money(r.w1)}</td><td className="num" style={{ textAlign: "right" }}>{money(r.w2)}</td><td className="num" style={{ textAlign: "right" }}>{r.has === null ? "—" : money(r.has)}</td></tr>)}</tbody>
        </table>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Needs against has, per account" style={{ display: "block", maxWidth: 820, height: "auto" }}>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={padL} x2={W - 8} y1={y(t)} y2={y(t)} stroke="var(--hairline)" />
                <text x={padL - 6} y={y(t) + 4} textAnchor="end" fontSize="10.5" fill="var(--muted)">{money(t)}</text>
              </g>
            ))}
            {rows.map((r, i) => {
              const x0 = padL + i * gw + gw / 2 - (bw * 3 + 8) / 2;
              return (
                <g key={r.name}>
                  {series.map((s, j) => {
                    const v = r[s.key];
                    const x = x0 + j * (bw + 4);
                    if (v === null || v <= 0) return <line key={s.key} x1={x} x2={x + bw} y1={y(0)} y2={y(0)} stroke="var(--muted)" strokeWidth={2}><title>{`${r.name}, ${s.label}: none`}</title></line>;
                    return (
                      <g key={s.key}>
                        <rect x={x} y={y(v)} width={bw} height={y(0) - y(v)} rx={4} fill={s.fill}><title>{`${r.name}, ${s.label}: ${money(v)}`}</title></rect>
                        <text x={x + bw / 2} y={y(v) - 4} textAnchor="middle" fontSize="10.5" fontWeight="700" fill="var(--ink)">{money(v)}</text>
                      </g>
                    );
                  })}
                  <text x={x0 + (bw * 3 + 8) / 2} y={H - 18} textAnchor="middle" fontSize="12" fontWeight="700" fill="var(--ink)">{r.name.length > 22 ? `${r.name.slice(0, 21)}…` : r.name}</text>
                </g>
              );
            })}
            <line x1={padL} x2={W - 8} y1={y(0)} y2={y(0)} stroke="var(--ink-2)" />
          </svg>
        </div>
      )}
    </div>
  );
}

export function CoverPanel({ cover, mode, onTicked }: { cover: Cover; mode: CoverMode; onTicked?: () => void }) {
  const [rows, setRows] = useState(cover.sheet);
  async function tick(row: SheetRow, done: boolean) {
    setRows((rs) => rs.map((r) => (r.id === row.id ? { ...r, done } : r)));
    try {
      await api("/api/v1/cover/sheet", { method: "PUT", body: JSON.stringify({ id: row.id, done }) });
      onTicked?.();
    } catch {
      setRows((rs) => rs.map((r) => (r.id === row.id ? { ...r, done: !done } : r)));
    }
  }
  if (mode === "sheet") return <Sheet rows={rows} onTick={tick} />;
  if (mode === "columns") return <Columns cover={cover} />;
  return <Gauges cover={cover} />;
}

export function coverSummary(cover: Cover): string {
  const p1 = cover.paydays[0];
  const short = cover.accounts.filter((a) => a.status === "crit").length;
  const watch = cover.accounts.filter((a) => a.status === "warn").length;
  if (short) return `${short} account${short === 1 ? "" : "s"} short before payday`;
  if (watch) return `${watch} account${watch === 1 ? "" : "s"} to watch before ${monthDay(dayBefore(cover.paydays[1]))}`;
  return `Every account covered through ${monthDay(dayBefore(cover.paydays[1]))} · next payday ${monthDay(p1)}`;
}
