// Housing: the home you live in and the one you are moving to, each with its rent or mortgage, the fees paid with
// it, the bills paid on their own, the lease's dates and notice, the deposit, the utilities and where each comes
// from, and the documents kept with them. Every amount is the engine's: it counts housing as bills in safe to
// spend, Cover and the outlook. This page draws and edits what you entered; it adds nothing up itself.
import { ChevronRight, CircleHelp, Droplet, Droplets, Flame, House, Phone, Plug, Trash2, TriangleAlert, Truck, Undo2, Wifi, Zap, type LucideIcon } from "lucide-react";
import { Fragment, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { ConfirmButton } from "../components/ConfirmButton";
import { Documents } from "../components/Documents";
import { Empty, Failed, Loading, Said, useJumpToHash } from "../components/State";
import { StatusPill } from "../components/Status";
import { localToday, money, ordinal, parseIso, parseMoney, shortDate } from "../format";
import { palette } from "../looks";
import { inkOn } from "../paychecks";
import { getPrefs } from "../prefs";
import type { HomeCharge, HomeNotice, HomeRow, HousingMove, HousingOut, UtilitiesOut, UtilityRow } from "../types";

type Note = { text: string; tone: "info" | "error" } | null;

const SMALL = { minHeight: 32, padding: "4px 10px" } as const;
const ROW = { display: "inline-flex", alignItems: "center", gap: 4 } as const;
const MUTED = { fontSize: 12, color: "var(--muted)" } as const;
const AMOUNT_HINT = "Type an amount like 120 or 1200.50";
const DAY_HINT = "Type a day of the month from 1 to 31.";
const DAYS_HINT = "Type a whole number of days, like 30.";

const KIND_WORD: Record<string, string> = { rent: "Rent", mortgage: "Mortgage", other: "Other housing" };
const CHARGE_KINDS: [string, string][] = [
  ["rent", "Rent"],
  ["pet", "Pet rent or a pet fee"],
  ["fee", "A fee from the property"],
  ["utility", "A utility the property bills"],
  ["insurance", "Insurance"],
  ["principal_interest", "Loan payment (principal and interest)"],
  ["escrow", "Escrow (property tax and insurance)"],
  ["pmi", "Mortgage insurance (PMI)"],
  ["hoa", "HOA dues"],
  ["parking", "Parking"],
  ["other", "Something else"],
];
const CHARGE_WORD: Record<string, string> = Object.fromEntries(CHARGE_KINDS);
const SERVICE_WORD: Record<string, string> = { electric: "Electricity", water: "Water", gas: "Gas", internet: "Internet", trash: "Trash", sewer: "Sewer", phone: "Phone", other: "Another service" };
const SERVICE_ICON: Record<string, LucideIcon> = { electric: Zap, water: Droplet, gas: Flame, internet: Wifi, trash: Trash2, sewer: Droplets, phone: Phone, other: Plug };
// The engine's words for where a bill comes from; used for the choices until GET /utilities answers.
const BILLED_WORD: Record<string, string> = { direct: "billed to you by the provider", landlord: "paid with the rent", submetered: "measured at your unit and billed by a billing company", included: "included in the rent" };
const NOTICE_RING: Record<HomeNotice["status"], string> = { good: "var(--safe)", warn: "var(--warning)", crit: "var(--critical)" };

interface Ctx {
  data: HousingOut;
  utilities: UtilitiesOut | null;
  /** A write the engine answered with the whole housing overview. */
  apply: (next: HousingOut) => void;
  /** A write answered with something else: ask again. */
  reload: () => void;
  /** A document was added or removed: every list of documents asks again. */
  docsChanged: () => void;
  docTick: number;
}

/** A date with its weekday and year, for a lease's start and end. */
function dateYear(iso: string): string {
  return `${shortDate(iso)}, ${iso.slice(0, 4)}`;
}

/** A day of the month typed by a person: 1 to 31, else null. */
function dayOf(text: string): number | null {
  const clean = text.trim();
  if (!/^\d{1,2}$/.test(clean)) return null;
  const n = Number(clean);
  return n >= 1 && n <= 31 ? n : null;
}

/** A whole number of days typed by a person, else null. */
function daysOf(text: string, most: number): number | null {
  const clean = text.trim();
  if (!/^\d{1,3}$/.test(clean)) return null;
  const n = Number(clean);
  return n <= most ? n : null;
}

function payeeOf(h: HomeRow): string {
  return h.payee || (h.kind === "mortgage" ? "the lender" : "the property");
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

// ---- small pieces ---------------------------------------------------------------------------------------------

function Card({ title, sub, action, children, level = 3 }: { title: ReactNode; sub?: ReactNode; action?: ReactNode; children: ReactNode; level?: 2 | 3 }) {
  const H = level === 2 ? "h2" : "h3";
  return (
    <section className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 8, flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 180px", minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
          <H style={{ margin: 0, fontSize: level === 2 ? 15 : 14, fontWeight: 700 }}>{title}</H>
          {sub && <span style={MUTED}>{sub}</span>}
        </div>
        {action}
      </div>
      {children}
    </section>
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

function Row({ children }: { children: ReactNode }) {
  return <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>{children}</div>;
}

function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "minmax(0, 2fr) minmax(0, 3fr)", gap: "6px 12px", fontSize: 13 }}>
      {rows.map(([k, v]) => (
        <Fragment key={k}>
          <dt style={{ color: "var(--ink-2)" }}>{k}</dt>
          <dd style={{ margin: 0, fontWeight: 600, overflowWrap: "anywhere" }}>{v}</dd>
        </Fragment>
      ))}
    </dl>
  );
}

function Tag({ icon: Icon, children }: { icon: LucideIcon; children: ReactNode }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "2px 8px", borderRadius: 999, background: "var(--surface-2)", color: "var(--ink-2)", fontSize: 12, fontWeight: 700, maxWidth: "100%" }}>
      <Icon size={13} aria-hidden="true" style={{ flexShrink: 0 }} />
      <span style={{ minWidth: 0 }}>{children}</span>
    </span>
  );
}

function Unknown({ text = "Not known yet" }: { text?: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 13, fontWeight: 700, color: "var(--warning-ink)" }}>
      <CircleHelp size={14} aria-hidden="true" />{text}
    </span>
  );
}

function EditToggle({ open, onClick, label = "Edit" }: { open: boolean; onClick: () => void; label?: string }) {
  return <button type="button" className="btn" aria-expanded={open} onClick={onClick} style={SMALL}>{open ? "Close" : label}</button>;
}

/** A save's state: busy, and its outcome said next to its button. */
function useAction() {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  async function run(work: () => Promise<void>, done: string, failWord = "Couldn't save it"): Promise<boolean> {
    setBusy(true);
    setNote(null);
    try {
      await work();
      setNote({ text: done, tone: "info" });
      return true;
    } catch (e) {
      setNote({ text: `${failWord}: ${(e as Error).message}`, tone: "error" });
      return false;
    } finally {
      setBusy(false);
    }
  }
  return { busy, note, setNote, run, fail: (text: string) => setNote({ text, tone: "error" }) };
}

// The steps of one colour, mixed toward the card's surface, so the parts paid together read as one payment and
// every colour still comes from the look.
const STEPS = [0, 0.34, 0.56, 0.18, 0.46, 0.68];
function toward(hex: string, base: string, t: number): string {
  const a = hex.replace("#", "");
  const b = base.replace("#", "");
  const mix = (i: number) => Math.round(parseInt(a.slice(i, i + 2), 16) * (1 - t) + parseInt(b.slice(i, i + 2), 16) * t).toString(16).padStart(2, "0");
  return `#${mix(0)}${mix(2)}${mix(4)}`;
}

// ---- the home's body, sent whole on every change (a PUT replaces) ----------------------------------------------

interface HomeBody {
  name: string;
  kind: string;
  payee: string;
  starts_on: string | null;
  ends_on: string | null;
  first_due: string | null;
  due_day: number;
  grace_days: number;
  late_fee: number;
  autopay: boolean;
  notice_days: number | null;
  notice_given_on: string | null;
  lease_ends_on: string | null;
  deposit: number;
  deposit_back_on: string | null;
  pay_from_account_id: number | null;
  note: string;
}

function homeBody(h: HomeRow, patch: Partial<HomeBody> = {}): HomeBody {
  return {
    name: h.name,
    kind: h.kind,
    payee: h.payee,
    starts_on: h.starts_on,
    ends_on: h.ends_on,
    first_due: h.first_due_entered,
    due_day: h.due_day,
    grace_days: h.grace_days,
    late_fee: h.late_fee,
    autopay: h.autopay,
    notice_days: h.notice_days,
    notice_given_on: h.notice_given_on,
    lease_ends_on: h.lease_ends_on,
    deposit: h.deposit,
    deposit_back_on: h.deposit_back_on,
    pay_from_account_id: h.pay_from_account_id,
    note: h.note,
    ...patch,
  };
}

async function putHome(ctx: Ctx, h: HomeRow, patch: Partial<HomeBody>): Promise<void> {
  ctx.apply(await api<HousingOut>(`/api/v1/housing/${h.id}`, { method: "PUT", body: JSON.stringify(homeBody(h, patch)) }));
}

// ---- the move, across the top ----------------------------------------------------------------------------------

interface Point {
  key: string;
  date: string;
  label: string;
  ring: string;
  notice?: HomeNotice;
}

