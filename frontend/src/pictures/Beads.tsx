// Beads on a thread: between two paydays, each bill on its day, sized by its amount. A bill whose due day can
// move is a bead you can drag, or pick from the list under the picture with the keyboard; the engine judges the
// new day against the lowest balance and drafts the request. The app never sends it and never moves anything.
import { useRef, useState } from "react";
import { api } from "../api";
import { money, monthDay, ordinal, shortDate } from "../format";
import type { Bead } from "../paychecks";
import { Said } from "../components/State";
import { Tip, useColours, useTip } from "./common";

const W = 820;
const H = 250;

interface MoveCheck {
  name: string;
  from_day: number;
  to_day: number;
  low_before: number;
  low_after: number;
  change: number;
  verdict: "better" | "worse" | "same";
  instruction: string;
  draft: string;
}

function days(a: string, b: string): number {
  return Math.round((new Date(`${b}T12:00:00`).getTime() - new Date(`${a}T12:00:00`).getTime()) / 86400000);
}

function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

export function Beads({ spans }: { spans: { start: string; end: string; before?: boolean; items: Bead[] }[] }) {
  const colours = useColours();
  const [i, setI] = useState(spans.length > 1 ? 1 : 0);
  const [drag, setDrag] = useState<{ key: string; x: number } | null>(null);
  const [check, setCheck] = useState<MoveCheck | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pickBill, setPickBill] = useState<number | null>(null);
  const [pickDay, setPickDay] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const { ref, tip, show, hide } = useTip();
  const span = spans[i];
  if (!span) return null;
  const padL = 70;
  const padR = 70;
  const total = Math.max(1, days(span.start, span.end));
  const x = (iso: string) => padL + ((W - padL - padR) * Math.max(0, Math.min(total, days(span.start, iso)))) / total;
  const toDate = (px: number) => addDays(span.start, Math.round(((px - padL) / (W - padL - padR)) * total));
  const yl = 125;
  const svgX = (clientX: number) => {
    const box = svgRef.current?.getBoundingClientRect();
    return box ? ((clientX - box.left) / box.width) * W : 0;
  };
  async function tryDay(b: Bead, day: number) {
    if (!b.bill_id) return;
    if (day === b.due_day) {
      setError(`The ${ordinal(day)} is already its due day. Pick another day.`);
      return;
    }
    setBusy(true);
    setCopied(false);
    setError(null);
    try {
      setCheck(await api<MoveCheck>(`/api/v1/bills/${b.bill_id}/move_check?day=${day}`));
    } catch (e) {
      setCheck(null);
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function drop(b: Bead, px: number) {
    const when = toDate(Math.max(padL, Math.min(W - padR, px)));
    const day = Math.min(28, Number(when.slice(8, 10)));
    if (!b.bill_id || day === b.due_day) return;
    await tryDay(b, day);
  }
  const items = span.items;
  // The same choice without a pointer: each bill whose date can move, once, and a day to try.
  const movable = items.filter((b, k) => b.movable && b.bill_id && items.findIndex((o) => o.bill_id === b.bill_id) === k);
  const chosen = movable.find((b) => b.bill_id === pickBill) ?? movable[0];
  const day = pickDay ?? chosen?.due_day ?? 1;
  // Beads on the same day sit side by side; each label takes the first lane, above or below, that is free.
  const byDay: Record<string, number> = {};
  const sameDay: Record<string, number> = {};
  items.forEach((b) => (byDay[b.date] = (byDay[b.date] ?? 0) + 1));
  const lanes = [-1, 1, -2, 2, -3, 3];
  const laneEnd: Record<number, number> = {};
  const placed = items.map((b, k) => {
    const j = (sameDay[b.date] = (sameDay[b.date] ?? -1) + 1);
    const cx0 = x(b.date) + (j - (byDay[b.date] - 1) / 2) * 16;
    const half = Math.max(b.name.slice(0, 16).length, money(b.amount).length) * 3.1 + 4;
    const lane = lanes.find((l) => (laneEnd[l] ?? -Infinity) < cx0 - half) ?? lanes[k % lanes.length];
    laneEnd[lane] = cx0 + half;
    return { b, k, cx0, lane };
  });
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div role="tablist" aria-label="Which stretch between paydays" style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
        {spans.map((s, k) => (
          <button key={s.start} type="button" role="tab" aria-selected={i === k} onClick={() => { setI(k); setCheck(null); setError(null); setPickBill(null); setPickDay(null); }}
            style={{ font: "inherit", fontSize: 12, fontWeight: 800, padding: "5px 10px", borderRadius: 999, border: "1px solid var(--hairline)", cursor: "pointer", minHeight: 32, background: i === k ? "var(--ink)" : "var(--surface)", color: i === k ? "var(--inverse)" : "var(--ink-2)" }}>
            {s.before ? "Until payday" : `${monthDay(s.start)} to ${monthDay(s.end)}`}
          </button>
        ))}
      </div>
      <div ref={ref} style={{ position: "relative", overflowX: "auto", touchAction: drag ? "none" : "auto" }} onMouseLeave={hide}>
        <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={`Bills between ${shortDate(span.start)} and ${shortDate(span.end)}`} style={{ display: "block", maxWidth: 980, minWidth: 560, height: "auto", userSelect: "none" }}
          onPointerMove={(e) => drag && setDrag({ ...drag, x: svgX(e.clientX) })}
          onPointerUp={(e) => {
            if (!drag) return;
            const b = items.find((it, k) => `${it.source}-${k}` === drag.key);
            setDrag(null);
            if (b) drop(b, svgX(e.clientX));
          }}>
          <line x1={padL} x2={W - padR} y1={yl} y2={yl} stroke="var(--hairline)" strokeWidth={4} strokeLinecap="round" />
          {[span.start, span.end].map((d, k) => (
            <g key={`${k}-${d}`}>
              <rect x={x(d) - 7} y={yl - 7} width={14} height={14} rx={3} transform={`rotate(45 ${x(d)} ${yl})`} fill={k === 0 && span.before ? "var(--ink-2)" : "var(--paycheck)"} />
              <text x={x(d)} y={yl + 4} dx={k === 0 ? -14 : 14} fontSize="11" fontWeight="800" textAnchor={k === 0 ? "end" : "start"} fill="var(--ink)">{k === 0 && span.before ? "today" : monthDay(d)}</text>
            </g>
          ))}
          {placed.map(({ b, k, cx0, lane }) => {
            const key = `${b.source}-${k}`;
            const r = 6 + Math.sqrt(Math.max(0, b.amount)) / 2.6;
            const cx = drag?.key === key ? Math.max(padL, Math.min(W - padR, drag.x)) : cx0;
            const up = lane < 0;
            const level = Math.abs(lane) - 1;
            const nameY = up ? yl - 22 - r - level * 26 : yl + r + 18 + level * 26;
            const colour = colours[b.part];
            const draggable = b.movable && !!b.bill_id;
            return (
              <g key={key} style={{ cursor: draggable ? "grab" : "default" }}
                onPointerDown={(e) => { if (draggable) { (e.target as Element).setPointerCapture?.(e.pointerId); setDrag({ key, x: svgX(e.clientX) }); hide(); } }}
                onMouseMove={(e) => !drag && show(e, <div><strong>{b.name}</strong><div className="num">{money(b.amount, { cents: true })} · {shortDate(b.date)}</div><div style={{ color: "var(--muted)" }}>{b.via_card ? "charged to the card; paid with the card payment" : draggable ? "drag it, or pick it below, to try another due day" : "its due day is fixed"}</div></div>)}>
                <line x1={cx} x2={cx} y1={up ? yl - r : yl + r} y2={up ? nameY + 14 : nameY - 12} stroke="var(--hairline)" />
                <circle cx={cx} cy={yl} r={r} fill={b.via_card ? "var(--surface)" : colour} stroke={b.via_card ? colour : "var(--surface)"} strokeWidth={b.via_card ? 3 : 2} />
                {draggable && <circle cx={cx} cy={yl} r={r + 4} fill="none" stroke={colour} strokeWidth={1.2} strokeDasharray="2 3" />}
                <text x={cx} y={nameY} fontSize="10.5" textAnchor="middle" fill="var(--ink-2)">{b.name.length > 16 ? `${b.name.slice(0, 15)}…` : b.name}</text>
                <text x={cx} y={nameY + 12} fontSize="10.5" fontWeight="800" textAnchor="middle" fill="var(--ink)">{drag?.key === key ? monthDay(toDate(cx)) : money(b.amount)}</text>
              </g>
            );
          })}
        </svg>
        <Tip tip={tip} width={ref.current?.clientWidth ?? W} />
      </div>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>A bigger bead is a bigger bill. A dashed ring means the due day can move: drag it, or pick it below. A hollow bead is charged to the card.</div>
      {chosen && (
        <form onSubmit={(e) => { e.preventDefault(); tryDay(chosen, day); }} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
            <span className="lab">Bill whose date can move</span>
            <select className="input" value={chosen.bill_id ?? ""} onChange={(e) => { setPickBill(Number(e.target.value)); setPickDay(null); setCheck(null); setError(null); }} style={{ minHeight: 36, padding: "4px 10px" }}>
              {movable.map((b) => <option key={b.bill_id ?? b.source} value={b.bill_id ?? ""}>{b.name}{b.due_day ? ` (now the ${ordinal(b.due_day)})` : ""}</option>)}
            </select>
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 3 }}>
            <span className="lab">Try another day</span>
            <select className="input" value={day} onChange={(e) => setPickDay(Number(e.target.value))} style={{ minHeight: 36, padding: "4px 10px" }}>
              {Array.from({ length: 28 }, (_, k) => k + 1).map((d) => <option key={d} value={d}>the {ordinal(d)}{d === chosen.due_day ? " (now)" : ""}</option>)}
            </select>
          </label>
          <button type="submit" className="btn" disabled={busy} style={{ minHeight: 36, padding: "4px 12px" }}>{busy ? "Checking…" : "Check that day"}</button>
        </form>
      )}
      {busy && <div role="status" style={{ fontSize: 13, color: "var(--muted)" }}>Checking that day…</div>}
      {error && <Said text={error} tone="error" />}
      {check && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: 12, borderRadius: 10, background: "var(--surface-2)" }}>
          <strong>{check.name} on the {ordinal(check.to_day)} instead of the {ordinal(check.from_day)}</strong>
          <span className="num" style={{ fontSize: 13, color: check.verdict === "worse" ? "var(--critical-ink)" : check.verdict === "better" ? "var(--safe-ink)" : "var(--ink-2)" }}>
            {check.verdict === "same" ? "No difference to your lowest balance." : `${check.verdict === "better" ? "Better" : "Worse"}: your lowest balance ${check.verdict === "better" ? "rises" : "falls"} by ${money(Math.abs(check.change))}: from ${money(check.low_before)} to ${money(check.low_after)}.`}
          </span>
          {check.verdict === "better" && (
            <>
              <span style={{ fontSize: 13 }}>{check.instruction} The request, ready to copy:</span>
              <textarea readOnly className="input" value={check.draft} rows={4} style={{ width: "100%", fontSize: 12 }} aria-label="The request to the biller" />
              <div style={{ display: "flex", gap: 8 }}>
                <button type="button" className="btn" onClick={() => navigator.clipboard?.writeText(check.draft).then(() => setCopied(true), () => setCopied(false))}>{copied ? "Copied" : "Copy the request"}</button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
