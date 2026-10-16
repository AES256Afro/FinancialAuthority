// The don't-buy list: what to hold off on and why, from the engine, each with where to look.
import { Link } from "react-router-dom";
import { useApi } from "../api";
import { placeName } from "./OneThing";
import { Failed, Loading } from "./State";
import { SeverityTag } from "./Status";

interface Item {
  kind: string;
  severity: "critical" | "warning" | "info";
  text: string;
  amount: number | null;
  sources: string[];
  link: string;
}

export function Watchlist() {
  const list = useApi<{ items: Item[]; not_advice: string }>("/api/v1/watchlist");
  if (list.error && !list.data) return <Failed error={list.error} what="what to hold off on" onRetry={list.reload} />;
  if (!list.data) return <Loading what="what to hold off on" />;
  const data = list.data;
  return (
    <section className="card" style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 4 }}>
      <h2 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>Hold off on</h2>
      {data.items.length === 0 && <div style={{ fontSize: 13, color: "var(--muted)" }}>Nothing to hold off on right now.</div>}
      {data.items.map((i, k) => (
        <div key={k} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 0", borderTop: k === 0 ? 0 : "1px solid var(--surface-2)", flexWrap: "wrap" }}>
          <SeverityTag severity={i.severity} />
          <div style={{ flex: "1 1 200px", minWidth: 0, fontSize: 13, fontWeight: i.severity === "info" ? 500 : 700 }}>{i.text}</div>
          {i.link && <Link to={i.link} className="btn" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", minHeight: 32, padding: "4px 10px" }} aria-label={`Open ${placeName(i.link)}: ${i.text}`}>→</Link>}
        </div>
      ))}
      <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>{data.not_advice}</div>
    </section>
  );
}
