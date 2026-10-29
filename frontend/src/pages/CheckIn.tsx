// The weekly check-in: five short questions from the ledger, then one line against last week. Answers are
// kept; a questioned charge gets a note on its row. Nothing is cancelled, moved or sent.
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { Failed, Loading, Said } from "../components/State";
import { StatusPill } from "../components/Status";
import { amountClass, money, shortDate } from "../format";
import type { CheckIn as CheckInData } from "../guide";

function Q({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
        <span className="num" aria-hidden="true" style={{ fontWeight: 800, color: "var(--muted)" }}>{n}</span>
        <h2 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>{title}</h2>
      </div>
      {children}
    </section>
  );
}

function Choice<T extends string | boolean>({ value, options, onChange, label }: { value: T | null | undefined; options: { v: T; text: string }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div role="radiogroup" aria-label={label} style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
      {options.map((o) => <button key={String(o.v)} type="button" role="radio" aria-checked={value === o.v} className={`btn${value === o.v ? " dark" : ""}`} onClick={() => onChange(o.v)}>{o.text}</button>)}
    </div>
  );
}

export function CheckIn() {
  const ci = useApi<CheckInData>("/api/v1/checkin");
  const [flagged, setFlagged] = useState<number[]>([]);
  const [sub, setSub] = useState<string | null>(null);
  const [card, setCard] = useState<boolean | null>(null);
  const [saved, setSaved] = useState<boolean | null>(null);
  const [note, setNote] = useState("");
  const [result, setResult] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const a = ci.data?.answers;
    if (a) {
      setFlagged(a.flagged ?? []);
      setSub(a.subscription ?? null);
      setCard(a.card_paid ?? null);
      setSaved(a.saved ?? null);
      setNote(a.note ?? "");
    }
  }, [ci.data]);
  if (ci.error && !ci.data) return <Failed error={ci.error} what="the check-in" onRetry={ci.reload} />;
  if (!ci.data) return <Loading what="this week's check-in" />;
  const d = ci.data;
  async function submit() {
    setBusy(true);
    setResult(null);
    try {
      await api("/api/v1/checkin", { method: "POST", body: JSON.stringify({ flagged, subscription: sub, card_paid: card, saved, note }) });
      setResult({ tone: "info", text: "Saved. See you next week." });
      ci.reload();
    } catch (e) {
      setResult({ tone: "error", text: `Couldn't save the check-in: ${(e as Error).message}` });
    } finally {
      setBusy(false);
    }
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 720 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Weekly check-in</h1>
        <span style={{ fontSize: 13, color: "var(--muted)" }}>{d.week}</span>
        {d.done && <StatusPill status="good">Done this week</StatusPill>}
      </div>
      <div className={amountClass(d.line)} style={{ fontSize: 14 }}>{d.line}</div>

      <Q n={1} title="Anything unexpected this week?">
        {d.unexpected.length === 0 ? <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Nothing outside your bills.</div> : d.unexpected.map((t) => (
          <label key={t.id} style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 14 }}>
            <input type="checkbox" checked={flagged.includes(t.id)} onChange={(e) => setFlagged((f) => (e.target.checked ? [...f, t.id] : f.filter((x) => x !== t.id)))} style={{ width: 18, height: 18 }} />
            <span style={{ flexGrow: 1 }}>{t.merchant} <span style={{ color: "var(--muted)" }}>· {shortDate(t.date)} · {t.category}</span></span>
            <strong className="num">{money(t.amount, { cents: true })}</strong>
          </label>
        ))}
        {d.unexpected.length > 0 && <div style={{ fontSize: 12, color: "var(--muted)" }}>Tick any you did not expect; each one gets a note in your transactions.</div>}
      </Q>

      <Q n={2} title={d.subscription ? `Still paying for ${d.subscription.name}?` : "Still paying for these?"}>
        {d.subscription ? (
          <>
            <div className="num" style={{ fontSize: 13, color: "var(--ink-2)" }}>{money(d.subscription.amount, { cents: true })} a month, {money(d.subscription.yearly)} a year.</div>
            <Choice label="Keep or drop" value={sub} onChange={setSub} options={[{ v: "keep", text: "Keep it" }, { v: "drop", text: "I will cancel it" }, { v: "unsure", text: "Not sure" }]} />
          </>
        ) : <div style={{ fontSize: 13, color: "var(--ink-2)" }}>No subscriptions on your bills list.</div>}
      </Q>

      <Q n={3} title="The card: paid before the statement?">
        {d.cards.length === 0 ? <div style={{ fontSize: 13, color: "var(--ink-2)" }}>No statement day in the next ten days.</div> : d.cards.map((c) => (
          <div key={c.id} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 13 }}>
            <StatusPill status={c.status} />
            <span>{c.name}: statement {shortDate(c.statement_on)}{c.statement_balance !== null ? `, about ${money(c.statement_balance)}` : ""}{c.to_target ? `; pay ${money(c.to_target)} by ${c.pay_by ? shortDate(c.pay_by) : "then"} to report under 30% of your limit` : ""}</span>
          </div>
        ))}
        {d.cards.length > 0 && <Choice label="Card paid" value={card} onChange={setCard} options={[{ v: true, text: "Paid" }, { v: false, text: "Not yet" }]} />}
      </Q>

      <Q n={4} title="Savings moved?">
        <div className="num" style={{ fontSize: 13, color: "var(--ink-2)" }}>{d.saved_14d > 0 ? `${money(d.saved_14d, { cents: true })} went into savings in the last two weeks.` : "Nothing went into savings in the last two weeks."} <Link to="/saving">Open Saving</Link></div>
        <Choice label="Saved" value={saved} onChange={setSaved} options={[{ v: true, text: "Yes" }, { v: false, text: "Not this time" }]} />
      </Q>

      <Q n={5} title="Next week">
        {d.coming.length === 0 ? <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Nothing big next week.</div> : d.coming.map((e) => (
          <div key={e.source + e.date} style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}><span>{e.name} · {shortDate(e.date)}</span><strong className="num">{money(e.amount)}</strong></div>
        ))}
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="lab">A note to yourself (if you like)</span>
          <textarea className="input" value={note} onChange={(e) => setNote(e.target.value)} rows={2} style={{ width: "100%", resize: "vertical" }} />
        </label>
      </Q>

      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <button type="button" className="btn dark" disabled={busy} onClick={submit}>{busy ? "Saving…" : d.done ? "Update this week" : "Done for this week"}</button>
        {result && <Said text={result.text} tone={result.tone} />}
      </div>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{d.not_advice}</div>
    </div>
  );
}
