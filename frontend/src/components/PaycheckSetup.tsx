// The paycheck on file and a paystub handed over by hand: how often you are paid, the usual take-home pay per
// position of the month, and a PDF upload that goes to Needs you. The owner's figures stand until the stubs
// refine them.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { money, parseMoney, when } from "../format";
import { Failed, Loading, Said } from "./State";

interface IncomeRow {
  id: number;
  name: string;
  cadence: string;
  expected_net: number;
  expected_net_end: number | null;
  next_date: string;
}

type Note = { text: string; tone: "info" | "error" } | null;

const CADENCES: [string, string][] = [["weekly", "every week"], ["biweekly", "every two weeks"], ["semimonthly", "twice a month: the 15th and the last day"], ["monthly", "monthly"]];
const AMOUNT_HINT = "Type an amount like 120 or 1200.50";

function IncomeEditor({ i, onSaved }: { i: IncomeRow; onSaved: () => void }) {
  const endOf = (x: IncomeRow) => (x.expected_net_end === null ? "" : String(x.expected_net_end));
  const [cadence, setCadence] = useState(i.cadence);
  const [net, setNet] = useState(String(i.expected_net));
  const [netEnd, setNetEnd] = useState(endOf(i));
  const [nextDate, setNextDate] = useState(i.next_date);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  const saved = [i.cadence, i.expected_net, i.expected_net_end, i.next_date].join("|");
  useEffect(() => {
    // After a save, the fields show what the server now holds.
    setCadence(i.cadence);
    setNet(String(i.expected_net));
    setNetEnd(endOf(i));
    setNextDate(i.next_date);
  }, [saved]); // eslint-disable-line react-hooks/exhaustive-deps
  const semi = cadence === "semimonthly";
  const dirty = cadence !== i.cadence || parseMoney(net) !== i.expected_net || nextDate !== i.next_date || (semi && netEnd !== endOf(i));

  async function save() {
    const value = parseMoney(net);
    const end = semi && netEnd.trim() ? parseMoney(netEnd) : undefined;
    if (value === null || value < 0 || end === null) {
      setNote({ text: AMOUNT_HINT, tone: "error" });
      return;
    }
    setBusy(true);
    setNote(null);
    try {
      await api(`/api/v1/incomes/${i.id}`, { method: "PUT", body: JSON.stringify({ cadence, expected_net: value, expected_net_end: end, next_date: nextDate }) });
      setNote({ text: "Saved. Every screen uses it now.", tone: "info" });
      onSaved();
    } catch (e) {
      setNote({ text: (e as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "flex-end" }}>
      <div style={{ flex: "1 1 160px" }}><strong>{i.name}</strong></div>
      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">How often you're paid</span>
        <select className="input" value={cadence} onChange={(e) => setCadence(e.target.value)}>{CADENCES.map(([v, w]) => <option key={v} value={v}>{w}</option>)}</select>
      </label>
      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">{semi ? "Usual take-home pay, the 15th check" : "Usual take-home pay"}</span><input className="input num" style={{ width: 110 }} value={net} onChange={(e) => setNet(e.target.value)} inputMode="decimal" /></label>
      {semi && <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Usual take-home pay, the end-of-month check</span><input className="input num" style={{ width: 110 }} value={netEnd} onChange={(e) => setNetEnd(e.target.value)} inputMode="decimal" placeholder="same" /></label>}
      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Next payday</span><input className="input" type="date" value={nextDate} onChange={(e) => setNextDate(e.target.value)} /></label>
      <button className="btn dark" type="button" disabled={!dirty || busy} onClick={save}>{busy ? "Saving…" : "Save"}</button>
      {note && <Said text={note.text} tone={note.tone} />}
    </div>
  );
}

export function PaycheckSetup() {
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

  return (
    <section id="paycheck" className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10, scrollMarginTop: 16 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 800 }}>Your paycheck</h2>
        <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Safe to spend, each paycheck's plan and the Outlook are worked out from this.</span>
      </div>
      {state.error && !data && <Failed error={state.error} what="your paycheck" onRetry={reload} card={false} />}
      {!data && !state.error && <Loading what="your paycheck" card={false} />}
      {data && data.incomes.length === 0 && <div style={{ fontSize: 13, color: "var(--ink-2)" }}>No paycheck on file yet. Name it on <Link to="/confirm">Needs you</Link> when the app finds it in your transactions, or confirm a paystub below.</div>}
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
