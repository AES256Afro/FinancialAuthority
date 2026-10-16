// Documents: every file you keep, by month. Picture first: the return and warranty dates still open, each a point on
// a line from today. Then the receipts, each with the purchase it belongs to (or a way to match it), then a strip of
// months with a small bar per month by kind, then a search through the text of every file. The app counts, dates and
// matches; this page lays out what it returned.
import { BookOpen, CalendarClock, Download, Link2, Pencil, Receipt, ShieldCheck, Undo2, Unlink, Upload, X } from "lucide-react";
import { useEffect, useState, type CSSProperties, type FormEvent } from "react";
import { Link, useLocation } from "react-router-dom";
import { api, useApi, type Loaded } from "../api";
import { DOC_KIND_WORD, DocLine, Documents, PurchaseChoices, Reader, SearchBox, dayWord, monthWord } from "../components/Documents";
import { ConfirmButton } from "../components/ConfirmButton";
import { Empty, Failed, Loading, Said } from "../components/State";
import { StatusPill } from "../components/Status";
import { money, parseMoney, shortDate } from "../format";
import type { Status } from "../guide";
import type { CandidatesOut, DocumentMonth, DocumentMonthsOut, KeptDocument, KeptDocumentsOut, ReceiptCandidate, ReceiptDeadline, ReceiptRow, ReceiptsOut } from "../types-receipts";

const SMALL: CSSProperties = { minHeight: 32, padding: "4px 10px", fontSize: 12 };
const ROW: CSSProperties = { display: "inline-flex", alignItems: "center", gap: 4 };
const FIELD: CSSProperties = { display: "flex", flexDirection: "column", gap: 4 };
const CARD: CSSProperties = { padding: 14, display: "flex", flexDirection: "column", gap: 10 };
const H2: CSSProperties = { margin: 0, fontSize: 15, fontWeight: 800 };
const ACCEPT = ".pdf,.png,.jpg,.jpeg";
const MAX_BYTES = 25 * 1024 * 1024;

type Outcome = { text: string; tone: "info" | "error" } | null;

// The same words StatusPill uses on its own, with the days left after them.
const STATUS_WORD: Record<Status, string> = { good: "On track", warn: "Watch", crit: "Act" };

// The four groups the month strip is coloured by, in the validated order of the looks palette (blue, orange,
// violet, pink: each neighbour passes the colour-blind check). Every bar also has a legend and words.
const GROUPS = [
  { key: "paystub", word: "Paystub", fill: "var(--paycheck)" },
  { key: "statement", word: "Statement", fill: "var(--bill)" },
  { key: "receipt", word: "Receipt", fill: "var(--debt)" },
  { key: "other", word: "Other (a lease, terms, anything else)", fill: "var(--later)" },
] as const;
const groupOf = (kind: string) => GROUPS.find((g) => g.key === kind) ?? GROUPS[3];

/** Days left in words: "today", "1 day left", "23 days left". */
function leftWord(n: number): string {
  if (n <= 0) return "today";
  return `${n} day${n === 1 ? "" : "s"} left`;
}

/** What a file is kept with, in words. */
function placeWord(attach: string): string {
  if (!attach) return "not kept with anything in particular";
  const what = attach.split(":")[0];
  return `kept with ${{ transaction: "a purchase", home: "a home", utility: "a utility", bill: "a bill", account: "an account", debt: "a debt", paystub: "a paystub" }[what] ?? "something else"}`;
}

/** Where a file's place is on screen. */
function placeLink(attach: string, around?: string | null): string | null {
  const [what, id] = attach.split(":");
  if (what === "transaction") return `/transactions?open=${id}${around ? `&around=${around}` : ""}`;
  return ({ home: "/housing", utility: "/housing", bill: "/bills", account: "/accounts", debt: "/debts", paystub: "/paystubs" } as Record<string, string>)[what] ?? null;
}

function scrollTo(id: string) {
  document.getElementById(id)?.scrollIntoView({ block: "start", behavior: "smooth" });
}

// ---- coming up: the open return and warranty dates on a line from today ----------------------------------------

function ComingUp({ state }: { state: Loaded<ReceiptsOut> }) {
  const title = (
    <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
      <h2 style={H2}>Coming up</h2>
      <span style={{ fontSize: 12, color: "var(--ink-2)" }}>The last days to return something and the warranties still open, soonest first.</span>
    </div>
  );
  if (state.error && !state.data) return <section className="card" style={CARD}>{title}<Failed error={state.error} what="the return and warranty dates" onRetry={state.reload} card={false} /></section>;
  if (!state.data) return <section className="card" style={CARD}>{title}<Loading what="the return and warranty dates" card={false} /></section>;
  const items = state.data.deadlines;
  return (
    <section className="card" style={CARD} aria-label="Coming up: return and warranty dates">
      {title}
      {items.length === 0 ? (
        <Empty card={false} to="/documents#add-receipt" action="Add a receipt">No return or warranty dates open.</Empty>
      ) : (
        <Timeline asOf={state.data.as_of} items={items} />
      )}
    </section>
  );
}