function Dot({ n, ring }: { n: number; ring: string }) {
  return (
    <span aria-hidden="true" style={{ width: 24, height: 24, borderRadius: 12, background: "var(--surface)", border: `3px solid ${ring}`, boxSizing: "border-box", display: "grid", placeItems: "center", fontSize: 11, fontWeight: 800, color: "var(--ink)", flexShrink: 0 }}>{n}</span>
  );
}

function MoveTimeline({ data, move }: { data: HousingOut; move: HousingMove }) {
  const up = data.homes.find((x) => x.id === data.upcoming_id) ?? null;
  const firstCost = move.first_cost;
  const candidates: (Point | null)[] = [
    { key: "today", date: data.as_of, label: "Today", ring: "var(--ink)" },
    move.old_notice ? { key: "notice", date: move.old_notice.by, label: move.old_notice.given_on ? `Notice to ${move.from}` : `Give ${move.from} written notice by`, ring: NOTICE_RING[move.old_notice.status], notice: move.old_notice } : null,
    firstCost ? { key: "cost", date: firstCost.on, label: `${firstCost.name} due${firstCost.amount !== null ? `: ${money(firstCost.amount, { cents: true })}` : ""}`, ring: "var(--bill)" } : null,
    move.on ? { key: "move", date: move.on, label: `Move into ${move.to}`, ring: "var(--housing)" } : null,
    move.old_ends_on ? { key: "end", date: move.old_ends_on, label: `The lease at ${move.from} ends`, ring: "var(--muted)" } : null,
    up?.first_due ? { key: "first", date: up.first_due, label: `First regular payment at ${move.to}`, ring: "var(--housing)" } : null,
  ];
  const points = candidates.filter((p): p is Point => p !== null).sort((a, b) => a.date.localeCompare(b.date));
  const t0 = parseIso(points[0].date).getTime();
  const t1 = parseIso(points[points.length - 1].date).getTime();
  // Placement only: where each date sits between the first and the last. Points close together take turns on
  // the line, above it and below it, so none hides another.
  let prev = -100;
  let prevLane = 0;
  const placed = points.map((p) => {
    const pct = t1 > t0 ? ((parseIso(p.date).getTime() - t0) / (t1 - t0)) * 100 : 50;
    const lane = pct - prev < 7 ? (prevLane + 1) % 3 : 0;
    prev = pct;
    prevLane = lane;
    return { ...p, pct, lane };
  });
  const lanes = placed.some((p) => p.lane === 2) ? 3 : placed.some((p) => p.lane === 1) ? 2 : 1;
  return (
    <Card level={2} title={`Your move: ${move.from} to ${move.to}`}
      sub={<>Each month changes by <strong className="num" style={{ color: "var(--ink)" }}>{money(move.monthly_change, { sign: true, cents: true })}</strong>. Still to pay to get in: <strong className="num" style={{ color: "var(--ink)" }}>{money(move.costs_total, { cents: true })}</strong>{move.costs_missing.length ? ` and ${move.costs_missing.join(", ")} (not known yet)` : ""}. The costs are on <Link to="/move">Moving home</Link>.</>}>
      <div role="img" aria-label={`The move in dates: ${points.map((p) => `${p.label}, ${shortDate(p.date)}`).join("; ")}`} style={{ position: "relative", height: lanes === 1 ? 28 : lanes === 2 ? 52 : 76, margin: "0 14px" }}>
        <div style={{ position: "absolute", left: 0, right: 0, top: lanes === 1 ? 13 : 37, height: 2, background: "var(--hairline)" }} />
        {placed.map((p, i) => (
          <span key={p.key} style={{ position: "absolute", left: `${p.pct}%`, top: lanes === 1 ? 2 : p.lane === 1 ? 0 : p.lane === 2 ? 38 : 26, transform: "translateX(-50%)", display: "flex", flexDirection: p.lane === 2 ? "column-reverse" : "column", alignItems: "center" }}>
            <Dot n={i + 1} ring={p.ring} />
            {p.lane !== 0 && <span aria-hidden="true" style={{ width: 2, height: 12, background: "var(--hairline)" }} />}
          </span>
        ))}
      </div>
      <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 230px), 1fr))", gap: "10px 16px" }}>
        {points.map((p, i) => (
          <li key={p.key} style={{ display: "flex", gap: 8, alignItems: "flex-start", minWidth: 0 }}>
            <Dot n={i + 1} ring={p.ring} />
            <div style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 3, alignItems: "flex-start" }}>
              <span style={{ fontSize: 13, fontWeight: 700, overflowWrap: "anywhere" }}>{p.label}</span>
              <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{shortDate(p.date)}</span>
              {p.notice && <StatusPill status={p.notice.status}>{p.notice.word}</StatusPill>}
            </div>
          </li>
        ))}
      </ol>
    </Card>
  );
}

// ---- the picture: what the home costs each month ---------------------------------------------------------------

interface Seg {
  key: string;
  name: string;
  amount: number | null;
  estimate: boolean;
  fill: string;
}

function amountWord(s: Seg): string {
  return s.amount === null ? "amount not known yet" : `${s.estimate ? "about " : ""}${money(s.amount, { cents: true })}`;
}

function Bar({ groups, total }: { groups: Seg[][]; total: number }) {
  const shown = groups.filter((g) => g.length > 0);
  const all = shown.flat();
  return (
    <div role="img" aria-label={all.map((s) => `${s.name}: ${amountWord(s)}`).join(", ")} style={{ display: "flex", alignItems: "stretch", height: 34, gap: 2, minWidth: 0 }}>
      {shown.map((g, gi) => (
        <Fragment key={gi}>
          {gi > 0 && <div aria-hidden="true" style={{ flex: "0 0 12px" }} />}
          {g.map((s) => {
            if (s.amount === null) {
              return (
                <div key={s.key} title={`${s.name}: amount not known yet`} style={{ flex: "0 0 40px", borderRadius: 5, border: "1.5px dashed var(--ink-2)", boxSizing: "border-box", background: "repeating-linear-gradient(135deg, transparent 0 5px, var(--hairline) 5px 8px)", display: "grid", placeItems: "center", color: "var(--ink-2)" }}>
                  <CircleHelp size={15} aria-hidden="true" />
                </div>
              );
            }
            // Wide enough to carry its own words: a fifth of the month or more.
            const labelled = s.amount / Math.max(1, total) >= 0.2;
            return (
              <div key={s.key} title={`${s.name}: ${amountWord(s)}`} style={{ flex: `${Math.max(s.amount, 0.0001)} 1 0px`, minWidth: 6, borderRadius: 5, background: s.fill, color: inkOn(s.fill), display: "flex", alignItems: "center", padding: labelled ? "0 8px" : 0, overflow: "hidden", whiteSpace: "nowrap", textOverflow: "ellipsis", fontSize: 11, fontWeight: 800 }}>
                {labelled ? `${s.name} ${money(s.amount)}` : ""}
              </div>
            );
          })}
        </Fragment>
      ))}
    </div>
  );
}

function Legend({ title, segs }: { title: string; segs: Seg[] }) {
  if (segs.length === 0) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
      <span className="lab">{title}</span>
      {segs.map((s) => (
        <div key={s.key} style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 12, minWidth: 0 }}>
          {s.amount === null ? (
            <span aria-hidden="true" style={{ width: 12, height: 12, borderRadius: 3, border: "1.5px dashed var(--ink-2)", boxSizing: "border-box", flexShrink: 0 }} />
          ) : (
            <span className="sw" style={{ background: s.fill, margin: 0, flexShrink: 0 }} />
          )}
          <span style={{ flexGrow: 1, minWidth: 0, overflowWrap: "anywhere" }}>{s.name}</span>
          {s.amount === null ? <Unknown text="amount not known yet" /> : <strong className="num" style={{ whiteSpace: "nowrap" }}>{amountWord(s)}</strong>}
        </div>
      ))}
    </div>
  );
}

function MonthlyPicture({ h }: { h: HomeRow }) {
  const c = palette(getPrefs().look);
  const withRent: Seg[] = h.charges.filter((x) => x.pays_to === "home").map((x, i) => ({ key: `c${x.id}`, name: x.name, amount: x.amount, estimate: x.estimate, fill: toward(c.housing, c.surface, STEPS[i % STEPS.length]) }));
  const ownBilled = h.utilities.filter((u) => (u.billed === "direct" || u.billed === "submetered") && !(u.amount === null && u.moves_with_you));
  const own: Seg[] = [
    ...h.charges.filter((x) => x.pays_to !== "home").map((x) => ({ key: `c${x.id}`, name: x.name, amount: x.amount, estimate: x.estimate })),
    ...ownBilled.map((u) => ({ key: `u${u.id}`, name: u.provider ? `${u.what} (${u.provider})` : u.what, amount: u.amount, estimate: u.estimate })),
  ].map((s, i) => ({ ...s, fill: toward(c.bill, c.surface, STEPS[i % STEPS.length]) }));
  const moving = h.utilities.filter((u) => u.moves_with_you && u.amount === null);
  const word = h.kind === "mortgage" ? "mortgage" : "rent";
  return (
    <Card title="Each month" sub={`What ${h.name} costs a month, as every figure in the app counts it.`}>
      {withRent.length === 0 && own.length === 0 ? (
        <Empty card={false}>No amounts yet. Add the {word} under “Paid with the {word} each month” below, and the picture fills in.</Empty>
      ) : (
        <>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 20px", alignItems: "baseline" }}>
            <span><strong className="num" style={{ fontSize: 24, fontWeight: 800 }}>{money(h.monthly_total, { cents: true })}</strong> <span style={{ fontSize: 13, color: "var(--ink-2)" }}>a month in all</span></span>
            <span style={{ fontSize: 13, display: "inline-flex", alignItems: "baseline", gap: 6 }}><span className="sw" style={{ background: c.housing, margin: 0 }} /><span><strong className="num">{money(h.to_landlord_monthly, { cents: true })}</strong> paid to {payeeOf(h)} on the {ordinal(h.due_day)}</span></span>
            <span style={{ fontSize: 13, display: "inline-flex", alignItems: "baseline", gap: 6 }}><span className="sw" style={{ background: c.bill, margin: 0 }} /><span><strong className="num">{money(h.own_bills_monthly, { cents: true })}</strong> paid on their own</span></span>
          </div>
          <Bar groups={[withRent, own]} total={h.monthly_total} />
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 240px), 1fr))", gap: 12 }}>
            <Legend title={`Paid with the ${word}`} segs={withRent} />
            <Legend title="Paid on their own" segs={own} />
          </div>
        </>
      )}
      {h.missing.length > 0 && (
        <div style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 12, fontWeight: 600, color: "var(--warning-ink)" }}>
          <TriangleAlert size={14} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} />
          <span>Not known yet, so not counted: {h.missing.join(", ")}. Add each amount below; a dashed box in the bar marks each one.</span>
        </div>
      )}
      {moving.length > 0 && (
        <div style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 12, color: "var(--ink-2)" }}>
          <Truck size={14} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} />
          <span>Moves with you, so its bill is already on your bills list: {moving.map((u) => (u.provider ? `${u.what} (${u.provider})` : u.what)).join(", ")}.</span>
        </div>
      )}
    </Card>
  );
}

