// Write-offs (M58): purchases from the last three years that could be business costs, if they were for a business,
// grouped by the kind of cost, each with its rule and source. A bar per group leads, sized by the engine's totals.
// The owner says which were for the business (at a share) and which were personal; nothing here adds up a figure.
import { Briefcase, CircleHelp, Info, Lightbulb, Receipt, UserRound } from "lucide-react";
import { useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { ConfirmButton } from "../components/ConfirmButton";
import { Said, Show } from "../components/State";
import { amountClass, money } from "../format";
import type { Business as BusinessRow, Finder, FinderGroup } from "../types-business";
import { BusinessPick, CARD, FIELD, H2, INLINE_ICON, NeedsBusiness, NeverList, NotAdvice, PageHead, SMALL, SourceLine, dayWithYear, useChosenBusiness } from "./Business";

const FIRST = 12; // purchases shown in a group before "Show all"

export function WriteOffs() {
  const { overview, business, choose } = useChosenBusiness();
  const finder = useApi<Finder>("/api/v1/business/finder?years=3", { live: true });
  // What the last answer did. A group whose purchases were all answered leaves the page, so its message shows at the top.
  const [said, setSaid] = useState<{ key: string; text: string; tone: "info" | "error" } | null>(null);
  const businesses = overview.data?.businesses ?? [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <PageHead back title="Write-offs" sub="Purchases that could be business costs, if they were for your business." />
      {overview.data && !business && (
        <NeedsBusiness>There is no business on file yet. Add it on Business first; a purchase can be marked as the business's once there is one. You can already say which were personal.</NeedsBusiness>
      )}
      <BusinessPick businesses={businesses} value={business?.id ?? null} onChange={choose} />
      <Show state={finder} what="the write-off finder">
        {(f) => {
          const lostGroup = said && !f.groups.some((g) => g.key === said.key) ? said : null;
          return (
            <>
              <Lead f={f} />
              {lostGroup && <div><Said text={lostGroup.text} tone={lostGroup.tone} /></div>}
              {f.groups.map((g) => (
                <GroupCard key={g.key} g={g} business={business} businesses={businesses} said={said?.key === g.key ? said : null} onSaid={setSaid} />
              ))}
              {f.equipment && (
                <section className="card" style={CARD} aria-labelledby="equipment-title">
                  <h2 id="equipment-title" style={H2}><Lightbulb size={16} aria-hidden="true" /> Equipment you already own</h2>
                  <div style={{ fontSize: 14, lineHeight: 1.5 }}>{f.equipment.words}</div>
                  <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
                    {f.equipment.count} {f.equipment.count === 1 ? "purchase" : "purchases"} from before this year, <span className="num">{money(f.equipment.cost, { cents: true })}</span> paid.
                  </div>
                  <SourceLine source={f.equipment.source} />
                </section>
              )}
              <section className="card" style={CARD} aria-labelledby="startup-title">
                <h2 id="startup-title" style={H2}><Briefcase size={16} aria-hidden="true" /> Costs before a business begins</h2>
                <div className={amountClass(f.startup.words)} style={{ fontSize: 14, lineHeight: 1.5 }}>{f.startup.words}</div>
                <SourceLine source={f.startup.source} />
              </section>
              <div style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: 14, lineHeight: 1.5, padding: "12px 14px", borderRadius: 12, background: "var(--surface-2)", color: "var(--ink-2)" }}>
                <Info size={16} aria-hidden="true" style={{ flexShrink: 0, marginTop: 3 }} />
                <span>{f.past_years}</span>
              </div>
              <NeverList items={f.never} />
              <NotAdvice text={f.not_advice} />
            </>
          );
        }}
      </Show>
    </div>
  );
}

