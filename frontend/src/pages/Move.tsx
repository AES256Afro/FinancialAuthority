// Moving home: the cost to get into your next home, and whether the money holds. First the home you are moving
// to: each cost to get in as a bar (the move-in check split into its parts, what comes back marked), then your
// balance day by day to a month after the move with every cost counted, then a place to add a cost. Below, folded,
// the what-if for a place you have not chosen: the verdict for a date, the first date that fits, each paycheck's
// load, what has to be true first, and saved scenarios. Every figure is the engine's; this page draws it.
import { ChevronRight, CircleCheck, CircleHelp, TriangleAlert, Undo2, type LucideIcon } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { ConfirmButton } from "../components/ConfirmButton";
import { Empty, Failed, Loading, Said } from "../components/State";
import { StatusPill } from "../components/Status";
import { localToday, money, monthDay, parseIso, parseMoney, shortDate } from "../format";
import { palette } from "../looks";
import { inkOn } from "../paychecks";
import { getPrefs } from "../prefs";
import type { CostPart, HomeCost, HomeRow, HousingOut, MoveCheckOut, MovePathPoint } from "../types";

type Note = { text: string; tone: "info" | "error" } | null;

const SMALL = { minHeight: 32, padding: "4px 10px" } as const;
const MUTED = { fontSize: 12, color: "var(--muted)" } as const;
const AMOUNT_HINT = "Type an amount like 120 or 1200.50";

/** A save's state: busy, and its outcome said next to its button. */
function useAction() {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  async function run(work: () => Promise<void>, done: string): Promise<boolean> {
    setBusy(true);
    setNote(null);
    try {
      await work();
      setNote({ text: done, tone: "info" });
      return true;
    } catch (e) {
      setNote({ text: `Couldn't save it: ${(e as Error).message}`, tone: "error" });
      return false;
    } finally {
      setBusy(false);
    }
  }
  return { busy, note, setNote, run, fail: (text: string) => setNote({ text, tone: "error" }) };
}

function Tag({ icon: Icon, children }: { icon: LucideIcon; children: ReactNode }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "2px 8px", borderRadius: 999, background: "var(--surface-2)", color: "var(--ink-2)", fontSize: 12, fontWeight: 700 }}>
      <Icon size={13} aria-hidden="true" style={{ flexShrink: 0 }} />{children}
    </span>
  );
}

function Field({ label, children, grow }: { label: string; children: ReactNode; grow?: boolean }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: grow ? "1 1 180px" : "0 1 auto", minWidth: 0 }}>
      <span className="lab">{label}</span>
      {children}
    </label>
  );
}

function Check({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label style={{ display: "flex", alignItems: "center", gap: 6, minHeight: 40, fontSize: 13 }}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} style={{ width: 18, height: 18 }} />
      {label}
    </label>
  );
}

// Steps of the bill colour toward the card's surface, so the parts of one cost read as one payment and every
// colour still comes from the look. What comes back is drawn in the saving colour, with its icon and its word.
const STEPS = [0, 0.34, 0.56, 0.18, 0.46, 0.68];
function toward(hex: string, base: string, t: number): string {
  const a = hex.replace("#", "");
  const b = base.replace("#", "");
  const mix = (i: number) => Math.round(parseInt(a.slice(i, i + 2), 16) * (1 - t) + parseInt(b.slice(i, i + 2), 16) * t).toString(16).padStart(2, "0");
  return `#${mix(0)}${mix(2)}${mix(4)}`;
}

/** The engine's sentence with its dates in words. */
function withDates(text: string): string {
  return text.replace(/\b(\d{4}-\d{2}-\d{2})\b/g, (_, iso: string) => shortDate(iso));
}

// ---- the costs to get in ---------------------------------------------------------------------------------------

interface CostBody {
  name: string;
  amount: number | null;
  due_on: string | null;
  refundable: boolean;
  parts: CostPart[];
  paid: boolean;
  note: string;
}

function costBody(c: HomeCost, patch: Partial<CostBody> = {}): CostBody {
  return { name: c.name, amount: c.amount, due_on: c.due_on, refundable: c.refundable, parts: c.parts, paid: c.paid, note: c.note, ...patch };
}

function CostForm({ h, cost, apply, onDone, onCancel }: { h: HomeRow; cost?: HomeCost; apply: (next: HousingOut) => void; onDone: (text: string) => void; onCancel?: () => void }) {
  const [name, setName] = useState(cost?.name ?? "");
  const [amount, setAmount] = useState(cost?.amount !== null && cost?.amount !== undefined ? String(cost.amount) : "");
  const [dueOn, setDueOn] = useState(cost?.due_on ?? "");
  const [refundable, setRefundable] = useState(cost?.refundable ?? false);
  const [paid, setPaid] = useState(cost?.paid ?? false);
  const [memo, setMemo] = useState(cost?.note ?? "");
  const act = useAction();

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return act.fail("Give it a name, such as Move-in check or Truck.");
    const value = amount.trim() ? parseMoney(amount) : null;
    if (amount.trim() && (value === null || value < 0)) return act.fail(AMOUNT_HINT);
    const body: CostBody = { name: name.trim(), amount: value, due_on: dueOn || null, refundable, parts: cost?.parts ?? [], paid, note: memo.trim() };
    const url = cost ? `/api/v1/housing/costs/${cost.id}` : `/api/v1/housing/${h.id}/costs`;
    const ok = await act.run(async () => apply(await api<HousingOut>(url, { method: cost ? "PUT" : "POST", body: JSON.stringify(body) })), "Saved.");
    if (!ok) return;
    const unsaid = value === null ? " Its amount is not known yet, so it is not counted until you add it." : !dueOn && !paid ? " It has no date yet, so it is not counted in your plan until you give it the day you pay." : "";
    onDone(`${cost ? "Saved" : "Added"} ${body.name}.${unsaid}`);
  }

  return (
    <form onSubmit={save} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
        <Field label="What it is" grow><input className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder="Move-in check, truck, application fee" /></Field>
        <Field label="Amount (blank: not known yet)"><input className="input num" value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" style={{ width: "min(100%, 140px)" }} /></Field>
        <Field label="The day you pay it"><input className="input" type="date" value={dueOn} onChange={(e) => setDueOn(e.target.value)} /></Field>
      </div>
      <div style={{ display: "flex", gap: "0 16px", flexWrap: "wrap", alignItems: "center" }}>
        <Check label="It comes back (refundable, like a deposit)" checked={refundable} onChange={setRefundable} />
        <Check label="Already paid" checked={paid} onChange={setPaid} />
      </div>
      <Field label="Note (optional)" grow><input className="input" value={memo} onChange={(e) => setMemo(e.target.value)} maxLength={300} /></Field>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button className="btn dark" type="submit" disabled={act.busy}>{act.busy ? "Saving…" : cost ? "Save" : "Add the cost"}</button>
        {onCancel && <button className="btn" type="button" onClick={onCancel}>Cancel</button>}
        <Said text={act.note?.tone === "error" ? act.note.text : null} tone="error" />
      </div>
    </form>
  );
}

