// Documents kept with what they belong to: a lease with its home, a utility's terms with the utility, a receipt with
// its purchase. Add one, see the list, read a page of the text the app took from it, search every document, download
// the original, or remove it. Any file can be kept with its month; a receipt also keeps its store, total and dates.
// The file stays encrypted on the server; what shows here is the text read from it, page by page.
import { BookOpen, ChevronLeft, ChevronRight, Download, FileText, Receipt, Search, Upload, X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { localToday, money, monthYear, parseMoney, shortDate, when } from "../format";
import type { DocumentHit, DocumentPage } from "../types";
import type { KeptDocument, KeptDocumentsOut, ReceiptCandidate } from "../types-receipts";
import { ConfirmButton } from "./ConfirmButton";
import { Empty, Failed, Loading, Said } from "./State";

export const DOC_KIND_WORD: Record<string, string> = {
  lease: "Lease",
  addendum: "Lease addendum",
  charges: "Charge sheet from the property",
  welcome: "Welcome letter",
  utility_terms: "Utility terms",
  card_agreement: "Card agreement",
  receipt: "Receipt",
  statement: "Statement",
  paystub: "Paystub",
  other: "Something else",
};
// A lease, an addendum, a charge sheet or a welcome letter kept with a home has its terms read and offered on Needs you.
const TERMS_KINDS = new Set(["lease", "addendum", "charges", "welcome"]);
const ACCEPT = ".pdf,.png,.jpg,.jpeg,.xlsx,.txt,.csv,.md";
const MAX_BYTES = 25 * 1024 * 1024;
const MONTH = /^\d{4}-\d{2}$/;
const SMALL = { minHeight: 32, padding: "4px 10px" } as const;
const ROW = { display: "inline-flex", alignItems: "center", gap: 4 } as const;
const FIELD = { display: "flex", flexDirection: "column", gap: 4 } as const;

type Note = { text: string; tone: "info" | "error"; terms?: boolean; homeless?: boolean } | null;

/** A file's size in words: "340 KB", "2.1 MB". */
function sizeWord(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function pagesWord(d: KeptDocument): string {
  if (!d.has_text) return "no text could be read";
  return `${d.pages} page${d.pages === 1 ? "" : "s"} of text${d.read_with === "ocr" ? ", read from the picture" : ""}`;
}

/** What a receipt's words seem to say (its total, its day), offered to keep with one tap; never kept on its own. */
function Suggested({ d, onChanged }: { d: KeptDocument; onChanged?: () => void }) {
  const [state, setState] = useState<"idle" | "busy" | "kept" | string>("idle");
  const s = d.suggested;
  if (!s || state === "kept") return state === "kept" ? <Said text="Kept with the receipt." /> : null;
  const parts = [s.amount !== undefined ? `total ${money(s.amount, { cents: true })}` : "", s.bought_on ? `bought ${dayWord(s.bought_on)}` : ""].filter(Boolean);
  async function keep() {
    setState("busy");
    try {
      await api(`/api/v1/documents/${d.id}`, { method: "PUT", body: JSON.stringify(s) });
      setState("kept");
      onChanged?.();
    } catch (e) {
      setState((e as Error).message);
    }
  }
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 12, background: "var(--surface-2)", borderRadius: 10, padding: "6px 10px" }}>
      <span className="num" style={{ flex: "1 1 180px", minWidth: 0 }}>Read from it: {parts.join(", ")}. Check it against the receipt.</span>
      <button type="button" className="btn" style={SMALL} disabled={state === "busy"} onClick={keep}>{state === "busy" ? "Keeping…" : "Keep these"}</button>
      {state !== "idle" && state !== "busy" && <Said text={state} tone="error" />}
    </div>
  );
}

/** A day in words, with the year when it is not this year: "Sun Nov 8", "Mon Oct 9, 2028". */
export function dayWord(iso: string): string {
  return iso.slice(0, 4) === localToday().slice(0, 4) ? shortDate(iso) : `${shortDate(iso)}, ${iso.slice(0, 4)}`;
}

/** A kept month in words: "2026-10" is "Oct 2026". */
export function monthWord(period: string): string {
  return MONTH.test(period) ? monthYear(`${period}-01`) : period;
}

