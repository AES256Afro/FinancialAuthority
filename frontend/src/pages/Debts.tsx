// Debts: the mountain. Every balance as a slice of the whole, the minimums due each month, the interest the
// month costs, and the two classic orders. Then paying it off: the date each debt is gone each way, with the extra
// you name; advance apps by what they cost; money between people. The engine computed all of it; this page draws it.
import { ArrowDownLeft, ArrowUpRight, Info } from "lucide-react";
import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi, type ApiError } from "../api";
import { ConfirmButton } from "../components/ConfirmButton";
import { CreditHealth } from "../components/CreditHealth";
import { dayWord } from "../components/Documents";
import { Empty, Failed, Loading, Said, useWidth } from "../components/State";
import { SeverityTag, StatusPill } from "../components/Status";
import { money, monthYear, parseIso, parseMoney, shortDate } from "../format";
import type { AdvanceCost, PayoffOut, PayoffWay, PersonLoan } from "../types";

interface DebtItem {
  id: number;
  name: string;
  kind: string;
  balance: number | null;
  apr: number | null;
  minimum: number | null;
  share: number;
  source: string;
  credit_limit?: number | null;
  utilisation?: number | null;
  utilisation_status?: { status: "good" | "warn" | "crit"; over_target: boolean; word: string } | null;
  statement_day?: number | null;
  due_day?: number | null;
  pay_in_full?: boolean;
  finance_charge?: number;
  ends?: string;
}

type Note = { text: string; tone: "info" | "error" } | null;

// The outcome is kept by the page, so it stays next to the button after the row reloads with the saved terms.
function Terms({ d, onSaved, said, setSaid }: { d: DebtItem; onSaved: () => void; said: Note; setSaid: (n: Note) => void }) {
  const [apr, setApr] = useState(d.apr !== null && d.apr !== undefined ? String(d.apr) : "");
  const [limit, setLimit] = useState(d.credit_limit ? String(d.credit_limit) : "");
  const [statement, setStatement] = useState(d.statement_day ? String(d.statement_day) : "");
  const [due, setDue] = useState(d.due_day ? String(d.due_day) : "");
  const [minimum, setMinimum] = useState(d.minimum ? String(d.minimum) : "");
  const [full, setFull] = useState(Boolean(d.pay_in_full));
  const [busy, setBusy] = useState(false);
  async function save() {
    const limitValue = limit.trim() ? parseMoney(limit) : null;
    const minimumValue = minimum.trim() ? parseMoney(minimum) : null;
    if ((limit.trim() && limitValue === null) || (minimum.trim() && minimumValue === null)) {
      setSaid({ tone: "error", text: "Type an amount like 120 or 1200.50" });
      return;
    }
    const day = (s: string) => Number(s.trim());
    if ((statement.trim() && !(Number.isInteger(day(statement)) && day(statement) >= 1 && day(statement) <= 31)) || (due.trim() && !(Number.isInteger(day(due)) && day(due) >= 1 && day(due) <= 31))) {
      setSaid({ tone: "error", text: "A day of the month is a number from 1 to 31." });
      return;
    }
    if (apr.trim() && Number.isNaN(Number(apr.trim()))) {
      setSaid({ tone: "error", text: "Type the APR as a number, like 24.99." });
      return;
    }
    setBusy(true);
    setSaid(null);
    try {
      // A box left empty is sent as null, so a term typed by mistake can be cleared.
      await api(`/api/v1/debts/${d.id}`, { method: "PUT", body: JSON.stringify({ apr: apr.trim() ? Number(apr) : null, credit_limit: limitValue, statement_day: statement.trim() ? day(statement) : null, due_day: due.trim() ? day(due) : null, minimum: minimumValue, pay_in_full: full }) });
      setSaid({ tone: "info", text: "Saved." });
      onSaved();
    } catch (e) {
      setSaid({ tone: "error", text: `Couldn't save: ${(e as Error).message}` });
    } finally {
      setBusy(false);
    }
  }
  return (
    <tr>
      <td colSpan={6} style={{ paddingTop: 0 }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end", fontSize: 12 }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 2 }}><span className="lab">APR %</span><input className="input num" style={{ width: 80 }} value={apr} onChange={(e) => setApr(e.target.value)} inputMode="decimal" /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 2 }}><span className="lab">Credit limit</span><input className="input num" style={{ width: 100 }} value={limit} onChange={(e) => setLimit(e.target.value)} inputMode="decimal" /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 2 }}><span className="lab">Statement day</span><input className="input num" style={{ width: 70 }} value={statement} onChange={(e) => setStatement(e.target.value)} inputMode="numeric" /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 2 }}><span className="lab">Due day</span><input className="input num" style={{ width: 70 }} value={due} onChange={(e) => setDue(e.target.value)} inputMode="numeric" /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 2 }}><span className="lab">Minimum</span><input className="input num" style={{ width: 80 }} value={minimum} onChange={(e) => setMinimum(e.target.value)} inputMode="decimal" /></label>
          <label style={{ display: "inline-flex", gap: 6, alignItems: "center", minHeight: 40 }}><input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} style={{ width: 18, height: 18 }} />paid in full each month</label>
          <button className="btn dark" type="button" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save terms"}</button>
          {said && <span style={{ alignSelf: "center" }}><Said text={said.text} tone={said.tone} /></span>}
          {d.utilisation_status && <span style={{ alignSelf: "center" }}><StatusPill status={d.utilisation_status.status}>{d.utilisation_status.word}</StatusPill></span>}
        </div>
      </td>
    </tr>
  );
}

