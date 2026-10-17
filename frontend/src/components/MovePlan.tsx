// The moving day plan (M43): one dated checklist from today to the first rent at the next home, built by the
// engine from Housing. The picture first (a strip with one square per step, filled when done), then each step on
// its date with what to do and why, a tick for the ones only you can tick, and the notice letter.
import { CalendarClock, CircleCheck, Copy, Mail, OctagonAlert, Square, SquareCheck, TriangleAlert } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { shortDate } from "../format";
import { Said, Show } from "./State";
import { SeverityTag } from "./Status";

interface Step {
  id: string;
  date: string | null;
  title: string;
  words: string;
  kind: string;
  state: "done" | "late" | "soon" | "later" | "open";
  done: boolean;
  done_by: "the app" | "you" | null;
  done_on: string | null;
  link: string;
  source: string;
  action: string | null;
  days_left: number | null;
}

interface Plan {
  as_of: string;
  move: { from: string | null; to: string; on: string; days_left: number } | null;
  steps: Step[];
  counts: Record<string, number>;
  total?: number;
  next: Step | null;
  letter: string | null;
  landlord?: string | null;
  note?: string;
  not_advice?: string;
}

const STATE: Record<Step["state"], { severity: string; word: string; fill: string }> = {
  done: { severity: "good", word: "Done", fill: "var(--safe)" },
  late: { severity: "critical", word: "Late", fill: "var(--critical)" },
  soon: { severity: "warning", word: "This week", fill: "var(--warning)" },
  later: { severity: "info", word: "Later", fill: "var(--surface-2)" },
  open: { severity: "info", word: "No date yet", fill: "var(--surface-2)" },
};

const SMALL = { minHeight: 32, padding: "4px 10px", fontSize: 13 } as const;

export function MovePlan({ onChanged }: { onChanged?: () => void }) {
  const state = useApi<Plan>("/api/v1/move/plan");
  const [plan, setPlan] = useState<Plan | null>(null);
  return (
    <Show state={state} what="the moving day plan">
      {(loaded) => <PlanBody plan={plan ?? loaded} setPlan={(p) => { setPlan(p); onChanged?.(); }} />}
    </Show>
  );
}

function PlanBody({ plan, setPlan }: { plan: Plan; setPlan: (p: Plan) => void }) {
  if (!plan.move) {
    return (
      <section className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 8 }}>
        <h2 style={{ margin: 0, fontSize: 17, fontWeight: 800 }}>Moving day plan</h2>
        <div style={{ fontSize: 14, color: "var(--ink-2)" }}>{plan.note} <Link to="/housing">Open Housing</Link></div>
      </section>
    );
  }
  const total = plan.total ?? plan.steps.length;
  const done = plan.counts.done ?? 0;
  return (
    <section className="card" aria-labelledby="move-plan-title" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", justifyContent: "space-between", gap: 8 }}>
        <h2 id="move-plan-title" style={{ margin: 0, fontSize: 17, fontWeight: 800 }}>Moving day plan</h2>
        <span style={{ fontSize: 13, color: "var(--ink-2)", display: "inline-flex", alignItems: "center", gap: 6 }}>
          <CalendarClock size={15} aria-hidden="true" />
          {plan.move.days_left > 0 ? `${plan.move.days_left} day${plan.move.days_left === 1 ? "" : "s"} to ${shortDate(plan.move.on)}` : plan.move.days_left === 0 ? "The move is today" : `Moved on ${shortDate(plan.move.on)}`}
        </span>
      </div>

      {/* The picture: one square per step, in date order, filled when done */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <div role="img" aria-label={`${done} of ${total} steps done`} style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          {plan.steps.map((s) => (
            <span key={s.id} title={`${s.title}: ${STATE[s.state].word}`} style={{ width: 18, height: 18, borderRadius: 4, background: STATE[s.state].fill, border: s.state === "later" || s.state === "open" ? "1px solid var(--hairline)" : "none" }} />
          ))}
        </div>
        <div style={{ fontSize: 13, color: "var(--ink-2)", display: "flex", flexWrap: "wrap", gap: "4px 14px" }}>
          <span><strong>{done}</strong> of {total} done</span>
          {(plan.counts.late ?? 0) > 0 && <span style={{ color: "var(--critical-ink)" }}><OctagonAlert size={13} aria-hidden="true" style={{ verticalAlign: -2 }} /> {plan.counts.late} late</span>}
          {(plan.counts.soon ?? 0) > 0 && <span style={{ color: "var(--warning-ink)" }}><TriangleAlert size={13} aria-hidden="true" style={{ verticalAlign: -2 }} /> {plan.counts.soon} this week</span>}
          {plan.next && <span>Next: {plan.next.title}{plan.next.date ? `, ${shortDate(plan.next.date)}` : ""}</span>}
        </div>
      </div>

      <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 10 }}>
        {plan.steps.map((s) => <StepRow key={s.id} step={s} setPlan={setPlan} letter={s.action === "letter" ? <Letter plan={plan} setPlan={setPlan} /> : null} />)}
      </ol>
      {plan.not_advice && <div style={{ fontSize: 12, color: "var(--muted)" }}>{plan.not_advice}</div>}
    </section>
  );
}

