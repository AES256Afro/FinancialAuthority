// Settings › Backups: the app's own copies of the whole database on a time line, each import with an undo, and
// putting everything back to a copy. A copy of the moment before is always kept, so a put-back can be undone too.
import { AlertTriangle, CheckCircle2, RotateCcw, Undo2 } from "lucide-react";
import { type ReactElement, useState } from "react";
import { api, useApi } from "../api";

interface Point { name: string; kind: string; reason: string; created_at: string; bytes: number; verified: boolean; transactions: number | null }
interface Status { count: number; bytes: number; latest_age_hours: number | null; database: { ok: boolean; result: string; bytes: number }; keep: Record<string, number>; last_check: { at: string; by: string; database: string; points_ok: boolean } | null }
interface Batch { id: number; kind: string; source_file: string; via: string; account: string | null; inserted: number; duplicates: number; merged: number; summary: string; status: string; created_at: string; undone_at: string | null; restore_point: string | null }

const KIND_WORD: Record<string, string> = { import: "Before an import", daily: "Daily", manual: "By hand", "before-restore": "Before a put-back" };
const VIA_WORD: Record<string, string> = { upload: "uploaded", folder: "from the import folder", mail: "from mail" };

function when(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function size(bytes: number): string {
  return bytes > 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1e3))} KB`;
}

// The copies on one line from the oldest to now, each a mark shaped by its kind, so the gaps are seen at a glance.
function Line({ points }: { points: Point[] }) {
  const W = 760;
  const H = 86;
  const now = Date.now();
  const times = points.map((p) => new Date(p.created_at).getTime()).filter((t) => !Number.isNaN(t));
  const start = Math.min(now - 2 * 86400000, ...times);
  const x = (t: number) => 24 + ((t - start) / Math.max(1, now - start)) * (W - 48);
  const shape: Record<string, (cx: number) => ReactElement> = {
    daily: (cx) => <circle cx={cx} cy={38} r={7} fill="var(--paycheck)" stroke="var(--surface)" strokeWidth={2} />,
    import: (cx) => <rect x={cx - 6} y={32} width={12} height={12} rx={3} fill="var(--bill)" stroke="var(--surface)" strokeWidth={2} />,
    manual: (cx) => <path d={`M${cx} 30 L${cx + 7} 44 L${cx - 7} 44 Z`} fill="var(--ink-2)" stroke="var(--surface)" strokeWidth={2} />,
    "before-restore": (cx) => <rect x={cx - 6} y={32} width={12} height={12} transform={`rotate(45 ${cx} 38)`} fill="var(--muted)" stroke="var(--surface)" strokeWidth={2} />,
  };
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={`${points.length} copies of the database from ${when(new Date(start).toISOString())} to now`} style={{ display: "block", maxWidth: 900 }}>
      <line x1={24} x2={W - 24} y1={38} y2={38} stroke="var(--hairline)" strokeWidth={4} strokeLinecap="round" />
      {points.map((p) => {
        const t = new Date(p.created_at).getTime();
        return Number.isNaN(t) ? null : <g key={p.name}><title>{`${KIND_WORD[p.kind] ?? p.kind}: ${when(p.created_at)}`}</title>{(shape[p.kind] ?? shape.manual)(x(t))}</g>;
      })}
      <text x={24} y={72} fontSize={11} fill="var(--muted)">{when(new Date(start).toISOString())}</text>
      <text x={W - 24} y={72} fontSize={11} fill="var(--muted)" textAnchor="end">now</text>
    </svg>
  );
}

export function Backups() {
  const points = useApi<{ points: Point[]; status: Status }>("/api/v1/restore-points");
  const imports = useApi<{ imports: Batch[] }>("/api/v1/imports/history");
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function act(run: () => Promise<string>) {
    setBusy(true);
    setNote(null);
    try {
      setNote({ ok: true, text: await run() });
    } catch (e) {
      setNote({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
      points.reload();
      imports.reload();
    }
  }
  const takeNow = () => act(async () => {
    const r = await api<{ made: Point }>("/api/v1/restore-points", { method: "POST" });
    return `A copy was taken at ${when(r.made.created_at)} and read back without a fault.`;
  });
  const undo = (b: Batch) => act(async () => {
    const r = await api<{ removed: number; bills_removed: number; bills_restored: number; accounts_closed: number }>(`/api/v1/imports/${b.id}/undo`, { method: "POST" });
    const parts = [r.removed && `${r.removed} transactions removed`, r.bills_removed && `${r.bills_removed} bills taken off`, r.bills_restored && `${r.bills_restored} bills put back as they were`, r.accounts_closed && `${r.accounts_closed} empty account closed`].filter(Boolean);
    return `${b.source_file} is undone: ${parts.join(", ") || "nothing was left to remove"}. Everything since stays.`;
  });
  const putBack = (p: Point) => act(async () => {
    await api(`/api/v1/restore-points/${p.name}/restore`, { method: "POST" });
    setConfirming(null);
    return `Everything is back as it was at ${when(p.created_at)}. The moment before is kept as a copy, so this can be undone the same way.`;
  });

  const s = points.data?.status;
  const list = points.data?.points ?? [];
  const sinceCount = (p: Point) => (imports.data?.imports ?? []).filter((b) => b.status === "kept" && b.created_at > p.created_at).length;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 960 }}>
      <div>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Backups</h1>
        <p style={{ margin: "4px 0 0", color: "var(--ink-2)", maxWidth: "80ch" }}>
          The app keeps its own copies of the whole database: one before every import, one a day, and any you take. Each copy is read back before it counts.
          An import can be undone on its own, leaving everything since in place; a copy puts everything back to its moment. On BoxPilot, the app's data is also archived with the server's own backups.
        </p>
      </div>

      <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
        {s && (
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", fontSize: 14 }}>
            {s.database.ok && s.latest_age_hours !== null && s.latest_age_hours <= 48
              ? <span style={{ display: "inline-flex", gap: 6, alignItems: "center", color: "var(--safe)" }}><CheckCircle2 size={18} aria-hidden /><strong>Protected.</strong></span>
              : <span style={{ display: "inline-flex", gap: 6, alignItems: "center", color: "var(--warning)" }}><AlertTriangle size={18} aria-hidden /><strong>Needs a look.</strong></span>}
            <span style={{ color: "var(--ink-2)" }}>
              {s.count} {s.count === 1 ? "copy" : "copies"}, {size(s.bytes)} in all{s.latest_age_hours !== null ? `; the newest is ${s.latest_age_hours < 1 ? "under an hour" : `${Math.round(s.latest_age_hours)} hours`} old` : "; none yet"}.
              The database {s.database.ok ? "passes its check" : `did not pass its check (${s.database.result})`}.
              {s.last_check ? ` Last checked by ${s.last_check.by} ${when(s.last_check.at)}: ${s.last_check.database === "ok" && s.last_check.points_ok ? "all read back" : "something did not read back"}.` : ""}
            </span>
            <button className="btn dark" type="button" style={{ marginLeft: "auto" }} disabled={busy} onClick={takeNow}>{busy ? "Working…" : "Take a copy now"}</button>
          </div>
        )}
        <Line points={list} />
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12, color: "var(--ink-2)" }}>
          <span>● Daily</span><span>■ Before an import</span><span>▲ By hand</span><span>◆ Before a put-back</span>
        </div>
        {note && (
          <div style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 13, color: note.ok ? "var(--safe)" : "var(--critical)" }}>
            {note.ok ? <CheckCircle2 size={16} aria-hidden style={{ flexShrink: 0, marginTop: 1 }} /> : <AlertTriangle size={16} aria-hidden style={{ flexShrink: 0, marginTop: 1 }} />}
            <span><strong>{note.ok ? "Done." : "Not done."}</strong> {note.text}</span>
          </div>
        )}
      </section>

      <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
        <strong style={{ fontSize: 15 }}>Imports</strong>
        <span style={{ fontSize: 13, color: "var(--ink-2)" }}>Undo removes exactly the rows an import added and puts back the bills it changed. What came in since stays.</span>
        <div style={{ overflowX: "auto" }}>
          <table className="ledger">
            <thead><tr><th>When</th><th>File</th><th>What it did</th><th /></tr></thead>
            <tbody>
              {(imports.data?.imports ?? []).map((b) => (
                <tr key={b.id} style={{ opacity: b.status === "undone" ? 0.55 : 1 }}>
                  <td style={{ whiteSpace: "nowrap" }}>{when(b.created_at)}</td>
                  <td>{b.source_file}<div style={{ fontSize: 11, color: "var(--muted)" }}>{VIA_WORD[b.via] ?? b.via}{b.account ? ` · into ${b.account}` : ""}</div></td>
                  <td style={{ fontSize: 13 }}>{b.summary}</td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {b.status === "undone"
                      ? <span style={{ fontSize: 12, color: "var(--muted)" }}>undone {b.undone_at ? when(b.undone_at) : ""}</span>
                      : <button className="btn" type="button" disabled={busy} onClick={() => undo(b)} style={{ display: "inline-flex", gap: 4, alignItems: "center" }}><Undo2 size={14} aria-hidden />Undo</button>}
                  </td>
                </tr>
              ))}
              {(imports.data?.imports ?? []).length === 0 && <tr><td colSpan={4} style={{ color: "var(--muted)" }}>No imports yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
        <strong style={{ fontSize: 15 }}>Copies of the whole database</strong>
        <span style={{ fontSize: 13, color: "var(--ink-2)" }}>Putting one back replaces everything with that moment: imports, mail, bills and settings after it are gone from the app, kept only in the copy taken just before.</span>
        <div style={{ overflowX: "auto" }}>
          <table className="ledger">
            <thead><tr><th>When</th><th>Why</th><th style={{ textAlign: "right" }}>Size</th><th /></tr></thead>
            <tbody>
              {list.map((p) => (
                <tr key={p.name}>
                  <td style={{ whiteSpace: "nowrap" }}>{when(p.created_at)}</td>
                  <td>{KIND_WORD[p.kind] ?? p.kind}<div style={{ fontSize: 11, color: "var(--muted)" }}>{p.reason}{p.verified ? " · read back" : ""}</div></td>
                  <td className="num" style={{ textAlign: "right" }}>{size(p.bytes)}</td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {confirming === p.name ? (
                      <span style={{ display: "inline-flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                        <span style={{ fontSize: 12 }}>{sinceCount(p) ? `${sinceCount(p)} import${sinceCount(p) === 1 ? "" : "s"} and everything else since go.` : "Everything since goes."}</span>
                        <button className="btn dark" type="button" disabled={busy} onClick={() => putBack(p)}>Put it back</button>
                        <button className="btn" type="button" onClick={() => setConfirming(null)}>Keep things as they are</button>
                      </span>
                    ) : (
                      <button className="btn" type="button" disabled={busy} onClick={() => setConfirming(p.name)} style={{ display: "inline-flex", gap: 4, alignItems: "center" }}><RotateCcw size={14} aria-hidden />Put everything back to this</button>
                    )}
                  </td>
                </tr>
              ))}
              {list.length === 0 && <tr><td colSpan={4} style={{ color: "var(--muted)" }}>No copies yet; the first is taken tonight, or now with the button above.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
