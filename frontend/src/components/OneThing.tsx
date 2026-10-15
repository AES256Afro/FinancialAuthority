// The one thing today: the single action with the biggest effect, why, and where to do it. Below it, the
// streaks the ledger counts. Nothing here moves money.
import { Flame } from "lucide-react";
import { Link } from "react-router-dom";
import { useApi } from "../api";
import type { TodayThing } from "../guide";
import { StatusPill } from "./Status";

export function OneThing() {
  const t = useApi<TodayThing>("/api/v1/today");
  if (t.error || !t.data) return null;
  const d = t.data;
  return (
    <section className="card" aria-label="The one thing today" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10, borderLeft: `4px solid ${d.status === "crit" ? "var(--critical)" : d.status === "warn" ? "var(--warning)" : "var(--safe)"}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span className="lab">The one thing today</span>
        <StatusPill status={d.status} />
      </div>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ fontSize: 17, fontWeight: 800, flex: "1 1 260px" }}>{d.thing.text}</div>
        {d.thing.link && <Link to={d.thing.link} className="btn dark" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center" }}>Open</Link>}
      </div>
      <div style={{ fontSize: 13, color: "var(--ink-2)" }}>{d.thing.why}</div>
      {d.others.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
          <span className="lab">Then</span>
          {d.others.map((o) => <Link key={o.source + o.date} to={o.link} style={{ color: "var(--ink)" }}>{o.text}</Link>)}
        </div>
      )}
      {d.streaks.length > 0 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }} aria-label="Streaks">
          {d.streaks.map((s) => (
            <span key={s.key} title={s.since ? `since ${s.since}` : undefined} style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 12, fontWeight: 700, padding: "4px 9px", borderRadius: 999, background: s.good ? "var(--safe-soft)" : "var(--surface-2)", color: s.good ? "var(--safe-ink)" : "var(--ink-2)" }}>
              {s.good && <Flame size={13} aria-hidden="true" />}{s.label}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
