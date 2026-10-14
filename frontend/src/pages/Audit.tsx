// Audit: every tool call any model or script made, with who, what, how many rows, and at what redaction.
import { useApi } from "../api";

interface Entry {
  id: number;
  at: string;
  client: string;
  client_kind: string;
  tool: string;
  rows: number;
  redaction: string;
}

interface AuditOut {
  entries: Entry[];
  week: { calls: number; by_tool: Record<string, number>; by_client: Record<string, number> };
}

export function Audit() {
  const { data, error } = useApi<AuditOut>("/api/v1/audit?limit=300");
  const tools = data ? Object.entries(data.week.by_tool).sort((a, b) => b[1] - a[1]) : [];
  const clients = data ? Object.entries(data.week.by_client).sort((a, b) => b[1] - a[1]) : [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 980 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Audit</h1>
      <p style={{ margin: 0, color: "var(--ink-2)" }}>Every read a model, an app or a script made through the tools, newest first. The owner's own screens are not logged; models and keys are.</p>
      {error && <div style={{ color: "var(--critical)" }}>{error.message}</div>}
      {data && (
        <>
          <section className="card" style={{ padding: 14, display: "flex", gap: 24, flexWrap: "wrap" }}>
            <div><div className="lab">Calls this week</div><div className="num" style={{ fontSize: 28, fontWeight: 800 }}>{data.week.calls}</div></div>
            <div style={{ fontSize: 13 }}>
              <div className="lab">By tool</div>
              {tools.length === 0 ? <div style={{ color: "var(--muted)" }}>none</div> : tools.slice(0, 8).map(([t, n]) => <div key={t}><code>{t}</code> <span className="num" style={{ color: "var(--ink-2)" }}>{n}</span></div>)}
            </div>
            <div style={{ fontSize: 13 }}>
              <div className="lab">By client</div>
              {clients.length === 0 ? <div style={{ color: "var(--muted)" }}>none</div> : clients.slice(0, 8).map(([c, n]) => <div key={c}>{c} <span className="num" style={{ color: "var(--ink-2)" }}>{n}</span></div>)}
            </div>
          </section>
          <section className="card" style={{ padding: "6px 14px", overflowX: "auto" }}>
            <table className="ledger">
              <thead><tr><th>When</th><th>Client</th><th>Tool</th><th style={{ textAlign: "right" }}>Rows</th><th>Saw</th></tr></thead>
              <tbody>
                {data.entries.map((e) => (
                  <tr key={e.id}>
                    <td style={{ color: "var(--ink-2)", whiteSpace: "nowrap" }}>{e.at.slice(0, 16).replace("T", " ")}</td>
                    <td><strong>{e.client}</strong> <span style={{ color: "var(--muted)", fontSize: 11 }}>{e.client_kind}</span></td>
                    <td><code>{e.tool}</code></td>
                    <td className="num" style={{ textAlign: "right" }}>{e.rows}</td>
                    <td style={{ color: "var(--ink-2)" }}>{e.redaction === "full" ? "full detail" : e.redaction === "merchants" ? "merchants only" : "aggregates only"}</td>
                  </tr>
                ))}
                {data.entries.length === 0 && <tr><td colSpan={5} style={{ color: "var(--muted)" }}>No calls yet.</td></tr>}
              </tbody>
            </table>
          </section>
        </>
      )}
    </div>
  );
}