/** A receipt's facts in one line: the store, the total, the day bought, the last day to return, the warranty's end. */
export function receiptWords(d: { merchant?: string; amount?: number | null; bought_on?: string | null; return_by?: string | null; warranty_until?: string | null }): string {
  return [
    d.merchant ?? "",
    d.amount != null ? money(d.amount, { cents: true }) : "",
    d.bought_on ? `bought ${dayWord(d.bought_on)}` : "",
    d.return_by ? `return by ${dayWord(d.return_by)}` : "",
    d.warranty_until ? `warranty until ${dayWord(d.warranty_until)}` : "",
  ].filter(Boolean).join(" · ");
}

/** One page of a document's text, with the page before and after. */
export function Reader({ open, onMove, onClose }: { open: { id: number; page: number }; onMove: (page: number) => void; onClose: () => void }) {
  const state = useApi<DocumentPage>(`/api/v1/documents/${open.id}/pages/${open.page}`);
  const p = state.data && state.data.id === open.id && state.data.page === open.page ? state.data : null;
  return (
    <div role="region" aria-label="The text of a document" style={{ display: "flex", flexDirection: "column", gap: 8, border: "1px solid var(--hairline)", borderRadius: 10, padding: "10px 12px", background: "var(--surface-2)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <BookOpen size={15} aria-hidden="true" style={{ flexShrink: 0 }} />
        <strong style={{ fontSize: 13, minWidth: 0, overflowWrap: "anywhere" }}>{p ? p.title : "Opening the page…"}</strong>
        {p && <span style={{ fontSize: 12, color: "var(--ink-2)" }}>page {p.page} of {p.of}</span>}
        <span style={{ flexGrow: 1 }} />
        <button type="button" className="btn" style={{ ...SMALL, ...ROW }} disabled={!p || p.page <= 1} onClick={() => onMove(open.page - 1)} aria-label="The page before"><ChevronLeft size={14} aria-hidden="true" />Before</button>
        <button type="button" className="btn" style={{ ...SMALL, ...ROW }} disabled={!p || p.page >= p.of} onClick={() => onMove(open.page + 1)} aria-label="The page after">After<ChevronRight size={14} aria-hidden="true" /></button>
        <button type="button" className="btn" style={{ ...SMALL, ...ROW }} onClick={onClose}><X size={14} aria-hidden="true" /> Close</button>
      </div>
      {state.error && !p ? (
        <Failed error={state.error} what="this page" onRetry={state.reload} card={false} />
      ) : !p ? (
        <Loading what="the page" card={false} />
      ) : (
        <>
          <div tabIndex={0} aria-label={`Page ${p.page} of ${p.title}`} style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontSize: 13, lineHeight: 1.55, maxHeight: 360, overflowY: "auto", background: "var(--surface)", borderRadius: 8, padding: "10px 12px", color: "var(--ink)" }}>
            {p.text.trim() ? p.text : "This page has no text the app could read."}
          </div>
          <span style={{ fontSize: 11, color: "var(--muted)" }}>The text the app read from the file. Download the original to see it as printed.</span>
        </>
      )}
    </div>
  );
}