/** One cost as a bar as long as its amount against the largest cost; a move-in check's parts are its segments. */
function CostBar({ cost, widest }: { cost: HomeCost; widest: number }) {
  const c = palette(getPrefs().look);
  const amount = cost.amount as number;
  const parts: CostPart[] = cost.parts.length ? cost.parts : [{ name: cost.name, amount, refundable: cost.refundable }];
  const fillOf = (p: CostPart, i: number) => (cost.paid ? c.buffer : p.refundable ? c.saving : toward(c.bill, c.surface, STEPS[i % STEPS.length]));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div role="img" aria-label={`${cost.name}: ${parts.map((p) => `${p.name} ${money(p.amount, { cents: true })}${p.refundable ? ", comes back" : ""}`).join("; ")}${cost.paid ? ". Already paid." : ""}`}
        style={{ width: `${Math.max(3, (amount / widest) * 100)}%`, display: "flex", gap: 2, height: 28 }}>
        {parts.map((p, i) => {
          const fill = fillOf(p, i);
          // Wide enough to carry its own words: a fifth of the widest bar or more.
          const labelled = p.amount / widest >= 0.2;
          return (
            <div key={i} title={`${p.name}: ${money(p.amount, { cents: true })}${p.refundable ? " (comes back)" : ""}`}
              style={{ flex: `${Math.max(p.amount, 0.0001)} 1 0px`, minWidth: 6, borderRadius: 4, background: fill, color: inkOn(fill), display: "flex", alignItems: "center", gap: 4, padding: labelled ? "0 7px" : 0, overflow: "hidden", whiteSpace: "nowrap", fontSize: 11, fontWeight: 800 }}>
              {labelled && <>{p.refundable && <Undo2 size={12} aria-hidden="true" style={{ flexShrink: 0 }} />}<span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{p.name} {money(p.amount)}</span></>}
            </div>
          );
        })}
      </div>
      {cost.parts.length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 220px), 1fr))", gap: "4px 14px", fontSize: 12 }}>
          {cost.parts.map((p, i) => (
            <div key={i} style={{ display: "flex", gap: 6, alignItems: "center", minWidth: 0 }}>
              <span className="sw" style={{ background: fillOf(p, i), margin: 0, flexShrink: 0 }} />
              <span style={{ flexGrow: 1, minWidth: 0, overflowWrap: "anywhere" }}>{p.name}{p.refundable && <span style={{ color: "var(--ink-2)", fontWeight: 700, whiteSpace: "nowrap" }}> · <Undo2 size={12} aria-hidden="true" style={{ display: "inline", verticalAlign: -2 }} /> comes back</span>}</span>
              <strong className="num" style={{ whiteSpace: "nowrap" }}>{money(p.amount, { cents: true })}</strong>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function CostRow({ cost, h, widest, apply }: { cost: HomeCost; h: HomeRow; widest: number; apply: (next: HousingOut) => void }) {
  const [editing, setEditing] = useState(false);
  const [asking, setAsking] = useState(false);
  const [typed, setTyped] = useState("");
  const act = useAction();

  async function addAmount(e: FormEvent) {
    e.preventDefault();
    const value = parseMoney(typed);
    if (value === null || value < 0) return act.fail(AMOUNT_HINT);
    const ok = await act.run(async () => apply(await api<HousingOut>(`/api/v1/housing/costs/${cost.id}`, { method: "PUT", body: JSON.stringify(costBody(cost, { amount: value })) })), `Saved: ${cost.name} is ${money(value, { cents: true })}.`);
    if (ok) setAsking(false);
  }
  async function remove() {
    apply(await api<HousingOut>(`/api/v1/housing/costs/${cost.id}`, { method: "DELETE" }));
  }

  if (editing) {
    return (
      <div style={{ borderTop: "1px solid var(--surface-2)", paddingTop: 10 }}>
        <CostForm h={h} cost={cost} apply={apply} onDone={(text) => { setEditing(false); act.setNote({ text, tone: "info" }); }} onCancel={() => setEditing(false)} />
      </div>
    );
  }
  const undated = cost.amount !== null && !cost.paid && !cost.due_on;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, borderTop: "1px solid var(--surface-2)", paddingTop: 10 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 160px", minWidth: 0 }}>
          <strong style={{ fontSize: 14, overflowWrap: "anywhere" }}>{cost.name}</strong>
          <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{cost.due_on ? `you pay it ${shortDate(cost.due_on)}` : "no date yet"}{cost.note ? ` · ${cost.note}` : ""}</div>
        </div>
        {cost.paid && <Tag icon={CircleCheck}>already paid</Tag>}
        {cost.refundable && <Tag icon={Undo2}>comes back</Tag>}
        {cost.amount === null ? (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 13, fontWeight: 700, color: "var(--warning-ink)" }}><CircleHelp size={14} aria-hidden="true" />Not known yet</span>
        ) : (
          <strong className="num" style={{ fontSize: 15 }}>{money(cost.amount, { cents: true })}</strong>
        )}
      </div>
      {cost.amount !== null && <CostBar cost={cost} widest={widest} />}
      {undated && (
        <div style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 12, fontWeight: 600, color: "var(--warning-ink)" }}>
          <TriangleAlert size={14} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} />
          <span>No date yet, so not counted: give it the day you pay, or mark it paid.</span>
        </div>
      )}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "flex-start" }}>
        {cost.amount === null && !asking && <button type="button" className="btn dark" style={SMALL} onClick={() => setAsking(true)}>Add the amount</button>}
        <button type="button" className="btn" style={SMALL} onClick={() => { setEditing(true); act.setNote(null); }}>{undated ? "Give it a date" : "Edit"}</button>
        <ConfirmButton label="Remove" style={SMALL} question={`Remove ${cost.name}? It stops counting.`} yes="Yes, remove it" no="Keep it" onConfirm={remove} />
      </div>
      {asking && cost.amount === null && (
        <form onSubmit={addAmount} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
          <Field label={`${cost.name}: amount`}><input className="input num" value={typed} onChange={(e) => setTyped(e.target.value)} inputMode="decimal" style={{ width: "min(100%, 140px)" }} autoFocus /></Field>
          <button className="btn dark" type="submit" disabled={act.busy}>{act.busy ? "Saving…" : "Save the amount"}</button>
          <button className="btn" type="button" onClick={() => setAsking(false)}>Cancel</button>
        </form>
      )}
      <Said text={act.note?.text ?? null} tone={act.note?.tone} />
    </div>
  );
}

