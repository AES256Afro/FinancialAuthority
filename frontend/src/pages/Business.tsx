// Business (M56, M57): setting a business up, step by step, and its year so far. With no business yet the page leads
// with sole proprietor beside an LLC; with one, it leads with how many setup steps are done. Every figure is the
// engine's (GET /api/v1/business and /books); this page draws them and never adds one up. The small pieces the
// other business screens share (a rule's source, the not-advice line, which business a page is about) live here.
import { ArrowLeft, Ban, Building2, Car, ChevronDown, ChevronRight, Circle, CircleCheck, FileText, Info, Receipt, Scale, UserRound, type LucideIcon } from "lucide-react";
import { Fragment, useState, type CSSProperties, type FormEvent, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, useApi, type Loaded } from "../api";
import { Empty, Said, Show } from "../components/State";
import { amountClass, money, monthDay } from "../format";
import type { Books, Business as BusinessRow, BusinessIn, BusinessKind, BusinessOverview, CompareRow, NeverItem, SetupStep, Source, TaxLine } from "../types-business";

// ---- shared by the business and tax screens --------------------------------------------------------------------

export const H2: CSSProperties = { margin: 0, fontSize: 15, fontWeight: 800, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" };
export const CARD: CSSProperties = { padding: "16px 20px", display: "flex", flexDirection: "column", gap: 10, minWidth: 0 };
export const FIELD: CSSProperties = { display: "flex", flexDirection: "column", gap: 4, minWidth: 0 };
export const SMALL: CSSProperties = { minHeight: 34, padding: "5px 12px", fontSize: 13 };
export const INLINE_ICON: CSSProperties = { display: "inline-block", verticalAlign: -2, flexShrink: 0 };

/** A date with its year, "Oct 17, 2026": the business screens reach back years, so the weekday alone is not enough. */
export function dayWithYear(iso: string): string {
  return `${monthDay(iso)}, ${iso.slice(0, 4)}`;
}

/** Every rule shown says where it comes from: "From: <the official page> · checked <day>". */
export function SourceLine({ source, style }: { source: Source; style?: CSSProperties }) {
  return (
    <div style={{ fontSize: 12, color: "var(--muted)", overflowWrap: "anywhere", ...style }}>
      From: <a href={source.url} target="_blank" rel="noopener noreferrer">{source.title}</a> · checked {dayWithYear(source.checked)}
    </div>
  );
}

/** The line every business and tax screen ends with, in the engine's words. */
export function NotAdvice({ text }: { text: string }) {
  return (
    <p style={{ margin: 0, fontSize: 12, color: "var(--muted)", display: "flex", gap: 6, alignItems: "flex-start" }}>
      <Info size={13} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2 }} />
      <span>{text}</span>
    </p>
  );
}

export function PageHead({ title, sub, back = false }: { title: string; sub: ReactNode; back?: boolean }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {back && (
        <Link to="/business" style={{ fontSize: 13, fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 4, alignSelf: "flex-start", minHeight: 32 }}>
          <ArrowLeft size={14} aria-hidden="true" /> Business
        </Link>
      )}
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", gap: "4px 12px" }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, letterSpacing: "-0.01em" }}>{title}</h1>
        <div style={{ fontSize: 13, color: "var(--ink-2)", minWidth: 0 }}>{sub}</div>
      </div>
    </div>
  );
}