// ---- paid with the rent, and paid on their own -----------------------------------------------------------------

interface ChargeBody {
  name: string;
  kind: string;
  amount: number | null;
  estimate: boolean;
  pays_to: string;
  payee: string;
  due_day: number | null;
  starts_on: string | null;
  ends_on: string | null;
  note: string;
}

function chargeBody(c: HomeCharge, patch: Partial<ChargeBody> = {}): ChargeBody {
  return { name: c.name, kind: c.kind, amount: c.amount, estimate: c.estimate, pays_to: c.pays_to, payee: c.payee, due_day: c.due_day, starts_on: c.starts_on, ends_on: c.ends_on, note: c.note, ...patch };
}

function ChargeForm({ h, ctx, paysTo, c, onDone, onCancel }: { h: HomeRow; ctx: Ctx; paysTo: "home" | "other"; c?: HomeCharge; onDone: (text: string) => void; onCancel: () => void }) {
  const firstWithRent = paysTo === "home" && !h.charges.some((x) => x.pays_to === "home");
  const [name, setName] = useState(c?.name ?? (firstWithRent ? (h.kind === "mortgage" ? "Mortgage payment" : "Rent") : ""));
  const [kind, setKind] = useState(c?.kind ?? (firstWithRent ? (h.kind === "mortgage" ? "principal_interest" : "rent") : paysTo === "home" ? "fee" : "insurance"));
  const [where, setWhere] = useState<string>(c?.pays_to ?? paysTo);
  const [amount, setAmount] = useState(c?.amount !== null && c?.amount !== undefined ? String(c.amount) : "");
  const [estimate, setEstimate] = useState(c?.estimate ?? false);
  const [payee, setPayee] = useState(c?.payee ?? "");
  const [dueDay, setDueDay] = useState(c?.due_day ? String(c.due_day) : "");
  const [startsOn, setStartsOn] = useState(c?.starts_on ?? "");
  const [endsOn, setEndsOn] = useState(c?.ends_on ?? "");
  const act = useAction();

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return act.fail("Give it a name, such as Pet rent.");
    const value = amount.trim() ? parseMoney(amount) : null;
    if (amount.trim() && (value === null || value < 0)) return act.fail(AMOUNT_HINT);
    const day = dueDay.trim() ? dayOf(dueDay) : null;
    if (dueDay.trim() && day === null) return act.fail(DAY_HINT);
    const body: ChargeBody = { name: name.trim(), kind, amount: value, estimate, pays_to: where, payee: payee.trim(), due_day: day, starts_on: startsOn || null, ends_on: endsOn || null, note: c?.note ?? "" };
    const url = c ? `/api/v1/housing/charges/${c.id}` : `/api/v1/housing/${h.id}/charges`;
    const ok = await act.run(async () => ctx.apply(await api<HousingOut>(url, { method: c ? "PUT" : "POST", body: JSON.stringify(body) })), "Saved.");
    if (ok) onDone(c ? `Saved ${body.name}.` : `Added ${body.name}.${value === null ? " Its amount is not known yet, so it is not counted until you add it." : ""}`);
  }

  return (
    <form onSubmit={save} style={{ display: "flex", flexDirection: "column", gap: 8, background: "var(--surface-2)", borderRadius: 10, padding: "10px 12px" }}>
      <Row>
        <Field label="What it is" grow><input className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} /></Field>
        <Field label="Kind">
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value)} style={{ maxWidth: "100%" }}>
            {CHARGE_KINDS.map(([k, w]) => <option key={k} value={k}>{w}</option>)}
          </select>
        </Field>
      </Row>
      <Row>
        <Field label="Amount a month (blank: not known yet)"><input className="input num" value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" style={{ width: "min(100%, 140px)" }} /></Field>
        <Check label="The amount is an estimate" checked={estimate} onChange={setEstimate} />
      </Row>
      {c && (
        <Field label="How it is paid">
          <select className="input" value={where} onChange={(e) => setWhere(e.target.value)}>
            <option value="home">With the {h.kind === "mortgage" ? "mortgage" : "rent"}, to {payeeOf(h)}</option>
            <option value="other">On its own, to someone else</option>
          </select>
        </Field>
      )}
      {where === "other" && (
        <Row>
          <Field label="Paid to" grow><input className="input" value={payee} onChange={(e) => setPayee(e.target.value)} maxLength={120} /></Field>
          <Field label={`Due day (blank: the ${ordinal(h.due_day)})`}><input className="input num" value={dueDay} onChange={(e) => setDueDay(e.target.value)} inputMode="numeric" style={{ width: "min(100%, 90px)" }} /></Field>
        </Row>
      )}
      <Row>
        <Field label="Starts (blank: with the home)"><input className="input" type="date" value={startsOn} onChange={(e) => setStartsOn(e.target.value)} /></Field>
        <Field label="Ends (blank: when you move out)"><input className="input" type="date" value={endsOn} onChange={(e) => setEndsOn(e.target.value)} /></Field>
      </Row>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button className="btn dark" type="submit" disabled={act.busy}>{act.busy ? "Saving…" : c ? "Save" : "Add it"}</button>
        <button className="btn" type="button" onClick={onCancel}>Cancel</button>
        <Said text={act.note?.tone === "error" ? act.note.text : null} tone="error" />
      </div>
    </form>
  );
}

function ChargeRow({ c, h, ctx }: { c: HomeCharge; h: HomeRow; ctx: Ctx }) {
  const [editing, setEditing] = useState(false);
  const [asking, setAsking] = useState(false);
  const [typed, setTyped] = useState("");
  const act = useAction();

  async function addAmount(e: FormEvent) {
    e.preventDefault();
    const value = parseMoney(typed);
    if (value === null || value < 0) return act.fail(AMOUNT_HINT);
    const ok = await act.run(async () => ctx.apply(await api<HousingOut>(`/api/v1/housing/charges/${c.id}`, { method: "PUT", body: JSON.stringify(chargeBody(c, { amount: value })) })), `Saved: ${c.name} is ${money(value, { cents: true })} a month, and it is counted now.`);
    if (ok) setAsking(false);
  }
  async function remove() {
    ctx.apply(await api<HousingOut>(`/api/v1/housing/charges/${c.id}`, { method: "DELETE" }));
  }

  if (editing) {
    return <ChargeForm h={h} ctx={ctx} paysTo={c.pays_to} c={c} onDone={(text) => { setEditing(false); act.setNote({ text, tone: "info" }); }} onCancel={() => setEditing(false)} />;
  }
  const kindWord = CHARGE_WORD[c.kind] && CHARGE_WORD[c.kind] !== c.name ? CHARGE_WORD[c.kind] : "";
  const facts = [
    kindWord,
    c.pays_to !== "home" && c.payee ? `paid to ${c.payee}` : "",
    c.pays_to !== "home" ? `due on the ${ordinal(c.due_day ?? h.due_day)}` : "",
    c.starts_on ? `from ${shortDate(c.starts_on)}` : "",
    c.ends_on ? `until ${shortDate(c.ends_on)}` : "",
  ].filter(Boolean);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, borderTop: "1px solid var(--surface-2)", paddingTop: 8 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 150px", minWidth: 0 }}>
          <strong style={{ fontSize: 13, overflowWrap: "anywhere" }}>{c.name}</strong>
          {facts.length > 0 && <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{facts.join(" · ")}</div>}
        </div>
        {c.amount === null ? <Unknown /> : <strong className="num" style={{ fontSize: 14 }}>{c.estimate ? "about " : ""}{money(c.amount, { cents: true })}</strong>}
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "flex-start" }}>
        {c.amount === null && !asking && <button type="button" className="btn dark" style={SMALL} onClick={() => setAsking(true)}>Add the amount</button>}
        <button type="button" className="btn" style={SMALL} onClick={() => setEditing(true)}>Edit</button>
        <ConfirmButton label="Remove" name={`Remove ${c.name}`} style={SMALL} question={`Remove ${c.name}? It stops counting in your bills.`} yes="Yes, remove it" no="Keep it" onConfirm={remove} />
      </div>
      {asking && c.amount === null && (
        <form onSubmit={addAmount} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
          <Field label={`${c.name}: amount a month`}><input className="input num" value={typed} onChange={(e) => setTyped(e.target.value)} inputMode="decimal" style={{ width: "min(100%, 140px)" }} autoFocus /></Field>
          <button className="btn dark" type="submit" disabled={act.busy}>{act.busy ? "Saving…" : "Save the amount"}</button>
          <button className="btn" type="button" onClick={() => setAsking(false)}>Cancel</button>
        </form>
      )}
      <Said text={act.note?.text ?? null} tone={act.note?.tone} />
    </div>
  );
}

