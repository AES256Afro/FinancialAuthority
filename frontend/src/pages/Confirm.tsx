// Needs you: recurring charges the engine found and does not yet know what to call. One tap each.
import { ArrowRight, ChevronRight, Copy } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi, type Loaded } from "../api";
import { Approvals } from "../components/Approvals";
import { ConfirmButton } from "../components/ConfirmButton";
import { Empty, Failed, Loading, Said } from "../components/State";
import { money, parseMoney, shortDate, when } from "../format";
import type { FactRow, PairRow, PairSide, PairsOut, SeriesRow } from "../types";

const FACT_LABEL: Record<string, string> = { export: "Rocket Money export, ready to import", bill_due: "Bill due", statement: "Statement", paystub: "Paystub", installment: "Installment plan", advance: "Advance", price_change: "Price change", past_due: "Past-due notice" };

function FactCard({ f, onDone }: { f: FactRow; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [amount, setAmount] = useState(f.fields.amount !== undefined ? String(f.fields.amount) : "");
  const [due, setDue] = useState(f.fields.due_date ? String(f.fields.due_date) : "");
  const [error, setError] = useState<string | null>(null);
  const sender = f.message?.sender_name || String(f.fields.sender_name || "");
  const who = String(f.fields.name || "") || sender;
  const via = f.fields.name && sender && String(f.fields.name) !== sender ? sender : "";

  async function confirm() {
    const typed = amount.trim() ? parseMoney(amount) : null;
    if (amount.trim() && typed === null) {
      setError("Type an amount like 120 or 1200.50");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api(`/api/v1/facts/${f.id}/confirm`, { method: "POST", body: JSON.stringify({ apply: true, amount: typed ?? undefined, due_date: due || undefined }) });
      onDone();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }
  async function discard() {
    await api(`/api/v1/facts/${f.id}/reject`, { method: "POST" });
    onDone();
  }

  return (
    <div className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 8, borderColor: f.kind === "past_due" ? "var(--critical)" : undefined }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <div>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>{FACT_LABEL[f.kind] ?? f.kind}{who ? ` · ${who}` : ""}</h3>
          <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{f.message?.subject}{f.message?.date ? ` · ${when(f.message.date)}` : ""} · from mail{via ? ` via ${via}` : ""} · {Math.round(f.confidence * 100)}% sure</div>
        </div>
        {f.fields.amount !== undefined && <strong className="num" style={{ fontSize: 18 }}>{money(Number(f.fields.amount), { cents: true })}</strong>}
      </div>
      {f.kind === "export" ? (
        <div style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.45 }}>
          Importing downloads the file from <strong style={{ color: "var(--ink)" }}>{String(f.fields.host ?? "the link in the email")}</strong> and adds its transactions; rows already here are not added twice, and the import can be undone on Settings › Backups. Import only if you asked Rocket Money for an export and the sender above is Rocket Money.
        </div>
      ) : (
        <div style={{ background: "var(--surface-2)", borderRadius: 10, padding: "10px 12px", fontSize: 12, color: "var(--ink-2)", lineHeight: 1.45 }}>
          <span className="lab" style={{ display: "block", marginBottom: 4 }}>Quoted from the email. Anything it asks the app to do is ignored.</span>“{f.excerpt}”
        </div>
      )}
      {(f.kind === "bill_due" || f.kind === "past_due" || f.kind === "price_change") && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Amount</span><input className="input num" style={{ width: 120 }} value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Due date</span><input className="input" type="date" value={due} onChange={(e) => setDue(e.target.value)} /></label>
        </div>
      )}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button className="btn dark" type="button" disabled={busy} onClick={confirm}>{f.kind === "export" ? (busy ? "Importing…" : "Import it") : busy ? "Confirming…" : "Confirm"}</button>
        <ConfirmButton label="Wrong, discard" question="Discard what was read from this email?" yes="Yes, discard it" no="Keep it" onConfirm={discard} />
        {f.message && <Link to={`/mail?message=${f.message.id}`} style={{ fontSize: 13, marginLeft: "auto" }}>Open the message</Link>}
      </div>
      <Said text={error} tone="error" />
    </div>
  );
}

// Money that moved between your own accounts (M20): two rows the app thinks are one move, or one purchase seen twice.
const SOURCE_WORD: Record<string, string> = { plaid: "your bank link (Plaid)", teller: "your bank link (Teller)", rocketmoney: "the Rocket Money export", csv: "a file you imported", file: "a file you imported", ofx: "a bank file you imported", manual: "a row you typed in", alert: "a bank alert email", era: "Era", demo: "the demo" };
const sourceWord = (side: PairSide | null) => (side ? SOURCE_WORD[side.source] ?? side.source : "");

