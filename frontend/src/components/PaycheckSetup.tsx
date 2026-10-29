// The paycheck on file and a paystub handed over by hand: how often you are paid (and on which days of the month),
// the usual take-home pay per check, and a PDF upload that goes to Needs you. With no paycheck on file yet, it can
// be added here by hand, or taken from a confirmed paystub. The owner's figures stand until the stubs refine them.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { money, ordinal, parseMoney, shortDate, when } from "../format";
import { Failed, Loading, Said } from "./State";

interface IncomeRow {
  id: number;
  name: string;
  cadence: string;
  expected_net: number;
  expected_net_end: number | null;
  next_date: string;
  /** Semi-monthly only: "" is the 15th and the last day, "1,15" the 1st and the 15th. */
  pay_days?: string;
  /** Monthly only: the day of the month it pays; 31 is the last day of every month. */
  pay_day?: number | null;
}

/** The latest confirmed paystub, offered as the paycheck when none is on file. */
export interface StubOffer {
  id: number;
  pay_date: string;
  net: number;
}

type Note = { text: string; tone: "info" | "error" } | null;

// One choice per real schedule. The two semi-monthly ones differ only in their days of the month (pay_days).
type Schedule = "weekly" | "biweekly" | "semi_15_end" | "semi_1_15" | "monthly";
const SCHEDULES: [Schedule, string][] = [
  ["weekly", "every week"],
  ["biweekly", "every two weeks"],
  ["semi_15_end", "twice a month: the 15th and the last day"],
  ["semi_1_15", "twice a month: the 1st and the 15th"],
  ["monthly", "once a month"],
];
const CADENCES: [string, string][] = [["weekly", "every week"], ["biweekly", "every two weeks"], ["semimonthly", "twice a month"], ["monthly", "once a month"]];
const AMOUNT_HINT = "Type an amount like 120 or 1200.50";
const DAY_HINT = "Type a day of the month from 1 to 31 (31 is the last day of every month).";
const FIELD = { display: "flex", flexDirection: "column", gap: 4 } as const;

function scheduleOf(cadence: string, payDays: string | undefined): Schedule {
  if (cadence === "semimonthly") return payDays === "1,15" ? "semi_1_15" : "semi_15_end";
  return (["weekly", "biweekly", "monthly"].includes(cadence) ? cadence : "biweekly") as Schedule;
}

/** What the server keeps for a schedule: its cadence, and the semi-monthly days when it has them. */
function cadenceOf(s: Schedule): { cadence: string; pay_days?: string } {
  if (s === "semi_15_end") return { cadence: "semimonthly", pay_days: "" };
  if (s === "semi_1_15") return { cadence: "semimonthly", pay_days: "1,15" };
  return { cadence: s };
}

/** A typed day of the month, 1 to 31, or null. */
function parseDay(text: string): number | null {
  if (!/^\d{1,2}$/.test(text.trim())) return null;
  const n = Number(text.trim());
  return n >= 1 && n <= 31 ? n : null;
}

function dayWord(n: number): string {
  return n === 31 ? "the last day of the month" : `the ${ordinal(n)}`;
}