function ChargesCard({ h, ctx, paysTo }: { h: HomeRow; ctx: Ctx; paysTo: "home" | "other" }) {
  const [adding, setAdding] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  const rows = h.charges.filter((c) => (paysTo === "home") === (c.pays_to === "home"));
  const word = h.kind === "mortgage" ? "mortgage" : "rent";
  const title = paysTo === "home" ? `Paid with the ${word} each month` : "Paid on their own";
  const sub = paysTo === "home" ? `One payment to ${payeeOf(h)}, due on the ${ordinal(h.due_day)}: the ${word} and every fee that comes with it.` : "Each is its own bill, paid to someone else, such as renter's insurance.";
  return (
    <Card title={title} sub={sub}>
      {rows.length === 0 && !adding && <div style={{ fontSize: 13, color: "var(--ink-2)" }}>{paysTo === "home" ? `Nothing yet. Add the ${word} first, then each fee.` : "Nothing yet."}</div>}
      {rows.map((c) => <ChargeRow key={c.id} c={c} h={h} ctx={ctx} />)}
      {adding ? (
        <ChargeForm h={h} ctx={ctx} paysTo={paysTo} onDone={(text) => { setAdding(false); setSaid(text); }} onCancel={() => setAdding(false)} />
      ) : (
        <button type="button" className="btn" aria-expanded={false} style={{ ...SMALL, alignSelf: "flex-start" }} onClick={() => { setAdding(true); setSaid(null); }}>
          {paysTo === "home" ? (rows.length ? "Add a fee" : `Add the ${word}`) : "Add a bill paid on its own"}
        </button>
      )}
      <Said text={said} />
    </Card>
  );
}

// ---- due day, lease, deposit -----------------------------------------------------------------------------------

function DueCard({ h, ctx }: { h: HomeRow; ctx: Ctx }) {
  const [editing, setEditing] = useState(false);
  const [dueDay, setDueDay] = useState(String(h.due_day));
  const [firstDue, setFirstDue] = useState(h.first_due_entered ?? "");
  const [grace, setGrace] = useState(String(h.grace_days));
  const [lateFee, setLateFee] = useState(h.late_fee ? String(h.late_fee) : "");
  const [autopay, setAutopay] = useState(h.autopay);
  const act = useAction();

  function open() {
    setDueDay(String(h.due_day));
    setFirstDue(h.first_due_entered ?? "");
    setGrace(String(h.grace_days));
    setLateFee(h.late_fee ? String(h.late_fee) : "");
    setAutopay(h.autopay);
    act.setNote(null);
    setEditing(!editing);
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    const day = dayOf(dueDay);
    if (day === null) return act.fail(DAY_HINT);
    const days = grace.trim() ? daysOf(grace, 31) : 0;
    if (days === null) return act.fail("Type the days of grace from 0 to 31.");
    const fee = lateFee.trim() ? parseMoney(lateFee) : 0;
    if (fee === null || fee < 0) return act.fail(AMOUNT_HINT);
    const ok = await act.run(() => putHome(ctx, h, { due_day: day, first_due: firstDue || null, grace_days: days, late_fee: fee, autopay }), "Saved. The bill moves with it.");
    if (ok) setEditing(false);
  }

  return (
    <Card title="Due day and late fees" sub={`When the payment to ${payeeOf(h)} is due, and what a late one costs.`} action={<EditToggle open={editing} onClick={open} />}>
      {editing ? (
        <form onSubmit={save} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <Row>
            <Field label="Due on the (day of the month)"><input className="input num" value={dueDay} onChange={(e) => setDueDay(e.target.value)} inputMode="numeric" style={{ width: "min(100%, 90px)" }} /></Field>
            <Field label="Days of grace"><input className="input num" value={grace} onChange={(e) => setGrace(e.target.value)} inputMode="numeric" style={{ width: "min(100%, 90px)" }} /></Field>
            <Field label="Late fee"><input className="input num" value={lateFee} onChange={(e) => setLateFee(e.target.value)} inputMode="decimal" style={{ width: "min(100%, 120px)" }} /></Field>
          </Row>
          <Row>
            <Field label="First regular payment (blank: the move-in day)"><input className="input" type="date" value={firstDue} onChange={(e) => setFirstDue(e.target.value)} /></Field>
            <Check label="Paid automatically (autopay)" checked={autopay} onChange={setAutopay} />
          </Row>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button className="btn dark" type="submit" disabled={act.busy}>{act.busy ? "Saving…" : "Save"}</button>
          </div>
        </form>
      ) : (
        <Facts rows={[
          ["Due on", `the ${ordinal(h.due_day)} of each month`],
          ["Next payment", h.next_due ? shortDate(h.next_due) : "none yet: add an amount paid with the rent"],
          ["First regular payment", h.first_due ? dateYear(h.first_due) : "not set"],
          ["Days of grace", h.grace_days ? `${plural(h.grace_days, "day")} after the due day` : "none"],
          ["Late fee", h.late_fee ? money(h.late_fee, { cents: true }) : "none on file"],
          ["Autopay", h.autopay ? "Yes, paid automatically" : "No, you pay it yourself"],
        ]} />
      )}
      <Said text={act.note?.text ?? null} tone={act.note?.tone} />
    </Card>
  );
}

function NoticeBlock({ h, ctx }: { h: HomeRow; ctx: Ctx }) {
  const [on, setOn] = useState(localToday());
  const act = useAction();
  if (!h.notice) {
    if (h.lease_word) return <div style={{ fontSize: 13, color: "var(--ink-2)" }}>{h.lease_word}</div>;
    return <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Add the day you move out and the days of notice the lease needs (Edit), and the date to give written notice by shows here.</div>;
  }
  const n = h.notice;
  async function give(e: FormEvent) {
    e.preventDefault();
    if (!on) return act.fail("Choose the date you gave notice.");
    await act.run(async () => ctx.apply(await api<HousingOut>(`/api/v1/housing/${h.id}/notice`, { method: "POST", body: JSON.stringify({ given_on: on }) })), `Saved: notice given on ${shortDate(on)}.`);
  }
  async function undo() {
    await act.run(async () => ctx.apply(await api<HousingOut>(`/api/v1/housing/${h.id}/notice`, { method: "POST", body: JSON.stringify({ given_on: null }) })), "Undone: notice is not given, and the reminder is back.");
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, background: "var(--surface-2)", borderRadius: 10, padding: "10px 12px" }}>
      <span className="lab">Written notice to move out</span>
      <div><StatusPill status={n.status}>{n.word}</StatusPill></div>
      {n.given_on ? (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button type="button" className="btn" style={{ ...SMALL, ...ROW }} disabled={act.busy} onClick={undo}><Undo2 size={14} aria-hidden="true" />Undo: I have not given notice</button>
        </div>
      ) : (
        <form onSubmit={give} style={{ display: "flex", gap: 8, alignItems: "flex-end", flexWrap: "wrap" }}>
          <Field label="The date you gave it"><input className="input" type="date" value={on} onChange={(e) => setOn(e.target.value)} /></Field>
          <button type="submit" className="btn dark" disabled={act.busy}>{act.busy ? "Saving…" : "I gave notice"}</button>
        </form>
      )}
      <Said text={act.note?.text ?? null} tone={act.note?.tone} />
    </div>
  );
}

