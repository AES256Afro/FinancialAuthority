// Where it goes: the flow of one paycheck into advances, bills, what is kept for the next paycheck, the safety
// cushion and what is yours.
import { Empty } from "../components/State";
import { noPayday } from "../components/SafeExplain";
import { amountClass, eventDay, money, moneyOr, shortDate } from "../format";
import { getPrefs } from "../prefs";
import { palette } from "../looks";
import { inkOn } from "../paychecks";
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
  // With no paycheck on file the engine's window is a frame two weeks out, not a payday: nothing is drawn as one.
  if (!w || noPayday(snapshot)) return <Empty to="/paystubs#paycheck" action="Add your paycheck">No paycheck is on file yet, so there is no paycheck to follow.</Empty>;
  const entering = w.inflow;
  // No balance yet: what checking carries in and what this check leaves are null; neither is drawn, and nothing is short.
  const safe = w.safe;
  const carriedIn = w.carried_in;
  const short = safe !== null && safe < 0;
  const behind = carriedIn !== null && carriedIn < 0 ? Math.abs(carriedIn) : 0; // checking is already short before payday
  // Text on a fill is dark or white by the fill; the short blocks are the soft red with dark red words.
  const groups = [
    { id: "behind", label: `Already short ${money(behind)}`, sub: "before payday", amount: behind, fill: "var(--critical-soft)", stroke: "var(--critical)", text: "var(--critical-ink)" },
    { id: "advances", label: `Advance apps ${money(w.advances.total)}`, amount: w.advances.total, fill: c.debt, text: inkOn(c.debt) },
    { id: "bills", label: `Bills ${money(w.bills.total)}`, sub: `${shortDate(w.start)} to ${shortDate(w.end)}`, amount: w.bills.total, fill: c.bill, text: inkOn(c.bill) },
    { id: "aside", label: `Set aside ${money(w.set_aside.total)}`, sub: "for the next paycheck", amount: w.set_aside.total, fill: c.surface, stroke: c.bill, text: c.ink },
    { id: "buffer", label: `Safety cushion ${money(w.buffer)}`, amount: w.buffer, fill: c.buffer, text: inkOn(c.buffer) },
    { id: "safe", label: safe !== null && safe < 0 ? `Short ${money(-safe)}` : "Left", sub: short ? undefined : moneyOr(safe), amount: safe === null ? 0 : Math.abs(safe), fill: short ? "var(--critical-soft)" : c.safe, stroke: short ? "var(--critical)" : undefined, text: short ? "var(--critical-ink)" : inkOn(c.safe) },
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
  for (const e of top) leaves.push({ label: `${e.name} · ${money(e.amount)} · ${eventDay(e)}`, amount: e.amount, fill: e.kind === "installment" ? c.debt : c.bill, group: "bills" });
  if (rest.length) leaves.push({ label: `${w.bills.after_eight.count} smaller · ${money(w.bills.after_eight.total)}`, amount: w.bills.after_eight.total, fill: c.bill, group: "bills" });
  for (const e of [...w.set_aside.items].sort((a, b) => b.amount - a.amount).slice(0, 5)) {
    const kept = e.kept ?? e.amount;
    leaves.push({ label: `${e.name}${(e.share ?? 1) < 1 ? ", half" : ""} · ${money(kept)} · ${eventDay(e)}`, amount: kept, fill: c.surface, stroke: c.bill, group: "aside" });
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
        <h2 style={{ margin: 0, fontSize: 22, fontWeight: 800, letterSpacing: "-0.01em" }}>Where the {shortDate(w.start)} paycheck goes</h2>
        <div style={{ fontSize: 12, color: "var(--muted)" }}>Read left to right: money comes in, each part goes where it is promised, and what reaches the right edge is yours.</div>
      </div>
      <section className="card" style={{ padding: "16px 20px", display: "flex", flexWrap: "wrap", gap: 20 }}>
        {/* On a phone the picture keeps a readable size and scrolls sideways; the list beside it has every part as words. */}
        <div style={{ flex: "1 1 600px", minWidth: 0, maxWidth: 860, overflowX: "auto" }}>
          <svg viewBox={`0 0 ${W} 500`} width="100%" style={{ display: "block", height: "auto", minWidth: 640 }} role="img" aria-label="Flow of the paycheck into advance apps, bills, what is kept for the next paycheck, the safety cushion and safe to spend">
            <rect x={40} y={TOP} width={150} height={srcHeight} rx={6} fill={c.paycheck} />
            <text x={115} y={TOP + srcHeight / 2 - 4} textAnchor="middle" fill={inkOn(c.paycheck)} fontSize={15} fontWeight={800}>Paycheck</text>
            <text className="num" x={115} y={TOP + srcHeight / 2 + 16} textAnchor="middle" fill={inkOn(c.paycheck)} fontSize={13} fontWeight={700}>{money(w.paycheck)}{carriedIn !== null && carriedIn > 0 ? ` + ${money(carriedIn)}` : ""}</text>
            {placed.map((g) => (
              <path key={`r-${g.id}`} d={ribbon(190, g.ySrc, g.h, 420, g.yMid, g.h)} fill={g.stroke ?? g.fill} fillOpacity={g.id === "aside" ? 0.14 : g.id === "buffer" ? 0.6 : 0.3} />
            ))}
            {placed.map((g) => (
              <g key={g.id}>
                <rect x={420} y={g.yMid} width={150} height={g.h} rx={5} fill={g.fill} stroke={g.stroke} strokeWidth={g.stroke ? 2 : 0} />
                {g.h >= 18 && <text className={amountClass(g.label)} x={495} y={g.yMid + g.h / 2 + (g.sub && g.h >= 36 ? -4 : 4)} textAnchor="middle" fill={g.text} fontSize={12} fontWeight={800}>{g.label}</text>}
                {g.sub && g.h >= 36 && <text className={amountClass(g.sub)} x={495} y={g.yMid + g.h / 2 + 14} textAnchor="middle" fill={g.text} fontSize={11} fontWeight={700}>{g.sub}</text>}
              </g>
            ))}
            {leafPlaced.map((l, i) => (
              <g key={i}>
                <path d={ribbon(570, l.fromY, l.h, 790, l.y, l.h)} fill={l.stroke ?? l.fill} fillOpacity={l.stroke ? 0.1 : 0.22} />
                <rect x={790} y={l.y} width={14} height={l.h} rx={3} fill={l.fill} stroke={l.stroke} strokeWidth={l.stroke ? 2 : 0} />
                <text className="num" x={814} y={l.y + Math.min(l.h, 14) / 2 + 4} fontSize={12} fontWeight={700} fill={c.ink}>{l.label}</text>
              </g>
            ))}
          </svg>
        </div>
        <div style={{ flex: "1 1 220px", display: "flex", flexDirection: "column", gap: 10, minWidth: "min(220px, 100%)" }}>
          <h3 style={{ margin: 0, fontSize: 13, color: "var(--ink-2)", fontWeight: 600 }}>In the order it leaves</h3>
          {placed.map((g) => (
            <div key={g.id} style={{ display: "flex", justifyContent: "space-between", gap: 10, padding: "6px 0", borderTop: "1px solid var(--surface-2)", fontSize: 13 }}>
              <span><span className="sw" style={{ background: g.fill, border: g.stroke ? `1.5px solid ${g.stroke}` : undefined, boxSizing: "border-box" }} />{g.label.replace(/ −?\$[\d,]+$/, "")}{g.id === "aside" ? " for the next paycheck" : g.id === "behind" ? " before payday" : ""}</span>
              <strong className="num">{moneyOr(g.id === "safe" ? safe : g.id === "behind" ? carriedIn : g.amount)}</strong>
            </div>
          ))}
          <div className="num" style={{ marginTop: 8, padding: "12px 14px", borderRadius: 10, background: "var(--surface-2)", fontSize: 12, color: "var(--ink-2)" }}>
            {entering === null ? `No bank has reported a balance yet, so what this paycheck leaves is not known; ${money(w.spoken_for)} of it already has somewhere to go.` : behind > 0 ? `Checking is already ${money(behind)} short before payday, so ${money(w.paycheck)} comes in and ${money(entering)} is left to use; ${money(w.spoken_for)} already has somewhere to go.` : entering > 0 ? `${money(entering)} comes in with this paycheck; ${money(w.spoken_for)} of it already has somewhere to go.` : ""} The next paycheck starts with {money(w.set_aside.total)} kept for it.
          </div>
        </div>
      </section>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{snapshot.not_advice}</div>
    </div>
  );
}