/** Today at the top of a line, each date below it. The space between two points grows with the days between them
 * (a square root, so a warranty two years out does not push the near ones off the screen): layout only. */
function Timeline({ asOf, items }: { asOf: string; items: ReceiptDeadline[] }) {
  const gaps = items.map((d, i) => 8 + Math.min(56, Math.round(Math.sqrt(Math.max(0, d.days_left - (i === 0 ? 0 : items[i - 1].days_left))) * 5)));
  // The line runs from each point's middle down to the next point.
  const line = (to: number) => <span aria-hidden="true" style={{ position: "absolute", left: 13, top: 14, bottom: -to, width: 2, background: "var(--muted)", borderRadius: 2 }} />;
  return (
    <ol style={{ listStyle: "none", margin: 0, padding: 0 }}>
      <li style={{ position: "relative", display: "flex", alignItems: "center", gap: 10, minHeight: 28 }}>
        {line(gaps[0] ?? 0)}
        <span aria-hidden="true" style={{ position: "relative", width: 28, display: "inline-flex", justifyContent: "center", flexShrink: 0 }}>
          <span style={{ width: 16, height: 16, borderRadius: 999, background: "var(--ink)", border: "3px solid var(--surface)", boxSizing: "border-box" }} />
        </span>
        <strong style={{ fontSize: 13 }}>Today, {shortDate(asOf)}</strong>
      </li>
      {items.map((d, i) => {
        const Icon = d.what === "return" ? Undo2 : ShieldCheck;
        return (
          <li key={`${d.document_id}-${d.what}`} style={{ position: "relative", display: "flex", alignItems: "flex-start", gap: 10, marginTop: gaps[i] }}>
            {i < items.length - 1 && line(gaps[i + 1])}
            <span aria-hidden="true" style={{ position: "relative", width: 28, height: 28, borderRadius: 999, background: "var(--surface)", border: "2px solid var(--ink-2)", display: "inline-flex", alignItems: "center", justifyContent: "center", flexShrink: 0, color: "var(--ink)", boxSizing: "border-box" }}>
              <Icon size={14} strokeWidth={2.4} />
            </span>
            <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0, flex: "1 1 auto" }}>
              <span style={{ fontSize: 13, overflowWrap: "anywhere" }}>
                <strong>{d.what === "return" ? "Last day to return" : "Warranty ends"} · {dayWord(d.on)}</strong>
                <span style={{ color: "var(--ink-2)" }}> · {d.merchant || d.title}{d.amount != null ? <span className="num"> · {money(d.amount, { cents: true })}</span> : null}</span>
              </span>
              <span style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                <StatusPill status={d.status}>{STATUS_WORD[d.status]} · {leftWord(d.days_left)}</StatusPill>
                <button type="button" onClick={() => scrollTo(`receipt-${d.document_id}`)} style={{ background: "none", border: 0, padding: "4px 0", minHeight: 32, font: "inherit", fontSize: 12, fontWeight: 700, color: "var(--paycheck-ink)", cursor: "pointer" }}>
                  See the receipt
                </button>
              </span>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

// ---- receipts ------------------------------------------------------------------------------------------------------

/** The purchases with this total near this day, asked of the app; null until both are known. */
function candidatesPath(amount: number | null | undefined, boughtOn: string | null | undefined, merchant: string | null | undefined): string | null {
  if (amount == null || !boughtOn) return null;
  return `/api/v1/receipts/candidates?amount=${encodeURIComponent(String(amount))}&bought_on=${encodeURIComponent(boughtOn)}&merchant=${encodeURIComponent(merchant ?? "")}`;
}

/** Find the purchase a receipt belongs to: the app's candidates, one to pick, then attach. */
function MatchIt({ r, onDone, onClose }: { r: ReceiptRow; onDone: (text: string) => void; onClose: () => void }) {
  const path = candidatesPath(r.amount, r.bought_on, r.merchant);
  const found = useApi<CandidatesOut>(path);
  const list = path ? found.data?.candidates ?? null : null;
  const [pick, setPick] = useState<number | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  const chosen = pick === undefined ? list?.[0]?.id ?? null : pick;

  async function attach() {
    if (chosen === null) return;
    setBusy(true);
    setSaid(null);
    try {
      await api(`/api/v1/documents/${r.id}`, { method: "PUT", body: JSON.stringify({ attach_to: `transaction:${chosen}` }) });
      const c = list?.find((x) => x.id === chosen);
      onDone(c ? `Kept with the ${c.merchant || "purchase"} purchase on ${shortDate(c.date)}.` : "Kept with the purchase.");
    } catch (e) {
      setSaid(`Couldn't attach it: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, background: "var(--surface-2)", borderRadius: 10, padding: "10px 12px" }}>
      {!path ? (
        <span style={{ fontSize: 13, color: "var(--ink-2)" }}>The app matches a receipt by its total and the day bought. Add both with “Change it”, then match it.</span>
      ) : found.error && !found.data ? (
        <Failed error={found.error} what="the purchases it may belong to" onRetry={found.reload} card={false} />
      ) : !list ? (
        <Loading what="the purchases it may belong to" card={false} />
      ) : list.length === 0 ? (
        <span style={{ fontSize: 13, color: "var(--ink-2)" }}>No purchase of {money(r.amount ?? 0, { cents: true })} was found from two days before {r.bought_on ? shortDate(r.bought_on) : "the day bought"} to ten days after. A card charge can take a few days to show; try again later.</span>
      ) : (
        <PurchaseChoices name={`match-${r.id}`} legend="Which purchase is it?" list={list} value={chosen} onPick={setPick} />
      )}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        {list && list.length > 0 && <button type="button" className="btn dark" disabled={busy || chosen === null} onClick={attach} style={{ ...SMALL, ...ROW }}><Link2 size={13} aria-hidden="true" />{busy ? "Attaching…" : "Attach it to this purchase"}</button>}
        <button type="button" className="btn" onClick={onClose} style={{ ...SMALL, ...ROW }}><X size={13} aria-hidden="true" />Close</button>
        <Said text={said} tone="error" />
      </div>
    </div>
  );
}

/** Change a receipt's store, total and dates. A blank date or total clears it. */
function ChangeReceipt({ r, onDone, onClose }: { r: ReceiptRow; onDone: (text: string) => void; onClose: () => void }) {
  const [merchant, setMerchant] = useState(r.merchant);
  const [total, setTotal] = useState(r.amount != null ? r.amount.toFixed(2) : "");
  const [bought, setBought] = useState(r.bought_on ?? "");
  const [returnBy, setReturnBy] = useState(r.return_by ?? "");
  const [warranty, setWarranty] = useState(r.warranty_until ?? "");
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);

  async function save(e: FormEvent) {
    e.preventDefault();
    const amount = total.trim() ? parseMoney(total) : null;
    if (total.trim() && amount === null) return setSaid("Type an amount like 120 or 1200.50");
    setBusy(true);
    setSaid(null);
    try {
      await api(`/api/v1/documents/${r.id}`, { method: "PUT", body: JSON.stringify({ merchant: merchant.trim(), amount, bought_on: bought || null, return_by: returnBy || null, warranty_until: warranty || null }) });
      onDone(`Saved the receipt from ${merchant.trim() || r.title}.`);
    } catch (err) {
      setSaid(`Couldn't save it: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} style={{ display: "flex", flexDirection: "column", gap: 8, background: "var(--surface-2)", borderRadius: 10, padding: "10px 12px" }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
        <label style={{ ...FIELD, flex: "1 1 160px", minWidth: 0 }}>
          <span className="lab">Store</span>
          <input className="input" value={merchant} onChange={(e) => setMerchant(e.target.value)} maxLength={80} />
        </label>
        <label style={{ ...FIELD, flex: "0 1 120px", minWidth: 0 }}>
          <span className="lab">Total</span>
          <input className="input num" inputMode="decimal" value={total} onChange={(e) => setTotal(e.target.value)} placeholder="42.50" style={{ width: "100%" }} />
        </label>
        <label style={{ ...FIELD, flex: "0 1 160px", minWidth: 0 }}>
          <span className="lab">Day bought</span>
          <input className="input" type="date" value={bought} onChange={(e) => setBought(e.target.value)} />
        </label>
        <label style={{ ...FIELD, flex: "0 1 160px", minWidth: 0 }}>
          <span className="lab">Last day to return</span>
          <input className="input" type="date" value={returnBy} onChange={(e) => setReturnBy(e.target.value)} />
        </label>
        <label style={{ ...FIELD, flex: "0 1 160px", minWidth: 0 }}>
          <span className="lab">Warranty until</span>
          <input className="input" type="date" value={warranty} onChange={(e) => setWarranty(e.target.value)} />
        </label>
      </div>
      <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Clear a date to stop its reminder.</span>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button className="btn dark" type="submit" disabled={busy} style={SMALL}>{busy ? "Saving…" : "Save"}</button>
        <button className="btn" type="button" onClick={onClose} style={SMALL}>Cancel</button>
        <Said text={said} tone="error" />
      </div>
    </form>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0 }}>
      <dt className="lab">{label}</dt>
      <dd className="num" style={{ margin: 0, fontSize: 13, fontWeight: 700, overflowWrap: "anywhere" }}>{value}</dd>
    </div>
  );
}

function ReceiptItem({ r, file, first, onChanged, onRemoved }: { r: ReceiptRow; file: KeptDocument | undefined; first: boolean; onChanged: () => void; onRemoved: (text: string) => void }) {
  const [mode, setMode] = useState<"match" | "change" | null>(null);
  const [reading, setReading] = useState<number | null>(null);
  const [said, setSaid] = useState<Outcome>(null);
  const to = r.attach_to ? placeLink(r.attach_to, r.bought_on) : null;
  const matched = r.attach_to.startsWith("transaction:");
  const done = (text: string) => {
    setMode(null);
    setSaid({ text, tone: "info" });
    onChanged();
  };

  async function detach() {
    try {
      await api(`/api/v1/documents/${r.id}`, { method: "PUT", body: JSON.stringify({ attach_to: "" }) });
      done("Taken off that purchase. Match it again when you find the right one.");
    } catch (e) {
      setSaid({ text: `Couldn't take it off: ${(e as Error).message}`, tone: "error" });
    }
  }
  async function remove() {
    await api(`/api/v1/documents/${r.id}`, { method: "DELETE" });
    onRemoved(`Removed the receipt from ${r.merchant || r.title}, with its reminders.`);
    onChanged();
  }

  return (
    <li id={`receipt-${r.id}`} style={{ display: "flex", flexDirection: "column", gap: 8, padding: "12px 0", borderTop: first ? 0 : "1px solid var(--surface-2)", scrollMarginTop: 12 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <Receipt size={16} aria-hidden="true" style={{ flexShrink: 0, alignSelf: "center", color: "var(--ink-2)" }} />
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 800, overflowWrap: "anywhere", minWidth: 0 }}>{r.merchant || r.title}</h3>
        <span style={{ flexGrow: 1 }} />
        {r.amount != null && <strong className="num" style={{ fontSize: 16 }}>{money(r.amount, { cents: true })}</strong>}
      </div>
      <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(140px, 100%), 1fr))", gap: 8 }}>
        <Fact label="Day bought" value={r.bought_on ? dayWord(r.bought_on) : "Not known"} />
        <Fact label="Return by" value={r.return_by ? dayWord(r.return_by) : "No date"} />
        <Fact label="Warranty until" value={r.warranty_until ? dayWord(r.warranty_until) : "No date"} />
      </dl>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", fontSize: 13 }}>
        {matched ? (
          <>
            <Link2 size={14} aria-hidden="true" style={{ flexShrink: 0 }} />
            {to ? <Link to={to} style={{ fontWeight: 700 }}>The purchase it belongs to, on Transactions</Link> : <strong>Kept with a purchase</strong>}
            <button type="button" className="btn" onClick={detach} style={{ ...SMALL, ...ROW }}><Unlink size={13} aria-hidden="true" />Not this purchase</button>
          </>
        ) : r.attach_to ? (
          <>
            <Link2 size={14} aria-hidden="true" style={{ flexShrink: 0 }} />
            {to ? <Link to={to} style={{ fontWeight: 700 }}>{placeWord(r.attach_to)}</Link> : <strong>{placeWord(r.attach_to)}</strong>}
          </>
        ) : (
          <>
            <Unlink size={14} aria-hidden="true" style={{ flexShrink: 0 }} />
            <strong>Not matched to a purchase</strong>
            <button type="button" className={mode === "match" ? "btn dark" : "btn"} aria-expanded={mode === "match"} onClick={() => setMode(mode === "match" ? null : "match")} style={{ ...SMALL, ...ROW }}><Link2 size={13} aria-hidden="true" />Match it</button>
          </>
        )}
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "flex-start" }}>
        <button type="button" className={mode === "change" ? "btn dark" : "btn"} aria-expanded={mode === "change"} onClick={() => setMode(mode === "change" ? null : "change")} style={{ ...SMALL, ...ROW }}><Pencil size={13} aria-hidden="true" />Change it</button>
        <button type="button" className="btn" disabled={!file?.has_text} onClick={() => setReading(reading === null ? 1 : null)} title={file?.has_text ? "Read the text the app took from it" : "No text could be read from it (a photo or a scan). Download it to see it."} style={{ ...SMALL, ...ROW }}><BookOpen size={13} aria-hidden="true" />{reading === null ? "Read" : "Close the text"}</button>
        <a className="btn" href={`/api/v1/documents/${r.id}/file`} download={file?.filename ?? ""} style={{ ...SMALL, ...ROW, textDecoration: "none" }}><Download size={13} aria-hidden="true" />Download</a>
        <ConfirmButton label="Remove" style={SMALL} question={`Remove the receipt from ${r.merchant || r.title}? The file, its text and its reminders are deleted from the app.`} yes="Yes, remove it" no="Keep it" onConfirm={remove} />
      </div>
      {mode === "match" && <MatchIt r={r} onDone={done} onClose={() => setMode(null)} />}
      {mode === "change" && <ChangeReceipt r={r} onDone={done} onClose={() => setMode(null)} />}
      {reading !== null && <Reader open={{ id: r.id, page: reading }} onMove={setReading} onClose={() => setReading(null)} />}
      {said && <Said text={said.text} tone={said.tone} />}
    </li>
  );
}