function LeaseCard({ h, ctx }: { h: HomeRow; ctx: Ctx }) {
  const [editing, setEditing] = useState(false);
  const [startsOn, setStartsOn] = useState(h.starts_on ?? "");
  const [endsOn, setEndsOn] = useState(h.ends_on ?? "");
  const [leaseEnds, setLeaseEnds] = useState(h.lease_ends_on ?? "");
  const [notice, setNotice] = useState(h.notice_days !== null ? String(h.notice_days) : "");
  const act = useAction();
  const mortgage = h.kind === "mortgage";

  function open() {
    setStartsOn(h.starts_on ?? "");
    setEndsOn(h.ends_on ?? "");
    setLeaseEnds(h.lease_ends_on ?? "");
    setNotice(h.notice_days !== null ? String(h.notice_days) : "");
    act.setNote(null);
    setEditing(!editing);
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    const days = notice.trim() ? daysOf(notice, 365) : null;
    if (notice.trim() && days === null) return act.fail(DAYS_HINT);
    if (startsOn && endsOn && endsOn < startsOn) return act.fail("The end comes after the start.");
    const ok = await act.run(() => putHome(ctx, h, { starts_on: startsOn || null, ends_on: endsOn || null, lease_ends_on: leaseEnds || null, notice_days: days }), "Saved.");
    if (ok) setEditing(false);
  }

  return (
    <Card title={mortgage ? "Dates" : "Lease"} sub={mortgage ? "When you moved in, and when you leave if you know." : "The lease's dates and the notice it needs before you move out."} action={<EditToggle open={editing} onClick={open} />}>
      {editing ? (
        <form onSubmit={save} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <Row>
            <Field label={h.state === "upcoming" ? "Moving in on" : "Moved in on"}><input className="input" type="date" value={startsOn} onChange={(e) => setStartsOn(e.target.value)} /></Field>
            <Field label={mortgage ? "Leaving on (blank: not planned)" : "Moving out on (blank: not planned)"}><input className="input" type="date" value={endsOn} onChange={(e) => setEndsOn(e.target.value)} /></Field>
          </Row>
          {!mortgage && <Field label="The lease term ends (as the lease says; the rent goes on after it unless you move out)"><input className="input" type="date" value={leaseEnds} onChange={(e) => setLeaseEnds(e.target.value)} style={{ width: "min(100%, 200px)" }} /></Field>}
          {!mortgage && <Field label="Days of written notice to move out"><input className="input num" value={notice} onChange={(e) => setNotice(e.target.value)} inputMode="numeric" style={{ width: "min(100%, 100px)" }} /></Field>}
          <div><button className="btn dark" type="submit" disabled={act.busy}>{act.busy ? "Saving…" : "Save"}</button></div>
        </form>
      ) : (
        <Facts rows={[
          [h.state === "upcoming" ? "Moving in" : "Moved in", h.starts_on ? dateYear(h.starts_on) : "not set"],
          [mortgage ? "Leaving" : "Moving out", h.ends_on ? dateYear(h.ends_on) : "not planned"],
          ...(mortgage ? [] : [
            ["The lease term ends", h.lease_ends_on ? dateYear(h.lease_ends_on) : "not set (month to month)"] as [string, ReactNode],
            ["Notice to move out", h.notice_days !== null ? `${plural(h.notice_days, "day")}, in writing` : "not set"] as [string, ReactNode],
          ]),
        ]} />
      )}
      {!mortgage && !editing && <NoticeBlock h={h} ctx={ctx} />}
      <Said text={act.note?.text ?? null} tone={act.note?.tone} />
    </Card>
  );
}

function DepositCard({ h, ctx }: { h: HomeRow; ctx: Ctx }) {
  const [editing, setEditing] = useState(false);
  const [deposit, setDeposit] = useState(h.deposit ? String(h.deposit) : "");
  const [backOn, setBackOn] = useState(h.deposit_back_on ?? "");
  const act = useAction();

  function open() {
    setDeposit(h.deposit ? String(h.deposit) : "");
    setBackOn(h.deposit_back_on ?? "");
    act.setNote(null);
    setEditing(!editing);
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    const value = deposit.trim() ? parseMoney(deposit) : 0;
    if (value === null || value < 0) return act.fail(AMOUNT_HINT);
    const ok = await act.run(() => putHome(ctx, h, { deposit: value, deposit_back_on: backOn || null }), "Saved.");
    if (ok) setEditing(false);
  }

  return (
    <Card title="Deposit" sub="The security deposit held for this home, and when it should come back." action={<EditToggle open={editing} onClick={open} />}>
      {editing ? (
        <form onSubmit={save} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <Row>
            <Field label="Deposit held"><input className="input num" value={deposit} onChange={(e) => setDeposit(e.target.value)} inputMode="decimal" style={{ width: "min(100%, 140px)" }} /></Field>
            <Field label="Should come back by"><input className="input" type="date" value={backOn} onChange={(e) => setBackOn(e.target.value)} /></Field>
          </Row>
          <div><button className="btn dark" type="submit" disabled={act.busy}>{act.busy ? "Saving…" : "Save"}</button></div>
        </form>
      ) : (
        <Facts rows={[
          ["Deposit held", h.deposit ? money(h.deposit, { cents: true }) : "none on file"],
          ["Comes back by", h.deposit_back_on ? dateYear(h.deposit_back_on) : "not set"],
        ]} />
      )}
      <Said text={act.note?.text ?? null} tone={act.note?.tone} />
    </Card>
  );
}

// ---- utilities -------------------------------------------------------------------------------------------------

function UtilityForm({ u, homeId, ctx, onDone, onCancel }: { u?: UtilityRow; homeId: number | null; ctx: Ctx; onDone: (text: string) => void; onCancel: () => void }) {
  const services = ctx.utilities?.services?.length ? ctx.utilities.services : Object.keys(SERVICE_WORD);
  const billedWords = ctx.utilities?.billed ?? BILLED_WORD;
  const bills = ctx.utilities?.bills_to_link ?? [];
  const [home, setHome] = useState(homeId !== null ? String(homeId) : "");
  const [service, setService] = useState(u?.service ?? "electric");
  const [provider, setProvider] = useState(u?.provider ?? "");
  const [billed, setBilled] = useState(u?.billed ?? "direct");
  const [company, setCompany] = useState(u?.billing_company ?? "");
  const [billId, setBillId] = useState("");
  const [last4, setLast4] = useState("");
  const [amount, setAmount] = useState(u?.amount !== null && u?.amount !== undefined ? String(u.amount) : "");
  const [estimate, setEstimate] = useState(u?.estimate ?? true);
  const [dueDay, setDueDay] = useState(u?.due_day ? String(u.due_day) : "");
  const [startsOn, setStartsOn] = useState(u?.starts_on ?? "");
  const [endsOn, setEndsOn] = useState(u?.ends_on ?? "");
  const [moves, setMoves] = useState(u?.moves_with_you ?? false);
  const [memo, setMemo] = useState(u?.note ?? "");
  const act = useAction();
  const own = billed === "direct" || billed === "submetered";

  async function save(e: FormEvent) {
    e.preventDefault();
    const value = amount.trim() ? parseMoney(amount) : null;
    if (amount.trim() && (value === null || value < 0)) return act.fail(AMOUNT_HINT);
    const day = dueDay.trim() ? dayOf(dueDay) : null;
    if (dueDay.trim() && day === null) return act.fail(DAY_HINT);
    if (last4.trim() && !/^\d{4}$/.test(last4.trim())) return act.fail("Type the last four digits of the account number, like 1234.");
    const body = {
      home_id: home ? Number(home) : null,
      service,
      provider: provider.trim(),
      billed,
      billing_company: billed === "submetered" ? company.trim() : "",
      account_last4: last4.trim() || undefined,
      amount: own ? value : null,
      estimate,
      due_day: own ? day : null,
      starts_on: startsOn || null,
      ends_on: endsOn || null,
      moves_with_you: moves,
      note: memo.trim(),
      bill_id: own && billId ? Number(billId) : undefined,
    };
    const ok = await act.run(async () => {
      await api<UtilitiesOut>(u ? `/api/v1/utilities/${u.id}` : "/api/v1/utilities", { method: u ? "PUT" : "POST", body: JSON.stringify(body) });
      ctx.reload();
    }, "Saved.");
    const what = SERVICE_WORD[service] ?? service;
    if (ok) onDone(u ? `Saved ${what}.` : `Added ${what}.`);
  }

  return (
    <form onSubmit={save} style={{ display: "flex", flexDirection: "column", gap: 8, background: "var(--surface-2)", borderRadius: 10, padding: "10px 12px" }}>
      <Row>
        <Field label="Service">
          <select className="input" value={service} onChange={(e) => setService(e.target.value)}>
            {services.map((s) => <option key={s} value={s}>{SERVICE_WORD[s] ?? s}</option>)}
          </select>
        </Field>
        <Field label="Provider (the company that supplies it)" grow><input className="input" value={provider} onChange={(e) => setProvider(e.target.value)} maxLength={120} /></Field>
      </Row>
      <Field label="Where its bill comes from">
        <select className="input" value={billed} onChange={(e) => setBilled(e.target.value)} style={{ maxWidth: "100%" }}>
          {Object.entries(billedWords).map(([k, w]) => <option key={k} value={k}>{w.charAt(0).toUpperCase() + w.slice(1)}</option>)}
        </select>
      </Field>
      {billed === "submetered" && <Field label="Billing company (who sends the bill)" grow><input className="input" value={company} onChange={(e) => setCompany(e.target.value)} maxLength={120} /></Field>}
      {own && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <Field label="Already on your bills list?">
            <select className="input" value={billId} onChange={(e) => setBillId(e.target.value)} style={{ maxWidth: "100%" }}>
              <option value="">{u?.bill_id ? "Keep it as it is" : "No, it is new"}</option>
              {bills.map((b) => <option key={b.id} value={b.id}>{b.name} · {money(b.amount, { cents: true })} · due the {ordinal(b.due_day)}</option>)}
            </select>
          </Field>
          <span style={MUTED}>
            {billId
              ? "The utility takes over that bill: its amount and due day, unless you type them below. No second bill is made."
              : bills.length
                ? "Choose the bill if it is already on your bills list, so it is not counted twice. Otherwise a new bill is made once it has an amount."
                : "A new bill is made once it has an amount. None of the bills on your list is free to take over."}
          </span>
        </div>
      )}
      {own && (
        <Row>
          <Field label="A usual month (blank: not known yet)"><input className="input num" value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" style={{ width: "min(100%, 140px)" }} /></Field>
          <Field label="Due day"><input className="input num" value={dueDay} onChange={(e) => setDueDay(e.target.value)} inputMode="numeric" style={{ width: "min(100%, 90px)" }} /></Field>
          <Check label="The amount is an estimate" checked={estimate} onChange={setEstimate} />
        </Row>
      )}
      <Row>
        <Field label="Starts"><input className="input" type="date" value={startsOn} onChange={(e) => setStartsOn(e.target.value)} /></Field>
        <Field label="Ends"><input className="input" type="date" value={endsOn} onChange={(e) => setEndsOn(e.target.value)} /></Field>
      </Row>
      <Row>
        <Field label={u?.account_last4 === "set" ? "Account's last four digits (on file; type to change)" : "Account's last four digits (optional)"}><input className="input num" value={last4} onChange={(e) => setLast4(e.target.value)} inputMode="numeric" maxLength={4} autoComplete="off" style={{ width: "min(100%, 110px)" }} /></Field>
        <Field label="Home">
          <select className="input" value={home} onChange={(e) => setHome(e.target.value)} style={{ maxWidth: "100%" }}>
            <option value="">Not tied to a home</option>
            {ctx.data.homes.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
          </select>
        </Field>
      </Row>
      <Check label="It moves with you (the same account at the next home)" checked={moves} onChange={setMoves} />
      <Field label="Note (optional)" grow><input className="input" value={memo} onChange={(e) => setMemo(e.target.value)} maxLength={120} /></Field>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button className="btn dark" type="submit" disabled={act.busy}>{act.busy ? "Saving…" : u ? "Save" : "Add it"}</button>
        <button className="btn" type="button" onClick={onCancel}>Cancel</button>
        <Said text={act.note?.tone === "error" ? act.note.text : null} tone="error" />
      </div>
    </form>
  );
}