/** Search the text of every document: each place the words appear, with its page and the lines around it. */
export function SearchBox({ onOpen, placeOf }: { onOpen: (id: number, page: number) => void; placeOf?: (attach: string) => string }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<DocumentHit[] | null>(null);
  const [asked, setAsked] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  async function find(e: FormEvent) {
    e.preventDefault();
    const words = q.trim();
    if (!words) return setNote("Type a word or two to look for, such as late fee or pet.");
    setBusy(true);
    setNote(null);
    try {
      const r = await api<{ hits: DocumentHit[] }>(`/api/v1/documents/search?q=${encodeURIComponent(words)}`);
      setHits(r.hits);
      setAsked(words);
    } catch (err) {
      setHits(null);
      setNote(`Couldn't search: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <form onSubmit={find} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 220px", minWidth: 0 }}>
          <span className="lab">Search the text of every document</span>
          <input className="input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="late fee, pet, notice" />
        </label>
        <button className="btn dark" type="submit" disabled={busy} style={ROW}><Search size={14} aria-hidden="true" />{busy ? "Searching…" : "Search"}</button>
      </form>
      <Said text={note} tone="error" />
      {hits && hits.length === 0 && <div style={{ fontSize: 13, color: "var(--ink-2)" }}>No document mentions “{asked}”. Photos and scans are read on the server; one it could not read has no text to search.</div>}
      {hits && hits.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{hits.length} place{hits.length === 1 ? "" : "s"} mention “{asked}”. Tap one to read that page.</span>
          {hits.map((h, i) => (
            <button key={`${h.document_id}-${h.page}-${i}`} type="button" onClick={() => onOpen(h.document_id, h.page)}
              style={{ textAlign: "left", font: "inherit", color: "var(--ink)", background: "var(--surface)", border: "1px solid var(--hairline)", borderRadius: 10, padding: "8px 10px", cursor: "pointer", display: "flex", flexDirection: "column", gap: 3, minHeight: 32 }}>
              <span style={{ fontSize: 13, fontWeight: 700, overflowWrap: "anywhere" }}>{h.title} <span style={{ fontWeight: 500, color: "var(--ink-2)" }}>· page {h.page} · {DOC_KIND_WORD[h.kind] ?? h.kind}{placeOf && h.attach_to ? ` · ${placeOf(h.attach_to)}` : ""}</span></span>
              <span style={{ fontSize: 12, color: "var(--muted)", overflowWrap: "anywhere" }}>…{h.snippet}…</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** One kept file: what it is, its month, a receipt's store, total and dates, and Read, Download and Remove. */
export function DocLine({ d, first = false, place, onRead, onRemove, extra, onChanged }: { d: KeptDocument; first?: boolean; place?: string; onRead: (id: number) => void; onRemove: (d: KeptDocument) => Promise<unknown>; extra?: ReactNode; onChanged?: () => void }) {
  const receipt = d.kind === "receipt" ? receiptWords(d) : "";
  const Icon = d.kind === "receipt" ? Receipt : FileText;
  const month = d.period ? ` · kept with ${monthWord(d.period)}` : d.period === "" ? " · no month" : "";
  return (
    <li style={{ display: "flex", gap: 10, alignItems: "flex-start", flexWrap: "wrap", padding: "8px 0", borderTop: first ? 0 : "1px solid var(--surface-2)" }}>
      <Icon size={18} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2, color: "var(--ink-2)" }} />
      <div style={{ flex: "1 1 200px", minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
        <strong style={{ fontSize: 13, overflowWrap: "anywhere" }}>{d.title}</strong>
        <span style={{ fontSize: 12, color: "var(--ink-2)" }}>
          {DOC_KIND_WORD[d.kind] ?? d.kind}{month} · {pagesWord(d)} · {sizeWord(d.size)} · added {when(d.uploaded_at)}
          {place ? ` · ${place}` : ""}
        </span>
        {receipt && <span className="num" style={{ fontSize: 12, color: "var(--ink)", overflowWrap: "anywhere" }}>{receipt}</span>}
        {d.note && <span style={{ fontSize: 12, color: "var(--muted)", overflowWrap: "anywhere" }}>{d.note}</span>}
        <Suggested d={d} onChanged={onChanged} />
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "flex-start" }}>
        <button type="button" className="btn" style={{ ...SMALL, ...ROW }} disabled={!d.has_text} onClick={() => onRead(d.id)} title={d.has_text ? "Read the text the app took from it" : "No text could be read from it. Download it to see it."}>
          <BookOpen size={14} aria-hidden="true" />Read
        </button>
        <a className="btn" href={`/api/v1/documents/${d.id}/file`} download={d.filename} style={{ ...SMALL, display: "inline-flex", alignItems: "center", gap: 4, textDecoration: "none" }}>
          <Download size={14} aria-hidden="true" />Download
        </a>
        <ConfirmButton label="Remove" style={SMALL} question={`Remove “${d.title}”? The file and its text are deleted from the app.`} yes="Yes, remove it" no="Keep it" onConfirm={() => onRemove(d)} />
      </div>
      {extra && <div style={{ flexBasis: "100%", minWidth: 0, paddingLeft: 28 }}>{extra}</div>}
    </li>
  );
}

/** The purchases a receipt may belong to, one choice each, and "without a purchase" when that is offered. */
export function PurchaseChoices({ name, legend, list, value, onPick, none }: { name: string; legend: string; list: ReceiptCandidate[]; value: number | null; onPick: (id: number | null) => void; none?: string }) {
  const choice = (on: boolean) => ({ display: "flex", alignItems: "center", gap: 8, minHeight: 32, padding: "6px 10px", borderRadius: 10, cursor: "pointer", fontSize: 13, border: `1px solid ${on ? "var(--ink)" : "var(--hairline)"}`, background: on ? "var(--surface-2)" : "var(--surface)" }) as const;
  return (
    <fieldset style={{ border: 0, padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
      <legend className="lab" style={{ padding: 0, marginBottom: 4 }}>{legend}</legend>
      {list.map((c) => (
        <label key={c.id} style={choice(value === c.id)}>
          <input type="radio" name={name} checked={value === c.id} onChange={() => onPick(c.id)} style={{ width: 18, height: 18, margin: 0, flexShrink: 0 }} />
          <span style={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0 }}>
            <span className="num" style={{ fontWeight: 700, overflowWrap: "anywhere" }}>{c.merchant || "(no name)"} · {c.account} · {shortDate(c.date)} · {money(c.amount, { cents: true })}</span>
            {(c.named || c.has_receipt) && (
              <span style={{ fontSize: 12, color: "var(--ink-2)", display: "inline-flex", alignItems: "center", gap: 4, flexWrap: "wrap" }}>
                {c.named && <span>The store's name matches.</span>}
                {c.has_receipt && <span style={{ display: "inline-flex", alignItems: "center", gap: 3 }}><Receipt size={12} aria-hidden="true" />It already has a receipt kept with it.</span>}
              </span>
            )}
          </span>
        </label>
      ))}
      {none && (
        <label style={choice(value === null)}>
          <input type="radio" name={name} checked={value === null} onChange={() => onPick(null)} style={{ width: 18, height: 18, margin: 0, flexShrink: 0 }} />
          <span style={{ fontWeight: 700 }}>{none}</span>
        </label>
      )}
    </fieldset>
  );
}

export function Documents({
  attachTo,
  defaultKind = "other",
  search = false,
  places,
  placeOf,
  prefill,
  startClosed = false,
  hideList = false,
  readFirst = false,
  refreshKey = 0,
  onChanged,
}: {
  /** What the documents belong to, like "home:3". Without it: every document. */
  attachTo?: string;
  defaultKind?: string;
  /** Show the search over every document's text. */
  search?: boolean;
  /** What a new document can belong to, when it is added from the list of every document. */
  places?: { value: string; label: string }[];
  /** What a document belongs to, in words. */
  placeOf?: (attach: string) => string;
  /** A receipt's store, total and day bought, filled in from what it belongs to (a purchase). */
  prefill?: { merchant?: string; amount?: string; bought_on?: string };
  /** Keep the form behind its button even when nothing is kept yet (a purchase's receipts, in a row's editor). */
  startClosed?: boolean;
  /** Only the button and the form to add one (the page lists the files its own way). */
  hideList?: boolean;
  /** Open the first file with text in the reader once the list has loaded (a row's Receipt mark). */
  readFirst?: boolean;
  /** Changes when a document was added or removed somewhere else on the page. */
  refreshKey?: number;
  onChanged?: () => void;
}) {
  const list = useApi<KeptDocumentsOut>(attachTo ? `/api/v1/documents?attach_to=${encodeURIComponent(attachTo)}` : "/api/v1/documents");
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState(defaultKind);
  const [place, setPlace] = useState("");
  const [memo, setMemo] = useState("");
  const [period, setPeriod] = useState("");
  const [merchant, setMerchant] = useState(prefill?.merchant ?? "");
  const [total, setTotal] = useState(prefill?.amount ?? "");
  const [bought, setBought] = useState(prefill?.bought_on ?? "");
  const [returnBy, setReturnBy] = useState("");
  const [warranty, setWarranty] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  const [open, setOpen] = useState<{ id: number; page: number } | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const first = useRef(true);
  const autoRead = useRef(readFirst);
  useEffect(() => {
    if (!autoRead.current || !list.data) return;
    autoRead.current = false;
    const d = list.data.documents.find((x) => x.has_text);
    if (d) setOpen({ id: d.id, page: 1 });
  }, [list.data]);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    list.reload();
  }, [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const kinds = list.data?.kinds?.length ? list.data.kinds : Object.keys(DOC_KIND_WORD);
  const docs = list.data?.documents ?? [];
  const showForm = adding || (!startClosed && !hideList && list.data !== null && docs.length === 0);
  const isReceipt = kind === "receipt";

  async function upload(e: FormEvent) {
    e.preventDefault();
    const chosen = file.current?.files?.[0];
    if (!chosen) return setNote({ text: "Choose a file first.", tone: "error" });
    if (chosen.size > MAX_BYTES) return setNote({ text: "That file is over 25 MB. A document is at most 25 MB.", tone: "error" });
    const month = period.trim();
    if (month && !MONTH.test(month)) return setNote({ text: "Type the month like 2026-10, or leave it blank.", tone: "error" });
    const typedTotal = isReceipt && total.trim() ? parseMoney(total) : null;
    if (isReceipt && total.trim() && typedTotal === null) return setNote({ text: "Type an amount like 120 or 1200.50", tone: "error" });
    const store = isReceipt ? merchant.trim() : "";
    const form = new FormData();
    form.append("file", chosen);
    form.append("title", title.trim() || (store ? `Receipt · ${store}` : ""));
    form.append("kind", kind);
    form.append("attach_to", attachTo ?? place);
    form.append("note", memo.trim());
    form.append("period", month);
    if (isReceipt) {
      form.append("merchant", store);
      form.append("amount", typedTotal !== null ? String(typedTotal) : "");
      form.append("bought_on", bought);
      form.append("return_by", returnBy);
      form.append("warranty_until", warranty);
    }
    setBusy(true);
    setNote(null);
    try {
      const doc = await api<KeptDocument>("/api/v1/documents", { method: "POST", body: form });
      const offers = TERMS_KINDS.has(doc.kind) && doc.attach_to.startsWith("home:") && doc.has_text;
      // A lease kept with nothing is kept, but its terms are read only once it is kept with a home.
      const homeless = TERMS_KINDS.has(doc.kind) && !doc.attach_to.startsWith("home:");
      const kept = doc.period ? ` Kept with ${monthWord(doc.period)}.` : "";
      setNote({ tone: "info", terms: offers, homeless, text: doc.note_text ? `Added “${doc.title}”. ${doc.note_text}${kept}` : `Added “${doc.title}”: ${pagesWord(doc)}.${kept}` });
      setTitle("");
      setMemo("");
      setPeriod("");
      setMerchant(prefill?.merchant ?? "");
      setTotal(prefill?.amount ?? "");
      setBought(prefill?.bought_on ?? "");
      setReturnBy("");
      setWarranty("");
      setKind(defaultKind);
      if (file.current) file.current.value = "";
      setAdding(false);
      list.reload();
      onChanged?.();
    } catch (err) {
      setNote({ tone: "error", text: `Couldn't add it: ${(err as Error).message}` });
    } finally {
      setBusy(false);
    }
  }

  async function remove(d: KeptDocument) {
    await api(`/api/v1/documents/${d.id}`, { method: "DELETE" });
    if (open?.id === d.id) setOpen(null);
    setNote({ tone: "info", text: `Removed “${d.title}”.` });
    list.reload();
    onChanged?.();
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {search && <SearchBox onOpen={(id, page) => setOpen({ id, page })} placeOf={placeOf} />}
      {open && <Reader open={open} onMove={(page) => setOpen({ id: open.id, page })} onClose={() => setOpen(null)} />}

      {hideList ? null : list.error && !list.data ? (
        <Failed error={list.error} what="the documents" onRetry={list.reload} card={false} />
      ) : !list.data ? (
        <Loading what="the documents" card={false} />
      ) : docs.length === 0 ? (
        <Empty card={false}>{defaultKind === "receipt" ? "No receipt kept here yet. Add one below: a photo or a PDF of it." : "No documents yet. Add one below: a PDF, a photo, a spreadsheet or a text file."}</Empty>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column" }}>
          {docs.map((d, i) => (
            <DocLine key={d.id} d={d} first={i === 0} place={!attachTo && placeOf ? placeOf(d.attach_to) : undefined} onRead={(id) => setOpen({ id, page: 1 })} onRemove={remove} onChanged={list.reload} />
          ))}
        </ul>
      )}

      {showForm ? (
        <form onSubmit={upload} style={{ display: "flex", flexDirection: "column", gap: 8, borderTop: "1px solid var(--surface-2)", paddingTop: 10 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
            <label style={{ ...FIELD, flex: "1 1 220px", minWidth: 0 }}>
              <span className="lab">File (PDF, photo, spreadsheet or text; 25 MB at most)</span>
              <input ref={file} className="input" type="file" accept={ACCEPT} style={{ maxWidth: "100%" }} />
            </label>
            <label style={FIELD}>
              <span className="lab">What it is</span>
              <select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
                {kinds.map((k) => <option key={k} value={k}>{DOC_KIND_WORD[k] ?? k}</option>)}
              </select>
            </label>
          </div>
          {isReceipt && (
            <fieldset style={{ border: 0, padding: 0, margin: 0, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end", minWidth: 0 }}>
              <legend className="lab" style={{ padding: 0, marginBottom: 4 }}>What the receipt says (each is optional)</legend>
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
            </fieldset>
          )}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
            <label style={{ ...FIELD, flex: "1 1 200px", minWidth: 0 }}>
              <span className="lab">Title (blank: {isReceipt ? "“Receipt” and the store" : "the file's name"})</span>
              <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} />
            </label>
            <label style={{ ...FIELD, flex: "0 1 180px", minWidth: 0 }}>
              <span className="lab">Month it belongs to</span>
              <input className="input" type="month" value={period} onChange={(e) => setPeriod(e.target.value)} placeholder="2026-10" />
            </label>
            {!attachTo && places && (
              <label style={{ ...FIELD, flex: "1 1 200px", minWidth: 0 }}>
                <span className="lab">It belongs to</span>
                <select className="input" value={place} onChange={(e) => setPlace(e.target.value)}>
                  <option value="">Nothing in particular</option>
                  {places.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </label>
            )}
            <label style={{ ...FIELD, flex: "1 1 200px", minWidth: 0 }}>
              <span className="lab">Note (optional)</span>
              <input className="input" value={memo} onChange={(e) => setMemo(e.target.value)} maxLength={300} />
            </label>
          </div>
          <span style={{ fontSize: 12, color: "var(--ink-2)" }}>
            The month is optional; a statement or a paystub is easiest to find with its month.{isReceipt ? " Left blank, a receipt is kept with the month of the day bought. A last day to return and a warranty's end come back as reminders." : ""}
          </span>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <button className="btn dark" type="submit" disabled={busy} style={ROW}><Upload size={14} aria-hidden="true" />{busy ? "Adding…" : isReceipt ? "Add the receipt" : "Add the document"}</button>
            {adding && (docs.length > 0 || startClosed || hideList) && <button className="btn" type="button" onClick={() => setAdding(false)}>Cancel</button>}
          </div>
        </form>
      ) : (
        <button className="btn" type="button" aria-expanded={false} onClick={() => setAdding(true)} style={{ alignSelf: "flex-start", ...SMALL, ...ROW }}>
          <Upload size={14} aria-hidden="true" />{defaultKind === "receipt" ? "Add a receipt" : "Add a document"}
        </button>
      )}
      {note && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "baseline" }}>
          <Said text={note.text} tone={note.tone} />
          {note.terms && <span style={{ fontSize: 12, fontWeight: 700 }}>The terms it states wait for you on <Link to="/confirm">Needs you</Link>; nothing changes until you confirm them there.</span>}
          {note.homeless && <span style={{ fontSize: 12, fontWeight: 700 }}>Its terms (rent, deposit, notice, due day) are read only for a document kept with a home. To have them read, remove it and add it again with its home picked under “It belongs to”, here or on <Link to="/housing">Housing</Link>.</span>}
        </div>
      )}
    </div>
  );
}
