// Settings › Setting up: where each part runs, and the few things only the owner can do, in order. Written for
// someone who installed the app from GitHub with nobody beside them. Each section has an anchor the next steps
// and the other screens link to.
import { ExternalLink } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../api";
import { Where } from "../components/Where";

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section id={id} className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8, scrollMarginTop: 16 }}>
      <strong style={{ fontSize: 15 }}>{title}</strong>
      <div style={{ fontSize: 13, color: "var(--ink-2)", display: "flex", flexDirection: "column", gap: 8, maxWidth: "80ch" }}>{children}</div>
    </section>
  );
}

function Out({ href, children }: { href: string; children: ReactNode }) {
  return <a href={href} target="_blank" rel="noreferrer noopener" style={{ display: "inline-flex", alignItems: "center", gap: 3 }}>{children}<ExternalLink size={11} aria-hidden /></a>;
}

// Where each part runs: the devices you hold, the server the app lives on, and what it reaches.
function Map() {
  const box = (x: number, title: string, sub: string, tone: string) => (
    <g>
      <rect x={x} y={20} width={210} height={74} rx={10} fill={`var(--${tone}-soft, var(--surface-2))`} stroke="var(--hairline)" />
      <text x={x + 105} y={50} textAnchor="middle" fontSize={14} fontWeight={800} fill="var(--ink)">{title}</text>
      <text x={x + 105} y={72} textAnchor="middle" fontSize={12} fill="var(--ink-2)">{sub}</text>
    </g>
  );
  return (
    <svg viewBox="0 0 720 112" width="100%" role="img" aria-label="Your phone and computer open the app in a browser; the app and its Proton Mail Bridge run on your server; the server reads your banks and mail." style={{ maxWidth: 820, display: "block" }}>
      {box(10, "Your phone, your computer", "a browser, Claude", "paycheck")}
      {box(255, "Your server", "the app and its Bridge", "safe")}
      {box(500, "Banks and mail", "read-only", "bill")}
      <line x1={222} x2={253} y1={57} y2={57} stroke="var(--ink-2)" strokeWidth={2} markerEnd="url(#arr)" />
      <line x1={467} x2={498} y1={57} y2={57} stroke="var(--ink-2)" strokeWidth={2} markerEnd="url(#arr)" />
      <defs><marker id="arr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M1 1L9 5L1 9" fill="none" stroke="var(--ink-2)" strokeWidth={1.6} /></marker></defs>
    </svg>
  );
}

export function Setup() {
  const about = useApi<{ version: string; public_url: string | null; install?: string; data_key_set?: boolean }>("/api/v1/about");
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const publicUrl = about.data?.public_url || origin;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 960 }}>
      <div>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Setting up</h1>
        <p style={{ margin: "4px 0 0", color: "var(--ink-2)", maxWidth: "80ch" }}>
          What only you can do, in order. When a step says <strong>on the server</strong>, it means the machine FinancialAuthority is installed on (a BoxPilot box, an Ubuntu server, a Windows or Mac with Docker), reached from your computer over SSH; not the phone or computer you are reading this on.
        </p>
      </div>
      <Map />

      <Section id="secrets" title="1. The app's own secrets">
        <span>Three values set when the app is installed: <code>FA_PASSWORD</code> (your sign-in), <code>FA_SESSION_SECRET</code> and <code>FA_DATA_KEY</code>. <code>FA_DATA_KEY</code> encrypts the passwords and keys you paste into the app; keep a copy somewhere safe, because a database restored without it keeps your ledger but loses those.</span>
        <span><strong>On BoxPilot:</strong> it makes all three when it installs the app; they are on the app's card, under its settings.</span>
        <span><strong>With Docker Compose:</strong> in the folder with <code>docker-compose.yml</code>, copy <code>.env.example</code> to <code>.env</code> and fill each with a long random value, for example from this command on the server:</span>
        <code style={{ display: "block", padding: "6px 8px", background: "var(--surface-2)", borderRadius: 6 }}>openssl rand -hex 32</code>
        <span>Then <code>docker compose up -d</code> again so the app reads them.</span>
      </Section>

      <Section id="mail" title="2. Your mail">
        <span>Bills, statements and paystubs read themselves from a mailbox. Proton Mail goes through the Proton Mail Bridge, which is signed in once on the server; Gmail and others use an app password. <Link to="/settings/connectors#mail">Connectors › Mailboxes</Link> walks through each, with the commands for this install.</span>
      </Section>

      <Section id="banks" title="3. Your bank">
        <span>Live transactions come through Plaid or Teller, with free developer keys you make once; the bank's own login opens in their window, and the app never sees your bank password. Or import your bank's CSV or OFX download. <Link to="/settings/connectors#banks">Connectors › Banks</Link> has the links for the keys.</span>
      </Section>

      <Section id="publish" title="4. Reach it from anywhere">
        <span>On your home network the app is at <code>{origin}</code>. To open it from your phone away from home, and to let Claude on the web reach it, it needs an https address:</span>
        <span><strong>On BoxPilot:</strong> install the Cloudflare Tunnel app, publish FinancialAuthority with it, then put the address it gives (for example <code>https://money.example.com</code>) in the app's <code>FA_PUBLIC_URL</code> setting on its card.</span>
        <span><strong>Elsewhere:</strong> Tailscale gives every device you own a private https address with no ports opened; or any reverse proxy with a certificate. Set <code>FA_PUBLIC_URL</code> to that address.</span>
        <Where id="tailscale" />
      </Section>

      <Section id="phone" title="5. Your phone">
        <span>Open <code>{publicUrl}</code> on the phone and add it to the Home Screen; it then opens like an app and can send you its own notifications. <Link to="/settings/notifications">Notifications</Link> has the button to turn them on and, if you prefer, ntfy or Gotify.</span>
        <Where id="phone_install" />
      </Section>

      <Section id="claude" title="6. Claude, or another model">
        <span>The built-in chat uses a model you choose on <Link to="/settings/models">Models</Link>: Ollama on the server (free), any OpenAI-compatible server, or Claude with an Anthropic key. Separately, Claude on the web, the desktop app or Claude Code can read your plan through the app's own sign-in:</span>
        <Where id="claude_connect" fill={{ url: `${publicUrl}/mcp` }} />
      </Section>

      <Section id="backups" title="7. Backups">
        <span>The app keeps its own copies of the database, before every import and once a day; <Link to="/settings/backups">Backups</Link> shows them. On BoxPilot the app's data is also archived with the server's backups. Keep <code>FA_DATA_KEY</code> with your backups.</span>
      </Section>

      <div style={{ fontSize: 12, color: "var(--muted)" }}>
        The same guide, with every install route, is in the project's <Out href="https://github.com/AES256Afro/FinancialAuthority/blob/main/docs/SETUP.md">docs/SETUP.md</Out>. Version {about.data?.version ?? ""}.
      </div>
    </div>
  );
}
