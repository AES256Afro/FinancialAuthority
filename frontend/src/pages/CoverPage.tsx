// Cover: how much each account needs, and by when. Three pictures of the same result, and the payday sheet.
import { useApi } from "../api";
import { COVER_MODES, CoverPanel, coverSummary, type CoverMode } from "../components/Cover";
import { ModeSwitch } from "../components/ModeSwitch";
import { StatusPill } from "../components/Status";
import { monthDay } from "../format";
import type { Cover } from "../guide";
import { useShowAs } from "../showas";

export function CoverPage() {
  const cover = useApi<Cover>("/api/v1/cover");
  const [mode, setMode] = useShowAs<CoverMode>("cover", "gauges", COVER_MODES.map((m) => m.id));
  if (cover.error) return <div style={{ color: "var(--critical)" }}>Could not load cover: {cover.error.message}</div>;
  if (!cover.data) return <div style={{ color: "var(--muted)" }}>Working out what each account needs…</div>;
  const c = cover.data;
  const worst = c.counts.crit ? "crit" : c.counts.warn ? "warn" : "good";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 1100 }}>
      <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Cover</h1>
          <StatusPill status={worst}>{coverSummary(c)}</StatusPill>
          <div style={{ flexGrow: 1 }} />
          <ModeSwitch label="Show cover as" modes={COVER_MODES} value={mode} onChange={setMode} />
        </div>
        <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)", maxWidth: "75ch" }}>
          What each account needs before {monthDay(c.paydays[0])} and before {monthDay(c.paydays[1])}, against what it holds. Checking pays its bills, the card payments and the everyday pace; the paycheck lands where it is deposited. The app never moves money: the sheet lists what you would move, and you tick it off.
        </p>
        <CoverPanel cover={c} mode={mode} onTicked={cover.reload} />
      </section>
      {mode !== "sheet" && (
        <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
          <strong>The payday sheet</strong>
          <CoverPanel cover={c} mode="sheet" onTicked={cover.reload} />
        </section>
      )}
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{c.not_advice}</div>
    </div>
  );
}
