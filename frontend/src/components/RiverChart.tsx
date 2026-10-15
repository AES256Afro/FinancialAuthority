// The river: sixty days of what is free to spend, drawn from the engine's projection.
// Two series: at your usual pace (the band) and if nothing else is spent (the thin line).
// Inline SVG, no chart library. Hover shows the day; the table twin lives beside it.
import { useMemo, useState } from "react";
import { money, monthDay, parseIso } from "../format";
import { palette } from "../looks";
import type { LookId } from "../looks";
import type { Snapshot } from "../types";

interface Props {
  snapshot: Snapshot;
  look: LookId;
}

const W = 1120;
const H = 330;
const LEFT = 70;
const RIGHT = 1090;
const TOP = 20;
const BASE = 270;

export function RiverChart({ snapshot, look }: Props) {
  const c = palette(look);
  const [hover, setHover] = useState<number | null>(null);
  const points = snapshot.projection;
  const n = points.length;
  let windowEnd = points.findIndex((p) => p.date > snapshot.window.end);
  if (windowEnd <= 0) windowEnd = n;
  const top = Math.max(600, ...points.map((p) => p.at_pace), ...points.slice(0, windowEnd).map((p) => p.available));
  const bottom = Math.min(0, ...points.map((p) => p.at_pace));
  const scale = (BASE - TOP) / (top - bottom);
  const x = (i: number) => LEFT + (i * (RIGHT - LEFT)) / Math.max(1, n - 1);
  const y = (v: number) => BASE - (v - bottom) * scale;
  const floorY = y(0);

  const paydays = useMemo(() => {
    const first = parseIso(snapshot.next_paycheck.date);
    const out: number[] = [];
    for (let k = 0; k < 5; k++) {
      const d = new Date(first);
      d.setDate(first.getDate() + 14 * k);
      const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      const idx = points.findIndex((p) => p.date === iso);
      if (idx >= 0) out.push(idx);
    }
    return out;
  }, [snapshot, points]);

  const bills = useMemo(() => {
    const events = [...snapshot.due_before_payday.items, ...snapshot.window.items, ...snapshot.set_aside.items];
    return events.map((e) => ({ idx: points.findIndex((p) => p.date === e.date), e })).filter((b) => b.idx >= 0);
  }, [snapshot, points]);

  const lowIdx = useMemo(() => {
    let endIdx = points.findIndex((p) => p.date > snapshot.window.end);
    if (endIdx <= 0) endIdx = n;
    let low = 0;
    for (let i = 0; i < endIdx; i++) if (points[i].at_pace < points[low].at_pace) low = i;
    return low;
  }, [points, snapshot, n]);

  const pathOf = (key: "available" | "at_pace", upTo: number = n) =>
    points.slice(0, upTo).map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`).join(" ");
  const paceLine = pathOf("at_pace");
  const paceArea = `${paceLine} L${x(n - 1).toFixed(1)},${floorY.toFixed(1)} L${LEFT},${floorY.toFixed(1)} Z`;
  const ticks = [500, 1000, 1500, 2000, 3000, 4000].filter((v) => v < top);
  const low = points[lowIdx];

  function onMove(evt: React.MouseEvent<SVGSVGElement>) {
    const rect = evt.currentTarget.getBoundingClientRect();
    const px = ((evt.clientX - rect.left) / rect.width) * W;
    const i = Math.round(((px - LEFT) / (RIGHT - LEFT)) * (n - 1));
    setHover(i >= 0 && i < n ? i : null);
  }

  return (
    <div style={{ position: "relative" }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block", height: "auto" }} role="img" aria-label="Projected money available to spend over the next sixty days, at the usual pace and if nothing else is spent" onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={LEFT} y1={y(v)} x2={RIGHT} y2={y(v)} stroke={c.hairline} strokeWidth={1} />
            <text x={LEFT - 8} y={y(v) + 4} textAnchor="end" fontSize={11} fill={c.muted}>{money(v)}</text>
          </g>
        ))}
        <text x={LEFT - 8} y={floorY + 4} textAnchor="end" fontSize={11} fill={c.muted}>buffer</text>
        {bottom < 0 && <rect x={LEFT} y={floorY} width={RIGHT - LEFT} height={BASE - floorY} fill={c.bill} fillOpacity={0.06} />}
        <path d={paceArea} fill={c.safe} fillOpacity={0.12} />
        <path d={paceLine} fill="none" stroke={c.safe} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        <path d={pathOf("available", windowEnd)} fill="none" stroke={c.ink2} strokeWidth={1.25} strokeLinejoin="round" strokeOpacity={0.7} />
        <line x1={LEFT} y1={floorY} x2={RIGHT} y2={floorY} stroke={c.buffer} strokeWidth={1.5} />
        {paydays.map((i, k) => (
          <g key={i}>
            <line x1={x(i)} y1={floorY} x2={x(i)} y2={y(points[i].at_pace)} stroke={c.paycheck} strokeWidth={2} />
            <circle cx={x(i)} cy={y(points[i].at_pace)} r={5.5} fill={c.paycheck} stroke={c.surface} strokeWidth={2} />
            <text x={x(i) + 8} y={y(points[i].at_pace) - 10} fontSize={11} fontWeight={700} fill={c.ink}>
              {snapshot.next_paycheck.known === false ? "No payday on file" : k === 0 ? `Payday ${money(snapshot.next_paycheck.amount, { sign: true })}` : "Payday"}
            </text>
          </g>
        ))}
        {bills.map(({ idx, e }, k) => (
          <circle key={`${e.source}-${k}`} cx={x(idx)} cy={y(points[idx].at_pace)} r={4.5} fill={e.kind === "installment" || e.kind === "advance" ? c.debt : c.bill} stroke={c.surface} strokeWidth={2} />
        ))}
        <line x1={x(0)} y1={14} x2={x(0)} y2={BASE} stroke={c.ink} strokeWidth={1.5} />
        <text x={x(0)} y={10} textAnchor="middle" fontSize={11} fontWeight={700} fill={c.ink}>Today</text>
        {low && (
          <g>
            <circle cx={x(lowIdx)} cy={y(low.at_pace)} r={5} fill={c.surface} stroke={c.ink} strokeWidth={2} />
            <text x={x(lowIdx) + 8} y={y(low.at_pace) + 20} fontSize={11} fontWeight={600} fill={c.ink2}>
              {monthDay(low.date)} · {low.at_pace < 0 ? `${money(-low.at_pace)} under the buffer at your usual pace` : `${money(low.at_pace)} left at your usual pace`}
            </text>
          </g>
        )}
        {[0, 7, 14, 21, 28, 35, 42, 49, 56].filter((i) => i < n).map((i) => (
          <text key={i} x={x(i)} y={H - 8} textAnchor="middle" fontSize={11} fill={c.muted}>{monthDay(points[i].date)}</text>
        ))}
        {hover !== null && (
          <g>
            <line x1={x(hover)} y1={TOP} x2={x(hover)} y2={BASE} stroke={c.ink2} strokeWidth={1} />
            <circle cx={x(hover)} cy={y(points[hover].at_pace)} r={6} fill={c.safe} stroke={c.surface} strokeWidth={2} />
          </g>
        )}
      </svg>
      {hover !== null && (
        <div style={{ position: "absolute", left: `${(x(hover) / W) * 100}%`, top: 0, transform: `translateX(${hover > n / 2 ? "-105%" : "8px"})`, background: "var(--ink)", color: "var(--inverse)", borderRadius: 8, padding: "6px 10px", fontSize: 12, pointerEvents: "none", whiteSpace: "nowrap" }}>
          <strong>{monthDay(points[hover].date)}</strong> · at pace {money(points[hover].at_pace)} · bills only {money(points[hover].available)} · cash {money(points[hover].cash)}
        </div>
      )}
    </div>
  );
}
