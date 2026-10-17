// The one thing today: the single action with the biggest effect, why, and where to do it. Below it, the
// streaks the ledger counts. Nothing here moves money.
import { Flame } from "lucide-react";
import { Link } from "react-router-dom";
import { useApi } from "../api";
import { amountClass, when } from "../format";
import type { TodayThing } from "../guide";
import { NAV_ITEMS } from "./Shell";
import { Failed, Loading } from "./State";
import { StatusPill } from "./Status";

const PLACES: Record<string, string> = { "/settings/connectors": "Connections", "/settings/setup": "Setting up", "/settings/appearance": "Appearance", "/settings/notifications": "Notifications", "/checkin": "Weekly check-in", "/welcome": "Setup steps", "/glossary": "Words" };

/** The name of the screen a link opens, so a button can say where it goes: "Open Bills". */
export function placeName(link: string): string {
  const path = link.split(/[?#]/)[0] || "/";
  if (PLACES[path]) return PLACES[path];
  const match = NAV_ITEMS.filter((n) => (n.end ? path === n.to : path === n.to || path.startsWith(`${n.to}/`))).sort((a, b) => b.to.length - a.to.length)[0];
  return match?.label ?? "it";
}

export function OneThing() {
  const t = useApi<TodayThing>("/api/v1/today");
  if (t.error && !t.data) return <Failed error={t.error} what="the one thing today" onRetry={t.reload} />;
  if (!t.data) return <Loading what="the one thing today" />;
  const d = t.data;
  return (
    <section className="card" aria-labelledby="one-thing-title" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10, borderLeft: `4px solid ${d.status === "crit" ? "var(--critical)" : d.status === "warn" ? "var(--warning)" : "var(--safe)"}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <h2 id="one-thing-title" className="lab" style={{ margin: 0 }}>The one thing today</h2>
        <StatusPill status={d.status} />
      </div>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <div className={amountClass(d.thing.text)} style={{ fontSize: 17, fontWeight: 800, flex: "1 1 260px" }}>{d.thing.text}</div>
        {d.thing.link && <Link to={d.thing.link} className="btn dark" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center" }}>Open {placeName(d.thing.link)}</Link>}
      </div>
      <div className={amountClass(d.thing.why)} style={{ fontSize: 13, color: "var(--ink-2)" }}>{d.thing.why}</div>
      {d.others.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
          <span className="lab">Then</span>
          {d.others.map((o) => <Link key={o.source + o.date} to={o.link} style={{ color: "var(--ink)", minHeight: 32, display: "inline-flex", alignItems: "center" }}><span className={amountClass(o.text)}>{o.text}</span></Link>)}
        </div>
      )}
      {d.streaks.length > 0 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }} aria-label="Streaks">
          {d.streaks.map((s) => (
            <span key={s.key} title={s.since ? `since ${when(s.since)}` : undefined} style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 12, fontWeight: 700, padding: "4px 9px", borderRadius: 999, background: s.good ? "var(--safe-soft)" : "var(--surface-2)", color: s.good ? "var(--safe-ink)" : "var(--ink-2)" }}>
              {s.good && <Flame size={13} aria-hidden="true" />}{s.label}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
