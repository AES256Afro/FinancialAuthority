// The chat: the advisor beside the app's figures, and the reviewer's notes with one-tap actions.
// The engine computed every number on this page; the models explain and push back, and any figure a model
// states that the engine did not return is marked.
import { CircleCheck, TriangleAlert } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { streamChat, type ChatTurn } from "../chat";
import { ConfirmButton } from "../components/ConfirmButton";
import { Failed, Loading, Said } from "../components/State";
import { money, shortDate, when } from "../format";
import type { ReviewRun, Snapshot } from "../types";
import { toolWord } from "./Audit";
import { CHAT_KEY } from "../session";

const STARTERS = ["What should this paycheck go on?", "What must be paid before payday?", "Which bills could move to after payday?", "Where does my usual pace dip under the safety cushion?"];

function loadTurns(): ChatTurn[] {
  try {
    const raw = localStorage.getItem(CHAT_KEY);
    const turns = raw ? (JSON.parse(raw) as ChatTurn[]) : [];
    // A reply cut off by a reload is dropped with its question, so the thread never ends in silence.
    while (turns.length && turns[turns.length - 1].role === "assistant" && !turns[turns.length - 1].content && !turns[turns.length - 1].error) turns.pop();
    while (turns.length && turns[turns.length - 1].role === "user") turns.pop();
    return turns;
  } catch {
    return [];
  }
}

function saveTurns(turns: ChatTurn[]) {
  try {
    localStorage.setItem(CHAT_KEY, JSON.stringify(turns.slice(-40)));
  } catch {
    // storage may be unavailable
  }
}

function Row({ label, value, tone }: { label: ReactNode; value: string; tone?: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 13, padding: "5px 0", borderTop: "1px solid var(--surface-2)" }}>
      <span style={{ color: "var(--ink-2)" }}>{label}</span>
      <strong className="num" style={{ color: tone }}>{value}</strong>
    </div>
  );
}

function EngineTable({ s }: { s: Snapshot }) {
  return (
    <section className="card" style={{ padding: "12px 14px" }}>
      <h2 className="lab" style={{ margin: "0 0 6px" }}>The app's figures · as of {shortDate(s.as_of)}</h2>
      <Row label="Cash to spend (checking)" value={money(s.cash)} />
      <Row label={`Due before payday (${s.due_before_payday.items.length})`} value={money(s.due_before_payday.total)} tone="var(--bill-ink)" />
      <Row label={s.next_paycheck.known === false ? "Payday (none on file)" : `Payday ${shortDate(s.next_paycheck.date)}`} value={money(s.next_paycheck.amount)} tone="var(--paycheck-ink)" />
      <Row label={`Advance apps take back (${s.advances.items.length})`} value={money(s.advances.total)} tone="var(--debt-ink)" />
      <Row label={`Bills this paycheck pays (${s.window.items.length})`} value={money(s.window.total)} tone="var(--bill-ink)" />
      <Row label="Kept for the next paycheck's bills" value={money(s.set_aside.total)} />
      <Row label={<><Link to="/glossary#buffer">Safety cushion</Link> (buffer)</>} value={money(s.buffer)} />
      <Row label={`Safe to spend through ${shortDate(s.safe_to_spend.through)}`} value={money(s.safe_to_spend.amount)} tone={s.safe_to_spend.amount < 0 ? "var(--critical-ink)" : "var(--safe-ink)"} />
      {s.usual_pace.per_day > 0 && <Row label="Usual pace, a day" value={money(s.usual_pace.per_day)} />}
      <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 8 }}>{s.not_advice}</div>
    </section>
  );
}

const KIND_TONE: Record<string, string> = { disagree: "var(--critical)", tighten: "var(--warning)", ask: "var(--paycheck)", ok: "var(--safe)" };
const KIND_WORD: Record<string, string> = { disagree: "Would decide differently", tighten: "Would tighten", ask: "Asks you", ok: "Agrees" };