/** One big figure with what it is. A money figure is blurred under Hide amounts. */
export function Tile({ label, value, sub, accent, isMoney = true }: { label: string; value: ReactNode; sub?: ReactNode; accent: string; isMoney?: boolean }) {
  return (
    <div className="card" style={{ padding: 14, borderTop: `4px solid ${accent}`, minWidth: 0 }}>
      <div className="lab">{label}</div>
      <div className={isMoney ? "num" : undefined} style={{ fontSize: 28, fontWeight: 800, overflowWrap: "anywhere", lineHeight: 1.2 }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

const PILL_TONE = {
  info: { background: "var(--paycheck-soft)", color: "var(--paycheck-ink)" },
  good: { background: "var(--safe-soft)", color: "var(--safe-ink)" },
  warn: { background: "var(--warning-soft)", color: "var(--warning-ink)" },
  plain: { background: "var(--surface-2)", color: "var(--ink-2)" },
} as const;

/** A state as an icon and a word, never colour alone. */
export function Pill({ Icon, word, tone = "plain" }: { Icon: LucideIcon; word: string; tone?: keyof typeof PILL_TONE }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "3px 9px", borderRadius: 999, fontSize: 12, fontWeight: 800, whiteSpace: "nowrap", flexShrink: 0, ...PILL_TONE[tone] }}>
      <Icon size={13} strokeWidth={2.3} aria-hidden="true" style={{ flexShrink: 0 }} />
      {word}
    </span>
  );
}

/** What never counts, each with its source, so hope does not mislead. */
export function NeverList({ items }: { items: NeverItem[] }) {
  if (!items.length) return null;
  return (
    <section className="card" style={CARD} aria-labelledby="never-title">
      <h2 id="never-title" style={H2}><Ban size={16} aria-hidden="true" /> What never counts</h2>
      <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 12 }}>
        {items.map((n) => (
          <li key={n.what} style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: 14 }}>
            <Ban size={15} aria-hidden="true" style={{ flexShrink: 0, marginTop: 3, color: "var(--muted)" }} />
            <div style={{ minWidth: 0 }}>
              <strong>{n.what}.</strong> <span className={amountClass(n.why)}>{n.why}</span>
              <SourceLine source={n.source} style={{ marginTop: 2 }} />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The business a page is about: the one ?b=<id> names, else the first one still running. */
export function useChosenBusiness() {
  const overview = useApi<BusinessOverview>("/api/v1/business", { live: true });
  const [params, setParams] = useSearchParams();
  const all = overview.data?.businesses ?? [];
  const asked = Number(params.get("b")) || null;
  const business = all.find((b) => b.id === asked) ?? all.find((b) => b.active) ?? all[0] ?? null;
  const choose = (id: number) =>
    setParams((p) => {
      const next = new URLSearchParams(p);
      next.set("b", String(id));
      return next;
    }, { replace: true });
  return { overview, business, choose };
}

/** With more than one business, a choice of which one this page shows. */
export function BusinessPick({ businesses, value, onChange }: { businesses: BusinessRow[]; value: number | null; onChange: (id: number) => void }) {
  if (businesses.length < 2) return null;
  return (
    <label style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      <span className="lab">Which business</span>
      <select className="input" value={value ?? ""} onChange={(e) => onChange(Number(e.target.value))} style={{ minHeight: 36, padding: "4px 10px" }}>
        {businesses.map((b) => <option key={b.id} value={b.id}>{b.name}{b.active ? "" : " (closed)"}</option>)}
      </select>
    </label>
  );
}

/** A screen about one business, before there is one: say so, and where to add it. */
export function NeedsBusiness({ children }: { children: ReactNode }) {
  return <Empty to="/business" action="Set up the business →">{children}</Empty>;
}

export const KIND_WORD: Record<BusinessKind, string> = { sole_prop: "Sole proprietor", llc: "LLC" };

// ---- the page ----------------------------------------------------------------------------------------------------

export function Business() {
  const { overview, business, choose } = useChosenBusiness();
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <PageHead
        title="Business"
        sub={business ? <>{business.name} · {KIND_WORD[business.kind]} in {business.state}{business.active ? "" : " · closed"}</> : "Setting a business up, its money, invoices and what may be written off."}
      />
      <Show state={overview} what="your business">
        {(o) => (business ? <WithBusiness o={o} business={business} choose={choose} /> : <NoBusiness o={o} onAdded={choose} />)}
      </Show>
    </div>
  );
}

// ---- before there is a business ----------------------------------------------------------------------------------