function IncomeEditor({ i, onSaved }: { i: IncomeRow; onSaved: () => void }) {
  const endOf = (x: IncomeRow) => (x.expected_net_end === null ? "" : String(x.expected_net_end));
  const dayOf = (x: IncomeRow) => (x.pay_day ? String(x.pay_day) : "");
  const [schedule, setSchedule] = useState<Schedule>(scheduleOf(i.cadence, i.pay_days));
  const [net, setNet] = useState(String(i.expected_net));
  const [netEnd, setNetEnd] = useState(endOf(i));
  const [nextDate, setNextDate] = useState(i.next_date);
  const [payDay, setPayDay] = useState(dayOf(i));
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  const saved = [i.cadence, i.pay_days, i.pay_day, i.expected_net, i.expected_net_end, i.next_date].join("|");
  useEffect(() => {
    // After a save, the fields show what the server now holds.
    setSchedule(scheduleOf(i.cadence, i.pay_days));
    setNet(String(i.expected_net));
    setNetEnd(endOf(i));
    setNextDate(i.next_date);
    setPayDay(dayOf(i));
  }, [saved]); // eslint-disable-line react-hooks/exhaustive-deps
  const semi = schedule === "semi_15_end" || schedule === "semi_1_15";
  const monthly = schedule === "monthly";
  const dayChanged = monthly && payDay !== dayOf(i);
  const dirty = schedule !== scheduleOf(i.cadence, i.pay_days) || parseMoney(net) !== i.expected_net || nextDate !== i.next_date || (semi && netEnd !== endOf(i)) || dayChanged;

  async function save() {
    const value = parseMoney(net);
    const end = semi && netEnd.trim() ? parseMoney(netEnd) : undefined;
    if (value === null || value < 0 || end === null) {
      setNote({ text: AMOUNT_HINT, tone: "error" });
      return;
    }
    const day = dayChanged ? parseDay(payDay) : undefined;
    if (day === null) {
      setNote({ text: DAY_HINT, tone: "error" });
      return;
    }
    setBusy(true);
    setNote(null);
    try {
      // The day of the month goes only when it was changed here; otherwise the server keeps it from the next payday.
      // The end check emptied on a twice-a-month paycheck goes as null: "the same as the mid check", cleared on the server.
      const body = { ...cadenceOf(schedule), expected_net: value, expected_net_end: semi && !netEnd.trim() ? null : end, next_date: nextDate, ...(day ? { pay_day: day } : {}) };
      await api(`/api/v1/incomes/${i.id}`, { method: "PUT", body: JSON.stringify(body) });
      setNote({ text: "Saved. Every screen uses it now.", tone: "info" });
      onSaved();
    } catch (e) {
      setNote({ text: (e as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  // The two checks of a semi-monthly paycheck are named by their days: the 15th check is the usual take-home pay,
  // the other (the 1st, or the last day) has its own field.
  const midField = <label key="mid" style={FIELD}><span className="lab">{semi ? "Take-home pay, the 15th check" : "Usual take-home pay"}</span><input className="input num" style={{ width: 120 }} value={net} onChange={(e) => setNet(e.target.value)} inputMode="decimal" /></label>;
  const endField = <label key="end" style={FIELD}><span className="lab">{schedule === "semi_1_15" ? "Take-home pay, the 1st check" : "Take-home pay, the end-of-month check"}</span><input className="input num" style={{ width: 120 }} value={netEnd} onChange={(e) => setNetEnd(e.target.value)} inputMode="decimal" placeholder="same" /></label>;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "flex-end" }}>
      <div style={{ flex: "1 1 160px" }}><strong>{i.name}</strong></div>
      <label style={FIELD}><span className="lab">How often you're paid</span>
        <select className="input" value={schedule} onChange={(e) => setSchedule(e.target.value as Schedule)}>{SCHEDULES.map(([v, w]) => <option key={v} value={v}>{w}</option>)}</select>
      </label>
      {semi ? (schedule === "semi_1_15" ? [endField, midField] : [midField, endField]) : midField}
      {monthly && (
        <label style={FIELD}><span className="lab">Day of the month it pays</span>
          <input className="input" style={{ width: 90 }} value={payDay} onChange={(e) => setPayDay(e.target.value)} inputMode="numeric" placeholder="from the date" aria-describedby={`payday-${i.id}`} />
          <span id={`payday-${i.id}`} style={{ fontSize: 11, color: "var(--ink-2)" }}>31 is the last day of every month</span>
        </label>
      )}
      <label style={FIELD}><span className="lab">Next payday</span><input className="input" type="date" value={nextDate} onChange={(e) => setNextDate(e.target.value)} /></label>
      <button className="btn dark" type="button" disabled={!dirty || busy} onClick={save}>{busy ? "Saving…" : "Save"}</button>
      {note && <Said text={note.text} tone={note.tone} />}
      {monthly && i.pay_day ? <span style={{ flexBasis: "100%", fontSize: 12, color: "var(--ink-2)" }}>Paid on {dayWord(i.pay_day)} each month.</span> : null}
    </div>
  );
}

// No paycheck on file: add one by hand. The plan counts no income until one is here.
function AddPaycheck({ onAdded }: { onAdded: () => void }) {
  const [name, setName] = useState("Paycheck");
  const [schedule, setSchedule] = useState<Schedule>("biweekly");
  const [net, setNet] = useState("");
  const [nextDate, setNextDate] = useState("");
  const [payDay, setPayDay] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  const monthly = schedule === "monthly";
  const semi = schedule === "semi_15_end" || schedule === "semi_1_15";

  async function add(e: React.FormEvent) {
    e.preventDefault();
    const value = parseMoney(net);
    if (value === null || value <= 0) {
      setNote({ text: AMOUNT_HINT, tone: "error" });
      return;
    }
    if (!nextDate) {
      setNote({ text: "Pick the date of your next payday.", tone: "error" });
      return;
    }
    const day = monthly && payDay.trim() ? parseDay(payDay) : undefined;
    if (day === null) {
      setNote({ text: DAY_HINT, tone: "error" });
      return;
    }
    setBusy(true);
    setNote(null);
    try {
      const { cadence, pay_days } = cadenceOf(schedule);
      await api("/api/v1/incomes", { method: "POST", body: JSON.stringify({ name: name.trim() || "Paycheck", cadence, expected_net: value, next_date: nextDate, pay_days: pay_days ?? "", ...(day ? { pay_day: day } : {}) }) });
      setNote({ text: `Added. Every screen now plans around the ${shortDate(nextDate)} paycheck.`, tone: "info" });
      onAdded();
    } catch (err) {
      setNote({ text: (err as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={add} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <h3 style={{ margin: 0, fontSize: 13, fontWeight: 800 }}>Add your paycheck</h3>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "flex-end" }}>
        <label style={FIELD}><span className="lab">Name</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} style={{ width: 160 }} /></label>
        <label style={FIELD}><span className="lab">How often you're paid</span>
          <select className="input" value={schedule} onChange={(e) => setSchedule(e.target.value as Schedule)}>{SCHEDULES.map(([v, w]) => <option key={v} value={v}>{w}</option>)}</select>
        </label>
        <label style={FIELD}><span className="lab">Usual take-home pay</span><input className="input num" style={{ width: 120 }} value={net} onChange={(e) => setNet(e.target.value)} inputMode="decimal" placeholder="1200" /></label>
        {monthly && (
          <label style={FIELD}><span className="lab">Day of the month it pays</span>
            <input className="input" style={{ width: 90 }} value={payDay} onChange={(e) => setPayDay(e.target.value)} inputMode="numeric" placeholder="from the date" />
          </label>
        )}
        <label style={FIELD}><span className="lab">Next payday</span><input className="input" type="date" value={nextDate} onChange={(e) => setNextDate(e.target.value)} required /></label>
        <button className="btn dark" type="submit" disabled={busy}>{busy ? "Adding…" : "Add the paycheck"}</button>
      </div>
      <span style={{ fontSize: 12, color: "var(--ink-2)" }}>
        The take-home pay is what reaches your bank account.{semi ? " If the two checks of the month differ, set the second one once it is added." : ""}{monthly ? " Leave the day empty to use the date of the next payday; 31 is the last day of every month." : ""}
      </span>
      {note && <Said text={note.text} tone={note.tone} />}
    </form>
  );
}

// A confirmed paystub, offered as the paycheck: its pay date becomes the next payday's rhythm and its take-home pay
// the usual amount. Only the schedule is asked, because a single stub cannot show it.
function FromStub({ stub, onAdded }: { stub: StubOffer; onAdded: () => void }) {
  const [cadence, setCadence] = useState("biweekly");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  async function use() {
    setBusy(true);
    setNote(null);
    try {
      await api(`/api/v1/incomes/from-paystub/${stub.id}`, { method: "POST", body: JSON.stringify({ cadence }) });
      setNote({ text: "Added from the paystub. Every screen uses it now.", tone: "info" });
      onAdded();
    } catch (e) {
      setNote({ text: (e as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, borderBottom: "1px solid var(--surface-2)", paddingBottom: 10 }}>
      <h3 style={{ margin: 0, fontSize: 13, fontWeight: 800 }}>Or use your {shortDate(stub.pay_date)} paystub</h3>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "flex-end" }}>
        <span style={{ fontSize: 13 }}>Take-home pay <strong className="num">{money(stub.net, { cents: true })}</strong>, paid {shortDate(stub.pay_date)}.</span>
        <label style={FIELD}><span className="lab">How often you're paid</span>
          <select className="input" value={cadence} onChange={(e) => setCadence(e.target.value)}>{CADENCES.map(([v, w]) => <option key={v} value={v}>{w}</option>)}</select>
        </label>
        <button className="btn" type="button" disabled={busy} onClick={use}>{busy ? "Adding…" : "Use this as your paycheck"}</button>
        {note && <Said text={note.text} tone={note.tone} />}
      </div>
    </div>
  );
}

export function PaycheckSetup({ stub = null }: { stub?: StubOffer | null }) {
  const state = useApi<{ incomes: IncomeRow[] }>("/api/v1/incomes");
  const { data, reload } = state;
  const [note, setNote] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  const [busy, setBusy] = useState(false);

  async function upload(f: File) {
    setBusy(true);
    setNote(null);
    try {
      const body = new FormData();
      body.append("file", f);
      const r = await api<{ net: number | null; pay_date: string | null; note: string }>("/api/v1/imports/paystub", { method: "POST", body });
      setNote({ text: `${f.name}: ${r.net !== null ? `take-home ${money(r.net, { cents: true })}` : "read"}${r.pay_date ? `, paid ${when(r.pay_date)}` : ""}. ${r.note}`, tone: "info" });
    } catch (e) {
      setNote({ text: (e as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  const none = data && data.incomes.length === 0;
  return (
    <section id="paycheck" className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10, scrollMarginTop: 16 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 800 }}>Your paycheck</h2>
        <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Safe to spend, each paycheck's plan and the Outlook are worked out from this.</span>
      </div>
      {state.error && !data && <Failed error={state.error} what="your paycheck" onRetry={reload} card={false} />}
      {!data && !state.error && <Loading what="your paycheck" card={false} />}
      {none && <div style={{ fontSize: 13, color: "var(--ink-2)" }}>No paycheck on file yet, so the plan counts no income. Add it below, or name it on <Link to="/confirm">Needs you</Link> when the app finds it in your transactions.</div>}
      {none && stub && <FromStub stub={stub} onAdded={reload} />}
      {none && <AddPaycheck onAdded={reload} />}
      {data?.incomes.map((i) => <IncomeEditor key={i.id} i={i} onSaved={reload} />)}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", borderTop: "1px solid var(--surface-2)", paddingTop: 8 }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="lab">Paystub PDF to read</span>
          <input className="input" type="file" accept=".pdf,application/pdf" disabled={busy} onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        </label>
        <span style={{ fontSize: 12, color: "var(--ink-2)", flex: "1 1 260px" }}>A paystub PDF: it is read here and waits on Needs you for one tap. The same for one forwarded to a connected mailbox or dropped into the import folder's paystubs subfolder.</span>
      </div>
      {busy && <span role="status" style={{ fontSize: 13, color: "var(--ink-2)" }}>Reading the paystub…</span>}
      {note && <div style={{ fontSize: 13, display: "flex", gap: 6, flexWrap: "wrap", alignItems: "baseline" }}><Said text={note.text} tone={note.tone} />{note.tone === "info" && <Link to="/confirm">Open Needs you</Link>}</div>}
    </section>
  );
}
