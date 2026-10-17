// Invoices (M57): bills the business sends, and bills it receives. What is owed to you leads, then the invoices still
// waiting for payment. A sent invoice is matched to the deposit that paid it (the engine offers the deposits of the
// same amount) or marked paid another way. The total of each invoice is the server's, worked out when it is saved.
import { Ban, CircleCheck, FilePen, Inbox, Plus, Printer, Send, X } from "lucide-react";
import { useState, type CSSProperties } from "react";
import { api, useApi } from "../api";
import { ConfirmButton } from "../components/ConfirmButton";
import { Said, Show } from "../components/State";
import { localToday, money, parseMoney } from "../format";
import type { Invoice, InvoiceIn, InvoiceLine, InvoicesOut, TaxLine } from "../types-business";
import { BusinessPick, CARD, FIELD, H2, NeedsBusiness, NotAdvice, PageHead, Pill, SMALL, Tile, dayWithYear, useChosenBusiness } from "./Business";

type Note = { text: string; tone: "info" | "error" } | null;
const ORDER: Record<Invoice["status"], number> = { sent: 0, draft: 1, paid: 2, void: 3 };

function look(inv: Invoice) {
  switch (inv.status) {
    case "draft":
      return { Icon: FilePen, word: "Draft", tone: "plain" as const };
    case "sent":
      return inv.direction === "out" ? { Icon: Send, word: "Sent", tone: "info" as const } : { Icon: Inbox, word: "To pay", tone: "info" as const };
    case "paid":
      return { Icon: CircleCheck, word: "Paid", tone: "good" as const };
    default:
      return { Icon: Ban, word: "Void", tone: "plain" as const };
  }
}

/** An invoice as the server keeps it, with one thing changed, ready to save again. */
function bodyOf(inv: Invoice, change: Partial<InvoiceIn>): InvoiceIn {
  return {
    direction: inv.direction, counterparty: inv.counterparty, issued_on: inv.issued_on, due_on: inv.due_on,
    lines: inv.lines.map((l) => ({ what: l.what, quantity: l.quantity, price: l.price })),
    status: inv.status === "paid" ? "sent" : inv.status, tax_line: inv.tax_line, note: inv.note, number: inv.number, ...change,
  };
}

function lineWords(lines: TaxLine[], key: string): string {
  const l = lines.find((x) => x.key === key);
  return l ? `Line ${l.line}: ${l.words}` : "No line chosen yet";
}

