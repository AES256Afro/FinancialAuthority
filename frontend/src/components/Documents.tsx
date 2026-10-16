// Documents kept with what they belong to: a lease with its home, a utility's terms with the utility. Add one,
// see the list, read a page of the text the app took from it, search every document, download the original, or
// remove it. The file stays encrypted on the server; what shows here is the text read from it, page by page.
import { BookOpen, ChevronLeft, ChevronRight, Download, FileText, Search, Upload, X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { when } from "../format";
import type { DocumentHit, DocumentPage, DocumentRow, DocumentsOut } from "../types";
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
const SMALL = { minHeight: 32, padding: "4px 10px" } as const;
const ROW = { display: "inline-flex", alignItems: "center", gap: 4 } as const;

type Note = { text: string; tone: "info" | "error"; terms?: boolean } | null;

/** A file's size in words: "340 KB", "2.1 MB". */
function sizeWord(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function pagesWord(d: DocumentRow): string {
  if (!d.has_text) return "no text could be read";
  return `${d.pages} page${d.pages === 1 ? "" : "s"} of text`;
}

/** One page of a document's text, with the page before and after. */
function Reader({ open, onMove, onClose }: { open: { id: number; page: number }; onMove: (page: number) => void; onClose: () => void }) {
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
function SearchBox({ onOpen, placeOf }: { onOpen: (id: number, page: number) => void; placeOf?: (attach: string) => string }) {
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
      {hits && hits.length === 0 && <div style={{ fontSize: 13, color: "var(--ink-2)" }}>No document mentions “{asked}”. A photo or a scan has no text to search.</div>}
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

export function Documents({
  attachTo,
  defaultKind = "other",
  search = false,
  places,
  placeOf,
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
  /** Changes when a document was added or removed somewhere else on the page. */
  refreshKey?: number;
  onChanged?: () => void;
}) {
  const list = useApi<DocumentsOut>(attachTo ? `/api/v1/documents?attach_to=${encodeURIComponent(attachTo)}` : "/api/v1/documents");
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState(defaultKind);
  const [place, setPlace] = useState("");
  const [memo, setMemo] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  const [open, setOpen] = useState<{ id: number; page: number } | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    list.reload();
  }, [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const kinds = list.data?.kinds?.length ? list.data.kinds : Object.keys(DOC_KIND_WORD);
  const docs = list.data?.documents ?? [];
  const showForm = adding || (list.data !== null && docs.length === 0);

  async function upload(e: FormEvent) {
    e.preventDefault();
    const chosen = file.current?.files?.[0];
    if (!chosen) return setNote({ text: "Choose a file first.", tone: "error" });
    if (chosen.size > MAX_BYTES) return setNote({ text: "That file is over 25 MB. A document is at most 25 MB.", tone: "error" });
    const form = new FormData();
    form.append("file", chosen);
    form.append("title", title.trim());
    form.append("kind", kind);
    form.append("attach_to", attachTo ?? place);
    form.append("note", memo.trim());
    setBusy(true);
    setNote(null);
    try {
      const doc = await api<DocumentRow>("/api/v1/documents", { method: "POST", body: form });
      const offers = TERMS_KINDS.has(doc.kind) && doc.attach_to.startsWith("home:") && doc.has_text;
      setNote({ tone: "info", terms: offers, text: doc.note_text ? `Added “${doc.title}”. ${doc.note_text}` : `Added “${doc.title}”: ${pagesWord(doc)}.` });
      setTitle("");
      setMemo("");
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

  async function remove(d: DocumentRow) {
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

      {list.error && !list.data ? (
        <Failed error={list.error} what="the documents" onRetry={list.reload} card={false} />
      ) : !list.data ? (
        <Loading what="the documents" card={false} />
      ) : docs.length === 0 ? (
        <Empty card={false}>No documents yet. Add one below: a PDF, a photo, a spreadsheet or a text file.</Empty>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column" }}>
          {docs.map((d, i) => (
            <li key={d.id} style={{ display: "flex", gap: 10, alignItems: "flex-start", flexWrap: "wrap", padding: "8px 0", borderTop: i === 0 ? 0 : "1px solid var(--surface-2)" }}>
              <FileText size={18} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2, color: "var(--ink-2)" }} />
              <div style={{ flex: "1 1 200px", minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
                <strong style={{ fontSize: 13, overflowWrap: "anywhere" }}>{d.title}</strong>
                <span style={{ fontSize: 12, color: "var(--ink-2)" }}>
                  {DOC_KIND_WORD[d.kind] ?? d.kind} · {pagesWord(d)} · {sizeWord(d.size)} · added {when(d.uploaded_at)}
                  {!attachTo && placeOf ? ` · ${placeOf(d.attach_to)}` : ""}
                </span>
                {d.note && <span style={{ fontSize: 12, color: "var(--muted)", overflowWrap: "anywhere" }}>{d.note}</span>}
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "flex-start" }}>
                <button type="button" className="btn" style={{ ...SMALL, ...ROW }} disabled={!d.has_text} onClick={() => setOpen({ id: d.id, page: 1 })} title={d.has_text ? "Read the text the app took from it" : "No text could be read from it (a photo or a scan). Download it to see it."}>
                  <BookOpen size={14} aria-hidden="true" />Read
                </button>
                <a className="btn" href={`/api/v1/documents/${d.id}/file`} download={d.filename} style={{ ...SMALL, display: "inline-flex", alignItems: "center", gap: 4, textDecoration: "none" }}>
                  <Download size={14} aria-hidden="true" />Download
                </a>
                <ConfirmButton label="Remove" style={SMALL} question={`Remove “${d.title}”? The file and its text are deleted from the app.`} yes="Yes, remove it" no="Keep it" onConfirm={() => remove(d)} />
              </div>
            </li>
          ))}
        </ul>
      )}

      {showForm ? (
        <form onSubmit={upload} style={{ display: "flex", flexDirection: "column", gap: 8, borderTop: "1px solid var(--surface-2)", paddingTop: 10 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
            <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 220px", minWidth: 0 }}>
              <span className="lab">File (PDF, photo, spreadsheet or text; 25 MB at most)</span>
              <input ref={file} className="input" type="file" accept={ACCEPT} style={{ maxWidth: "100%" }} />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span className="lab">What it is</span>
              <select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
                {kinds.map((k) => <option key={k} value={k}>{DOC_KIND_WORD[k] ?? k}</option>)}
              </select>
            </label>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
            <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 200px", minWidth: 0 }}>
              <span className="lab">Title (blank: the file's name)</span>
              <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} />
            </label>
            {!attachTo && places && (
              <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 200px", minWidth: 0 }}>
                <span className="lab">It belongs to</span>
                <select className="input" value={place} onChange={(e) => setPlace(e.target.value)}>
                  <option value="">Nothing in particular</option>
                  {places.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </label>
            )}
            <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 200px", minWidth: 0 }}>
              <span className="lab">Note (optional)</span>
              <input className="input" value={memo} onChange={(e) => setMemo(e.target.value)} maxLength={300} />
            </label>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <button className="btn dark" type="submit" disabled={busy} style={ROW}><Upload size={14} aria-hidden="true" />{busy ? "Adding…" : "Add the document"}</button>
            {adding && docs.length > 0 && <button className="btn" type="button" onClick={() => setAdding(false)}>Cancel</button>}
          </div>
        </form>
      ) : (
        <button className="btn" type="button" aria-expanded={false} onClick={() => setAdding(true)} style={{ alignSelf: "flex-start", ...SMALL, ...ROW }}>
          <Upload size={14} aria-hidden="true" />Add a document
        </button>
      )}
      {note && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "baseline" }}>
          <Said text={note.text} tone={note.tone} />
          {note.terms && <span style={{ fontSize: 12, fontWeight: 700 }}>The terms it states wait for you on <Link to="/confirm">Needs you</Link>; nothing changes until you confirm them there.</span>}
        </div>
      )}
    </div>
  );
}
