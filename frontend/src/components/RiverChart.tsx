// The river: sixty days of what is free to spend, drawn from the engine's projection.
// Two series: at your usual spending (the band) and if nothing else is spent (the thin line). Neither is the bank
// balance (that is "In checking" in a day's detail), so the low point is "lowest left to spend", never a balance.
// Inline SVG, no chart library. It is drawn at the width it is given, so its words stay readable on a phone.
// A day's detail shows on hover, on a tap, or from the keyboard (focus the chart, then the arrow keys); the
// table twin shows every day as figures.
import { useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { amountClass, money, monthDay, parseIso, shortDate } from "../format";
import { palette } from "../looks";
import type { LookId } from "../looks";
import type { Snapshot } from "../types";
import { NoBalanceLine, dailyLine, type LinePoint } from "./SafeExplain";

interface Props {
  snapshot: Snapshot;
  look: LookId;
}

const WIDE = 1120;
const TOP = 20;

/** The river starts from what checking holds; with no balance from a bank there is no starting point, so it is not
 * drawn from a made-up zero. */
export function RiverChart({ snapshot, look }: Props) {
  const points = dailyLine(snapshot);
  if (!points) return <NoBalanceLine what="the next 60 days are drawn" />;
  return <RiverDrawn snapshot={snapshot} points={points} look={look} />;
}

function RiverDrawn({ snapshot, points, look }: Props & { points: LinePoint[] }) {
  const c = palette(look);
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(WIDE);
  const [sel, setSel] = useState<number | null>(null);
  const [pinned, setPinned] = useState(false);
  const [table, setTable] = useState(false);
  const pointing = useRef(false);

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => el.clientWidth && setWidth(el.clientWidth);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [table]);

  // One drawing unit is one pixel: the chart takes the width it has, and a narrow one is a little shorter.
  const W = Math.max(280, Math.min(WIDE, width));
  const narrow = W < 640;
  const H = Math.round(Math.min(330, Math.max(240, W * 0.3)));
  const LEFT = narrow ? 50 : 70;
  const RIGHT = W - (narrow ? 14 : 30);
  const BASE = H - 60;

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
    // With no paycheck on file the engine's dates are a frame, not paydays: none is marked.
    if (snapshot.next_paycheck.known === false) return [];
    // The engine lists the coming paydays by the paycheck's own cadence; the fourteen-day walk is only a fallback.
    const isos = snapshot.paydays?.length ? snapshot.paydays.map((p) => p.date) : [];
    if (isos.length === 0) {
      const first = parseIso(snapshot.next_paycheck.date);
      for (let k = 0; k < 5; k++) {
        const d = new Date(first);
        d.setDate(first.getDate() + 14 * k);
        isos.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`);
      }
    }
    return isos.map((iso) => points.findIndex((p) => p.date === iso)).filter((idx) => idx >= 0);
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
  // Date labels every week when they fit, every two weeks on a narrow screen.
  const every = ((RIGHT - LEFT) / Math.max(1, n - 1)) * 7 >= 48 ? 7 : 14;
  const dateTicks = Array.from({ length: Math.ceil(n / every) }, (_, k) => k * every).filter((i) => i < n);

  const whatHappens = (i: number): string => {
    const words: string[] = [];
    if (paydays.includes(i)) words.push(`payday${i === paydays[0] ? ` ${money(snapshot.next_paycheck.amount, { sign: true })}` : ""}`);
    for (const b of bills) if (b.idx === i) words.push(`${b.e.name} ${money(b.e.amount)}`);
    if (i === lowIdx) words.push("lowest left to spend");
    return words.join(", ");
  };
  const detail = (i: number): string => {
    const p = points[i];
    const parts = [`At your usual spending ${money(p.at_pace)}`];
    if (i < windowEnd) parts.push(`If you spend nothing else ${money(p.available)}`);
    parts.push(`In checking ${money(p.cash)}`);
    const happens = whatHappens(i);
    return `${shortDate(p.date)}${happens ? ` (${happens})` : ""} · ${parts.join(" · ")}`;
  };

  function dayAt(evt: PointerEvent<SVGSVGElement>): number | null {
    const rect = evt.currentTarget.getBoundingClientRect();
    const px = ((evt.clientX - rect.left) / rect.width) * W;
    const i = Math.round(((px - LEFT) / (RIGHT - LEFT)) * (n - 1));
    return i >= 0 && i < n ? i : null;
  }
  function onKey(e: KeyboardEvent<SVGSVGElement>) {
    const at = sel ?? 0;
    const to = { ArrowRight: at + 1, ArrowLeft: at - 1, ArrowUp: at + 1, ArrowDown: at - 1, PageDown: at + 7, PageUp: at - 7, Home: 0, End: n - 1 }[e.key];
    if (e.key === "Escape") {
      setSel(null);
      setPinned(false);
      return;
    }
    if (to === undefined) return;
    e.preventDefault();
    setSel(Math.max(0, Math.min(n - 1, to)));
    setPinned(true);
  }

  const labelW = (text: string) => text.length * 6.4;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {table ? (
        <div style={{ overflowX: "auto", maxHeight: 420, overflowY: "auto" }}>
          <table className="ledger">
            <thead><tr><th>Day</th><th style={{ textAlign: "right" }}>At your usual spending</th><th style={{ textAlign: "right" }}>If you spend nothing else</th><th style={{ textAlign: "right" }}>In checking</th><th>What happens</th></tr></thead>
            <tbody>
              {points.map((p, i) => (
                <tr key={p.date} style={{ fontWeight: i === lowIdx ? 800 : undefined }}>
                  <td style={{ whiteSpace: "nowrap" }}>{shortDate(p.date)}</td>
                  <td className="num" style={{ textAlign: "right" }}>{money(p.at_pace)}</td>
                  <td className="num" style={{ textAlign: "right" }}>{i < windowEnd ? money(p.available) : "—"}</td>
                  <td className="num" style={{ textAlign: "right" }}>{money(p.cash)}</td>
                  <td className={amountClass(whatHappens(i))} style={{ color: "var(--ink-2)" }}>{whatHappens(i)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div ref={box} style={{ position: "relative" }}>
          <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block", height: "auto", touchAction: "pan-y" }}
            role="slider" tabIndex={0} aria-label="What is free to spend, day by day, for the next sixty days. Use the arrow keys to move from day to day."
            aria-valuemin={0} aria-valuemax={Math.max(0, n - 1)} aria-valuenow={sel ?? 0} aria-valuetext={n ? detail(sel ?? 0) : "No days to show"}
            onPointerMove={(e) => { if (e.pointerType === "mouse" || e.buttons) setSel(dayAt(e)); }}
            onPointerDown={(e) => { pointing.current = true; if (e.pointerType !== "mouse") { setSel(dayAt(e)); setPinned(true); } }}
            onPointerLeave={(e) => { if (e.pointerType === "mouse" && !pinned) setSel(null); }}
            onFocus={() => {
              // Focus from a click or a tap keeps the day it chose; focus from the keyboard starts at today.
              if (pointing.current) pointing.current = false;
              else if (sel === null) { setSel(0); setPinned(true); }
            }}
            onBlur={() => { pointing.current = false; setPinned(false); setSel(null); }}
            onKeyDown={onKey}>
            {ticks.map((v) => (
              <g key={v}>
                <line x1={LEFT} y1={y(v)} x2={RIGHT} y2={y(v)} stroke={c.hairline} strokeWidth={1} />
                <text className="num" x={LEFT - 8} y={y(v) + 4} textAnchor="end" fontSize={11} fill={c.muted}>{money(v)}</text>
              </g>
            ))}
            <text x={LEFT - 8} y={floorY + 4} textAnchor="end" fontSize={11} fill={c.muted}>cushion</text>
            {bottom < 0 && <rect x={LEFT} y={floorY} width={RIGHT - LEFT} height={BASE - floorY} fill={c.bill} fillOpacity={0.06} />}
            <path d={paceArea} fill={c.safe} fillOpacity={0.12} />
            <path d={paceLine} fill="none" stroke={c.safe} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            <path d={pathOf("available", windowEnd)} fill="none" stroke={c.ink2} strokeWidth={1.25} strokeLinejoin="round" strokeOpacity={0.7} />
            <line x1={LEFT} y1={floorY} x2={RIGHT} y2={floorY} stroke={c.buffer} strokeWidth={1.5} />
            {paydays.map((i, k) => {
              const text = k === 0 ? `Payday ${money(snapshot.next_paycheck.amount, { sign: true })}` : "Payday";
              const flip = x(i) + 8 + labelW(text) > W;
              return (
                <g key={i}>
                  <line x1={x(i)} y1={floorY} x2={x(i)} y2={y(points[i].at_pace)} stroke={c.paycheck} strokeWidth={2} />
                  <circle cx={x(i)} cy={y(points[i].at_pace)} r={5.5} fill={c.paycheck} stroke={c.surface} strokeWidth={2} />
                  <text className={k === 0 ? "num" : undefined} x={flip ? x(i) - 8 : x(i) + 8} y={y(points[i].at_pace) - 10} textAnchor={flip ? "end" : "start"} fontSize={11} fontWeight={700} fill={c.ink}>{text}</text>
                </g>
              );
            })}
            {bills.map(({ idx, e }, k) => (
              <circle key={`${e.source}-${k}`} cx={x(idx)} cy={y(points[idx].at_pace)} r={4.5} fill={e.kind === "installment" || e.kind === "advance" ? c.debt : c.bill} stroke={c.surface} strokeWidth={2} />
            ))}
            <line x1={x(0)} y1={14} x2={x(0)} y2={BASE} stroke={c.ink} strokeWidth={1.5} />
            <text x={x(0)} y={10} textAnchor="start" fontSize={11} fontWeight={700} fill={c.ink}>Today</text>
            {low && (() => {
              const text = narrow
                ? `Lowest left ${monthDay(low.date)}: ${low.at_pace < 0 ? `${money(-low.at_pace)} under the cushion` : money(low.at_pace)}`
                : `Lowest left to spend ${shortDate(low.date)}: ${low.at_pace < 0 ? `${money(-low.at_pace)} under your safety cushion` : `${money(low.at_pace)} free`} at your usual spending`;
              const flip = x(lowIdx) + 8 + labelW(text) > W;
              return (
                <g>
                  <circle cx={x(lowIdx)} cy={y(low.at_pace)} r={5} fill={c.surface} stroke={c.ink} strokeWidth={2} />
                  <text className="num" x={flip ? Math.max(labelW(text), x(lowIdx) - 8) : x(lowIdx) + 8} y={Math.min(BASE + 16, y(low.at_pace) + 20)} textAnchor={flip ? "end" : "start"} fontSize={11} fontWeight={600} fill={c.ink2}>{text}</text>
                </g>
              );
            })()}
            {dateTicks.map((i) => (
              <text key={i} x={x(i)} y={H - 8} textAnchor={i === 0 ? "start" : "middle"} fontSize={11} fill={c.muted}>{monthDay(points[i].date)}</text>
            ))}
            {sel !== null && points[sel] && (
              <g>
                <line x1={x(sel)} y1={TOP} x2={x(sel)} y2={BASE} stroke={c.ink2} strokeWidth={1} />
                <circle cx={x(sel)} cy={y(points[sel].at_pace)} r={6} fill={c.safe} stroke={c.surface} strokeWidth={2} />
              </g>
            )}
          </svg>
          {sel !== null && points[sel] && (
            <div aria-hidden="true" style={{ position: "absolute", left: `${(x(sel) / W) * 100}%`, top: 0, transform: `translateX(${sel > n / 2 ? "calc(-100% - 8px)" : "8px"})`, width: "max-content", maxWidth: "min(300px, 75%)", background: "var(--ink)", color: "var(--inverse)", borderRadius: 8, padding: "6px 10px", fontSize: 12, pointerEvents: "none", whiteSpace: "normal", lineHeight: 1.4 }}>
              {detail(sel)}
            </div>
          )}
        </div>
      )}
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10, fontSize: 12, color: "var(--ink-2)" }}>
        {!table && <span>Tap a day, or click the chart and use the arrow keys, to see that day.</span>}
        <button type="button" className="btn" aria-pressed={table} onClick={() => { setTable((t) => !t); setSel(null); setPinned(false); }} style={{ marginLeft: "auto", minHeight: 32, padding: "4px 10px" }}>{table ? "Show the chart" : "Show as a table"}</button>
      </div>
    </div>
  );
}
