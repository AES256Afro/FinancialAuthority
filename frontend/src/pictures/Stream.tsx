// The stream: six months of where the checks are due to go, as bands that widen and narrow. When a plan ends
// its band thins and what is yours widens; the shape shows the direction without a single number.
import { money } from "../format";
import type { Month } from "../paychecks";
import { PartTip, Tip, useColours, useTip } from "./common";

const W = 820;
const H = 280;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function Stream({ months }: { months: Month[] }) {
  const colours = useColours();
  const { ref, tip, show, hide } = useTip();
  if (months.length < 2) return null;
  const keys = months[0].parts.map((p) => p.key).filter((k) => k !== "aside");
  const padL = 56;
  const padR = 150;
  const padT = 16;
  const padB = 28;
  const totals = months.map((m) => m.parts.filter((p) => p.key !== "aside").reduce((t, p) => t + p.amount, 0));
  const max = Math.max(...totals, ...months.map((m) => m.income)) * 1.06;
  const x = (i: number) => padL + ((W - padL - padR) * i) / (months.length - 1);
  const y = (v: number) => padT + (H - padT - padB) * (1 - v / Math.max(1, max));
  const curve = (pts: [number, number][]) => pts.slice(1).reduce((d, b, i) => {
    const a = pts[i];
    const mx = (a[0] + b[0]) / 2;
    return `${d} C ${mx} ${a[1]}, ${mx} ${b[1]}, ${b[0]} ${b[1]}`;
  }, `M ${pts[0][0]} ${pts[0][1]}`);
  const base = months.map(() => 0);
  const layers = keys.map((k) => {
    const lower = [...base];
    const upper = months.map((m, i) => lower[i] + (m.parts.find((p) => p.key === k)?.amount ?? 0));
    upper.forEach((v, i) => (base[i] = v));
    return { k, lower, upper };
  });
  const step = max > 8000 ? 4000 : max > 4000 ? 2000 : 1000;
  const ticks: number[] = [];
  for (let t = step; t < max; t += step) ticks.push(t);
  return (
    <div ref={ref} style={{ position: "relative", overflowX: "auto" }} onMouseLeave={hide}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Six months of where the checks go" style={{ display: "block", maxWidth: 980, minWidth: 560, height: "auto" }}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} stroke="var(--hairline)" />
            <text x={padL - 6} y={y(t) + 4} fontSize="10.5" textAnchor="end" fill="var(--muted)">{money(t)}</text>
          </g>
        ))}
        {layers.map(({ k, lower, upper }) => {
          const top = upper.map((v, i) => [x(i), y(v)] as [number, number]);
          const bottom = lower.map((v, i) => [x(i), y(v)] as [number, number]).reverse();
          const d = `${curve(top)} L ${bottom[0][0]} ${bottom[0][1]} ${curve(bottom).slice(curve(bottom).indexOf("C") - 1)} Z`;
          const last = months.length - 1;
          const mid = (y(upper[last]) + y(lower[last])) / 2;
          const thick = y(lower[last]) - y(upper[last]);
          const label = months[0].parts.find((p) => p.key === k)?.label ?? k;
          return (
            <g key={k}>
              <path d={d} fill={colours[k]} opacity={0.9} stroke="var(--surface)" strokeWidth={2}
                onMouseMove={(e) => {
                  const box = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
                  const rel = ((e.clientX - box.left) / box.width) * W;
                  const i = Math.max(0, Math.min(last, Math.round(((rel - padL) / (W - padL - padR)) * last)));
                  const part = months[i].parts.find((p) => p.key === k);
                  if (part) show(e, <><div style={{ fontWeight: 800, marginBottom: 4 }}>{MONTHS[Number(months[i].month.slice(5, 7)) - 1]} {months[i].month.slice(0, 4)}</div><PartTip part={part} colour={colours[k]} of={months[i].income} /></>);
                }} />
              {thick > 9 && <text x={W - padR + 8} y={mid + 4} fontSize="11.5" fontWeight="700" fill="var(--ink)">{label} <tspan fill="var(--ink-2)" fontWeight="600">{money(upper[last] - lower[last])}</tspan></text>}
            </g>
          );
        })}
        <path d={curve(months.map((m, i) => [x(i), y(m.income)]))} fill="none" stroke="var(--ink)" strokeWidth={2} strokeDasharray="6 4" />
        {months.map((m, i) => (
          <g key={m.month}>
            <circle cx={x(i)} cy={y(m.income)} r={3.5} fill="var(--surface)" stroke="var(--ink)" strokeWidth={2} />
            <text x={x(i)} y={H - 8} fontSize="11" textAnchor="middle" fill="var(--muted)">{MONTHS[Number(m.month.slice(5, 7)) - 1]}{m.partial ? " (rest)" : ""}</text>
          </g>
        ))}
        <line x1={padL} x2={W - padR} y1={y(0)} y2={y(0)} stroke="var(--ink-2)" />
      </svg>
      <Tip tip={tip} width={ref.current?.clientWidth ?? W} />
      <div style={{ fontSize: 12, color: "var(--ink-2)" }}><svg width="22" height="8" aria-hidden="true"><line x1="0" x2="22" y1="4" y2="4" stroke="var(--ink)" strokeWidth="2" strokeDasharray="6 4" /></svg> what the checks bring in each month</div>
    </div>
  );
}
