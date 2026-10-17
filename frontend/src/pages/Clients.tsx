// API keys for models and scripts. A key is shown once; the owner keeps it in the client, never in chat.
// Home Assistant and widgets (M35) read a few figures with such a key; the setup is shown here, address filled in.
import { CalendarDays, Copy, Gauge, Inbox, Receipt, Wallet } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { money, shortDate, when } from "../format";
import type { Status } from "../guide";
import { ConfirmButton } from "../components/ConfirmButton";
import { Failed, Loading, Said } from "../components/State";
import { StatusPill } from "../components/Status";
import { Where } from "../components/Where";

interface KeyRow {
  id: number;
  name: string;
  prefix: string;
  scopes: string[];
  redaction: string;
  created_at: string;
  last_used_at: string | null;
  revoked: boolean;
}

interface OAuthClientRow {
  id: number;
  client_id: string;
  name: string;
  scopes: string;
  redaction: string;
  created_at: string;
  last_used_at: string | null;
  revoked: boolean;
  sessions: number;
}

/** What a client may see, in words: the redaction level the server applies to everything it reads. */
export const SEES: Record<string, string> = {
  full: "Everything, with descriptions and notes",
  merchants: "Merchant names, no descriptions or notes",
  aggregates: "Totals only, no merchant names",
};

const SCOPE_WORD: Record<string, string> = { read: "read your plan", confirm: "confirm details on Needs you", approve: "approve emails", admin: "change settings" };

function scopesInWords(scopes: string[]): string {
  const words = scopes.filter(Boolean).map((s) => SCOPE_WORD[s] ?? s);
  return words.length ? words.join(", ") : "nothing";
}

