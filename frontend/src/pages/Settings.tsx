import { Link } from "react-router-dom";
import { useApi } from "../api";

const ITEMS = [
  { to: "/settings/appearance", title: "Appearance", text: "Eight home views, three looks, kept per device." },
  { to: "/settings/connectors", title: "Connectors", text: "Rocket Money export in and out; the import folder; live bank connectors arrive in M1." },
  { to: "/settings/models", title: "Models", text: "The advisor and the reviewer: Ollama, any OpenAI-compatible server, or Claude by API; your rules and goals." },
  { to: "/settings/clients", title: "Clients", text: "Apps signed in through OAuth, and API keys for scripts, each with a scope and a redaction level." },
  { to: "/settings/jobs", title: "Jobs", text: "What the app does on its own, and whether it ran." },
];

export function SettingsIndex() {
  const { data } = useApi<{ version: string; demo: boolean; transactions: number; today: string; tz: string; public_url: string | null }>("/api/v1/about");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 820 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Settings</h1>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 12 }}>
        {ITEMS.map((i) => (
          <Link key={i.to} to={i.to} className="card" style={{ padding: 14, textDecoration: "none", color: "var(--ink)" }}>
            <strong>{i.title}</strong>
            <div style={{ fontSize: 13, color: "var(--ink-2)", marginTop: 4 }}>{i.text}</div>
          </Link>
        ))}
      </div>
      {data && (
        <div style={{ fontSize: 12, color: "var(--muted)" }}>
          Version {data.version}{data.demo ? " · demo data" : ""} · {data.transactions} transactions · today {data.today} · time zone {data.tz}{data.public_url ? ` · published at ${data.public_url}` : " · not published yet"}
        </div>
      )}
    </div>
  );
}
