// The data check (M52): what may be counted twice, or may not be genuine, laid out picture first. One bar per kind
// of finding, then each finding with its records side by side and the answers that settle it. Nothing changes until
// the owner answers; "they are different" is remembered. Below, what the app set right on its own, with why.
// Every finding, record and sentence is the server's.
import { CircleCheck, OctagonAlert, RefreshCw, TriangleAlert, Wrench } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { ConfirmButton } from "../components/ConfirmButton";
import { Said, Show } from "../components/State";
import { SeverityTag } from "../components/Status";
import { amountClass, shortDate, when } from "../format";

interface Item { label: string; detail: string; source: string; link: string }
interface Action { id: string; label: string; question?: string }
interface Finding { key: string; kind: string; severity: "crit" | "warn"; title: string; why: string; items: Item[]; actions: Action[]; fix_on?: string }
interface CheckOut {
  as_of: string;
  findings: Finding[];
  kinds: { kind: string; words: string; count: number }[];
  said: string;
  fixed: { on: string; what: string; why: string; sources: string[] }[];
  last: { at: string; count: number } | null;
  decided: number;
}

function FindingCard({ f, onAnswered }: { f: Finding; onAnswered: (text: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function send(action: string) {
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ said: string }>("/api/v1/data-check/answer", { method: "POST", body: JSON.stringify({ key: f.key, action }) });
      onAnswered(r.said);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10, minWidth: 0, borderColor: f.severity === "crit" ? "var(--critical)" : undefined }}>
      <div style={{ display: "flex", gap: 8, alignItems: "flex-start", flexWrap: "wrap" }}>
        <SeverityTag severity={f.severity} word={f.severity === "crit" ? "Changes what is counted" : "Worth a look"} />
        <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800, flex: "1 1 220px", minWidth: 0, overflowWrap: "anywhere" }}>{f.title}</h2>
      </div>
      <div className={amountClass(f.why)} style={{ fontSize: 13, color: "var(--ink-2)" }}>{f.why}</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(220px, 100%), 1fr))", gap: 8 }}>
        {f.items.map((i) => (
          <div key={i.source} style={{ padding: "8px 10px", borderRadius: 10, background: "var(--surface-2)", display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
            <strong style={{ fontSize: 13, overflowWrap: "anywhere" }}>{i.label}</strong>
            <span className="num" style={{ fontSize: 12, color: "var(--ink-2)", overflowWrap: "anywhere" }}>{i.detail}</span>
            {i.link && <Link to={i.link} aria-label={`Open ${i.label}`} style={{ fontSize: 12, alignSelf: "flex-start" }}>Open</Link>}
          </div>
        ))}
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        {f.actions.map((a) =>
          a.question ? (
            <ConfirmButton key={a.id} label={a.label} question={a.question} yes="Yes" no="Not yet" onConfirm={() => send(a.id)} />
          ) : (
            <button key={a.id} type="button" className="btn" disabled={busy} onClick={() => send(a.id)}>{a.label}</button>
          ),
        )}
        {f.fix_on && <Link to={f.fix_on} style={{ fontSize: 13 }}>Settle it where it was linked</Link>}
      </div>
      <Said text={error} tone="error" />
    </section>
  );
}

export function DataCheck() {
  const state = useApi<CheckOut>("/api/v1/data-check", { live: true });
  const [said, setSaid] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  const [running, setRunning] = useState(false);
  async function again() {
    setRunning(true);
    setSaid(null);
    try {
      await api("/api/v1/data-check/run", { method: "POST" });
      state.reload();
      setSaid({ text: "Checked again.", tone: "info" });
    } catch (e) {
      setSaid({ text: (e as Error).message, tone: "error" });
    } finally {
      setRunning(false);
    }
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 820 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800, flex: "1 1 auto" }}>Data check</h1>
        <button type="button" className="btn" onClick={again} disabled={running} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <RefreshCw size={14} aria-hidden="true" />{running ? "Checking…" : "Check again"}
        </button>
      </div>
      <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)" }}>
        The app looks over everything it keeps for anything counted twice, bills that look alike, the same purchase from two sources, and mail that may not be from who it says. It runs on its own every six hours, with no model. Nothing changes until you answer.
      </p>
      <Said text={said?.text ?? null} tone={said?.tone} />
      <Show state={state} what="the data check">
        {(d) => {
          const top = Math.max(1, ...d.kinds.map((k) => k.count));
          return (
            <>
              <section className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
                <h2 className="sr-only">What the check found</h2>
                {d.findings.length > 0 && (
                  <div role="img" aria-label={d.kinds.map((k) => `${k.words}: ${k.count}`).join("; ")} style={{ display: "flex", flexDirection: "column", gap: 7 }}>
                    {d.kinds.map((k) => (
                      <div key={k.kind} style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                        <span style={{ fontSize: 13, fontWeight: 700 }}>{k.words} · {k.count}</span>
                        <div style={{ height: 12, background: "var(--surface-2)", borderRadius: 6, overflow: "hidden" }}>
                          {k.count > 0 && <div style={{ width: `${(k.count / top) * 100}%`, minWidth: 8, height: "100%", background: "var(--bill)", borderRadius: 6 }} />}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
                <div style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 14, fontWeight: 700, color: d.findings.length ? "var(--ink)" : "var(--safe-ink)" }}>
                  {d.findings.length === 0 ? <CircleCheck size={16} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2 }} /> : d.findings.some((f) => f.severity === "crit") ? <OctagonAlert size={16} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2, color: "var(--critical-ink)" }} /> : <TriangleAlert size={16} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2, color: "var(--warning-ink)" }} />}
                  <span className={amountClass(d.said)}>{d.said}</span>
                </div>
                <span style={{ fontSize: 12, color: "var(--muted)" }}>
                  {d.last ? `Last checked ${when(d.last.at)}.` : "Looked over as this page opened; the six-hourly check has not run yet."}{d.decided ? ` ${d.decided} you settled as different are not raised again.` : ""}
                </span>
              </section>
              {d.findings.map((f) => <FindingCard key={f.key} f={f} onAnswered={(text) => { setSaid({ text, tone: "info" }); state.reload(); }} />)}
              {d.fixed.length > 0 && (
                <section className="card" style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 8 }}>
                  <h2 style={{ margin: 0, fontSize: 14, fontWeight: 800, display: "flex", alignItems: "center", gap: 6 }}><Wrench size={15} aria-hidden="true" />Set right on its own</h2>
                  <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Only what is certain: the same email kept twice, and a bill a lender's own plan already counts.</span>
                  <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 6 }}>
                    {d.fixed.map((x, i) => (
                      <li key={i} style={{ fontSize: 13, display: "flex", flexDirection: "column", gap: 1, borderTop: i ? "1px solid var(--surface-2)" : 0, paddingTop: i ? 6 : 0 }}>
                        <span><strong className="num">{x.what}</strong> <span style={{ color: "var(--muted)" }}>· {shortDate(x.on)}</span></span>
                        <span className="num" style={{ color: "var(--ink-2)" }}>{x.why}</span>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </>
          );
        }}
      </Show>
    </div>
  );
}