interface Mountain {
  as_of: string;
  total: number;
  items: DebtItem[];
  minimums_month: number;
  interest_month: number;
  bnpl: { total: number; count: number };
  order: { avalanche: string[]; snowball: string[] };
  not_advice: string;
}

// Series colours from the validated palette; the warning colour is kept for status and never used here.
const KIND_TONE: Record<string, string> = { card: "var(--debt)", loan: "var(--debt-ink)", bnpl: "var(--bill)", advance: "var(--later)", medical: "var(--paycheck)" };
const KIND_WORD: Record<string, string> = { card: "card", loan: "loan", bnpl: "pay-later plan", advance: "advance", medical: "medical" };

// One drawing unit is one pixel, so each slice's name and balance stay readable on a phone; a slice too narrow for
// its words keeps them in its tooltip and in the table below.
function MountainChart({ items, total }: { items: DebtItem[]; total: number }) {
  const box = useRef<HTMLDivElement>(null);
  const w = Math.max(260, Math.min(720, useWidth(box, 640)));
  const h = 150;
  const base = h - 24;
  let x = 0;
  const gap = 2;
  const usable = w - gap * Math.max(0, items.length - 1);
  return (
    <div ref={box} style={{ width: "100%", maxWidth: 720 }}>
    <svg viewBox={`0 0 ${w} ${h}`} width={w} height={h} role="img" aria-label={`Debts as slices of the whole: ${items.map((d) => `${d.name} ${money(d.balance ?? 0)}`).join("; ")}`} style={{ display: "block", maxWidth: "100%" }}>
      {items.map((d) => {
        const width = Math.max(2, ((d.balance ?? 0) / Math.max(1, total)) * usable);
        const height = 30 + 90 * ((d.balance ?? 0) / Math.max(1, total));
        const left = x;
        x += width + gap;
        const showLabel = width > 54;
        return (
          <g key={d.source}>
            <title>{`${d.name}: ${money(d.balance ?? 0)}${d.apr ? `, ${d.apr}% APR` : ""}, ${(d.share * 100).toFixed(0)}% of the whole`}</title>
            <rect x={left} y={base - height} width={width} height={height} rx={4} fill={KIND_TONE[d.kind] ?? "var(--muted)"} />
            {showLabel && <text x={left + width / 2} y={base - height - 6} textAnchor="middle" fontSize={11} fontWeight={700} fill="var(--ink-2)">{d.name.length > Math.floor(width / 7) ? d.name.slice(0, Math.max(3, Math.floor(width / 7) - 1)) + "…" : d.name}</text>}
            {showLabel && <text x={left + width / 2} y={h - 6} textAnchor="middle" fontSize={11} fill="var(--muted)" className="num">{money(d.balance ?? 0)}</text>}
          </g>
        );
      })}
      <line x1={0} x2={w} y1={base} y2={base} stroke="var(--ink-2)" strokeWidth={1} />
    </svg>
    </div>
  );
}

const AMOUNT_HINT = "Type an amount like 120 or 1200.50";
const SMALL_BTN = { minHeight: 32, padding: "4px 10px" } as const;
const H2 = { margin: 0, fontSize: 14, fontWeight: 700 } as const;
const DAY_MS = 86_400_000;
// A debt that is not gone within the fifty years the app looks ahead: its bar runs to the edge, striped.
const NOT_GONE = "repeating-linear-gradient(135deg, var(--debt) 0 4px, var(--debt-soft) 4px 8px)";
const WAY_WORD: Record<"avalanche" | "snowball", { title: string; plain: string }> = {
  avalanche: { title: "Highest rate first", plain: "The extra goes to the debt with the highest rate (the avalanche)." },
  snowball: { title: "Smallest balance first", plain: "The extra goes to the smallest balance (the snowball)." },
};

// Where a date sits between today and the last paid-off date of either order, as a share of the width. Placing
// only: nothing here is shown as money or as a date.
function timeline(p: PayoffOut) {
  const from = parseIso(p.as_of).getTime();
  const dates = [p.avalanche, p.snowball].flatMap((w) => [w.debt_free, ...w.debts.map((d) => d.paid_on)]).filter((d): d is string => Boolean(d));
  const end = Math.max(from + 365 * DAY_MS, ...dates.map((d) => parseIso(d).getTime()));
  const share = (iso: string) => Math.min(1, Math.max(0, (parseIso(iso).getTime() - from) / (end - from)));
  const years: number[] = [];
  for (let y = parseIso(p.as_of).getFullYear() + 1; y <= new Date(end).getFullYear(); y++) years.push(y);
  const step = Math.max(1, Math.ceil(years.length / 6));
  const ticks = years.map((y, i) => ({ year: y, at: share(`${y}-01-01`), labelled: i % step === 0 }));
  return { share, ticks };
}

