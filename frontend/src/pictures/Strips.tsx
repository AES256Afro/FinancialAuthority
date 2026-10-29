// Strips over time: the last checks as your transactions saw them, solid, then the planned ones, faded. Each column is
// what a check brought in, stacked by where it went. The number on top is what was yours; the dashed line is
// what the check had to cover. The trend of the gap between them is the story.
import { money, monthDay, shortDate } from "../format";
import type { Check } from "../paychecks";
import { NoChecks, PartTip, Tip, useColours, useTip } from "./common";

const W = 820;
const H = 280;

export function Strips({ history, planned }: { history: Check[]; planned: Check[] }) {
  const colours = useColours();
  const { ref, tip, show, hide } = useTip();
  const checks = [...history, ...planned];
  if (!checks.length) return <NoChecks />;
  const padL = 56;
  const padR = 12;
  const padT = 30;
  const padB = 34;
  const max = Math.max(...checks.map((c) => Math.max(c.inflow, c.parts_total))) * 1.08;
  const minShort = Math.max(...checks.map((c) => c.short), 0);
  const range = max + minShort;
  const y = (v: number) => padT + ((H - padT - padB) * (max - v)) / Math.max(1, range);
  const cw = (W - padL - padR) / checks.length;
  const bw = Math.min(46, cw * 0.62);
  const step = max > 4000 ? 2000 : max > 2000 ? 1000 : 500;
  const ticks: number[] = [];
  for (let t = step; t < max; t += step) ticks.push(t);
  const cx = (i: number) => padL + i * cw + cw / 2;
  const coverOf = (c: Check) => c.cover_total;
  const line = (vals: number[]) => vals.map((v, i) => `${i ? "L" : "M"} ${cx(i)} ${y(v)}`).join(" ");
  return (
    <div ref={ref} style={{ position: "relative", overflowX: "auto" }} onMouseLeave={hide}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Each check stacked by where it went, past and planned" style={{ display: "block", maxWidth: 980, minWidth: 560, height: "auto" }}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} stroke="var(--hairline)" />
            <text className="num" x={padL - 6} y={y(t) + 4} fontSize="10.5" textAnchor="end" fill="var(--muted)">{money(t)}</text>
          </g>
        ))}
        {history.length > 0 && planned.length > 0 && (
          <g>
            <line x1={padL + history.length * cw} x2={padL + history.length * cw} y1={padT - 16} y2={H - padB + 4} stroke="var(--ink-2)" strokeDasharray="3 3" />
            <text x={padL + history.length * cw + 4} y={padT - 6} fontSize="10.5" fill="var(--ink-2)">planned</text>
            <text x={padL + history.length * cw - 4} y={padT - 6} fontSize="10.5" fill="var(--ink-2)" textAnchor="end">from your transactions</text>
          </g>
        )}
        {checks.map((c, i) => {
          let acc = 0;
          const x = cx(i) - bw / 2;
          const shown = c.parts.filter((p) => p.amount > 0);
          return (
            <g key={c.start} opacity={c.actual ? 1 : 0.62}>
              {shown.map((p, k) => {
                const top = y(acc + p.amount);
                const h = Math.max(0, y(acc) - top - (k < shown.length - 1 ? 2 : 0));
                acc += p.amount;
                return <rect key={p.key} x={x} y={top} width={bw} height={h} rx={k === shown.length - 1 ? 4 : 0} fill={colours[p.key]} onMouseMove={(e) => show(e, <><div style={{ fontWeight: 800, marginBottom: 4 }}>The {shortDate(c.start)} check{c.actual ? "" : " (planned)"}</div><PartTip part={p} colour={colours[p.key]} of={c.inflow} /></>)} />;
              })}
              {c.short > 0 && <rect x={x} y={y(0)} width={bw} height={y(-c.short) - y(0)} rx={3} fill="var(--critical)" onMouseMove={(e) => show(e, <div><strong>Short {money(c.short)}</strong><div>The check did not cover the days until the next one.</div></div>)} />}
              <text className="num" x={cx(i)} y={y(Math.max(c.inflow, acc)) - 6} fontSize="10.5" fontWeight="800" textAnchor="middle" fill="var(--ink)">{c.short > 0 ? `−${money(c.short)}` : money(c.parts.find((p) => p.key === "yours")?.amount ?? 0)}</text>
              <text x={cx(i)} y={H - padB + 16} fontSize="10.5" textAnchor="middle" fill="var(--muted)">{monthDay(c.start)}</text>
            </g>
          );
        })}
        <line x1={padL} x2={W - padR} y1={y(0)} y2={y(0)} stroke="var(--ink-2)" />
        <path d={line(checks.map((c) => c.inflow))} fill="none" stroke="var(--ink)" strokeWidth={2} />
        <path d={line(checks.map(coverOf))} fill="none" stroke="var(--ink-2)" strokeWidth={2} strokeDasharray="5 4" />
        {checks.map((c, i) => <circle key={`n${c.start}`} cx={cx(i)} cy={y(c.inflow)} r={3.5} fill="var(--surface)" stroke="var(--ink)" strokeWidth={2} />)}
      </svg>
      <Tip tip={tip} width={ref.current?.clientWidth ?? W} />
      <div style={{ fontSize: 12, color: "var(--ink-2)", display: "flex", gap: 14, flexWrap: "wrap", marginTop: 4 }}>
        <span><svg width="22" height="8" aria-hidden="true"><line x1="0" x2="22" y1="4" y2="4" stroke="var(--ink)" strokeWidth="2" /></svg> what came in</span>
        <span><svg width="22" height="8" aria-hidden="true"><line x1="0" x2="22" y1="4" y2="4" stroke="var(--ink-2)" strokeWidth="2" strokeDasharray="5 4" /></svg> what it had to cover</span>
        <span>the number on top: what was yours</span>
      </div>
    </div>
  );
}