function CostPicture({ h, on, apply }: { h: HomeRow; on: string | null; apply: (next: HousingOut) => void }) {
  const known = h.costs.filter((x) => x.amount !== null);
  const unknown = h.costs.filter((x) => x.amount === null);
  // Placement only: every bar is drawn against the largest cost.
  const widest = Math.max(1, ...known.map((x) => x.amount as number));
  return (
    <section className="card" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 10 }}>
      <h2 style={{ margin: 0, fontSize: 18, fontWeight: 800, overflowWrap: "anywhere" }}>Getting into {h.name}{on ? ` on ${shortDate(on)}` : ""}</h2>
      {h.costs.length === 0 ? (
        <Empty card={false}>No costs yet. Add the first one below: the move-in check, the fees, the truck.</Empty>
      ) : (
        <>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 20px", alignItems: "baseline" }}>
            <span><strong className="num" style={{ fontSize: 26, fontWeight: 800 }}>{money(h.costs_total, { cents: true })}</strong> <span style={{ fontSize: 13, color: "var(--ink-2)" }}>cost to get in, still to pay</span></span>
            <span style={{ fontSize: 13 }}>Counted in your plan: <strong className="num">{money(h.costs_counted, { cents: true })}</strong></span>
            {h.refundable_in_costs > 0 && <span style={{ fontSize: 13, display: "inline-flex", alignItems: "center", gap: 4 }}><Undo2 size={14} aria-hidden="true" /><span><strong className="num">{money(h.refundable_in_costs, { cents: true })}</strong> of it comes back</span></span>}
            {h.costs_paid_before > 0 && <span style={{ fontSize: 13, display: "inline-flex", alignItems: "center", gap: 4 }}><CircleCheck size={14} aria-hidden="true" /><span><strong className="num">{money(h.costs_paid_before, { cents: true })}</strong> already paid</span></span>}
          </div>
          {h.costs_missing.length > 0 && (
            <div style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 12, fontWeight: 600, color: "var(--warning-ink)" }}>
              <CircleHelp size={14} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} />
              <span>Not known yet, so not in the cost to get in: {h.costs_missing.join(", ")}.</span>
            </div>
          )}
          {h.costs_undated.length > 0 && (
            <div style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 12, fontWeight: 600, color: "var(--warning-ink)" }}>
              <TriangleAlert size={14} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} />
              <span>No date yet, so not counted in your plan: {h.costs_undated.join(", ")}. Give each the day you pay, or mark it paid.</span>
            </div>
          )}
          {known.map((cost) => <CostRow key={cost.id} cost={cost} h={h} widest={widest} apply={apply} />)}
          {unknown.map((cost) => <CostRow key={cost.id} cost={cost} h={h} widest={widest} apply={apply} />)}
        </>
      )}
    </section>
  );
}

function AddCost({ h, apply }: { h: HomeRow; apply: (next: HousingOut) => void }) {
  const [said, setSaid] = useState<string | null>(null);
  const [round, setRound] = useState(0);
  return (
    <section className="card" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>Add a cost to get in</h2>
        <span style={MUTED}>A one-time cost of moving: it is counted on the day you pay it, until you mark it paid.</span>
      </div>
      <CostForm key={round} h={h} apply={apply} onDone={(text) => { setSaid(text); setRound((r) => r + 1); }} />
      <Said text={said} />
    </section>
  );
}

// ---- whether the money holds -----------------------------------------------------------------------------------

