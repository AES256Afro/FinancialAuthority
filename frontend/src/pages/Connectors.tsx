// Connectors: live banks through Plaid and Teller, the mailboxes, and the Rocket Money export in and out. All read-only.
import { OctagonAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { api, useApi } from "../api";
import { when } from "../format";
import type { MailAccountRow } from "../types";
import { Banks } from "./Banks";
import { FileImport } from "../components/FileImport";
import { BridgeGuide, type BridgeInfo } from "../components/BridgeGuide";
import { ConfirmButton } from "../components/ConfirmButton";
import { Failed, Loading, Said } from "../components/State";
import { Where } from "../components/Where";

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

type Note = { text: string; tone: "info" | "error" } | null;

const KIND_TITLE = { proton: "Proton Mail, through the Bridge", gmail: "Gmail, app password", imap: "Another IMAP mailbox" } as const;

function MailboxForm({ presets, bridge, onSaved }: { presets: Record<string, Preset>; bridge: BridgeInfo; onSaved: () => void }) {
  const [kind, setKind] = useState<"proton" | "gmail" | "imap">("proton");
  const [label, setLabel] = useState("Proton");
  const [host, setHost] = useState(presets.proton?.host ?? "bridge");
  const [port, setPort] = useState(String(presets.proton?.port ?? 143));
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [folders, setFolders] = useState("INBOX");
  const [result, setResult] = useState<Note>(null);
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
      setResult(r.ok ? { text: `Connected. ${r.inbox_messages} messages in INBOX; folders: ${(r.folders ?? []).slice(0, 8).join(", ")}`, tone: "info" } : { text: `Could not connect: ${r.error}`, tone: "error" });
    } catch (e) {
      setResult({ text: (e as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setResult(null);
    try {
      await api("/api/v1/mail/accounts", { method: "POST", body: JSON.stringify(payload()) });
      setPassword("");
      setResult({ text: "Saved. The password is stored encrypted; the first read starts within fifteen minutes, or press Sync now.", tone: "info" });
      onSaved();
    } catch (err) {
      setResult({ text: (err as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div role="group" aria-label="Kind of mailbox" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {(["proton", "gmail", "imap"] as const).map((k) => (
          <button key={k} type="button" aria-pressed={kind === k} className={`btn${kind === k ? " dark" : ""}`} onClick={() => pick(k)}>{KIND_TITLE[k]}</button>
        ))}
      </div>
      <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{presets[kind]?.help}</div>
      {kind === "gmail" && <Where id="gmail_app_password" />}
      {kind === "proton" && <BridgeGuide info={bridge} onDesktop={(h, p) => { setHost(h); setPort(p); }} />}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(180px, 100%), 1fr))", gap: 8 }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Name</span><input className="input" value={label} onChange={(e) => setLabel(e.target.value)} /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">IMAP host</span><input className="input" value={host} onChange={(e) => setHost(e.target.value)} required /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Port</span><input className="input num" value={port} onChange={(e) => setPort(e.target.value)} inputMode="numeric" /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Username</span><input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" required /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">{kind === "gmail" ? "App password" : kind === "proton" ? "Bridge password" : "Password"}</span><input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" required /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Folders to watch</span><input className="input" value={folders} onChange={(e) => setFolders(e.target.value)} /></label>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button type="button" className="btn" onClick={test} disabled={busy || !username || !password}>{busy ? "Working…" : "Test first"}</button>
        <button type="submit" className="btn dark" disabled={busy || !username || !password}>Save mailbox</button>
        {result && <Said text={result.text} tone={result.tone} />}
      </div>
      <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Read-only: the app never marks, moves, deletes or sends. The password never appears again after it is saved.</div>
    </form>
  );
}

const JUMPS = [
  { id: "banks", label: "Banks" },
  { id: "mail", label: "Mailboxes" },
  { id: "import", label: "Import a file" },
  { id: "rocketmoney", label: "Rocket Money" },
];

export function Connectors() {
  const connectors = useApi<{ connectors: ConnectorRow[] }>("/api/v1/connectors");
  const { data } = connectors;
  const presets = useApi<{ presets: Record<string, Preset>; bridge?: BridgeInfo }>("/api/v1/mail/presets");
  const accounts = useApi<{ accounts: MailAccountRow[] }>("/api/v1/mail/accounts");
  const [rocketNote, setRocketNote] = useState<Note>(null);
  const [mailNote, setMailNote] = useState<{ id: number; text: string; tone: "info" | "error" } | null>(null);
  const [syncing, setSyncing] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  async function upload(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const file = (form.elements.namedItem("file") as HTMLInputElement).files?.[0];
    if (!file) {
      setRocketNote({ text: "Choose the export file first.", tone: "error" });
      return;
    }
    setBusy(true);
    setRocketNote(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const r = await api<{ summary: string; accounts_created: number }>("/api/v1/imports/rocketmoney", { method: "POST", body });
      setRocketNote({ text: `${file.name}: ${r.summary}; ${r.accounts_created} new accounts`, tone: "info" });
      form.reset();
    } catch (err) {
      setRocketNote({ text: `Could not import: ${(err as Error).message}`, tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function runWatch() {
    setBusy(true);
    setRocketNote(null);
    try {
      const r = await api<{ ok?: boolean; summary: string }>("/api/v1/jobs/import_watch/run", { method: "POST" });
      setRocketNote({ text: `Import folder: ${r.summary || "nothing new"}`, tone: r.ok === false ? "error" : "info" });
    } catch (e) {
      setRocketNote({ text: (e as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function syncNow(id: number) {
    setSyncing(id);
    setMailNote(null);
    try {
      const r = await api<{ ok?: boolean; summary: string }>(`/api/v1/mail/accounts/${id}/sync`, { method: "POST" });
      setMailNote({ id, text: r.summary, tone: r.ok === false ? "error" : "info" });
      accounts.reload();
    } catch (e) {
      setMailNote({ id, text: (e as Error).message, tone: "error" });
    } finally {
      setSyncing(null);
    }
  }

  async function remove(id: number) {
    await api(`/api/v1/mail/accounts/${id}`, { method: "DELETE" });
    accounts.reload();
  }

  const jump = (id: string) => document.getElementById(id)?.scrollIntoView({ block: "start", behavior: "smooth" });
  const { hash } = useLocation();
  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [hash, data]);
  const rocket = data?.connectors.find((c) => c.id === "rocketmoney");
  const live = (accounts.data?.accounts ?? []).filter((a) => a.active);
  const sectionStyle = { padding: 16, display: "flex", flexDirection: "column", gap: 10, scrollMarginTop: 16 } as const;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 900 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Connectors</h1>
      <nav aria-label="On this page" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", fontSize: 13 }}>
        <span className="lab">Jump to</span>
        {JUMPS.filter((j) => j.id !== "rocketmoney" || rocket || !data).map((j) => (
          <Link key={j.id} to={{ hash: `#${j.id}` }} onClick={() => jump(j.id)} className="btn" style={{ minHeight: 32, padding: "4px 12px", textDecoration: "none", display: "inline-flex", alignItems: "center" }}>{j.label}</Link>
        ))}
      </nav>

      <Banks />

      <section id="mail" className="card" style={sectionStyle}>
        <div>
          <h2 style={{ margin: 0, fontSize: 14, fontWeight: 800 }}>Mailboxes</h2>
          <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Bills, statements, paystubs, installment schedules, payment confirmations, past-due and bank alerts, and the Rocket Money export, read every fifteen minutes.</div>
        </div>
        {accounts.error && !accounts.data && <Failed error={accounts.error} what="your mailboxes" onRetry={accounts.reload} card={false} />}
        {!accounts.data && !accounts.error && <Loading what="your mailboxes" card={false} />}
        {live.length > 0 && (
          <div style={{ overflowX: "auto" }}>
            <table className="ledger">
              <thead><tr><th>Mailbox</th><th>Where</th><th>Last read</th><th>Seen</th><th><span className="sr-only">Sync or remove</span></th></tr></thead>
              <tbody>
                {live.map((a) => (
                  <tr key={a.id}>
                    <td><strong>{a.label}</strong><div style={{ fontSize: 11, color: "var(--muted)" }}>{a.kind} · {a.username}</div></td>
                    <td style={{ color: "var(--ink-2)" }}>{a.host}:{a.port} · {a.folders}</td>
                    <td>
                      {a.last_error
                        ? <span style={{ display: "inline-flex", gap: 4, alignItems: "flex-start", color: "var(--critical-ink)" }}><OctagonAlert size={14} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2 }} /><span><strong>Last read failed.</strong> {a.last_error}</span></span>
                        : <span style={{ color: "var(--ink-2)", whiteSpace: "nowrap" }}>{a.last_sync_at ? when(a.last_sync_at) : "not yet"}</span>}
                    </td>
                    <td className="num">{a.messages_seen}</td>
                    <td style={{ textAlign: "right" }}>
                      <span style={{ display: "inline-flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end", alignItems: "flex-start" }}>
                        <button className="btn" type="button" disabled={syncing !== null} onClick={() => syncNow(a.id)}>{syncing === a.id ? "Reading…" : "Sync now"}</button>
                        <ConfirmButton label="Remove" question={`Stop reading ${a.label}? Its password is forgotten; mail already read stays.`} yes="Yes, remove it" no="Keep it" onConfirm={() => remove(a.id)} />
                      </span>
                      {mailNote?.id === a.id && <div style={{ marginTop: 4, textAlign: "left" }}><Said text={mailNote.text} tone={mailNote.tone} /></div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div style={{ borderTop: "1px solid var(--surface-2)", paddingTop: 10 }}>
          <h3 className="lab" style={{ margin: "0 0 8px" }}>Add a mailbox</h3>
          {presets.error && !presets.data && <Failed error={presets.error} what="the mailbox form" onRetry={presets.reload} card={false} />}
          {!presets.data && !presets.error && <Loading what="the mailbox form" card={false} />}
          {presets.data && <MailboxForm presets={presets.data.presets} bridge={presets.data.bridge ?? { container: "financialauthority-bridge", install: "", in_container: true }} onSaved={accounts.reload} />}
        </div>
      </section>

      <section id="import" className="card" style={sectionStyle}>
        <div>
          <h2 style={{ margin: 0, fontSize: 14, fontWeight: 800 }}>Import a file from any bank or vendor</h2>
          <div style={{ fontSize: 13, color: "var(--ink-2)" }}>For a bank or a vendor with no live connection: download its CSV or OFX file, or its statement PDF, and bring it here. The app works out the columns, shows the first rows, and skips what your transactions already have. A bills list (name, amount, how often, due day) lands on your bills list the same way. The same files can be dropped into the Imports folder.</div>
        </div>
        <FileImport />
      </section>

      {(rocket || !data) && (
        <section id="rocketmoney" className="card" style={sectionStyle}>
          {connectors.error && !data && <Failed error={connectors.error} what="the Rocket Money connector" onRetry={connectors.reload} card={false} />}
          {!data && !connectors.error && <Loading what="the Rocket Money connector" card={false} />}
          {rocket && (
            <>
              <div>
                <h2 style={{ margin: 0, fontSize: 14, fontWeight: 800 }}>{rocket.title}</h2>
                <div style={{ fontSize: 13, color: "var(--ink-2)" }}>{rocket.yields}</div>
              </div>
              <form onSubmit={upload} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
                <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  <span className="lab">Rocket Money export (CSV)</span>
                  <input className="input" type="file" name="file" accept=".csv,text/csv" />
                </label>
                <button className="btn dark" type="submit" disabled={busy}>{busy ? "Working…" : "Import this export"}</button>
                <button className="btn" type="button" disabled={busy} onClick={runWatch}>Check the import folder now</button>
                <a className="btn" href="/api/v1/exports/rocketmoney.csv" download="financialauthority-export.csv" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center" }}>Export your transactions as CSV</a>
                <a className="btn" href="https://app.rocketmoney.com/transactions" target="_blank" rel="noreferrer" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center" }}>Open Rocket Money's export page</a>
              </form>
              {rocketNote && <Said text={rocketNote.text} tone={rocketNote.tone} />}
              <Where id="rocketmoney_export" />
            </>
          )}
        </section>
      )}
    </div>
  );
}
