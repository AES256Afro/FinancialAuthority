// Paycheck windows: one column per paycheck, filled from the bottom with what leaves it.
import { money, monthDay, shortDate } from "../format";
import type { Event, Window } from "../types";
import type { ViewProps } from "./index";

const STACK = 420;

function Block({ h, bg, border, color, label }: { h: number; bg: string; border?: string; color?: string; label?: string }) {
  const show = label && h >= 15;
  return (
    <div style={{ height: h, background: bg, border: border ? `2px solid ${border}` : undefined, color: color ?? "#ffffff", borderRadius: 4, display: "flex", alignItems: "center", padding: "0 8px", fontSize: 11, fontWeight: 700, boxSizing: "border-box", whiteSpace: "nowrap", overflow: "hidden" }} title={label}>
      {show ? label : ""}
    </div>
  );
}

function Column({ w, index }: { w: Window; index: number }) {
  const entering = Math.max(1, w.paycheck + w.carried_in);
  const px = (v: number) => Math.max(6, (Math.abs(v) / entering) * STACK);
  const bills = [...w.bills.items].sort((a, b) => b.amount - a.amount);
  const short = w.safe < 0;
  return (
    <div className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10, border: index === 0 ? "2px solid var(--ink)" : undefined }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <strong>{shortDate(w.start)} to {shortDate(w.end)}</strong>
        {index === 0 && <span style={{ fontSize: 11, fontWeight: 800, color: "var(--paycheck-ink)", background: "var(--paycheck-soft)", padding: "2px 8px", borderRadius: 999 }}>next</span>}
      </div>
      <div className="num" style={{ fontSize: 22, fontWeight: 800, letterSpacing: "-0.01em" }}>{money(w.paycheck, { sign: true })}{w.carried_in > 0 && <span style={{ fontSize: 12, color: "var(--muted)", fontWeight: 600 }}> + {money(w.carried_in)} carried in</span>}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 2, height: STACK, justifyContent: "flex-end" }}>
        {short ? (
          <Block h={px(w.safe)} bg="var(--critical-soft)" border="var(--critical)" color="var(--ink)" label={`Short by ${money(-w.safe)}`} />
        ) : (
          <Block h={px(w.safe)} bg="var(--safe)" label={`Safe to spend ${money(w.safe)}`} />
        )}
        {w.buffer > 0 && <Block h={px(w.buffer)} bg="var(--buffer)" color="var(--ink)" label={`Buffer ${money(w.buffer)}`} />}
        {w.set_aside.total > 0 && <Block h={px(w.set_aside.total)} bg="var(--surface)" border="var(--bill)" color="var(--ink)" label={`Set aside ${money(w.set_aside.total)} · ${w.set_aside.items.length} items`} />}
        {bills.map((e: Event, i: number) => (
          <Block key={`${e.source}-${i}`} h={px(e.amount)} bg={e.kind === "installment" ? "var(--debt)" : "var(--bill)"} label={`${e.name} ${money(e.amount)} · ${monthDay(e.date)}`} />
        ))}
        {w.advances.total > 0 && <Block h={px(w.advances.total)} bg="var(--debt)" label={`Advances repay ${money(w.advances.total)}`} />}
      </div>
      <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{w.bills.items.length} bills{w.advances.items.length ? ` · ${w.advances.items.length} advances` : ""}{w.set_aside.items.length ? ` · ${w.set_aside.items.length} set-asides` : ""}</div>
    </div>
  );
}

export function Windows({ snapshot }: ViewProps) {
  const first = snapshot.windows[0];
  const order = first ? [...first.advances.items, ...first.bills.items].sort((a, b) => a.date.localeCompare(b.date)) : [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <h1 style={{ margin: 0, fontSize: 16, fontWeight: 800 }}>Each paycheck is a window. Fill it, and what is left is yours.</h1>
      <section className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4" style={{ gap: 16 }}>
        {snapshot.windows.map((w, i) => <Column key={w.start} w={w} index={i} />)}
      </section>
      {first && (
        <section className="card" style={{ padding: "14px 20px" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 6 }}>
            <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800 }}>Inside the next window, in order</h2>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>every line names where it came from</span>
          </div>
          <div style={{ overflowX: "auto" }}>
            <table className="ledger">
              <thead><tr><th>When</th><th>What</th><th>Known from</th><th style={{ textAlign: "right" }}>Amount</th><th style={{ textAlign: "right" }}>Status</th></tr></thead>
              <tbody>
                {order.map((e, i) => (
                  <tr key={`${e.source}-${i}`}>
                    <td style={{ whiteSpace: "nowrap" }}>{shortDate(e.date)}</td>
                    <td><span className="sw" style={{ background: e.kind === "bill" || e.kind === "subscription" ? "var(--bill)" : "var(--debt)" }} />{e.name}</td>
                    <td style={{ color: "var(--ink-2)" }}>{e.source.split(":")[0]}</td>
                    <td className="num" style={{ textAlign: "right" }}>{money(e.amount)}</td>
                    <td style={{ textAlign: "right" }}>
                      <span style={{ fontSize: 11, fontWeight: 700, padding: "2px 8px", borderRadius: 999, background: e.autopay ? "var(--surface-2)" : "var(--warning-soft)", color: e.autopay ? "var(--ink-2)" : "var(--warning-ink)" }}>{e.autopay ? "autopay" : `pay by ${monthDay(e.date)}`}</span>
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