/** The picture: one bar per kind of cost, as long as its total, with the engine's sentence above. */
function Lead({ f }: { f: Finder }) {
  const most = Math.max(0, ...f.groups.map((g) => g.total));
  return (
    <section className="card" style={CARD} aria-labelledby="lead-title">
      <h2 id="lead-title" style={H2}>What could be business costs, since {f.since.slice(0, 4)}</h2>
      <div className={amountClass(f.said)} style={{ fontSize: 15, lineHeight: 1.5, fontWeight: 600, maxWidth: "75ch" }}>{f.said}</div>
      {f.groups.length > 0 && (
        <ul aria-label="Purchases by kind of cost" style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 10 }}>
          {f.groups.map((g) => (
            <li key={g.key}>
              <a href={`#wo-${g.key}`} style={{ display: "flex", flexDirection: "column", gap: 4, color: "var(--ink)", textDecoration: "none" }}>
                <span style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap", fontSize: 13 }}>
                  <strong style={{ minWidth: 0 }}>{g.words}</strong>
                  <span style={{ color: "var(--muted)", fontSize: 12 }}>{g.count} {g.count === 1 ? "purchase" : "purchases"}</span>
                  <span className="num" style={{ marginLeft: "auto", fontWeight: 800 }}>{money(g.total, { cents: true })}</span>
                </span>
                <span style={{ display: "block", height: 14, background: "var(--surface-2)", borderRadius: 4, overflow: "hidden" }} aria-hidden="true">
                  <span style={{ display: "block", width: `${most > 0 ? (g.total / most) * 100 : 0}%`, height: "100%", background: "var(--paycheck)", borderRadius: 4 }} />
                </span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

type Share = "1" | "0.5" | "other";

function GroupCard({ g, business, businesses, said, onSaid }: {
  g: FinderGroup;
  business: BusinessRow | null;
  businesses: BusinessRow[];
  said: { text: string; tone: "info" | "error" } | null;
  onSaid: (s: { key: string; text: string; tone: "info" | "error" }) => void;
}) {
  const [picked, setPicked] = useState<Set<number>>(() => new Set());
  const [all, setAll] = useState(false);
  const [asking, setAsking] = useState(false);
  const [share, setShare] = useState<Share>("1");
  const [other, setOther] = useState("");
  const [bizId, setBizId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const shown = all ? g.items : g.items.slice(0, FIRST);
  const n = picked.size;
  const allShownOn = shown.length > 0 && shown.every((i) => picked.has(i.id));
  const forBusiness = bizId ?? business?.id ?? null;
  const otherShare = Number(other.trim());
  const shareValue = share === "other" ? (other.trim() && otherShare > 0 && otherShare <= 100 ? otherShare / 100 : null) : Number(share);

  function toggle(id: number) {
    setPicked((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function answer(kind: "business" | "personal") {
    const ids = [...picked];
    if (!ids.length) return;
    if (kind === "business" && (forBusiness === null || shareValue === null)) return;
    setBusy(true);
    try {
      const out = await api<Finder>("/api/v1/business/finder/answer", {
        method: "POST",
        body: JSON.stringify({ ids, answer: kind, business_id: kind === "business" ? forBusiness : null, tax_line: kind === "business" ? g.key : "", share: kind === "business" ? shareValue : 1 }),
      });
      onSaid({ key: g.key, text: out.said, tone: "info" });
      setPicked(new Set());
      setAsking(false);
    } catch (err) {
      // "Personal" asks first; its question shows the refusal in place. "For the business" says it below.
      if (kind === "personal") throw err;
      onSaid({ key: g.key, text: `Couldn't save that: ${(err as Error).message}`, tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  const check: CSSProperties = { width: 20, height: 20, margin: 0, flexShrink: 0, cursor: "pointer" };
  return (
    <section id={`wo-${g.key}`} className="card" style={{ ...CARD, scrollMarginTop: 16 }} aria-labelledby={`wo-${g.key}-title`}>
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <h2 id={`wo-${g.key}-title`} style={H2}>{g.words}</h2>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
          Schedule C line {g.line}: {g.examples}
        </div>
      </div>
      <div style={{ display: "flex", gap: "6px 14px", flexWrap: "wrap", fontSize: 13, alignItems: "baseline" }}>
        <span><strong className="num">{money(g.total, { cents: true })}</strong> in {g.count} {g.count === 1 ? "purchase" : "purchases"}</span>
        <span style={{ color: "var(--ink-2)" }}><Receipt size={13} aria-hidden="true" style={INLINE_ICON} /> {g.receipts} with a receipt kept</span>
        {g.by_year.map((y) => (
          <span key={y.year} style={{ color: "var(--ink-2)" }}>{y.year}: <span className="num">{money(y.total, { cents: true })}</span></span>
        ))}
      </div>
      <div style={{ fontSize: 14, lineHeight: 1.5, maxWidth: "75ch" }}>{g.rule}</div>
      <SourceLine source={g.source} />
      <div style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: 15, fontWeight: 800 }}>
        <CircleHelp size={18} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2 }} />
        <span>{g.question}</span>
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", borderTop: "1px solid var(--surface-2)", paddingTop: 8 }}>
        <label style={{ display: "inline-flex", gap: 8, alignItems: "center", fontSize: 13, fontWeight: 700, minHeight: 32 }}>
          <input type="checkbox" checked={allShownOn} onChange={() => setPicked(allShownOn ? new Set() : new Set(shown.map((i) => i.id)))} style={check} />
          Pick every one shown
        </label>
        {n > 0 && <span style={{ fontSize: 13, color: "var(--ink-2)" }}>{n} picked</span>}
      </div>
      <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column" }}>
        {shown.map((i) => (
          <li key={i.id} style={{ borderTop: "1px solid var(--surface-2)" }}>
            <label style={{ display: "flex", gap: 10, alignItems: "center", padding: "8px 0", cursor: "pointer", flexWrap: "wrap" }}>
              <input type="checkbox" checked={picked.has(i.id)} onChange={() => toggle(i.id)} style={check} />
              <span style={{ display: "flex", flexDirection: "column", flex: "1 1 160px", minWidth: 0 }}>
                <strong style={{ fontSize: 14, overflowWrap: "anywhere" }}>{i.merchant}</strong>
                <span style={{ fontSize: 12, color: "var(--muted)" }}>{dayWithYear(i.date)}</span>
              </span>
              {i.receipt && (
                <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12, fontWeight: 700, color: "var(--ink-2)" }}>
                  <Receipt size={13} aria-hidden="true" /> Receipt
                </span>
              )}
              <span className="num" style={{ fontWeight: 800, fontSize: 14, marginLeft: "auto" }}>{money(i.amount, { cents: true })}</span>
            </label>
          </li>
        ))}
      </ul>
      {g.items.length > FIRST && (
        <button type="button" className="btn" onClick={() => setAll((a) => !a)} style={{ ...SMALL, alignSelf: "flex-start" }} aria-expanded={all}>
          {all ? `Show the first ${FIRST}` : `Show all ${g.items.length}`}
        </button>
      )}
      {g.count > g.items.length && <div style={{ fontSize: 12, color: "var(--muted)" }}>The newest {g.items.length} of {g.count} are listed.</div>}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", borderTop: "1px solid var(--surface-2)", paddingTop: 10 }}>
        <button type="button" className="btn dark" disabled={busy || n === 0 || !business} aria-expanded={asking} onClick={() => setAsking((a) => !a)} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <Briefcase size={15} aria-hidden="true" /> For the business{n ? ` (${n})` : ""}
        </button>
        {n > 0 ? (
          <ConfirmButton label={`Personal (${n})`} question={`Keep ${n} as personal? They are not suggested again.`} yes="Yes, personal" no="Not now" onConfirm={() => answer("personal")} />
        ) : (
          <button type="button" className="btn" disabled style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><UserRound size={15} aria-hidden="true" /> Personal</button>
        )}
        {n === 0 && <span style={{ fontSize: 12, color: "var(--muted)" }}>Tick purchases first.</span>}
        {n > 0 && !business && <span style={{ fontSize: 12, color: "var(--ink-2)" }}>For the business needs a business on file: <Link to="/business">add it on Business</Link>.</span>}
      </div>
      {asking && n > 0 && business && (
        <div role="group" aria-label="How much was for the business" style={{ display: "flex", flexDirection: "column", gap: 10, padding: "12px 14px", borderRadius: 12, background: "var(--surface-2)" }}>
          <span className="lab">How much of {n === 1 ? "it" : "them"} was for the business</span>
          <div role="radiogroup" aria-label="The business share" style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {([["1", "All of it (100%)"], ["0.5", "Half (50%)"], ["other", "Another share"]] as const).map(([v, word]) => (
              <button key={v} type="button" role="radio" aria-checked={share === v} className={share === v ? "btn dark" : "btn"} onClick={() => setShare(v)} style={SMALL}>{word}</button>
            ))}
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
            {share === "other" && (
              <label style={{ ...FIELD, width: 130 }}>
                <span className="lab">Share, in %</span>
                <input className="input" inputMode="decimal" value={other} onChange={(e) => setOther(e.target.value)} placeholder="30" />
              </label>
            )}
            {businesses.length > 1 && (
              <label style={{ ...FIELD, flex: "1 1 180px" }}>
                <span className="lab">Which business</span>
                <select className="input" value={forBusiness ?? ""} onChange={(e) => setBizId(Number(e.target.value))}>
                  {businesses.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
                </select>
              </label>
            )}
            <button type="button" className="btn dark" disabled={busy || shareValue === null} onClick={() => answer("business")}>
              {busy ? "Saving…" : `Mark ${n} as the business's`}
            </button>
            <button type="button" className="btn" disabled={busy} onClick={() => setAsking(false)}>Not now</button>
          </div>
          {share === "other" && shareValue === null && other.trim() !== "" && <Said text="The share is a number from 1 to 100." tone="error" />}
          <div style={{ fontSize: 12, color: "var(--ink-2)" }}>They go on line {g.line} ({g.words.toLowerCase()}). Only the business share counts.</div>
        </div>
      )}
      {said && <div><Said text={said.text} tone={said.tone} /></div>}
    </section>
  );
}
