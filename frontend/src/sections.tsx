// Sections: the pieces a home is arranged from. Each has a name, the pictures it can be drawn as, and draws
// the engine's results only. An arrangement is a list of sections in order, each with its picture, some hidden.
// The eight home views are presets of the arranger; a new arrangement needs no code.
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useApi } from "./api";
import type { Arrangement } from "./appearance";
import { ByCheck } from "./components/ByCheck";
import { COVER_MODES, CoverPanel, coverSummary, type CoverMode } from "./components/Cover";
import { Goals } from "./components/Goals";
import { NextStepsCard } from "./components/NextSteps";
import { OneThing } from "./components/OneThing";
import { PurchaseCheck } from "./components/PurchaseCheck";
import { RiverChart } from "./components/RiverChart";
import { StatusPill, Why } from "./components/Status";
import { money, shortDate } from "./format";
import type { Cover, Saving } from "./guide";
import { palette } from "./looks";
import { PAYCHECK_MODES, PaycheckSection, type PaycheckMode } from "./pictures";
import { getPrefs } from "./prefs";
import type { Snapshot } from "./types";
import { MonthGrid } from "./views/MonthGrid";
import { VIEWS, viewById } from "./views";

export interface SectionDef {
  key: string;
  name: string;
  description: string;
  modes?: { id: string; name: string }[];
  render: (p: { snapshot: Snapshot; showAs?: string | null; setShowAs?: (v: string) => void }) => ReactNode;
}

export function ShowAs({ modes, value, onChange, label }: { modes: { id: string; name: string }[]; value: string; onChange?: (v: string) => void; label: string }) {
  if (!onChange || modes.length < 2) return null;
  return (
    <select className="input" value={value} onChange={(e) => onChange(e.target.value)} aria-label={`Show ${label} as`} style={{ minHeight: 30, padding: "2px 8px", fontSize: 12, marginLeft: "auto" }}>
      {modes.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
    </select>
  );
}

function Card({ title, children, link, picker }: { title: string; children: ReactNode; link?: { to: string; text: string }; picker?: ReactNode }) {
  return (
    <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <strong style={{ fontSize: 15 }}>{title}</strong>
        {picker}
        {link && <Link to={link.to} style={{ marginLeft: picker ? 0 : "auto", fontSize: 13 }}>{link.text}</Link>}
      </div>
      {children}
    </section>
  );
}

const SAFE_MODES = [{ id: "hero", name: "Big number" }, { id: "waterfall", name: "Waterfall" }, { id: "gauge", name: "Gauge" }];
const SAVING_MODES = [{ id: "ladder", name: "Ladder" }, { id: "bar", name: "Next rung" }];
const SIXTY_MODES = [{ id: "river", name: "The River" }, { id: "month", name: "Month grid" }];

function SafeSection({ s, mode, setMode }: { s: Snapshot; mode: string; setMode?: (v: string) => void }) {
  const c = palette(getPrefs().look);
  const parts = s.safe_to_spend.parts;
  const amount = s.safe_to_spend.amount;
  const head = (
    <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
      <div className="num" style={{ fontSize: mode === "hero" ? 52 : 34, fontWeight: 800, letterSpacing: "-0.03em", lineHeight: 1, color: amount < 0 ? "var(--critical)" : "var(--ink)" }}>{money(amount)}</div>
      <span style={{ fontSize: 13, color: "var(--ink-2)" }}>safe to spend through {shortDate(s.safe_to_spend.through)}</span>
      <ShowAs modes={SAFE_MODES} value={mode} onChange={setMode} label="safe to spend" />
    </div>
  );
  let body: ReactNode = null;
  if (mode === "waterfall" && parts) {
    const steps = [
      { label: "in checking", v: parts.cash, fill: "var(--ink)", start: 0 },
      { label: "due before payday", v: -parts.due_before_payday, fill: c.bill },
      { label: "advances", v: -parts.advances, fill: c.later },
      { label: "buffer", v: -parts.buffer, fill: c.buffer },
      { label: "next check short", v: -parts.next_window_shortfall, fill: c.debt },
    ].filter((x, i) => i === 0 || x.v !== 0);
    const max = Math.max(parts.cash, 1);
    let level = 0;
    body = (
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }} role="list" aria-label="How safe to spend is reached">
        {steps.map((st, i) => {
          const from = i === 0 ? 0 : level;
          const to = i === 0 ? st.v : level + st.v;
          level = to;
          const left = (Math.min(from, to) / max) * 100;
          const width = (Math.abs(st.v) / max) * 100;
          return (
            <div key={st.label} role="listitem" style={{ display: "grid", gridTemplateColumns: "150px minmax(0, 1fr) 90px", gap: 8, alignItems: "center", fontSize: 12 }}>
              <span>{st.label}</span>
              <div style={{ position: "relative", height: 16, background: "var(--surface-2)", borderRadius: 4 }}><div style={{ position: "absolute", left: `${Math.max(0, left)}%`, width: `${Math.max(0.5, width)}%`, top: 0, bottom: 0, background: st.fill, borderRadius: 4 }} /></div>
              <span className="num" style={{ textAlign: "right", fontWeight: 700 }}>{i === 0 ? money(st.v) : `−${money(-st.v)}`}</span>
            </div>
          );
        })}
        <div style={{ display: "grid", gridTemplateColumns: "150px minmax(0, 1fr) 90px", gap: 8, alignItems: "center", fontSize: 12, fontWeight: 800 }}>
          <span>safe to spend</span>
          <div style={{ position: "relative", height: 16, background: "var(--surface-2)", borderRadius: 4 }}><div style={{ position: "absolute", left: 0, width: `${Math.max(0.5, (Math.max(0, amount) / max) * 100)}%`, top: 0, bottom: 0, background: amount < 0 ? "var(--critical)" : c.safe, borderRadius: 4 }} /></div>
          <span className="num" style={{ textAlign: "right" }}>{money(amount)}</span>
        </div>
      </div>
    );
  } else if (mode === "gauge" && parts) {
    const share = Math.max(0, Math.min(1, amount / Math.max(1, parts.cash)));
    body = (
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <div role="img" aria-label={`${Math.round(share * 100)}% of checking is safe to spend`} style={{ height: 18, borderRadius: 9, background: "var(--surface-2)", overflow: "hidden" }}>
          <div style={{ width: `${share * 100}%`, height: "100%", background: amount < 0 ? "var(--critical)" : c.safe }} />
        </div>
        <span className="num" style={{ fontSize: 12, color: "var(--ink-2)" }}>{Math.round(share * 100)}% of the {money(parts.cash)} in checking</span>
      </div>
    );
  }
  return (
    <section className="card" style={{ padding: 18, display: "flex", flexDirection: "column", gap: 10 }}>
      {head}
      {body}
      <Why text={s.why?.safe_to_spend} />
    </section>
  );
}

