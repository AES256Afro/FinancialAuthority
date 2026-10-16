// Settings › Jobs: what the app does on its own, how often, and how each run went. A failed run is never silent.
import { useState } from "react";
import { api, useApi } from "../api";
import { when } from "../format";
import { Failed, Loading, Said } from "../components/State";
import { StatusPill } from "../components/Status";

interface JobsData {
  scheduler: string;
  jobs: { name: string; every_seconds: number; description: string }[];
  runs: { id: number; name: string; started_at: string; finished_at: string | null; ok: boolean | null; summary: string }[];
}

// Each job's id in plain words. The id stays visible in small type, because other screens name it.
const JOB_WORD: Record<string, string> = {
  mail_sync: "Read the mailboxes",
  bank_sync: "Read the linked banks",
  import_watch: "Import files from the import folder",
  detect_recurring: "Find repeating charges and paychecks",
  match_payments: "Match bills to their payments",
  review_if_due: "Ask the reviewer for notes",
  morning_brief: "Send the morning brief",
  reminders: "Send your reminders",
  moves_verify: "Check a bank switch",
  balance_history: "Keep today's balances",
  self_check: "Check the app's own health",
  restore_point_daily: "Take the daily copy",
  offbox_copy: "Seal the off-box copy",
  restore_drill: "Prove the newest copy opens",
  prune_job_runs: "Tidy old job records",
};

export function jobWord(id: string): string {
  return JOB_WORD[id] ?? id.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

function every(seconds: number): string {
  if (seconds >= 3600) {
    const h = seconds / 3600;
    return `every ${h === 1 ? "hour" : `${h} hours`}`;
  }
  const m = Math.round(seconds / 60);
  return `every ${m} minute${m === 1 ? "" : "s"}`;
}

function JobName({ id }: { id: string }) {
  return (
    <>
      <strong>{jobWord(id)}</strong>
      <div style={{ fontSize: 11, color: "var(--muted)" }}><code>{id}</code></div>
    </>
  );
}

export function Jobs() {
  const state = useApi<JobsData>("/api/v1/jobs");
  const { data, reload, error } = state;
  const [running, setRunning] = useState<string | null>(null);
  const [result, setResult] = useState<{ name: string; ok: boolean; text: string } | null>(null);
  async function run(name: string) {
    setRunning(name);
    setResult(null);
    try {
      const r = await api<{ ok: boolean; summary: string }>(`/api/v1/jobs/${name}/run`, { method: "POST" });
      setResult({ name, ok: r.ok, text: r.ok ? `Done. ${r.summary || "Nothing to do."}` : `Failed. ${r.summary}` });
      reload();
    } catch (e) {
      setResult({ name, ok: false, text: (e as Error).message });
    } finally {
      setRunning(null);
    }
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 900 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Jobs</h1>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 13, color: "var(--ink-2)" }}>
        {data && (data.scheduler === "running" ? <StatusPill status="good">The scheduler is running</StatusPill> : <StatusPill status="crit">The scheduler is {data.scheduler}</StatusPill>)}
        <span>Each job is safe to run again, and a failure always shows here.</span>
      </div>
      {error && !data && <Failed error={error} what="the jobs" onRetry={reload} />}
      {!data && !error && <Loading what="the jobs" />}
      {data && (
        <>
          <section className="card" style={{ padding: "6px 14px" }}>
            <h2 className="sr-only">What runs on its own</h2>
            <div style={{ overflowX: "auto" }}>
              <table className="ledger">
                <thead><tr><th>Job</th><th>How often</th><th>What it does</th><th><span className="sr-only">Run now</span></th></tr></thead>
                <tbody>
                  {data.jobs.map((j) => (
                    <tr key={j.name}>
                      <td><JobName id={j.name} /></td>
                      <td style={{ whiteSpace: "nowrap" }}>{every(j.every_seconds)}</td>
                      <td style={{ color: "var(--ink-2)" }}>{j.description}</td>
                      <td style={{ textAlign: "right" }}>
                        <button className="btn" type="button" disabled={running !== null} onClick={() => run(j.name)}>{running === j.name ? "Running…" : "Run now"}</button>
                        {result?.name === j.name && <div style={{ marginTop: 4, textAlign: "left", maxWidth: 260 }}><Said text={result.text} tone={result.ok ? "info" : "error"} /></div>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section className="card" style={{ padding: "6px 14px" }}>
            <h2 style={{ margin: "8px 0 4px", fontSize: 14, fontWeight: 800 }}>Recent runs</h2>
            <div style={{ overflowX: "auto" }}>
              <table className="ledger">
                <thead><tr><th>Started</th><th>Job</th><th>Result</th></tr></thead>
                <tbody>
                  {data.runs.map((r) => (
                    <tr key={r.id}>
                      <td style={{ whiteSpace: "nowrap", color: "var(--ink-2)" }}>{when(r.started_at)}</td>
                      <td><JobName id={r.name} /></td>
                      <td>
                        <div style={{ display: "flex", gap: 8, alignItems: "flex-start", flexWrap: "wrap" }}>
                          {r.ok === null ? <StatusPill status="warn">Running</StatusPill> : r.ok ? <StatusPill status="good">Worked</StatusPill> : <StatusPill status="crit">Failed</StatusPill>}
                          <span style={{ flex: "1 1 200px", minWidth: 0, overflowWrap: "anywhere" }}>{r.summary}</span>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {data.runs.length === 0 && <tr><td colSpan={3} style={{ color: "var(--ink-2)" }}>Nothing has run yet.</td></tr>}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
