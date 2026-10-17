// How the app itself has been (M51): one strip per part (disk, restore points, each mailbox and bank link, the jobs,
// the public address, the phone), a square per day, the worst hour of the day. Each state wears its icon and word
// in the key, every square says its day and note when touched, and a list under the strips says what needs a
// look now. Every state and note is the server's; the screen only lays them out.
import { CircleCheck, CircleDashed, OctagonAlert, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { shortDate, when } from "../format";
import { Said, Show } from "./State";
import { SeverityTag } from "./Status";

type CellState = "good" | "warn" | "crit" | "none";
interface HealthRow { key: string; label: string; cells: { state: CellState; note: string }[] }
interface HealthOut { days: string[]; rows: HealthRow[]; open: { key: string; label: string; state: "warn" | "crit"; note: string }[]; latest_at: string | null; said: string; alerts_on: boolean; push_ready: boolean }

const KEY: Record<CellState, { word: string; fill: string; Icon: typeof CircleCheck; ink: string }> = {
  good: { word: "Working", fill: "var(--safe)", Icon: CircleCheck, ink: "var(--safe-ink)" },
  warn: { word: "Needs a look", fill: "var(--warning)", Icon: TriangleAlert, ink: "var(--warning-ink)" },
  crit: { word: "Broken", fill: "var(--critical)", Icon: OctagonAlert, ink: "var(--critical-ink)" },
  none: { word: "No sample", fill: "var(--surface-2)", Icon: CircleDashed, ink: "var(--muted)" },
};

function rowSummary(r: HealthRow): string {
  const count = (s: CellState) => r.cells.filter((c) => c.state === s).length;
  return `${r.label}: ${(["good", "warn", "crit", "none"] as const).filter((s) => count(s) > 0).map((s) => `${count(s)} ${count(s) === 1 ? "day" : "days"} ${KEY[s].word.toLowerCase()}`).join(", ")}`;
}

export function AppHealth() {
  const state = useApi<HealthOut>("/api/v1/health/history", { live: true });
  const [picked, setPicked] = useState<{ row: string; i: number } | null>(null);
  const [said, setSaid] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  async function alerts(on: boolean) {
    setSaid(null);
    try {
      await api("/api/v1/health/alerts", { method: "PUT", body: JSON.stringify({ on }) });
      setSaid({ text: on ? "Your phone is told when a part breaks, daily while it stays broken, and when it is fixed." : "Your phone is no longer told about the app's own health.", tone: "info" });
      state.reload();
    } catch (e) {
      setSaid({ text: (e as Error).message, tone: "error" });
    }
  }
  return (
    <Show state={state} what="the app's health">
      {(h) => {
        const pickedRow = picked ? h.rows.find((r) => r.key === picked.row) : null;
        const pickedCell = pickedRow && picked ? pickedRow.cells[picked.i] : null;
        return (
          <section className="card" aria-labelledby="health-title" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <h2 id="health-title" style={{ margin: 0, fontSize: 16, fontWeight: 800 }}>How the app has been, the last {h.days.length} days</h2>
              <div style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 14, fontWeight: 700, color: h.open.length ? "var(--ink)" : "var(--safe-ink)" }}>
                {h.open.length === 0 ? <CircleCheck size={16} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2 }} /> : <TriangleAlert size={16} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2, color: "var(--warning-ink)" }} />}
                <span>{h.said}</span>
              </div>
            </div>

            {h.rows.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {h.rows.map((r) => (
                  <div key={r.key} style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "4px 10px", minWidth: 0 }}>
                    <span style={{ flex: "0 1 170px", minWidth: 0, fontSize: 13, fontWeight: 700, overflowWrap: "anywhere" }}>{r.label}</span>
                    <div role="img" aria-label={rowSummary(r)} style={{ flex: "1 1 300px", display: "grid", gridTemplateColumns: `repeat(${r.cells.length}, minmax(0, 1fr))`, gap: 2, minWidth: 0 }}>
                      {r.cells.map((c, i) => (
                        <button key={i} type="button" aria-hidden="true" tabIndex={-1} title={`${shortDate(h.days[i])}: ${KEY[c.state].word}${c.note ? `, ${c.note}` : ""}`} onClick={() => setPicked({ row: r.key, i })}
                          style={{ height: 16, padding: 0, border: c.state === "none" ? "1px solid var(--hairline)" : 0, borderRadius: 3, background: KEY[c.state].fill, cursor: "pointer", outline: picked?.row === r.key && picked.i === i ? "2px solid var(--ink)" : undefined, outlineOffset: 1 }} />
                      ))}
                    </div>
                  </div>
                ))}
                <div aria-hidden="true" style={{ display: "flex", flexWrap: "wrap", gap: "0 10px" }}>
                  <span style={{ flex: "0 1 170px" }} />
                  <div style={{ flex: "1 1 300px", display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--muted)" }}>
                    <span>{shortDate(h.days[0])}</span>
                    <span>today</span>
                  </div>
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 14px", fontSize: 12, color: "var(--ink-2)" }}>
                  {(["good", "warn", "crit", "none"] as const).map((s) => {
                    const k = KEY[s];
                    return (
                      <span key={s} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                        <span aria-hidden="true" style={{ width: 12, height: 12, borderRadius: 3, background: k.fill, border: s === "none" ? "1px solid var(--hairline)" : 0, boxSizing: "border-box" }} />
                        <k.Icon size={13} aria-hidden="true" style={{ display: "inline-block", color: k.ink }} />
                        {k.word}
                      </span>
                    );
                  })}
                </div>
                {pickedRow && pickedCell && picked && (
                  <div aria-live="polite" style={{ fontSize: 13, background: "var(--surface-2)", borderRadius: 10, padding: "8px 10px" }}>
                    <strong>{pickedRow.label}, {shortDate(h.days[picked.i])}:</strong> {KEY[pickedCell.state].word.toLowerCase()}{pickedCell.note ? `; ${pickedCell.note}` : ""}.
                  </div>
                )}
              </div>
            )}

            {h.open.length > 0 && (
              <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 6 }}>
                {h.open.map((o) => (
                  <li key={o.key} style={{ display: "flex", gap: 8, alignItems: "flex-start", flexWrap: "wrap", fontSize: 13 }}>
                    <SeverityTag severity={o.state} word={o.state === "crit" ? "Broken" : "Needs a look"} />
                    <span style={{ flex: "1 1 200px", minWidth: 0 }}><strong>{o.label}</strong>: {o.note}.</span>
                  </li>
                ))}
              </ul>
            )}

            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, minHeight: 32 }}>
                <input type="checkbox" checked={h.alerts_on} onChange={(e) => alerts(e.target.checked)} style={{ width: 18, height: 18 }} />
                Tell my phone when a part breaks, daily while it stays broken, and when it is fixed
              </label>
              {!h.push_ready && <div style={{ fontSize: 12, color: "var(--ink-2)" }}>No phone is set up to be told yet: <Link to="/settings/notifications">set up notifications</Link>.</div>}
              <div style={{ fontSize: 12, color: "var(--muted)" }}>Only the part and where to look are sent, never a job's own error text. {h.latest_at ? `Last sample ${when(h.latest_at)}; the self-check takes one each hour.` : ""}</div>
              <Said text={said?.text ?? null} tone={said?.tone} />
            </div>
          </section>
        );
      }}
    </Show>
  );
}