function CoverSection({ mode, setMode }: { mode: CoverMode; setMode?: (v: string) => void }) {
  const cover = useApi<Cover>("/api/v1/cover");
  if (!cover.data) return null;
  const c = cover.data;
  return (
    <Card title="Cover" link={{ to: "/cover", text: "The sheet" }} picker={<ShowAs modes={COVER_MODES} value={mode} onChange={setMode} label="cover" />}>
      <StatusPill status={c.counts.crit ? "crit" : c.counts.warn ? "warn" : "good"}>{coverSummary(c)}</StatusPill>
      <CoverPanel cover={c} mode={mode} onTicked={cover.reload} />
    </Card>
  );
}

function SavingSection({ mode, setMode }: { mode: string; setMode?: (v: string) => void }) {
  const sv = useApi<Saving>("/api/v1/saving");
  const c = palette(getPrefs().look);
  if (!sv.data) return null;
  const s = sv.data;
  if (mode === "bar") {
    const r = s.next_rung;
    return (
      <Card title={r ? `Saving · rung ${r.n}, ${r.name.toLowerCase()}` : "Saving · every rung reached"} link={{ to: "/saving", text: "The ladder" }} picker={<ShowAs modes={SAVING_MODES} value={mode} onChange={setMode} label="saving" />}>
        {r && (
          <>
            <div role="img" aria-label={`${money(s.balance)} of ${money(r.target)}`} style={{ height: 12, borderRadius: 6, background: "var(--surface-2)", overflow: "hidden" }}><div style={{ width: `${Math.round(r.progress * 100)}%`, height: "100%", background: c.saving }} /></div>
            <span className="num" style={{ fontSize: 13, color: "var(--ink-2)" }}>{money(s.balance)} of {money(r.target)}{r.eta ? ` · ${shortDate(r.eta)} at ${s.share_used}%` : ""}</span>
          </>
        )}
      </Card>
    );
  }
  return (
    <Card title="The saving ladder" link={{ to: "/saving", text: "Open" }} picker={<ShowAs modes={SAVING_MODES} value={mode} onChange={setMode} label="saving" />}>
      {[...s.rungs].reverse().map((r) => (
        <div key={r.n} style={{ display: "grid", gridTemplateColumns: "minmax(120px, 200px) minmax(0, 1fr) auto", gap: 8, alignItems: "center", fontSize: 12 }}>
          <span style={{ fontWeight: s.next_rung?.n === r.n ? 800 : 600 }}>{r.n} · {r.name}</span>
          <div style={{ height: 10, borderRadius: 5, background: "var(--surface-2)", overflow: "hidden" }}><div style={{ width: `${Math.round(r.progress * 100)}%`, height: "100%", background: c.saving }} /></div>
          <span className="num" style={{ color: "var(--ink-2)" }}>{money(r.target)}</span>
        </div>
      ))}
    </Card>
  );
}

