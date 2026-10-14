// API keys for models and scripts. A key is shown once; the owner keeps it in the client, never in chat.
import { useState } from "react";
import { api, useApi } from "../api";

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
      <p style={{ margin: 0, color: "var(--ink-2)" }}>A key lets a model or a script read through the MCP endpoint at <code>/mcp</code> or the REST API. Keys read only; approvals stay with you. The OAuth sign-in for claude.ai arrives in M3.</p>
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