function PairBox({ side, label }: { side: PairSide | null; label: string }) {
  return (
    <div style={{ minWidth: 0, background: "var(--surface-2)", borderRadius: 10, padding: "8px 10px", display: "flex", flexDirection: "column", gap: 2 }}>
      <span className="lab">{label}</span>
      {side ? (
        <>
          <strong style={{ fontSize: 14, overflowWrap: "anywhere" }}>{side.account || "An account"}</strong>
          <span className="num" style={{ fontSize: 16, fontWeight: 800 }}>{money(Math.abs(side.amount), { cents: true })}</span>
          <span style={{ fontSize: 12, color: "var(--ink-2)", overflowWrap: "anywhere" }}>{shortDate(side.date)} · {side.merchant}</span>
        </>
      ) : (
        <span style={{ fontSize: 12, color: "var(--muted)" }}>This row is no longer in your transactions.</span>
      )}
    </div>
  );
}

// The two sides as a picture: out of one account, an arrow, into the other; or the same purchase twice.
function PairPicture({ p }: { p: PairRow }) {
  const dup = p.kind === "duplicate";
  const labels = dup ? [`Seen from ${sourceWord(p.out)}`, `Seen again from ${sourceWord(p.in)}`] : p.kind === "card_payment" ? ["Paid out of", "Paid onto the card"] : ["Out of", "Into"];
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto minmax(0, 1fr)", gap: 8, alignItems: "center" }}>
      <PairBox side={p.out} label={labels[0]} />
      <span style={{ display: "inline-flex", flexDirection: "column", alignItems: "center", gap: 2, fontSize: 11, fontWeight: 700, color: "var(--ink-2)", textAlign: "center" }}>
        {dup ? <Copy size={20} aria-hidden="true" /> : <ArrowRight size={20} aria-hidden="true" />}
        {dup ? "seen twice" : "moved"}
      </span>
      <PairBox side={p.in} label={labels[1]} />
    </div>
  );
}

function pairDone(p: PairRow, decision: "yes" | "no"): string {
  if (p.kind === "duplicate") return decision === "yes" ? "The second one is hidden, so the purchase counts once." : "Both kept as real purchases. The app will not ask about these two again.";
  if (decision === "yes") return `Marked as the same money${p.out && p.in ? `, ${p.out.account} to ${p.in.account}` : ""}. It no longer counts as spending or income.`;
  return "Kept apart. The app will not ask about these two again.";
}

