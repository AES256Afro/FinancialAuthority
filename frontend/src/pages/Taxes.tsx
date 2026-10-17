// Taxes (M59): the tax year prepared as it goes, not in April. Its dates lead, as a line with a dot for each, the next
// one marked. Then the engine's sentence, the standard deduction beside what could be itemized, the deductions to
// check (with the questions that decide them), each business's profit, how far back a return can be amended, and
// what never counts. Every figure is the engine's (GET /api/v1/taxes); this page draws them.
import { Briefcase, CalendarClock, CircleCheck, CircleHelp, CircleX, History } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { Said, Show } from "../components/State";
import { amountClass, localToday, money } from "../format";
import type { FilingStatus, TaxCheck, TaxDate, TaxYear } from "../types-business";
import { CARD, FIELD, H2, NeverList, NotAdvice, PageHead, Pill, SMALL, SourceLine, dayWithYear } from "./Business";

const FILING: { value: FilingStatus; word: string }[] = [
  { value: "single", word: "Single" },
  { value: "married_joint", word: "Married filing jointly" },
  { value: "head_of_household", word: "Head of household" },
];

const STATE_LOOK = {
  ask: { Icon: CircleHelp, word: "Ask", tone: "info" as const },
  likely: { Icon: CircleCheck, word: "Likely", tone: "good" as const },
  no: { Icon: CircleX, word: "Doesn't qualify", tone: "plain" as const },
};

export function Taxes() {
  const thisYear = Number(localToday().slice(0, 4));
  const [year, setYear] = useState<number | null>(null);
  const tax = useApi<TaxYear>(year ? `/api/v1/taxes/${year}` : "/api/v1/taxes", { live: true });
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <PageHead title="Taxes" sub="The tax year as it goes: its dates, deductions to check, and what never counts." />
      <div role="radiogroup" aria-label="Which tax year" style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        <button type="button" role="radio" aria-checked={year === null} className={year === null ? "btn dark" : "btn"} onClick={() => setYear(null)} style={SMALL}>This year, {thisYear}</button>
        <button type="button" role="radio" aria-checked={year === thisYear - 1} className={year === thisYear - 1 ? "btn dark" : "btn"} onClick={() => setYear(thisYear - 1)} style={SMALL}>Last year, {thisYear - 1}</button>
      </div>
      <Show state={tax} what="the tax year">
        {(t) => (
          <>
            <Timeline t={t} />
            {t.said && <p className={amountClass(t.said)} style={{ margin: 0, fontSize: 15, lineHeight: 1.55, fontWeight: 600, maxWidth: "75ch" }}>{t.said}</p>}
            <Deduction t={t} />
            {t.checks.map((c) => <CheckCard key={c.key} c={c} />)}
            <Businesses t={t} />
            <section className="card" style={CARD} aria-labelledby="amend-title">
              <h2 id="amend-title" style={H2}><History size={16} aria-hidden="true" /> Fixing an earlier return</h2>
              <div style={{ fontSize: 14, lineHeight: 1.5 }}>{t.amend.words}</div>
              {t.amend.years.length > 0 && (
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", fontSize: 13 }}>
                  <span style={{ color: "var(--ink-2)" }}>The years before {t.year}:</span>
                  {t.amend.years.map((y) => <span key={y} style={{ padding: "2px 10px", borderRadius: 999, background: "var(--surface-2)", fontWeight: 700 }}>{y}</span>)}
                </div>
              )}
              <SourceLine source={t.amend.source} />
            </section>
            <NeverList items={t.never} />
            {t.sources.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                {t.sources.map((s) => <SourceLine key={s.url} source={s} />)}
              </div>
            )}
            <NotAdvice text={t.not_advice} />
          </>
        )}
      </Show>
    </div>
  );
}

/** The year's dates as a line with a dot for each: past ones muted, the next one marked. */
function Timeline({ t }: { t: TaxYear }) {
  const next = t.upcoming[0]?.due ?? null;
  return (
    <section className="card" style={CARD} aria-labelledby="dates-title">
      <h2 id="dates-title" style={H2}><CalendarClock size={16} aria-hidden="true" /> The {t.year} tax year, date by date</h2>
      <ol style={{ margin: 0, padding: 0, listStyle: "none", position: "relative" }}>
        {t.dates.map((d, i) => <DateRow key={`${d.due}-${i}`} d={d} past={next === null || d.due < next} isNext={d.due === next} last={i === t.dates.length - 1} />)}
      </ol>
      {next === null && <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Every date for {t.year} has passed.</div>}
    </section>
  );
}