type Scale = ReturnType<typeof timeline>;

function Axis({ scale }: { scale: Scale }) {
  return (
    <div aria-hidden="true" style={{ position: "relative", height: 16, fontSize: 11, color: "var(--muted)" }}>
      <span style={{ position: "absolute", left: 0 }}>Today</span>
      {scale.ticks.filter((t) => t.labelled && t.at > 0.12).map((t) => (
        <span key={t.year} className="num" style={{ position: "absolute", left: `${t.at * 100}%`, transform: t.at > 0.9 ? "translateX(-100%)" : "translateX(-50%)" }}>{t.year}</span>
      ))}
    </div>
  );
}

// One order: a line per debt from today to the month it is paid off, earliest first, then the dates and the
// interest in words. The thin gaps in each bar are the turns of the year.
function OrderColumn({ way, kind, scale, cheapest }: { way: PayoffWay; kind: "avalanche" | "snowball"; scale: Scale; cheapest: boolean }) {
  const rows = [...way.debts].sort((a, b) => (a.paid_on ?? "9999").localeCompare(b.paid_on ?? "9999"));
  const id = `payoff-${kind}`;
  return (
    <section aria-labelledby={id} style={{ display: "flex", flexDirection: "column", gap: 8, padding: 12, borderRadius: 10, border: "1px solid var(--hairline)", minWidth: 0 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <h3 id={id} style={{ margin: 0, fontSize: 14, fontWeight: 700, flex: "1 1 auto" }}>{WAY_WORD[kind].title}</h3>
        {cheapest && <StatusPill status="good">Cheapest: the least interest</StatusPill>}
      </div>
      <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{WAY_WORD[kind].plain}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {rows.map((d) => (
          <div key={d.source} style={{ display: "flex", flexDirection: "column", gap: 3 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 12, flexWrap: "wrap" }}>
              <strong style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={d.name}>{d.name}</strong>
              <span className="num" style={{ color: "var(--ink-2)" }}>{d.paid_on ? `paid off ${monthYear(d.paid_on)} · ${money(d.interest ?? 0)} interest` : "not paid off within 50 years: the balance grows"}</span>
            </div>
            <div role="img" aria-label={`${d.name}: ${d.paid_on ? `paid off ${monthYear(d.paid_on)}, ${money(d.interest ?? 0)} in interest` : "not paid off within 50 years, the balance grows"}`} style={{ position: "relative", height: 12, background: "var(--surface-2)", borderRadius: 4, overflow: "hidden" }}>
              <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: d.paid_on ? `${scale.share(d.paid_on) * 100}%` : "100%", background: d.paid_on ? "var(--debt)" : NOT_GONE, borderRadius: 4 }} />
              {scale.ticks.map((t) => <div key={t.year} style={{ position: "absolute", left: `${t.at * 100}%`, top: 0, bottom: 0, width: 1, background: "var(--surface)" }} />)}
            </div>
          </div>
        ))}
      </div>
      <Axis scale={scale} />
      <div style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 13 }}>
        <div className="num" style={{ fontSize: 17, fontWeight: 800 }}>{way.debt_free ? `Debt-free ${monthYear(way.debt_free)}` : "Not debt-free within 50 years"}</div>
        {way.months !== null && <div className="num" style={{ color: "var(--ink-2)" }}>in {way.months} {way.months === 1 ? "month" : "months"}</div>}
        {way.interest !== null ? (
          <div className="num">Interest along the way: <strong>{money(way.interest)}</strong></div>
        ) : (
          <div>The minimum does not cover the interest, so the balance grows; there is no interest figure to show.</div>
        )}
        {way.interest_saved !== null && way.interest_saved !== undefined ? (
          <div className="num" style={{ color: "var(--ink-2)" }}>{money(way.interest_saved)} less interest than paying only the minimums</div>
        ) : (
          <div style={{ color: "var(--ink-2)" }}>Paying only the minimums never clears them, so there is no saving to compare.</div>
        )}
      </div>
    </section>
  );
}