function SeesSelect({ value, onChange, label }: { value: string; onChange: (v: string) => void; label?: string }) {
  return (
    <select className="input" style={{ minHeight: 32, padding: "4px 8px" }} value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
      {Object.entries(SEES).map(([k, w]) => <option key={k} value={k}>{w}</option>)}
    </select>
  );
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [said, setSaid] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setSaid({ text: "Copied.", tone: "info" });
      window.setTimeout(() => setSaid(null), 2000);
    } catch {
      setSaid({ text: "This browser would not copy; select the text and copy it by hand.", tone: "error" });
    }
  }
  return (
    <span style={{ display: "inline-flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      <button type="button" className="btn" onClick={copy} style={{ minHeight: 32, padding: "4px 10px", display: "inline-flex", gap: 4, alignItems: "center" }}><Copy size={13} aria-hidden="true" />{label}</button>
      {said && <Said text={said.text} tone={said.tone} />}
    </span>
  );
}

/** What GET /api/v1/ha answers: the figures a Home Assistant dashboard reads, each by name. */
interface HaSensors {
  as_of: string;
  safe_to_spend: number;
  safe_through: string | null;
  payday: string | null;
  days_to_payday: number | null;
  payday_amount: number | null;
  next_bill_name: string | null;
  next_bill_amount: number | null;
  next_bill_date: string | null;
  next_bill_days: number | null;
  needs_you: number;
  status: string;
  status_word: string;
  sentence: string | null;
}

const HA_SECRET = `# Paste your key after "Bearer " (it starts with fa_; make it under API keys on this page).
financialauthority_key: "Bearer fa_paste_your_key_here"`;

function haConfig(origin: string): string {
  return `rest:
  - resource: ${origin}/api/v1/ha
    scan_interval: 900
    headers:
      Authorization: !secret financialauthority_key
    sensor:
      - name: Safe to spend
        value_template: "{{ value_json.safe_to_spend }}"
        unit_of_measurement: "$"
        device_class: monetary
      - name: Days to payday
        value_template: "{{ value_json.days_to_payday }}"
        unit_of_measurement: "days"
      - name: Next bill
        value_template: "{{ value_json.next_bill_name }}"
        json_attributes:
          - next_bill_amount
          - next_bill_date
          - next_bill_days
      - name: Money needs you
        value_template: "{{ value_json.needs_you }}"
      - name: Money plan
        value_template: "{{ value_json.status_word }}"
        json_attributes:
          - sentence
          - safe_through
          - payday
          - payday_amount`;
}

const HA_CARD = `type: entities
title: Money
entities:
  - entity: sensor.safe_to_spend
  - entity: sensor.days_to_payday
  - entity: sensor.next_bill
    secondary_info: attribute
  - entity: sensor.money_needs_you
  - entity: sensor.money_plan`;

function CodeBlock({ title, text, copy }: { title: string; text: string; copy: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
      <span className="lab">{title}</span>
      <pre style={{ margin: 0, padding: "8px 10px", background: "var(--surface-2)", borderRadius: 8, fontSize: 12, lineHeight: 1.45, overflowX: "auto", maxWidth: "100%" }}><code>{text}</code></pre>
      <span><CopyButton text={text} label={copy} /></span>
    </div>
  );
}

const STATUSES: Status[] = ["good", "warn", "crit"];

/** The dashboard card as Home Assistant draws it, with today's figures from the same address it reads. */
function HaPreview({ s }: { s: HaSensors }) {
  const days = (n: number) => `${n} ${n === 1 ? "day" : "days"}`;
  const rows: { Icon: typeof Wallet; name: string; value: ReactNode; sub?: string }[] = [
    { Icon: Wallet, name: "Safe to spend", value: money(s.safe_to_spend), sub: s.safe_through ? `through ${shortDate(s.safe_through)}` : undefined },
    { Icon: CalendarDays, name: "Days to payday", value: s.days_to_payday === null ? "not known yet" : days(s.days_to_payday), sub: s.payday ? `the ${shortDate(s.payday)} check` : undefined },
    { Icon: Receipt, name: "Next bill", value: s.next_bill_name ?? "none coming", sub: s.next_bill_amount !== null && s.next_bill_date ? `${money(s.next_bill_amount)} on ${shortDate(s.next_bill_date)}` : undefined },
    { Icon: Inbox, name: "Money needs you", value: String(s.needs_you) },
    { Icon: Gauge, name: "Money plan", value: STATUSES.includes(s.status as Status) ? <StatusPill status={s.status as Status}>{s.status_word}</StatusPill> : s.status_word },
  ];
  return (
    <div style={{ background: "var(--surface-2)", borderRadius: 12, padding: 10, maxWidth: 420 }}>
      <div style={{ background: "var(--surface)", border: "1px solid var(--hairline)", borderRadius: 10, padding: "10px 14px", display: "flex", flexDirection: "column", gap: 8 }}>
        <strong style={{ fontSize: 15 }}>Money</strong>
        {rows.map((r) => (
          <div key={r.name} style={{ display: "grid", gridTemplateColumns: "20px minmax(0, 1fr) auto", gap: 10, alignItems: "center", fontSize: 13 }}>
            <r.Icon size={18} aria-hidden="true" style={{ color: "var(--ink-2)" }} />
            <span style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
              <span>{r.name}</span>
              {r.sub && <span style={{ fontSize: 11, color: "var(--muted)" }}>{r.sub}</span>}
            </span>
            <strong className="num" style={{ textAlign: "right" }}>{r.value}</strong>
          </div>
        ))}
      </div>
    </div>
  );
}

function HomeAssistant() {
  const state = useApi<HaSensors>("/api/v1/ha");
  const origin = window.location.origin;
  return (
    <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
      <h2 style={{ margin: 0, fontSize: 16, fontWeight: 800 }}>Home Assistant and widgets</h2>
      {state.error && !state.data && <Failed error={state.error} what="the dashboard figures" onRetry={state.reload} card={false} />}
      {!state.data && !state.error && <Loading what="the dashboard figures" card={false} />}
      {state.data && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="lab">The dashboard card, with today's figures</span>
          <HaPreview s={state.data} />
        </div>
      )}
      <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)" }}>
        A Home Assistant dashboard, or a widget on an iPhone, can show five figures from this app: safe to spend, days to payday, the next bill, how many things need you, and whether the plan is on track. It reads them with a key and cannot change anything.
      </p>
      <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)" }}>
        A key that sees <strong>"{SEES.aggregates}"</strong> keeps names off the dashboard: the next bill shows as "A bill" and the one-line sentence is left out. Choose it if other people see the dashboard.
      </p>
      <details style={{ fontSize: 13 }}>
        <summary style={{ cursor: "pointer", fontWeight: 700, minHeight: 32, display: "inline-flex", alignItems: "center" }}>Show the steps</summary>
        <ol style={{ margin: "6px 0 0", paddingLeft: 20, display: "flex", flexDirection: "column", gap: 8 }}>
          <li><strong>Make a key, here.</strong> Under API keys on this page, name it "Home Assistant", choose what it can see, and press Make a key. Copy the key it shows; it is shown once and starts with <code>fa_</code>.</li>
          <li><strong>On the machine that runs Home Assistant,</strong> open <code>secrets.yaml</code> (in the config folder, beside <code>configuration.yaml</code>) and add the first block below, with your key pasted in after <code>Bearer </code>.</li>
          <li>Add the second block below to <code>configuration.yaml</code>. Its address is the one this page is open at, <code>{origin}</code>. If Home Assistant reaches the app another way (by its tailnet name and port, for example), put that address in instead.</li>
          <li>Restart Home Assistant: <strong>Settings › System › Restart</strong>. Five sensors appear under <strong>Settings › Devices &amp; services › Entities</strong>; search for "Safe to spend".</li>
          <li>
            For the card above: on a dashboard, <strong>Edit dashboard › Add card › Manual</strong>, and paste this.
            <div style={{ marginTop: 6 }}><CodeBlock title="Dashboard card" text={HA_CARD} copy="Copy the card" /></div>
          </li>
          <li><strong>The iPhone widget.</strong> In Shortcuts, tap <strong>+</strong> and add <strong>Get Contents of URL</strong> with <code>{origin}/api/v1/glance</code>. Tap <strong>Show More</strong> and add a header <code>Authorization</code> whose value is <code>Bearer </code> and your key. Add <strong>Get Dictionary Value</strong> for <code>text</code>, then <strong>Speak Text</strong> or <strong>Show Result</strong>. Name it "Money" and add it to the Home Screen as a widget; "Hey Siri, Money" reads the sentence aloud. The widget needs a key that sees everything: a totals-only key is refused at that address.</li>
        </ol>
        <p style={{ margin: "8px 0 0", color: "var(--ink-2)" }}>
          If a sensor shows "unavailable", open <code>{origin}/api/v1/ha</code> in a browser on the same network as Home Assistant. Any answer, even "Sign in, or send an API key", means the address is right and the key is the problem: it was mistyped or revoked, so make a new one. No answer at all means the address or the port is wrong.
        </p>
      </details>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(320px, 100%), 1fr))", gap: 12 }}>
        <CodeBlock title="1. In secrets.yaml" text={HA_SECRET} copy="Copy for secrets.yaml" />
        <CodeBlock title="2. In configuration.yaml" text={haConfig(origin)} copy="Copy for configuration.yaml" />
      </div>
    </section>
  );
}

