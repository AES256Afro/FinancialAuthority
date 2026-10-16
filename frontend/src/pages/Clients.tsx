// API keys for models and scripts. A key is shown once; the owner keeps it in the client, never in chat.
import { Copy } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { when } from "../format";
import { ConfirmButton } from "../components/ConfirmButton";
import { Failed, Loading, Said } from "../components/State";
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
    </div>
  );
}
