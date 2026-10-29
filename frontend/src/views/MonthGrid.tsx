// Month grid: the calendar with paydays, due dates and each week's lowest left to spend. On a phone the same weeks
// show as an agenda: each week's lowest left to spend, then each day that has something on it, then its items.
// "Left to spend" is the engine's at_pace figure: checking less the safety cushion, what is kept for bills and the
// usual everyday spending. It is not the bank balance, so it is never called one.
import { TriangleAlert } from "lucide-react";
import { Link } from "react-router-dom";
import { money, monthDay, parseIso, shortDate } from "../format";
import { palette } from "../looks";
import { inkOn } from "../paychecks";
import { getPrefs } from "../prefs";
import type { Event } from "../types";
import type { ViewProps } from "./index";
import { PlanCaveats, SafeExplain, SafePill, SafeSentence, noBalance, safeFigure, safeTitle } from "../components/SafeExplain";

const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function iso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function daysOf(start: string): string[] {
  const first = parseIso(start);
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date(first);
    d.setDate(first.getDate() + i);
    return iso(d);
  });
}

export function MonthGrid({ snapshot }: ViewProps) {
  const c = palette(getPrefs().look);
  // A chip's text is dark or white by its fill, checked per colour.
  const chipStyle = (e: Event): React.CSSProperties => {
    const fill = e.kind === "paycheck" ? c.paycheck : e.kind === "installment" || e.kind === "advance" ? c.debt : c.bill;
    return { background: fill, color: inkOn(fill) };
  };
  const chipText = (e: Event) => (e.kind === "paycheck" ? `Payday ${money(-e.amount, { sign: true })}` : `${e.name} ${money(e.amount)}${e.late && e.due ? ` · was due ${monthDay(e.due)}` : ""}`);
  const weeks = snapshot.calendar.weeks.slice(0, 6);
  const byDate = new Map<string, Event[]>();
  for (const e of snapshot.calendar.events) byDate.set(e.date, [...(byDate.get(e.date) ?? []), e]);
  const today = snapshot.as_of;
  // The weekly lows start from what checking holds: with no balance the engine leaves them null, said in words.
  const empty = noBalance(snapshot);
  const lowFigure = (w: (typeof weeks)[number]) => (w.low === null ? (empty ? "no balance yet" : "—") : money(w.low));
  const firstBad = weeks.find((w) => w.low !== null && w.low < 0);
  const title = weeks.length ? `${MONTHS[parseIso(weeks[0].start).getMonth()]} to ${MONTHS[parseIso(weeks[weeks.length - 1].end).getMonth()]} ${parseIso(weeks[weeks.length - 1].end).getFullYear()}` : "";
  const lowWords = (w: (typeof weeks)[number]) => {
    const bad = w.low !== null && w.low < 0;
    return w.low_date ? (bad ? `${shortDate(w.low_date)}, under your safety cushion` : shortDate(w.low_date)) : "";
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 16 }}>
        <h2 style={{ margin: 0, fontSize: 22, fontWeight: 800, letterSpacing: "-0.01em" }}>{title}</h2>
        <div style={{ flexGrow: 1 }} />
        <div style={{ display: "flex", gap: 16, fontSize: 12, color: "var(--ink-2)", fontWeight: 600, flexWrap: "wrap" }}>
          <span><span className="sw" style={{ background: c.paycheck }} />Paycheck</span>
          <span><span className="sw" style={{ background: c.bill }} />Bill</span>
          <span><span className="sw" style={{ background: c.debt }} />Advance or installment</span>
          <span className="hidden md:inline"><span className="sw" style={{ background: "var(--surface-2)" }} />Past</span>
        </div>
      </div>

      {/* Below the md breakpoint: an agenda, date first, then what happens that day. */}
      <section className="flex flex-col md:hidden" aria-label="Agenda with paydays and bills" style={{ gap: 10 }}>
        {weeks.map((w) => {
          const days = daysOf(w.start).filter((day) => day >= today && (byDate.get(day) ?? []).length > 0);
          const bad = w.low !== null && w.low < 0;
          if (w.end < today) return null;
          return (
            <div key={w.start} className="card" style={{ padding: "10px 14px", display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8, flexWrap: "wrap", padding: "6px 8px", borderRadius: 8, background: bad ? "var(--warning-soft)" : "var(--surface-2)" }}>
                <span className="lab">Week of {shortDate(w.start)}</span>
                <span style={{ fontSize: 12, color: bad ? "var(--warning-ink)" : "var(--ink-2)", fontWeight: bad ? 700 : 500 }}>
                  {bad && <TriangleAlert size={13} aria-hidden="true" style={{ verticalAlign: -2, marginRight: 4 }} />}
                  Lowest left to spend <strong className="num">{lowFigure(w)}</strong>{w.low_date ? ` · ${lowWords(w)}` : ""}
                </span>
              </div>
              {days.length === 0 && <div style={{ fontSize: 13, color: "var(--muted)" }}>Nothing due this week.</div>}
              {days.map((day) => (
                <div key={day} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  <strong style={{ fontSize: 13 }}>{shortDate(day)}{day === today ? " · today" : ""}</strong>
                  {(byDate.get(day) ?? []).map((e, i) => (
                    <span key={`${e.source}-${i}`} style={{ ...chipStyle(e), alignSelf: "flex-start", maxWidth: "100%", fontSize: 12, fontWeight: 700, borderRadius: 5, padding: "3px 8px", overflowWrap: "anywhere" }}><span className="num">{chipText(e)}</span></span>
                  ))}
                </div>
              ))}
            </div>
          );
        })}
      </section>

      <section className="hidden md:block" style={{ overflowX: "auto" }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(min(120px, 100%), 1fr)) 110px", gap: 4, minWidth: 960 }} aria-label="Calendar with paydays and bills">
          {DOW.map((d) => <div key={d} className="lab" style={{ padding: "0 6px 4px" }}>{d}</div>)}
          <div className="lab" style={{ padding: "0 6px 4px" }}>Lowest left to spend</div>
          {weeks.map((w) => {
            const cells = daysOf(w.start);
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
                        <span className="num">{chipText(e)}</span>
                      </span>
                    ))}
                  </div>
                );
              }),
              <div key={`low-${w.start}`} style={{ display: "flex", flexDirection: "column", justifyContent: "center", gap: 2, padding: "6px 8px", borderRadius: 10, background: bad ? "var(--warning-soft)" : "var(--surface-2)" }}>
                <span className="lab">lowest left</span>
                <span className="num" style={{ fontSize: 18, fontWeight: 800 }}>{lowFigure(w)}</span>
                <span style={{ fontSize: 11, color: bad ? "var(--warning-ink)" : "var(--muted)", fontWeight: bad ? 700 : 400 }}>{bad && <TriangleAlert size={12} aria-hidden="true" style={{ verticalAlign: -2, marginRight: 3 }} />}{lowWords(w)}</span>
              </div>,
            ];
          })}
        </div>
      </section>
      <section style={{ display: "flex", flexWrap: "wrap", gap: 14, fontSize: 13 }}>
        <div className="card" style={{ flex: "1 1 360px", padding: "12px 16px", display: "flex", gap: 14, alignItems: "center", flexWrap: "wrap" }}>
          <div className="num" style={{ fontSize: 30, fontWeight: 800, letterSpacing: "-0.02em" }}>{safeFigure(snapshot)}</div>
          <div style={{ flex: "1 1 200px", display: "flex", flexDirection: "column", gap: 4, alignItems: "flex-start" }}>
            <strong>{safeTitle(snapshot)}.</strong>
            <SafePill s={snapshot} />
            {empty ? <span style={{ color: "var(--ink-2)" }}><SafeSentence s={snapshot} /></span> : <span style={{ color: "var(--ink-2)" }}>Lowest left to spend is what stays free after the safety cushion, what is kept for bills, and your usual <span className="num">{money(snapshot.usual_pace.per_day)}</span> a day. It is not your bank balance.</span>}
          </div>
          <PlanCaveats s={snapshot} />
          <SafeExplain s={snapshot} />
        </div>
        {firstBad && (
          <div className="card" style={{ flex: "1 1 360px", padding: "12px 16px", display: "flex", gap: 14, alignItems: "center", borderColor: "var(--warning)" }}>
            <TriangleAlert size={26} color="var(--warning-ink)" aria-label="Watch" style={{ flexShrink: 0 }} />
            <div><strong>The week of {shortDate(firstBad.start)} dips <span className="num">{money(-(firstBad.low ?? 0))}</span> under your safety cushion.</strong><br /><span style={{ color: "var(--ink-2)" }}>That is at your usual spending. </span><Link to="/bills">Open Bills</Link><span style={{ color: "var(--ink-2)" }}> to see which bill dates could move to help.</span></div>
          </div>
        )}
      </section>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{snapshot.not_advice}</div>
    </div>
  );
}