function NoBusiness({ o, onAdded }: { o: BusinessOverview; onAdded: (id: number) => void }) {
  return (
    <>
      <CompareCard rows={o.guide.compare} />
      <p className={amountClass(o.guide.bare_minimum)} style={{ margin: 0, fontSize: 15, fontWeight: 800, lineHeight: 1.5, maxWidth: "70ch" }}>{o.guide.bare_minimum}</p>
      <section className="card" style={CARD} aria-labelledby="add-title">
        <h2 id="add-title" style={H2}>Add your business</h2>
        <div className={amountClass(o.guide.said)} style={{ fontSize: 13, color: "var(--ink-2)" }}>{o.guide.said}</div>
        <BusinessForm start={null} lines={o.lines} onSaved={onAdded} />
      </section>
      {o.guide.steps.length > 0 && (
        <section className="card" style={CARD} aria-labelledby="after-title">
          <h2 id="after-title" style={H2}>The steps, in order</h2>
          <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Once the business is added, each step opens here with how to do it and where the rule comes from.</div>
          <ol style={{ margin: 0, paddingLeft: 22, display: "flex", flexDirection: "column", gap: 4, fontSize: 14 }}>
            {o.guide.steps.map((s) => <li key={s.id}>{s.title}</li>)}
          </ol>
        </section>
      )}
      <NotAdvice text={o.guide.not_advice} />
    </>
  );
}

function ColumnHead({ Icon, title, accent }: { Icon: LucideIcon; title: string; accent: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 6, paddingLeft: 10, borderLeft: `4px solid ${accent}`, paddingBottom: 6, minWidth: 0 }}>
      <span style={{ display: "inline-flex", width: 40, height: 40, borderRadius: 999, alignItems: "center", justifyContent: "center", background: "var(--surface-2)", color: "var(--ink)" }}>
        <Icon size={22} aria-hidden="true" />
      </span>
      <strong style={{ fontSize: 15, overflowWrap: "anywhere" }}>{title}</strong>
    </div>
  );
}

/** The picture before there is a business: the two ways to run one, side by side, row by row. */
function CompareCard({ rows }: { rows: CompareRow[] }) {
  const cell: CSSProperties = { fontSize: 13, lineHeight: 1.45, paddingLeft: 10, minWidth: 0, overflowWrap: "anywhere" };
  return (
    <section className="card" style={CARD} aria-labelledby="compare-title">
      <h2 id="compare-title" style={H2}>Sole proprietor or LLC</h2>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", columnGap: 12 }}>
        <ColumnHead Icon={UserRound} title="Sole proprietor" accent="var(--paycheck)" />
        <ColumnHead Icon={Building2} title="LLC" accent="var(--debt)" />
        {rows.map((r) => (
          <Fragment key={r.what}>
            <div className="lab" style={{ gridColumn: "1 / -1", padding: "10px 0 4px", borderTop: "1px solid var(--surface-2)", marginTop: 6 }}>{r.what}</div>
            <div className={amountClass(r.sole_prop)} style={{ ...cell, borderLeft: "4px solid var(--paycheck)" }}>{r.sole_prop}</div>
            <div className={amountClass(r.llc)} style={{ ...cell, borderLeft: "4px solid var(--debt)" }}>{r.llc}</div>
          </Fragment>
        ))}
      </div>
    </section>
  );
}

// ---- the add and change form -------------------------------------------------------------------------------------

interface AccountChoice {
  id: number;
  name: string;
  institution: string;
  kind: string;
  active?: boolean;
}