function Reviewer({ onChanged }: { onChanged: () => void }) {
  const reviews = useApi<{ reviews: ReviewRun[]; pending: { reason: string; at: string } | null; configured: boolean }>("/api/v1/reviews?limit=3");
  const { data, reload } = reviews;
  const [busy, setBusy] = useState(false);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const latest = data?.reviews.find((r) => r.ok) ?? data?.reviews[0];

  async function runNow() {
    setBusy(true);
    setError(null);
    try {
      const run = await api<ReviewRun>("/api/v1/reviews", { method: "POST" });
      if (!run.ok) setError(run.error);
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function act(run: ReviewRun, n: number, dismiss: boolean) {
    const key = `${run.id}:${n}`;
    const body = dismiss ? undefined : JSON.stringify({ answer: answers[key] ?? null });
    try {
      await api(`/api/v1/reviews/${run.id}/notes/${n}/${dismiss ? "dismiss" : "apply"}`, { method: "POST", body });
      reload();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <section className="card" style={{ padding: "12px 14px", display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <h2 className="lab" style={{ flexGrow: 1, margin: 0 }}>The reviewer</h2>
        {data?.configured && <button className="btn" type="button" disabled={busy} onClick={runNow} style={{ minHeight: 32, padding: "4px 10px" }}>{busy ? "Reviewing…" : "Review now"}</button>}
      </div>
      {reviews.error && !data && <Failed error={reviews.error} what="the reviewer's notes" onRetry={reload} card={false} />}
      {!data && !reviews.error && <Loading what="the reviewer's notes" card={false} />}
      {data && !data.configured && (
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
          No reviewer yet. A second model reads the plan and the rules, never the mail, and says where it would push back. Choose one on <Link to="/settings/models">Settings › Models</Link>.
        </div>
      )}
      {data?.pending && data.configured && <div style={{ fontSize: 12, color: "var(--ink-2)" }}>A review is due: {data.pending.reason}. It runs within the hour, or now.</div>}
      {error && <Said text={error} tone="error" />}
      {latest && latest.ok && (
        <>
          <div style={{ fontSize: 12, color: "var(--muted)" }}>
            {latest.model} · {latest.trigger === "payday" ? "on payday" : latest.trigger === "change" ? "after a change" : "on request"} · {when(latest.created_at)} · {latest.input_tokens + latest.output_tokens} tokens{latest.cost !== null ? ` · $${latest.cost.toFixed(4)}` : ""}
          </div>
          {latest.notes.map((n) => {
            const key = `${latest.id}:${n.n}`;
            const closed = n.status !== "open";
            return (
              <div key={n.n} style={{ borderTop: "1px solid var(--surface-2)", paddingTop: 8, opacity: closed ? 0.55 : 1 }}>
                <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4 }}>
                  <span className="sw" style={{ background: KIND_TONE[n.kind] ?? "var(--muted)", margin: 0 }} />
                  <span style={{ fontSize: 11, fontWeight: 800, color: "var(--ink-2)", textTransform: "uppercase", letterSpacing: ".04em" }}>{KIND_WORD[n.kind] ?? n.kind}</span>
                  {closed && <span style={{ fontSize: 11, color: "var(--muted)" }}>· {n.status}</span>}
                </div>
                <div style={{ fontSize: 13, lineHeight: 1.45 }}>{n.text}</div>
                {n.unverified.length > 0 && <div style={{ display: "flex", gap: 4, alignItems: "flex-start", fontSize: 11, color: "var(--warning-ink)", fontWeight: 700, marginTop: 2 }}><TriangleAlert size={12} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} />Not from the app's figures: {n.unverified.join(", ")}</div>}
                {!closed && (
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 6, alignItems: "center" }}>
                    {n.action.type === "rule" && <button className="btn dark" type="button" style={{ minHeight: 32, padding: "4px 10px" }} onClick={() => act(latest, n.n, false)} title={n.action.text}>Add the rule</button>}
                    {n.action.type === "buffer" && <button className="btn dark" type="button" style={{ minHeight: 32, padding: "4px 10px" }} onClick={() => act(latest, n.n, false)}>Set the safety cushion to {money(n.action.value ?? 0)}</button>}
                    {n.action.type === "ask" && (
                      <>
                        <input className="input" style={{ minHeight: 32, padding: "4px 8px", flex: "1 1 140px" }} placeholder="Your answer" value={answers[key] ?? ""} onChange={(e) => setAnswers({ ...answers, [key]: e.target.value })} aria-label="Answer" />
                        <button className="btn dark" type="button" style={{ minHeight: 32, padding: "4px 10px" }} disabled={!(answers[key] ?? "").trim()} onClick={() => act(latest, n.n, false)}>Answer</button>
                      </>
                    )}
                    {n.action.type === "none" && <button className="btn" type="button" style={{ minHeight: 32, padding: "4px 10px" }} onClick={() => act(latest, n.n, false)}>Noted</button>}
                    <button className="btn" type="button" style={{ minHeight: 32, padding: "4px 10px" }} onClick={() => act(latest, n.n, true)}>Dismiss</button>
                  </div>
                )}
                {n.action.type === "rule" && !closed && <div style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 4 }}>Rule: {n.action.text}</div>}
              </div>
            );
          })}
        </>
      )}
      {latest && !latest.ok && <Said text={`The last review failed: ${latest.error}`} tone="error" />}
      {data?.configured && !latest && <div style={{ fontSize: 13, color: "var(--ink-2)" }}>No review yet. It runs on payday and after a confirmed change, or now.</div>}
    </section>
  );
}

