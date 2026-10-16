// How it went: each paycheck's plan beside what happened. The app keeps the plan for a paycheck the day before it
// lands; when that paycheck's days are over, the plan is set beside your transactions. First the picture (for each
// paycheck, what was free to spend beside what everyday spending took), then the words. Every figure is the app's
// own (GET /api/v1/recap); the bars only place those figures on a line.
import { Minus, TrendingDown, TrendingUp, Wallet } from "lucide-react";
import { Link } from "react-router-dom";
import { useApi } from "../api";
import { Empty, Failed, Loading, Show } from "../components/State";
import { SeverityTag, StatusPill } from "../components/Status";
import { money, shortDate } from "../format";
import type { RecapOut, RecapWindow, Snapshot } from "../types";

const TITLE = { margin: 0, fontSize: 15, fontWeight: 700 } as const;
const SUB = { margin: 0, fontSize: 13, fontWeight: 700 } as const;
// One grammar on every bar of this page: the plan (or the usual) in the paycheck colour, what happened in the
// spending colour.
const PLAN = "var(--paycheck)";
const HAPPENED = "var(--bill)";

// Where a figure sits along one bar, as a share of the bar's width. Placing only: nothing here is shown as money.
function at(value: number, scale: number): string {
  return `${Math.min(100, Math.max(0, (value / scale) * 100))}%`;
}

function Bar({ label, value, scale, fill, note }: { label: string; value: number; scale: number; fill: string; note?: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 13, flexWrap: "wrap" }}>
        <span>{label}</span>
        <strong className="num">{money(value)}</strong>
      </div>
      <div role="img" aria-label={`${label}: ${money(value)}${note ? `. ${note}` : ""}`} style={{ position: "relative", height: 14, background: "var(--surface-2)", borderRadius: 4 }}>
        <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: at(value, scale), background: fill, borderRadius: 4 }} />
      </div>
      {note && <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{note}</div>}
    </div>
  );
}

function Legend() {
  const item = { display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--ink-2)" } as const;
  return (
    <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
      <span style={item}><span aria-hidden="true" className="sw" style={{ background: PLAN, margin: 0 }} />The plan, kept the day before payday</span>
      <span style={item}><span aria-hidden="true" className="sw" style={{ background: HAPPENED, margin: 0 }} />What happened, from your transactions</span>
    </div>
  );
}

// Kinds of spending as short bars on their own scale: the biggest is the full width.
function TopList({ top, label }: { top: { category: string; spent: number }[]; label: string }) {
  if (top.length === 0) return <div style={{ fontSize: 13, color: "var(--ink-2)" }}>No everyday spending in these days.</div>;
  const scale = Math.max(1, ...top.map((t) => t.spent));
  return (
    <div role="group" aria-label={label} style={{ display: "flex", flexDirection: "column", gap: 6, maxWidth: 560 }}>
      {top.map((t) => (
        <div key={t.category} style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", gap: "2px 10px", alignItems: "center", fontSize: 13 }}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={t.category}>{t.category}</span>
          <strong className="num" style={{ textAlign: "right" }}>{money(t.spent)}</strong>
          <div role="img" aria-label={`${t.category}: ${money(t.spent)}`} style={{ gridColumn: "1 / -1", position: "relative", height: 8, background: "var(--surface-2)", borderRadius: 4 }}>
            <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: at(t.spent, scale), background: HAPPENED, borderRadius: 4 }} />
          </div>
        </div>
      ))}
    </div>
  );
}

// One paycheck: the pair of bars first (what was free to spend, what everyday spending took), on the same scale as
// every other paycheck on the page, then the paycheck, the bills and where the spending went.
function PaycheckCard({ w, scale, billScale }: { w: RecapWindow; scale: number; billScale: number }) {
  const p = w.plan;
  const id = `recap-${p.start}`;
  return (
    <section className="card" aria-labelledby={id} style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <h2 id={id} style={{ ...TITLE, flex: "1 1 auto", minWidth: 0 }}>
          The {shortDate(p.start)} check <span style={{ fontWeight: 500, fontSize: 13, color: "var(--ink-2)" }}>· {shortDate(p.start)} to {shortDate(p.end)}</span>
        </h2>
        {!w.finished && <SeverityTag severity="info" word="So far: not over yet" />}
        <StatusPill status={w.status}>{w.word}</StatusPill>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, maxWidth: 560 }}>
        <Bar label="Free to spend, in the plan" value={p.free} scale={scale} fill={PLAN} note={p.free < 0 ? "The plan was already short: nothing was free to spend." : undefined} />
        <Bar label={w.finished ? "Everyday spending" : "Everyday spending so far"} value={w.everyday} scale={scale} fill={HAPPENED} />
      </div>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 8, fontSize: 13 }}>
        <Wallet size={15} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2, color: "var(--paycheck-ink)" }} />
        <span>
          <strong>Paycheck: {w.paycheck_word}.</strong>{" "}
          <span className="num" style={{ color: "var(--ink-2)" }}>In the plan {money(p.paycheck)}; came in {money(w.came_in)}.</span>
        </span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <h3 style={SUB}>Bills: planned and paid</h3>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, maxWidth: 560 }}>
          <Bar label="Bills in the plan" value={p.bills} scale={billScale} fill={PLAN} />
          <Bar label={w.finished ? "Bills paid" : "Bills paid so far"} value={w.bills_paid} scale={billScale} fill={HAPPENED} />
        </div>
        {(p.set_aside > 0 || p.advances > 0) && (
          <div className="num" style={{ fontSize: 12, color: "var(--ink-2)" }}>
            The plan also kept {money(p.set_aside)} for the next paycheck{p.advances > 0 ? ` and ${money(p.advances)} to repay advances` : ""}.
          </div>
        )}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <h3 style={SUB}>Where everyday spending went</h3>
        <TopList top={w.top} label={`Where everyday spending went in the ${shortDate(p.start)} check`} />
      </div>
    </section>
  );
}

