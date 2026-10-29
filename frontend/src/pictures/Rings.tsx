// Rings: one ring per check, what is yours in the middle. Side by side, the rhythm of the checks shows: the one
// that carries the rent, the one that carries the card.
import { money, monthDay, shortDate } from "../format";
import type { Check } from "../paychecks";
import { NoChecks, PartTip, Tip, useColours, useTip } from "./common";

function arc(cx: number, cy: number, r: number, ir: number, a0: number, a1: number): string {
  const large = a1 - a0 > Math.PI ? 1 : 0;
  const p = (rr: number, a: number) => [cx + rr * Math.cos(a), cy + rr * Math.sin(a)];
  const [ax, ay] = p(r, a0);
  const [bx, by] = p(r, a1);
  const [cx2, cy2] = p(ir, a1);
  const [dx, dy] = p(ir, a0);
  return `M ${ax} ${ay} A ${r} ${r} 0 ${large} 1 ${bx} ${by} L ${cx2} ${cy2} A ${ir} ${ir} 0 ${large} 0 ${dx} ${dy} Z`;
}

export function Rings({ checks }: { checks: Check[] }) {
  const colours = useColours();
  const { ref, tip, show, hide } = useTip();
  const shown = checks.slice(0, 4);
  if (!shown.length) return <NoChecks />;
  const yoursOf = (c: Check) => c.parts.find((p) => p.key === "yours")?.amount ?? 0;
  // The picture's answer in words: each check, and what it leaves or how short it falls.
  const words = shown.map((c) => `${shortDate(c.start)} check ${money(c.inflow)}, ${c.short > 0 ? `short ${money(c.short)}` : `${money(yoursOf(c))} yours`}`).join("; ");
  const cell = 190;
  const W = cell * shown.length;
  const H = 200;
  const r = 66;
  const ir = 44;
  return (
    <div ref={ref} style={{ position: "relative", overflowX: "auto" }} onMouseLeave={hide}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={`One ring per check: ${words}`} style={{ display: "block", maxWidth: Math.min(980, W * 1.3), minWidth: 360, height: "auto" }}>
        {shown.map((c, k) => {
          const cx = cell * k + cell / 2;
          const cy = 92;
          const total = Math.max(1, c.parts_total + c.short); // the ring's whole: the parts, and what was short, side by side
          let a = -Math.PI / 2;
          const yours = yoursOf(c);
          return (
            <g key={c.start} opacity={c.actual ? 1 : 0.92}>
              {c.parts.filter((p) => p.amount > 0).map((p) => {
                const span = (p.amount / total) * Math.PI * 2;
                const d = arc(cx, cy, r, ir, a, a + Math.max(0.002, span - 0.02));
                a += span;
                return <path key={p.key} d={d} fill={colours[p.key]} onMouseMove={(e) => show(e, <><div style={{ fontWeight: 800, marginBottom: 4 }}>The {shortDate(c.start)} check</div><PartTip part={p} colour={colours[p.key]} of={c.inflow} /></>)} />;
              })}
              {c.short > 0 && <path d={arc(cx, cy, r, ir, a, a + (c.short / total) * Math.PI * 2 - 0.02)} fill="var(--critical)" />}
              <text className="num" x={cx} y={cy - 2} fontSize="15" fontWeight="800" textAnchor="middle" fill={c.short > 0 ? "var(--critical-ink)" : "var(--ink)"}>{c.short > 0 ? `−${money(c.short)}` : money(yours)}</text>
              <text x={cx} y={cy + 14} fontSize="10.5" fontWeight={c.short > 0 ? 800 : 400} textAnchor="middle" fill={c.short > 0 ? "var(--critical-ink)" : "var(--ink-2)"}>{c.short > 0 ? "short" : "yours"}</text>
              <text className="num" x={cx} y={cy + r + 24} fontSize="12" fontWeight="800" textAnchor="middle" fill="var(--ink)">{monthDay(c.start)} · {money(c.inflow)}</text>
            </g>
          );
        })}
      </svg>
      <Tip tip={tip} width={ref.current?.clientWidth ?? W} />
    </div>
  );
}