function PathChart({ path, moveOn, low }: { path: MovePathPoint[]; moveOn: string; low: MovePathPoint | null }) {
  const c = palette(getPrefs().look);
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  const [sel, setSel] = useState<number | null>(null);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => el.clientWidth && setWidth(el.clientWidth);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // One drawing unit is one pixel, so the words stay readable on a phone.
  const W = Math.max(280, Math.min(900, width));
  const H = 210;
  const LEFT = 58;
  const RIGHT = W - 12;
  const TOP = 22;
  const BASE = H - 26;
  const n = path.length;
  const hi = Math.max(1, ...path.map((p) => p.at_pace));
  const lo = Math.min(0, ...path.map((p) => p.at_pace));
  const x = (i: number) => LEFT + (i * (RIGHT - LEFT)) / Math.max(1, n - 1);
  const y = (v: number) => BASE - ((v - lo) / (hi - lo)) * (BASE - TOP);
  const line = path.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.at_pace).toFixed(1)}`).join(" ");
  const moveIdx = path.findIndex((p) => p.date >= moveOn);
  const lowIdx = low ? path.findIndex((p) => p.date === low.date) : -1;
  const labelW = (text: string) => text.length * 6.2;
  const said = (p: MovePathPoint) => `${shortDate(p.date)}: ${p.at_pace < 0 ? `${money(-p.at_pace)} under your safety cushion` : `${money(p.at_pace)} above your safety cushion`}`;

  function dayAt(evt: PointerEvent<SVGSVGElement>): number | null {
    const rect = evt.currentTarget.getBoundingClientRect();
    const px = ((evt.clientX - rect.left) / rect.width) * W;
    const i = Math.round(((px - LEFT) / (RIGHT - LEFT)) * (n - 1));
    return i >= 0 && i < n ? i : null;
  }
  function onKey(e: KeyboardEvent<SVGSVGElement>) {
    const at = sel ?? 0;
    const to = ({ ArrowRight: at + 1, ArrowLeft: at - 1, Home: 0, End: n - 1 } as Record<string, number>)[e.key];
    if (e.key === "Escape") return setSel(null);
    if (to === undefined) return;
    e.preventDefault();
    setSel(Math.max(0, Math.min(n - 1, to)));
  }

  const moveText = `Move day ${shortDate(moveOn)}`;
  const lowText = low ? `Lowest ${money(low.at_pace)} · ${shortDate(low.date)}` : "";
  const flipMove = moveIdx >= 0 && x(moveIdx) + labelW(moveText) + 6 > W;
  const flipLow = lowIdx >= 0 && x(lowIdx) + labelW(lowText) + 10 > W;
  return (
    <div ref={box} style={{ position: "relative" }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block", height: "auto", touchAction: "pan-y" }}
        role="slider" tabIndex={0} aria-label="Your balance day by day, above your safety cushion, to a month after the move. Use the arrow keys to move from day to day."
        aria-valuemin={0} aria-valuemax={Math.max(0, n - 1)} aria-valuenow={sel ?? 0} aria-valuetext={n ? said(path[sel ?? 0]) : "No days to show"}
        onPointerMove={(e) => setSel(dayAt(e))} onPointerDown={(e) => setSel(dayAt(e))} onPointerLeave={(e) => { if (e.pointerType === "mouse") setSel(null); }}
        onBlur={() => setSel(null)} onKeyDown={onKey}>
        <line x1={LEFT} x2={RIGHT} y1={y(hi)} y2={y(hi)} stroke={c.hairline} strokeWidth={1} />
        <text x={LEFT - 6} y={y(hi) + 4} textAnchor="end" fontSize={11} fill={c.muted}>{money(hi)}</text>
        {lo < 0 && (
          <g>
            <line x1={LEFT} x2={RIGHT} y1={y(lo)} y2={y(lo)} stroke={c.hairline} strokeWidth={1} />
            <text x={LEFT - 6} y={y(lo) + 4} textAnchor="end" fontSize={11} fill={c.muted}>{money(lo)}</text>
          </g>
        )}
        <line x1={LEFT} x2={RIGHT} y1={y(0)} y2={y(0)} stroke={c.buffer} strokeWidth={1.5} />
        <text x={LEFT - 6} y={y(0) + 4} textAnchor="end" fontSize={11} fill={c.muted}>cushion</text>
        {moveIdx >= 0 && (
          <g>
            <line x1={x(moveIdx)} x2={x(moveIdx)} y1={TOP - 6} y2={BASE} stroke={c.housing} strokeWidth={1.5} strokeDasharray="4 3" />
            <text x={flipMove ? x(moveIdx) - 5 : x(moveIdx) + 5} y={TOP - 8} textAnchor={flipMove ? "end" : "start"} fontSize={11} fontWeight={800} fill={c.ink}>{moveText}</text>
          </g>
        )}
        <path d={line} fill="none" stroke={c.safe} strokeWidth={1.75} strokeLinejoin="round" strokeLinecap="round" />
        {low && lowIdx >= 0 && (
          <g>
            <circle cx={x(lowIdx)} cy={y(low.at_pace)} r={4.5} fill={c.surface} stroke={c.ink} strokeWidth={2} />
            <text x={flipLow ? x(lowIdx) - 8 : x(lowIdx) + 8} y={y(low.at_pace) + 16 <= BASE - 4 ? y(low.at_pace) + 16 : y(low.at_pace) - 10} textAnchor={flipLow ? "end" : "start"} fontSize={11} fontWeight={700} fill={c.ink2}>{lowText}</text>
          </g>
        )}
        {n > 0 && <text x={LEFT} y={H - 6} textAnchor="start" fontSize={11} fill={c.muted}>{monthDay(path[0].date)}</text>}
        {n > 1 && <text x={RIGHT} y={H - 6} textAnchor="end" fontSize={11} fill={c.muted}>{monthDay(path[n - 1].date)}</text>}
        {sel !== null && path[sel] && (
          <g>
            <line x1={x(sel)} x2={x(sel)} y1={TOP} y2={BASE} stroke={c.ink2} strokeWidth={1} />
            <circle cx={x(sel)} cy={y(path[sel].at_pace)} r={5} fill={c.safe} stroke={c.surface} strokeWidth={2} />
          </g>
        )}
      </svg>
      {sel !== null && path[sel] && (
        <div aria-hidden="true" style={{ position: "absolute", left: `${(x(sel) / W) * 100}%`, top: 0, transform: `translateX(${sel > n / 2 ? "calc(-100% - 8px)" : "8px"})`, width: "max-content", maxWidth: "min(280px, 75%)", background: "var(--ink)", color: "var(--inverse)", borderRadius: 8, padding: "6px 10px", fontSize: 12, pointerEvents: "none", lineHeight: 1.4 }}>
          {said(path[sel])}
        </div>
      )}
    </div>
  );
}

function MoveCheck({ homeId, version }: { homeId: number; version: string }) {
  const check = useApi<MoveCheckOut>(`/api/v1/housing/${homeId}/move`);
  const first = useRef(true);
  useEffect(() => {
    // A cost was added or changed above: ask the engine again.
    if (first.current) {
      first.current = false;
      return;
    }
    check.reload();
  }, [version]); // eslint-disable-line react-hooks/exhaustive-deps
  const d = check.data;
  return (
    <section className="card" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>Whether the money holds</h2>
        <span style={MUTED}>Your balance day by day from today to a month after the move, at your usual spending, with every dated cost to get in and the new home's bills counted. Tap the chart, or use the arrow keys, to see a day.</span>
      </div>
      {check.error && !d ? (
        <Failed error={check.error} what="the check against your paychecks" onRetry={check.reload} card={false} />
      ) : !d ? (
        <Loading what="the check against your paychecks" card={false} />
      ) : (
        <>
          <div><StatusPill status={d.status}>{withDates(d.word)}</StatusPill></div>
          {d.path.length > 1 ? <PathChart path={d.path} moveOn={d.move_on} low={d.low} /> : <Empty card={false} to="/paystubs#paycheck" action="Add your paycheck">No days to draw yet.</Empty>}
          {d.caveats.length > 0 && (
            <ul style={{ margin: 0, paddingLeft: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 4 }}>
              {d.caveats.map((text) => (
                <li key={text} style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 12, fontWeight: 600, color: "var(--warning-ink)" }}>
                  <TriangleAlert size={14} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} /><span>{text}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

// ---- compare another place (the what-if) ------------------------------------------------------------------------

interface OneOff {
  name: string;
  amount: string;
  date: string;
}

interface Recurring {
  name: string;
  amount: string;
  due_day: string;
}

interface PathPoint {
  date: string;
  at_pace: number;
}

interface Result {
  name: string;
  move_date: string;
  verdict: "passes" | "short";
  summary: string;
  first_passes: string | null;
  baseline: { low: { date: string; value: number }; path: PathPoint[] };
  scenario: { low: { date: string; value: number }; path: PathPoint[]; shortfall: number; short_days: number };
  windows: { start: string; end: string; paycheck: number; total: number; baseline_total: number; delta: number; before_payday?: boolean; items: { name: string; amount: number; date: string; scenario?: string; kind: string }[] }[];
  monthly_change: number;
  one_offs_total: number;
  old_rent: number;
  new_rent: number;
  buffer: number;
  cash: number;
  caveats: string[];
  not_advice: string;
}

interface ReadinessItem {
  key: string;
  label: string;
  value: string | number;
  target: string | number;
  met: boolean;
  projected?: string | null;
  note: string;
  link: string;
}

interface Readiness {
  items: ReadinessItem[];
  met: number;
  of: number;
  ready: boolean;
}

interface Saved {
  id: number;
  name: string;
  spec: { move_date: string; new_rent: number | null; overlap_months: number; one_offs: { name: string; amount: number; date: string }[]; recurring: { name: string; amount: number; due_day: number | null }[] };
}

/** A date some days after today, as YYYY-MM-DD in local time. */
function daysFromToday(days: number): string {
  const d = parseIso(localToday());
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function Chart({ r }: { r: Result }) {
  const c = palette(getPrefs().look);
  const w = 760;
  const h = 220;
  const left = 46;
  const right = 12;
  const top = 14;
  const base = h - 28;
  const n = Math.max(2, r.scenario.path.length);
  const all = [...r.baseline.path, ...r.scenario.path].map((p) => p.at_pace);
  const hi = Math.max(1, ...all);
  const lo = Math.min(0, ...all);
  const x = (i: number) => left + (i * (w - left - right)) / (n - 1);
  const y = (v: number) => base - ((v - lo) / (hi - lo)) * (base - top);
  const line = (path: PathPoint[]) => path.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.at_pace).toFixed(1)}`).join(" ");
  const moveIdx = r.scenario.path.findIndex((p) => p.date >= r.move_date);
  const lowIdx = r.scenario.path.findIndex((p) => p.date === r.scenario.low.date);
  const ticks = [0, Math.floor(n / 4), Math.floor(n / 2), Math.floor((3 * n) / 4), n - 1];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" role="img" aria-label={`Your balance day by day at your usual spending, with and without the move, over ${n} days`} style={{ display: "block", maxWidth: 900 }}>
      <line x1={left} x2={w - right} y1={y(0)} y2={y(0)} stroke={c.ink} strokeWidth={1.2} />
      <text x={left - 6} y={y(0) + 4} textAnchor="end" fontSize={10} fill={c.ink2}>0</text>
      <text x={left - 6} y={y(hi) + 4} textAnchor="end" fontSize={10} fill={c.ink2}>{money(hi)}</text>
      {lo < 0 && <text x={left - 6} y={y(lo) + 4} textAnchor="end" fontSize={10} fill={c.ink2}>{money(lo)}</text>}
      <path d={line(r.baseline.path)} fill="none" stroke={c.buffer} strokeWidth={2} strokeDasharray="5 4" />
      <path d={line(r.scenario.path)} fill="none" stroke={r.verdict === "passes" ? c.safe : c.bill} strokeWidth={2.4} />
      {moveIdx >= 0 && (
        <g>
          <line x1={x(moveIdx)} x2={x(moveIdx)} y1={top} y2={base} stroke={c.paycheck} strokeWidth={1.5} strokeDasharray="3 3" />
          <text x={x(moveIdx) + 4} y={top + 10} fontSize={11} fontWeight={800} fill="var(--paycheck-ink)">move {shortDate(r.move_date)}</text>
        </g>
      )}
      {lowIdx >= 0 && (
        <g>
          <circle cx={x(lowIdx)} cy={y(r.scenario.low.value)} r={5} fill={r.verdict === "passes" ? c.safe : c.bill} stroke={c.surface} strokeWidth={2} />
          <text x={Math.min(x(lowIdx) + 8, w - 150)} y={y(r.scenario.low.value) - 8} fontSize={11} fontWeight={800} fill={c.ink}>lowest {money(r.scenario.low.value)} · {shortDate(r.scenario.low.date)}</text>
        </g>
      )}
      {ticks.map((i) => <text key={i} x={x(i)} y={h - 8} textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"} fontSize={10} fill={c.ink2}>{shortDate(r.scenario.path[i]?.date ?? r.move_date)}</text>)}
      <g fontSize={11} fontWeight={700} fill={c.ink2}>
        <line x1={w - 250} x2={w - 226} y1={top + 4} y2={top + 4} stroke={c.buffer} strokeWidth={2} strokeDasharray="5 4" /><text x={w - 220} y={top + 8}>as things are</text>
        <line x1={w - 130} x2={w - 106} y1={top + 4} y2={top + 4} stroke={r.verdict === "passes" ? c.safe : c.bill} strokeWidth={2.4} /><text x={w - 100} y={top + 8}>with the move</text>
      </g>
    </svg>
  );
}

