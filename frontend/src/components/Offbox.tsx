// Backups that prove themselves: the weekly restore drill's verdict, the sealed copy kept off the data disk, and
// the recovery kit that says what to keep and how to bring everything back on a new server.
import { AlertTriangle, CheckCircle2, ShieldCheck } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { api, useApi } from "../api";

interface Drill { at: string; ok: boolean; detail: string; point?: string }
interface Offbox { folder: string; passphrase_set: boolean; every_days: number; last_at: string | null; last_file: string; last_error: string; copies: string[]; folder_ok: boolean; suggested: string; drill: Drill | null }

function when(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function Verdict({ ok, children }: { ok: boolean; children: ReactNode }) {
  return (
    <div style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 13, color: ok ? "var(--safe)" : "var(--critical)" }}>
      {ok ? <CheckCircle2 size={16} aria-hidden style={{ flexShrink: 0, marginTop: 1 }} /> : <AlertTriangle size={16} aria-hidden style={{ flexShrink: 0, marginTop: 1 }} />}
      <span style={{ color: "var(--ink)" }}>{children}</span>
    </div>
  );
}

export function DrillCard({ onRun }: { onRun?: () => void }) {
  const { data, reload } = useApi<Offbox>("/api/v1/restore-points/offbox");
  const [busy, setBusy] = useState(false);
  const d = data?.drill ?? null;
  return (
    <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
      <strong style={{ fontSize: 15, display: "inline-flex", alignItems: "center", gap: 6 }}><ShieldCheck size={18} aria-hidden />Proved, not just kept</strong>
      <span style={{ fontSize: 13, color: "var(--ink-2)" }}>Once a week the app opens its newest copy on its own, has SQLite check every page, and compares what it holds with what was counted when it was made.</span>
      {d ? <Verdict ok={d.ok}><strong>{d.ok ? "Passed" : "Failed"}</strong> {when(d.at)}. {d.detail}</Verdict> : <span style={{ fontSize: 13, color: "var(--muted)" }}>No drill yet; the first runs within six hours, or now.</span>}
      <button type="button" className="btn" style={{ alignSelf: "flex-start" }} disabled={busy} onClick={async () => { setBusy(true); try { await api("/api/v1/restore-points/drill", { method: "POST" }); reload(); onRun?.(); } finally { setBusy(false); } }}>{busy ? "Testing…" : "Run the drill now"}</button>
    </section>
  );
}

export function OffboxCard() {
  const { data, reload } = useApi<Offbox>("/api/v1/restore-points/offbox");
  const [folder, setFolder] = useState("");
  const [pass, setPass] = useState("");
  const [every, setEvery] = useState(1);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (data) {
      setFolder(data.folder || data.suggested || "");
      setEvery(data.every_days || 1);
    }
  }, [data]);
  if (!data) return null;
  async function run(fn: () => Promise<string>) {
    setBusy(true);
    setNote(null);
    try {
      setNote({ ok: true, text: await fn() });
      reload();
    } catch (e) {
      setNote({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
      <strong style={{ fontSize: 15 }}>A sealed copy off this disk</strong>
      <span style={{ fontSize: 13, color: "var(--ink-2)", maxWidth: "80ch" }}>
        If the disk fails, the copies above go with it. The app can seal a copy, daily or weekly, into a folder on another disk: a USB drive the server mounts, or a folder another program syncs to a cloud (Syncthing, rclone, a NAS). Each copy is locked with a passphrase only you know; without it the file cannot be opened, by anyone. On BoxPilot the folder is <code>/offbox</code>, chosen on the app's card.
      </span>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(220px, 100%), 1fr))", gap: 8 }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Folder, as the server sees it</span><input className="input" value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="/offbox" /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Passphrase · {data.passphrase_set ? "set" : "not set"}</span><input className="input" type="password" autoComplete="new-password" value={pass} onChange={(e) => setPass(e.target.value)} placeholder={data.passphrase_set ? "Leave empty to keep it" : "four or five words you will keep"} /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">How often</span><select className="input" value={every} onChange={(e) => setEvery(Number(e.target.value))}><option value={1}>every day</option><option value={7}>every week</option></select></label>
      </div>
      <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Write the passphrase down or keep it in a password manager. It is not your sign-in password and it is not shown again.</span>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button type="button" className="btn dark" disabled={busy} onClick={() => run(async () => { await api("/api/v1/restore-points/offbox", { method: "PUT", body: JSON.stringify({ folder, every_days: every, ...(pass ? { passphrase: pass } : {}) }) }); setPass(""); return "Saved."; })}>Save</button>
        <button type="button" className="btn" disabled={busy || !data.passphrase_set || !data.folder} onClick={() => run(async () => { const r = await api<{ made: { file: string; bytes: number } }>("/api/v1/restore-points/offbox/copy", { method: "POST" }); return `Sealed ${r.made.file} (${Math.round(r.made.bytes / 1024)} KB).`; })}>Make a sealed copy now</button>
      </div>
      {note && <Verdict ok={note.ok}>{note.text}</Verdict>}
      {data.folder && !data.folder_ok && <Verdict ok={false}>The server cannot write to {data.folder}. Mount the drive or the synced folder there (on BoxPilot: the app's card, the off-box folder).</Verdict>}
      {data.last_error && <Verdict ok={false}>The last copy failed: {data.last_error}</Verdict>}
      {data.last_at && !data.last_error && <Verdict ok>Last sealed copy {when(data.last_at)}: {data.last_file}. {data.copies.length} kept in the folder.</Verdict>}
    </section>
  );
}

export function RecoveryKit() {
  return (
    <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
        <strong style={{ fontSize: 15 }}>Recovery kit</strong>
        <button type="button" className="btn" style={{ marginLeft: "auto" }} onClick={() => window.print()}>Print this page</button>
      </div>
      <span style={{ fontSize: 13, color: "var(--ink-2)" }}>What to keep somewhere other than this server, and how to bring everything back on a new one.</span>
      <ol style={{ margin: 0, paddingLeft: 18, fontSize: 13, display: "flex", flexDirection: "column", gap: 6 }}>
        <li><strong>Keep three things:</strong> the off-box passphrase; <code>FA_DATA_KEY</code> (on BoxPilot it is on the app's card; with Docker Compose it is in the <code>.env</code> file); and where the sealed copies are.</li>
        <li><strong>On the new server,</strong> install FinancialAuthority the same way (docs/SETUP.md), with the same <code>FA_DATA_KEY</code>, and stop it.</li>
        <li><strong>Open the newest sealed copy</strong> in the app's folder on the server; it asks for the passphrase:
          <code style={{ display: "block", marginTop: 4, padding: "6px 8px", background: "var(--surface-2)", borderRadius: 6, overflowX: "auto", whiteSpace: "nowrap" }}>docker compose run --rm financialauthority /app/backend/.venv/bin/python -m app.offbox open /offbox/financialauthority-YYYYMMDD-HHMMSS.fabackup /data/financialauthority.db</code>
        </li>
        <li><strong>Start the app.</strong> Everything is back as it was at that copy. Without <code>FA_DATA_KEY</code> the ledger still comes back, but pasted passwords and keys must be pasted again.</li>
      </ol>
    </section>
  );
}
