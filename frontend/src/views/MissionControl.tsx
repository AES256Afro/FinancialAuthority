// Mission control: tiles. What the app did, what it watches, what it wants from you. Best in the Night or Console look.
import { Link } from "react-router-dom";
import { useApi } from "../api";
import { money, monthDay, shortDate } from "../format";
import { palette } from "../looks";
import { getPrefs } from "../prefs";
import type { ViewProps } from "./index";

interface JobsData {
  scheduler: string;
  jobs: { name: string; every_seconds: number; description: string }[];
  runs: { id: number; name: string; started_at: string; finished_at: string | null; ok: boolean | null; summary: string }[];
}

function Tile({ label, children, border }: { label: string; children: React.ReactNode; border?: string }) {
  return (
    <div className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 6, borderColor: border }}>
      <div className="lab">{label}</div>
      {children}
    </div>
  );
}

function Big({ value, sub }: { value: string; sub: string }) {
  return (
    <>
      <div className="num" style={{ fontSize: 28, fontWeight: 800, letterSpacing: "-0.02em" }}>{value}</div>
      <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{sub}</div>
    </>
  );
}

function Row({ left, right, tone }: { left: string; right: string; tone?: "ok" | "warn" }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 10, padding: "5px 0", borderTop: "1px solid var(--surface-2)", fontSize: 13 }}>
      <span>{left}</span>
      <span style={{ fontWeight: 800, fontSize: 12, color: tone === "warn" ? "var(--warning-ink)" : tone === "ok" ? "var(--safe-ink)" : "var(--ink-2)" }}>{right}</span>
    </div>
  );
}