export function Invoices() {
  const { overview, business, choose } = useChosenBusiness();
  const invoices = useApi<InvoicesOut>(business ? `/api/v1/business/${business.id}/invoices` : null, { live: true });
  const [adding, setAdding] = useState<"out" | "in" | null>(null);
  const [said, setSaid] = useState<Note>(null);
  const lines = overview.data?.lines ?? [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <PageHead back title="Invoices" sub="Bills you send to the people you work for, and bills the business receives." />
      <Show state={overview} what="your business">
        {(o) => !business ? (
          <NeedsBusiness>Invoices belong to a business. Add yours on Business first.</NeedsBusiness>
        ) : (
          <>
            <BusinessPick businesses={o.businesses} value={business.id} onChange={choose} />
            <Show state={invoices} what="the invoices">
              {(d) => {
                const out = d.invoices.filter((i) => i.direction === "out").sort((a, b) => ORDER[a.status] - ORDER[b.status]);
                const received = d.invoices.filter((i) => i.direction === "in").sort((a, b) => ORDER[a.status] - ORDER[b.status]);
                return (
                  <>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 10 }}>
                      <Tile label="Owed to you" value={money(d.owed_to_you, { cents: true })} accent="var(--paycheck)" sub="on invoices sent and not paid yet" />
                      <Tile label="Waiting for payment" value={d.unpaid} isMoney={false} accent="var(--saving)" sub={d.unpaid === 1 ? "invoice sent, not paid yet" : "invoices sent, not paid yet"} />
                    </div>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                      <button type="button" className={adding === "out" ? "btn" : "btn dark"} aria-expanded={adding === "out"} onClick={() => { setAdding(adding === "out" ? null : "out"); setSaid(null); }} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                        <Plus size={16} aria-hidden="true" /> New invoice
                      </button>
                      <button type="button" className="btn" aria-expanded={adding === "in"} onClick={() => { setAdding(adding === "in" ? null : "in"); setSaid(null); }} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                        <Inbox size={16} aria-hidden="true" /> Add a bill the business received
                      </button>
                      {said && <Said text={said.text} tone={said.tone} />}
                    </div>
                    {adding && (
                      <section className="card" style={CARD} aria-labelledby="new-title">
                        <h2 id="new-title" style={H2}>{adding === "out" ? "New invoice" : "A bill the business received"}</h2>
                        <InvoiceForm key={adding} businessId={business.id} direction={adding} start={null} lines={lines} onClose={() => setAdding(null)} onDone={(text) => { setAdding(null); setSaid({ text, tone: "info" }); }} />
                      </section>
                    )}

                    <section style={{ display: "flex", flexDirection: "column", gap: 10 }} aria-labelledby="sent-title">
                      <h2 id="sent-title" style={H2}><Send size={16} aria-hidden="true" /> Invoices you send</h2>
                      {out.length === 0 && <div className="card" style={{ ...CARD, fontSize: 13, color: "var(--ink-2)" }}>No invoices yet. Make the first with New invoice.</div>}
                      {out.map((i) => <InvoiceCard key={i.id} inv={i} lines={lines} onRemoved={(text) => setSaid({ text, tone: "info" })} />)}
                    </section>

                    <section style={{ display: "flex", flexDirection: "column", gap: 10 }} aria-labelledby="received-title">
                      <h2 id="received-title" style={H2}><Inbox size={16} aria-hidden="true" /> Bills the business received</h2>
                      {received.length === 0 && <div className="card" style={{ ...CARD, fontSize: 13, color: "var(--ink-2)" }}>None yet. A bill from someone who worked for the business, or for something it bought, goes here with the tax line it belongs on.</div>}
                      {received.map((i) => <InvoiceCard key={i.id} inv={i} lines={lines} onRemoved={(text) => setSaid({ text, tone: "info" })} />)}
                    </section>
                  </>
                );
              }}
            </Show>
            <NotAdvice text={o.guide.not_advice} />
          </>
        )}
      </Show>
    </div>
  );
}

