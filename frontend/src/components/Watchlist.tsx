// The don't-buy list: what to hold off on and why, from the engine, each with where to look.
import { Link } from "react-router-dom";
import { useApi } from "../api";

interface Item {
  kind: string;
  severity: "critical" | "warning" | "info";
  text: string;
  amount: number | null;
  sources: string[];
  link: string;
}

const DOT = { info: "var(--paycheck)", warning: "var(--warning)", critical: "var(--critical)" };

export function Watchlist() {
  const { data } = useApi<{ items: Item[]; not_advice: string }>("/api/v1/watchlist");
  if (!data) return null;
  return (
    <section className="card" style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 4 }}>
      <strong style={{ fontSize: 14 }}>Hold off on</strong>
      {data.items.length === 0 && <div style={{ fontSize: 13, color: "var(--muted)" }}>Nothing to hold off on. The registers are clean.</div>}
      {data.items.map((i, k) => (
        <div key={k} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 0", borderTop: k === 0 ? 0 : "1px solid var(--surface-2)" }}>
          <span className="sw" style={{ background: DOT[i.severity], flexShrink: 0, margin: 0 }} />
          <div style={{ flexGrow: 1, fontSize: 13, fontWeight: i.severity === "info" ? 500 : 700 }}>{i.text}</div>
          <Link to={i.link} className="btn" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", minHeight: 32, padding: "4px 10px" }} aria-label="Open">→</Link>
        </div>
      ))}
      <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>{data.not_advice}</div>
    </section>
  );
}
