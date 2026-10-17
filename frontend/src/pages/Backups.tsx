// Settings › Backups: the app's own copies of the whole database on a time line, each import with an undo, and
// putting everything back to a copy. A copy of the moment before is always kept, so a put-back can be undone too.
import { type ReactElement, useRef, useState } from "react";
import { api, useApi } from "../api";
import { when } from "../format";
import { ConfirmButton } from "../components/ConfirmButton";
import { DrillCard, OffboxCard, RecoveryKit } from "../components/Offbox";
import { Failed, Loading, Said, useWidth } from "../components/State";
import { StatusPill } from "../components/Status";

interface Point { name: string; kind: string; reason: string; created_at: string; bytes: number; verified: boolean; transactions: number | null }
interface Status { count: number; bytes: number; latest_age_hours: number | null; database: { ok: boolean; result: string; bytes: number }; keep: Record<string, number>; last_check: { at: string; by: string; database: string; points_ok: boolean } | null }
interface Batch { id: number; kind: string; source_file: string; via: string; account: string | null; inserted: number; duplicates: number; merged: number; summary: string; status: string; created_at: string; undone_at: string | null; restore_point: string | null }

const KIND_WORD: Record<string, string> = { import: "Before an import", daily: "Daily", manual: "By hand", "before-restore": "Before a put-back" };
const VIA_WORD: Record<string, string> = { upload: "uploaded", folder: "from the import folder", mail: "from mail" };

// Where a result is said: beside the button that caused it.
type Area = "take" | "imports" | "copies";

