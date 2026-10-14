// Next steps: everything the app needs from you, each a tap away from the place to do it.
// The strip rides under the header on every screen; the full list sits on Today.
import { Link, useLocation } from "react-router-dom";
import { useApi } from "../api";

export interface Step {
  id: string;
  severity: "critical" | "warning" | "info";
  text: string;
  action: string;
  link: string;
}

const DOT = { info: "var(--paycheck)", warning: "var(--warning)", critical: "var(--critical)" };

export function useNextSteps() {
  const { pathname } = useLocation();
  return useApi<{ steps: Step[]; count: number }>(`/api/v1/next_steps?at=${encodeURIComponent(pathname)}`);
}

export function NextStepsStrip() {
  const { pathname } = useLocation();
  const { data } = useNextSteps();
  if (!data || data.steps.length === 0 || pathname === "/today") return null;
  const shown = data.steps.filter((s) => s.severity !== "info").slice(0, 3);
  const rest = data.count - shown.length;
  if (shown.length === 0 && rest > 0) {
    return (
      <div style={{ fontSize: 12, color: "var(--ink-2)" }}>
        <Link to="/today#steps" style={{ color: "inherit" }}>{data.count} thing{data.count === 1 ? "" : "s"} to set up or finish, with a tap for each</Link>
      </div>
    );
  }
  return (
    <nav aria-label="Next steps" style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", fontSize: 12 }}>
      {shown.map((s) => (
        <Link key={s.id} to={s.link} style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "4px 10px", borderRadius: 999, border: "1px solid var(--hairline)", background: "var(--surface)", color: "var(--ink)", textDecoration: "none", maxWidth: "100%" }}>
          <span className="sw" style={{ background: DOT[s.severity], margin: 0, flexShrink: 0 }} />
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 360 }}>{s.text}</span>
          <strong style={{ whiteSpace: "nowrap" }}>{s.action} →</strong>
        </Link>
      ))}
      {rest > 0 && <Link to="/today#steps" style={{ color: "var(--ink-2)" }}>+{rest} more</Link>}
    </nav>
  );
}

export function NextStepsCard() {
  const { data } = useNextSteps();
  if (!data) return null;
  return (
    <section id="steps" className="card" style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 4 }}>
      <strong style={{ fontSize: 14 }}>Next steps</strong>
      {data.steps.length === 0 && <div style={{ fontSize: 13, color: "var(--muted)" }}>Nothing needs you. Every source reads, every key is in place.</div>}
      {data.steps.map((s, i) => (
        <div key={s.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 0", borderTop: i === 0 ? 0 : "1px solid var(--surface-2)" }}>
          <span className="sw" style={{ background: DOT[s.severity], flexShrink: 0, margin: 0 }} />
          <div style={{ flexGrow: 1, fontSize: 13, fontWeight: s.severity === "info" ? 500 : 700 }}>{s.text}</div>
          <Link to={s.link} className="btn" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", minHeight: 34, padding: "4px 10px", whiteSpace: "nowrap" }}>{s.action} →</Link>
        </div>
      ))}
    </section>
  );
}