function BriefSection({ s }: { s: Snapshot }) {
  const dot = { info: "var(--paycheck)", warning: "var(--warning)", critical: "var(--critical)" };
  return (
    <Card title="This morning">
      {s.brief.map((b, i) => (
        <Link key={i} to={b.link || "/today"} style={{ display: "flex", gap: 8, alignItems: "center", color: "var(--ink)", textDecoration: "none", fontSize: 13 }}>
          <span className="sw" style={{ background: dot[b.severity], flexShrink: 0 }} />{b.text}
        </Link>
      ))}
    </Card>
  );
}

function BorrowingSection({ s }: { s: Snapshot }) {
  const b = s.borrowing;
  if (!b) return null;
  return (
    <Card title="Borrowing" link={{ to: "/borrowing", text: "The way out" }}>
      <div style={{ display: "flex", gap: 18, flexWrap: "wrap" }}>
        <div><div className="lab">Lost a month</div><div className="num" style={{ fontSize: 22, fontWeight: 800, color: b.fees_month ? "var(--critical)" : "var(--ink)" }}>{money(b.fees_month)}</div></div>
        <div><div className="lab">Advances out</div><div className="num" style={{ fontSize: 22, fontWeight: 800 }}>{b.outstanding} · {money(b.outstanding_total)}</div></div>
        <div><div className="lab">Advances, 90 days</div><div className="num" style={{ fontSize: 22, fontWeight: 800 }}>{b.advances_90d}</div></div>
      </div>
    </Card>
  );
}

function DebtsSection({ s }: { s: Snapshot }) {
  const total = s.debts.reduce((t, d) => t + (d.balance || 0), 0);
  const c = palette(getPrefs().look);
  const max = Math.max(1, ...s.debts.map((d) => d.balance || 0));
  return (
    <Card title={`Debts · ${money(total)}`} link={{ to: "/debts", text: "The order" }}>
      {s.debts.slice(0, 8).map((d) => (
        <div key={d.id} style={{ display: "grid", gridTemplateColumns: "minmax(120px, 220px) minmax(0, 1fr) auto", gap: 8, alignItems: "center", fontSize: 12 }}>
          <span>{d.name}{d.apr ? <span style={{ color: "var(--muted)" }}> · {d.apr}%</span> : null}</span>
          <div style={{ height: 10, borderRadius: 5, background: "var(--surface-2)" }}><div style={{ width: `${(d.balance / max) * 100}%`, height: "100%", borderRadius: 5, background: c.debt }} /></div>
          <span className="num">{money(d.balance)}</span>
        </div>
      ))}
    </Card>
  );
}

const VIEW_MODES = VIEWS.filter((v) => v.status === "ready").map((v) => ({ id: v.id, name: v.name }));

