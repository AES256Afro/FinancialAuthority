// The phone home: the brief, in the order it matters this morning, from the engine's own sentences.
// First the one thing, then safe to spend, this morning's lines, the next steps, Cover and Saving; the rest
// sits under "More".
import { Clock, Mail } from "lucide-react";
import { useEffect } from "react";
import { Link, useLocation } from "react-router-dom";
import { PurchaseCheck } from "../components/PurchaseCheck";
import { Goals } from "../components/Goals";
import { CoverPanel, coverSummary } from "../components/Cover";
import { OneThing, placeName } from "../components/OneThing";
import { Show } from "../components/State";
import { SeverityTag, StatusPill, Why } from "../components/Status";
import { useApi } from "../api";
import type { Cover, Saving } from "../guide";
import { FirstRunBanner } from "./Welcome";
import { NextStepsCard } from "../components/NextSteps";
import { Watchlist, WorthALook } from "../components/Watchlist";
import { money, shortDate, when } from "../format";
import type { Snapshot } from "../types";
import { SafeExplain } from "../components/SafeExplain";

const TITLE = { margin: 0, fontSize: 14, fontWeight: 700 } as const;

// A pending amount in words: money out as it is, money coming back (a refund) said so.
function flight(n: number): string {
  return n < 0 ? `${money(Math.abs(n), { cents: true })} back` : money(n, { cents: true });
}