function StepRow({ step, setPlan, letter }: { step: Step; setPlan: (p: Plan) => void; letter: ReactNode }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const look = STATE[step.state];
  async function toggle() {
    setBusy(true);
    setError(null);
    try {
      setPlan(await api<Plan>(`/api/v1/move/plan/steps/${encodeURIComponent(step.id)}`, { method: "POST", body: JSON.stringify({ done: !step.done }) }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const byApp = step.done_by === "the app";
  return (
    <li style={{ display: "grid", gridTemplateColumns: "minmax(0, 84px) minmax(0, 1fr)", gap: 10, padding: "10px 0", borderTop: "1px solid var(--hairline)" }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: step.state === "late" ? "var(--critical-ink)" : "var(--ink)" }}>{step.date ? shortDate(step.date) : "No date"}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
          <strong style={{ fontSize: 14, textDecoration: step.done ? "line-through" : "none", color: step.done ? "var(--ink-2)" : "var(--ink)" }}>{step.title}</strong>
          <SeverityTag severity={look.severity} word={look.word} />
        </div>
        {!step.done && <div style={{ fontSize: 13, color: "var(--ink-2)" }}>{step.words}</div>}
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
          {byApp ? (
            <span style={{ fontSize: 12, color: "var(--safe-ink)", display: "inline-flex", alignItems: "center", gap: 4 }}><CircleCheck size={14} aria-hidden="true" />The app saw this done</span>
          ) : (
            <button type="button" className="btn" style={SMALL} aria-pressed={step.done} disabled={busy} onClick={toggle}>
              {step.done ? <SquareCheck size={15} aria-hidden="true" /> : <Square size={15} aria-hidden="true" />}
              {step.done ? " Done (tap to undo)" : " Mark done"}
            </button>
          )}
          {step.link && step.link !== "/move" && <Link to={step.link} style={{ fontSize: 13 }}>Open {step.link === "/housing" ? "Housing" : step.link.replace("/", "")}</Link>}
        </div>
        <Said text={error} tone="error" />
        {!step.done && letter}
      </div>
    </li>
  );
}

function Letter({ plan, setPlan }: { plan: Plan; setPlan: (p: Plan) => void }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(plan.letter ?? "");
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  const unfilled = /\[your/i.test(text);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setSaid({ text: "Copied. Paste it into the office's portal, an email, or a document to print.", tone: "info" });
    } catch {
      setSaid({ text: "Copying is not allowed here: select the letter's text and copy it by hand.", tone: "error" });
    }
  }
  async function queue() {
    setBusy(true);
    setSaid(null);
    try {
      const out = await api<{ said: string; plan: Plan }>("/api/v1/move/plan/letter", { method: "POST", body: JSON.stringify({ to: to.trim(), body: text }) });
      setPlan(out.plan);
      setSaid({ text: out.said, tone: "info" });
    } catch (e) {
      setSaid({ text: (e as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  }
  if (!open) {
    return <div><button type="button" className="btn" style={SMALL} aria-expanded={false} onClick={() => setOpen(true)}><Mail size={15} aria-hidden="true" /> Show the notice letter</button></div>;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: 10, borderRadius: 10, background: "var(--surface-2)" }}>
      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span className="lab">The letter (change anything; fill in the parts in brackets)</span>
        <textarea className="input" value={text} onChange={(e) => setText(e.target.value)} rows={14} style={{ width: "100%", fontFamily: "inherit", fontSize: 13, lineHeight: 1.5, resize: "vertical" }} />
      </label>
      {unfilled && <div style={{ fontSize: 12, color: "var(--warning-ink)", display: "flex", gap: 6, alignItems: "center" }}><TriangleAlert size={14} aria-hidden="true" />Some parts in brackets are still to fill in.</div>}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        <button type="button" className="btn" style={SMALL} onClick={copy}><Copy size={15} aria-hidden="true" /> Copy the letter</button>
      </div>
      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span className="lab">Or email it{plan.landlord ? ` to ${plan.landlord}` : ""}: their address</span>
        <input className="input" type="email" value={to} onChange={(e) => setTo(e.target.value)} placeholder="office@example.com" style={{ width: "min(100%, 320px)" }} />
      </label>
      <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Nothing is sent from here: the email waits on <Link to="/confirm">Needs you</Link> until you approve it.</div>
      <div><button type="button" className="btn dark" style={SMALL} disabled={busy || !to.trim() || unfilled} onClick={queue}>{busy ? "Queuing…" : "Queue the email for my approval"}</button></div>
      <Said text={said?.text ?? null} tone={said?.tone} />
    </div>
  );
}