function UtilityItem({ u, ctx }: { u: UtilityRow; ctx: Ctx }) {
  const [editing, setEditing] = useState(false);
  const [docs, setDocs] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  const Icon = SERVICE_ICON[u.service] ?? Plug;
  const own = u.billed === "direct" || u.billed === "submetered";
  const name = u.provider ? `${u.what} · ${u.provider}` : u.what;

  async function remove() {
    await api<UtilitiesOut>(`/api/v1/utilities/${u.id}`, { method: "DELETE" });
    ctx.reload();
  }
  if (editing) {
    return <UtilityForm u={u} homeId={u.home_id} ctx={ctx} onDone={(text) => { setEditing(false); setSaid(text); }} onCancel={() => setEditing(false)} />;
  }
  const facts = [
    own && u.due_day ? `due on the ${ordinal(u.due_day)}` : "",
    u.account_last4 === "set" ? "account number on file" : "",
    u.starts_on ? `from ${shortDate(u.starts_on)}` : "",
    u.ends_on ? `until ${shortDate(u.ends_on)}` : "",
  ].filter(Boolean);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, borderTop: "1px solid var(--surface-2)", paddingTop: 8 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "flex-start", flexWrap: "wrap" }}>
        <span aria-hidden="true" style={{ width: 32, height: 32, borderRadius: 8, background: "var(--surface-2)", display: "grid", placeItems: "center", flexShrink: 0, color: "var(--ink-2)" }}><Icon size={17} /></span>
        <div style={{ flex: "1 1 170px", minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
          <strong style={{ fontSize: 13, overflowWrap: "anywhere" }}>{name}</strong>
          <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Where it comes from: {u.billed_word}{u.billed === "submetered" && u.billing_company ? `, ${u.billing_company}` : ""}.</span>
          {facts.length > 0 && <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{facts.join(" · ")}</span>}
          {u.note && <span style={MUTED}>{u.note}</span>}
        </div>
        {u.amount !== null ? (
          <strong className="num" style={{ fontSize: 14, whiteSpace: "nowrap" }}>{u.estimate ? "about " : ""}{money(u.amount, { cents: true })}<span style={{ fontWeight: 500, fontSize: 12, color: "var(--ink-2)" }}> a month</span></strong>
        ) : own && !u.moves_with_you ? (
          <Unknown />
        ) : !own ? (
          <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{u.billed === "included" ? "in the rent" : "with the rent"}</span>
        ) : null}
      </div>
      {u.moves_with_you && <div><Tag icon={Truck}>Moves with you: its bill is already on your bills list</Tag></div>}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "flex-start" }}>
        <button type="button" className="btn" style={SMALL} onClick={() => { setEditing(true); setSaid(null); }}>{own && u.amount === null && !u.moves_with_you ? "Add the amount" : "Edit"}</button>
        <button type="button" className="btn" style={SMALL} aria-expanded={docs} onClick={() => setDocs(!docs)}>{docs ? "Hide its documents" : "Its documents"}</button>
        <ConfirmButton label="Remove" name={`Remove ${u.what}`} style={SMALL} question={`Remove ${u.what}? Its bill stops counting.`} yes="Yes, remove it" no="Keep it" onConfirm={remove} />
      </div>
      {docs && (
        <div style={{ borderLeft: "3px solid var(--surface-2)", paddingLeft: 10 }}>
          <Documents attachTo={`utility:${u.id}`} defaultKind="utility_terms" refreshKey={ctx.docTick} onChanged={ctx.docsChanged} />
        </div>
      )}
      <Said text={said} />
    </div>
  );
}

function UtilitiesCard({ h, ctx }: { h: HomeRow; ctx: Ctx }) {
  const [adding, setAdding] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  return (
    <Card title="Utilities" sub="Each service, where its bill comes from, and its terms.">
      {h.utilities.length === 0 && !adding && <div style={{ fontSize: 13, color: "var(--ink-2)" }}>None yet. Add electricity, water, internet and the rest, and where each one is billed.</div>}
      {h.utilities.map((u) => <UtilityItem key={u.id} u={u} ctx={ctx} />)}
      {adding ? (
        <UtilityForm homeId={h.id} ctx={ctx} onDone={(text) => { setAdding(false); setSaid(text); }} onCancel={() => setAdding(false)} />
      ) : (
        <button type="button" className="btn" aria-expanded={false} style={{ ...SMALL, alignSelf: "flex-start" }} onClick={() => { setAdding(true); setSaid(null); }}>Add a utility</button>
      )}
      <Said text={said} />
    </Card>
  );
}

// ---- costs to get in, documents --------------------------------------------------------------------------------

function CostsCard({ h }: { h: HomeRow }) {
  return (
    <Card title="Costs to get in" sub={<>The one-time costs of moving in. Add, change and check them on <Link to="/move">Moving home</Link>.</>}>
      {h.costs.length === 0 ? (
        <Empty card={false} to="/move" action="Add them on Moving home">No costs yet: the deposit, the first month, the fees, the truck.</Empty>
      ) : (
        <>
          <div style={{ display: "flex", gap: "4px 16px", flexWrap: "wrap", fontSize: 13 }}>
            <span>Still to pay: <strong className="num">{money(h.costs_total, { cents: true })}</strong></span>
            <span>Counted in your plan: <strong className="num">{money(h.costs_counted, { cents: true })}</strong></span>
            {h.costs_paid_before > 0 && <span>Already paid: <strong className="num">{money(h.costs_paid_before, { cents: true })}</strong></span>}
          </div>
          {h.costs.map((c) => (
            <div key={c.id} style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap", borderTop: "1px solid var(--surface-2)", paddingTop: 6, fontSize: 13 }}>
              <div style={{ flex: "1 1 150px", minWidth: 0 }}>
                <strong style={{ overflowWrap: "anywhere" }}>{c.name}</strong>
                <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{c.due_on ? `due ${shortDate(c.due_on)}` : "no date yet"}{c.paid ? " · already paid" : ""}{c.refundable ? " · comes back" : ""}</div>
              </div>
              {c.amount === null ? <Unknown /> : <strong className="num">{money(c.amount, { cents: true })}</strong>}
            </div>
          ))}
          {h.costs_undated.length > 0 && <div style={{ fontSize: 12, color: "var(--warning-ink)", fontWeight: 600 }}>No date yet, so not counted: {h.costs_undated.join(", ")}.</div>}
        </>
      )}
    </Card>
  );
}

function HomeDocuments({ h, ctx }: { h: HomeRow; ctx: Ctx }) {
  return (
    <Card title="Documents" sub={h.kind === "mortgage" ? "The loan's papers and statements for this home." : "The lease, its addenda and the property's charge sheets. The terms a lease states are offered on Needs you."}>
      <Documents attachTo={`home:${h.id}`} defaultKind={h.kind === "mortgage" ? "statement" : "lease"} refreshKey={ctx.docTick} onChanged={ctx.docsChanged} />
    </Card>
  );
}

// ---- one home --------------------------------------------------------------------------------------------------

