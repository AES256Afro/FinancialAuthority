// Where it goes: the flow of one paycheck into advances, bills, set-asides, the buffer and what is yours.
import { money, monthDay, shortDate } from "../format";
import { getPrefs } from "../prefs";
import { palette } from "../looks";
import type { Event } from "../types";
import type { ViewProps } from "./index";

const W = 1000;
const TOP = 20;
const USABLE = 424;
const GAP = 8;

function ribbon(x1: number, y1: number, h1: number, x2: number, y2: number, h2: number): string {
  const cx = (x1 + x2) / 2;
  return `M${x1},${y1} C${cx},${y1} ${cx},${y2} ${x2},${y2} L${x2},${y2 + h2} C${cx},${y2 + h2} ${cx},${y1 + h1} ${x1},${y1 + h1} Z`;
}

export function WhereItGoes({ snapshot }: ViewProps) {
  const c = palette(getPrefs().look);
  const w = snapshot.windows[0];
  if (!w) return <div className="card" style={{ padding: 18 }}>No paycheck is known yet. Confirm a paycheck on Needs you.</div>;
  const entering = w.paycheck + w.carried_in;
  const short = w.safe < 0;
  const groups = [
    { id: "advances", label: `Advances ${money(w.advances.total)}`, amount: w.advances.total, fill: c.debt, text: "#ffffff" },
    { id: "bills", label: `Bills ${money(w.bills.total)}`, sub: `${monthDay(w.start)} to ${monthDay(w.end)}`, amount: w.bills.total, fill: c.bill, text: "#ffffff" },
    { id: "aside", label: `Set aside ${money(w.set_aside.total)}`, sub: "for the window after", amount: w.set_aside.total, fill: c.surface, stroke: c.bill, text: c.ink },
    { id: "buffer", label: `Buffer ${money(w.buffer)}`, amount: w.buffer, fill: c.buffer, text: c.ink },
    { id: "safe", label: short ? `Short ${money(-w.safe)}` : "Safe to spend", sub: short ? undefined : money(w.safe), amount: Math.abs(w.safe), fill: short ? "#d03b3b" : c.safe, text: "#ffffff" },
  ].filter((g) => g.amount > 0);
  const scale = USABLE / Math.max(1, groups.reduce((s, g) => s + g.amount, 0));
  let ySrc = TOP;
  let yMid = TOP;
  const placed = groups.map((g) => {
    const h = Math.max(6, g.amount * scale);
    const out = { ...g, h, ySrc, yMid };
    ySrc += h;
    yMid += h + GAP;
    return out;
  });
  const srcHeight = ySrc - TOP;

  const leaves: { label: string; amount: number; fill: string; stroke?: string; group: string }[] = [];
  const bills: Event[] = [...w.bills.items].sort((a, b) => b.amount - a.amount);
  const top = bills.slice(0, 8);
  const rest = bills.slice(8);
  for (const e of top) leaves.push({ label: `${e.name} · ${money(e.amount)} · ${monthDay(e.date)}`, amount: e.amount, fill: e.kind === "installment" ? c.debt : c.bill, group: "bills" });
  if (rest.length) leaves.push({ label: `${rest.length} smaller · ${money(rest.reduce((s, e) => s + e.amount, 0))}`, amount: rest.reduce((s, e) => s + e.amount, 0), fill: c.bill, group: "bills" });
  for (const e of [...w.set_aside.items].sort((a, b) => b.amount - a.amount).slice(0, 5)) {
    leaves.push({ label: `${e.name}${e.amount >= 500 ? ", half" : ""} · ${money(e.amount >= 500 ? e.amount / 2 : e.amount)} · ${monthDay(e.date)}`, amount: e.amount >= 500 ? e.amount / 2 : e.amount, fill: c.surface, stroke: c.bill, group: "aside" });
  }
  const leafPlaced: (typeof leaves[number] & { y: number; h: number; fromY: number })[] = [];
  for (const g of placed) {
    const mine = leaves.filter((l) => l.group === g.id);
    let y = g.yMid;
    let from = g.yMid;
    for (const l of mine) {
      const h = Math.max(6, l.amount * scale);
      leafPlaced.push({ ...l, y, h, fromY: from });
      y += Math.max(15, h) + 3; // a label needs its own line even when the bar is thin
      from += h;
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 16 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, letterSpacing: "-0.01em" }}>Where {shortDate(w.start)}'s paycheck goes</h1>
        <div style={{ fontSize: 12, color: "var(--muted)" }}>Read left to right: money enters, is spoken for, and what reaches the right edge is yours.</div>
      </div>
      <section className="card" style={{ padding: "16px 20px", display: "flex", flexWrap: "wrap", gap: 20 }}>
        <svg viewBox={`0 0 ${W} 500`} width="100%" style={{ display: "block", height: "auto", maxWidth: 860, flex: "1 1 600px" }} role="img" aria-label="Flow of the paycheck into advances, bills, set-asides, buffer and safe to spend">
          <rect x={40} y={TOP} width={150} height={srcHeight} rx={6} fill={c.paycheck} />
          <text x={115} y={TOP + srcHeight / 2 - 4} textAnchor="middle" fill="#ffffff" fontSize={15} fontWeight={800}>Paycheck</text>
          <text x={115} y={TOP + srcHeight / 2 + 16} textAnchor="middle" fill="#ffffff" fontSize={13} fontWeight={700}>{money(w.paycheck)}{w.carried_in > 0 ? ` + ${money(w.carried_in)}` : ""}</text>
          {placed.map((g) => (
            <path key={`r-${g.id}`} d={ribbon(190, g.ySrc, g.h, 420, g.yMid, g.h)} fill={g.stroke ?? g.fill} fillOpacity={g.id === "aside" ? 0.14 : g.id === "buffer" ? 0.6 : 0.3} />
          ))}
          {placed.map((g) => (
            <g key={g.id}>
              <rect x={420} y={g.yMid} width={150} height={g.h} rx={5} fill={g.fill} stroke={g.stroke} strokeWidth={g.stroke ? 2 : 0} />
              {g.h >= 18 && <text x={495} y={g.yMid + g.h / 2 + (g.sub && g.h >= 36 ? -4 : 4)} textAnchor="middle" fill={g.text} fontSize={12} fontWeight={800}>{g.label}</text>}
              {g.sub && g.h >= 36 && <text x={495} y={g.yMid + g.h / 2 + 14} textAnchor="middle" fill={g.text} fontSize={11} fontWeight={700}>{g.sub}</text>}
            </g>
          ))}
          {leafPlaced.map((l, i) => (
            <g key={i}>
              <path d={ribbon(570, l.fromY, l.h, 790, l.y, l.h)} fill={l.stroke ?? l.fill} fillOpacity={l.stroke ? 0.1 : 0.22} />
              <rect x={790} y={l.y} width={14} height={l.h} rx={3} fill={l.fill} stroke={l.stroke} strokeWidth={l.stroke ? 2 : 0} />
              <text x={814} y={l.y + Math.min(l.h, 14) / 2 + 4} fontSize={12} fontWeight={700} fill={c.ink}>{l.label}</text>
            </g>
          ))}
        </svg>
        <div style={{ flex: "1 1 220px", display: "flex", flexDirection: "column", gap: 10, minWidth: 220 }}>
          <div style={{ fontSize: 13, color: "var(--ink-2)", fontWeight: 600 }}>In the order it leaves</div>
          {placed.map((g) => (
            <div key={g.id} style={{ display: "flex", justifyContent: "space-between", gap: 10, padding: "6px 0", borderTop: "1px solid var(--surface-2)", fontSize: 13 }}>
              <span><span className="sw" style={{ background: g.fill, border: g.stroke ? `1.5px solid ${g.stroke}` : undefined, boxSizing: "border-box" }} />{g.label.replace(/ \$[\d,]+$/, "")}</span>
              <strong className="num">{money(g.id === "safe" ? w.safe : g.amount)}</strong>
            </div>
          ))}
          <div style={{ marginTop: 8, padding: "12px 14px", borderRadius: 10, background: "var(--surface-2)", fontSize: 12, color: "var(--ink-2)" }}>
            {entering > 0 ? `${money(entering)} enters this window; ${money(w.advances.total + w.bills.total + w.set_aside.total + w.buffer)} is spoken for.` : ""} The next window carries {money(w.set_aside.total)} in.
          </div>
        </div>
      </section>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{snapshot.not_advice}</div>
    </div>
  );
}
