// Bills the bank shows no payment for: is it paid? One card each, with the payments that might be it and the four
// answers. The same cards sit on Needs you and on Bills, where Today's "Pay X; it was due" sends you.
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type Loaded } from "../api";
import { localToday, money, shortDate } from "../format";
import { ConfirmButton } from "./ConfirmButton";
import { PurchaseChoices } from "./Documents";
import { Said, useFocusOnSaid } from "./State";
import { SeverityTag } from "./Status";

export interface PaidCandidate { id: number; date: string; merchant: string; amount: number; account: string; source: string; named?: boolean }
export interface PastDueRow { payment_id: number; bill_id: number; name: string; amount: number; due_date: string; days_late: number; autopay: boolean; candidates: PaidCandidate[]; source: string }
type PaidAnswer = { answer: "paid" | "not_yet" | "stopped"; transaction_id?: number; paid_on?: string };
interface Done { text: string; tone?: "info" | "error"; to?: string; link?: string }

export const PAST_DUE_WORDS = "Bills with no payment seen";
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** One bill the bank shows no payment for: when it was due, how much, how late, the payments that might be it (the
 * app's, the closest amount and date first), and the four answers. */
function PaidCard({ p, onDone }: { p: PastDueRow; onDone: (text: string, answer: PaidAnswer["answer"]) => void }) {
  // The first candidate is preselected only when it carries the bill's own name; an unnamed close amount is offered, not assumed.
  const [choice, setChoice] = useState<number | null>(p.candidates[0]?.named ? p.candidates[0].id : null);
  const [paidOn, setPaidOn] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const late = p.days_late <= 0 ? "Due today" : `${plural(p.days_late, "day", "days")} late`;

  async function send(body: PaidAnswer) {
    const r = await api<{ said: string }>(`/api/v1/needs-you/payments/${p.payment_id}`, { method: "POST", body: JSON.stringify(body) });
    onDone(r.said, body.answer);
  }
  async function answer(body: PaidAnswer) {
    setBusy(true);
    setError(null);
    try {
      await send(body);
    } catch (e) {
      setError(`Couldn't save your answer: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <div style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 4 }}>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, overflowWrap: "anywhere" }}>{p.name}</h3>
          <span style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 12, color: "var(--ink-2)" }}>
            <SeverityTag severity="critical" word={late} />
            <span>Was due {shortDate(p.due_date)}{p.autopay ? " · set to pay automatically (autopay)" : ""}</span>
          </span>
        </div>
        <strong className="num" style={{ fontSize: 18 }}>{money(p.amount, { cents: true })}</strong>
      </div>
      {p.candidates.length > 0 ? (
        <PurchaseChoices name={`payment-${p.payment_id}`} legend="The payment that might be it (the closest amount and date first)" list={p.candidates.map((c) => ({ ...c, named: false, has_receipt: false }))} value={choice} onPick={setChoice} />
      ) : (
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>No payment with a close amount was found in your transactions near the due date.</div>
      )}
      <label style={{ display: "flex", flexDirection: "column", gap: 4, alignItems: "flex-start" }}>
        <span className="lab">If you paid another way: the day you paid (empty means today)</span>
        <input className="input" type="date" value={paidOn} max={localToday()} onChange={(e) => setPaidOn(e.target.value)} />
      </label>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-start" }}>
        {p.candidates.length > 0 && <button className="btn dark" type="button" disabled={busy || choice === null} aria-label={`${p.name}: paid with this one`} onClick={() => choice !== null && answer({ answer: "paid", transaction_id: choice })}>Paid with this one</button>}
        <button className="btn" type="button" disabled={busy} aria-label={`${p.name}: paid another way`} onClick={() => answer({ answer: "paid", paid_on: paidOn || undefined })}>Paid another way</button>
        <button className="btn" type="button" disabled={busy} aria-label={`${p.name}: not paid yet`} onClick={() => answer({ answer: "not_yet" })}>Not paid yet</button>
        <ConfirmButton label="This bill has stopped" name={`${p.name} has stopped`} question={`${p.name} has stopped? It is no longer counted from ${shortDate(p.due_date)} on.`} yes="Yes, it has stopped" no="Keep counting it" onConfirm={() => send({ answer: "stopped" })} />
      </div>
      <Said text={error} tone="error" />
    </div>
  );
}

export function PaidQuestions({ state, onAnswered, here = "confirm" }: { state: Loaded<{ past_due: PastDueRow[] }>; onAnswered: () => void; here?: "confirm" | "bills" }) {
  const [said, setSaid] = useState<Done | null>(null);
  const saidRef = useFocusOnSaid(said);
  const list = state.data?.past_due ?? [];
  if (list.length === 0 && !said) return null;
  return (
    <>
      <h2 className="lab" style={{ margin: "6px 0 0" }}>{PAST_DUE_WORDS}: is it paid? · {list.length}</h2>
      {said && (
        <div ref={saidRef} tabIndex={-1} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "baseline" }}>
          <Said text={said.text} tone={said.tone} />
          {said.to && said.link && <Link to={said.to} style={{ fontSize: 12, fontWeight: 700 }}>{said.link}</Link>}
        </div>
      )}
      {list.map((p) => (
        <PaidCard
          key={p.payment_id}
          p={p}
          onDone={(text, answer) => {
            // "Not paid yet" keeps the card; its sentence does not name the bill, so the name goes first.
            setSaid(here === "bills" ? { text: answer === "not_yet" ? `${p.name}: ${text}` : text, to: "/today", link: "See Today" } : { text: answer === "not_yet" ? `${p.name}: ${text}` : text, to: "/bills", link: "See it on Bills" });
            state.reload();
            onAnswered();
          }}
        />
      ))}
    </>
  );
}