function BusinessForm({ start, lines, onSaved, onCancel }: { start: BusinessRow | null; lines: TaxLine[]; onSaved?: (id: number) => void; onCancel?: () => void }) {
  const accounts = useApi<{ accounts: AccountChoice[] }>("/api/v1/accounts");
  const [name, setName] = useState(start?.name ?? "");
  const [kind, setKind] = useState<BusinessKind>(start?.kind ?? "sole_prop");
  const [state, setState] = useState(start?.state ?? "TX");
  const [startedOn, setStartedOn] = useState(start?.started_on ?? "");
  const [what, setWhat] = useState(start?.what_it_does ?? "");
  const [ein, setEin] = useState(start?.ein_on_file ?? false);
  const [assumed, setAssumed] = useState(start?.assumed_name ?? false);
  const [account, setAccount] = useState(start?.account_id ? String(start.account_id) : "");
  const [sqft, setSqft] = useState(start?.home_office_sqft ? String(start.home_office_sqft) : "");
  const [active, setActive] = useState(start?.active ?? true);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  const homeOffice = lines.find((l) => l.key === "home_office");

  async function save(e: FormEvent) {
    e.preventDefault();
    const st = state.trim().toUpperCase();
    if (!name.trim()) return setNote({ tone: "error", text: "Give the business a name. Your own name is fine." });
    if (!/^[A-Z]{2}$/.test(st)) return setNote({ tone: "error", text: "The state is two letters, like TX." });
    const space = sqft.trim() ? Number(sqft.trim()) : null;
    if (space !== null && (!Number.isInteger(space) || space < 0 || space > 10000)) return setNote({ tone: "error", text: "The home office is a whole number of square feet, like 120, or left empty." });
    const body: BusinessIn = {
      name: name.trim(), kind, state: st, started_on: startedOn || null, what_it_does: what.trim(), ein_on_file: ein, assumed_name: assumed,
      account_id: account ? Number(account) : null, home_office_sqft: space, active,
    };
    setBusy(true);
    setNote(null);
    try {
      const out = await api<BusinessOverview>(start ? `/api/v1/business/${start.id}` : "/api/v1/business", { method: start ? "PUT" : "POST", body: JSON.stringify(body) });
      setNote({ tone: "info", text: start ? "Saved." : `Added ${body.name}.` });
      const kept = start ?? out.businesses[out.businesses.length - 1];
      if (kept) onSaved?.(kept.id);
    } catch (err) {
      setNote({ tone: "error", text: `Couldn't save it: ${(err as Error).message}` });
    } finally {
      setBusy(false);
    }
  }

  const accountList = (accounts.data?.accounts ?? []).filter((a) => a.active !== false || String(a.id) === account);
  return (
    <form onSubmit={save} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div role="radiogroup" aria-label="How the business is run" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {(["sole_prop", "llc"] as const).map((k) => {
          const Icon = k === "sole_prop" ? UserRound : Building2;
          return (
            <button key={k} type="button" role="radio" aria-checked={kind === k} className={kind === k ? "btn dark" : "btn"} onClick={() => setKind(k)} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <Icon size={16} aria-hidden="true" /> {KIND_WORD[k]}
            </button>
          );
        })}
      </div>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <label style={{ ...FIELD, flex: "2 1 220px" }}>
          <span className="lab">Its name</span>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} placeholder="Your own name works" required />
        </label>
        <label style={{ ...FIELD, flex: "0 1 90px" }}>
          <span className="lab">State</span>
          <input className="input" value={state} onChange={(e) => setState(e.target.value.toUpperCase())} maxLength={2} aria-describedby="state-hint" style={{ textTransform: "uppercase" }} />
        </label>
        <label style={{ ...FIELD, flex: "1 1 160px" }}>
          <span className="lab">The day it began</span>
          <input className="input" type="date" value={startedOn} onChange={(e) => setStartedOn(e.target.value)} />
        </label>
      </div>
      <span id="state-hint" className="sr-only">Two letters, like TX</span>
      <label style={FIELD}>
        <span className="lab">What it does, in a few words</span>
        <input className="input" value={what} onChange={(e) => setWhat(e.target.value)} maxLength={300} placeholder="Web design for small shops" />
      </label>
      <label style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: 14 }}>
        <input type="checkbox" checked={ein} onChange={(e) => setEin(e.target.checked)} style={{ width: 20, height: 20, margin: "1px 0 0", flexShrink: 0 }} />
        <span><strong>I have an EIN</strong> (the business's number from the IRS). <span style={{ color: "var(--ink-2)" }}>The app only notes that you have one. It never asks for the number and never keeps it.</span></span>
      </label>
      <label style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: 14 }}>
        <input type="checkbox" checked={assumed} onChange={(e) => setAssumed(e.target.checked)} style={{ width: 20, height: 20, margin: "1px 0 0", flexShrink: 0 }} />
        <span><strong>An assumed name (DBA) is filed for it.</strong> <span style={{ color: "var(--ink-2)" }}>Only needed when you work under a name other than your own.</span></span>
      </label>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <label style={{ ...FIELD, flex: "2 1 240px" }}>
          <span className="lab">The account kept for the business</span>
          <select className="input" value={account} onChange={(e) => setAccount(e.target.value)}>
            <option value="">None yet</option>
            {accountList.map((a) => <option key={a.id} value={a.id}>{a.institution ? `${a.institution} · ` : ""}{a.name}</option>)}
          </select>
        </label>
        <label style={{ ...FIELD, flex: "1 1 160px" }}>
          <span className="lab">Home office, square feet</span>
          <input className="input" value={sqft} onChange={(e) => setSqft(e.target.value)} inputMode="numeric" placeholder="none" aria-describedby="sqft-hint" />
        </label>
      </div>
      <div id="sqft-hint" style={{ fontSize: 12, color: "var(--ink-2)", marginTop: -6 }}>Leave the home office empty unless it is {homeOffice?.examples ?? "a space used only and regularly for the business"}.</div>
      {start && (
        <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 14 }}>
          <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} style={{ width: 20, height: 20, margin: 0 }} />
          <span>The business is still running</span>
        </label>
      )}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button type="submit" className="btn dark" disabled={busy}>{busy ? "Saving…" : start ? "Save the changes" : "Add the business"}</button>
        {onCancel && <button type="button" className="btn" onClick={onCancel} disabled={busy}>Close</button>}
        {note && <Said text={note.text} tone={note.tone} />}
      </div>
    </form>
  );
}

