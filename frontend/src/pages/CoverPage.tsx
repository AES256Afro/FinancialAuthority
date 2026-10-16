// Cover: how much each account needs, and by when. Three pictures of the same result, and the payday sheet.
import { Link } from "react-router-dom";
import { useApi } from "../api";
import { COVER_MODES, CoverPanel, coverSummary, type CoverMode } from "../components/Cover";
import { ModeSwitch } from "../components/ModeSwitch";
import { Failed, Loading } from "../components/State";
import { StatusPill } from "../components/Status";
import { shortDate } from "../format";
import type { Cover } from "../guide";
import { useShowAs } from "../showas";

export function CoverPage() {
  const cover = useApi<Cover>("/api/v1/cover");
  const [mode, setMode] = useShowAs<CoverMode>("cover", "gauges", COVER_MODES.map((m) => m.id));
  if (cover.error && !cover.data) return <Failed error={cover.error} what="Cover" onRetry={cover.reload} />;
  if (!cover.data) return <Loading what="what each account needs" />;
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
        <p style={{ margin: 0, fontSize: 14, fontWeight: 600, maxWidth: "75ch" }}>
          Cover means: does each account hold enough for what comes out of it before {shortDate(c.paydays[0])}, and before {shortDate(c.paydays[1])}? <Link to="/glossary#cover" style={{ fontWeight: 500 }}>What Cover means</Link>
        </p>
        <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)", maxWidth: "75ch" }}>
          When one is short, the payday sheet lists what to move. The app never moves money: you make each move and tick it off.
        </p>
        <CoverPanel cover={c} mode={mode} onTicked={cover.reload} />
      </section>
      {mode !== "sheet" && (
        <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
          <h2 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>The payday sheet</h2>
          <CoverPanel cover={c} mode="sheet" onTicked={cover.reload} />
        </section>
      )}
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{c.not_advice}</div>
    </div>
  );
}
