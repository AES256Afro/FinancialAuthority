// The paycheck on file and a paystub handed over by hand: how often it lands, the usual net per position of
// the month, and a PDF upload that goes to Needs you. The owner's figures stand until the stubs refine them.
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { money } from "../format";

interface IncomeRow {
  id: number;
  name: string;
  cadence: string;
  expected_net: number;
  expected_net_end: number | null;
  next_date: string;
}

const CADENCES: [string, string][] = [["weekly", "every week"], ["biweekly", "every two weeks"], ["semimonthly", "twice a month: the 15th and the last day"], ["monthly", "monthly"]];

function IncomeEditor({ i, onSaved }: { i: IncomeRow; onSaved: () => void }) {
  const [cadence, setCadence] = useState(i.cadence);
  const [net, setNet] = useState(String(i.expected_net));
  const [netEnd, setNetEnd] = useState(i.expected_net_end === null ? "" : String(i.expected_net_end));
  const [nextDate, setNextDate] = useState(i.next_date);
  const [busy, setBusy] = useState(false);
  const semi = cadence === "semimonthly";
  const dirty = cadence !== i.cadence || Number(net) !== i.expected_net || nextDate !== i.next_date || (semi && netEnd !== (i.expected_net_end === null ? "" : String(i.expected_net_end)));

  async function save() {
    setBusy(true);
    try {
      await api(`/api/v1/incomes/${i.id}`, { method: "PUT", body: JSON.stringify({ cadence, expected_net: Number(net), expected_net_end: semi && netEnd ? Number(netEnd) : undefined, next_date: nextDate }) });
      onSaved();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "flex-end" }}>
      <div style={{ flex: "1 1 160px" }}><strong>{i.name}</strong></div>
      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Lands</span>
        <select className="input" value={cadence} onChange={(e) => setCadence(e.target.value)}>{CADENCES.map(([v, w]) => <option key={v} value={v}>{w}</option>)}</select>
      </label>
      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">{semi ? "Usual net, the 15th check" : "Usual net"}</span><input className="input num" style={{ width: 110 }} value={net} onChange={(e) => setNet(e.target.value)} inputMode="decimal" /></label>
      {semi && <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Usual net, the end-of-month check</span><input className="input num" style={{ width: 110 }} value={netEnd} onChange={(e) => setNetEnd(e.target.value)} inputMode="decimal" placeholder="same" /></label>}
      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Next payday</span><input className="input" type="date" value={nextDate} onChange={(e) => setNextDate(e.target.value)} /></label>
      <button className="btn dark" type="button" disabled={!dirty || busy} onClick={save}>Save</button>
    </div>
  );
}

export function PaycheckSetup() {
  const { data, reload } = useApi<{ incomes: IncomeRow[] }>("/api/v1/incomes");
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function upload(f: File) {
    setBusy(true);
    setNote(null);
    try {
      const body = new FormData();
      body.append("file", f);
      const r = await api<{ net: number | null; pay_date: string | null; note: string }>("/api/v1/imports/paystub", { method: "POST", body });
      setNote(`${f.name}: ${r.net !== null ? `net ${money(r.net, { cents: true })}` : "read"}${r.pay_date ? ` paid ${r.pay_date}` : ""}. ${r.note}`);
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section id="paycheck" className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10, scrollMarginTop: 16 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <strong style={{ fontSize: 14 }}>Your paycheck</strong>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>what the windows, the safe-to-spend and the outlook are built on</span>
      </div>
      {data && data.incomes.length === 0 && <div style={{ fontSize: 13, color: "var(--ink-2)" }}>No paycheck on file yet. Name it on <Link to="/confirm">Needs you</Link> when the ledger finds it, or confirm a paystub below.</div>}
      {data?.incomes.map((i) => <IncomeEditor key={`${i.id}-${i.cadence}-${i.expected_net}-${i.expected_net_end}-${i.next_date}`} i={i} onSaved={reload} />)}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", borderTop: "1px solid var(--surface-2)", paddingTop: 8 }}>
        <input className="input" type="file" accept=".pdf,application/pdf" disabled={busy} onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} aria-label="Paystub PDF to read" />
        <span style={{ fontSize: 12, color: "var(--muted)" }}>A paystub PDF: it is read here and waits on Needs you for one tap. The same for one forwarded to a connected mailbox or dropped into the import folder's paystubs subfolder.</span>
      </div>
      {note && <div style={{ fontSize: 13 }}>{note} <Link to="/confirm">Open Needs you</Link></div>}
    </section>
  );
}
