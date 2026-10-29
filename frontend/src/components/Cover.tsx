// Cover: what each account needs before each of the next two paydays, against what it has. Three ways to show
// the same engine result: a gauge per account, the payday sheet to tick off, and needs-against-has columns.
// Every figure is the engine's; the pictures only draw them. The app never moves money; the sheet says what
// you would move.
import { OctagonAlert, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { amountClass, isoDay, money, shortDate } from "../format";
import type { Cover, CoverAccount, SheetRow } from "../guide";
import { palette } from "../looks";
import { getPrefs } from "../prefs";
import { Said } from "./State";
import { StatusPill } from "./Status";

export type CoverMode = "gauges" | "sheet" | "columns";
export const COVER_MODES: { id: CoverMode; name: string }[] = [
  { id: "gauges", name: "Gauges" },
  { id: "sheet", name: "Payday sheet" },
  { id: "columns", name: "Needs against has" },
];

// The day before a date, as a local date (never the UTC day, which is a day off far from UTC).
function dayBefore(iso: string): string {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() - 1);
  return isoDay(d);
}

// A mark is a line across the bar; its words sit in a key under the bar, so two close marks never overlap and
// a long one is never cut off. The first mark is a solid line, the second a dashed one.
function Meter({ value, max, marks, tone, label }: { value: number; max: number; marks: { at: number; text: string }[]; tone: "fill" | "short" | "watch" | "card"; label: string }) {
  const scale = Math.max(1, max);
  const fill = tone === "short" ? "var(--critical)" : tone === "watch" ? "var(--warning)" : tone === "card" ? "var(--debt)" : "var(--safe)";
  const stroke = (k: number) => `2px ${k === 0 ? "solid" : "dashed"} var(--ink)`;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div role="img" aria-label={label} style={{ position: "relative", height: 14, borderRadius: 7, background: "var(--surface-2)", margin: "4px 0" }}>
        <div style={{ position: "absolute", inset: "0 auto 0 0", width: `${Math.min(100, Math.max(0, (value / scale) * 100))}%`, borderRadius: 7, background: fill }} />
        {marks.map((m, k) => (
          <div key={m.text} style={{ position: "absolute", top: -4, bottom: -4, left: `calc(${Math.min(100, Math.max(0, (m.at / scale) * 100))}% - 1px)`, width: 0, borderLeft: stroke(k) }} />
        ))}
      </div>
      {marks.length > 0 && (
        <div aria-hidden="true" style={{ display: "flex", flexWrap: "wrap", gap: "2px 12px", fontSize: 11, fontWeight: 700, color: "var(--ink-2)" }}>
          {marks.map((m, k) => (
            <span key={m.text} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
              <span style={{ display: "inline-block", height: 12, borderLeft: stroke(k) }} /><span className={amountClass(m.text)}>{m.text}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function SpendGauge({ a, paydays }: { a: CoverAccount; paydays: [string, string] }) {
  if (a.no_balance) {
    return <div style={{ fontSize: 12, color: "var(--ink-2)" }}>What it holds is not known yet, so nothing is weighed against it. <Link to="/settings/connectors#banks" style={{ fontWeight: 700 }}>Link your bank</Link></div>;
  }
  return (
    <>
      {(a.windows ?? []).map((w, k) => {
        if (w.start > w.end) return null; // on payday the stretch before it is empty
        const has = w.has_total;
        const left = w.left;
        if (has === null || left === null || w.has === null) return null; // an older server's account with no balance
        const label = k === 0 ? `Before ${shortDate(paydays[0])}` : `${shortDate(w.start)} to ${shortDate(w.end)}`;
        const short = left < 0;
        const aside = w.set_aside ?? 0;
        // Short of the set-aside only: the window's own bills are paid, the window after starts with less.
        const asideOnly = short && aside > 0 && left + aside >= 0;
        return (
          <div key={`${k}-${w.start}`} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span style={{ fontWeight: 700, fontSize: 12 }}>{label}</span>
            <Meter value={has} max={Math.max(has, w.needs) * 1.05} marks={[{ at: w.needs, text: `needs ${money(w.needs)}` }]} tone={asideOnly ? "watch" : short ? "short" : "fill"} label={`${label}: has ${money(has)}, needs ${money(w.needs)}`} />
            <span className="num" style={{ fontSize: 12, color: "var(--ink-2)" }}>
              Has {money(w.has)}{w.inflow ? ` (+${money(w.inflow)} paycheck)` : ""} · Needs: bills {money(w.bills)}, everyday {money(w.everyday)}{w.buffer ? `, cushion ${money(w.buffer)}` : ""}{aside > 0 ? `, set aside ${money(aside)} for the ${shortDate(paydays[1])} window` : ""} ·{" "}
              <strong style={{ color: asideOnly ? "var(--warning-ink)" : short ? "var(--critical-ink)" : "var(--ink)", whiteSpace: "nowrap" }}>
                {short ? <>{asideOnly ? <TriangleAlert size={13} aria-hidden="true" style={{ verticalAlign: -2, marginRight: 3 }} /> : <OctagonAlert size={13} aria-hidden="true" style={{ verticalAlign: -2, marginRight: 3 }} />}{money(Math.abs(left))} short{asideOnly ? " of the set-aside" : ""}</> : `${money(left)} to spare`}
              </strong>
            </span>
          </div>
        );
      })}
    </>
  );
}

function CardGauge({ a }: { a: CoverAccount }) {
  const owed = a.balance ?? 0;
  if (a.limit) {
    const marks = a.target_amount ? [{ at: a.target_amount, text: "30% of your limit" }] : [];
    if (a.statement_balance !== null && a.statement_balance !== undefined && a.statement_on) marks.push({ at: a.statement_balance, text: `${shortDate(a.statement_on)} statement ${money(a.statement_balance)}` });
    return (
      <>
        <Meter value={owed} max={a.limit} marks={marks} tone="card" label={`${money(owed)} owed of a ${money(a.limit)} credit limit`} />
        <div style={{ fontSize: 12, color: "var(--ink-2)" }} className="num">
          {money(owed)} owed of a {money(a.limit)} credit limit · {Math.round((a.utilisation ?? 0) * 100)}% of your limit used now{a.statement_utilisation !== null && a.statement_utilisation !== undefined ? ` · ${Math.round(a.statement_utilisation * 100)}% on the statement` : ""}
          {a.pay_by && (a.to_target ?? 0) > 0 ? ` · pay ${money(a.to_target ?? 0)} by ${shortDate(a.pay_by)}` : ""}
        </div>
      </>
    );
  }
  return (
    <div style={{ fontSize: 12, color: "var(--ink-2)", display: "flex", flexDirection: "column", gap: 2 }}>
      <span className="num">{money(owed)} owed.</span>
      <span>No credit limit saved, so the share used is unknown. <Link to="/debts">Add the limit on Debts</Link></span>
      {(a.payments ?? []).slice(0, 2).map((p, i) => <span key={`${p.date}-${i}`} className="num">Card payment {money(p.amount)} on {shortDate(p.date)}</span>)}
    </div>
  );
}

function SaveGauge({ a }: { a: CoverAccount }) {
  const r = a.rung;
  if (!r) return <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Savings, not counted as spending money.</div>;
  return (
    <>
      <Meter value={a.balance ?? 0} max={r.target} marks={[{ at: r.target, text: `rung ${r.n} · ${money(r.target)}` }]} tone={(a.short ?? 0) > 0 && (a.balance ?? 0) <= 0 ? "short" : "fill"} label={`${money(a.balance ?? 0)} saved of ${money(r.target)}`} />
      <div style={{ fontSize: 12, color: "var(--ink-2)" }}><Link to="/saving">Open Saving</Link> · <Link to="/glossary#rung">rung</Link> {r.n} {r.eta ? `reached ${shortDate(r.eta)} at the chosen share` : "not reached within six years at this share"}</div>
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
          <div className={amountClass(a.headline)} style={{ fontSize: 13 }}>{a.headline}</div>
          {a.role === "spend" && <SpendGauge a={a} paydays={cover.paydays} />}
          {a.role === "card" && <CardGauge a={a} />}
          {a.role === "save" && <SaveGauge a={a} />}
        </div>
      ))}
    </div>
  );
}

function ShortWord() {
  return <><OctagonAlert size={14} aria-hidden="true" style={{ verticalAlign: -2, marginRight: 4 }} />Short: </>;
}

function Sheet({ rows, onTick }: { rows: SheetRow[]; onTick?: (row: SheetRow, done: boolean) => void }) {
  if (!rows.length) return <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Nothing to move before the next two paydays.</div>;
  return (
    <>
    <ul className="flex flex-col md:hidden" style={{ listStyle: "none", margin: 0, padding: 0, gap: 8 }}>
      {rows.map((r) => (
        <li key={r.id} style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: 10, borderRadius: 10, background: "var(--surface-2)", opacity: r.done ? 0.55 : 1 }}>
          <label style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", minWidth: 32, minHeight: 32, flexShrink: 0, cursor: onTick ? "pointer" : "default" }}>
            <input type="checkbox" checked={r.done} disabled={!onTick} onChange={(e) => onTick?.(r, e.target.checked)} aria-label={`Done: ${r.action}`} style={{ width: 22, height: 22, margin: 0, accentColor: "var(--safe)" }} />
          </label>
          <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0, flexGrow: 1 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
              <strong className={amountClass(r.action)} style={{ textDecoration: r.done ? "line-through" : "none", color: r.kind === "short" ? "var(--critical-ink)" : "var(--ink)" }}>{r.kind === "short" && <ShortWord />}{r.action}</strong>
              <strong className="num" style={{ whiteSpace: "nowrap" }}>{money(r.amount, { cents: true })}</strong>
            </div>
            <span className={amountClass(r.so_that)} style={{ fontSize: 12, color: "var(--ink-2)" }}>{shortDate(r.date)} · so that {r.so_that}</span>
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
              <td>
                <label style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", minWidth: 32, minHeight: 32, cursor: onTick ? "pointer" : "default" }}>
                  <input type="checkbox" checked={r.done} disabled={!onTick} onChange={(e) => onTick?.(r, e.target.checked)} aria-label={`Done: ${r.action}`} style={{ width: 18, height: 18, margin: 0, accentColor: "var(--safe)" }} />
                </label>
              </td>
              <td style={{ whiteSpace: "nowrap" }}>{shortDate(r.date)}</td>
              <td className={amountClass(r.action)} style={{ textDecoration: r.done ? "line-through" : "none" }}>{r.kind === "short" ? <strong style={{ color: "var(--critical-ink)" }}><ShortWord />{r.action}</strong> : r.action}</td>
              <td className="num" style={{ textAlign: "right", fontWeight: 800 }}>{money(r.amount, { cents: true })}</td>
              <td className={amountClass(r.so_that)} style={{ color: "var(--ink-2)" }}>{r.so_that}</td>
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
      if (a.role === "spend") return { name: a.name, w1: a.windows?.[0]?.needs ?? 0, w2: a.windows?.[1]?.needs ?? 0, has: a.no_balance ? null : (a.balance ?? 0), unknown: Boolean(a.no_balance) };
      return { name: a.name, w1: a.per_window?.[0] ?? 0, w2: a.per_window?.[1] ?? 0, has: null as number | null, unknown: false };
    });
  // Series colours of the look in their validated order (housing blue, bills orange, card violet); the status
  // colours are never used as a series.
  const series = [
    { key: "has" as const, label: "has now", fill: c.housing },
    { key: "w1" as const, label: `needs before ${shortDate(cover.paydays[0])}`, fill: c.bill },
    { key: "w2" as const, label: `needs before ${shortDate(cover.paydays[1])}`, fill: c.debt },
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
        <button type="button" className="btn" aria-pressed={table} style={{ marginLeft: "auto", minHeight: 32, padding: "4px 10px" }} onClick={() => setTable((t) => !t)}>{table ? "Show the chart" : "Show as a table"}</button>
      </div>
      {table ? (
        <div style={{ overflowX: "auto" }}>
          <table className="ledger">
            <thead><tr><th>Account</th>{series.map((s) => <th key={s.key} style={{ textAlign: "right" }}>{s.label}</th>)}</tr></thead>
            <tbody>{rows.map((r) => <tr key={r.name}><td>{r.name}</td><td className="num" style={{ textAlign: "right" }}>{r.has === null ? (r.unknown ? "no balance yet" : "—") : money(r.has)}</td><td className="num" style={{ textAlign: "right" }}>{money(r.w1)}</td><td className="num" style={{ textAlign: "right" }}>{money(r.w2)}</td></tr>)}</tbody>
          </table>
        </div>
      ) : (
        // On a phone the chart keeps a readable size and scrolls sideways inside its card.
        <div style={{ overflowX: "auto" }}>
          <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Needs against has, per account" style={{ display: "block", maxWidth: 820, minWidth: 480, height: "auto" }}>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={padL} x2={W - 8} y1={y(t)} y2={y(t)} stroke="var(--hairline)" />
                <text className="num" x={padL - 6} y={y(t) + 4} textAnchor="end" fontSize="10.5" fill="var(--muted)">{money(t)}</text>
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
                        <text className="num" x={x + bw / 2} y={y(v) - 4} textAnchor="middle" fontSize="10.5" fontWeight="700" fill="var(--ink)">{money(v)}</text>
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
  const [failed, setFailed] = useState(false);
  // A fresh answer from the server replaces the ticks shown here.
  useEffect(() => setRows(cover.sheet), [cover.sheet]);
  async function tick(row: SheetRow, done: boolean) {
    setFailed(false);
    setRows((rs) => rs.map((r) => (r.id === row.id ? { ...r, done } : r)));
    try {
      await api("/api/v1/cover/sheet", { method: "PUT", body: JSON.stringify({ id: row.id, done }) });
      onTicked?.();
    } catch {
      setRows((rs) => rs.map((r) => (r.id === row.id ? { ...r, done: !done } : r)));
      setFailed(true);
    }
  }
  if (coverUnknown(cover)) {
    return (
      <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
        Cover weighs what each account holds against what comes out of it, and no bank has told the app a balance yet; an imported file brings transactions only. <Link to="/settings/connectors#banks" style={{ fontWeight: 700 }}>Link your bank</Link>
      </div>
    );
  }
  if (mode === "sheet") {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {failed && <div><Said text="Couldn't save that tick. Try again." tone="error" /></div>}
        <Sheet rows={rows} onTick={tick} />
      </div>
    );
  }
  if (mode === "columns") return <Columns cover={cover} />;
  return <Gauges cover={cover} />;
}

/** Cover with nothing to measure: no account yet, or no account with a balance (an imported file brings none). The
 * engine then counts each account from $0, so its verdicts are not shown as fact. */
function coverUnknown(cover: Cover): string | null {
  if (cover.accounts.length === 0) return "No account yet: link your bank or import its file";
  // The engine marks each spending account it has no balance for; an older server is read from the balances instead.
  const spend = cover.accounts.filter((a) => a.role === "spend");
  if (spend.length > 0 && spend.every((a) => a.no_balance ?? a.balance === null)) return "No balance from your bank yet, so nothing to measure";
  if (cover.accounts.every((a) => a.balance === null)) return "No balance from your bank yet, so nothing to measure";
  return null;
}

/** The pill's tone beside coverSummary. */
export function coverTone(cover: Cover): "crit" | "warn" | "good" {
  if (coverUnknown(cover)) return "warn";
  return cover.counts.crit ? "crit" : cover.counts.warn ? "warn" : "good";
}

export function coverSummary(cover: Cover): string {
  const unknown = coverUnknown(cover);
  if (unknown) return unknown;
  const p1 = cover.paydays[0];
  // "crit" is a spending account short before payday, or a card with a minimum past due: each says its own words.
  const short = cover.accounts.filter((a) => a.status === "crit" && a.role !== "card").length;
  const pastDue = cover.accounts.filter((a) => a.status === "crit" && a.role === "card").length;
  const watch = cover.accounts.filter((a) => a.status === "warn").length;
  const shortWords = short ? `${short} account${short === 1 ? "" : "s"} short before payday` : "";
  const dueWords = pastDue ? `${pastDue} card${pastDue === 1 ? "" : "s"} past due` : "";
  if (short || pastDue) return [shortWords, dueWords].filter(Boolean).join(" · ");
  if (watch) return `${watch} account${watch === 1 ? "" : "s"} to watch before ${shortDate(dayBefore(cover.paydays[1]))}`;
  return `Every account covered through ${shortDate(dayBefore(cover.paydays[1]))} · next payday ${shortDate(p1)}`;
}