export const SECTIONS: SectionDef[] = [
  { key: "one_thing", name: "The one thing today", description: "The single most useful action, why, and the streaks.", render: () => <OneThing /> },
  { key: "safe", name: "Safe to spend", description: "The figure, and how it is reached.", modes: SAFE_MODES, render: ({ snapshot, showAs, setShowAs }) => <SafeSection s={snapshot} mode={showAs ?? "hero"} setMode={setShowAs} /> },
  { key: "paycheck", name: "Your paychecks", description: "Where each check goes, drawn eight ways.", modes: PAYCHECK_MODES.map((m) => ({ id: m.id, name: m.name })), render: ({ snapshot, showAs, setShowAs }) => <PaycheckSection snapshot={snapshot} mode={(showAs as PaycheckMode) ?? "ribbon"} onMode={setShowAs} /> },
  { key: "cover", name: "Cover", description: "What each account needs before each payday.", modes: COVER_MODES, render: ({ showAs, setShowAs }) => <CoverSection mode={(showAs as CoverMode) ?? "gauges"} setMode={setShowAs} /> },
  { key: "saving", name: "Saving", description: "The ladder and the next rung.", modes: SAVING_MODES, render: ({ showAs, setShowAs }) => <SavingSection mode={showAs ?? "ladder"} setMode={setShowAs} /> },
  { key: "sixty", name: "Sixty days", description: "The honest line, or the calendar.", modes: SIXTY_MODES, render: ({ snapshot, showAs, setShowAs }) => (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {setShowAs && <div style={{ display: "flex" }}><ShowAs modes={SIXTY_MODES} value={showAs ?? "river"} onChange={setShowAs} label="sixty days" /></div>}
      {showAs === "month" ? <MonthGrid snapshot={snapshot} /> : <section className="card" style={{ padding: 16 }}><RiverChart snapshot={snapshot} look={getPrefs().look} /></section>}
    </div>
  ) },
  { key: "by_check", name: "What each check pays", description: "One bar per check, the card payment in its parts.", render: ({ snapshot }) => <ByCheck s={snapshot} /> },
  { key: "brief", name: "The brief", description: "This morning's lines, in order.", render: ({ snapshot }) => <BriefSection s={snapshot} /> },
  { key: "borrowing", name: "Borrowing", description: "What advances and pay-later cost.", render: ({ snapshot }) => <BorrowingSection s={snapshot} /> },
  { key: "debts", name: "Debts", description: "Every balance, largest first.", render: ({ snapshot }) => <DebtsSection s={snapshot} /> },
  { key: "next_steps", name: "Next steps", description: "What the app needs from you.", render: () => <NextStepsCard /> },
  { key: "purchase", name: "Should I buy this?", description: "The purchase check.", render: () => <PurchaseCheck /> },
  { key: "goals", name: "Goals", description: "Save-up and pay-down goals.", render: () => <Goals /> },
  { key: "view", name: "A whole home view", description: "One of the home views, entire.", modes: VIEW_MODES, render: ({ snapshot, showAs }) => { const V = viewById(showAs ?? "river").component; return <V snapshot={snapshot} />; } },
];

export function sectionByKey(key: string): SectionDef | undefined {
  return SECTIONS.find((s) => s.key === key);
}

export const PRESETS: (Arrangement & { description: string })[] = [
  { id: "paycheck-first", name: "Paycheck first", description: "The one thing, safe to spend, the ribbon, cover and the ladder.", sections: [{ key: "one_thing" }, { key: "safe", show_as: "hero" }, { key: "paycheck", show_as: "ribbon" }, { key: "cover", show_as: "gauges" }, { key: "saving", show_as: "ladder" }] },
  { id: "calm", name: "Calm", description: "Three things: the figure, the rings and the next rung.", sections: [{ key: "safe", show_as: "hero" }, { key: "paycheck", show_as: "rings" }, { key: "saving", show_as: "bar" }] },
  { id: "planner", name: "Planner", description: "The trend, six months ahead, the month and the sheet.", sections: [{ key: "one_thing" }, { key: "paycheck", show_as: "strips" }, { key: "paycheck", show_as: "stream" }, { key: "sixty", show_as: "month" }, { key: "cover", show_as: "sheet" }] },
  { id: "out-of-debt", name: "Out of debt", description: "Borrowing, debts, the waterfall and the ladder.", sections: [{ key: "one_thing" }, { key: "borrowing" }, { key: "debts" }, { key: "paycheck", show_as: "waterfall" }, { key: "saving", show_as: "ladder" }] },
  ...VIEWS.filter((v) => v.status === "ready").map((v) => ({ id: v.id, name: v.name, description: v.description, sections: [{ key: "one_thing" }, { key: "view", show_as: v.id }] })),
];

export function arrangementById(id: string, mine: Arrangement[]): Arrangement {
  return mine.find((a) => a.id === id) ?? PRESETS.find((p) => p.id === id) ?? PRESETS.find((p) => p.id === "river") ?? PRESETS[0];
}