/** Add a receipt by hand: the file, what it says, and (once the day and the total are typed) the purchase it is for. */
function AddReceipt({ onAdded, onClose }: { onAdded: (text: string) => void; onClose: () => void }) {
  const [merchant, setMerchant] = useState("");
  const [total, setTotal] = useState("");
  const [bought, setBought] = useState("");
  const [returnBy, setReturnBy] = useState("");
  const [warranty, setWarranty] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [pick, setPick] = useState<number | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  const amount = total.trim() ? parseMoney(total) : null;
  const path = candidatesPath(amount, bought, merchant.trim());
  const found = useApi<CandidatesOut>(path);
  const list: ReceiptCandidate[] | null = path && found.data && !found.loading ? found.data.candidates : null;
  useEffect(() => setPick(undefined), [path]);
  const chosen = pick === undefined ? list?.find((c) => !c.has_receipt)?.id ?? list?.[0]?.id ?? null : pick;

  async function add(e: FormEvent) {
    e.preventDefault();
    if (!file) return setSaid("Choose the file first: a photo or a PDF of the receipt.");
    if (file.size > MAX_BYTES) return setSaid("That file is over 25 MB. A receipt is at most 25 MB.");
    if (total.trim() && amount === null) return setSaid("Type an amount like 120 or 1200.50");
    const store = merchant.trim();
    const attach = list && chosen !== null ? `transaction:${chosen}` : "";
    const form = new FormData();
    form.append("file", file);
    form.append("title", store ? `Receipt · ${store}` : "");
    form.append("kind", "receipt");
    form.append("attach_to", attach);
    form.append("merchant", store);
    form.append("amount", amount !== null ? String(amount) : "");
    form.append("bought_on", bought);
    form.append("return_by", returnBy);
    form.append("warranty_until", warranty);
    setBusy(true);
    setSaid(null);
    try {
      const doc = await api<KeptDocument>("/api/v1/documents", { method: "POST", body: form });
      const c = attach ? list?.find((x) => x.id === chosen) : undefined;
      onAdded(`Kept the receipt${store ? ` from ${store}` : ""}${c ? ` with the ${c.merchant || "purchase"} purchase on ${shortDate(c.date)}` : ", not matched to a purchase"}.${doc.period ? ` It is under ${monthWord(doc.period)}.` : ""}`);
    } catch (err) {
      setSaid(`Couldn't add it: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form id="add-receipt" onSubmit={add} style={{ display: "flex", flexDirection: "column", gap: 10, borderTop: "1px solid var(--surface-2)", paddingTop: 12, scrollMarginTop: 12 }}>
      <h3 style={{ margin: 0, fontSize: 14, fontWeight: 800 }}>Add a receipt by hand</h3>
      <label style={{ ...FIELD, minWidth: 0 }}>
        <span className="lab">The receipt (a photo or a PDF; 25 MB at most)</span>
        <input className="input" type="file" accept={ACCEPT} onChange={(e) => setFile(e.target.files?.[0] ?? null)} style={{ maxWidth: "100%" }} />
      </label>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
        <label style={{ ...FIELD, flex: "1 1 160px", minWidth: 0 }}>
          <span className="lab">Store</span>
          <input className="input" value={merchant} onChange={(e) => setMerchant(e.target.value)} maxLength={80} />
        </label>
        <label style={{ ...FIELD, flex: "0 1 120px", minWidth: 0 }}>
          <span className="lab">Total</span>
          <input className="input num" inputMode="decimal" value={total} onChange={(e) => setTotal(e.target.value)} placeholder="42.50" style={{ width: "100%" }} />
        </label>
        <label style={{ ...FIELD, flex: "0 1 160px", minWidth: 0 }}>
          <span className="lab">Day bought</span>
          <input className="input" type="date" value={bought} onChange={(e) => setBought(e.target.value)} />
        </label>
        <label style={{ ...FIELD, flex: "0 1 160px", minWidth: 0 }}>
          <span className="lab">Last day to return</span>
          <input className="input" type="date" value={returnBy} onChange={(e) => setReturnBy(e.target.value)} />
        </label>
        <label style={{ ...FIELD, flex: "0 1 160px", minWidth: 0 }}>
          <span className="lab">Warranty until</span>
          <input className="input" type="date" value={warranty} onChange={(e) => setWarranty(e.target.value)} />
        </label>
      </div>
      {total.trim() && amount === null && <Said text="Type an amount like 120 or 1200.50" tone="error" />}
      {!path ? (
        <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Type the total and the day bought, and the purchases it may belong to show here.</span>
      ) : found.error && !found.data ? (
        <Failed error={found.error} what="the purchases it may belong to" onRetry={found.reload} card={false} />
      ) : !list ? (
        <Loading what="the purchases it may belong to" card={false} />
      ) : list.length === 0 ? (
        <span style={{ fontSize: 13, color: "var(--ink-2)" }}>No purchase of {money(amount ?? 0, { cents: true })} was found near {shortDate(bought)}. It is kept without a purchase; match it later.</span>
      ) : (
        <PurchaseChoices name="add-receipt-purchase" legend="The purchase it is for" list={list} value={chosen} onPick={setPick} none="Keep it without a purchase" />
      )}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button className="btn dark" type="submit" disabled={busy} style={ROW}><Upload size={14} aria-hidden="true" />{busy ? "Adding…" : "Keep the receipt"}</button>
        <button className="btn" type="button" onClick={onClose}>Cancel</button>
        <Said text={said} tone="error" />
      </div>
    </form>
  );
}

function Receipts({ state, files, adding, setAdding, onChanged }: { state: Loaded<ReceiptsOut>; files: Map<number, KeptDocument>; adding: boolean; setAdding: (on: boolean) => void; onChanged: () => void }) {
  const [said, setSaid] = useState<string | null>(null);
  const data = state.data;
  return (
    <section id="receipts" className="card" style={{ ...CARD, scrollMarginTop: 12 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2 style={H2}>Receipts</h2>
        {data && data.count > 0 && <span className="num" style={{ fontSize: 12, color: "var(--ink-2)" }}>{data.count} kept, adding up to {money(data.total, { cents: true })}{data.unmatched.length > 0 ? ` · ${data.unmatched.length} not matched to a purchase` : ""}</span>}
      </div>
      <span style={{ fontSize: 12, color: "var(--ink-2)" }}>A receipt forwarded to a connected mailbox waits on <Link to="/confirm">Needs you</Link>, then is kept here with its purchase. Its last day to return and its warranty's end come back as reminders (<Link to="/settings/notifications">Notifications</Link>).</span>
      {state.error && !data ? (
        <Failed error={state.error} what="your receipts" onRetry={state.reload} card={false} />
      ) : !data ? (
        <Loading what="your receipts" card={false} />
      ) : data.receipts.length === 0 ? (
        <Empty card={false}>No receipts kept yet. Forward one to a connected mailbox, or add one by hand below.</Empty>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column" }}>
          {data.receipts.map((r, i) => <ReceiptItem key={r.id} r={r} file={files.get(r.id)} first={i === 0} onChanged={onChanged} onRemoved={setSaid} />)}
        </ul>
      )}
      {adding ? (
        <AddReceipt
          onAdded={(text) => {
            setSaid(text);
            setAdding(false);
            onChanged();
          }}
          onClose={() => setAdding(false)}
        />
      ) : (
        <button type="button" className="btn" aria-expanded={false} onClick={() => { setSaid(null); setAdding(true); }} style={{ ...SMALL, ...ROW, alignSelf: "flex-start" }}>
          <Upload size={13} aria-hidden="true" />Add a receipt by hand
        </button>
      )}
      <Said text={said} />
    </section>
  );
}

// ---- by month --------------------------------------------------------------------------------------------------

const BAR = 64; // the tallest bar, in pixels

/** One month as a small bar split by kind, its count above it and its name below. */
function MonthBar({ m, tallest, on, onPick }: { m: DocumentMonth; tallest: number; on: boolean; onPick: () => void }) {
  const [mon, year] = monthWord(m.month).split(" ");
  const parts = Object.entries(m.kinds).sort((a, b) => GROUPS.indexOf(groupOf(a[0])) - GROUPS.indexOf(groupOf(b[0])));
  const said = parts.map(([k, n]) => `${DOC_KIND_WORD[k] ?? k}: ${n}`).join(", ");
  return (
    <button type="button" aria-pressed={on} onClick={onPick} aria-label={`${monthWord(m.month)}: ${m.count} file${m.count === 1 ? "" : "s"}, ${said}`} title={said}
      style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 3, minWidth: 48, padding: "6px 4px", borderRadius: 10, border: `2px solid ${on ? "var(--ink)" : "transparent"}`, background: on ? "var(--surface-2)" : "none", font: "inherit", color: "var(--ink)", cursor: "pointer", flexShrink: 0 }}>
      <span className="num" style={{ fontSize: 12, fontWeight: 800 }}>{m.count}</span>
      <span aria-hidden="true" style={{ height: BAR, display: "flex", flexDirection: "column", justifyContent: "flex-end", width: 22 }}>
        <span style={{ display: "flex", flexDirection: "column-reverse", gap: 1, height: Math.max(4, Math.round((m.count / tallest) * BAR)), borderRadius: 4, overflow: "hidden" }}>
          {parts.map(([k, n]) => <span key={k} style={{ flex: `${n} 1 0`, minHeight: 2, background: groupOf(k).fill }} />)}
        </span>
      </span>
      <span style={{ fontSize: 12, fontWeight: on ? 800 : 600, lineHeight: 1.1 }}>{mon}</span>
      <span style={{ fontSize: 10, color: "var(--ink-2)", lineHeight: 1.1 }}>{year}</span>
    </button>
  );
}

/** Keep a file with another month (or with none). */
function MonthMove({ d, onMoved }: { d: KeptDocument; onMoved: (text: string) => void }) {
  const [open, setOpen] = useState(false);
  const [period, setPeriod] = useState(d.period ?? "");
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  async function save(e: FormEvent) {
    e.preventDefault();
    const month = period.trim();
    if (month && !/^\d{4}-\d{2}$/.test(month)) return setSaid("Type the month like 2026-10, or leave it blank for no month.");
    setBusy(true);
    setSaid(null);
    try {
      await api(`/api/v1/documents/${d.id}`, { method: "PUT", body: JSON.stringify({ period: month }) });
      setOpen(false);
      onMoved(month ? `“${d.title}” is under ${monthWord(month)} now.` : `“${d.title}” has no month now.`);
    } catch (err) {
      setSaid(`Couldn't move it: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }
  if (!open) {
    return <button type="button" className="btn" aria-expanded={false} onClick={() => setOpen(true)} style={{ ...SMALL, ...ROW }}><CalendarClock size={13} aria-hidden="true" />{d.period ? "Keep it with another month" : "Keep it with a month"}</button>;
  }
  return (
    <form onSubmit={save} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
      <label style={{ ...FIELD, minWidth: 0 }}>
        <span className="lab">Month (blank: no month)</span>
        <input className="input" type="month" value={period} onChange={(e) => setPeriod(e.target.value)} placeholder="2026-10" style={{ minHeight: 34, padding: "4px 10px" }} />
      </label>
      <button className="btn dark" type="submit" disabled={busy} style={SMALL}>{busy ? "Saving…" : "Save"}</button>
      <button className="btn" type="button" onClick={() => setOpen(false)} style={SMALL}>Cancel</button>
      <Said text={said} tone="error" />
    </form>
  );
}

function ByMonth({ state, onChanged }: { state: Loaded<DocumentMonthsOut>; onChanged: () => void }) {
  const [pick, setPick] = useState<string | null>(null);
  const [reading, setReading] = useState<{ id: number; page: number } | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const title = (
    <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
      <h2 style={H2}>By month</h2>
      <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Statements, paystubs and receipts, each kept with its month. Pick a month to see its files.</span>
    </div>
  );
  if (state.error && !state.data) return <section className="card" style={CARD}>{title}<Failed error={state.error} what="your files by month" onRetry={state.reload} card={false} /></section>;
  if (!state.data) return <section className="card" style={CARD}>{title}<Loading what="your files by month" card={false} /></section>;
  const { months, undated } = state.data;
  const shown = (pick === "none" && undated.length > 0) || (pick && months.some((m) => m.month === pick)) ? pick : months[0]?.month ?? (undated.length ? "none" : null);
  const tallest = Math.max(1, ...months.map((m) => m.count), undated.length);
  const files = shown === "none" ? undated : months.find((m) => m.month === shown)?.documents ?? [];
  const changed = (text: string) => {
    setSaid(text);
    onChanged();
  };
  async function remove(d: KeptDocument) {
    await api(`/api/v1/documents/${d.id}`, { method: "DELETE" });
    if (reading?.id === d.id) setReading(null);
    changed(`Removed “${d.title}”.`);
  }

  return (
    <section className="card" style={CARD}>
      {title}
      {months.length === 0 && undated.length === 0 ? (
        <Empty card={false}>No files kept yet. Import a statement or a paystub, or add a file below.</Empty>
      ) : (
        <>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", fontSize: 12, fontWeight: 700, color: "var(--ink-2)" }} aria-label="What the colours mean">
            {GROUPS.map((g) => <span key={g.key}><span className="sw" style={{ background: g.fill }} />{g.word}</span>)}
            {undated.length > 0 && <span><span className="sw" style={{ border: "1.5px dashed var(--ink-2)", boxSizing: "border-box" }} />No month</span>}
          </div>
          <div role="group" aria-label="Pick a month" style={{ overflowX: "auto", paddingBottom: 4 }}>
            <div style={{ display: "flex", gap: 4, alignItems: "flex-end", width: "max-content", minWidth: "100%" }}>
              {[...months].reverse().map((m) => <MonthBar key={m.month} m={m} tallest={tallest} on={shown === m.month} onPick={() => { setPick(m.month); setReading(null); }} />)}
              {undated.length > 0 && (
                <button type="button" aria-pressed={shown === "none"} onClick={() => { setPick("none"); setReading(null); }} aria-label={`No month: ${undated.length} file${undated.length === 1 ? "" : "s"}`}
                  style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 3, minWidth: 56, padding: "6px 4px", borderRadius: 10, border: `2px solid ${shown === "none" ? "var(--ink)" : "transparent"}`, background: shown === "none" ? "var(--surface-2)" : "none", font: "inherit", color: "var(--ink)", cursor: "pointer", flexShrink: 0, marginLeft: 8 }}>
                  <span className="num" style={{ fontSize: 12, fontWeight: 800 }}>{undated.length}</span>
                  <span aria-hidden="true" style={{ height: BAR, display: "flex", flexDirection: "column", justifyContent: "flex-end", width: 22 }}>
                    <span style={{ height: Math.max(4, Math.round((undated.length / tallest) * BAR)), borderRadius: 4, border: "1.5px dashed var(--ink-2)", boxSizing: "border-box" }} />
                  </span>
                  <span style={{ fontSize: 12, fontWeight: shown === "none" ? 800 : 600, lineHeight: 1.1 }}>No</span>
                  <span style={{ fontSize: 10, color: "var(--ink-2)", lineHeight: 1.1 }}>month</span>
                </button>
              )}
            </div>
          </div>
          {shown && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <h3 style={{ margin: 0, fontSize: 14, fontWeight: 800 }}>{shown === "none" ? "No month" : monthWord(shown)} · {files.length} file{files.length === 1 ? "" : "s"}</h3>
              {shown === "none" && <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Files kept without a month. Give one a month to find it with the rest of that month.</span>}
              {reading && <Reader open={reading} onMove={(page) => setReading({ id: reading.id, page })} onClose={() => setReading(null)} />}
              <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column" }}>
                {files.map((d, i) => (
                  <DocLine key={d.id} d={d} first={i === 0} place={placeWord(d.attach_to)} onRead={(id) => setReading({ id, page: 1 })} onRemove={remove}
                    extra={<MonthMove d={d} onMoved={changed} />} />
                ))}
              </ul>
            </div>
          )}
        </>
      )}
      <Said text={said} />
      <Documents hideList startClosed onChanged={onChanged} />
    </section>
  );
}

