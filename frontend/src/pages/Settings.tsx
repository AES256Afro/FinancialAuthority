import { Link } from "react-router-dom";
import { useApi } from "../api";
import { Cushion } from "../components/Cushion";
import { when } from "../format";
import { roleOf, type MePerson } from "../types-people";

// A second person sees their own sign-in page, and the cards say what is the owner's alone (the server refuses it).
type Item = { to: string; title: string; text: string; owner?: boolean; person?: { title?: string; text: string } };
const ITEMS: Item[] = [
  { to: "/settings/setup", title: "Setting up", text: "What only you can do, in order: the app's secrets, mail, your bank, reaching it from anywhere, your phone, Claude, backups." },
  { to: "/settings/appearance", title: "Appearance", text: "The home views, four looks, text size. Saved for all your devices.", person: { text: "The home views, four looks, text size, Hide amounts. One appearance for the whole app: a change here changes it for the owner too." } },
  { to: "/settings/connectors", title: "Connections", text: "Your banks, your mailboxes, importing a file, and Rocket Money exports in and out." },
  { to: "/settings/models", title: "Models", text: "The advisor and the reviewer: Ollama, any OpenAI-compatible server, or Claude by API; your rules and goals." },
  { to: "/settings/security", title: "Sign-in and security", text: "Passkeys, a code from an authenticator app at sign-in, and the devices signed in.", person: { title: "Your sign-in", text: "Your passkeys, a code from an authenticator app at sign-in, and the devices signed in as you." } },
  { to: "/settings/people", title: "People", text: "A sign-in for someone else, with everything or the shared accounts only; which accounts are shared.", owner: true },
  { to: "/settings/privacy", title: "Privacy", text: "What each model sees, every service that receives anything, everything as one file, and deleting everything.", person: { text: "What each model sees, every service that receives anything, and everything as one file. Only the owner can delete everything." } },
  { to: "/settings/clients", title: "Apps and keys", text: "Apps and keys that can read your plan, and what each can see." },
  { to: "/settings/notifications", title: "Notifications", text: "Notifications on this device, reminders, and the morning brief through ntfy or Gotify." },
  { to: "/settings/backups", title: "Backups", text: "A copy of everything before each import and once a day; undo an import, or put everything back to a moment.", person: { text: "A copy of everything before each import and once a day. Only the owner puts everything back to a moment or sets the copy kept off this server." } },
  { to: "/settings/jobs", title: "Jobs", text: "What the app does on its own, and whether it ran." },
  { to: "/data-check", title: "Data check", text: "Anything counted twice, and mail that may not be genuine." },
  { to: "/settings/audit", title: "Activity", text: "Every read a model, an app or a script made of your data." },
];

export function SettingsIndex({ me = null }: { me?: MePerson | null }) {
  const { data } = useApi<{ version: string; demo: boolean; transactions: number; today: string; tz: string; public_url: string | null }>("/api/v1/about");
  const owner = roleOf(me) === "owner";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 820 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Settings</h1>
      <Cushion />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(240px, 100%), 1fr))", gap: 12 }}>
        {ITEMS.filter((i) => !i.owner || owner).map((i) => (
          <Link key={i.to} to={i.to} className="card" style={{ padding: 14, textDecoration: "none", color: "var(--ink)" }}>
            <h2 style={{ margin: 0, fontSize: 14, fontWeight: 800 }}>{(!owner && i.person?.title) || i.title}</h2>
            <div style={{ fontSize: 13, color: "var(--ink-2)", marginTop: 4 }}>{(!owner && i.person?.text) || i.text}</div>
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