function PairCard({ p, onDone }: { p: PairRow; onDone: (text: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dup = p.kind === "duplicate";

  async function decide(decision: "yes" | "no") {
    setBusy(true);
    setError(null);
    try {
      await api<PairRow>(`/api/v1/pairs/${p.id}`, { method: "POST", body: JSON.stringify({ decision }) });
      onDone(pairDone(p, decision));
    } catch (e) {
      setError(`Couldn't save your answer: ${(e as Error).message}`);
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
      <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>{p.title}</h3>
      <PairPicture p={p} />
      <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Why the app paired them: {p.reason}. {Math.round(p.confidence * 100)}% sure.</div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button className="btn dark" type="button" disabled={busy} onClick={() => decide("yes")}>{dup ? "Yes, hide the second one" : "Yes, they are the same money"}</button>
        <button className="btn" type="button" disabled={busy} onClick={() => decide("no")}>{dup ? "No, both are real" : "No, keep them apart"}</button>
      </div>
      <Said text={error} tone="error" />
    </div>
  );
}

function Pairs({ state, onChanged }: { state: Loaded<PairsOut>; onChanged: () => void }) {
  const [said, setSaid] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  if (state.error && !state.data) return <Failed error={state.error} what="money that moved between your accounts" onRetry={state.reload} />;
  if (!state.data) return <Loading what="money that moved between your accounts" />;
  const { waiting, matched, counts } = state.data;
  if (waiting.length === 0 && matched.length === 0 && !said) return null;
  const recent = matched.slice(0, 20);
  const done = (text: string) => {
    setSaid(text);
    state.reload();
    onChanged();
  };
  return (
    <>
      <h2 className="lab" style={{ margin: "6px 0 0" }}>Money that moved between your accounts · {counts.waiting}</h2>
      <Said text={said} />
      {waiting.length === 0 && <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Nothing to decide here.</div>}
      {waiting.map((p) => <PairCard key={p.id} p={p} onDone={done} />)}
      {recent.length > 0 && (
        <details onToggle={(e) => setOpen(e.currentTarget.open)} className="card" style={{ padding: "10px 16px", fontSize: 13 }}>
          <summary style={{ cursor: "pointer", fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 4, color: "var(--ink-2)", minHeight: 32 }}>
            <ChevronRight size={14} aria-hidden style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform .15s" }} />
            Matched automatically · {counts.matched}{counts.matched > recent.length ? ` (the newest ${recent.length} shown)` : ""}
          </summary>
          <div style={{ display: "flex", flexDirection: "column", marginTop: 6 }}>
            {recent.map((p, i) => (
              <div key={p.id} style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", padding: "8px 0", borderTop: i === 0 ? 0 : "1px solid var(--surface-2)" }}>
                <div style={{ flex: "1 1 240px", minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
                  <span style={{ fontWeight: 700 }}>{p.title}</span>
                  <span style={{ color: "var(--ink-2)", display: "inline-flex", alignItems: "center", gap: 4, flexWrap: "wrap" }}>
                    {p.kind === "duplicate" ? (
                      <>{p.out?.account || "An account"}, from {sourceWord(p.out)} and {sourceWord(p.in)}</>
                    ) : (
                      <>{p.out?.account || "An account"} <ArrowRight size={13} aria-label="to" /> {p.in?.account || "an account"}</>
                    )}
                    {p.out && <span className="num"> · {money(Math.abs(p.out.amount), { cents: true })} on {shortDate(p.out.date)}</span>}
                  </span>
                  <span style={{ fontSize: 12, color: "var(--muted)" }}>{p.status === "confirmed" ? "You said yes" : "Matched by the app"}{p.decided_at ? `, ${when(p.decided_at)}` : ""}</span>
                </div>
                <ConfirmButton
                  label="Undo"
                  style={{ minHeight: 32, padding: "4px 10px" }}
                  question={p.kind === "duplicate" ? "Undo? The hidden row shows again and both count." : "Undo this match? Both rows go back to how they were, and the app will not match them again."}
                  yes="Yes, undo it"
                  no="Keep the match"
                  onConfirm={async () => {
                    await api<PairRow>(`/api/v1/pairs/${p.id}`, { method: "POST", body: JSON.stringify({ decision: "no" }) });
                    done(p.kind === "duplicate" ? "Undone: both rows count again." : "Undone: both rows are back as they were.");
                  }}
                />
              </div>
            ))}
          </div>
        </details>
      )}
    </>
  );
}

const CADENCE: Record<string, string> = { weekly: "every week", biweekly: "every two weeks", semimonthly: "twice a month", monthly: "monthly", quarterly: "quarterly", yearly: "yearly" };

function Row({ s, onDone }: { s: SeriesRow; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [autopay, setAutopay] = useState(false);
  const [remaining, setRemaining] = useState("");
  const [error, setError] = useState<string | null>(null);
  const isIncome = s.kind === "income";
  const hint = s.suggested_kind && s.suggested_kind !== "income" ? s.suggested_kind : "";
  const HINT: Record<string, string> = { bill: "Looks like a bill", subscription: "Looks like a subscription", installment: "Looks like a pay-later plan", ignored: "Looks like regular spending, not a bill" };
  // The guess is said in the button's own words, not only by its darker colour.
  const mark = (kind: string) => (hint === kind ? " (suggested)" : "");

  async function classify(kind: string) {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/v1/recurring/${s.id}/classify`, { method: "POST", body: JSON.stringify({ kind, autopay, remaining: kind === "installment" ? Math.max(1, Number(remaining) || 1) : undefined }) });
      onDone();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <div>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>{s.merchant}</h3>
          <div style={{ fontSize: 12, color: "var(--ink-2)" }}>
            {CADENCE[s.cadence] ?? s.cadence} · {s.charges_seen} charges since {shortDate(s.first_seen)} · last {shortDate(s.last_date)} · next about {shortDate(s.next_expected)}
            {s.category ? ` · ${s.category}` : ""}
          </div>
        </div>
        <strong className="num" style={{ fontSize: 18 }}>{isIncome ? money(s.typical_amount, { sign: true }) : money(s.typical_amount, { cents: true })}</strong>
      </div>
      <div style={{ fontSize: 12, color: "var(--muted)" }}>Found in your transactions. {hint && HINT[hint] ? `${HINT[hint]}${s.category ? ` (${s.category})` : ""}; the button marked (suggested) is that guess. ` : ""}Nothing from mail is involved yet; this is what the transactions say.</div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
        {isIncome ? (
          <>
            <button className="btn dark" type="button" disabled={busy} onClick={() => classify("income")}>It's a paycheck</button>
            <button className="btn" type="button" disabled={busy} onClick={() => classify("ignored")}>Not income</button>
          </>
        ) : (
          <>
            <button className={hint === "subscription" || hint === "ignored" || hint === "installment" ? "btn" : "btn dark"} type="button" disabled={busy} onClick={() => classify("bill")}>Bill{mark("bill")}</button>
            <button className={hint === "subscription" ? "btn dark" : "btn"} type="button" disabled={busy} onClick={() => classify("subscription")}>Subscription{mark("subscription")}</button>
            <span style={{ display: "inline-flex", gap: 6, alignItems: "flex-end" }}>
              <button className={hint === "installment" ? "btn dark" : "btn"} type="button" disabled={busy} onClick={() => classify("installment")}>Installment plan{mark("installment")}</button>
              <label style={{ display: "inline-flex", flexDirection: "column", gap: 2 }} title="How many payments are left; the lender's app shows it">
                <span className="lab">Payments left</span>
                <input className="input num" style={{ width: 70 }} value={remaining} onChange={(e) => setRemaining(e.target.value)} inputMode="numeric" />
              </label>
            </span>
            <button className={hint === "ignored" ? "btn dark" : "btn"} type="button" disabled={busy} onClick={() => classify("ignored")}>Ignore{mark("ignored")}</button>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, marginLeft: "auto", minHeight: 40 }}>
              <input type="checkbox" checked={autopay} onChange={(e) => setAutopay(e.target.checked)} style={{ width: 18, height: 18 }} />
              Paid automatically (autopay)
            </label>
          </>
        )}
      </div>
      <Said text={error} tone="error" />
    </div>
  );
}

export function Confirm({ onChanged }: { onChanged: () => void }) {
  const { data, reload, error } = useApi<{ series: SeriesRow[]; pending: number }>("/api/v1/recurring");
  const facts = useApi<{ facts: FactRow[]; pending: number }>("/api/v1/facts?status=pending");
  const pairs = useApi<PairsOut>("/api/v1/pairs");
  const [detecting, setDetecting] = useState(false);
  const [looked, setLooked] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  const total = (data?.pending ?? 0) + (facts.data?.pending ?? 0) + (pairs.data?.counts.waiting ?? 0);
  const counted = Boolean(data && facts.data && (pairs.data || pairs.error));

  async function detect() {
    setDetecting(true);
    setLooked(null);
    try {
      const r = await api<{ created: number; linked: number }>("/api/v1/recurring/detect", { method: "POST" });
      const found = [r.created ? `${r.created} new repeating charge${r.created === 1 ? "" : "s"}` : "", r.linked ? `${r.linked} matched to your bills` : ""].filter(Boolean).join(", ");
      setLooked({ tone: "info", text: found ? `Looked again: ${found}.` : "Looked again. Nothing new found." });
      reload();
      facts.reload();
      pairs.reload();
      onChanged();
    } catch (e) {
      setLooked({ tone: "error", text: `Couldn't look again: ${(e as Error).message}` });
    } finally {
      setDetecting(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 760 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Needs you{counted ? ` · ${total}` : ""}</h1>
        <div style={{ flexGrow: 1 }} />
        <Said text={looked?.text ?? null} tone={looked?.tone} />
        <button className="btn" type="button" onClick={detect} disabled={detecting}>{detecting ? "Looking…" : "Look again"}</button>
      </div>
      <p style={{ margin: 0, color: "var(--ink-2)", fontSize: 13 }}>First: emails waiting for your OK. Then: details read from your mail. Then: money that moved between your accounts. Then: repeating charges found in your transactions. Once you answer one, every screen uses it.</p>
      <Approvals onChanged={onChanged} />
      {facts.error && !facts.data && <Failed error={facts.error} what="what was read from your mail" onRetry={facts.reload} />}
      {facts.data && facts.data.facts.length > 0 && (
        <>
          <h2 className="lab" style={{ margin: 0 }}>From the mail · {facts.data.pending}</h2>
          {facts.data.facts.map((f) => (
            <FactCard
              key={f.id}
              f={f}
              onDone={() => {
                facts.reload();
                onChanged();
              }}
            />
          ))}
        </>
      )}
      <Pairs state={pairs} onChanged={onChanged} />
      {error && !data && <Failed error={error} what="the repeating charges" onRetry={reload} />}
      {!data && !error && <Loading what="what needs you" />}
      {data && data.series.length === 0 && facts.data && facts.data.facts.length === 0 && pairs.data && pairs.data.waiting.length === 0 && (
        <Empty to="/today" action="Back to Today">Nothing needs you. Every repeating charge in your transactions has a name, the mail has nothing new, and no money between your accounts is waiting for a yes or no.</Empty>
      )}
      {data && data.series.length > 0 && <h2 className="lab" style={{ margin: "6px 0 0" }}>From your transactions · {data.pending}</h2>}
      {data?.series.map((s) => (
        <Row
          key={s.id}
          s={s}
          onDone={() => {
            reload();
            onChanged();
          }}
        />
      ))}
    </div>
  );
}