// ---- the page ------------------------------------------------------------------------------------------------------

export function Files() {
  const receipts = useApi<ReceiptsOut>("/api/v1/receipts");
  const months = useApi<DocumentMonthsOut>("/api/v1/documents/months");
  const receiptFiles = useApi<KeptDocumentsOut>("/api/v1/documents?kind=receipt");
  const [reading, setReading] = useState<{ id: number; page: number } | null>(null);
  const { hash } = useLocation();
  const [adding, setAdding] = useState(hash === "#add-receipt");
  const files = new Map((receiptFiles.data?.documents ?? []).map((d) => [d.id, d]));

  // /documents#receipts lands on the receipts once they have loaded; #add-receipt opens the form there.
  useEffect(() => {
    if (hash === "#add-receipt") setAdding(true);
    if (hash && receipts.data) window.requestAnimationFrame(() => (document.getElementById(hash.slice(1)) ?? document.getElementById("receipts"))?.scrollIntoView({ block: "start" }));
  }, [hash, receipts.data]);

  function changed() {
    receipts.reload();
    months.reload();
    receiptFiles.reload();
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 960 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Documents</h1>
        <p style={{ margin: 0, color: "var(--ink-2)", fontSize: 13 }}>Receipts, statements, paystubs, your lease: every file you keep, by month. Return and warranty dates come back as reminders.</p>
      </div>
      <ComingUp state={receipts} />
      <Receipts state={receipts} files={files} adding={adding} setAdding={setAdding} onChanged={changed} />
      <ByMonth state={months} onChanged={changed} />
      <section className="card" style={CARD}>
        <h2 style={H2}>Search every document</h2>
        <SearchBox onOpen={(id, page) => setReading({ id, page })} placeOf={placeWord} />
        {reading && <Reader open={reading} onMove={(page) => setReading({ id: reading.id, page })} onClose={() => setReading(null)} />}
      </section>
      {receipts.data?.not_advice && <div style={{ fontSize: 12, color: "var(--muted)" }}>{receipts.data.not_advice}</div>}
    </div>
  );
}

export default Files;
