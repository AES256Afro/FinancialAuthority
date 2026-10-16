// Setup that checks itself: each step with its state now (icon and word), what is wrong in plain words, and the
// link to fix it; "Test everything" tries every connection for real.
import { AlertTriangle, CheckCircle2, Circle, XCircle } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";

interface Check { id: string; title: string; state: "ok" | "warn" | "todo" | "error"; detail: string; fix: string; link: string }
interface Test { id: string; title: string; ok: boolean; detail: string }

const LOOK = {
  ok: { Icon: CheckCircle2, word: "Working", color: "var(--safe)" },
  warn: { Icon: AlertTriangle, word: "Look at this", color: "var(--warning)" },
  todo: { Icon: Circle, word: "To do", color: "var(--muted)" },
  error: { Icon: XCircle, word: "Not working", color: "var(--critical)" },
} as const;

export function SetupChecklist() {
  const { data, reload } = useApi<{ checks: Check[]; summary: Record<string, number> }>("/api/v1/setup/check");
  const [tests, setTests] = useState<Test[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!data) return null;
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
        <strong id="checklist-h" style={{ fontSize: 15 }}>Where your setup stands</strong>
        <span style={{ fontSize: 13, color: "var(--ink-2)" }}>{s.ok} working · {s.todo} to do{s.warn ? ` · ${s.warn} to look at` : ""}{s.error ? ` · ${s.error} not working` : ""}</span>
        <button type="button" className="btn dark" style={{ marginLeft: "auto" }} disabled={busy} onClick={testAll}>{busy ? "Testing…" : "Test everything"}</button>
      </div>
      <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(260px, 100%), 1fr))", gap: 8 }}>
        {data.checks.map((c) => {
          const { Icon, word, color } = LOOK[c.state];
          return (
            <li key={c.id} style={{ border: "1px solid var(--hairline)", borderRadius: 10, padding: 10, display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 800, color }}><Icon size={15} aria-hidden />{word}</span>
              <strong style={{ fontSize: 14 }}>{c.title}</strong>
              <span style={{ fontSize: 13, color: "var(--ink-2)", overflowWrap: "anywhere" }}>{c.detail}</span>
              {c.state !== "ok" && c.fix && <span style={{ fontSize: 13 }}>{c.fix} {c.link && <Link to={c.link}>Go there</Link>}</span>}
            </li>
          );
        })}
      </ul>
      {error && <div style={{ fontSize: 13, color: "var(--critical)" }}>{error}</div>}
      {tests && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <strong style={{ fontSize: 13 }}>Tested just now</strong>
          {tests.length === 0 && <span style={{ fontSize: 13, color: "var(--ink-2)" }}>Nothing to test yet: no mailbox, bank keys or https address.</span>}
          {tests.map((t) => (
            <span key={t.id} style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 13, color: t.ok ? "var(--safe)" : "var(--critical)" }}>
              {t.ok ? <CheckCircle2 size={15} aria-hidden style={{ flexShrink: 0, marginTop: 2 }} /> : <XCircle size={15} aria-hidden style={{ flexShrink: 0, marginTop: 2 }} />}
              <span><strong>{t.title}: {t.ok ? "works." : "does not work."}</strong> <span style={{ color: "var(--ink-2)" }}>{t.detail}</span></span>
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
