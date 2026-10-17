// Paycheck columns: one column per paycheck, filled from the bottom with what leaves it.
import { Empty } from "../components/State";
import { noPayday } from "../components/SafeExplain";
import { amountClass, money, shortDate } from "../format";
import { palette } from "../looks";
import { inkOn } from "../paychecks";
import { getPrefs } from "../prefs";
import type { Event, Window } from "../types";
import { sourceWord } from "./BillBoard";
import type { ViewProps } from "./index";

const STACK = 420;

type Palette = ReturnType<typeof palette>;

function Block({ h, bg, border, color, label }: { h: number; bg: string; border?: string; color?: string; label?: string }) {
  const show = label && h >= 15;
  return (
    <div style={{ height: h, background: bg, border: border ? `2px solid ${border}` : undefined, color: color ?? inkOn(bg), borderRadius: 4, display: "flex", alignItems: "center", padding: "0 8px", fontSize: 11, fontWeight: 700, boxSizing: "border-box", whiteSpace: "nowrap", overflow: "hidden" }} title={label}>
      {show ? <span className={amountClass(label)}>{label}</span> : ""}
    </div>
  );
}

function Column({ w, index, c }: { w: Window; index: number; c: Palette }) {
  const entering = Math.max(1, w.inflow, w.paycheck); // drawing only: the column's height is the most that comes in
  const px = (v: number) => Math.max(6, (Math.abs(v) / entering) * STACK);
  const bills = [...w.bills.items].sort((a, b) => b.amount - a.amount);
  const short = w.safe < 0;
  return (
    <div className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10, border: index === 0 ? "2px solid var(--ink)" : undefined }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>{shortDate(w.start)} to {shortDate(w.end)}</h3>
        {index === 0 && <span style={{ fontSize: 11, fontWeight: 800, color: "var(--paycheck-ink)", background: "var(--paycheck-soft)", padding: "2px 8px", borderRadius: 999 }}>this paycheck</span>}
      </div>
      <div className="num" style={{ fontSize: 22, fontWeight: 800, letterSpacing: "-0.01em" }}>
        {money(w.paycheck, { sign: true })}
        {w.carried_in > 0 && <span style={{ fontSize: 12, color: "var(--muted)", fontWeight: 600 }}> + {money(w.carried_in)} carried in</span>}
        {w.carried_in < 0 && <span style={{ fontSize: 12, color: "var(--critical-ink)", fontWeight: 700 }}> − {money(Math.abs(w.carried_in))} already short before payday</span>}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 2, height: STACK, justifyContent: "flex-end" }}>
        {short ? (
          <Block h={px(w.safe)} bg="var(--critical-soft)" border="var(--critical)" color="var(--critical-ink)" label={`Short by ${money(-w.safe)}`} />
        ) : (
          <Block h={px(w.safe)} bg={c.safe} label={`Safe to spend ${money(w.safe)}`} />
        )}
        {w.buffer > 0 && <Block h={px(w.buffer)} bg={c.buffer} label={`Safety cushion ${money(w.buffer)}`} />}
        {w.set_aside.total > 0 && <Block h={px(w.set_aside.total)} bg={c.surface} border={c.bill} color="var(--ink)" label={`Kept for the next paycheck ${money(w.set_aside.total)} · ${w.set_aside.items.length} items`} />}
        {bills.map((e: Event, i: number) => {
          const fill = e.kind === "installment" ? c.debt : c.bill;
          return <Block key={`${e.source}-${i}`} h={px(e.amount)} bg={fill} label={`${e.name} ${money(e.amount)} · ${shortDate(e.date)}`} />;
        })}
        {w.advances.total > 0 && <Block h={px(w.advances.total)} bg={c.debt} label={`Advance apps take back ${money(w.advances.total)}`} />}
        {w.carried_in < 0 && <Block h={px(w.carried_in)} bg="var(--critical-soft)" border="var(--critical)" color="var(--critical-ink)" label={`Already short before payday ${money(Math.abs(w.carried_in))}`} />}
      </div>
      <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{w.bills.items.length} bills{w.advances.items.length ? ` · ${w.advances.items.length} advances` : ""}{w.set_aside.items.length ? ` · ${w.set_aside.items.length} kept for the next paycheck` : ""}</div>
    </div>
  );
}

export function Windows({ snapshot }: ViewProps) {
  const c = palette(getPrefs().look);
  // With no paycheck on file the engine's columns are a frame two weeks apart, not paydays: none is drawn as one.
  if (noPayday(snapshot)) return <Empty to="/paystubs#paycheck" action="Add your paycheck">No paycheck is on file yet, so there is no paycheck to fill. Add it once and each column shows one check.</Empty>;
  const first = snapshot.windows[0];
  const order = first ? [...first.advances.items, ...first.bills.items].sort((a, b) => a.date.localeCompare(b.date)) : [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <h2 style={{ margin: 0, fontSize: 16, fontWeight: 800 }}>Each paycheck, filled from the bottom with what it pays. What is left at the top is yours.</h2>
      <section className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4" style={{ gap: 16 }}>
        {snapshot.windows.map((w, i) => <Column key={w.start} w={w} index={i} c={c} />)}
      </section>
      {first && (
        <section className="card" style={{ padding: "14px 20px" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap", marginBottom: 6 }}>
            <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800 }}>What this paycheck pays, by date</h2>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>every line says where it came from</span>
          </div>
          <div style={{ overflowX: "auto" }}>
            <table className="ledger">
              <thead><tr><th>When</th><th>What</th><th>Known from</th><th style={{ textAlign: "right" }}>Amount</th><th style={{ textAlign: "right" }}>How it is paid</th></tr></thead>
              <tbody>
                {order.map((e, i) => (
                  <tr key={`${e.source}-${i}`}>
                    <td style={{ whiteSpace: "nowrap" }}>{shortDate(e.date)}</td>
                    <td><span className="sw" style={{ background: e.kind === "bill" || e.kind === "subscription" ? c.bill : c.debt }} />{e.name}</td>
                    <td style={{ color: "var(--ink-2)" }}>{sourceWord(e.source)}</td>
                    <td className="num" style={{ textAlign: "right" }}>{money(e.amount)}</td>
                    <td style={{ textAlign: "right" }}>
                      <span style={{ fontSize: 11, fontWeight: 700, padding: "2px 8px", borderRadius: 999, background: e.autopay ? "var(--surface-2)" : "var(--warning-soft)", color: e.autopay ? "var(--ink-2)" : "var(--warning-ink)", whiteSpace: "nowrap" }}>{e.autopay ? "autopay" : `pay it yourself by ${shortDate(e.date)}`}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{snapshot.not_advice}</div>
    </div>
  );
}