function WhatIf() {
  const [name, setName] = useState("New place");
  const [moveDate, setMoveDate] = useState(() => daysFromToday(45));
  const [newRent, setNewRent] = useState("");
  const [overlap, setOverlap] = useState("0");
  const [oneOffs, setOneOffs] = useState<OneOff[]>([{ name: "Application and admin fees", amount: "", date: "" }, { name: "Truck and help", amount: "", date: "" }]);
  const [recurring, setRecurring] = useState<Recurring[]>([{ name: "Renter's insurance", amount: "", due_day: "1" }]);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { data: saved, reload: reloadSaved } = useApi<{ items: Saved[] }>("/api/v1/scenarios");
  const [ready, setReady] = useState<Readiness | null>(null);
  const [readyError, setReadyError] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  const [checkedNote, setCheckedNote] = useState<string | null>(null);

  /** The scenario as the engine reads it, or null (with the reason said) when an amount cannot be read. */
  function spec() {
    const typed = [newRent, ...oneOffs.map((o) => o.amount), ...recurring.map((r) => r.amount)].filter((t) => t.trim());
    if (typed.some((t) => parseMoney(t) === null)) {
      setError("Type an amount like 120 or 1200.50");
      return null;
    }
    return {
      name,
      move_date: moveDate,
      new_rent: newRent.trim() ? parseMoney(newRent) ?? undefined : undefined,
      overlap_months: Number(overlap) || 0,
      one_offs: oneOffs.filter((o) => o.amount.trim()).map((o) => ({ name: o.name || "One-off", amount: parseMoney(o.amount) as number, date: o.date || moveDate })),
      recurring: recurring.filter((r) => r.amount.trim()).map((r) => ({ name: r.name || "Added bill", amount: parseMoney(r.amount) as number, due_day: r.due_day ? Number(r.due_day) : undefined })),
    };
  }
  async function loadReadiness(q: URLSearchParams) {
    try {
      setReady(await api<Readiness>(`/api/v1/scenarios/readiness?${q.toString()}`));
      setReadyError(null);
    } catch (e) {
      setReadyError((e as Error).message);
    }
  }
  async function run() {
    setError(null);
    const body = spec();
    if (!body) return;
    setBusy(true);
    try {
      const r = await api<Result>("/api/v1/scenarios/run", { method: "POST", body: JSON.stringify(body) });
      setResult(r);
      const q = new URLSearchParams({ move_date: moveDate, ...(body.new_rent !== undefined ? { new_rent: String(body.new_rent) } : {}) });
      await loadReadiness(q);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    setError(null);
    setSavedNote(null);
    const body = spec();
    if (!body) return;
    try {
      await api("/api/v1/scenarios", { method: "POST", body: JSON.stringify(body) });
      setSavedNote({ tone: "info", text: `Saved as "${name}". It is listed under Saved.` });
      reloadSaved();
    } catch (e) {
      setSavedNote({ tone: "error", text: `Couldn't save it: ${(e as Error).message}` });
    }
  }
  async function markChecked() {
    setCheckedNote(null);
    try {
      await api("/api/v1/scenarios/reconciled", { method: "POST" });
      await loadReadiness(new URLSearchParams({ move_date: moveDate }));
    } catch (e) {
      setCheckedNote(`Couldn't save that: ${(e as Error).message}`);
    }
  }
  function load(s: Saved) {
    setName(s.name);
    setMoveDate(s.spec.move_date);
    setNewRent(s.spec.new_rent !== null && s.spec.new_rent !== undefined ? String(s.spec.new_rent) : "");
    setOverlap(String(s.spec.overlap_months ?? 0));
    setOneOffs(s.spec.one_offs.length ? s.spec.one_offs.map((o) => ({ name: o.name, amount: String(o.amount), date: o.date })) : [{ name: "", amount: "", date: "" }]);
    setRecurring(s.spec.recurring.length ? s.spec.recurring.map((r) => ({ name: r.name, amount: String(r.amount), due_day: r.due_day ? String(r.due_day) : "" })) : [{ name: "Renter's insurance", amount: "", due_day: "1" }]);
  }
  useEffect(() => {
    loadReadiness(new URLSearchParams({ move_date: moveDate }));
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {result && (
        <section className="card" style={{ padding: "18px 22px", borderTop: `5px solid ${result.verdict === "passes" ? "var(--safe)" : "var(--critical)"}`, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 14, flexWrap: "wrap" }}>
            <div style={{ fontSize: 26, fontWeight: 800 }}>{result.verdict === "passes" ? "Fits" : "Short"} · {result.name} on {shortDate(result.move_date)}</div>
            {result.verdict === "short" && result.first_passes && <div style={{ fontSize: 14, fontWeight: 700, color: "var(--safe-ink)" }}>first date that fits: {shortDate(result.first_passes)}</div>}
          </div>
          <div style={{ fontSize: 14 }}>{result.summary}</div>
          <div style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 13, color: "var(--ink-2)" }}>
            <span>monthly change <strong className="num" style={{ color: result.monthly_change > 0 ? "var(--critical-ink)" : "var(--safe-ink)" }}>{result.monthly_change >= 0 ? "+" : ""}{money(result.monthly_change)}</strong></span>
            <span>one-offs <strong className="num">{money(result.one_offs_total)}</strong></span>
            <span>rent {money(result.old_rent)} → {money(result.new_rent)}</span>
            <span>lowest balance with the move {money(result.scenario.low.value)} on {shortDate(result.scenario.low.date)}</span>
            <span>lowest balance without the move {money(result.baseline.low.value)} on {shortDate(result.baseline.low.date)}</span>
          </div>
          <Chart r={result} />
          {result.caveats.length > 0 && <div style={{ fontSize: 12, color: "var(--warning-ink)", fontWeight: 600, display: "flex", gap: 6, alignItems: "flex-start" }}><TriangleAlert size={14} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} /><span>{result.caveats.join(" ")}</span></div>}
        </section>
      )}

      <section className="card" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}><h3 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>The scenario</h3><span style={{ fontSize: 12, color: "var(--muted)" }}>the new place's own figures; leave blank what you do not know yet</span></div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Name</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} style={{ width: "min(100%, 160px)" }} /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Move on</span><input className="input" type="date" value={moveDate} onChange={(e) => setMoveDate(e.target.value)} /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">New rent a month</span><input className="input num" value={newRent} onChange={(e) => setNewRent(e.target.value)} inputMode="decimal" style={{ width: 110 }} placeholder="same" /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Old rent still paid</span>
            <select className="input" value={overlap} onChange={(e) => setOverlap(e.target.value)}><option value="0">no overlap</option><option value="1">one more month</option><option value="2">two more months</option></select>
          </label>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px), 1fr))", gap: 12 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span className="lab">One-off costs</span>
            {oneOffs.map((o, i) => (
              <div key={i} style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "flex-end" }}>
                <label style={{ display: "flex", flexDirection: "column", gap: 2, flex: "1 1 120px" }}><span className="lab">What</span><input className="input" value={o.name} onChange={(e) => setOneOffs(oneOffs.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} /></label>
                <label style={{ display: "flex", flexDirection: "column", gap: 2 }}><span className="lab">Amount</span><input className="input num" value={o.amount} onChange={(e) => setOneOffs(oneOffs.map((x, j) => (j === i ? { ...x, amount: e.target.value } : x)))} inputMode="decimal" style={{ width: 90 }} /></label>
                <label style={{ display: "flex", flexDirection: "column", gap: 2 }}><span className="lab">Date (blank: move day)</span><input className="input" type="date" value={o.date} onChange={(e) => setOneOffs(oneOffs.map((x, j) => (j === i ? { ...x, date: e.target.value } : x)))} style={{ width: 150 }} /></label>
              </div>
            ))}
            <button className="btn" type="button" onClick={() => setOneOffs([...oneOffs, { name: "", amount: "", date: "" }])} style={{ alignSelf: "flex-start" }}>Another one-off</button>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span className="lab">Bills that start with the move (a month)</span>
            {recurring.map((r, i) => (
              <div key={i} style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "flex-end" }}>
                <label style={{ display: "flex", flexDirection: "column", gap: 2, flex: "1 1 120px" }}><span className="lab">What</span><input className="input" value={r.name} onChange={(e) => setRecurring(recurring.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} /></label>
                <label style={{ display: "flex", flexDirection: "column", gap: 2 }}><span className="lab">Amount</span><input className="input num" value={r.amount} onChange={(e) => setRecurring(recurring.map((x, j) => (j === i ? { ...x, amount: e.target.value } : x)))} inputMode="decimal" style={{ width: 90 }} /></label>
                <label style={{ display: "flex", flexDirection: "column", gap: 2 }}><span className="lab">Due day</span><input className="input num" value={r.due_day} onChange={(e) => setRecurring(recurring.map((x, j) => (j === i ? { ...x, due_day: e.target.value } : x)))} inputMode="numeric" style={{ width: 64 }} /></label>
              </div>
            ))}
            <button className="btn" type="button" onClick={() => setRecurring([...recurring, { name: "", amount: "", due_day: "" }])} style={{ alignSelf: "flex-start" }}>Another bill</button>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <button className="btn dark" type="button" disabled={busy} onClick={run}>{busy ? "Running…" : "Run it against my checks"}</button>
          <button className="btn" type="button" disabled={busy} onClick={save}>Save this scenario</button>
          {savedNote && <Said text={savedNote.text} tone={savedNote.tone} />}
          {saved && saved.items.length > 0 && (
            <span style={{ fontSize: 12, color: "var(--ink-2)", display: "inline-flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
              Saved: {saved.items.map((s) => <button key={s.id} className="btn" type="button" onClick={() => load(s)} style={{ padding: "4px 10px", minHeight: 32 }}>{s.name} · {shortDate(s.spec.move_date)}</button>)}
            </span>
          )}
        </div>
        <Said text={error} tone="error" />
      </section>

      {result && (
        <section className="card" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}><h3 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>Each paycheck with the move</h3><span style={{ fontSize: 12, color: "var(--muted)" }}>what each paycheck has to pay, and how much more than today</span></div>
          <div style={{ overflowX: "auto" }}>
          <table className="ledger" style={{ fontSize: 13 }}>
            <thead><tr><th>Paycheck</th><th style={{ textAlign: "right" }}>Brings in</th><th style={{ textAlign: "right" }}>Has to pay</th><th style={{ textAlign: "right" }}>Change</th><th>Biggest bills</th></tr></thead>
            <tbody>
              {result.windows.slice(0, 9).map((w) => (
                <tr key={w.start} style={{ background: w.delta > 0 ? "var(--warning-soft)" : undefined }}>
                  <td>{w.before_payday ? "Before the next paycheck" : shortDate(w.start)}<div style={{ fontSize: 11, color: "var(--muted)" }}>through {shortDate(w.end)}</div></td>
                  <td className="num" style={{ textAlign: "right" }}>{w.before_payday ? money(result.cash) : money(w.paycheck)}</td>
                  <td className="num" style={{ textAlign: "right" }}>{money(w.total)}</td>
                  <td className="num" style={{ textAlign: "right", fontWeight: 800, color: w.delta > 0 ? "var(--critical-ink)" : w.delta < 0 ? "var(--safe-ink)" : "var(--muted)" }}>{w.delta === 0 ? "—" : `${w.delta > 0 ? "+" : ""}${money(w.delta)}`}</td>
                  <td style={{ fontSize: 12 }}>{w.items.slice(0, 4).map((i) => `${i.name} ${money(i.amount)}${i.scenario ? " (new)" : ""}`).join(" · ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </section>
      )}

      {readyError && !ready && <Failed error={readyError} what="what has to be true first" onRetry={() => loadReadiness(new URLSearchParams({ move_date: moveDate }))} />}
      {ready && (
        <section className="card" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>What has to be true first</h3>
            <span style={{ fontSize: 12, color: ready.ready ? "var(--safe-ink)" : "var(--ink-2)", fontWeight: 700 }}>{ready.met} of {ready.of} met</span>
          </div>
          {ready.items.map((i) => (
            <div key={i.key} style={{ display: "grid", gridTemplateColumns: "auto minmax(0, 1fr) auto", gap: 10, alignItems: "start", fontSize: 13, borderTop: "1px solid var(--surface-2)", paddingTop: 6 }}>
              <StatusPill status={i.met ? "good" : "warn"}>{i.met ? "Met" : "Not yet"}</StatusPill>
              <div style={{ minWidth: 0 }}>
                <div><strong>{i.label}</strong> <span style={{ color: "var(--ink-2)" }}>· {typeof i.value === "number" ? money(i.value) : i.value} · target {typeof i.target === "number" ? money(i.target) : i.target}{i.projected && !i.met ? ` · on track for ${shortDate(i.projected)}` : ""}</span></div>
                {i.note && <div style={{ fontSize: 12, color: "var(--muted)" }}>{i.note}</div>}
              </div>
              {i.key === "reconciled" && !i.met ? <span /> : <Link to={i.link} aria-label={`Go to: ${i.label}`} className="btn" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", minHeight: 32, padding: "2px 10px", fontSize: 12, whiteSpace: "nowrap" }}>Go →</Link>}
            </div>
          ))}
          {ready.items.some((i) => i.key === "reconciled" && !i.met) && (
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <button className="btn" type="button" onClick={markChecked}>I checked one paycheck by hand and it matched the bank</button>
              <Said text={checkedNote} tone="error" />
            </div>
          )}
        </section>
      )}
      {result && <div style={{ fontSize: 11, color: "var(--muted)" }}>{result.not_advice}</div>}
    </div>
  );
}

export function Move({ onChanged }: { onChanged?: () => void }) {
  const state = useApi<HousingOut>("/api/v1/housing");
  const [fresh, setFresh] = useState<HousingOut | null>(null);
  const [comparing, setComparing] = useState(false);
  useEffect(() => setFresh(null), [state.data]);
  const data = fresh ?? state.data;
  const up = data && data.upcoming_id !== null ? data.homes.find((h) => h.id === data.upcoming_id) ?? null : null;
  const apply = (next: HousingOut) => {
    setFresh(next);
    onChanged?.();
  };
  // What the check against your paychecks depends on: when a cost changes, it asks again.
  const version = up ? up.costs.map((c) => `${c.id}:${c.amount}:${c.due_on}:${c.paid}`).join("|") + `|${up.starts_on}` : "";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, letterSpacing: "-0.01em" }}>Moving home</h1>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>The cost to get into your next home, and whether the money holds.</div>
      </div>

      {state.error && !data ? (
        <Failed error={state.error} what="your next home" onRetry={state.reload} />
      ) : !data ? (
        <Loading what="your next home" />
      ) : !up ? (
        <Empty to="/housing" action="Add your next home on Housing">No move planned. Add the home you are moving to on Housing, with the day you move in, and the cost to get into it shows here.</Empty>
      ) : (
        <>
          <CostPicture h={up} on={data.move?.on ?? up.starts_on} apply={apply} />
          <MoveCheck homeId={up.id} version={version} />
          <AddCost h={up} apply={apply} />
          <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
            {up.first_due ? <>The rent and fees from {shortDate(up.first_due)} are on <Link to="/housing">Housing</Link>.</> : <>The rent and fees are on <Link to="/housing">Housing</Link>.</>}
          </div>
        </>
      )}

      <section style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <h2 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>
            <button type="button" aria-expanded={comparing} onClick={() => setComparing(!comparing)} style={{ display: "inline-flex", alignItems: "center", gap: 6, background: "none", border: 0, padding: 0, minHeight: 32, font: "inherit", color: "var(--ink)", cursor: "pointer" }}>
              <ChevronRight size={16} aria-hidden="true" style={{ transform: comparing ? "rotate(90deg)" : "none", transition: "transform .15s" }} />
              Compare another place
            </button>
          </h2>
          <span style={MUTED}>A what-if for a place you have not chosen, over your real paychecks and bills. Nothing in your bills list or transactions changes.</span>
        </div>
        {comparing && <WhatIf />}
      </section>
      {up && data && <div style={{ fontSize: 11, color: "var(--muted)" }}>{data.not_advice}</div>}
    </div>
  );
}
