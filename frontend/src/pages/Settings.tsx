import { Link } from "react-router-dom";
import { useApi } from "../api";
import { Cushion } from "../components/Cushion";
import { when } from "../format";
import { roleOf, type MePerson } from "../types-people";

const ITEMS = [
  { to: "/settings/setup", title: "Setting up", text: "What only you can do, in order: the app's secrets, mail, your bank, reaching it from anywhere, your phone, Claude, backups." },
  { to: "/settings/appearance", title: "Appearance", text: "Eight home views, three looks, text size. Saved for all your devices." },
  { to: "/settings/connectors", title: "Connections", text: "Your banks, your mailboxes, importing a file, and Rocket Money exports in and out." },
  { to: "/settings/models", title: "Models", text: "The advisor and the reviewer: Ollama, any OpenAI-compatible server, or Claude by API; your rules and goals." },
  { to: "/settings/security", title: "Sign-in and security", text: "Passkeys, a code from an authenticator app at sign-in, and the devices signed in." },
  { to: "/settings/people", title: "People", text: "A sign-in for someone else, with everything or the shared accounts only; which accounts are shared.", owner: true },
  { to: "/settings/privacy", title: "Privacy", text: "What each model sees, every service that receives anything, everything as one file, and deleting everything." },
  { to: "/settings/clients", title: "Apps and keys", text: "Apps and keys that can read your plan, and what each can see." },
  { to: "/settings/notifications", title: "Notifications", text: "Notifications on this device, reminders, and the morning brief through ntfy or Gotify." },
  { to: "/settings/backups", title: "Backups", text: "A copy of everything before each import and once a day; undo an import, or put everything back to a moment." },
  { to: "/settings/jobs", title: "Jobs", text: "What the app does on its own, and whether it ran." },
  { to: "/settings/audit", title: "Activity", text: "Every read a model, an app or a script made of your data." },
];

export function SettingsIndex({ me = null }: { me?: MePerson | null }) {
  const { data } = useApi<{ version: string; demo: boolean; transactions: number; today: string; tz: string; public_url: string | null }>("/api/v1/about");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 820 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Settings</h1>
      <Cushion />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(240px, 100%), 1fr))", gap: 12 }}>
        {ITEMS.filter((i) => !i.owner || roleOf(me) === "owner").map((i) => (
          <Link key={i.to} to={i.to} className="card" style={{ padding: 14, textDecoration: "none", color: "var(--ink)" }}>
            <h2 style={{ margin: 0, fontSize: 14, fontWeight: 800 }}>{i.title}</h2>
            <div style={{ fontSize: 13, color: "var(--ink-2)", marginTop: 4 }}>{i.text}</div>
          </Link>
        ))}
      </div>
      {data && (
        <div style={{ fontSize: 12, color: "var(--muted)" }}>
          Version {data.version}{data.demo ? " · demo data" : ""} · {data.transactions} transactions · today {when(data.today)} · time zone {data.tz}{data.public_url ? ` · published at ${data.public_url}` : " · not published yet"}
        </div>
      )}
    </div>
  );
}
