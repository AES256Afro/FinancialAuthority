// Mission control: tiles. What the app did, what it watches, what it wants from you. Best in the Night or Console look.
import { CircleCheck, TriangleAlert } from "lucide-react";
import { Link } from "react-router-dom";
import { useApi } from "../api";
import { amountClass, connectorWord, daysWord, money, shortDate, when } from "../format";
import { palette } from "../looks";
import { getPrefs } from "../prefs";
import type { ViewProps } from "./index";
import { PlanCaveats, SafeExplain, SafePill, noPayday, safeFigure, safeShort } from "../components/SafeExplain";
import { Failed, Loading } from "../components/State";
import { SeverityTag } from "../components/Status";
import { Watchlist } from "../components/Watchlist";
import { jobWord } from "../pages/Jobs";

interface JobsData {
  scheduler: string;
  jobs: { name: string; every_seconds: number; description: string }[];
  runs: { id: number; name: string; started_at: string; finished_at: string | null; ok: boolean | null; summary: string }[];
}

interface SelfCheck {
  at?: string;
  free_gb?: number;
  db_mb?: number;
  last_transaction?: string | null;
  stale_accounts?: string[];
  sources?: { kind: string; name: string; last_ok: string | null; error: string }[];
}

function Tile({ label, children, border }: { label: string; children: React.ReactNode; border?: string }) {
  return (
    <div className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 6, borderColor: border }}>
      <h3 className="lab" style={{ margin: 0 }}>{label}</h3>
      {children}
    </div>
  );
}

function Big({ value, sub }: { value: string; sub: string }) {
  return (
    <>
      <div className="num" style={{ fontSize: 28, fontWeight: 800, letterSpacing: "-0.02em" }}>{value}</div>
      <div className={amountClass(sub)} style={{ fontSize: 12, color: "var(--ink-2)" }}>{sub}</div>
    </>
  );
}

// A row's state is an icon and words, never colour alone: "ok" rows say what is fine, "warn" rows what is not.
function Row({ left, right, tone }: { left: string; right: string; tone?: "ok" | "warn" }) {
  const Icon = tone === "warn" ? TriangleAlert : tone === "ok" ? CircleCheck : null;
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10, padding: "5px 0", borderTop: "1px solid var(--surface-2)", fontSize: 13 }}>
      <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>{left}</span>
      <span style={{ fontWeight: 800, fontSize: 12, textAlign: "right", color: tone === "warn" ? "var(--warning-ink)" : tone === "ok" ? "var(--safe-ink)" : "var(--ink-2)" }}>
        {Icon && <Icon size={12} aria-hidden="true" style={{ verticalAlign: -1, marginRight: 4 }} />}{right}
      </span>
    </div>
  );
}