// The interest each way on one scale: only the minimums, then the two orders with the extra.
function InterestBars({ p }: { p: PayoffOut }) {
  const rows = [
    { label: "Only the minimums", value: p.minimums_only.interest ?? 0 },
    { label: WAY_WORD.avalanche.title, value: p.avalanche.interest ?? 0 },
    { label: WAY_WORD.snowball.title, value: p.snowball.interest ?? 0 },
  ]; // a way that never finishes has no interest figure and draws as nothing
  const scale = Math.max(1, ...rows.map((r) => r.value));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, maxWidth: 560 }}>
      <h3 style={{ margin: 0, fontSize: 13, fontWeight: 700 }}>Interest each way</h3>
      {rows.map((r) => (
        <div key={r.label} style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", gap: "2px 10px", alignItems: "center", fontSize: 13 }}>
          <span>{r.label}</span>
          <strong className="num" style={{ textAlign: "right" }}>{money(r.value)}</strong>
          <div role="img" aria-label={`${r.label}: ${money(r.value)} in interest`} style={{ gridColumn: "1 / -1", position: "relative", height: 10, background: "var(--surface-2)", borderRadius: 4 }}>
            <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${Math.min(100, (r.value / scale) * 100)}%`, background: "var(--debt)", borderRadius: 4 }} />
          </div>
        </div>
      ))}
    </div>
  );
}

// Advance apps by what they cost: the fee or tip for each $100 over the last 90 days, and the yearly rate a card
// would need to cost the same. The bar is that yearly rate, the costliest provider the full width.
function AdvanceCosts({ items }: { items: AdvanceCost[] }) {
  const scale = Math.max(1, ...items.map((a) => a.as_yearly_rate));
  const rate = (n: number) => `${n.toLocaleString("en-US", { maximumFractionDigits: 1 })}%`;
  return (
    <section className="card" aria-labelledby="advance-cost-title" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
      <h2 id="advance-cost-title" style={H2}>Advance apps, by what they cost</h2>
      <div style={{ fontSize: 12, color: "var(--ink-2)" }}>The fee or tip you paid for each $100 taken over the last 90 days, and the yearly rate a card would have to charge to cost the same for as long as you held the money.</div>
      {items.length === 0 ? (
        <Empty card={false}>No advances taken in the last 90 days.</Empty>
      ) : (
        items.map((a) => (
          <div key={a.provider} style={{ display: "flex", flexDirection: "column", gap: 4, borderTop: "1px solid var(--surface-2)", paddingTop: 8 }}>
            <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap", fontSize: 13 }}>
              <strong style={{ flex: "1 1 120px", minWidth: 0 }}>{a.provider}</strong>
              <span className="num">{money(a.fee_per_100, { cents: true })} for each $100</span>
              <strong className="num">{rate(a.as_yearly_rate)} a year</strong>
            </div>
            <div role="img" aria-label={`${a.provider}: as a yearly rate, ${rate(a.as_yearly_rate)}`} style={{ position: "relative", height: 10, background: "var(--surface-2)", borderRadius: 4, maxWidth: 560 }}>
              <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${Math.min(100, (a.as_yearly_rate / scale) * 100)}%`, background: "var(--later)", borderRadius: 4 }} />
            </div>
            <div className="num" style={{ fontSize: 12, color: "var(--ink-2)" }}>
              {a.advances === 1 ? "1 advance" : `${a.advances} advances`}: {money(a.taken)} taken, {money(a.fees, { cents: true })} in fees or tips, each held about {a.days_held} days.
            </div>
          </div>
        ))
      )}
      <div style={{ fontSize: 12 }}><Link to="/borrowing">Open Borrowing</Link> for each advance and when it is repaid.</div>
    </section>
  );
}

// One person loan changed in place: who, which way, how much, the day and the note. Sent whole (a PUT replaces).
function PersonEdit({ index, r, onSaved, onCancel }: { index: number; r: PersonLoan; onSaved: (text: string) => void; onCancel: () => void }) {
  const [person, setPerson] = useState(r.person);
  const [direction, setDirection] = useState<"lent" | "borrowed">(r.direction);
  const [amount, setAmount] = useState(String(r.amount));
  const [on, setOn] = useState(r.on);
  const [note, setNote] = useState(r.note);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<Note>(null);
  async function save(e: React.FormEvent) {
    e.preventDefault();
    const name = person.trim();
    if (!name) return setSaid({ tone: "error", text: "Type the person's name." });
    const value = parseMoney(amount);
    if (value === null || value <= 0) return setSaid({ tone: "error", text: AMOUNT_HINT });
    setBusy(true);
    setSaid(null);
    try {
      await api<{ people: PersonLoan[] }>(`/api/v1/debts/payoff/people/${index}`, { method: "PUT", body: JSON.stringify({ person: name, direction, amount: value, on: on || null, note: note.trim(), settled: r.settled }) });
      onSaved(`Changed: ${direction === "lent" ? `you lent ${name}` : `you borrowed from ${name}`} ${money(value, { cents: true })}.`);
    } catch (err) {
      setSaid({ tone: "error", text: `Couldn't change it: ${(err as Error).message}` });
      setBusy(false);
    }
  }
  return (
    <form onSubmit={save} style={{ display: "flex", flexDirection: "column", gap: 8, padding: 10, borderRadius: 10, background: "var(--surface-2)" }}>
      <fieldset style={{ border: 0, padding: 0, margin: 0, display: "flex", gap: 16, flexWrap: "wrap" }}>
        <legend className="lab" style={{ padding: 0, marginBottom: 4 }}>Which way the money went</legend>
        <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, minHeight: 36 }}>
          <input type="radio" name={`person-edit-${index}`} checked={direction === "lent"} onChange={() => setDirection("lent")} style={{ width: 18, height: 18 }} />
          I lent money to them
        </label>
        <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, minHeight: 36 }}>
          <input type="radio" name={`person-edit-${index}`} checked={direction === "borrowed"} onChange={() => setDirection("borrowed")} style={{ width: 18, height: 18 }} />
          I borrowed money from them
        </label>
      </fieldset>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 160px", minWidth: 0 }}>
          <span className="lab">Person</span>
          <input className="input" value={person} onChange={(e) => setPerson(e.target.value)} autoComplete="off" />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="lab">Amount</span>
          <input className="input num" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} style={{ width: "min(100%, 130px)" }} />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="lab">On</span>
          <input className="input" type="date" value={on} onChange={(e) => setOn(e.target.value)} />
        </label>
      </div>
      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span className="lab">Note (optional)</span>
        <input className="input" value={note} onChange={(e) => setNote(e.target.value)} maxLength={200} />
      </label>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button type="submit" className="btn dark" style={SMALL_BTN} disabled={busy}>{busy ? "Saving…" : "Keep the changes"}</button>
        <button type="button" className="btn" style={SMALL_BTN} onClick={onCancel}>Cancel</button>
        <Said text={said?.text ?? null} tone={said?.tone} />
      </div>
    </form>
  );
}

