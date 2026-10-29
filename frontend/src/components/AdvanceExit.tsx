// Leaving the advance apps (M46): one row per coming paycheck, the advance it needs drawn on one scale (none is the
// goal), the engine's sentence on when none is needed and the fees that saves. Every figure is the engine's.
import { CircleCheck, TriangleAlert } from "lucide-react";
import { useApi } from "../api";
import { amountClass, money, shortDate } from "../format";
import { Show } from "./State";
import { StatusPill } from "./Status";

interface ExitStep {
  payday: string;
  paycheck: number;
  left_with_no_new_advance: number;
  pays_back: number;
  borrow: number;
  fee: number;
  left: number;
}

interface ExitPlan {
  as_of: string;
  state: "none" | "exits" | "short";
  word: string;
  free_from?: string | null;
  steps: ExitStep[];
  owed_now?: number;
  usual_per_paycheck?: number;
  fee_per_100?: number;
  fees_saved?: number;
  not_advice: string;
}

export function AdvanceExit() {
  const state = useApi<ExitPlan>("/api/v1/borrowing/exit");
  return (
    <Show state={state} what="the plan to leave the advance apps">
      {(p) => (
        <section className="card" aria-labelledby="exit-title" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
            <h2 id="exit-title" style={{ margin: 0, fontSize: 17, fontWeight: 800 }}>Leaving the advance apps</h2>
            {p.state === "exits" && <StatusPill status="good">No new advance needed{p.free_from ? ` from ${shortDate(p.free_from)}` : ""}</StatusPill>}
            {p.state === "short" && <StatusPill status="warn">Still short at your usual spending</StatusPill>}
          </div>
          {p.state !== "none" && p.steps.length > 0 && <Rows steps={p.steps} />}
          <div className={amountClass(p.word)} style={{ fontSize: 14, color: "var(--ink)" }}>{p.word}</div>
          {p.state !== "none" && (
            <div style={{ fontSize: 13, color: "var(--ink-2)", display: "flex", flexWrap: "wrap", gap: "4px 16px" }}>
              {p.owed_now ? <span>Owed to the apps now, with fees: <strong className="num">{money(p.owed_now, { cents: true })}</strong></span> : null}
              {p.fee_per_100 ? <span>Their fee, from the last 90 days: <strong className="num">{money(p.fee_per_100, { cents: true })}</strong> per $100</span> : null}
            </div>
          )}
          <div style={{ fontSize: 12, color: "var(--muted)" }}>{p.not_advice}</div>
        </section>
      )}
    </Show>
  );
}

function Rows({ steps }: { steps: ExitStep[] }) {
  const scale = Math.max(1, ...steps.map((s) => s.borrow), ...steps.map((s) => s.pays_back));
  return (
    <div role="table" aria-label="Each coming paycheck and the advance it needs" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {steps.map((s) => (
        <div key={s.payday} role="row" style={{ display: "grid", gridTemplateColumns: "minmax(0, 92px) minmax(0, 1fr)", gap: 10, alignItems: "center" }}>
          <span role="cell" style={{ fontSize: 13, fontWeight: 700 }}>{shortDate(s.payday)}</span>
          <div role="cell" style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
            <div style={{ height: 12, borderRadius: 6, background: "var(--surface-2)", overflow: "hidden" }} aria-hidden="true">
              <div style={{ width: `${(s.borrow / scale) * 100}%`, height: "100%", background: "var(--later)", borderRadius: 6 }} />
            </div>
            <span style={{ fontSize: 12, color: "var(--ink-2)", lineHeight: 1.5 }}>
              {/* `borrow` is the advance, rounded up to what an app lends: never called the shortfall, which is the
                  check's own figure (River and Windows say it). */}
              {s.borrow > 0 ? (
                <><TriangleAlert size={13} aria-hidden="true" style={{ display: "inline-block", color: "var(--warning-ink)", verticalAlign: -2, marginRight: 4 }} />Needs an advance of <strong className="num">{money(s.borrow)}</strong>, the smallest that covers this check's shortfall; it costs <span className="num">{money(s.fee, { cents: true })}</span></>
              ) : (
                <><CircleCheck size={13} aria-hidden="true" style={{ display: "inline-block", color: "var(--safe-ink)", verticalAlign: -2, marginRight: 4 }} />No new advance</>
              )}
              {s.pays_back > 0 && <>. Pays back <span className="num">{money(s.pays_back, { cents: true })}</span></>}
              . Leaves <span className="num">{money(s.left)}</span>.
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}
