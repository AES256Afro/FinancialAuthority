// Settings › Rules (M62): what answers the mail without a tap. The picture is how much each rule answered. Every rule and
// every sender has its switch, every answer says which rule gave it and why, and every one can be undone. Only the owner
// changes a switch; the server has the last word. Every count here is the engine's.
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi, type Loaded } from "../api";
import { ConfirmButton } from "../components/ConfirmButton";
import { OwnerLocked } from "../components/OwnerLocked";
import { Figures, Said, Show, useFocusOnSaid } from "../components/State";
import { money, when } from "../format";

interface RuleRow { name: string; title: string; does: string; on: boolean; kept: number }
interface SenderKind { kind: string; words: string; answers: number; usual_low: number | null; usual_high: number | null }
interface SenderRow { sender: string; name: string; on: boolean; off_why: string; filed: number; kinds: SenderKind[] }
interface ActionRow { id: number; kind: string; words: string; rule: string; rule_title: string; why: string; status: string; sender: string; sender_name: string; subject: string; amount: number | null; at: string | null }
export interface RulesOut { as_of: string; rules: RuleRow[]; senders: SenderRow[]; actions: ActionRow[]; kept: number; waiting: number; said: string; earn_after: number; not_advice: string; can_change: boolean }

function Picture({ data }: { data: RulesOut }) {
  const bars = [...data.rules.map((r) => ({ key: r.name, words: r.title, count: r.kept, fill: r.on ? "var(--paycheck)" : "var(--surface-2)" })), { key: "waiting", words: "Waiting for you", count: data.waiting, fill: "var(--bill)" }];
  const top = Math.max(1, ...bars.map((b) => b.count));
  return (
    <section className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
      <h2 className="sr-only">How much each rule answered</h2>
      <div role="img" aria-label={bars.map((b) => `${b.words}: ${b.count}`).join("; ")} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {bars.map((b) => (
          <div key={b.key} style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
            <span style={{ fontSize: 13, fontWeight: 700, overflowWrap: "anywhere" }}>{b.words} · {b.count}</span>
            <div style={{ height: 14, background: "var(--surface-2)", borderRadius: 7, overflow: "hidden" }}>
              {b.count > 0 && <div style={{ width: `${(b.count / top) * 100}%`, minWidth: 8, height: "100%", background: b.fill, borderRadius: 7 }} />}
            </div>
          </div>
        ))}
      </div>
      <div style={{ fontSize: 14, fontWeight: 700 }}>{data.said}</div>
    </section>
  );
}

function Switch({ on, label, busy, onChange }: { on: boolean; label: string; busy: boolean; onChange: (on: boolean) => void }) {
  return (
    <label style={{ display: "inline-flex", gap: 8, alignItems: "center", minHeight: 32, fontSize: 13, fontWeight: 700, flexShrink: 0 }}>
      <input type="checkbox" checked={on} disabled={busy} aria-label={label} onChange={(e) => onChange(e.target.checked)} style={{ width: 18, height: 18 }} />
      {on ? "On" : "Off"}
    </label>
  );
}

function usual(k: SenderKind): string {
  if (k.usual_low === null || k.usual_high === null) return `${k.words}: it has never stated an amount, so one that does waits for you.`;
  const span = k.usual_low === k.usual_high ? money(k.usual_low, { cents: true }) : `${money(k.usual_low, { cents: true })} to ${money(k.usual_high, { cents: true })}`;
  return `${k.words}: you answered ${k.answers} times, usually ${span}. An amount well outside that waits for you.`;
}

