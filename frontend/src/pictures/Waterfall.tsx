// The waterfall: a check down to what is yours, one step per part, read left to right like a receipt. Good for
// "why is it that number": every step is a part you can hover for its bills.
import { useState } from "react";
import { money } from "../format";
import type { Check } from "../paychecks";
import { CheckPicker, PartTip, Tip, useColours, useTip } from "./common";

const W = 820;
const H = 270;

export function Waterfall({ checks, initial = 0 }: { checks: Check[]; initial?: number }) {
  const colours = useColours();
  const [i, setI] = useState(Math.min(initial, checks.length - 1));
  const { ref, tip, show, hide } = useTip();
  const c = checks[i];
  if (!c) return null;
  const steps = c.parts.filter((p) => p.key !== "yours" && p.amount > 0.5);
  const yours = c.parts.find((p) => p.key === "yours");
  const end = c.short > 0 ? -c.short : yours?.amount ?? 0;
  const n = steps.length + 2;
  const padL = 56;
  const padT = 24;
  const padB = 42;
  const max = c.inflow * 1.08;
  const min = Math.min(0, end) * 1.2;
  const y = (v: number) => padT + ((H - padT - padB) * (max - v)) / Math.max(1, max - min);
  const cw = (W - padL - 10) / n;
  const bw = cw * 0.6;
  const x = (k: number) => padL + k * cw + (cw - bw) / 2;
  const step = max > 4000 ? 2000 : max > 2000 ? 1000 : 500;
  const ticks: number[] = [];
  for (let t = step; t < max; t += step) ticks.push(t);
  let level = c.inflow;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <CheckPicker checks={checks} value={i} onChange={setI} />
      <div ref={ref} style={{ position: "relative", overflowX: "auto" }} onMouseLeave={hide}>
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={`${money(c.inflow)} less ${steps.map((s) => `${s.label} ${money(s.amount)}`).join(", ")}, leaves ${money(end)}`} style={{ display: "block", maxWidth: 980, minWidth: 560, height: "auto" }}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={padL} x2={W - 10} y1={y(t)} y2={y(t)} stroke="var(--hairline)" />
              <text x={padL - 6} y={y(t) + 4} fontSize="10.5" textAnchor="end" fill="var(--muted)">{money(t)}</text>
            </g>
          ))}
          <rect x={x(0)} y={y(c.inflow)} width={bw} height={y(0) - y(c.inflow)} rx={4} fill="var(--ink)" />
          <text x={x(0) + bw / 2} y={y(c.inflow) - 6} fontSize="11" fontWeight="800" textAnchor="middle" fill="var(--ink)">{money(c.inflow)}</text>
          <text x={x(0) + bw / 2} y={H - padB + 16} fontSize="11" textAnchor="middle" fill="var(--ink-2)">{c.carried_in > 0 ? "check + carried" : "the check"}</text>
          {steps.map((p, k) => {
            const top = level;
            const bottom = level - p.amount;
            level = bottom;
            const xi = x(k + 1);
            return (
              <g key={p.key} onMouseMove={(e) => show(e, <PartTip part={p} colour={colours[p.key]} of={c.inflow} />)}>
                <line x1={xi - (cw - bw)} x2={xi} y1={y(top)} y2={y(top)} stroke="var(--muted)" strokeDasharray="3 3" />
                <rect x={xi} y={y(top)} width={bw} height={Math.max(1, y(bottom) - y(top))} rx={4} fill={colours[p.key]} />
                <text x={xi + bw / 2} y={y(top) - 6} fontSize="11" fontWeight="800" textAnchor="middle" fill="var(--ink)">−{money(p.amount)}</text>
                <text x={xi + bw / 2} y={H - padB + 16} fontSize="11" textAnchor="middle" fill="var(--ink-2)">{p.label.length > 14 ? `${p.label.slice(0, 13)}…` : p.label}</text>
              </g>
            );
          })}
          {(() => {
            const xi = x(n - 1);
            const fill = end < 0 ? "var(--critical)" : colours.yours;
            return (
              <g onMouseMove={(e) => yours && show(e, end < 0 ? <div><strong>Short {money(-end)}</strong><div>The check does not cover its window.</div></div> : <PartTip part={yours} colour={colours.yours} of={c.inflow} />)}>
                <line x1={xi - (cw - bw)} x2={xi} y1={y(level)} y2={y(level)} stroke="var(--muted)" strokeDasharray="3 3" />
                <rect x={xi} y={y(Math.max(0, end))} width={bw} height={Math.max(1, Math.abs(y(end) - y(0)))} rx={4} fill={fill} />
                <text x={xi + bw / 2} y={y(Math.max(0, end)) - 6} fontSize="12" fontWeight="800" textAnchor="middle" fill="var(--ink)">{end < 0 ? `short ${money(-end)}` : money(end)}</text>
                <text x={xi + bw / 2} y={H - padB + 16} fontSize="11" fontWeight="800" textAnchor="middle" fill="var(--ink)">{end < 0 ? "short" : "yours"}</text>
              </g>
            );
          })()}
          <line x1={padL} x2={W - 10} y1={y(0)} y2={y(0)} stroke="var(--ink-2)" />
        </svg>
        <Tip tip={tip} width={ref.current?.clientWidth ?? W} />
      </div>
    </div>
  );
}