export function MissionControl({ snapshot }: ViewProps) {
  const c = palette(getPrefs().look);
  const jobs = useApi<JobsData>("/api/v1/jobs");
  const settings = useApi<{ selfcheck: SelfCheck }>("/api/v1/settings");
  const s = snapshot;
  const pts = s.projection.slice(0, 30);
  const top = Math.max(600, ...pts.map((p) => p.at_pace));
  const bottom = Math.min(0, ...pts.map((p) => p.at_pace));
  const y = (v: number) => 130 - ((v - bottom) / (top - bottom)) * 120;
  const x = (i: number) => (i / Math.max(1, pts.length - 1)) * 520;
  const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.at_pace).toFixed(1)}`).join(" ");
  const lastRun = (name: string) => jobs.data?.runs.find((r) => r.name === name);
  const check = settings.data?.selfcheck ?? {};
  const stale = new Set(check.stale_accounts ?? []);
  const mailboxes = (check.sources ?? []).filter((m) => m.kind === "mail");
  const soon = s.due_before_payday.total;
  const waiting = s.pending.total;
  const short = safeShort(s);
  const unknown = noPayday(s);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 16 }}>
        <h2 style={{ margin: 0, fontSize: 20, fontWeight: 800, letterSpacing: "-0.01em" }}>Mission control</h2>
        <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{shortDate(s.as_of)} · what the app did, what it watches, what it wants from you</div>
        <div style={{ flexGrow: 1 }} />
        <div style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 12, fontWeight: 800, padding: "5px 12px", borderRadius: 999, background: "var(--paycheck-soft)", color: "var(--paycheck-ink)" }}><span className="sw" style={{ background: "var(--paycheck)", margin: 0 }} />{s.next_paycheck.known === false ? "No payday on file" : `Payday ${daysWord(s.next_paycheck.days)} · ${shortDate(s.next_paycheck.date)}`}</div>
      </div>
      <section className="grid grid-cols-2 xl:grid-cols-4" style={{ gap: 12 }}>
        <Tile label="Safe to spend" border={short ? "var(--critical)" : "var(--safe)"}>
          <Big value={safeFigure(s)} sub={`${unknown ? "now, counting no paycheck; bills due by" : "through"} ${shortDate(s.safe_to_spend.through)} · safety cushion ${money(s.buffer)} kept`} />
          {(short || unknown) && <div><SafePill s={s} /></div>}
          {unknown && <Link to="/paystubs#paycheck" style={{ fontSize: 12, fontWeight: 700 }}>Add your paycheck</Link>}
        </Tile>
        <Tile label={unknown ? `Due by ${shortDate(s.safe_to_spend.through)}` : "Due before payday"}><Big value={money(soon)} sub={s.due_before_payday.groups && s.due_before_payday.groups.length > 0 ? s.due_before_payday.groups.map((g) => `${g.label.toLowerCase()} ${money(g.total)}`).join(" · ") : s.due_before_payday.items.map((e) => e.name).join(", ") || "nothing"} /></Tile>
        <Tile label="Past due" border={s.pending.past_due ? "var(--critical)" : undefined}><Big value={String(s.pending.past_due)} sub={s.payments.late.length ? `${s.payments.late.length} not matched yet` : "none unmatched"} /></Tile>
        <Tile label="Needs you" border={waiting ? "var(--warning)" : undefined}><Big value={String(waiting)} sub={`${s.pending.confirmations} to confirm · ${s.pending.approvals} to approve`} /></Tile>
      </section>
      <PlanCaveats s={s} />
      <SafeExplain s={s} />
      <section className="grid grid-cols-1 xl:grid-cols-[1.4fr_1fr_1fr]" style={{ gap: 12 }}>
        <Tile label="At your usual spending, next 30 days">
          <svg viewBox="0 0 520 150" width="100%" style={{ display: "block", height: "auto" }} role="img" aria-label="What is free to spend, day by day, for the next thirty days, at your usual spending">
            <line x1={0} y1={y(0)} x2={520} y2={y(0)} stroke={c.buffer} strokeWidth={1} />
            <path d={`${line} L${x(pts.length - 1).toFixed(1)},${y(0)} L0,${y(0)} Z`} fill={c.safe} fillOpacity={0.16} />
            <path d={line} fill="none" stroke={c.safe} strokeWidth={2} />
            <line x1={0} y1={6} x2={0} y2={130} stroke={c.ink} strokeWidth={1.5} />
            <text x={4} y={14} fontSize={11} fontWeight={700} fill={c.ink2}>today</text>
            {pts.map((p, i) => (s.calendar.events.some((e) => e.kind === "paycheck" && e.date === p.date) ? <g key={p.date}><line x1={x(i)} y1={y(0)} x2={x(i)} y2={y(p.at_pace)} stroke={c.paycheck} strokeWidth={2} /><text x={x(i) + 4} y={y(p.at_pace) - 6} fontSize={11} fontWeight={700} fill={c.ink2}>payday</text></g> : null))}
          </svg>
          <div className="num" style={{ fontSize: 11, color: "var(--muted)" }}>{money(s.usual_pace.per_day)} a day, from the last {s.usual_pace.basis_days} days of your transactions</div>
        </Tile>
        <Tile label="Connections">
          {s.accounts.map((a) => {
            const old = stale.has(a.name);
            return <Row key={a.id} left={`${a.name} · ${connectorWord(a.connector)}`} right={a.balance_at ? `${old ? "Not updated since" : "Updated"} ${when(a.balance_at)}` : "Balance typed in"} tone={old ? "warn" : a.balance_at ? "ok" : undefined} />;
          })}
          {settings.error && !settings.data && <Failed error={settings.error} what="the self-check and the mailboxes" onRetry={settings.reload} card={false} />}
          {!settings.data && !settings.error && <Loading what="the self-check" card={false} />}
          {settings.data && <Row left="Self-check" right={check.at ? `Ran ${when(check.at)} · ${check.free_gb} GB free · database ${check.db_mb} MB` : "Not run yet"} tone={check.at ? "ok" : "warn"} />}
          {settings.data && mailboxes.length === 0 && <Row left="Mailboxes" right="None connected yet" />}
          {mailboxes.map((m) => <Row key={m.name} left={`Mailbox · ${m.name}`} right={m.error ? `Problem: ${m.error}` : m.last_ok ? `Read ${when(m.last_ok)}` : "Not read yet"} tone={m.error || !m.last_ok ? "warn" : "ok"} />)}
          <div style={{ fontSize: 12 }}><Link to="/settings/connectors">Open Connections</Link></div>
        </Tile>
        <Tile label="Jobs">
          {jobs.error && !jobs.data && <Failed error={jobs.error} what="the jobs" onRetry={jobs.reload} card={false} />}
          {!jobs.data && !jobs.error && <Loading what="the jobs" card={false} />}
          {(jobs.data?.jobs ?? []).map((j) => {
            const r = lastRun(j.name);
            return <Row key={j.name} left={jobWord(j.name)} right={r ? (r.ok === false ? "Failed" : r.summary.slice(0, 40) || "Ran fine") : "Not run yet"} tone={r ? (r.ok === false ? "warn" : "ok") : "warn"} />;
          })}
          <div style={{ fontSize: 12 }}><Link to="/settings/jobs">Open Jobs: every run</Link></div>
        </Tile>
      </section>
      <section className="grid grid-cols-1 xl:grid-cols-3" style={{ gap: 12 }}>
        <Tile label="Needs you">
          {waiting === 0 && <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Nothing. The next thing to confirm will appear here.</div>}
          {s.pending.series > 0 && <Row left={`${s.pending.series} repeating ${s.pending.series === 1 ? "charge" : "charges"} to name`} right="Needs you" tone="warn" />}
          {s.pending.facts > 0 && <Row left={`${s.pending.facts} ${s.pending.facts === 1 ? "detail" : "details"} from your mail to confirm`} right="Needs you" tone="warn" />}
          {(s.pending.pairs ?? 0) > 0 && <Row left={`${s.pending.pairs} matched ${s.pending.pairs === 1 ? "pair" : "pairs"} to check`} right="Needs you" tone="warn" />}
          {s.pending.approvals > 0 && <Row left={`${s.pending.approvals} emails waiting for your OK`} right="Needs you" tone="warn" />}
          <div style={{ fontSize: 12 }}><Link to="/confirm">Open Needs you</Link></div>
        </Tile>
        <Tile label="The brief">
          <div style={{ fontSize: 13, color: "var(--ink)", lineHeight: 1.5, display: "flex", flexDirection: "column", gap: 6 }}>
            {s.brief.length === 0 && <span style={{ color: "var(--ink-2)" }}>Nothing to tell you this morning.</span>}
            {s.brief.map((b, i) => <div key={i} style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}><SeverityTag severity={b.severity} /><span className={amountClass(b.text)} style={{ flex: "1 1 160px", minWidth: 0 }}>{b.text}</span></div>)}
          </div>
        </Tile>
        <Watchlist />
      </section>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{s.not_advice}</div>
    </div>
  );
}