function DateRow({ d, past, isNext, last }: { d: TaxDate; past: boolean; isNext: boolean; last: boolean }) {
  const dot = isNext ? 18 : 12;
  return (
    <li style={{ display: "flex", gap: 12, alignItems: "stretch", minWidth: 0 }}>
      <div aria-hidden="true" style={{ width: 20, flexShrink: 0, display: "flex", flexDirection: "column", alignItems: "center" }}>
        <span style={{ width: dot, height: dot, borderRadius: 999, marginTop: isNext ? 10 : 12, flexShrink: 0, boxSizing: "border-box", background: isNext ? "var(--paycheck)" : past ? "var(--surface-2)" : "var(--surface)", border: `2px solid ${isNext ? "var(--paycheck)" : past ? "var(--muted)" : "var(--ink-2)"}` }} />
        {!last && <span style={{ flex: "1 1 auto", width: 2, background: "var(--hairline)", minHeight: 12 }} />}
      </div>
      <div style={{ flex: "1 1 auto", minWidth: 0, padding: isNext ? "8px 12px" : "8px 0", margin: "2px 0", borderRadius: 10, background: isNext ? "var(--paycheck-soft)" : undefined, color: past ? "var(--muted)" : "var(--ink)" }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <strong style={{ fontSize: 14 }}>{dayWithYear(d.due)}</strong>
          {isNext && <Pill Icon={CalendarClock} word="Next" tone="info" />}
          {past && <span style={{ fontSize: 12 }}>passed</span>}
        </div>
        <div className={amountClass(d.what)} style={{ fontSize: 13, lineHeight: 1.45, color: past ? "var(--muted)" : "var(--ink-2)" }}>{d.what}</div>
      </div>
    </li>
  );
}

/** The standard deduction beside what the records show could be itemized, as two bars on one scale. */
function Deduction({ t }: { t: TaxYear }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  const standard = t.standard_deduction;
  const most = Math.max(standard ?? 0, t.itemizable, 0);
  async function setStatus(value: string) {
    setBusy(true);
    setNote(null);
    try {
      await api("/api/v1/taxes/answers", { method: "POST", body: JSON.stringify({ answers: { filing_status: value } }) });
      setNote({ text: "Saved.", tone: "info" });
    } catch (err) {
      setNote({ text: `Couldn't save that: ${(err as Error).message}`, tone: "error" });
    } finally {
      setBusy(false);
    }
  }
  const bars = [
    { label: "Standard deduction", value: standard, colour: "var(--paycheck)" },
    { label: "What you could itemize", value: t.itemizable, colour: "var(--saving)" },
  ];
  return (
    <section className="card" style={CARD} aria-labelledby="deduction-title">
      <h2 id="deduction-title" style={H2}>Standard deduction vs what you could itemize</h2>
      <div style={{ display: "flex", gap: 8, alignItems: "flex-end", flexWrap: "wrap" }}>
        <label style={{ ...FIELD, flex: "0 1 280px" }}>
          <span className="lab">Your filing status</span>
          <select className="input" value={t.filing_status} disabled={busy} onChange={(e) => setStatus(e.target.value)}>
            {FILING.map((f) => <option key={f.value} value={f.value}>{f.word}</option>)}
          </select>
        </label>
        {note && <Said text={note.text} tone={note.tone} />}
      </div>
      <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 10 }}>
        {bars.map((b) => (
          <li key={b.label} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap", fontSize: 14 }}>
              <strong>{b.label}</strong>
              <span className="num" style={{ marginLeft: "auto", fontWeight: 800, fontSize: 16 }}>{b.value === null ? "not known for this year" : money(b.value, { cents: true })}</span>
            </div>
            <div style={{ height: 16, background: "var(--surface-2)", borderRadius: 4, overflow: "hidden" }} aria-hidden="true">
              <div style={{ width: `${most > 0 && b.value !== null ? (Math.max(0, b.value) / most) * 100 : 0}%`, height: "100%", background: b.colour, borderRadius: 4 }} />
            </div>
          </li>
        ))}
      </ul>
      {t.itemizable_categories.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4, borderTop: "1px solid var(--surface-2)", paddingTop: 10 }}>
          <span className="lab">Spending in the categories a return asks about</span>
          <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column" }}>
            {t.itemizable_categories.map((c) => (
              <li key={c.name} style={{ display: "flex", gap: 10, flexWrap: "wrap", fontSize: 13, padding: "4px 0" }}>
                <span style={{ flex: "1 1 160px", minWidth: 0 }}>{c.name}{c.itemizable === false ? <span style={{ color: "var(--muted)", fontSize: 12 }}> · not itemized</span> : c.itemizable ? <span style={{ color: "var(--ink-2)", fontSize: 12 }}> · can be itemized</span> : null}</span>
                <span style={{ color: "var(--muted)" }}>{c.count} {c.count === 1 ? "purchase" : "purchases"}</span>
                <span className="num" style={{ fontWeight: 700 }}>{money(c.total, { cents: true })}</span>
              </li>
            ))}
          </ul>
          <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Only the ones marked “can be itemized” count toward the bar above (medical only past 7.5% of income); a tax preparer says what applies to you. Tag a purchase “tax” on <Link to="/transactions">Transactions</Link> to keep it with the year.</div>
        </div>
      )}
    </section>
  );
}

