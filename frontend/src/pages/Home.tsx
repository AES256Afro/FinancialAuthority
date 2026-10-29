// The home: the arrangement chosen in Settings › Appearance (a preset or your own), section by section, or the
// one named in ?view= for a direct link. Every section draws the engine's results only.
import { Link, useSearchParams } from "react-router-dom";
import { useAppearance, type Section } from "../appearance";
import { arrangementById, sectionByKey } from "../sections";
import { useShowAs } from "../showas";
import type { Snapshot } from "../types";
import { FirstRunBanner } from "./Welcome";

// One section of the home, with its picture remembered for this place in this arrangement.
function Placed({ s, at, snapshot }: { s: Section; at: string; snapshot: Snapshot }) {
  const def = sectionByKey(s.key);
  const fallback = s.show_as ?? def?.modes?.[0]?.id ?? "";
  const [showAs, setShowAs] = useShowAs<string>(at, fallback, def?.modes?.map((m) => m.id));
  if (!def) return null;
  const switchable = Boolean(def.modes) && s.key !== "view";
  return <div style={{ minWidth: 0 }}>{def.render({ snapshot, showAs: switchable ? showAs : s.show_as, setShowAs: switchable ? setShowAs : undefined })}</div>;
}

export function Home({ snapshot }: { snapshot: Snapshot }) {
  const [params] = useSearchParams();
  const a = useAppearance();
  const chosen = arrangementById(params.get("view") ?? a.active, a.arrangements);
  const shown = chosen.sections.filter((s) => !s.hidden);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
      {/* The page's one h1; each section and home view titles itself with an h2. */}
      <h1 className="sr-only">Home: {chosen.name}</h1>
      {/* Sign-in lands here: the five setup steps show until they are done or hidden, as on Today. */}
      <FirstRunBanner />
      {shown.map((s, i) => <Placed key={`${chosen.id}-${s.key}-${i}`} s={s} at={`home.${chosen.id}.${i}`} snapshot={snapshot} />)}
      <div style={{ fontSize: 12, color: "var(--muted)" }}>
        {chosen.name} · <Link to="/settings/appearance">arrange this home</Link>
      </div>
    </div>
  );
}
