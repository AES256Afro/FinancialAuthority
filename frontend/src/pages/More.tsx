// Every section, for a phone: the tab bar holds the daily four, and this page holds the rest.
import { Link } from "react-router-dom";
import { NAV_GROUPS, NAV_ITEMS } from "../components/Shell";

export function More() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, maxWidth: 820 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>More</h1>
      {[...NAV_GROUPS, { id: "foot", label: "Settings" }].map((g) => {
        const items = NAV_ITEMS.filter((n) => n.group === g.id && n.to !== "/more");
        if (!items.length) return null;
        return (
          <section key={g.id} aria-labelledby={`more-${g.id}`} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <h2 id={`more-${g.id}`} className="lab" style={{ margin: 0 }}>{g.label}</h2>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(220px, 100%), 1fr))", gap: 10 }}>
              {items.map(({ to, label, icon: Icon, hint }) => (
                <Link key={to} to={to} className="card" style={{ padding: 12, display: "flex", gap: 10, alignItems: "flex-start", textDecoration: "none", color: "var(--ink)" }}>
                  <Icon size={20} strokeWidth={1.8} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2 }} />
                  <span><strong>{label}</strong><span style={{ display: "block", fontSize: 12, color: "var(--ink-2)", marginTop: 2 }}>{hint}</span></span>
                </Link>
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}