// What has happened but is not posted. Pending charges are already off the balance the bank reports; purchases
// seen only in a bank's alert email are shown and never taken off until the bank's own row arrives. The solid
// edge marks the counted ones, the dashed edge the ones only seen.
function InFlight({ s }: { s: Snapshot }) {
  const f = s.in_flight;
  if (!f || (f.pending.items.length === 0 && f.mail.items.length === 0)) return null;
  const row = (edge: string) => ({ display: "flex", alignItems: "baseline", gap: 10, padding: "6px 0 6px 10px", borderLeft: edge, fontSize: 13, flexWrap: "wrap" as const });
  const head = { display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" as const };
  return (
    <section id="in-flight" className="card" aria-labelledby="flight-title" style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
      <h2 id="flight-title" style={TITLE}>Happened, not posted yet</h2>
      {f.pending.items.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <div style={head}>
            <Clock size={15} aria-hidden="true" style={{ flexShrink: 0, color: "var(--bill-ink)" }} />
            <h3 style={{ margin: 0, fontSize: 13, fontWeight: 700, flex: "1 1 220px", minWidth: 0 }}>Pending at your bank, already off the balance it reports</h3>
            <strong className="num" style={{ fontSize: 13 }}>{flight(f.pending.total)}</strong>
          </div>
          {f.pending.items.map((x) => (
            <div key={x.id} style={row("3px solid var(--bill)")}>
              <span style={{ color: "var(--ink-2)", whiteSpace: "nowrap" }}>{shortDate(x.date)}</span>
              <span style={{ flex: "1 1 140px", minWidth: 0 }}>{x.merchant}{x.account ? <span style={{ color: "var(--muted)" }}> · {x.account}</span> : null}</span>
              <strong className="num">{flight(x.amount)}</strong>
            </div>
          ))}
        </div>
      )}
      {f.mail.items.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <div style={head}>
            <Mail size={15} aria-hidden="true" style={{ flexShrink: 0, color: "var(--ink-2)" }} />
            <h3 style={{ margin: 0, fontSize: 13, fontWeight: 700, flex: "1 1 220px", minWidth: 0 }}>Seen in your bank's email; not taken off until your bank sends it</h3>
            <strong className="num" style={{ fontSize: 13, color: "var(--ink-2)" }}>{flight(f.mail.total)}</strong>
          </div>
          {f.mail.items.map((x) => (
            <div key={x.source} style={row("3px dashed var(--muted)")}>
              <span style={{ color: "var(--ink-2)", whiteSpace: "nowrap" }}>{shortDate(x.date)}</span>
              <span style={{ flex: "1 1 140px", minWidth: 0 }}>{x.merchant || "A purchase"}{x.last4 ? <span style={{ color: "var(--muted)" }}> · card ending {x.last4}</span> : null}</span>
              <strong className="num">{flight(x.amount)}</strong>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

export function Today({ snapshot }: { snapshot: Snapshot }) {
  const s = snapshot;
  const { hash } = useLocation();
  const cover = useApi<Cover>("/api/v1/cover");
  const saving = useApi<Saving>("/api/v1/saving");
  useEffect(() => {
    // A next-step or an outlook link lands on one card of this page.
    if (hash) window.setTimeout(() => document.getElementById(hash.slice(1))?.scrollIntoView({ block: "start", behavior: "smooth" }), 150);
  }, [hash]);
  const short = s.safe_to_spend.amount < 0;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 640 }}>
      <h1 className="sr-only">Today</h1>
      <FirstRunBanner />
      <OneThing />
      <section className="card" aria-labelledby="safe-title" style={{ padding: "18px 18px 14px", display: "flex", flexDirection: "column", gap: 8 }}>
        <h2 id="safe-title" style={{ margin: 0, fontSize: 13, color: "var(--ink-2)", fontWeight: 700 }}>Safe to spend through {shortDate(s.safe_to_spend.through)}</h2>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <div className="num" style={{ fontSize: 54, fontWeight: 800, letterSpacing: "-0.03em", lineHeight: 1 }}>{money(s.safe_to_spend.amount)}</div>
          <StatusPill status={short ? "crit" : "good"}>{short ? "Short before payday" : "Bills covered"}</StatusPill>
        </div>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
          What you can spend until payday on {shortDate(s.next_paycheck.date)}. Already taken off: bills due before then, and your {money(s.buffer)} <Link to="/glossary#buffer">safety cushion</Link>. Bills after payday come out of that paycheck.
          {s.usual_pace.per_day > 0 ? ` You usually spend ${money(s.usual_pace.per_day)} a day.` : ""}
        </div>
        {s.fresh && (
          <div style={{ display: "flex", alignItems: "flex-start", gap: 6, fontSize: 12, color: "var(--ink-2)" }}>
            <Clock size={13} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2 }} />
            <span>
              {s.fresh.balances_as_of ? `As of ${when(s.fresh.balances_as_of)}: balances as your banks last reported them (the oldest report among your accounts).` : "No bank has reported a balance yet."}
              {s.fresh.newest_transaction ? ` Newest transaction ${when(s.fresh.newest_transaction)}.` : ""}
            </span>
          </div>
        )}
        <Why text={s.why?.safe_to_spend} />
        <SafeExplain s={s} />
      </section>

      <InFlight s={s} />

      <section className="card" aria-labelledby="brief-title" style={{ padding: "10px 16px 6px" }}>
        <h2 id="brief-title" style={TITLE}>This morning</h2>
        {s.brief.length === 0 && <div style={{ fontSize: 13, color: "var(--muted)", padding: "8px 0" }}>Nothing to tell you this morning.</div>}
        {s.brief.map((b, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 0", borderTop: i === 0 ? 0 : "1px solid var(--surface-2)", flexWrap: "wrap" }}>
            <SeverityTag severity={b.severity} />
            <div style={{ flex: "1 1 200px", minWidth: 0, fontSize: 14, fontWeight: b.severity === "info" ? 500 : 700 }}>{b.text}</div>
            {b.link && b.link !== "/today" && <Link to={b.link} className="btn" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", minHeight: 36, padding: "6px 10px" }} aria-label={`Open ${placeName(b.link)}: ${b.text}`}>→</Link>}
          </div>
        ))}
      </section>

      <WorthALook />

      <NextStepsCard />

      <Show state={cover} what="Cover">
        {(c) => (
          <section id="cover" className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <h2 style={TITLE}>Cover</h2>
              <StatusPill status={c.counts.crit ? "crit" : c.counts.warn ? "warn" : "good"}>{coverSummary(c)}</StatusPill>
              <Link to="/cover" style={{ marginLeft: "auto", fontSize: 13 }}>Open Cover</Link>
            </div>
            <CoverPanel cover={c} mode="gauges" onTicked={cover.reload} />
          </section>
        )}
      </Show>

      <Show state={saving} what="Saving">
        {(sv) => (
          <section className="card" style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
              <h2 style={TITLE}>{sv.next_rung ? `Saving · rung ${sv.next_rung.n}` : "Saving · every rung reached"}</h2>
              {sv.next_rung && <span style={{ fontSize: 13, color: "var(--ink-2)" }}>{sv.next_rung.name}</span>}
              <Link to="/saving" style={{ marginLeft: "auto", fontSize: 13 }}>Open Saving</Link>
            </div>
            {sv.next_rung && (
              <>
                <div style={{ height: 10, borderRadius: 5, background: "var(--surface-2)", overflow: "hidden" }} role="img" aria-label={`${money(sv.balance)} of ${money(sv.next_rung.target)}`}>
                  <div style={{ width: `${Math.round(sv.next_rung.progress * 100)}%`, height: "100%", background: "var(--saving)" }} />
                </div>
                <div className="num" style={{ fontSize: 13, color: "var(--ink-2)" }}>
                  {money(sv.balance)} of {money(sv.next_rung.target)}.
                  {sv.next_rung.eta && <> Reached by {shortDate(sv.next_rung.eta)} if you keep {sv.share_used}% of each check{sv.chosen ? "." : <> (suggested; choose yours on <Link to="/saving">Saving</Link>).</>}</>}
                </div>
              </>
            )}
          </section>
        )}
      </Show>

      <h2 className="lab" style={{ margin: "6px 0 0" }}>More</h2>
      <PurchaseCheck />
      <Watchlist />
      <Goals />
      <section className="card" style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 6 }}>
        <h2 style={TITLE}>Cash today</h2>
        {s.accounts.filter((a) => ["checking", "savings", "cash"].includes(a.kind)).map((a) => (
          <div key={a.id} style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 13 }}>
            <span>{a.name} <span style={{ color: "var(--muted)" }}>· {a.institution}</span></span>
            <strong className="num">{a.balance === null ? "—" : money(a.balance)}</strong>
          </div>
        ))}
        <div style={{ fontSize: 12 }}><Link to="/">Open Home</Link></div>
      </section>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <Link to="/checkin" className="btn" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center" }}>Weekly check-in</Link>
        <Link to="/welcome" className="btn" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center" }}>Setup steps</Link>
      </div>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{s.not_advice}</div>
    </div>
  );
}
