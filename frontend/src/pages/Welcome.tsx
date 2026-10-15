// The first run: five steps, each done when the registers say so. Skip any; come back from Today.
import { CircleCheck } from "lucide-react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import type { FirstRun } from "../guide";

export function FirstRunBanner() {
  const fr = useApi<FirstRun>("/api/v1/firstrun");
  if (!fr.data || !fr.data.show) return null;
  const next = fr.data.steps.find((s) => !s.done);
  return (
    <section className="card" style={{ padding: "12px 16px", display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", background: "var(--paycheck-soft)", borderColor: "transparent" }}>
      <div style={{ flex: "1 1 240px" }}>
        <strong>Getting set up: {fr.data.done} of {fr.data.total}</strong>
        {next && <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Next: {next.title}</div>}
      </div>
      <Link to="/welcome" className="btn dark" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center" }}>Continue</Link>
    </section>
  );
}

export function Welcome() {
  const fr = useApi<FirstRun>("/api/v1/firstrun");
  if (!fr.data) return <div style={{ color: "var(--muted)" }}>Opening…</div>;
  const d = fr.data;
  const put = async (body: Record<string, unknown>) => {
    await api("/api/v1/firstrun", { method: "PUT", body: JSON.stringify(body) });
    fr.reload();
  };
  const current = d.steps.find((s) => !s.done);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 720 }}>
      <div>
        <h1 style={{ margin: "0 0 4px", fontSize: 22, fontWeight: 800 }}>Five steps to your first plan</h1>
        <p style={{ margin: 0, color: "var(--ink-2)" }}>{d.done} of {d.total} done. Each step takes a minute or two; skip any and come back from Today.</p>
      </div>
      <div role="progressbar" aria-label="Steps done" aria-valuemin={0} aria-valuemax={d.total} aria-valuenow={d.done} style={{ display: "flex", gap: 4 }}>
        {d.steps.map((s) => <div key={s.id} style={{ flex: 1, height: 8, borderRadius: 4, background: s.done ? "var(--safe)" : "var(--surface-2)" }} />)}
      </div>
      <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 10 }}>
        {d.steps.map((s, i) => (
          <li key={s.id} className="card" style={{ padding: 14, display: "flex", gap: 12, alignItems: "flex-start", outline: current?.id === s.id ? "2px solid var(--ink)" : "none" }}>
            <span aria-hidden="true" style={{ width: 28, height: 28, borderRadius: 14, display: "grid", placeItems: "center", flexShrink: 0, background: s.done ? "var(--safe-soft)" : "var(--surface-2)", color: s.done ? "var(--safe-ink)" : "var(--ink-2)", fontWeight: 800 }}>
              {s.done ? <CircleCheck size={18} /> : i + 1}
            </span>
            <div style={{ display: "flex", flexDirection: "column", gap: 6, flexGrow: 1 }}>
              <strong style={{ fontSize: 15 }}>{s.title}{s.done ? <span style={{ fontWeight: 600, color: "var(--safe-ink)" }}> · done</span> : null}</strong>
              <span style={{ fontSize: 13, color: "var(--ink-2)" }}>{s.text}</span>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <Link to={s.link} className={`btn${current?.id === s.id ? " dark" : ""}`} style={{ textDecoration: "none", display: "inline-flex", alignItems: "center" }}>{s.done ? "Look again" : "Do this"}</Link>
                {!s.done && <button type="button" className="btn" onClick={() => put({ mark: s.id })}>Mark done</button>}
              </div>
            </div>
          </li>
        ))}
      </ol>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button type="button" className="btn" onClick={() => put({ dismissed: !d.dismissed })}>{d.dismissed ? "Show this on Today again" : "Hide this from Today"}</button>
        <Link to="/today" className="btn" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center" }}>Go to Today</Link>
      </div>
    </div>
  );
}
