// Connectors: today the Rocket Money export, in by upload or the import folder, and out as the same CSV.
import { useState } from "react";
import { api, useApi } from "../api";

interface ConnectorRow {
  id: string;
  title: string;
  yields: string;
}

export function Connectors() {
  const { data } = useApi<{ connectors: ConnectorRow[] }>("/api/v1/connectors");
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function upload(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const file = (form.elements.namedItem("file") as HTMLInputElement).files?.[0];
    if (!file) return;
    setBusy(true);
    try {
      const body = new FormData();
      body.append("file", file);
      const r = await api<{ summary: string; accounts_created: number }>("/api/v1/imports/rocketmoney", { method: "POST", body });
      setResult(`${file.name}: ${r.summary}; ${r.accounts_created} new accounts`);
      form.reset();
    } catch (err) {
      setResult(`Could not import: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  async function runWatch() {
    const r = await api<{ summary: string }>("/api/v1/jobs/import_watch/run", { method: "POST" });
    setResult(`Import folder: ${r.summary}`);
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 820 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Connectors</h1>
      {(data?.connectors ?? []).map((c) => (
        <div key={c.id} className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
          <div>
            <strong>{c.title}</strong>
            <div style={{ fontSize: 13, color: "var(--ink-2)" }}>{c.yields}</div>
          </div>
          <form onSubmit={upload} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <input className="input" type="file" name="file" accept=".csv,text/csv" />
            <button className="btn dark" type="submit" disabled={busy}>Import this export</button>
            <button className="btn" type="button" onClick={runWatch}>Check the import folder now</button>
            <a className="btn" href="/api/v1/exports/rocketmoney.csv" download="financialauthority-export.csv" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center" }}>Export the ledger as CSV</a>
          </form>
          <div style={{ fontSize: 12, color: "var(--muted)" }}>
            In Rocket Money: Transactions › Export › all time. The link arrives by email and lives seven days. From M1.2 the app follows that link itself; until then, save the file here or into the import folder.
          </div>
        </div>
      ))}
      {result && <div className="card" style={{ padding: 12, fontSize: 13 }}>{result}</div>}
      <div className="card" style={{ padding: 16, color: "var(--ink-2)", fontSize: 13 }}>
        Live bank connectors (Plaid trial, Teller developer tier, Era Context), bank alert mail and both mailboxes arrive in M1 and M4. Nothing here will ever ask for a bank password.
      </div>
    </div>
  );
}