function InvoiceCard({ inv, lines, onRemoved }: { inv: Invoice; lines: TaxLine[]; onRemoved: (text: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  const l = look(inv);
  const name = inv.number ? `invoice ${inv.number}` : inv.direction === "out" ? "this invoice" : "this bill";

  // Saves one change. A refusal is thrown: a question (Void it) shows it in place, and tryChange says it below.
  async function change(changeTo: Partial<InvoiceIn>, done: string) {
    setBusy(true);
    setNote(null);
    try {
      await api<Invoice>(`/api/v1/business/invoices/${inv.id}`, { method: "PUT", body: JSON.stringify(bodyOf(inv, changeTo)) });
      setNote({ text: done, tone: "info" });
    } finally {
      setBusy(false);
    }
  }
  const tryChange = (changeTo: Partial<InvoiceIn>, done: string) => change(changeTo, done).catch((err: Error) => setNote({ text: `Couldn't save that: ${err.message}`, tone: "error" }));

  if (editing) {
    return (
      <article className="card" style={CARD} aria-label={`Changing ${name}`}>
        <h3 style={{ ...H2, fontSize: 14 }}>Changing {name}</h3>
        <InvoiceForm businessId={inv.business_id} direction={inv.direction} start={inv} lines={lines} onClose={() => setEditing(false)} onDone={(text) => { setEditing(false); setNote({ text, tone: "info" }); }} />
      </article>
    );
  }
  const muted = inv.status === "void";
  return (
    <article className="card" style={{ ...CARD, opacity: muted ? 0.75 : 1 }} aria-label={`${inv.counterparty}, ${l.word}`}>
      <div style={{ display: "flex", gap: 10, alignItems: "flex-start", flexWrap: "wrap" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 2, flex: "1 1 200px", minWidth: 0 }}>
          <strong style={{ fontSize: 16, overflowWrap: "anywhere" }}>{inv.counterparty}</strong>
          <span style={{ fontSize: 12, color: "var(--muted)" }}>
            {inv.number ? `Invoice ${inv.number}` : "No number"} · dated {dayWithYear(inv.issued_on)}{inv.due_on ? ` · due ${dayWithYear(inv.due_on)}` : ""}
          </span>
        </div>
        <Pill Icon={l.Icon} word={l.word} tone={l.tone} />
        <span className="num" style={{ fontSize: 20, fontWeight: 800, textDecoration: muted ? "line-through" : undefined }}>{money(inv.amount, { cents: true })}</span>
      </div>
      <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", fontSize: 13 }}>
        {inv.lines.map((x, i) => (
          <li key={i} style={{ display: "flex", gap: 10, flexWrap: "wrap", padding: "4px 0", borderTop: "1px solid var(--surface-2)" }}>
            <span style={{ flex: "1 1 160px", minWidth: 0, overflowWrap: "anywhere" }}>{x.what}</span>
            <span className="num" style={{ color: "var(--ink-2)" }}>{x.quantity} × {money(x.price, { cents: true })}</span>
          </li>
        ))}
      </ul>
      {inv.note && <div style={{ fontSize: 13, color: "var(--ink-2)", overflowWrap: "anywhere" }}>{inv.note}</div>}
      {inv.status === "paid" && inv.paid_on && (
        <div style={{ fontSize: 13 }}>
          <CircleCheck size={14} aria-hidden="true" style={{ display: "inline-block", verticalAlign: -2, color: "var(--safe-ink)" }} /> Paid {dayWithYear(inv.paid_on)}{inv.transaction_id ? (inv.direction === "out" ? ", matched to the deposit" : ", matched to the payment") : ""}.
        </div>
      )}
      {inv.direction === "in" && (
        inv.status === "paid" ? (
          <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Tax line: {lineWords(lines, inv.tax_line)}</div>
        ) : (
          <label style={{ ...FIELD, maxWidth: 420 }}>
            <span className="lab">Which tax line it goes on</span>
            <select className="input" value={inv.tax_line} disabled={busy} onChange={(e) => tryChange({ tax_line: e.target.value }, `Now on ${lineWords(lines, e.target.value).toLowerCase()}.`)}>
              <option value="">Not chosen yet</option>
              {lines.map((t) => <option key={t.key} value={t.key}>Line {t.line}: {t.words}</option>)}
            </select>
          </label>
        )
      )}
      {inv.status === "sent" && <PaidChooser inv={inv} onSaid={setNote} />}
      {inv.direction === "out" || (inv.status !== "paid" && inv.status !== "void") ? (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", borderTop: "1px solid var(--surface-2)", paddingTop: 10 }}>
          {inv.direction === "out" && (
            <a href={`/api/v1/business/invoices/${inv.id}/print`} target="_blank" rel="noopener noreferrer" className="btn" style={{ ...SMALL, display: "inline-flex", alignItems: "center", gap: 6, textDecoration: "none" }}>
              <Printer size={15} aria-hidden="true" /> Print or save as PDF
            </a>
          )}
          {inv.status === "draft" && inv.direction === "out" && (
            <button type="button" className="btn dark" disabled={busy} onClick={() => tryChange({ status: "sent" }, "Marked as sent. It now counts as owed to you.")} style={{ ...SMALL, display: "inline-flex", alignItems: "center", gap: 6 }}>
              <Send size={15} aria-hidden="true" /> Mark as sent
            </button>
          )}
          {(inv.status === "draft" || inv.status === "sent") && <button type="button" className="btn" disabled={busy} onClick={() => setEditing(true)} style={SMALL}>Change it</button>}
          {inv.status === "draft" && (
            <ConfirmButton
              label="Remove"
              question={`Remove ${name}? A draft is gone for good.`}
              yes="Yes, remove it"
              style={SMALL}
              onConfirm={async () => {
                await api(`/api/v1/business/invoices/${inv.id}`, { method: "DELETE" });
                onRemoved(`Removed the draft for ${inv.counterparty}.`);
              }}
            />
          )}
          {inv.status === "sent" && (
            <ConfirmButton
              label="Void it"
              question={`Void ${name}? It stops counting as ${inv.direction === "out" ? "owed to you" : "owed"}; its number stays on record.`}
              yes="Yes, void it"
              style={SMALL}
              onConfirm={() => change({ status: "void" }, "Voided. It stays on the list, crossed out.")}
            />
          )}
        </div>
      ) : null}
      {note && <div><Said text={note.text} tone={note.tone} /></div>}
    </article>
  );
}

/** For a sent invoice: the deposits (or payments) of the same amount the engine found, or paid another way. */
function PaidChooser({ inv, onSaid }: { inv: Invoice; onSaid: (n: Note) => void }) {
  const [choice, setChoice] = useState("");
  const [day, setDay] = useState(localToday());
  const [busy, setBusy] = useState(false);
  const candidates = inv.candidates ?? [];
  const word = inv.direction === "out" ? "This deposit" : "This payment";
  const radio: CSSProperties = { width: 20, height: 20, margin: "1px 0 0", flexShrink: 0, cursor: "pointer" };
  async function go() {
    if (!choice) return;
    if (choice === "other" && !day) return onSaid({ text: "Pick the day it was paid.", tone: "error" });
    setBusy(true);
    try {
      const body = choice === "other" ? { paid_on: day } : { transaction_id: Number(choice) };
      await api<Invoice>(`/api/v1/business/invoices/${inv.id}/paid`, { method: "POST", body: JSON.stringify(body) });
      onSaid({ text: choice === "other" ? `Marked paid on ${dayWithYear(day)}.` : `Marked paid by that ${inv.direction === "out" ? "deposit" : "payment"}; it now counts as the business's.`, tone: "info" });
      setChoice("");
    } catch (err) {
      onSaid({ text: `Couldn't mark it paid: ${(err as Error).message}`, tone: "error" });
    } finally {
      setBusy(false);
    }
  }
  return (
    <fieldset style={{ border: 0, margin: 0, padding: "10px 12px", borderRadius: 12, background: "var(--surface-2)", display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
      <legend className="lab" style={{ float: "left", padding: 0, marginBottom: 2 }}>Has it been paid?</legend>
      {candidates.length === 0 && (
        <div style={{ fontSize: 13, color: "var(--ink-2)", clear: "both" }}>
          No {inv.direction === "out" ? "deposit" : "payment"} of the same amount yet, from its date on. When one arrives, it shows here to pick.
        </div>
      )}
      {candidates.map((c) => (
        <label key={c.id} style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: 14, cursor: "pointer", clear: "both" }}>
          <input type="radio" name={`paid-${inv.id}`} value={c.id} checked={choice === String(c.id)} onChange={() => setChoice(String(c.id))} style={radio} />
          <span style={{ minWidth: 0, overflowWrap: "anywhere" }}><strong>{word}:</strong> {dayWithYear(c.date)} · {c.merchant} · <span className="num">{money(c.amount, { cents: true })}</span></span>
        </label>
      ))}
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", clear: "both" }}>
        <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 14, cursor: "pointer" }}>
          <input type="radio" name={`paid-${inv.id}`} value="other" checked={choice === "other"} onChange={() => setChoice("other")} style={radio} />
          <span><strong>Paid another way</strong>, on</span>
        </label>
        <input className="input" type="date" value={day} max={localToday()} onChange={(e) => { setDay(e.target.value); setChoice("other"); }} aria-label="The day it was paid" style={{ minHeight: 36, padding: "4px 10px" }} />
      </div>
      <div>
        <button type="button" className="btn dark" disabled={busy || !choice} onClick={go} style={{ ...SMALL, display: "inline-flex", alignItems: "center", gap: 6 }}>
          <CircleCheck size={15} aria-hidden="true" /> {busy ? "Saving…" : "Mark it paid"}
        </button>
      </div>
    </fieldset>
  );
}