function HomeHeader({ h, ctx }: { h: HomeRow; ctx: Ctx }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(h.name);
  const [kind, setKind] = useState<string>(h.kind);
  const [payee, setPayee] = useState(h.payee);
  const [memo, setMemo] = useState(h.note);
  const act = useAction();
  const stateWord = h.state === "current" ? "Where you live now" : h.state === "upcoming" ? `Moving in${h.starts_on ? ` ${shortDate(h.starts_on)}` : ""}` : `Moved out${h.ends_on ? ` ${shortDate(h.ends_on)}` : ""}`;

  function open() {
    setName(h.name);
    setKind(h.kind);
    setPayee(h.payee);
    setMemo(h.note);
    act.setNote(null);
    setEditing(!editing);
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return act.fail("Give the home a name, such as its street.");
    const ok = await act.run(() => putHome(ctx, h, { name: name.trim(), kind, payee: payee.trim(), note: memo.trim() }), "Saved.");
    if (ok) setEditing(false);
  }
  async function remove() {
    ctx.apply(await api<HousingOut>(`/api/v1/housing/${h.id}`, { method: "DELETE" }));
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <h2 id={`home-${h.id}`} style={{ margin: 0, fontSize: 18, fontWeight: 800, overflowWrap: "anywhere" }}>{h.name}</h2>
        <Tag icon={h.state === "upcoming" ? Truck : House}>{stateWord}</Tag>
        <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{KIND_WORD[h.kind] ?? h.kind} · paid to {payeeOf(h)}</span>
        <span style={{ flexGrow: 1 }} />
        <EditToggle open={editing} onClick={open} label="Edit the name" />
        <ConfirmButton label="Remove this home" style={SMALL} question={`Remove ${h.name}? Its rent, fees and costs stop counting, and so do its utilities' bills, even one that was on your bills list before the home. Its documents stay.`} yes="Yes, remove it" no="Keep it" onConfirm={remove} />
      </div>
      {h.note && !editing && <div style={MUTED}>{h.note}</div>}
      {editing && (
        <form onSubmit={save} className="card" style={{ padding: "10px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
          <Row>
            <Field label="Name" grow><input className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} /></Field>
            <Field label="Kind">
              <select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
                <option value="rent">Rent</option><option value="mortgage">Mortgage</option><option value="other">Other housing</option>
              </select>
            </Field>
          </Row>
          <Row>
            <Field label="Paid to (the property or the lender)" grow><input className="input" value={payee} onChange={(e) => setPayee(e.target.value)} maxLength={120} /></Field>
            <Field label="Note (optional)" grow><input className="input" value={memo} onChange={(e) => setMemo(e.target.value)} maxLength={300} /></Field>
          </Row>
          <div><button className="btn dark" type="submit" disabled={act.busy}>{act.busy ? "Saving…" : "Save"}</button></div>
        </form>
      )}
      <Said text={act.note?.text ?? null} tone={act.note?.tone} />
    </div>
  );
}

function HomeSection({ h, ctx }: { h: HomeRow; ctx: Ctx }) {
  const mortgage = h.kind === "mortgage";
  return (
    <section aria-labelledby={`home-${h.id}`} style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
      <HomeHeader h={h} ctx={ctx} />
      <MonthlyPicture h={h} />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 340px), 1fr))", gap: 12, alignItems: "start" }}>
        <ChargesCard h={h} ctx={ctx} paysTo="home" />
        <ChargesCard h={h} ctx={ctx} paysTo="other" />
        <DueCard h={h} ctx={ctx} />
        <LeaseCard h={h} ctx={ctx} />
        {(!mortgage || h.deposit > 0) && <DepositCard h={h} ctx={ctx} />}
        <UtilitiesCard h={h} ctx={ctx} />
        {(h.state === "upcoming" || h.costs.length > 0) && <CostsCard h={h} />}
        <HomeDocuments h={h} ctx={ctx} />
      </div>
    </section>
  );
}

// ---- no home yet, adding one -----------------------------------------------------------------------------------

// A rent or mortgage bill on the bills list that no home here holds. With no home yet, it can become the home. Once
// homes exist, it may be the same rent counted twice (as this bill and as a home's rent), so the card says so and
// offers both ways out: take the bill off the list, or make it a home of its own if it is another place.
function AdoptCard({ bill, ctx, onDone }: { bill: HousingOut["rent_bills_to_adopt"][number]; ctx: Ctx; onDone?: (text: string) => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState("rent");
  const act = useAction();
  const homes = ctx.data.homes.filter((h) => h.state !== "ended").map((h) => h.name);
  async function adopt(e: FormEvent) {
    e.preventDefault();
    const ok = await act.run(async () => ctx.apply(await api<HousingOut>("/api/v1/housing/adopt", { method: "POST", body: JSON.stringify({ bill_id: bill.id, name: name.trim() || bill.name, kind }) })), "Done: it is your home now.");
    if (ok) onDone?.(`${name.trim() || bill.name} is a home now, with ${bill.name} as its rent.`);
  }
  async function takeOff() {
    await api(`/api/v1/bills/${bill.id}`, { method: "PUT", body: JSON.stringify({ active: false }) });
    onDone?.(`${bill.name} is off your bills list. The home's rent is the one that counts.`);
    ctx.reload();
  }
  const sub = homes.length
    ? <>{money(bill.amount, { cents: true })} due on the {ordinal(bill.due_day)}, on your bills list but not held by any home here. If it is the rent of {homes.join(" or ")}, it is counted twice now: once as this bill and once as the home's rent. Take it off your bills list. If it is another place, make it a home of its own below.</>
    : <>{money(bill.amount, { cents: true })} due on the {ordinal(bill.due_day)}, already on your bills list. Its amount becomes this home's rent, and it stays the one bill that counts.</>;
  return (
    <Card level={2} title={homes.length ? `${bill.name} on your bills list is not held by a home` : `Make ${bill.name} your home`} sub={sub}>
      {homes.length > 0 && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <StatusPill status="warn">May be counted twice</StatusPill>
          <ConfirmButton label="Take it off your bills list" question={`Take ${bill.name} off your bills list? The home's rent keeps counting.`} yes="Yes, take it off" no="Keep it" onConfirm={takeOff} style={SMALL} />
        </div>
      )}
      <form onSubmit={adopt} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
        <Field label={`What to call the home (blank: ${bill.name})`} grow><input className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder="the street, or the building" /></Field>
        <Field label="Kind">
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="rent">Rent</option><option value="mortgage">Mortgage</option><option value="other">Other housing</option>
          </select>
        </Field>
        <button className={homes.length ? "btn" : "btn dark"} type="submit" disabled={act.busy} style={ROW}><House size={14} aria-hidden="true" />{act.busy ? "Saving…" : homes.length ? "Make it a home of its own" : "Make it my home"}</button>
      </form>
      <Said text={act.note?.text ?? null} tone={act.note?.tone} />
    </Card>
  );
}

function AddHomeForm({ ctx, onDone }: { ctx: Ctx; onDone: (text: string) => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState("rent");
  const [payee, setPayee] = useState("");
  const [startsOn, setStartsOn] = useState("");
  const [firstDue, setFirstDue] = useState("");
  const [endsOn, setEndsOn] = useState("");
  const [dueDay, setDueDay] = useState("1");
  const [rent, setRent] = useState("");
  const [notice, setNotice] = useState("");
  const act = useAction();

  async function add(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return act.fail("Give the home a name, such as its street.");
    const day = dayOf(dueDay);
    if (day === null) return act.fail(DAY_HINT);
    const amount = rent.trim() ? parseMoney(rent) : null;
    if (rent.trim() && (amount === null || amount < 0)) return act.fail(AMOUNT_HINT);
    const days = notice.trim() ? daysOf(notice, 365) : null;
    if (notice.trim() && days === null) return act.fail(DAYS_HINT);
    if (startsOn && endsOn && endsOn < startsOn) return act.fail("The lease ends after it starts.");
    const body: HomeBody = { name: name.trim(), kind, payee: payee.trim(), starts_on: startsOn || null, ends_on: null, lease_ends_on: endsOn || null, first_due: firstDue || null, due_day: day, grace_days: 0, late_fee: 0, autopay: false, notice_days: days, notice_given_on: null, deposit: 0, deposit_back_on: null, pay_from_account_id: null, note: "" };
    const before = new Set(ctx.data.homes.map((h) => h.id));
    // The home is made first, then its rent. If the rent fails after the home exists, the home is kept and shown, the
    // form closes, and the rent is left to add by hand, so trying again never makes a second home.
    const made: { out?: HousingOut } = {};
    const ok = await act.run(async () => {
      made.out = await api<HousingOut>("/api/v1/housing", { method: "POST", body: JSON.stringify(body) });
    }, "Added.", "Couldn't add it");
    if (!ok || !made.out) return;
    let next: HousingOut = made.out;
    const home = next.homes.find((h) => !before.has(h.id));
    if (home && amount !== null) {
      const charge: ChargeBody = { name: kind === "mortgage" ? "Mortgage payment" : "Rent", kind: kind === "mortgage" ? "principal_interest" : "rent", amount, estimate: false, pays_to: "home", payee: "", due_day: null, starts_on: null, ends_on: null, note: "" };
      try {
        next = await api<HousingOut>(`/api/v1/housing/${home.id}/charges`, { method: "POST", body: JSON.stringify(charge) });
      } catch (err) {
        ctx.apply(next);
        onDone(`Added ${body.name}, but couldn't add its ${kind === "mortgage" ? "payment" : "rent"}: ${(err as Error).message} Add it under “Paid with the rent each month”.`);
        return;
      }
    }
    ctx.apply(next);
    onDone(`Added ${body.name}.${amount === null ? " Add its rent under “Paid with the rent each month”." : ""}`);
  }

  return (
    <form onSubmit={add} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <Row>
        <Field label="Name (the street, or the building)" grow><input className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} /></Field>
        <Field label="Kind">
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="rent">Rent</option><option value="mortgage">Mortgage</option><option value="other">Other housing</option>
          </select>
        </Field>
      </Row>
      <Row>
        <Field label="Paid to (the property or the lender)" grow><input className="input" value={payee} onChange={(e) => setPayee(e.target.value)} maxLength={120} /></Field>
        <Field label={kind === "mortgage" ? "Payment a month (optional)" : "Rent a month (optional)"}><input className="input num" value={rent} onChange={(e) => setRent(e.target.value)} inputMode="decimal" style={{ width: "min(100%, 140px)" }} /></Field>
        <Field label="Due on the (day)"><input className="input num" value={dueDay} onChange={(e) => setDueDay(e.target.value)} inputMode="numeric" style={{ width: "min(100%, 90px)" }} /></Field>
      </Row>
      <Row>
        <Field label="Moving in on (blank: you live there now)"><input className="input" type="date" value={startsOn} onChange={(e) => setStartsOn(e.target.value)} /></Field>
        <Field label="First regular payment (blank: the move-in day)"><input className="input" type="date" value={firstDue} onChange={(e) => setFirstDue(e.target.value)} /></Field>
      </Row>
      <Row>
        <Field label="The lease term ends (blank: no end date)"><input className="input" type="date" value={endsOn} onChange={(e) => setEndsOn(e.target.value)} /></Field>
        <Field label="Days of notice to move out (optional)"><input className="input num" value={notice} onChange={(e) => setNotice(e.target.value)} inputMode="numeric" style={{ width: "min(100%, 100px)" }} /></Field>
      </Row>
      {ctx.data.rent_bills_to_adopt.length > 0 && rent.trim() !== "" && (
        <div style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 12, color: "var(--warning-ink)", fontWeight: 600 }}>
          <TriangleAlert size={14} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} />
          <span>Your bills list already has {ctx.data.rent_bills_to_adopt.map((b) => `${b.name} (${money(b.amount, { cents: true })} on the ${ordinal(b.due_day)})`).join(" and ")}. If this is the same place, make that bill your home instead, so the rent is not counted twice.</span>
        </div>
      )}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button className="btn dark" type="submit" disabled={act.busy} style={ROW}><House size={14} aria-hidden="true" />{act.busy ? "Adding…" : "Add the home"}</button>
        <Said text={act.note?.tone === "error" ? act.note.text : null} tone="error" />
      </div>
    </form>
  );
}

