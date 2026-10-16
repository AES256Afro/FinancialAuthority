// Month grid: the calendar with paydays, due dates and each week's low point.
import { Link } from "react-router-dom";
import { money, parseIso, shortDate } from "../format";
import type { Event } from "../types";
import type { ViewProps } from "./index";
import { SafeExplain } from "../components/SafeExplain";

const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function iso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function chipStyle(e: Event): React.CSSProperties {
  if (e.kind === "paycheck") return { background: "var(--paycheck)", color: "#ffffff" };
  if (e.kind === "installment" || e.kind === "advance") return { background: "var(--debt)", color: "#ffffff" };
  return { background: "var(--bill)", color: "#ffffff" };
}

export function MonthGrid({ snapshot }: ViewProps) {
  const weeks = snapshot.calendar.weeks.slice(0, 6);
  const byDate = new Map<string, Event[]>();
  for (const e of snapshot.calendar.events) byDate.set(e.date, [...(byDate.get(e.date) ?? []), e]);
  const today = snapshot.as_of;
  const firstBad = weeks.find((w) => w.low !== null && w.low < 0);
  const title = weeks.length ? `${MONTHS[parseIso(weeks[0].start).getMonth()]} to ${MONTHS[parseIso(weeks[weeks.length - 1].end).getMonth()]} ${parseIso(weeks[weeks.length - 1].end).getFullYear()}` : "";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 16 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, letterSpacing: "-0.01em" }}>{title}</h1>
        <div style={{ flexGrow: 1 }} />
        <div style={{ display: "flex", gap: 16, fontSize: 12, color: "var(--ink-2)", fontWeight: 600, flexWrap: "wrap" }}>
          <span><span className="sw" style={{ background: "var(--paycheck)" }} />Paycheck</span>
          <span><span className="sw" style={{ background: "var(--bill)" }} />Bill</span>
          <span><span className="sw" style={{ background: "var(--debt)" }} />Advance or installment</span>
          <span><span className="sw" style={{ background: "var(--surface-2)" }} />Past</span>
        </div>
      </div>
      <section style={{ overflowX: "auto" }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(min(120px, 100%), 1fr)) 110px", gap: 4, minWidth: 960 }} aria-label="Calendar with paydays and bills">
          {DOW.map((d) => <div key={d} className="lab" style={{ padding: "0 6px 4px" }}>{d}</div>)}
          <div className="lab" style={{ padding: "0 6px 4px" }}>Week low</div>
          {weeks.map((w) => {
            const start = parseIso(w.start);
            const cells = Array.from({ length: 7 }, (_, i) => {
              const d = new Date(start);
              d.setDate(start.getDate() + i);
              return iso(d);
            });
            const bad = w.low !== null && w.low < 0;
            return [
              ...cells.map((day) => {
                const past = day < today;
                const isToday = day === today;
                const events = byDate.get(day) ?? [];
                const payday = events.some((e) => e.kind === "paycheck");
                const dd = parseIso(day);
                const label = dd.getDate() === 1 || day === cells[0] && weeks[0].start === w.start ? `${MONTHS[dd.getMonth()]} ${dd.getDate()}` : String(dd.getDate());
                return (
                  <div key={day} style={{ minHeight: 96, borderRadius: 10, background: past ? "var(--surface-2)" : payday ? "var(--paycheck-soft)" : "var(--surface)", border: isToday ? "2px solid var(--ink)" : "1px solid var(--hairline)", padding: "6px 7px", boxSizing: "border-box", display: "flex", flexDirection: "column", gap: 3, opacity: past ? 0.6 : 1 }}>
                    <span style={{ fontSize: 12, fontWeight: 800 }}>{label}{isToday ? " · today" : ""}</span>
                    {events.map((e, i) => (
                      <span key={`${e.source}-${i}`} style={{ ...chipStyle(e), fontSize: 11, fontWeight: 700, borderRadius: 5, padding: "2px 6px", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={`${e.name} ${money(Math.abs(e.amount))}`}>
                        {e.kind === "paycheck" ? `Payday ${money(-e.amount, { sign: true })}` : `${e.name} ${money(e.amount)}`}
                      </span>
                    ))}
                  </div>
                );
              }),
              <div key={`low-${w.start}`} style={{ display: "flex", flexDirection: "column", justifyContent: "center", gap: 2, padding: "6px 8px", borderRadius: 10, background: bad ? "var(--warning-soft)" : "var(--surface-2)" }}>
                <span className="lab">low</span>
                <span className="num" style={{ fontSize: 18, fontWeight: 800 }}>{w.low === null ? "—" : money(w.low)}</span>
                <span style={{ fontSize: 11, color: bad ? "var(--warning-ink)" : "var(--muted)", fontWeight: bad ? 700 : 400 }}>{w.low_date ? (bad ? `${shortDate(w.low_date)}, under the buffer` : shortDate(w.low_date)) : ""}</span>
              </div>,
            ];
          })}
        </div>
      </section>
      <section style={{ display: "flex", flexWrap: "wrap", gap: 14, fontSize: 13 }}>
        <div className="card" style={{ flex: "1 1 360px", padding: "12px 16px", display: "flex", gap: 14, alignItems: "center" }}>
          <div className="num" style={{ fontSize: 30, fontWeight: 800, letterSpacing: "-0.02em" }}>{money(snapshot.safe_to_spend.amount)}</div>
          <div><strong>Safe to spend through {shortDate(snapshot.safe_to_spend.through)}.</strong><br /><span style={{ color: "var(--ink-2)" }}>Lows are at your usual pace of {money(snapshot.usual_pace.per_day)} a day.</span></div>
          <SafeExplain s={snapshot} />
        </div>
        {firstBad && (
          <div className="card" style={{ flex: "1 1 360px", padding: "12px 16px", display: "flex", gap: 14, alignItems: "center", borderColor: "var(--warning)" }}>
            <svg width="26" height="26" viewBox="0 0 26 26" role="img" aria-label="Warning"><path d="M13 3 L24 22 H2 Z" fill="var(--warning)" /><path d="M13 10v6M13 18.5v1" stroke="#141518" strokeWidth="2" strokeLinecap="round" /></svg>
            <div><strong>The week of {shortDate(firstBad.start)} dips {money(-(firstBad.low ?? 0))} under the buffer.</strong><br /><span style={{ color: "var(--ink-2)" }}>At your usual pace. The date mover (M5.5) will suggest what to shift; until then, </span><Link to="/bills">see the bills</Link>.</div>
          </div>
        )}
      </section>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{snapshot.not_advice}</div>
    </div>
  );
}
