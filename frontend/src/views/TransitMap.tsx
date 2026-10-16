// Transit map: bills as stations on three lines across four weeks, paydays as the interchanges.
import { useState } from "react";
import { Link } from "react-router-dom";
import { money, monthDay, parseIso, shortDate } from "../format";
import { palette } from "../looks";
import { getPrefs } from "../prefs";
import type { Event } from "../types";
import type { ViewProps } from "./index";
import { SafeExplain } from "../components/SafeExplain";
import { StatusPill } from "../components/Status";

const W = 1120;
const DAYS = 28;
const X0 = 60;
const X1 = 1060;
const LINES: { id: "utilities" | "housing" | "debt"; label: string; y: number }[] = [
  { id: "utilities", label: "UTILITIES", y: 140 },
  { id: "housing", label: "HOUSING", y: 220 },
  { id: "debt", label: "DEBT", y: 300 },
];

export function TransitMap({ snapshot }: ViewProps) {
  const c = palette(getPrefs().look);
  const [list, setList] = useState(false);
  const lineColor = { utilities: c.ink2, housing: c.bill, debt: c.debt };
  const lineName = (id: string) => { const l = LINES.find((x) => x.id === id) ?? LINES[0]; return l.label[0] + l.label.slice(1).toLowerCase(); };
  const start = parseIso(snapshot.as_of);
  start.setDate(start.getDate() - 2);
  const dayOf = (iso: string) => Math.round((parseIso(iso).getTime() - start.getTime()) / 86400000);
  const x = (day: number) => X0 + (day * (X1 - X0)) / DAYS;
  const events: Event[] = snapshot.calendar.events.filter((e) => dayOf(e.date) >= 0 && dayOf(e.date) <= DAYS);
  const paydays = events.filter((e) => e.kind === "paycheck");
  const stations = events.filter((e) => e.kind !== "paycheck");
  const byLine = (id: string) => stations.filter((e) => (e.line ?? "utilities") === id);
  const next = stations.find((e) => dayOf(e.date) >= dayOf(snapshot.as_of));
  const biggest = [...stations].sort((a, b) => b.amount - a.amount)[0];
  const short = snapshot.safe_to_spend.amount < 0;
  const ticks = [0, 7, 14, 21, 28].map((d) => {
    const dt = new Date(start);
    dt.setDate(start.getDate() + d);
    return { d, label: `${monthDay(`${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`)}` };
  });
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 16 }}>
        <h2 style={{ margin: 0, fontSize: 22, fontWeight: 800, letterSpacing: "-0.01em" }}>The next four weeks, as a map</h2>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Each bill is a station on its line. Paydays are the interchanges where every line gets money.</div>
        <div style={{ flexGrow: 1 }} />
        <div style={{ display: "flex", gap: 16, fontSize: 13, fontWeight: 700, flexWrap: "wrap" }}>
          <span><span style={{ display: "inline-block", width: 28, height: 6, borderRadius: 3, background: c.paycheck, marginRight: 8, verticalAlign: 2 }} />Payday</span>
          {LINES.map((l) => <span key={l.id}><span style={{ display: "inline-block", width: 28, height: 6, borderRadius: 3, background: lineColor[l.id], marginRight: 8, verticalAlign: 2 }} />{lineName(l.id)}</span>)}
        </div>
      </div>
      <section className="card" style={{ padding: "12px 16px 6px", display: "flex", flexDirection: "column", gap: 8 }}>
        {/* Safe to spend sits above the map, never on it, so it cannot cover a payday's label. */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span className="lab">Safe to spend</span>
          <strong className="num" style={{ fontSize: 20, color: short ? "var(--critical-ink)" : undefined }}>{money(snapshot.safe_to_spend.amount)}</strong>
          {short && <StatusPill status="crit">Short before payday</StatusPill>}
          <button type="button" className="btn" aria-pressed={list} onClick={() => setList((v) => !v)} style={{ marginLeft: "auto", minHeight: 32, padding: "4px 10px" }}>{list ? "Show the map" : "Show as a list"}</button>
        </div>
        {list ? (
          <div style={{ overflowX: "auto" }}>
            <table className="ledger">
              <thead><tr><th>When</th><th>What</th><th>Line</th><th style={{ textAlign: "right" }}>Amount</th><th>How it is paid</th></tr></thead>
              <tbody>
                {events.map((e, i) => (
                  <tr key={`${e.source}-${e.date}-${i}`}>
                    <td style={{ whiteSpace: "nowrap" }}>{shortDate(e.date)}</td>
                    <td>{e.kind === "paycheck" ? "Payday" : e.name}</td>
                    <td style={{ color: "var(--ink-2)" }}>{e.kind === "paycheck" ? "every line" : lineName(e.line ?? "utilities")}</td>
                    <td className="num" style={{ textAlign: "right" }}>{e.kind === "paycheck" ? money(-e.amount, { sign: true }) : money(e.amount)}</td>
                    <td style={{ color: "var(--ink-2)" }}>{e.kind === "paycheck" ? "" : `${e.autopay ? "autopay" : "pay it yourself"}${e.funded === false ? " · not yet funded" : ""}`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <svg viewBox={`0 0 ${W} 440`} width="100%" style={{ display: "block", height: "auto", minWidth: 720 }} role="img" aria-label="Bills as stations on three lines across four weeks, with paydays as interchanges">
              {ticks.map((t) => (
                <g key={t.d}>
                  {t.d > 0 && t.d < DAYS && <line x1={x(t.d)} y1={40} x2={x(t.d)} y2={400} stroke={c.hairline} strokeWidth={1} />}
                  <text x={x(t.d)} y={420} textAnchor="middle" fontSize={11} fontWeight={700} fill={c.muted}>{t.label}</text>
                </g>
              ))}
              <line x1={x(dayOf(snapshot.as_of))} y1={30} x2={x(dayOf(snapshot.as_of))} y2={400} stroke={c.ink} strokeWidth={2} />
              <text x={x(dayOf(snapshot.as_of))} y={22} textAnchor="middle" fontSize={12} fontWeight={800} fill={c.ink}>You are here · {shortDate(snapshot.as_of)}</text>
              {paydays.map((p) => (
                <g key={p.date}>
                  <rect x={x(dayOf(p.date)) - 12} y={80} width={24} height={240} rx={12} fill={c.paycheck} />
                  <text x={x(dayOf(p.date))} y={66} textAnchor={x(dayOf(p.date)) > W - 90 ? "end" : "middle"} fontSize={12} fontWeight={800} fill={c.paycheck}>PAYDAY {shortDate(p.date)}</text>
                  <text x={x(dayOf(p.date))} y={346} textAnchor="middle" fontSize={12} fontWeight={800} fill={c.paycheck}>{money(-p.amount, { sign: true })}</text>
                </g>
              ))}
              {LINES.map((l) => (
                <g key={l.id}>
                  <line x1={X0} y1={l.y} x2={X1} y2={l.y} stroke={lineColor[l.id]} strokeWidth={8} strokeLinecap="round" />
                  <text x={X0} y={l.y - 14} fontSize={11} fontWeight={800} fill={lineColor[l.id]}>{l.label}</text>
                  {paydays.map((p) => <circle key={p.date} cx={x(dayOf(p.date))} cy={l.y} r={9} fill={c.surface} stroke={c.paycheck} strokeWidth={4} />)}
                  {byLine(l.id).map((e, i) => {
                    const big = e.amount >= 400;
                    const above = i % 2 === 0;
                    return (
                      <g key={`${e.source}-${e.date}`}>
                        {e.kind === "advance" ? (
                          <rect x={x(dayOf(e.date)) - 8} y={l.y - 8} width={16} height={16} rx={3} transform={`rotate(45 ${x(dayOf(e.date))} ${l.y})`} fill={c.surface} stroke={lineColor[l.id]} strokeWidth={3.5} />
                        ) : (
                          <circle cx={x(dayOf(e.date))} cy={l.y} r={big ? 10 : 7} fill={c.surface} stroke={lineColor[l.id]} strokeWidth={3.5} />
                        )}
                        <text x={x(dayOf(e.date))} y={above ? l.y - 24 : l.y + 30} textAnchor="middle" fontSize={11} fontWeight={big ? 800 : 700} fill={c.ink}>{e.name.length > 18 ? `${e.name.slice(0, 17)}…` : e.name} {money(e.amount)}</text>
                      </g>
                    );
                  })}
                </g>
              ))}
            </svg>
          </div>
        )}
        <SafeExplain s={snapshot} />
      </section>
      <section style={{ display: "flex", flexWrap: "wrap", gap: 14 }}>
        {next && (
          <div className="card" style={{ flex: "1 1 300px", padding: "12px 16px", display: "flex", gap: 12, alignItems: "center" }}>
            <svg width="34" height="34" viewBox="0 0 34 34" aria-hidden="true" style={{ flexShrink: 0 }}><circle cx="17" cy="17" r="14" fill={c.surface} stroke={lineColor[next.line ?? "utilities"]} strokeWidth="4" /></svg>
            <div><h3 className="lab" style={{ margin: 0 }}>Next stop</h3><strong>{next.name} {money(next.amount)} · {shortDate(next.date)}</strong><div style={{ fontSize: 12, color: "var(--ink-2)" }}>{next.autopay ? "Autopay." : "Pay it yourself."} {next.funded === false ? "Not yet funded." : "Funded."}</div></div>
          </div>
        )}
        {biggest && (
          <div className="card" style={{ flex: "1 1 300px", padding: "12px 16px", display: "flex", gap: 12, alignItems: "center" }}>
            <svg width="34" height="34" viewBox="0 0 34 34" aria-hidden="true" style={{ flexShrink: 0 }}><circle cx="17" cy="17" r="14" fill={c.surface} stroke={lineColor[biggest.line ?? "utilities"]} strokeWidth="4" /></svg>
            <div><h3 className="lab" style={{ margin: 0 }}>Biggest station</h3><strong>{biggest.name} {money(biggest.amount)} · {shortDate(biggest.date)}</strong><div style={{ fontSize: 12, color: "var(--ink-2)" }}><Link to="/bills">Open Bills</Link> to see whether its date could move.</div></div>
          </div>
        )}
        {paydays[0] && (
          <div className="card" style={{ flex: "1 1 300px", padding: "12px 16px", display: "flex", gap: 12, alignItems: "center" }}>
            <svg width="34" height="34" viewBox="0 0 34 34" aria-hidden="true" style={{ flexShrink: 0 }}><circle cx="17" cy="17" r="14" fill={c.surface} stroke={c.paycheck} strokeWidth="4" /></svg>
            <div><h3 className="lab" style={{ margin: 0 }}>Interchange</h3><strong>{shortDate(paydays[0].date)} · {money(-paydays[0].amount, { sign: true })}</strong><div style={{ fontSize: 12, color: "var(--ink-2)" }}>{snapshot.advances.total > 0 ? `Advance apps take ${money(snapshot.advances.total)} back as it lands.` : "No advance apps take from it."}</div></div>
          </div>
        )}
      </section>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{snapshot.not_advice}</div>
    </div>
  );
}
