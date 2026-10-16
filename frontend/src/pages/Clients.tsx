// API keys for models and scripts. A key is shown once; the owner keeps it in the client, never in chat.
import { useState } from "react";
import { api, useApi } from "../api";
import { Where } from "../components/Where";
import { Devices } from "../components/Devices";

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

function ConnectedApps() {
  const { data, reload } = useApi<{ clients: OAuthClientRow[] }>("/api/v1/oauth/clients");
  async function revoke(id: string) {
    await api(`/api/v1/oauth/clients/${id}`, { method: "DELETE" });
    reload();
  }
  async function setRedaction(id: string, redaction: string) {
    await api(`/api/v1/oauth/clients/${id}`, { method: "PUT", body: JSON.stringify({ redaction }) });
    reload();
  }
  const rows = (data?.clients ?? []).filter((c) => !c.revoked);
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <h2 style={{ margin: 0, fontSize: 16, fontWeight: 800 }}>Connected apps</h2>
      <p style={{ margin: 0, color: "var(--ink-2)", fontSize: 13 }}>
        Apps that signed in through the consent page: claude.ai on the phone or the web, Claude Code, Open WebUI. Revoking an app signs it out everywhere.
      </p>
      <Where id="claude_connect" fill={{ url: `${window.location.origin}/mcp` }} />
      <div className="card" style={{ padding: "6px 14px", overflowX: "auto" }}>
        <table className="ledger">
          <thead><tr><th>App</th><th>Scopes</th><th>Sees</th><th>Signed in</th><th>Last used</th><th></th></tr></thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.client_id}>
                <td><strong>{c.name}</strong></td>
                <td>{c.scopes}</td>
                <td>
                  <select className="input" style={{ minHeight: 32, padding: "4px 8px" }} value={c.redaction} onChange={(e) => setRedaction(c.client_id, e.target.value)} aria-label={`What ${c.name} sees`}>
                    <option value="full">Full detail</option>
                    <option value="merchants">Merchants only</option>
                    <option value="aggregates">Aggregates only</option>
                  </select>
                </td>
                <td className="num">{c.sessions}</td>
                <td style={{ color: "var(--ink-2)" }}>{c.last_used_at ? c.last_used_at.slice(0, 16).replace("T", " ") : "never"}</td>
                <td style={{ textAlign: "right" }}><button className="btn" type="button" onClick={() => revoke(c.client_id)}>Revoke</button></td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={6} style={{ color: "var(--muted)" }}>No app has signed in yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function Clients() {
  const { data, reload, error } = useApi<{ keys: KeyRow[] }>("/api/v1/keys");
  const [name, setName] = useState("");
  const [redaction, setRedaction] = useState("full");
  const [token, setToken] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const made = await api<{ token: string }>("/api/v1/keys", { method: "POST", body: JSON.stringify({ name: name || "client", scopes: ["read"], redaction }) });
      setToken(made.token);
      setName("");
      reload();
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: number) {
    await api(`/api/v1/keys/${id}`, { method: "DELETE" });
    reload();
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 900 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Clients</h1>
      <ConnectedApps />
      <Devices />
      <h2 style={{ margin: "8px 0 0", fontSize: 16, fontWeight: 800 }}>API keys</h2>
      <p style={{ margin: 0, color: "var(--ink-2)" }}>A key lets a script or a client without a browser read through the MCP endpoint at <code>/mcp</code> or the REST API. Keys read only; approvals stay with you.</p>
      <form onSubmit={create} className="card" style={{ padding: 14, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 200px" }}>
          <span className="lab">Name</span>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="claude-code" />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="lab">Redaction</span>
          <select className="input" value={redaction} onChange={(e) => setRedaction(e.target.value)}>
            <option value="full">Full detail</option>
            <option value="merchants">Merchants only</option>
            <option value="aggregates">Aggregates only</option>
          </select>
        </label>
        <button className="btn dark" type="submit" disabled={busy}>Make a key</button>
      </form>
      {token && (
        <div className="card" style={{ padding: 14, borderColor: "var(--safe)" }}>
          <div className="lab">Copy it now; it is not shown again</div>
          <code style={{ display: "block", wordBreak: "break-all", marginTop: 6, fontSize: 13 }}>{token}</code>
          <div style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 8 }}>Claude Code: <code>claude mcp add --transport http financialauthority {window.location.origin}/mcp --header "Authorization: Bearer {token.slice(0, 10)}…"</code></div>
        </div>
      )}
      {error && <div style={{ color: "var(--critical)" }}>{error.message}</div>}
      <div className="card" style={{ padding: "6px 14px", overflowX: "auto" }}>
        <table className="ledger">
          <thead><tr><th>Name</th><th>Prefix</th><th>Scopes</th><th>Redaction</th><th>Last used</th><th></th></tr></thead>
          <tbody>
            {(data?.keys ?? []).map((k) => (
              <tr key={k.id} style={{ opacity: k.revoked ? 0.5 : 1 }}>
                <td>{k.name}</td>
                <td><code>{k.prefix}…</code></td>
                <td>{k.scopes.join(", ")}</td>
                <td>{k.redaction}</td>
                <td style={{ color: "var(--ink-2)" }}>{k.last_used_at ? k.last_used_at.slice(0, 16).replace("T", " ") : "never"}</td>
                <td style={{ textAlign: "right" }}>{!k.revoked && <button className="btn" type="button" onClick={() => revoke(k.id)}>Revoke</button>}</td>
              </tr>
            ))}
            {data && data.keys.length === 0 && <tr><td colSpan={6} style={{ color: "var(--muted)" }}>No keys yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