function CheckCard({ c }: { c: TaxCheck }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const l = STATE_LOOK[c.state];
  async function answer(key: string, value: boolean | null) {
    setBusy(key);
    setNote(null);
    try {
      await api("/api/v1/taxes/answers", { method: "POST", body: JSON.stringify({ answers: { [key]: value } }) });
    } catch (err) {
      setNote(`Couldn't save that: ${(err as Error).message}`);
    } finally {
      setBusy(null);
    }
  }
  return (
    <section className="card" style={CARD} aria-labelledby={`check-${c.key}`}>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <h2 id={`check-${c.key}`} style={{ ...H2, flex: "1 1 200px" }}>{c.title}</h2>
        <Pill Icon={l.Icon} word={l.word} tone={l.tone} />
      </div>
      <div className={amountClass(c.words)} style={{ fontSize: 14, lineHeight: 1.5, maxWidth: "75ch" }}>{c.words}</div>
      {c.questions.length > 0 && (
        <ol style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column" }}>
          {c.questions.map((q, i) => (
            <li key={q.key} style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", padding: "8px 0", borderTop: "1px solid var(--surface-2)" }}>
              <span style={{ flex: "1 1 220px", minWidth: 0, fontSize: 14 }}>{i + 1}. {q.text}</span>
              <span role="group" aria-label={q.text} style={{ display: "inline-flex", gap: 6 }}>
                {([true, false] as const).map((v) => {
                  const on = q.answer === v;
                  return (
                    <button key={String(v)} type="button" aria-pressed={on} className={on ? "btn dark" : "btn"} disabled={busy === q.key} onClick={() => answer(q.key, on ? null : v)} style={{ ...SMALL, minWidth: 64 }}>
                      {v ? "Yes" : "No"}
                    </button>
                  );
                })}
              </span>
            </li>
          ))}
        </ol>
      )}
      {c.loans_seen.length > 0 && <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Car loans the app sees in your bills and debts: {c.loans_seen.join(", ")}.</div>}
      {note && <Said text={note} tone="error" />}
      <SourceLine source={c.source} />
    </section>
  );
}

function Businesses({ t }: { t: TaxYear }) {
  return (
    <section className="card" style={CARD} aria-labelledby="biz-title">
      <h2 id="biz-title" style={H2}><Briefcase size={16} aria-hidden="true" /> The business{t.businesses.length > 1 ? "es" : ""} in {t.year}</h2>
      {t.businesses.length === 0 ? (
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>No business on file. If someone pays you for work, <Link to="/business">set the business up on Business</Link>; its profit and self-employment tax then show here.</div>
      ) : (
        <>
          {t.businesses.map((b, i) => (
            <div key={`${b.business}-${i}`} style={{ display: "flex", flexDirection: "column", gap: 4, paddingTop: 8, borderTop: "1px solid var(--surface-2)" }}>
              <strong style={{ fontSize: 14 }}>{b.business}</strong>
              <div style={{ display: "flex", gap: "4px 16px", flexWrap: "wrap", fontSize: 14 }}>
                <span>Profit: <strong className="num">{money(b.profit, { cents: true })}</strong></span>
                <span>Self-employment tax: <strong className="num">{money(b.se_tax, { cents: true })}</strong></span>
              </div>
              <div className={amountClass(b.se_note)} style={{ fontSize: 12, color: "var(--ink-2)" }}>{b.se_note}</div>
            </div>
          ))}
          {t.businesses.length > 1 && (
            <div style={{ display: "flex", gap: "4px 16px", flexWrap: "wrap", fontSize: 14, paddingTop: 8, borderTop: "1px solid var(--surface-2)" }}>
              <span>All of them: profit <strong className="num">{money(t.profit, { cents: true })}</strong></span>
              <span>self-employment tax <strong className="num">{money(t.se_tax, { cents: true })}</strong></span>
            </div>
          )}
          <Link to="/business" style={{ fontSize: 13, fontWeight: 700 }}>Business →</Link>
        </>
      )}
    </section>
  );
}