interface RowDraft {
  what: string;
  quantity: string;
  price: string;
}

function InvoiceForm({ businessId, direction, start, lines, onDone, onClose }: {
  businessId: number;
  direction: "out" | "in";
  start: Invoice | null;
  lines: TaxLine[];
  onDone: (text: string) => void;
  onClose: () => void;
}) {
  const [who, setWho] = useState(start?.counterparty ?? "");
  const [issued, setIssued] = useState(start?.issued_on ?? localToday());
  const [due, setDue] = useState(start?.due_on ?? "");
  const [rows, setRows] = useState<RowDraft[]>(() => start && start.lines.length ? start.lines.map((l) => ({ what: l.what, quantity: String(l.quantity), price: String(l.price) })) : [{ what: "", quantity: "1", price: "" }]);
  const [note, setNote] = useState(start?.note ?? "");
  const [number, setNumber] = useState(start?.number ?? "");
  const [taxLine, setTaxLine] = useState(start?.tax_line ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const out = direction === "out";

  function setRow(i: number, part: Partial<RowDraft>) {
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...part } : r)));
  }

  function readLines(): InvoiceLine[] | string {
    const kept: InvoiceLine[] = [];
    for (const [i, r] of rows.entries()) {
      const what = r.what.trim();
      const quantity = Number(r.quantity.trim() || "1");
      const price = parseMoney(r.price);
      if (!what) return `Line ${i + 1}: say what it was for.`;
      if (!Number.isFinite(quantity) || quantity < 0) return `Line ${i + 1}: the quantity is a number, like 1 or 2.5.`;
      if (price === null || price < 0) return `Line ${i + 1}: the price is an amount, like 150 or 150.00.`;
      kept.push({ what, quantity, price });
    }
    return kept;
  }

  async function save(status: "draft" | "sent") {
    if (!who.trim()) return setError(out ? "Say who the invoice is for." : "Say who the bill is from.");
    if (!issued) return setError("Pick the invoice's date.");
    const kept = readLines();
    if (typeof kept === "string") return setError(kept);
    const body: InvoiceIn = { direction, counterparty: who.trim(), issued_on: issued, due_on: due || null, lines: kept, status, tax_line: out ? (start?.tax_line ?? "") : taxLine, note: note.trim(), number: number.trim() };
    setBusy(true);
    setError(null);
    try {
      const saved = await api<Invoice>(start ? `/api/v1/business/invoices/${start.id}` : `/api/v1/business/${businessId}/invoices`, { method: start ? "PUT" : "POST", body: JSON.stringify(body) });
      const what = out ? `Invoice${saved.number ? ` ${saved.number}` : ""}` : "The bill";
      onDone(`${what} saved${out ? (status === "sent" ? " as sent" : " as a draft") : ""}: ${money(saved.amount, { cents: true })} in all.`);
    } catch (err) {
      setError(`Couldn't save it: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  const small: CSSProperties = { minHeight: 38, padding: "6px 10px" };
  return (
    <form onSubmit={(e) => { e.preventDefault(); save(start?.status === "sent" || !out ? "sent" : "draft"); }} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <label style={{ ...FIELD, flex: "2 1 220px" }}>
          <span className="lab">{out ? "Who it is for" : "Who it is from"}</span>
          <input className="input" value={who} onChange={(e) => setWho(e.target.value)} maxLength={120} required />
        </label>
        <label style={{ ...FIELD, flex: "1 1 150px" }}>
          <span className="lab">Date</span>
          <input className="input" type="date" value={issued} onChange={(e) => setIssued(e.target.value)} required />
        </label>
        <label style={{ ...FIELD, flex: "1 1 150px" }}>
          <span className="lab">Due by</span>
          <input className="input" type="date" value={due} min={issued || undefined} onChange={(e) => setDue(e.target.value)} />
        </label>
      </div>
      <fieldset style={{ border: 0, margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
        <legend className="lab" style={{ padding: 0, marginBottom: 6 }}>What it is for</legend>
        {rows.map((r, i) => (
          <div key={i} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end", paddingBottom: 8, borderBottom: "1px solid var(--surface-2)" }}>
            <label style={{ ...FIELD, flex: "3 1 200px" }}>
              <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Line {i + 1}: what</span>
              <input className="input" value={r.what} onChange={(e) => setRow(i, { what: e.target.value })} maxLength={200} placeholder="Website updates, March" style={small} />
            </label>
            <label style={{ ...FIELD, flex: "0 1 90px" }}>
              <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Quantity</span>
              <input className="input" value={r.quantity} onChange={(e) => setRow(i, { quantity: e.target.value })} inputMode="decimal" style={small} />
            </label>
            <label style={{ ...FIELD, flex: "1 1 110px" }}>
              <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Price each</span>
              <input className="input num" value={r.price} onChange={(e) => setRow(i, { price: e.target.value })} inputMode="decimal" placeholder="150.00" style={small} />
            </label>
            <button type="button" className="btn" disabled={rows.length < 2} onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))} aria-label={`Remove line ${i + 1}`} style={{ ...small, display: "inline-flex", alignItems: "center", gap: 4 }}>
              <X size={14} aria-hidden="true" /> Remove
            </button>
          </div>
        ))}
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <button type="button" className="btn" onClick={() => setRows((rs) => [...rs, { what: "", quantity: "1", price: "" }])} disabled={rows.length >= 100} style={{ ...SMALL, display: "inline-flex", alignItems: "center", gap: 6 }}>
            <Plus size={15} aria-hidden="true" /> Add a line
          </button>
          <span style={{ fontSize: 12, color: "var(--muted)" }}>The total is worked out by the app when you save.</span>
        </div>
      </fieldset>
      {!out && (
        <label style={{ ...FIELD, maxWidth: 420 }}>
          <span className="lab">Which tax line it goes on</span>
          <select className="input" value={taxLine} onChange={(e) => setTaxLine(e.target.value)}>
            <option value="">Not chosen yet</option>
            {lines.map((t) => <option key={t.key} value={t.key}>Line {t.line}: {t.words}</option>)}
          </select>
        </label>
      )}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <label style={{ ...FIELD, flex: "3 1 240px" }}>
          <span className="lab">Note</span>
          <input className="input" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder={out ? "Thank you. Payment by bank transfer." : ""} />
        </label>
        <label style={{ ...FIELD, flex: "1 1 140px" }}>
          <span className="lab">Number</span>
          <input className="input" value={number} onChange={(e) => setNumber(e.target.value)} maxLength={40} placeholder={out ? "the app numbers it" : "theirs, if it has one"} />
        </label>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        {out && start?.status !== "sent" && (
          <>
            <button type="button" className="btn" disabled={busy} onClick={() => save("draft")} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <FilePen size={15} aria-hidden="true" /> Save as a draft
            </button>
            <button type="button" className="btn dark" disabled={busy} onClick={() => save("sent")} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <Send size={15} aria-hidden="true" /> Save as sent
            </button>
          </>
        )}
        {(!out || start?.status === "sent") && (
          <button type="submit" className="btn dark" disabled={busy}>{busy ? "Saving…" : start ? "Save the changes" : "Save it"}</button>
        )}
        <button type="button" className="btn" disabled={busy} onClick={onClose}>Close</button>
        {busy && <span style={{ fontSize: 12, color: "var(--muted)" }}>Saving…</span>}
        {error && <Said text={error} tone="error" />}
      </div>
    </form>
  );
}