function Rules_({ state }: { state: Loaded<RulesOut> }) {
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  const ref = useFocusOnSaid(said);
  async function change(path: string, method: string, body: unknown, text: string) {
    setBusy(true);
    setSaid(null);
    try {
      await api(path, { method, body: JSON.stringify(body) });
      setSaid({ text, tone: "info" });
      state.reload();
    } catch (e) {
      setSaid({ text: (e as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  }
  async function undo(a: ActionRow) {
    try {
      const r = await api<{ said: string }>(`/api/v1/facts/${a.id}/unconfirm`, { method: "POST" });
      setSaid({ text: r.said, tone: "info" });
      state.reload();
    } catch (e) {
      setSaid({ text: (e as Error).message, tone: "error" });
    }
  }
  return (
    <Show state={state} what="the rules">
      {(d) => (
        <>
          <Picture data={d} />
          <div ref={ref} tabIndex={-1}><Said text={said?.text ?? null} tone={said?.tone} /></div>
          <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <h2 className="lab" style={{ margin: 0 }}>The rules</h2>
            <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)" }}>A rule acts only on mail the mail server confirmed, through your own mailbox, and only where it changes nothing the plan counts or your ledger or bank link already says the same. Everything else waits on <Link to="/confirm">Needs you</Link>.</p>
            <OwnerLocked what="the rules">
              {d.rules.map((r) => (
                <article key={r.name} className="card" style={{ padding: "12px 14px", display: "flex", gap: 12, alignItems: "flex-start" }}>
                  <div style={{ flex: "1 1 auto", minWidth: 0 }}>
                    <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>{r.title}</h3>
                    <div style={{ fontSize: 13, color: "var(--ink-2)", marginTop: 2 }}>{r.does}</div>
                    <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 2 }}>Has answered {r.kept}.</div>
                  </div>
                  <Switch on={r.on} label={r.title} busy={busy} onChange={(on) => change(`/api/v1/needs-you/rules/${r.name}`, "PUT", { on }, `${r.title}: ${on ? "on" : "off. It stops now; what it already did stays until you undo it"}.`)} />
                </article>
              ))}
            </OwnerLocked>
          </section>
          <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <h2 className="lab" style={{ margin: 0 }}>Senders that earned it · {d.senders.length}</h2>
            {d.senders.length === 0 && <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)" }}>None yet. After you answer one sender the same way {d.earn_after} times, it shows here.</p>}
            {d.senders.length > 0 && (
              <OwnerLocked what="the senders">
                {d.senders.map((s) => (
                  <article key={s.sender} className="card" style={{ padding: "12px 14px", display: "flex", gap: 12, alignItems: "flex-start" }}>
                    <div style={{ flex: "1 1 auto", minWidth: 0 }}>
                      <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, overflowWrap: "anywhere" }}>{s.name || s.sender}</h3>
                      <div style={{ fontSize: 12, color: "var(--muted)", overflowWrap: "anywhere" }}>{s.sender} · filed {s.filed}{s.on || !s.off_why ? "" : ` · off: ${s.off_why}`}</div>
                      {s.kinds.map((k) => <div key={k.kind} style={{ fontSize: 13, color: "var(--ink-2)", marginTop: 2 }}><Figures text={usual(k)} /></div>)}
                    </div>
                    <Switch on={s.on} label={s.name || s.sender} busy={busy} onChange={(on) => change("/api/v1/needs-you/rules/senders", "POST", { sender: s.sender, on }, `${s.name || s.sender}: ${on ? "on" : "off"}.`)} />
                  </article>
                ))}
              </OwnerLocked>
            )}
          </section>
          <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <h2 className="lab" style={{ margin: 0 }}>What the rules answered</h2>
            {d.actions.length === 0 && <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)" }}>Nothing yet. It runs once an hour, and on <Link to="/settings/jobs">Jobs</Link> when you press Run now.</p>}
            {d.actions.length > 0 && (
              <div className="card" style={{ padding: "4px 16px", fontSize: 13, display: "flex", flexDirection: "column" }}>
                {d.actions.map((a, i) => (
                  <div key={a.id} style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", padding: "8px 0", borderTop: i === 0 ? 0 : "1px solid var(--surface-2)" }}>
                    <div style={{ flex: "1 1 260px", minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
                      <span style={{ fontWeight: 700, overflowWrap: "anywhere" }}>{a.words}{a.sender_name ? ` · ${a.sender_name}` : ""}{a.amount !== null ? <> · <Figures text={money(a.amount, { cents: true })} /></> : null}</span>
                      <span style={{ fontSize: 12, color: "var(--ink-2)", overflowWrap: "anywhere" }}><Figures text={a.why} /></span>
                      <span style={{ fontSize: 12, color: "var(--muted)" }}>{a.rule_title}{a.at ? `, ${when(a.at)}` : ""}</span>
                    </div>
                    <ConfirmButton label="Undo" name={`Undo ${a.words}${a.sender_name ? ` ${a.sender_name}` : ""}`} style={{ minHeight: 32, padding: "4px 10px" }}
                      question={a.rule === "earned_sender" ? "Undo? It waits for your answer again, no rule answers it, and this sender goes off." : "Undo? It waits for your answer again, and no rule answers it."}
                      yes="Yes, undo it" no="Keep it" onConfirm={() => undo(a)} />
                  </div>
                ))}
              </div>
            )}
          </section>
          <p style={{ margin: 0, fontSize: 12, color: "var(--muted)" }}>{d.not_advice}</p>
        </>
      )}
    </Show>
  );
}

export function Rules() {
  const state = useApi<RulesOut>("/api/v1/needs-you/rules", { live: true });
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 760 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Rules</h1>
      <Rules_ state={state} />
    </div>
  );
}