// No plan kept yet: say when the first one will be, by the next payday the app knows.
function NoPlanYet() {
  const snap = useApi<Snapshot>("/api/v1/snapshot");
  if (snap.error && !snap.data) return <Failed error={snap.error} what="your next payday" onRetry={snap.reload} />;
  if (!snap.data) return <Loading what="your next payday" />;
  const next = snap.data.next_paycheck;
  if (next.known === false) {
    return (
      <Empty to="/paystubs#paycheck" action="Set up your pay">
        No plan has been kept yet. The app keeps a paycheck's plan the day before payday, and it does not know your payday yet.
      </Empty>
    );
  }
  return (
    <Empty to="/budgets" action="See this paycheck on Budgets">
      No plan has been kept yet. The app keeps a paycheck's plan the day before payday, so the first one is kept the day before your next payday, {shortDate(next.date)}, and shows here from that payday. When that paycheck's days are over, it is set beside what happened.
    </Empty>
  );
}

function Pace({ r }: { r: RecapOut }) {
  const { pace } = r;
  // The app's "then" is the usual pace written into the plan before the latest one (the latest when there is one).
  const thenPlan = r.windows.length > 1 ? r.windows[1].plan : r.windows[0]?.plan;
  const scale = Math.max(1, pace.now, pace.then ?? 0);
  const change = pace.change;
  const Icon = change === null || change === 0 ? Minus : change > 0 ? TrendingUp : TrendingDown;
  return (
    <section className="card" aria-labelledby="pace-title" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
      <h2 id="pace-title" style={TITLE}>Your usual pace, then and now</h2>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, maxWidth: 560 }}>
        {pace.then !== null && thenPlan && <Bar label={`Then, a day: in the plan for the ${shortDate(thenPlan.start)} check`} value={pace.then} scale={scale} fill={PLAN} />}
        <Bar label="Now, a day" value={pace.now} scale={scale} fill={HAPPENED} />
      </div>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 8, fontSize: 13 }}>
        <Icon size={15} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2, color: "var(--ink-2)" }} />
        <span className="num">
          {change === null
            ? "No earlier plan is kept yet, so there is nothing to compare the pace with."
            : change === 0
              ? "You spend the same each day as then."
              : change > 0
                ? `You spend ${money(change, { cents: true })} a day more than then.`
                : `You spend ${money(Math.abs(change), { cents: true })} a day less than then.`}
        </span>
      </div>
      <div style={{ fontSize: 12, color: "var(--muted)" }}>Your usual pace is what everyday spending takes on an average day, from your recent transactions.</div>
    </section>
  );
}

function Week({ r }: { r: RecapOut }) {
  const wk = r.week;
  const scale = Math.max(1, wk.spent, wk.usual_week);
  return (
    <section className="card" aria-labelledby="week-title" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "baseline" }}>
        <h2 id="week-title" style={TITLE}>This week</h2>
        <span style={{ fontSize: 13, color: "var(--ink-2)" }}>{shortDate(wk.from)} to {shortDate(wk.to)}</span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, maxWidth: 560 }}>
        <Bar label="Went out these seven days" value={wk.spent} scale={scale} fill={HAPPENED} />
        <Bar label="A usual week" value={wk.usual_week} scale={scale} fill={PLAN} />
      </div>
      <div style={{ fontSize: 14 }}>{wk.text}</div>
      {wk.top.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <h3 style={SUB}>Where it went</h3>
          <TopList top={wk.top} label="Where this week's spending went" />
        </div>
      )}
    </section>
  );
}

export function Recap() {
  const state = useApi<RecapOut>("/api/v1/recap");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 760 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>How it went</h1>
        <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)" }}>Each paycheck's plan beside what really happened: what was free to spend against what everyday spending took, the paycheck, and the bills. Newest first.</p>
      </div>
      <Show state={state} what="how it went">
        {(r) => {
          // One scale for every paycheck's pair, so the bars compare across paychecks. Placing only.
          const scale = Math.max(1, ...r.windows.flatMap((w) => [w.plan.free, w.everyday]));
          const billScale = Math.max(1, ...r.windows.flatMap((w) => [w.plan.bills, w.bills_paid]));
          const going = r.windows.filter((w) => !w.finished);
          const done = r.windows.filter((w) => w.finished);
          return (
            <>
              {r.windows.length === 0 ? (
                <NoPlanYet />
              ) : (
                <>
                  <Legend />
                  {going.map((w) => <PaycheckCard key={w.plan.start} w={w} scale={scale} billScale={billScale} />)}
                  {done.length === 0 && going[0] && (
                    <Empty>No paycheck is finished yet. The {shortDate(going[0].plan.start)} check is set beside what happened once its last day, {shortDate(going[0].plan.end)}, is over.</Empty>
                  )}
                  {done.map((w) => <PaycheckCard key={w.plan.start} w={w} scale={scale} billScale={billScale} />)}
                </>
              )}
              <Week r={r} />
              {r.windows.length > 0 && <Pace r={r} />}
              <div style={{ fontSize: 12, color: "var(--muted)" }}>
                {r.note} Everyday spending leaves out bills, transfers, card payments and income. <Link to="/budgets">Budgets</Link> shows this paycheck by kind of spending.
              </div>
            </>
          );
        }}
      </Show>
    </div>
  );
}
