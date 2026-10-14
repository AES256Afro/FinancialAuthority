import { api, useApi } from "../api";

interface JobsData {
  scheduler: string;
  jobs: { name: string; every_seconds: number; description: string }[];
  runs: { id: number; name: string; started_at: string; finished_at: string | null; ok: boolean | null; summary: string }[];
}

export function Jobs() {
  const { data, reload } = useApi<JobsData>("/api/v1/jobs");
  async function run(name: string) {
    await api(`/api/v1/jobs/${name}/run`, { method: "POST" });
    reload();
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 900 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Jobs</h1>
      <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Scheduler: {data?.scheduler ?? "…"}. Every job is idempotent and never silent on failure.</div>
      <div className="card" style={{ padding: "6px 14px" }}>
        <table className="ledger">
          <thead><tr><th>Job</th><th>Every</th><th>What it does</th><th></th></tr></thead>
          <tbody>
            {(data?.jobs ?? []).map((j) => (
              <tr key={j.name}>
                <td><code>{j.name}</code></td>
                <td>{j.every_seconds >= 3600 ? `${j.every_seconds / 3600} h` : `${j.every_seconds / 60} min`}</td>
                <td style={{ color: "var(--ink-2)" }}>{j.description}</td>
                <td style={{ textAlign: "right" }}><button className="btn" type="button" onClick={() => run(j.name)}>Run now</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="card" style={{ padding: "6px 14px", overflowX: "auto" }}>
        <table className="ledger">
          <thead><tr><th>Started</th><th>Job</th><th>Result</th></tr></thead>
          <tbody>
            {(data?.runs ?? []).map((r) => (
              <tr key={r.id}>
                <td style={{ whiteSpace: "nowrap", color: "var(--ink-2)" }}>{r.started_at.slice(0, 16).replace("T", " ")}</td>
                <td><code>{r.name}</code></td>
                <td>{r.ok === null ? "running" : r.ok ? "ok" : "failed"} · {r.summary}</td>
              </tr>
            ))}
            {data && data.runs.length === 0 && <tr><td colSpan={3} style={{ color: "var(--muted)" }}>Nothing has run yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