export function MissionControl({ snapshot }: ViewProps) {
  const c = palette(getPrefs().look);
  const jobs = useApi<JobsData>("/api/v1/jobs");
  const settings = useApi<{ selfcheck: { at?: string; free_gb?: number; db_mb?: number; last_transaction?: string | null; stale_accounts?: string[] } }>("/api/v1/settings");
  const s = snapshot;
  const pts = s.projection.slice(0, 30);
  const top = Math.max(600, ...pts.map((p) => p.at_pace));
  const bottom = Math.min(0, ...pts.map((p) => p.at_pace));
  const y = (v: number) => 130 - ((v - bottom) / (top - bottom)) * 120;
  const x = (i: number) => (i / Math.max(1, pts.length - 1)) * 520;
  const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.at_pace).toFixed(1)}`).join(" ");
  const lastRun = (name: string) => jobs.data?.runs.find((r) => r.name === name);
  const check = settings.data?.selfcheck ?? {};
  const soon = s.due_before_payday.total;
  const waiting = s.pending.confirmations + s.pending.approvals;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 16 }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800, letterSpacing: "-0.01em" }}>Mission control</h1>
        <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{shortDate(s.as_of)} · what the app did, what it watches, what it wants from you</div>
        <div style={{ flexGrow: 1 }} />
        <div style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 12, fontWeight: 800, padding: "5px 12px", borderRadius: 999, background: "var(--paycheck-soft)", color: "var(--paycheck-ink)" }}><span className="sw" style={{ background: "var(--paycheck)", margin: 0 }} />T minus {s.next_paycheck.days} days to payday</div>
      </div>
      <section className="grid grid-cols-2 xl:grid-cols-4" style={{ gap: 12 }}>
        <Tile label="Safe to spend" border="var(--safe)"><Big value={money(s.safe_to_spend.amount)} sub={`through ${shortDate(s.safe_to_spend.through)} · buffer ${money(s.buffer)} kept`} /></Tile>
        <Tile label="Clears before payday"><Big value={money(soon)} sub={s.due_before_payday.items.map((e) => e.name).join(", ") || "nothing"} /></Tile>
        <Tile label="Past due" border={s.pending.past_due ? "var(--critical)" : undefined}><Big value={String(s.pending.past_due)} sub={s.payments.late.length ? `${s.payments.late.length} not matched yet` : "none unmatched"} /></Tile>
        <Tile label="Waiting for you" border={waiting ? "var(--warning)" : undefined}><Big value={String(waiting)} sub={`${s.pending.confirmations} to confirm · ${s.pending.approvals} to approve`} /></Tile>
      </section>
      <section className="grid grid-cols-1 xl:grid-cols-[1.4fr_1fr_1fr]" style={{ gap: 12 }}>
        <Tile label="At your usual pace, next 30 days">
          <svg viewBox="0 0 520 150" width="100%" style={{ display: "block", height: "auto" }} role="img" aria-label="Projected available balance at the usual pace for the next thirty days">
            <line x1={0} y1={y(0)} x2={520} y2={y(0)} stroke={c.buffer} strokeWidth={1} />
            <path d={`${line} L${x(pts.length - 1).toFixed(1)},${y(0)} L0,${y(0)} Z`} fill={c.safe} fillOpacity={0.16} />
            <path d={line} fill="none" stroke={c.safe} strokeWidth={2} />
            <line x1={0} y1={6} x2={0} y2={130} stroke={c.ink} strokeWidth={1.5} />
            <text x={4} y={14} fontSize={11} fontWeight={700} fill={c.ink2}>today</text>
            {pts.map((p, i) => (s.calendar.events.some((e) => e.kind === "paycheck" && e.date === p.date) ? <g key={p.date}><line x1={x(i)} y1={y(0)} x2={x(i)} y2={y(p.at_pace)} stroke={c.paycheck} strokeWidth={2} /><text x={x(i) + 4} y={y(p.at_pace) - 6} fontSize={11} fontWeight={700} fill={c.ink2}>payday</text></g> : null))}
          </svg>
          <div style={{ fontSize: 11, color: "var(--muted)" }}>{money(s.usual_pace.per_day)} a day from the last {s.usual_pace.basis_days} days of the ledger</div>
        </Tile>
        <Tile label="Connectors">
          {s.accounts.map((a) => <Row key={a.id} left={`${a.name} · ${a.connector}`} right={a.balance_at ? monthDay(a.balance_at.slice(0, 10)) : "balance entered"} tone="ok" />)}
          <Row left="Self-check" right={check.at ? `${check.free_gb} GB free · db ${check.db_mb} MB` : "not yet run"} tone={check.at ? "ok" : "warn"} />
          <Row left="Mailboxes" right="M4" tone="warn" />
        </Tile>
        <Tile label="Jobs">
          {(jobs.data?.jobs ?? []).map((j) => {
            const r = lastRun(j.name);
            return <Row key={j.name} left={j.name.replace(/_/g, " ")} right={r ? (r.ok === false ? "failed" : r.summary.slice(0, 40) || "ok") : "not yet"} tone={r ? (r.ok === false ? "warn" : "ok") : "warn"} />;
          })}
          <div style={{ fontSize: 12 }}><Link to="/settings/jobs">All runs</Link></div>
        </Tile>
      </section>
      <section className="grid grid-cols-1 xl:grid-cols-3" style={{ gap: 12 }}>
        <Tile label="Waiting for you">
          {waiting === 0 && <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Nothing. The next thing to confirm will appear here.</div>}
          {s.pending.confirmations > 0 && <Row left={`${s.pending.confirmations} recurring charges to name`} right="open" tone="warn" />}
          <div style={{ fontSize: 12 }}><Link to="/confirm">Needs you</Link></div>
        </Tile>
        <Tile label="The brief">
          <div style={{ fontSize: 13, color: "var(--ink)", lineHeight: 1.5, display: "flex", flexDirection: "column", gap: 4 }}>
            {s.brief.map((b, i) => <div key={i}><span className="sw" style={{ background: b.severity === "critical" ? "var(--critical)" : b.severity === "warning" ? "var(--warning)" : "var(--paycheck)" }} />{b.text}</div>)}
          </div>
        </Tile>
        <Tile label="Don't buy">
          <div style={{ fontSize: 13, color: "var(--ink-2)" }}>The rules that fill this list arrive with the engine in M5.</div>
        </Tile>
      </section>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{s.not_advice}</div>
    </div>
  );
}