// ---- with a business -----------------------------------------------------------------------------------------------

function WithBusiness({ o, business, choose }: { o: BusinessOverview; business: BusinessRow; choose: (id: number) => void }) {
  const books = useApi<Books>(`/api/v1/business/${business.id}/books`, { live: true });
  const [editing, setEditing] = useState(false);
  const q = `?b=${business.id}`;
  const g = o.guide;
  const next = g.steps.find((s) => !s.done);
  return (
    <>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <BusinessPick businesses={o.businesses} value={business.id} onChange={choose} />
        <button type="button" className="btn" aria-expanded={editing} onClick={() => setEditing((x) => !x)} style={SMALL}>{editing ? "Close the details" : "Change the details"}</button>
      </div>
      {editing && (
        <section className="card" style={CARD} aria-labelledby="edit-title">
          <h2 id="edit-title" style={H2}>{business.name}: the details</h2>
          <BusinessForm key={business.id} start={business} lines={o.lines} onCancel={() => setEditing(false)} />
        </section>
      )}

      <section className="card" style={{ ...CARD, flexDirection: "row", alignItems: "center", gap: 16, flexWrap: "wrap" }} aria-labelledby="setup-title">
        <Ring done={g.done} total={g.total} />
        <div style={{ flex: "1 1 150px", minWidth: 0, display: "flex", flexDirection: "column", gap: 4 }}>
          <h2 id="setup-title" style={H2}>Setting it up</h2>
          <div className={amountClass(g.said)} style={{ fontSize: 15, fontWeight: 700 }}>{g.said}</div>
          {next ? <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Next: {next.title}</div> : <div><Pill Icon={CircleCheck} word="Every step done" tone="good" /></div>}
        </div>
      </section>

      <section className="card" style={CARD} aria-labelledby="steps-title">
        <h2 id="steps-title" style={H2}>The steps</h2>
        <ol style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column" }}>
          {g.steps.map((s, i) => <StepRow key={s.id} step={s} n={i + 1} business={business} onEdit={() => setEditing(true)} />)}
        </ol>
      </section>

      <ThisYear books={books} q={q} />
      <Payers books={books} q={q} />
      <BigLinks q={q} />

      <details className="card" style={{ padding: "12px 20px" }}>
        <summary style={{ cursor: "pointer", fontWeight: 800, minHeight: 32, display: "flex", alignItems: "center" }}>Add another business</summary>
        <div style={{ paddingTop: 10 }}>
          <BusinessForm start={null} lines={o.lines} onSaved={choose} />
        </div>
      </details>
      <NotAdvice text={g.not_advice} />
    </>
  );
}

