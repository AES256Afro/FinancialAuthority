// Envelopes: every obligation in the next window as an envelope, with its fill level from the engine.
import { money, shortDate } from "../format";
import type { Event } from "../types";
import type { ViewProps } from "./index";

interface Env {
  key: string;
  name: string;
  sub: string;
  amount: number;
  target: number;
  fill: number; // 0..1
  tone: "safe" | "buffer" | "debt" | "bill";
  tag: string;
}

function envelope(e: Env) {
  const bg = { safe: "var(--safe)", buffer: "var(--buffer)", debt: "var(--debt)", bill: "var(--bill)" }[e.tone];
  const track = { safe: "var(--safe-soft)", buffer: "var(--surface-2)", debt: "var(--debt-soft)", bill: "var(--bill-soft)" }[e.tone];
  const ink = { safe: "var(--safe-ink)", buffer: "var(--ink-2)", debt: "var(--debt-ink)", bill: "var(--bill-ink)" }[e.tone];
  return (
    <div key={e.key} className="card" style={{ padding: "12px 14px", display: "flex", flexDirection: "column", gap: 8, minHeight: 168, boxSizing: "border-box", border: e.tone === "safe" ? "2px solid var(--safe)" : undefined }}>
      <div style={{ fontWeight: 800, fontSize: 14 }}>{e.name}</div>
      <div style={{ fontSize: 11, color: "var(--muted)", fontWeight: 600 }}>{e.sub}</div>
      <div style={{ flexGrow: 1, borderRadius: 8, position: "relative", overflow: "hidden", minHeight: 64, background: track }} role="img" aria-label={`${Math.round(e.fill * 100)} percent funded`}>
        <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: `${Math.round(Math.max(0, Math.min(1, e.fill)) * 100)}%`, background: bg }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", fontSize: 13 }}>
        <strong className="num">{money(e.amount)}{e.target !== e.amount && <span style={{ fontWeight: 600, color: "var(--muted)" }}> of {money(e.target)}</span>}</strong>
        <span style={{ fontSize: 10, fontWeight: 800, textTransform: "uppercase", letterSpacing: ".06em", padding: "2px 7px", borderRadius: 999, background: track, color: ink }}>{e.tag}</span>
      </div>
    </div>
  );
}

export function Envelopes({ snapshot }: ViewProps) {
  const w = snapshot.windows[0];
  if (!w) return <div className="card" style={{ padding: 18 }}>No paycheck is known yet.</div>;
  const envs: Env[] = [];
  const short = w.safe < 0;
  envs.push({ key: "spend", name: short ? "Short" : "Spend", sub: `${shortDate(w.start)} to ${shortDate(w.end)} · yours`, amount: w.safe, target: w.safe, fill: short ? 0 : 1, tone: "safe", tag: short ? "short" : "free" });
  if (w.buffer > 0) envs.push({ key: "buffer", name: "Buffer", sub: "always kept", amount: w.buffer, target: w.buffer, fill: 1, tone: "buffer", tag: "full" });
  if (w.advances.total > 0) envs.push({ key: "adv", name: "Advances", sub: `repay ${shortDate(w.start)}`, amount: w.advances.total, target: w.advances.total, fill: 1, tone: "debt", tag: `${w.advances.items.length} app${w.advances.items.length === 1 ? "" : "s"}` });
  const small: Event[] = [];
  for (const e of [...w.bills.items].sort((a, b) => b.amount - a.amount)) {
    if (e.amount < 25) {
      small.push(e);
      continue;
    }
    envs.push({ key: e.source, name: e.name, sub: `due ${shortDate(e.date)}${e.autopay ? " · autopay" : ""}`, amount: e.amount, target: e.amount, fill: e.funded ? 1 : 0, tone: e.kind === "installment" ? "debt" : "bill", tag: e.funded ? "funded" : "not yet" });
  }
  if (small.length) {
    const total = small.reduce((s, e) => s + e.amount, 0);
    envs.push({ key: "small", name: "Small stuff", sub: small.map((e) => e.name).slice(0, 4).join(", "), amount: total, target: total, fill: small.every((e) => e.funded) ? 1 : 0.5, tone: "bill", tag: small.every((e) => e.funded) ? "funded" : "partly" });
  }
  for (const e of [...w.set_aside.items].sort((a, b) => b.amount - a.amount)) {
    const share = e.share ?? 1;
    envs.push({ key: `aside-${e.source}`, name: e.name, sub: `due ${shortDate(e.date)} · next window`, amount: e.amount * share, target: e.amount, fill: e.funded ? share : 0, tone: "bill", tag: e.funded ? (share < 1 ? "half" : "set aside") : "not yet" });
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 16 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, letterSpacing: "-0.01em" }}>Envelopes</h1>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Every dollar from {shortDate(w.start)}'s paycheck already has an envelope. Fill level is how funded each one is.</div>
      </div>
      <section className="card" style={{ padding: "12px 18px", display: "flex", flexWrap: "wrap", gap: 24, alignItems: "center" }}>
        <div><span style={{ fontSize: 12, color: "var(--ink-2)", fontWeight: 600 }}>Spend envelope</span><div className="num" style={{ fontSize: 32, fontWeight: 800, letterSpacing: "-0.02em" }}>{money(w.safe)}</div></div>
        <div style={{ fontSize: 13, color: "var(--ink-2)", maxWidth: 560 }}>
          {w.bills.items.every((e) => e.funded) ? `Everything due through ${shortDate(w.end)} is funded.` : `${w.bills.items.filter((e) => !e.funded).length} bills in this window are not yet funded.`}
          {w.set_aside.items.length ? ` The next window's envelopes hold ${money(w.set_aside.total)}; the ${shortDate(snapshot.windows[1]?.start ?? w.end)} paycheck fills the rest.` : ""}
        </div>
        <div style={{ flexGrow: 1 }} />
        <div style={{ display: "flex", gap: 16, fontSize: 12, color: "var(--ink-2)", fontWeight: 600 }}>
          <span><span className="sw" style={{ background: "var(--bill)" }} />Bill</span>
          <span><span className="sw" style={{ background: "var(--debt)" }} />Debt or advance</span>
          <span><span className="sw" style={{ background: "var(--buffer)" }} />Buffer</span>
          <span><span className="sw" style={{ background: "var(--safe)" }} />Yours</span>
        </div>
      </section>
      <section className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5" style={{ gap: 12 }}>{envs.map(envelope)}</section>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{snapshot.not_advice}</div>
    </div>
  );
}
