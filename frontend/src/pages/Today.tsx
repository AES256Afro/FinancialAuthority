// The phone home: the brief, in the order it matters this morning, from the engine's own sentences.
import { useEffect } from "react";
import { Link, useLocation } from "react-router-dom";
import { PurchaseCheck } from "../components/PurchaseCheck";
import { Goals } from "../components/Goals";
import { CoverPanel, coverSummary } from "../components/Cover";
import { OneThing } from "../components/OneThing";
import { StatusPill, Why } from "../components/Status";
import { useApi } from "../api";
import type { Cover, Saving } from "../guide";
import { FirstRunBanner } from "./Welcome";
import { NextStepsCard } from "../components/NextSteps";
import { Watchlist } from "../components/Watchlist";
import { money, shortDate } from "../format";
import type { Snapshot } from "../types";
import { SafeExplain } from "../components/SafeExplain";

const DOT = { info: "var(--paycheck)", warning: "var(--warning)", critical: "var(--critical)" };

export function Today({ snapshot }: { snapshot: Snapshot }) {
  const s = snapshot;
  const { hash } = useLocation();
  const cover = useApi<Cover>("/api/v1/cover");
  const saving = useApi<Saving>("/api/v1/saving");
  useEffect(() => {
    // A next-step or an outlook link lands on one card of this page.
    if (hash) window.setTimeout(() => document.getElementById(hash.slice(1))?.scrollIntoView({ block: "start", behavior: "smooth" }), 150);
  }, [hash]);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 640 }}>
      <FirstRunBanner />
      <OneThing />
      <section className="card" style={{ padding: "18px 18px 14px", display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ fontSize: 13, color: "var(--ink-2)", fontWeight: 700 }}>Safe to spend through {shortDate(s.safe_to_spend.through)}</div>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
          <div className="num" style={{ fontSize: 54, fontWeight: 800, letterSpacing: "-0.03em", lineHeight: 1 }}>{money(s.safe_to_spend.amount)}</div>
          <div aria-hidden="true" style={{ width: 14, height: 14, borderRadius: 4, background: s.safe_to_spend.amount < 0 ? "var(--critical)" : "var(--safe)" }} />
        </div>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
          Money in your spending accounts that nothing needs before payday on {shortDate(s.next_paycheck.date)}: what comes out before then and your {money(s.buffer)} buffer are already taken off. Bills after payday are paid from that paycheck.
          {s.usual_pace.per_day > 0 ? ` Your usual pace is ${money(s.usual_pace.per_day)} a day.` : ""}
        </div>
        <Why text={s.why?.safe_to_spend} />
        <SafeExplain s={s} />
      </section>

      {cover.data && (
        <section id="cover" className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <strong style={{ fontSize: 14 }}>Cover</strong>
            <StatusPill status={cover.data.counts.crit ? "crit" : cover.data.counts.warn ? "warn" : "good"}>{coverSummary(cover.data)}</StatusPill>
            <Link to="/cover" style={{ marginLeft: "auto", fontSize: 13 }}>The sheet</Link>
          </div>
          <CoverPanel cover={cover.data} mode="gauges" onTicked={cover.reload} />
        </section>
      )}

      {saving.data && saving.data.next_rung && (
        <section className="card" style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
            <strong style={{ fontSize: 14 }}>Saving · rung {saving.data.next_rung.n}</strong>
            <span style={{ fontSize: 13, color: "var(--ink-2)" }}>{saving.data.next_rung.name}</span>
            <Link to="/saving" style={{ marginLeft: "auto", fontSize: 13 }}>The ladder</Link>
          </div>
          <div style={{ height: 10, borderRadius: 5, background: "var(--surface-2)", overflow: "hidden" }} role="img" aria-label={`${money(saving.data.balance)} of ${money(saving.data.next_rung.target)}`}>
            <div style={{ width: `${Math.round(saving.data.next_rung.progress * 100)}%`, height: "100%", background: "var(--saving, #eda100)" }} />
          </div>
          <div className="num" style={{ fontSize: 13, color: "var(--ink-2)" }}>
            {money(saving.data.balance)} of {money(saving.data.next_rung.target)}{saving.data.next_rung.eta ? ` · ${shortDate(saving.data.next_rung.eta)} at ${saving.data.share_used}% of each check` : ""}{saving.data.chosen ? "" : " (the suggested share; choose yours on Saving)"}
          </div>
        </section>
      )}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <Link to="/checkin" className="btn" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center" }}>Weekly check-in</Link>
        <Link to="/welcome" className="btn" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center" }}>Setup steps</Link>
      </div>

      <section className="card" style={{ padding: "4px 16px 6px" }}>
        {s.brief.map((b, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 0", borderTop: i === 0 ? 0 : "1px solid var(--surface-2)" }}>
            <span className="sw" style={{ background: DOT[b.severity], flexShrink: 0 }} />
            <div style={{ flexGrow: 1, fontSize: 14, fontWeight: b.severity === "info" ? 500 : 700 }}>{b.text}</div>
            {b.link && b.link !== "/today" && <Link to={b.link} className="btn" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", minHeight: 36, padding: "6px 10px" }} aria-label="Open">→</Link>}
          </div>
        ))}
      </section>

      <NextStepsCard />

      <PurchaseCheck />

      <Watchlist />

      <Goals />

      <section className="card" style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 6 }}>
        <strong style={{ fontSize: 14 }}>Cash today</strong>
        {s.accounts.filter((a) => ["checking", "savings", "cash"].includes(a.kind)).map((a) => (
          <div key={a.id} style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
            <span>{a.name} <span style={{ color: "var(--muted)" }}>· {a.institution}</span></span>
            <strong className="num">{a.balance === null ? "—" : money(a.balance)}</strong>
          </div>
        ))}
        <div style={{ fontSize: 12 }}><Link to="/">Open the home view</Link></div>
      </section>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{s.not_advice}</div>
    </div>
  );
}