function size(bytes: number): string {
  return bytes > 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1e3))} KB`;
}

// The copies on one line from the oldest to now, each a mark shaped by its kind, so the gaps are seen at a glance.
// One drawing unit is one pixel, so the dates stay readable on a phone.
function Line({ points }: { points: Point[] }) {
  const box = useRef<HTMLDivElement>(null);
  const W = Math.max(260, Math.min(900, useWidth(box, 760)));
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
    <div ref={box} style={{ width: "100%", maxWidth: 900 }}>
    <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={`${points.length} copies of the database from ${when(new Date(start).toISOString())} to now`} style={{ display: "block", maxWidth: "100%" }}>
      <line x1={24} x2={W - 24} y1={38} y2={38} stroke="var(--hairline)" strokeWidth={4} strokeLinecap="round" />
      {points.map((p) => {
        const t = new Date(p.created_at).getTime();
        return Number.isNaN(t) ? null : <g key={p.name}><title>{`${KIND_WORD[p.kind] ?? p.kind}: ${when(p.created_at)}`}</title>{(shape[p.kind] ?? shape.manual)(x(t))}</g>;
      })}
      <text x={24} y={72} fontSize={11} fill="var(--muted)">{when(new Date(start).toISOString())}</text>
      <text x={W - 24} y={72} fontSize={11} fill="var(--muted)" textAnchor="end">now</text>
    </svg>
    </div>
  );
}

export function Backups() {
  const points = useApi<{ points: Point[]; status: Status }>("/api/v1/restore-points");
  const imports = useApi<{ imports: Batch[] }>("/api/v1/imports/history");
  const [note, setNote] = useState<{ at: Area; ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  function reloadAll() {
    points.reload();
    imports.reload();
  }
  async function takeNow() {
    setBusy(true);
    setNote(null);
    try {
      const r = await api<{ made: Point }>("/api/v1/restore-points", { method: "POST" });
      setNote({ at: "take", ok: true, text: `A copy was taken at ${when(r.made.created_at)} and read back without a fault.` });
    } catch (e) {
      setNote({ at: "take", ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
      reloadAll();
    }
  }
  // Undo and put-back are asked first by ConfirmButton, which also says a failure beside the button.
  async function undo(b: Batch) {
    setNote(null);
    const r = await api<{ removed: number; bills_removed: number; bills_restored: number; accounts_closed: number }>(`/api/v1/imports/${b.id}/undo`, { method: "POST" });
    const parts = [r.removed && `${r.removed} transactions removed`, r.bills_removed && `${r.bills_removed} bills taken off`, r.bills_restored && `${r.bills_restored} bills put back as they were`, r.accounts_closed && `${r.accounts_closed} empty account closed`].filter(Boolean);
    setNote({ at: "imports", ok: true, text: `${b.source_file} is undone: ${parts.join(", ") || "nothing was left to remove"}. Everything since stays.` });
    reloadAll();
  }
  async function putBack(p: Point) {
    setNote(null);
    await api(`/api/v1/restore-points/${p.name}/restore`, { method: "POST" });
    setNote({ at: "copies", ok: true, text: `Everything is back as it was at ${when(p.created_at)}. The moment before is kept as a copy, so this can be undone the same way.` });
    reloadAll();
  }
  const said = (at: Area) => (note?.at === at ? <Said text={`${note.ok ? "Done." : "Not done."} ${note.text}`} tone={note.ok ? "info" : "error"} /> : null);

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
        <h2 className="sr-only">Where the copies stand</h2>
        {points.error && !points.data && <Failed error={points.error} what="the copies" onRetry={points.reload} card={false} />}
        {!points.data && !points.error && <Loading what="the copies" card={false} />}
        {s && (
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", fontSize: 14 }}>
            {s.database.ok && s.latest_age_hours !== null && s.latest_age_hours <= 48
              ? <StatusPill status="good">Protected</StatusPill>
              : <StatusPill status="warn">Needs a look</StatusPill>}
            <span style={{ color: "var(--ink-2)", flex: "1 1 300px" }}>
              {s.count} {s.count === 1 ? "copy" : "copies"}, {size(s.bytes)} in all{s.latest_age_hours !== null ? `; the newest is ${s.latest_age_hours < 1 ? "under an hour" : `${Math.round(s.latest_age_hours)} hours`} old` : "; none yet"}.
              The database {s.database.ok ? "passes its check" : `did not pass its check (${s.database.result})`}.
              {s.last_check ? ` Last checked by ${s.last_check.by} ${when(s.last_check.at)}: ${s.last_check.database === "ok" && s.last_check.points_ok ? "all read back" : "something did not read back"}.` : ""}
            </span>
            <button className="btn dark" type="button" style={{ marginLeft: "auto" }} disabled={busy} onClick={takeNow}>{busy ? "Working…" : "Take a copy now"}</button>
          </div>
        )}
        {said("take")}
        <Line points={list} />
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12, color: "var(--ink-2)" }}>
          <span>● Daily</span><span>■ Before an import</span><span>▲ By hand</span><span>◆ Before a put-back</span>
        </div>
      </section>

      <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
        <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800 }}>Imports</h2>
        <span style={{ fontSize: 13, color: "var(--ink-2)" }}>Undo removes exactly the rows an import added and puts back the bills it changed. What came in since stays.</span>
        {said("imports")}
        {imports.error && !imports.data && <Failed error={imports.error} what="the imports" onRetry={imports.reload} card={false} />}
        {!imports.data && !imports.error && <Loading what="the imports" card={false} />}
        {imports.data && (
          <div style={{ overflowX: "auto" }}>
            <table className="ledger">
              <thead><tr><th>When</th><th>File</th><th>What it did</th><th><span className="sr-only">Undo</span></th></tr></thead>
              <tbody>
                {imports.data.imports.map((b) => (
                  <tr key={b.id} style={{ opacity: b.status === "undone" ? 0.55 : 1 }}>
                    <td style={{ whiteSpace: "nowrap" }}>{when(b.created_at)}</td>
                    <td>{b.source_file}<div style={{ fontSize: 11, color: "var(--muted)" }}>{VIA_WORD[b.via] ?? b.via}{b.account ? ` · into ${b.account}` : ""}</div></td>
                    <td style={{ fontSize: 13 }}>{b.summary}</td>
                    <td>
                      {b.status === "undone"
                        ? <span style={{ fontSize: 12, color: "var(--ink-2)", whiteSpace: "nowrap" }}>undone {b.undone_at ? when(b.undone_at) : ""}</span>
                        : <ConfirmButton label="Undo" question={`Take out every row ${b.source_file} added? Everything since stays.`} yes="Yes, undo it" no="Keep it" onConfirm={() => undo(b)} />}
                    </td>
                  </tr>
                ))}
                {imports.data.imports.length === 0 && <tr><td colSpan={4} style={{ color: "var(--ink-2)" }}>No imports yet.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
        <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800 }}>Copies of the whole database</h2>
        <span style={{ fontSize: 13, color: "var(--ink-2)" }}>Putting one back replaces everything with that moment: imports, mail, bills and settings after it are gone from the app, kept only in the copy taken just before.</span>
        {said("copies")}
        <div style={{ overflowX: "auto" }}>
          <table className="ledger">
            <thead><tr><th>When</th><th>Why</th><th style={{ textAlign: "right" }}>Size</th><th><span className="sr-only">Put back</span></th></tr></thead>
            <tbody>
              {list.map((p) => {
                const since = sinceCount(p);
                return (
                  <tr key={p.name}>
                    <td style={{ whiteSpace: "nowrap" }}>{when(p.created_at)}</td>
                    <td>{KIND_WORD[p.kind] ?? p.kind}<div style={{ fontSize: 11, color: "var(--muted)" }}>{p.reason}{p.verified ? " · read back" : ""}</div></td>
                    <td className="num" style={{ textAlign: "right" }}>{size(p.bytes)}</td>
                    <td>
                      <ConfirmButton
                        label="Put everything back to this"
                        question={`${since ? `${since} import${since === 1 ? "" : "s"} and everything else since go.` : "Everything since goes."} Put everything back to ${when(p.created_at)}?`}
                        yes="Put it back"
                        no="Keep things as they are"
                        onConfirm={() => putBack(p)}
                      />
                    </td>
                  </tr>
                );
              })}
              {points.data && list.length === 0 && <tr><td colSpan={4} style={{ color: "var(--ink-2)" }}>No copies yet; the first is taken tonight, or now with the button above.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
      <DrillCard />
      <OffboxCard />
      <RecoveryKit />
    </div>
  );
}
