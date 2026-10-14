// Connectors: live banks through Plaid and Teller, the mailboxes, and the Rocket Money export in and out. All read-only.
import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { api, useApi } from "../api";
import type { MailAccountRow } from "../types";
import { Banks } from "./Banks";

interface ConnectorRow {
  id: string;
  title: string;
  yields: string;
}

interface Preset {
  host: string;
  port: number;
  use_ssl: boolean;
  starttls: boolean;
  help: string;
}

function MailboxForm({ presets, onSaved }: { presets: Record<string, Preset>; onSaved: () => void }) {
  const [kind, setKind] = useState<"proton" | "gmail" | "imap">("proton");
  const [label, setLabel] = useState("Proton");
  const [host, setHost] = useState(presets.proton?.host ?? "bridge");
  const [port, setPort] = useState(String(presets.proton?.port ?? 1143));
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [folders, setFolders] = useState("INBOX");
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function pick(k: "proton" | "gmail" | "imap") {
    setKind(k);
    const p = presets[k];
    if (p) {
      setHost(p.host);
      setPort(String(p.port));
    }
    setLabel(k === "imap" ? "Mail" : k === "gmail" ? "Gmail" : "Proton");
  }

  const payload = () => ({ kind, label, host, port: Number(port), username, password, folders, use_ssl: presets[kind]?.use_ssl, starttls: presets[kind]?.starttls });

  async function test() {
    setBusy(true);
    setResult(null);
    try {
      const r = await api<{ ok: boolean; folders?: string[]; inbox_messages?: number; error?: string }>("/api/v1/mail/accounts/test", { method: "POST", body: JSON.stringify(payload()) });
      setResult(r.ok ? `Connected. ${r.inbox_messages} messages in INBOX; folders: ${(r.folders ?? []).slice(0, 8).join(", ")}` : `Could not connect: ${r.error}`);
    } catch (e) {
      setResult((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api("/api/v1/mail/accounts", { method: "POST", body: JSON.stringify(payload()) });
      setPassword("");
      setResult("Saved. The password is stored encrypted; the first read starts within fifteen minutes, or press Sync now.");
      onSaved();
    } catch (err) {
      setResult((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {(["proton", "gmail", "imap"] as const).map((k) => (
          <button key={k} type="button" className={`btn${kind === k ? " dark" : ""}`} onClick={() => pick(k)}>{k === "proton" ? "Proton Mail, through the Bridge" : k === "gmail" ? "Gmail, app password" : "Another IMAP mailbox"}</button>
        ))}
      </div>
      <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{presets[kind]?.help}</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 8 }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Name</span><input className="input" value={label} onChange={(e) => setLabel(e.target.value)} /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">IMAP host</span><input className="input" value={host} onChange={(e) => setHost(e.target.value)} required /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Port</span><input className="input num" value={port} onChange={(e) => setPort(e.target.value)} inputMode="numeric" /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Username</span><input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" required /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">{kind === "gmail" ? "App password" : kind === "proton" ? "Bridge password" : "Password"}</span><input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" required /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Folders to watch</span><input className="input" value={folders} onChange={(e) => setFolders(e.target.value)} /></label>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button type="button" className="btn" onClick={test} disabled={busy || !username || !password}>Test first</button>
        <button type="submit" className="btn dark" disabled={busy || !username || !password}>Save mailbox</button>
        {result && <span style={{ fontSize: 13, color: "var(--ink-2)" }}>{result}</span>}
      </div>
      <div style={{ fontSize: 12, color: "var(--muted)" }}>Read-only: the app never marks, moves, deletes or sends. The password never appears again after it is saved.</div>
    </form>
  );
}

export function Connectors() {
  const { data } = useApi<{ connectors: ConnectorRow[] }>("/api/v1/connectors");
  const presets = useApi<{ presets: Record<string, Preset> }>("/api/v1/mail/presets");
  const accounts = useApi<{ accounts: MailAccountRow[] }>("/api/v1/mail/accounts");
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

  async function syncNow(id: number) {
    const r = await api<{ summary: string }>(`/api/v1/mail/accounts/${id}/sync`, { method: "POST" });
    setResult(r.summary);
    accounts.reload();
  }

  async function remove(id: number) {
    await api(`/api/v1/mail/accounts/${id}`, { method: "DELETE" });
    accounts.reload();
  }

  const { hash } = useLocation();
  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [hash, data]);
  const rocket = data?.connectors.find((c) => c.id === "rocketmoney");
  const live = (accounts.data?.accounts ?? []).filter((a) => a.active);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 900 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Connectors</h1>

      <Banks />

      <section id="mail" className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
        <div>
          <strong>Mailboxes</strong>
          <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Bills, statements, paystubs, installment schedules, payment confirmations, past-due and bank alerts, and the Rocket Money export, read every fifteen minutes.</div>
        </div>
        {live.length > 0 && (
          <div style={{ overflowX: "auto" }}>
            <table className="ledger">
              <thead><tr><th>Mailbox</th><th>Where</th><th>Last read</th><th>Seen</th><th></th></tr></thead>
              <tbody>
                {live.map((a) => (
                  <tr key={a.id}>
                    <td><strong>{a.label}</strong><div style={{ fontSize: 11, color: "var(--muted)" }}>{a.kind} · {a.username}</div></td>
                    <td style={{ color: "var(--ink-2)" }}>{a.host}:{a.port} · {a.folders}</td>
                    <td style={{ color: a.last_error ? "var(--critical)" : "var(--ink-2)" }}>{a.last_error ? a.last_error : a.last_sync_at ? a.last_sync_at.slice(0, 16).replace("T", " ") : "not yet"}</td>
                    <td className="num">{a.messages_seen}</td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}><button className="btn" type="button" onClick={() => syncNow(a.id)}>Sync now</button> <button className="btn" type="button" onClick={() => remove(a.id)}>Remove</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div style={{ borderTop: "1px solid var(--surface-2)", paddingTop: 10 }}>
          <div className="lab" style={{ marginBottom: 8 }}>Add a mailbox</div>
          {presets.data && <MailboxForm presets={presets.data.presets} onSaved={accounts.reload} />}
        </div>
      </section>

      {rocket && (
        <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
          <div>
            <strong>{rocket.title}</strong>
            <div style={{ fontSize: 13, color: "var(--ink-2)" }}>{rocket.yields}</div>
          </div>
          <form onSubmit={upload} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <input className="input" type="file" name="file" accept=".csv,text/csv" />
            <button className="btn dark" type="submit" disabled={busy}>Import this export</button>
            <button className="btn" type="button" onClick={runWatch}>Check the import folder now</button>
            <a className="btn" href="/api/v1/exports/rocketmoney.csv" download="financialauthority-export.csv" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center" }}>Export the ledger as CSV</a>
            <a className="btn" href="https://app.rocketmoney.com/transactions" target="_blank" rel="noreferrer" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center" }}>Open Rocket Money's export page</a>
          </form>
          <div style={{ fontSize: 12, color: "var(--muted)" }}>
            In Rocket Money: Transactions › Export › all time. The link arrives by email and lives seven days; with a mailbox connected, the app follows it and imports the file by itself. A file can also be dropped into the import folder.
          </div>
        </section>
      )}
      {result && <div className="card" style={{ padding: 12, fontSize: 13 }}>{result}</div>}
    </div>
  );
}
