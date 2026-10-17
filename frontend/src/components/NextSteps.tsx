// Next steps: everything the app needs from you, each a tap away from the place to do it.
// The strip rides under the header on every screen; the full list sits on Today.
import { Link, useLocation } from "react-router-dom";
import { useApi } from "../api";
import { amountClass } from "../format";
import { Failed, Loading } from "./State";
import { SeverityTag } from "./Status";

export interface Step {
  id: string;
  severity: "critical" | "warning" | "info";
  text: string;
  action: string;
  link: string;
}

// Live: a save on any screen (a bank linked, a paycheck added) asks for the steps again.
export function useNextSteps() {
  const { pathname } = useLocation();
  return useApi<{ steps: Step[]; count: number }>(`/api/v1/next_steps?at=${encodeURIComponent(pathname)}`, { live: true });
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
        // The whole text shows: on a narrow screen the chip wraps onto a second line instead of cutting it off.
        <Link key={s.id} to={s.link} style={{ display: "inline-flex", alignItems: "center", flexWrap: "wrap", gap: 6, padding: "4px 10px", borderRadius: 14, border: "1px solid var(--hairline)", background: "var(--surface)", color: "var(--ink)", textDecoration: "none", maxWidth: "100%", minHeight: 32, boxSizing: "border-box" }}>
          <SeverityTag severity={s.severity} />
          <span className={amountClass(s.text)} style={{ minWidth: 0, overflowWrap: "anywhere" }}>{s.text}</span>
          <strong style={{ whiteSpace: "nowrap" }}>{s.action} →</strong>
        </Link>
      ))}
      {rest > 0 && <Link to="/today#steps" style={{ color: "var(--ink-2)" }}>+{rest} more</Link>}
    </nav>
  );
}

export function NextStepsCard() {
  const steps = useNextSteps();
  if (steps.error && !steps.data) return <Failed error={steps.error} what="the next steps" onRetry={steps.reload} />;
  if (!steps.data) return <Loading what="the next steps" />;
  const data = steps.data;
  return (
    <section id="steps" className="card" style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 4 }}>
      <h2 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>Next steps</h2>
      {data.steps.length === 0 && <div style={{ fontSize: 13, color: "var(--muted)" }}>Nothing to set up. Every bank and mailbox is reading, and every key is saved.</div>}
      {data.steps.map((s, i) => (
        <div key={s.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 0", borderTop: i === 0 ? 0 : "1px solid var(--surface-2)", flexWrap: "wrap" }}>
          <SeverityTag severity={s.severity} />
          <div className={amountClass(s.text)} style={{ flex: "1 1 200px", minWidth: 0, fontSize: 13, fontWeight: s.severity === "info" ? 500 : 700 }}>{s.text}</div>
          <Link to={s.link} className="btn" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", minHeight: 34, padding: "4px 10px", whiteSpace: "nowrap" }}>{s.action} →</Link>
        </div>
      ))}
    </section>
  );
}