function AddHomeCard({ ctx, startOpen = false }: { ctx: Ctx; startOpen?: boolean }) {
  const [open, setOpen] = useState(startOpen);
  const [said, setSaid] = useState<string | null>(null);
  return (
    <Card level={2} title="Add a home" sub="The place you live now, or the next one you are moving to. Its costs to get in go on Moving home."
      action={<button type="button" className="btn" aria-expanded={open} style={SMALL} onClick={() => { setOpen(!open); setSaid(null); }}>{open ? "Close" : "Add a home"}</button>}>
      {open && <AddHomeForm ctx={ctx} onDone={(text) => { setOpen(false); setSaid(text); }} />}
      <Said text={said} />
    </Card>
  );
}

function NoHome({ ctx }: { ctx: Ctx }) {
  const bills = ctx.data.rent_bills_to_adopt;
  return (
    <>
      <Empty>
        No home yet. {bills.length ? "Your bills list already has a rent or mortgage bill: make it your home below, and its amount becomes the rent." : "Add the place you live now below: its rent, its fees and its lease's dates."}
      </Empty>
      {bills.map((b) => <AdoptCard key={b.id} bill={b} ctx={ctx} />)}
      <AddHomeCard ctx={ctx} startOpen={bills.length === 0} />
    </>
  );
}

/** Once homes exist, a rent or mortgage bill no home holds: maybe the same rent counted twice. */
function RentBillsNotHeld({ ctx }: { ctx: Ctx }) {
  const [said, setSaid] = useState<string | null>(null);
  const bills = ctx.data.rent_bills_to_adopt;
  if (bills.length === 0 && !said) return null;
  return (
    <>
      {bills.map((b) => <AdoptCard key={b.id} bill={b} ctx={ctx} onDone={setSaid} />)}
      <Said text={said} />
    </>
  );
}

function EarlierHomes({ homes, ctx }: { homes: HomeRow[]; ctx: Ctx }) {
  const [open, setOpen] = useState(false);
  return (
    <section className="card" style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 12 }}>
      <h2 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>
        <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} style={{ display: "inline-flex", alignItems: "center", gap: 6, background: "none", border: 0, padding: 0, minHeight: 32, font: "inherit", color: "var(--ink)", cursor: "pointer" }}>
          <ChevronRight size={16} aria-hidden="true" style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform .15s" }} />
          Earlier homes · {homes.length}
        </button>
      </h2>
      {open && homes.map((h) => <HomeSection key={h.id} h={h} ctx={ctx} />)}
    </section>
  );
}

function LooseUtilities({ ctx }: { ctx: Ctx }) {
  const list = ctx.data.utilities_unassigned;
  return (
    <Card level={2} title="Utilities not tied to a home" sub="Edit one to tie it to a home.">
      {list.map((u) => <UtilityItem key={u.id} u={u} ctx={ctx} />)}
    </Card>
  );
}

// ---- every document --------------------------------------------------------------------------------------------

function AllDocuments({ ctx }: { ctx: Ctx }) {
  const homes = ctx.data.homes;
  const utilities = [...homes.flatMap((h) => h.utilities), ...ctx.data.utilities_unassigned];
  const utilityName = (u: UtilityRow) => `${u.what}${u.provider ? ` (${u.provider})` : ""}${u.home_id ? ` at ${homes.find((h) => h.id === u.home_id)?.name ?? "a home"}` : ""}`;
  const places = [
    ...homes.map((h) => ({ value: `home:${h.id}`, label: h.name })),
    ...utilities.map((u) => ({ value: `utility:${u.id}`, label: utilityName(u) })),
  ];
  function placeOf(attach: string): string {
    if (!attach) return "not kept with anything in particular";
    const [what, id] = attach.split(":");
    if (what === "home") {
      const h = homes.find((x) => x.id === Number(id));
      return h ? `kept with ${h.name}` : "kept with a home that was removed";
    }
    if (what === "utility") {
      const u = utilities.find((x) => x.id === Number(id));
      return u ? `kept with ${utilityName(u)}` : "kept with a utility that was removed";
    }
    return `kept with ${{ bill: "a bill", account: "an account", transaction: "a transaction", debt: "a debt" }[what] ?? "something else"}`;
  }
  return (
    <Card level={2} title="All your documents" sub="Search the text of every document you added, read a page, or download the original.">
      <Documents search places={places} placeOf={placeOf} refreshKey={ctx.docTick} onChanged={ctx.docsChanged} />
    </Card>
  );
}

// ---- the page --------------------------------------------------------------------------------------------------

export function Housing({ onChanged }: { onChanged?: () => void }) {
  const state = useApi<HousingOut>("/api/v1/housing");
  const utilities = useApi<UtilitiesOut>("/api/v1/utilities");
  const [fresh, setFresh] = useState<HousingOut | null>(null);
  const [docTick, setDocTick] = useState(0);
  useEffect(() => setFresh(null), [state.data]);
  const data = fresh ?? state.data;
  useJumpToHash(data);

  const ctx: Ctx | null = data
    ? {
        data,
        utilities: utilities.data,
        apply: (next) => {
          setFresh(next);
          utilities.reload();
          onChanged?.();
        },
        reload: () => {
          state.reload();
          utilities.reload();
          onChanged?.();
        },
        docsChanged: () => {
          state.reload();
          setDocTick((t) => t + 1);
        },
        docTick,
      }
    : null;

  const live = data ? [...data.homes.filter((h) => h.state === "current"), ...data.homes.filter((h) => h.state === "upcoming")] : [];
  const ended = data ? data.homes.filter((h) => h.state === "ended") : [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, letterSpacing: "-0.01em" }}>Housing</h1>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
          Your rent or mortgage, its fees, your utilities and your lease, in one place. Every amount here is counted in safe to spend, <Link to="/cover">Cover</Link> and the <Link to="/outlook">outlook</Link>.
        </div>
      </div>

      {state.error && !data ? (
        <Failed error={state.error} what="your housing" onRetry={state.reload} />
      ) : !data || !ctx ? (
        <Loading what="your housing" />
      ) : (
        <>
          {data.said && (
            <div role="status" style={{ fontSize: 13, color: "var(--ink)", padding: "8px 12px", borderRadius: 10, background: "var(--paycheck-soft)" }}>{data.said}</div>
          )}
          {(data.warnings ?? []).map((w) => (
            <div key={w} role="note" style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: 13, padding: "8px 12px", borderRadius: 10, background: "var(--warning-soft)", color: "var(--warning-ink)" }}>
              <TriangleAlert size={15} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2 }} /><span>{w}</span>
            </div>
          ))}
          {data.move && <MoveTimeline data={data} move={data.move} />}
          {data.homes.length > 0 && <RentBillsNotHeld ctx={ctx} />}
          {(data.homes.length === 0 || live.length > 0) && (
            // /housing#homes: the homes you live in or move to, or the way to add the first one.
            <div id="homes" style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
              {data.homes.length === 0 && <NoHome ctx={ctx} />}
              {live.map((h) => <HomeSection key={h.id} h={h} ctx={ctx} />)}
            </div>
          )}
          {ended.length > 0 && <EarlierHomes homes={ended} ctx={ctx} />}
          {data.utilities_unassigned.length > 0 && <LooseUtilities ctx={ctx} />}
          {utilities.error && !utilities.data && <Failed error={utilities.error} what="the utility choices and the bills a utility can take over" onRetry={utilities.reload} />}
          {data.homes.length > 0 && <AddHomeCard ctx={ctx} />}
          <AllDocuments ctx={ctx} />
          <div style={{ fontSize: 11, color: "var(--muted)" }}>{data.not_advice}</div>
        </>
      )}
    </div>
  );
}