/** How many setup steps are done, as a ring; the same figure is said in words beside it. */
function Ring({ done, total }: { done: number; total: number }) {
  const r = 34;
  const around = 2 * Math.PI * r;
  const part = total > 0 ? Math.min(1, done / total) : 0;
  return (
    <svg width={92} height={92} viewBox="0 0 92 92" role="img" aria-label={`${done} of ${total} steps done`} style={{ flexShrink: 0 }}>
      <circle cx={46} cy={46} r={r} fill="none" stroke="var(--surface-2)" strokeWidth={11} />
      {part > 0 && <circle cx={46} cy={46} r={r} fill="none" stroke="var(--paycheck)" strokeWidth={11} strokeLinecap="round" strokeDasharray={`${around * part} ${around}`} transform="rotate(-90 46 46)" />}
      <text x={46} y={46} textAnchor="middle" fontSize={20} fontWeight={800} fill="var(--ink)">{done}/{total}</text>
      <text x={46} y={61} textAnchor="middle" fontSize={10} fontWeight={700} fill="var(--muted)">steps</text>
    </svg>
  );
}

function StepRow({ step, n, business, onEdit }: { step: SetupStep; n: number; business: BusinessRow; onEdit: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Some steps are done by the business's own details rather than a tick: adding it, the account kept for it and a
  // filed assumed name. Those change on the details. The EIN is the business's "I have an EIN" box, ticked from here.
  const byDetails = step.id === "record" || (step.id === "account" && business.account_id !== null) || (step.id === "assumed_name" && business.assumed_name);

  async function tick(done: boolean) {
    setBusy(true);
    setError(null);
    try {
      if (step.id === "ein") {
        const body: BusinessIn = { ...business, ein_on_file: done };
        await api(`/api/v1/business/${business.id}`, { method: "PUT", body: JSON.stringify(body) });
      } else {
        await api(`/api/v1/business/setup/${encodeURIComponent(step.id)}`, { method: "POST", body: JSON.stringify({ done }) });
      }
      setOpen(false);
    } catch (err) {
      setError(`Couldn't save that: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  const row: CSSProperties = { borderTop: n > 1 ? "1px solid var(--surface-2)" : undefined, padding: "10px 0" };
  if (step.done && !open) {
    return (
      <li style={row}>
        <button type="button" aria-expanded={false} onClick={() => setOpen(true)} style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", background: "none", border: 0, padding: 0, font: "inherit", color: "var(--ink-2)", cursor: "pointer", textAlign: "left", minHeight: 32 }}>
          <Pill Icon={CircleCheck} word="Done" tone="good" />
          <span style={{ flex: "1 1 auto", minWidth: 0, fontSize: 14 }}>{step.title}</span>
          <ChevronRight size={16} aria-hidden="true" style={{ flexShrink: 0 }} />
        </button>
      </li>
    );
  }
  return (
    <li style={{ ...row, display: "flex", gap: 10, alignItems: "flex-start" }}>
      <span aria-hidden="true" style={{ flexShrink: 0, marginTop: 1, color: step.done ? "var(--safe-ink)" : "var(--ink-2)" }}>
        {step.done ? <CircleCheck size={20} /> : <Circle size={20} />}
      </span>
      <div style={{ flex: "1 1 auto", minWidth: 0, display: "flex", flexDirection: "column", gap: 6 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <strong style={{ fontSize: 15, minWidth: 0 }}>{n}. {step.title}</strong>
          {step.for === "llc" && <Pill Icon={Building2} word="LLC only" />}
          {step.done && <Pill Icon={CircleCheck} word="Done" tone="good" />}
          {step.done && (
            <button type="button" aria-expanded={true} onClick={() => setOpen(false)} style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 4, background: "none", border: 0, font: "inherit", fontSize: 12, fontWeight: 700, color: "var(--ink-2)", cursor: "pointer", minHeight: 32 }}>
              <ChevronDown size={14} aria-hidden="true" /> Fold it
            </button>
          )}
        </div>
        <div className={amountClass(step.how)} style={{ fontSize: 14, lineHeight: 1.5, maxWidth: "75ch" }}>{step.how}</div>
        <SourceLine source={step.source} />
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {step.id === "record" ? null : byDetails ? (
            <button type="button" className="btn" onClick={onEdit} style={SMALL}>Change it on the details</button>
          ) : step.done ? (
            <button type="button" className="btn" disabled={busy} onClick={() => tick(false)} style={SMALL}>{busy ? "Saving…" : step.id === "ein" ? "I don't have one yet" : "Mark it not done"}</button>
          ) : (
            <button type="button" className="btn dark" disabled={busy} onClick={() => tick(true)} style={{ ...SMALL, display: "inline-flex", alignItems: "center", gap: 6 }}>
              <CircleCheck size={15} aria-hidden="true" /> {busy ? "Saving…" : step.id === "ein" ? "I have an EIN now" : "Mark it done"}
            </button>
          )}
          {error && <Said text={error} tone="error" />}
        </div>
      </div>
    </li>
  );
}

/** The year so far, by the lines of Schedule C. Bars are drawn in proportion to the engine's amounts. */
function ThisYear({ books, q }: { books: Loaded<Books>; q: string }) {
  return (
    <section className="card" style={CARD} aria-labelledby="year-title">
      <h2 id="year-title" style={H2}>This year{books.data ? `, ${books.data.year}` : ""}</h2>
      <Show state={books} what="the business's year">
        {(b) => {
          const most = Math.max(0, ...b.lines.map((l) => l.amount));
          return (
            <>
              <div style={{ fontSize: 13, color: "var(--ink-2)" }}>From deposits and purchases marked as the business's, invoices paid, and the miles logged.</div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 10 }}>
                <Tile label="Came in" value={money(b.income, { cents: true })} accent="var(--paycheck)" />
                <Tile label="Costs" value={money(b.costs, { cents: true })} accent="var(--bill)" />
                <Tile label="Profit" value={money(b.profit, { cents: true })} accent="var(--saving)" sub={b.profit < 0 ? "a loss so far" : undefined} />
              </div>
              {b.lines.length === 0 ? (
                <div style={{ fontSize: 13, color: "var(--ink-2)" }}>No costs yet. Mark purchases as the business's on <Link to={`/business/write-offs${q}`}>Write-offs</Link> or on <Link to="/transactions">Transactions</Link>.</div>
              ) : (
                <ul aria-label="Costs by tax line" style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 10 }}>
                  {b.lines.map((l) => (
                    <li key={l.key} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap", fontSize: 13 }}>
                        <strong style={{ minWidth: 0 }}>{l.words}</strong>
                        <span style={{ color: "var(--muted)", fontSize: 12 }}>line {l.line} · {l.count} {l.count === 1 ? "entry" : "entries"}{l.rule ? <> · <a href={l.rule.url} target="_blank" rel="noopener noreferrer">the rule</a></> : null}</span>
                        <span className="num" style={{ marginLeft: "auto", fontWeight: 800 }}>{money(l.amount, { cents: true })}</span>
                      </div>
                      <div style={{ height: 10, background: "var(--surface-2)", borderRadius: 4, overflow: "hidden" }} aria-hidden="true">
                        <div style={{ width: `${most > 0 ? (Math.max(0, l.amount) / most) * 100 : 0}%`, height: "100%", background: "var(--bill)", borderRadius: 4 }} />
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 14, borderTop: "1px solid var(--surface-2)", paddingTop: 10 }}>
                <div>Self-employment tax on this profit: <strong className="num">{money(b.se_tax, { cents: true })}</strong></div>
                <div className={amountClass(b.se_note)} style={{ fontSize: 13, color: "var(--ink-2)" }}>{b.se_note}</div>
                <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
                  <Car size={14} aria-hidden="true" style={INLINE_ICON} /> {b.trips} {b.trips === 1 ? "trip" : "trips"}, {b.miles} business miles logged. <Link to={`/business/miles${q}`}>Miles →</Link>
                </div>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                {b.sources.map((s) => <SourceLine key={s.url} source={s} />)}
              </div>
            </>
          );
        }}
      </Show>
    </section>
  );
}

function Payers({ books, q }: { books: Loaded<Books>; q: string }) {
  const b = books.data;
  if (!b) return null;
  return (
    <section className="card" style={CARD} aria-labelledby="payers-title">
      <h2 id="payers-title" style={H2}><FileText size={16} aria-hidden="true" /> Who paid you, and the 1099s to expect</h2>
      <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
        Anyone who paid you <strong className="num">{money(b.form_1099_threshold)}</strong> or more for work in {b.year} sends you a 1099-NEC. The income counts whether one comes or not.
      </div>
      {b.payers.length === 0 ? (
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>No invoice paid yet this year. Payers show here once an invoice is marked paid on <Link to={`/business/invoices${q}`}>Invoices</Link>.</div>
      ) : (
        <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column" }}>
          {b.payers.map((p, i) => (
            <li key={p.payer} style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", padding: "8px 0", borderTop: i ? "1px solid var(--surface-2)" : undefined }}>
              <strong style={{ flex: "1 1 140px", minWidth: 0, overflowWrap: "anywhere" }}>{p.payer}</strong>
              <span className="num" style={{ fontWeight: 800 }}>{money(p.paid, { cents: true })}</span>
              {p.expect_1099 ? <Pill Icon={FileText} word="Expect a 1099-NEC" tone="info" /> : <Pill Icon={Circle} word="Below the 1099 line" />}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const LINKS: { to: string; label: string; Icon: LucideIcon; hint: string; withBusiness: boolean }[] = [
  { to: "/business/write-offs", label: "Write-offs", Icon: Receipt, hint: "Purchases that could be business costs, and what never counts.", withBusiness: true },
  { to: "/business/invoices", label: "Invoices", Icon: FileText, hint: "Bill the people you work for, and match their payments.", withBusiness: true },
  { to: "/business/miles", label: "Miles", Icon: Car, hint: "Log business trips; each counts at the rate for its day.", withBusiness: true },
  { to: "/taxes", label: "Taxes", Icon: Scale, hint: "The tax year: its dates, deductions to check, and what never counts.", withBusiness: false },
];

function BigLinks({ q }: { q: string }) {
  return (
    <nav aria-label="Business screens" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 10 }}>
      {LINKS.map((l) => (
        <Link key={l.to} to={`${l.to}${l.withBusiness ? q : ""}`} className="card" style={{ padding: "14px 16px", display: "flex", gap: 12, alignItems: "flex-start", textDecoration: "none", color: "var(--ink)", minHeight: 64, minWidth: 0 }}>
          <span style={{ display: "inline-flex", width: 40, height: 40, borderRadius: 10, alignItems: "center", justifyContent: "center", background: "var(--surface-2)", flexShrink: 0 }}>
            <l.Icon size={20} aria-hidden="true" />
          </span>
          <span style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
            <strong style={{ fontSize: 16 }}>{l.label} →</strong>
            <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{l.hint}</span>
          </span>
        </Link>
      ))}
    </nav>
  );
}
