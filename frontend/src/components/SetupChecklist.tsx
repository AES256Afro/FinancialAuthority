// Setup that checks itself: each step with its state now (icon and word), what is wrong in plain words, and the
// link to fix it; "Test everything" tries every connection for real.
import { AlertTriangle, CheckCircle2, Circle, XCircle } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { Failed, Loading, Said } from "./State";

export interface Check { id: string; title: string; state: "ok" | "warn" | "todo" | "error"; detail: string; fix: string; link: string }
interface Test { id: string; title: string; ok: boolean; detail: string }

const LOOK = {
  ok: { Icon: CheckCircle2, word: "Working", color: "var(--safe-ink)" },
  warn: { Icon: AlertTriangle, word: "Look at this", color: "var(--warning-ink)" },
  todo: { Icon: Circle, word: "To do", color: "var(--ink-2)" },
  error: { Icon: XCircle, word: "Not working", color: "var(--critical-ink)" },
} as const;

export function SetupChecklist() {
  const state = useApi<{ checks: Check[]; summary: Record<string, number> }>("/api/v1/setup/check");
  const { data, reload } = state;
  const [tests, setTests] = useState<Test[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!data) {
    return state.error ? <Failed error={state.error} what="where your setup stands" onRetry={reload} /> : <Loading what="where your setup stands" />;
  }
  async function testAll() {
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ tests: Test[] }>("/api/v1/setup/test", { method: "POST" });
      setTests(r.tests);
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const s = data.summary;
  return (
    <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }} aria-labelledby="checklist-h">
      <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
        <h2 id="checklist-h" style={{ margin: 0, fontSize: 15, fontWeight: 800 }}>Where your setup stands</h2>
        <span style={{ fontSize: 13, color: "var(--ink-2)" }}>{s.ok} working · {s.todo} to do{s.warn ? ` · ${s.warn} to look at` : ""}{s.error ? ` · ${s.error} not working` : ""}</span>
        <button type="button" className="btn dark" style={{ marginLeft: "auto" }} disabled={busy} onClick={testAll}>{busy ? "Testing…" : "Test everything"}</button>
      </div>
      {error && <Said text={error} tone="error" />}
      <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(260px, 100%), 1fr))", gap: 8 }}>
        {data.checks.map((c) => {
          const { Icon, word, color } = LOOK[c.state];
          return (
            <li key={c.id} style={{ border: "1px solid var(--hairline)", borderRadius: 10, padding: 10, display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 800, color }}><Icon size={15} aria-hidden />{word}</span>
              <h3 style={{ margin: 0, fontSize: 14, fontWeight: 800 }}>{c.title}</h3>
              <span style={{ fontSize: 13, color: "var(--ink-2)", overflowWrap: "anywhere" }}>{c.detail}</span>
              {c.state !== "ok" && c.fix && <span style={{ fontSize: 13 }}>{c.fix} {c.link && <Link to={c.link} aria-label={`Go there: ${c.title}`}>Go there</Link>}</span>}
            </li>
          );
        })}
      </ul>
      {tests && (
        <div role="status" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <h3 style={{ margin: 0, fontSize: 13, fontWeight: 800 }}>Tested just now</h3>
          {tests.length === 0 && <span style={{ fontSize: 13, color: "var(--ink-2)" }}>Nothing to test yet: no mailbox, bank keys or https address.</span>}
          {tests.map((t) => (
            <span key={t.id} style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 13, color: t.ok ? "var(--safe-ink)" : "var(--critical-ink)" }}>
              {t.ok ? <CheckCircle2 size={15} aria-hidden style={{ flexShrink: 0, marginTop: 2 }} /> : <XCircle size={15} aria-hidden style={{ flexShrink: 0, marginTop: 2 }} />}
              <span><strong>{t.title}: {t.ok ? "works." : "does not work."}</strong> <span style={{ color: "var(--ink-2)" }}>{t.detail}</span></span>
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