// Money lent to or borrowed from people: a list kept by hand, with no rate. Settling is a switch, so it can be
// turned back; an entry typed wrong can be changed or removed.
function People({ rows, onChanged }: { rows: PersonLoan[]; onChanged: () => void }) {
  const [person, setPerson] = useState("");
  const [direction, setDirection] = useState<"lent" | "borrowed">("lent");
  const [amount, setAmount] = useState("");
  const [on, setOn] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [formSaid, setFormSaid] = useState<Note>(null);
  const [rowSaid, setRowSaid] = useState<Record<number, Note>>({});
  const [editing, setEditing] = useState<number | null>(null);
  const [listSaid, setListSaid] = useState<string | null>(null);

  async function removeRow(index: number, r: PersonLoan) {
    try {
      await api<{ people: PersonLoan[] }>(`/api/v1/debts/payoff/people/${index}`, { method: "DELETE" });
    } catch (err) {
      if ((err as ApiError).status === 405) throw new Error("This version of the app cannot remove an entry yet. Change it with Edit, or mark it as settled.");
      throw err;
    }
    setEditing(null);
    setRowSaid({});
    setListSaid(`Removed: ${r.direction === "lent" ? `you lent ${r.person}` : `you borrowed from ${r.person}`} ${money(r.amount, { cents: true })}.`);
    onChanged();
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    const name = person.trim();
    if (!name) {
      setFormSaid({ tone: "error", text: "Type the person's name." });
      return;
    }
    const value = parseMoney(amount);
    if (value === null || value <= 0) {
      setFormSaid({ tone: "error", text: AMOUNT_HINT });
      return;
    }
    setBusy("form");
    setFormSaid(null);
    try {
      await api<{ people: PersonLoan[] }>("/api/v1/debts/payoff/people", { method: "POST", body: JSON.stringify({ person: name, direction, amount: value, on: on || null, note: note.trim(), settled: false }) });
      setFormSaid({ tone: "info", text: `Added: ${direction === "lent" ? `you lent ${name}` : `you borrowed from ${name}`} ${money(value, { cents: true })}.` });
      setPerson("");
      setAmount("");
      setOn("");
      setNote("");
      onChanged();
    } catch (err) {
      setFormSaid({ tone: "error", text: `Couldn't add it: ${(err as Error).message}` });
    } finally {
      setBusy(null);
    }
  }

  async function settle(index: number, r: PersonLoan) {
    setBusy(`row-${index}`);
    setRowSaid((all) => ({ ...all, [index]: null }));
    try {
      await api<{ people: PersonLoan[] }>(`/api/v1/debts/payoff/people/${index}`, { method: "PUT", body: JSON.stringify({ ...r, settled: !r.settled }) });
      setRowSaid((all) => ({ ...all, [index]: { tone: "info", text: r.settled ? "Marked as not settled." : "Marked as settled." } }));
      onChanged();
    } catch (err) {
      setRowSaid((all) => ({ ...all, [index]: { tone: "error", text: `Couldn't change it: ${(err as Error).message}` } }));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="card" aria-labelledby="people-title" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
      <h2 id="people-title" style={H2}>Money between people</h2>
      <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Money you lent to someone or borrowed from someone, kept here so it is not forgotten. It has no rate and is not counted in your debts above.</div>
      {rows.length === 0 ? (
        <Empty card={false}>Nothing on the list. Add a loan to or from a person below.</Empty>
      ) : (
        <div style={{ display: "flex", flexDirection: "column" }}>
          {rows.map((r, i) => {
            const Icon = r.direction === "lent" ? ArrowUpRight : ArrowDownLeft;
            const said = rowSaid[i];
            return (
              <div key={`${i}-${r.person}-${r.on}`} style={{ display: "flex", flexDirection: "column", gap: 4, padding: "8px 0", borderTop: i === 0 ? 0 : "1px solid var(--surface-2)", opacity: r.settled ? 0.75 : 1 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", fontSize: 13 }}>
                  <Icon size={16} aria-hidden="true" style={{ flexShrink: 0, color: "var(--ink-2)" }} />
                  <span style={{ flex: "1 1 180px", minWidth: 0 }}>
                    <strong>{r.direction === "lent" ? `You lent ${r.person}` : `You borrowed from ${r.person}`}</strong>
                    <span style={{ color: "var(--ink-2)" }}> · {shortDate(r.on)}</span>
                  </span>
                  <strong className="num">{money(r.amount, { cents: true })}</strong>
                  {r.settled ? <StatusPill status="good">Settled</StatusPill> : <SeverityTag severity="info" word="Still open" />}
                </div>
                {r.note && <div style={{ fontSize: 12, color: "var(--ink-2)", paddingLeft: 24 }}>{r.note}</div>}
                {editing === i ? (
                  <div style={{ paddingLeft: 24 }}>
                    <PersonEdit index={i} r={r} onCancel={() => setEditing(null)} onSaved={(text) => { setEditing(null); setRowSaid((all) => ({ ...all, [i]: { tone: "info", text } })); onChanged(); }} />
                  </div>
                ) : (
                  <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", paddingLeft: 24 }}>
                    <button type="button" className="btn" style={SMALL_BTN} disabled={busy === `row-${i}`} onClick={() => settle(i, r)}>{r.settled ? "Mark as not settled" : "Mark as settled"}</button>
                    <button type="button" className="btn" style={SMALL_BTN} onClick={() => { setListSaid(null); setEditing(i); }}>Edit</button>
                    <ConfirmButton label="Remove" name={`Remove the loan ${r.direction === "lent" ? "to" : "from"} ${r.person}`} style={SMALL_BTN} question={`Take ${r.direction === "lent" ? `the loan to ${r.person}` : `the loan from ${r.person}`} off the list?`} yes="Yes, remove it" no="Keep it" onConfirm={() => removeRow(i, r)} />
                    {said && <Said text={said.text} tone={said.tone} />}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
      <Said text={listSaid} />
      <form onSubmit={add} style={{ display: "flex", flexDirection: "column", gap: 10, borderTop: "1px solid var(--hairline)", paddingTop: 10 }}>
        <h3 style={{ margin: 0, fontSize: 13, fontWeight: 700 }}>Add to the list</h3>
        <fieldset style={{ border: 0, padding: 0, margin: 0, display: "flex", gap: 16, flexWrap: "wrap" }}>
          <legend className="lab" style={{ padding: 0, marginBottom: 4 }}>Which way the money went</legend>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, minHeight: 36 }}>
            <input type="radio" name="person-direction" checked={direction === "lent"} onChange={() => setDirection("lent")} style={{ width: 18, height: 18 }} />
            I lent money to them
          </label>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, minHeight: 36 }}>
            <input type="radio" name="person-direction" checked={direction === "borrowed"} onChange={() => setDirection("borrowed")} style={{ width: 18, height: 18 }} />
            I borrowed money from them
          </label>
        </fieldset>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 160px", minWidth: 0 }}>
            <span className="lab">Person</span>
            <input className="input" value={person} onChange={(e) => setPerson(e.target.value)} autoComplete="off" />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span className="lab">Amount</span>
            <input className="input num" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} style={{ width: "min(100%, 130px)" }} />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span className="lab">On (empty means today)</span>
            <input className="input" type="date" value={on} onChange={(e) => setOn(e.target.value)} />
          </label>
        </div>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="lab">Note (optional)</span>
          <input className="input" value={note} onChange={(e) => setNote(e.target.value)} maxLength={200} />
        </label>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
          <button type="submit" className="btn dark" disabled={busy === "form"}>{busy === "form" ? "Adding…" : "Add to the list"}</button>
          <Said text={formSaid?.text ?? null} tone={formSaid?.tone} />
        </div>
      </form>
    </section>
  );
}

// Paying it off: the extra each month is asked of the app again whenever it changes. `changed` goes up when the
// debts' terms are saved above, so the dates follow.
function Payoff({ changed }: { changed: number }) {
  const [text, setText] = useState("");
  const [extra, setExtra] = useState(0);
  const [said, setSaid] = useState<Note>(null);
  const state = useApi<PayoffOut>(`/api/v1/debts/payoff?extra=${extra}`);
  const { reload } = state;
  // reload is a new function each render; only a save above should ask again.
  useEffect(() => {
    if (changed > 0) reload();
  }, [changed]); // eslint-disable-line react-hooks/exhaustive-deps

  function ask(e: React.FormEvent) {
    e.preventDefault();
    const value = text.trim() === "" ? 0 : parseMoney(text);
    if (value === null || value < 0) {
      setSaid({ tone: "error", text: AMOUNT_HINT });
      return;
    }
    if (value > 100_000) {
      setSaid({ tone: "error", text: "Type an extra of 100,000 or less." });
      return;
    }
    setSaid(null);
    if (value === extra) reload();
    else setExtra(value);
  }

  const p = state.data;
  const working = state.loading && p !== null;
  const assumed = p ? p.debts.filter((d) => d.minimum_assumed).map((d) => d.name) : [];
  const same = p ? p.avalanche.interest === p.snowball.interest : false;
  const scale = p ? timeline(p) : null;
  return (
    <>
      <section className="card" aria-labelledby="payoff-title" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
        <h2 id="payoff-title" style={H2}>Paying it off</h2>
        <form onSubmit={ask} style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span className="lab">Extra each month, on top of the minimums</span>
            <input className="input num" inputMode="decimal" value={text} onChange={(e) => setText(e.target.value)} placeholder="0" style={{ width: "min(100%, 160px)" }} />
          </label>
          <button type="submit" className="btn dark" disabled={working}>{working ? "Working it out…" : "Work it out"}</button>
          <Said text={said?.text ?? null} tone={said?.tone} />
          {state.error && p && <Said text={`Couldn't work it out: ${state.error.message}`} tone="error" />}
        </form>
        {state.error && !p && <Failed card={false} error={state.error} what="the payoff dates" onRetry={reload} />}
        {!state.error && !p && <Loading card={false} what="the payoff dates" />}
        {p && scale && (
          p.debts.length === 0 ? (
            <Empty card={false}>No debts with a balance to pay off. Cards, loans, pay-later plans and medical bills appear here once they have a balance.</Empty>
          ) : (
            <>
              <div className="num" style={{ fontSize: 13, color: "var(--ink-2)" }}>
                Worked out with <strong style={{ color: "var(--ink)" }}>{money(p.extra, { cents: true })}</strong> extra each month on top of the minimums ({money(p.minimums_total, { cents: true })} a month together). When a debt is gone, its minimum moves on to the next one.
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(300px, 100%), 1fr))", gap: 12 }}>
                <OrderColumn way={p.avalanche} kind="avalanche" scale={scale} cheapest={!same && p.cheapest === "avalanche"} />
                <OrderColumn way={p.snowball} kind="snowball" scale={scale} cheapest={!same && p.cheapest === "snowball"} />
              </div>
              {same && <div style={{ fontSize: 13 }}>Both orders cost the same interest here.</div>}
              <InterestBars p={p} />
              <div className="num" style={{ fontSize: 13, color: "var(--ink-2)" }}>
                Paying only the minimums: {p.minimums_only.debt_free ? `debt-free ${monthYear(p.minimums_only.debt_free)}${p.minimums_only.months !== null ? `, in ${p.minimums_only.months} months` : ""}, with ${money(p.minimums_only.interest ?? 0)} in interest.` : "not debt-free within 50 years; the minimum does not cover the interest, so the balance grows."}
              </div>
              {assumed.length > 0 && (
                <div style={{ display: "flex", alignItems: "flex-start", gap: 6, fontSize: 12, color: "var(--ink-2)", background: "var(--surface-2)", borderRadius: 10, padding: "8px 10px" }}>
                  <Info size={14} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} />
                  <span>{p.note} Assumed for: {assumed.join(", ")}.</span>
                </div>
              )}
              <div style={{ fontSize: 11, color: "var(--muted)" }}>{p.not_advice}</div>
            </>
          )
        )}
      </section>
      {p && <AdvanceCosts items={p.advances} />}
      {p && <People rows={p.people} onChanged={reload} />}
    </>
  );
}

export function Debts() {
  const { data, error, reload } = useApi<Mountain>("/api/v1/debts");
  const [notes, setNotes] = useState<Record<string, Note>>({});
  const [changed, setChanged] = useState(0);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 980 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Debts</h1>
      {error && !data && <Failed error={error} what="your debts" onRetry={reload} />}
      {!data && !error && <Loading what="your debts" />}
      {data && (
        <>
          <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "flex", gap: 22, flexWrap: "wrap", alignItems: "baseline" }}>
              <div><div className="lab">Owed in all</div><div className="num" style={{ fontSize: 34, fontWeight: 800, letterSpacing: "-0.02em", lineHeight: 1.1 }}>{money(data.total)}</div></div>
              <div><div className="lab">Minimums each month</div><div className="num" style={{ fontSize: 22, fontWeight: 800 }}>{money(data.minimums_month)}</div></div>
              <div><div className="lab">Interest this month costs</div><div className="num" style={{ fontSize: 22, fontWeight: 800, color: "var(--bill-ink)" }}>{money(data.interest_month)}</div></div>
            </div>
            <MountainChart items={data.items} total={data.total} />
            <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12, color: "var(--ink-2)" }}>
              {Object.entries(KIND_WORD).filter(([k]) => data.items.some((d) => d.kind === k)).map(([k, word]) => (
                <span key={k} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span className="sw" style={{ background: KIND_TONE[k], margin: 0 }} />{word}</span>
              ))}
            </div>
          </section>

          {data.items.some((d) => d.kind === "bnpl") && (
            <section className="card" style={{ padding: 14, display: "flex", gap: 22, flexWrap: "wrap", alignItems: "baseline" }}>
              <div><div className="lab">Pay-later plans, still to pay</div><div className="num" style={{ fontSize: 22, fontWeight: 800 }}>{money(data.bnpl.total)}</div></div>
              <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{data.bnpl.count} plans · the lender's own schedule where the confirmation email was read, else the payments left you entered · <Link to="/borrowing#loans">see each loan</Link></div>
            </section>
          )}
          <section className="card" style={{ padding: "6px 14px", overflowX: "auto" }}>
            <table className="ledger">
              <thead><tr><th>Debt</th><th>Kind</th><th style={{ textAlign: "right" }}>Balance</th><th style={{ textAlign: "right" }}>APR</th><th style={{ textAlign: "right" }}>Minimum</th><th style={{ textAlign: "right" }}>Share</th></tr></thead>
              <tbody>
                {data.items.map((d) => (
                  <React.Fragment key={d.source}>
                    <tr>
                      <td><strong>{d.name}</strong>{d.credit_limit ? <div style={{ fontSize: 11, color: "var(--muted)" }}>credit limit {money(d.credit_limit)}{d.statement_day ? ` · statement day ${d.statement_day}` : ""}{d.due_day ? ` · due day ${d.due_day}` : ""}</div> : null}{d.kind === "bnpl" ? <div style={{ fontSize: 11, color: "var(--muted)" }}>{d.ends ? `ends ${dayWord(d.ends)}` : ""}{d.finance_charge ? ` · ${money(d.finance_charge)} finance charge over its life` : " · 0% as far as your transactions show"}</div> : null}</td>
                      <td style={{ color: "var(--ink-2)" }}>{KIND_WORD[d.kind] ?? d.kind}</td>
                      <td className="num" style={{ textAlign: "right" }}>{money(d.balance ?? 0)}</td>
                      <td className="num" style={{ textAlign: "right", color: "var(--ink-2)" }}>{d.apr !== null && d.apr !== undefined ? `${d.apr}%` : "—"}</td>
                      <td className="num" style={{ textAlign: "right", color: "var(--ink-2)" }}>{d.minimum ? money(d.minimum) : "—"}</td>
                      <td className="num" style={{ textAlign: "right", color: "var(--muted)" }}>{(d.share * 100).toFixed(0)}%</td>
                    </tr>
                    {d.source.startsWith("debt:") && (d.kind === "card" || d.kind === "loan") && <Terms key={`${d.source}-${d.apr}-${d.credit_limit}-${d.statement_day}-${d.due_day}-${d.minimum}-${String(d.pay_in_full)}`} d={d} onSaved={() => { reload(); setChanged((n) => n + 1); }} said={notes[d.source] ?? null} setSaid={(n) => setNotes((all) => ({ ...all, [d.source]: n }))} />}
                  </React.Fragment>
                ))}
                {data.items.length === 0 && <tr><td colSpan={6} style={{ color: "var(--muted)" }}>No debts on record. Cards, loans, pay-later plans and advances appear here as they are confirmed.</td></tr>}
              </tbody>
            </table>
          </section>

          {(data.order.avalanche.length > 0 || data.order.snowball.length > 0) && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(280px, 100%), 1fr))", gap: 14 }}>
              <section className="card" style={{ padding: 14 }}>
                <h2 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>Highest rate first</h2>
                <div style={{ fontSize: 12, color: "var(--ink-2)", marginBottom: 6 }}>Pays the least interest over time.</div>
                <ol style={{ margin: 0, paddingLeft: 20, fontSize: 13, lineHeight: 1.7 }}>{data.order.avalanche.map((n) => <li key={n}>{n}</li>)}</ol>
              </section>
              <section className="card" style={{ padding: 14 }}>
                <h2 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>Smallest balance first</h2>
                <div style={{ fontSize: 12, color: "var(--ink-2)", marginBottom: 6 }}>Clears whole debts sooner, which keeps people going.</div>
                <ol style={{ margin: 0, paddingLeft: 20, fontSize: 13, lineHeight: 1.7 }}>{data.order.snowball.map((n) => <li key={n}>{n}</li>)}</ol>
              </section>
            </div>
          )}
          <CreditHealth />
          <Payoff changed={changed} />
          <div style={{ fontSize: 12, color: "var(--muted)" }}>{data.not_advice} Pay-later plans count what is left to pay; advances count the amount with its fee.</div>
        </>
      )}
    </div>
  );
}