function ConnectedApps() {
  const state = useApi<{ clients: OAuthClientRow[] }>("/api/v1/oauth/clients");
  const { data, reload } = state;
  const [note, setNote] = useState<{ id: string; text: string; tone: "info" | "error" } | null>(null);
  async function revoke(id: string) {
    await api(`/api/v1/oauth/clients/${id}`, { method: "DELETE" });
    reload();
  }
  async function setRedaction(id: string, redaction: string) {
    setNote(null);
    try {
      await api(`/api/v1/oauth/clients/${id}`, { method: "PUT", body: JSON.stringify({ redaction }) });
      setNote({ id, text: "Saved.", tone: "info" });
      reload();
    } catch (e) {
      setNote({ id, text: (e as Error).message, tone: "error" });
    }
  }
  const rows = (data?.clients ?? []).filter((c) => !c.revoked);
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <h2 style={{ margin: 0, fontSize: 16, fontWeight: 800 }}>Connected apps</h2>
      <p style={{ margin: 0, color: "var(--ink-2)", fontSize: 13 }}>
        Apps that signed in through the consent page: claude.ai on the phone or the web, Claude Code, Open WebUI. Revoking an app signs it out everywhere.
      </p>
      <Where id="claude_connect" fill={{ url: `${window.location.origin}/mcp` }} />
      {state.error && !data && <Failed error={state.error} what="the connected apps" onRetry={reload} />}
      {!data && !state.error && <Loading what="the connected apps" />}
      {data && (
        <div className="card" style={{ padding: "6px 14px", overflowX: "auto" }}>
          <table className="ledger">
            <thead><tr><th>App</th><th>Can</th><th>What it can see</th><th>Sign-ins</th><th>Last used</th><th><span className="sr-only">Revoke</span></th></tr></thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.client_id}>
                  <td><strong>{c.name}</strong></td>
                  <td>{scopesInWords(c.scopes.split(/[\s,]+/))}</td>
                  <td>
                    <SeesSelect value={c.redaction} onChange={(v) => setRedaction(c.client_id, v)} label={`What ${c.name} can see`} />
                    {note?.id === c.client_id && <div style={{ marginTop: 4 }}><Said text={note.text} tone={note.tone} /></div>}
                  </td>
                  <td className="num">{c.sessions}</td>
                  <td style={{ color: "var(--ink-2)", whiteSpace: "nowrap" }}>{c.last_used_at ? when(c.last_used_at) : "never"}</td>
                  <td style={{ textAlign: "right" }}><ConfirmButton label="Revoke" question={`Sign ${c.name} out everywhere? It must connect again to read your plan.`} yes="Yes, revoke it" no="Keep it" onConfirm={() => revoke(c.client_id)} /></td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={6} style={{ color: "var(--ink-2)" }}>No app has signed in yet.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function Clients() {
  const state = useApi<{ keys: KeyRow[] }>("/api/v1/keys");
  const { data, reload, error } = state;
  const [name, setName] = useState("");
  const [redaction, setRedaction] = useState("full");
  const [token, setToken] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setNote(null);
    try {
      const made = await api<{ token: string }>("/api/v1/keys", { method: "POST", body: JSON.stringify({ name: name || "client", scopes: ["read"], redaction }) });
      setToken(made.token);
      setName("");
      reload();
    } catch (err) {
      setNote((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: number) {
    await api(`/api/v1/keys/${id}`, { method: "DELETE" });
    reload();
  }

  const command = token ? `claude mcp add --transport http financialauthority ${window.location.origin}/mcp --header "Authorization: Bearer ${token}"` : "";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 900 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Apps and keys</h1>
      <ConnectedApps />
      <p style={{ margin: 0, color: "var(--ink-2)", fontSize: 13 }}>
        The phones and computers signed in to this app as you are listed on <Link to="/settings/security" style={{ fontWeight: 700 }}>Settings › Sign-in and security</Link>, where you can sign any of them out.
      </p>
      <h2 style={{ margin: "8px 0 0", fontSize: 16, fontWeight: 800 }}>API keys</h2>
      <p style={{ margin: 0, color: "var(--ink-2)" }}>A key lets a script or a client without a browser read through the MCP endpoint at <code>/mcp</code> or the REST API. Keys read only; approvals stay with you.</p>
      <form onSubmit={create} className="card" style={{ padding: 14, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 200px" }}>
          <span className="lab">Name</span>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="claude-code" />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="lab">What it can see</span>
          <SeesSelect value={redaction} onChange={setRedaction} />
        </label>
        <button className="btn dark" type="submit" disabled={busy}>{busy ? "Making…" : "Make a key"}</button>
        {note && <Said text={note} tone="error" />}
      </form>
      {token && (
        <div className="card" style={{ padding: 14, borderColor: "var(--safe)", display: "flex", flexDirection: "column", gap: 8 }}>
          <div className="lab">Copy it now; it is not shown again</div>
          <code style={{ display: "block", wordBreak: "break-all", fontSize: 13 }}>{token}</code>
          <CopyButton text={token} label="Copy the key" />
          <div style={{ fontSize: 12, color: "var(--ink-2)", display: "flex", flexDirection: "column", gap: 6 }}>
            <span>For Claude Code, run this in a terminal on your computer. It already holds the whole key:</span>
            <code style={{ display: "block", wordBreak: "break-all", padding: "6px 8px", background: "var(--surface-2)", borderRadius: 6 }}>{command}</code>
            <CopyButton text={command} label="Copy the command" />
          </div>
        </div>
      )}
      {error && !data && <Failed error={error} what="your keys" onRetry={reload} />}
      {!data && !error && <Loading what="your keys" />}
      {data && (
        <div className="card" style={{ padding: "6px 14px", overflowX: "auto" }}>
          <table className="ledger">
            <thead><tr><th>Name</th><th>Starts with</th><th>Can</th><th>What it can see</th><th>Last used</th><th><span className="sr-only">Revoke</span></th></tr></thead>
            <tbody>
              {data.keys.map((k) => (
                <tr key={k.id} style={{ opacity: k.revoked ? 0.5 : 1 }}>
                  <td>{k.name}{k.revoked && <div style={{ fontSize: 11, color: "var(--ink-2)" }}>revoked</div>}</td>
                  <td><code>{k.prefix}…</code></td>
                  <td>{scopesInWords(k.scopes)}</td>
                  <td>{SEES[k.redaction] ?? k.redaction}</td>
                  <td style={{ color: "var(--ink-2)", whiteSpace: "nowrap" }}>{k.last_used_at ? when(k.last_used_at) : "never"}</td>
                  <td style={{ textAlign: "right" }}>{!k.revoked && <ConfirmButton label="Revoke" question={`Revoke ${k.name}? Anything using this key stops reading at once.`} yes="Yes, revoke it" no="Keep it" onConfirm={() => revoke(k.id)} />}</td>
                </tr>
              ))}
              {data.keys.length === 0 && <tr><td colSpan={6} style={{ color: "var(--ink-2)" }}>No keys yet.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      <HomeAssistant />
    </div>
  );
}