export function Chat({ snapshot, onChanged }: { snapshot: Snapshot; onChanged: () => void }) {
  const [turns, setTurns] = useState<ChatTurn[]>(loadTurns);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [tools, setTools] = useState<string[]>([]);
  const endRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    saveTurns(turns);
  }, [turns]);
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [turns, busy]);

  async function send(text: string) {
    const question = text.trim();
    if (!question || busy) return;
    setDraft("");
    const history = [...turns, { role: "user" as const, content: question }];
    setTurns([...history, { role: "assistant", content: "" }]);
    setBusy(true);
    setTools([]);
    const controller = new AbortController();
    abortRef.current = controller;
    let reply = "";
    const used: { name: string; rows: number }[] = [];
    const meta: Partial<ChatTurn> = {};
    const patch = (extra: Partial<ChatTurn>) => {
      Object.assign(meta, extra);
      setTurns([...history, { role: "assistant", content: reply, used: [...used], ...meta }]);
    };
    try {
      await streamChat(
        history.map((t) => ({ role: t.role, content: t.content })),
        (e) => {
          if (e.type === "text") {
            reply += e.text;
            patch({});
          } else if (e.type === "tool") {
            used.push({ name: e.name, rows: e.rows });
            setTools(used.map((u) => u.name));
            patch({});
          } else if (e.type === "done") {
            if (used.length === 0) used.push(...e.used.map((u) => ({ name: u.name, rows: u.rows })));
            patch({ unverified: e.unverified, model: e.model });
          } else if (e.type === "error") {
            patch({ error: e.message });
          }
        },
        controller.signal,
      );
    } catch (err) {
      patch({ error: (err as Error).message });
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }

  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(260px, 340px)", gap: 14, alignItems: "start" }} className="chat-grid">
      <style>{`@media (max-width: 860px) { .chat-grid { grid-template-columns: minmax(0, 1fr) !important; } .chat-main { min-height: 0 !important; } }`}</style>
      {/* On a phone the box to type in is on the first screen: the conversation grows as it fills, not before. */}
      <section className="chat-main" style={{ display: "flex", flexDirection: "column", gap: 10, minHeight: "60vh" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
          <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Chat</h1>
          <span style={{ fontSize: 13, color: "var(--ink-2)" }}>The advisor explains the app's plan. It cannot move money, send anything, or change a number.</span>
          <div style={{ flexGrow: 1 }} />
          {turns.length > 0 && <ConfirmButton label="Clear" question="Clear this conversation? It cannot be brought back." yes="Yes, clear it" no="Keep it" onConfirm={() => setTurns([])} style={{ minHeight: 32, padding: "4px 10px" }} />}
        </div>
        <div className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 12, flexGrow: 1 }}>
          {turns.length === 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Ask anything about this paycheck. A few to start:</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {STARTERS.map((s) => <button key={s} className="btn" type="button" onClick={() => send(s)} style={{ fontWeight: 600 }}>{s}</button>)}
              </div>
            </div>
          )}
          {turns.map((t, i) => (
            <div key={i} style={{ display: "flex", flexDirection: "column", alignItems: t.role === "user" ? "flex-end" : "flex-start", gap: 4 }}>
              <div style={{ maxWidth: "88%", padding: "10px 12px", borderRadius: 14, background: t.role === "user" ? "var(--paycheck-soft)" : "var(--surface-2)", color: t.role === "user" ? "var(--paycheck-ink)" : "var(--ink)", fontSize: 14, lineHeight: 1.5, whiteSpace: "pre-wrap" }}>
                {t.content || (t.role === "assistant" && busy && i === turns.length - 1 ? <span style={{ color: "var(--muted)" }}>{tools.length ? `Reading: ${toolWord(tools[tools.length - 1])}…` : "Thinking…"}</span> : t.content)}
              </div>
              {t.role === "assistant" && (t.used?.length || t.unverified?.length || t.error || t.model) ? (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, fontSize: 11, color: "var(--muted)", alignItems: "center" }}>
                  {t.used?.map((u, k) => <span key={k} style={{ padding: "2px 8px", borderRadius: 999, border: "1px solid var(--hairline)" }}>{toolWord(u.name)} · {u.rows} rows</span>)}
                  {t.unverified && t.unverified.length > 0 && <span style={{ display: "inline-flex", gap: 4, alignItems: "center", padding: "2px 8px", borderRadius: 999, background: "var(--warning-soft)", color: "var(--warning-ink)", fontWeight: 800 }}><TriangleAlert size={12} aria-hidden="true" />Not from the app's figures: {t.unverified.join(", ")}</span>}
                  {t.unverified && t.unverified.length === 0 && t.content && <span style={{ display: "inline-flex", gap: 4, alignItems: "center", color: "var(--safe-ink)", fontWeight: 700 }}><CircleCheck size={12} aria-hidden="true" />Every figure checked</span>}
                  {t.error && <Said text={t.error} tone="error" />}
                  {t.model && <span>{t.model}</span>}
                </div>
              ) : null}
            </div>
          ))}
          <div ref={endRef} />
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            send(draft);
          }}
          style={{ display: "flex", gap: 8 }}
        >
          <input className="input" style={{ flexGrow: 1 }} value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Ask the advisor…" aria-label="Message" disabled={busy} />
          {busy ? (
            <button className="btn" type="button" onClick={() => abortRef.current?.abort()}>Stop</button>
          ) : (
            <button className="btn dark" type="submit" disabled={!draft.trim()}>Send</button>
          )}
        </form>
      </section>
      <aside style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <Reviewer onChanged={onChanged} />
        <EngineTable s={snapshot} />
      </aside>
    </div>
  );
}
