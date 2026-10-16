// Transactions: every row the app reads, with your own hand on it: a category, a note, tags, hidden or not, one
// purchase split across categories, and many rows changed at once. The server checks and keeps every change; the
// only sum here is the split form's "left to place" hint, which checks what was typed against the row's own amount.
import { ArrowDownLeft, ArrowLeftRight, Clock, CreditCard, Eye, EyeOff, Pencil, Plus, Split, Tag, X } from "lucide-react";
import { Fragment, memo, useCallback, useEffect, useId, useMemo, useRef, useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { ConfirmButton } from "../components/ConfirmButton";
import { LedgerRules } from "../components/LedgerRules";
import { Empty, Failed, Loading, Said } from "../components/State";
import { money, parseMoney, shortDate } from "../format";
import type { TransactionRow } from "../types";

type Kind = "spending" | "transfer" | "card payment" | "income";
interface Part {
  category: string;
  amount: number;
  note: string;
}
interface Row extends TransactionRow {
  pending?: boolean;
  hidden?: boolean;
  kind?: Kind;
  tags?: string[];
  splits?: Part[];
}
interface Search {
  returned: number;
  total_amount: number;
  transactions: Row[];
}
type Tone = "info" | "error";
type Note = { id: number | null; text: string; tone: Tone } | null;

const NO_ROWS: Row[] = [];
const SMALL: CSSProperties = { minHeight: 32, padding: "4px 10px", fontSize: 12 };
const FIELD: CSSProperties = { display: "flex", flexDirection: "column", gap: 4 };
const OPEN: CSSProperties = { display: "inline-flex", alignItems: "center", gap: 6, minHeight: 32, padding: "4px 0", background: "none", border: 0, font: "inherit", fontWeight: 700, color: "var(--ink)", cursor: "pointer", textAlign: "left" };

const KIND_MARK: Record<Exclude<Kind, "spending">, { word: string; Icon: typeof Tag }> = {
  transfer: { word: "Transfer", Icon: ArrowLeftRight },
  "card payment": { word: "Card payment", Icon: CreditCard },
  income: { word: "Income", Icon: ArrowDownLeft },
};

const MARK_TONE = {
  plain: { background: "var(--surface-2)", color: "var(--ink-2)" },
  pending: { background: "var(--paycheck-soft)", color: "var(--paycheck-ink)" },
  split: { background: "var(--debt-soft)", color: "var(--debt-ink)" },
} as const;

/** Tag words as typed, comma-separated: trimmed, lower case, no repeats. The server cleans them again. */
function tagWords(text: string): string[] {
  const out: string[] = [];
  for (const raw of text.split(",")) {
    const word = raw.trim().toLowerCase();
    if (word && !out.includes(word)) out.push(word);
  }
  return out;
}

// A row's state as an icon and a word, never colour alone.
function Mark({ Icon, word, tone = "plain", title }: { Icon: typeof Tag; word: string; tone?: keyof typeof MARK_TONE; title?: string }) {
  return (
    <span title={title} style={{ ...MARK_TONE[tone], display: "inline-flex", alignItems: "center", gap: 4, padding: "1px 7px", borderRadius: 999, fontSize: 11, fontWeight: 800, whiteSpace: "nowrap" }}>
      <Icon size={11} strokeWidth={2.4} aria-hidden="true" />
      {word}
    </span>
  );
}

function Chip({ word }: { word: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 3, padding: "0 7px", borderRadius: 999, border: "1px solid var(--hairline)", fontSize: 11, fontWeight: 700, color: "var(--ink-2)", whiteSpace: "nowrap" }}>
      <Tag size={10} aria-hidden="true" />
      {word}
    </span>
  );
}

// One row. Kept apart and memoised so ticking one box among 500 redraws one row, not all of them.
const TxRow = memo(function TxRow({ t, selected, open, onSelect, onOpen }: { t: Row; selected: boolean; open: boolean; onSelect: (id: number) => void; onOpen: (id: number) => void }) {
  const kind = t.kind && t.kind !== "spending" ? KIND_MARK[t.kind] : null;
  const parts = t.splits ?? [];
  const tags = t.tags ?? [];
  const marked = Boolean(t.pending || t.hidden || kind || parts.length > 0 || tags.length > 0);
  return (
    <tr style={{ background: selected ? "var(--surface-2)" : undefined }}>
      <td style={{ width: 34, paddingRight: 4 }}>
        <label style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", minWidth: 32, minHeight: 32, cursor: "pointer" }}>
          <input type="checkbox" checked={selected} onChange={() => onSelect(t.id)} style={{ width: 18, height: 18, margin: 0, cursor: "pointer" }} />
          <span className="sr-only">Select {t.merchant || "this row"}, {shortDate(t.date)}, {money(t.amount, { cents: true })}</span>
        </label>
      </td>
      <td style={{ whiteSpace: "nowrap" }}>{shortDate(t.date)}</td>
      <td style={{ minWidth: 160 }}>
        <button id={`tx-open-${t.id}`} type="button" aria-expanded={open} aria-controls={open ? `tx-edit-${t.id}` : undefined} onClick={() => onOpen(t.id)} style={OPEN}>
          <span>{t.merchant || "(no name)"}</span>
          <Pencil size={12} aria-hidden="true" style={{ color: "var(--muted)", flexShrink: 0 }} />
          <span className="sr-only">{open ? "(close the editor)" : "(open to change it)"}</span>
        </button>
        {marked && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 2 }}>
            {t.pending && <Mark Icon={Clock} word="Pending" tone="pending" title="The bank has not finished it yet. It is already taken off the bank's balance." />}
            {t.hidden && <Mark Icon={EyeOff} word="Hidden" title="Left out of spending, budgets and reports." />}
            {kind && <Mark Icon={kind.Icon} word={kind.word} />}
            {parts.length > 0 && <Mark Icon={Split} word="Split" tone="split" />}
            {tags.map((w) => <Chip key={w} word={w} />)}
          </div>
        )}
        {t.note && <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Note: {t.note}</div>}
      </td>
      <td style={{ color: "var(--ink-2)" }}>
        {parts.length > 0
          ? parts.map((p, i) => <div key={i} style={{ whiteSpace: "nowrap" }}>{p.category} <span className="num">{money(p.amount, { cents: true })}</span></div>)
          : t.category === "Borrowing" ? <Link to="/borrowing" style={{ fontWeight: 700 }}>Borrowing</Link> : t.category}
      </td>
      <td style={{ color: "var(--ink-2)" }}>{t.account}</td>
      <td className="num" style={{ textAlign: "right", whiteSpace: "nowrap" }}>{money(t.amount, { cents: true })}</td>
    </tr>
  );
});

// Category, note, tags and hidden for one row.
function ChangeRow({ row, onSaved }: { row: Row; onSaved: (id: number, text: string) => void }) {
  const [category, setCategory] = useState(row.category);
  const [note, setNote] = useState(row.note ?? "");
  const [tagsText, setTagsText] = useState((row.tags ?? []).join(", "));
  const [hidden, setHidden] = useState(Boolean(row.hidden));
  const [said, setSaid] = useState<{ text: string; tone: Tone } | null>(null);
  const [busy, setBusy] = useState(false);
  const id = useId();
  const words = tagWords(tagsText);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!category.trim()) {
      setSaid({ tone: "error", text: "Type a category, or pick one from the list." });
      return;
    }
    setBusy(true);
    setSaid(null);
    try {
      await api(`/api/v1/transactions/${row.id}`, { method: "PATCH", body: JSON.stringify({ category: category.trim(), note: note.trim(), tags: words, hidden }) });
      const text = hidden && !row.hidden ? `Saved ${row.merchant}. It is hidden now: out of spending, budgets and reports ("Everything" still lists it).` : `Saved ${row.merchant}.`;
      setSaid({ tone: "info", text });
      onSaved(row.id, text);
    } catch (err) {
      setSaid({ tone: "error", text: `Couldn't save it: ${(err as Error).message}` });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} className="card" style={{ padding: 12, display: "flex", flexDirection: "column", gap: 10 }}>
      <h3 style={{ margin: 0, fontSize: 13, fontWeight: 800 }}>Change this row</h3>
      <label style={FIELD}>
        <span className="lab">Category</span>
        <input className="input" list="tx-categories" value={category} onChange={(e) => setCategory(e.target.value)} autoFocus />
      </label>
      <label style={FIELD}>
        <span className="lab">Note</span>
        <input className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="What it was for" maxLength={500} />
      </label>
      <label style={FIELD}>
        <span className="lab">Tags, with a comma between each</span>
        <input className="input" list="tx-tags" value={tagsText} onChange={(e) => setTagsText(e.target.value)} placeholder="trip, gift" />
      </label>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, minHeight: 18, fontSize: 12, color: "var(--muted)" }} aria-live="polite">
        {words.length === 0 ? "No tags." : words.map((w) => <Chip key={w} word={w} />)}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span id={`${id}-hide`} className="lab">Hide this row</span>
        <button type="button" role="switch" aria-checked={hidden} aria-labelledby={`${id}-hide`} className={hidden ? "btn dark" : "btn"} onClick={() => setHidden(!hidden)} style={{ ...SMALL, display: "inline-flex", alignItems: "center", gap: 6 }}>
          {hidden ? <EyeOff size={14} aria-hidden="true" /> : <Eye size={14} aria-hidden="true" />}
          {hidden ? "Hidden" : "Shown"}
        </button>
        <span style={{ fontSize: 12, color: "var(--ink-2)", flex: "1 1 180px" }}>A hidden row stays in the app, out of spending, budgets and reports.</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <button className="btn dark" type="submit" disabled={busy} style={{ minHeight: 34, padding: "4px 12px" }}>{busy ? "Saving…" : "Save"}</button>
        <Said text={said?.text ?? null} tone={said?.tone} />
      </div>
    </form>
  );
}

let partKey = 0;
const blankPart = (category = "", amount = "", note = "") => ({ key: partKey++, category, amount, note });

// One purchase across two or more categories. The parts must add up to the row to the cent; the server decides.
function SplitRow({ row, onSaved }: { row: Row; onSaved: (id: number, text: string) => void }) {
  const had = row.splits ?? [];
  const [parts, setParts] = useState(() => (had.length >= 2 ? had.map((p) => blankPart(p.category, Math.abs(p.amount).toFixed(2), p.note)) : [blankPart(row.category), blankPart()]));
  const [said, setSaid] = useState<{ text: string; tone: Tone } | null>(null);
  const [busy, setBusy] = useState(false);
  const moneyIn = row.amount < 0;

  // The form check: what was typed against the row's own amount, in whole cents. A hint, not a figure.
  const typed = parts.map((p) => (p.amount.trim() === "" ? 0 : parseMoney(p.amount)));
  const unreadable = typed.some((v) => v === null);
  const left = Math.round(Math.abs(row.amount) * 100) - typed.reduce<number>((sum, v) => sum + Math.round(Math.abs(v ?? 0) * 100), 0);
  const hint = unreadable
    ? { text: "Type each amount like 120 or 1200.50.", ink: "var(--critical-ink)" }
    : left > 0
      ? { text: `${money(left / 100, { cents: true })} left to place.`, ink: "var(--ink-2)" }
      : left < 0
        ? { text: `${money(-left / 100, { cents: true })} more than the ${moneyIn ? "amount" : "purchase"}.`, ink: "var(--critical-ink)" }
        : { text: "All of it is placed.", ink: "var(--safe-ink)" };

  const change = (key: number, field: "category" | "amount" | "note", value: string) => setParts((all) => all.map((p) => (p.key === key ? { ...p, [field]: value } : p)));

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const sign = moneyIn ? -1 : 1;
    const out: Part[] = [];
    for (const p of parts) {
      const v = parseMoney(p.amount);
      if (!p.category.trim()) {
        setSaid({ tone: "error", text: "Each part needs a category." });
        return;
      }
      if (v === null || v === 0) {
        setSaid({ tone: "error", text: "Type each part's amount like 120 or 1200.50." });
        return;
      }
      out.push({ category: p.category.trim(), amount: sign * Math.abs(v), note: p.note.trim() });
    }
    setBusy(true);
    setSaid(null);
    try {
      await api(`/api/v1/transactions/${row.id}/split`, { method: "PUT", body: JSON.stringify({ parts: out }) });
      const text = `Split ${row.merchant} into ${out.length} parts.`;
      setSaid({ tone: "info", text });
      onSaved(row.id, text);
    } catch (err) {
      setSaid({ tone: "error", text: `Couldn't split it: ${(err as Error).message}` });
    } finally {
      setBusy(false);
    }
  }
  async function unsplit() {
    await api(`/api/v1/transactions/${row.id}/split`, { method: "PUT", body: JSON.stringify({ parts: [] }) });
    const text = `${row.merchant} is one row again, in ${row.category}.`;
    setSaid({ tone: "info", text });
    onSaved(row.id, text);
  }

  return (
    <form onSubmit={save} className="card" style={{ padding: 12, display: "flex", flexDirection: "column", gap: 10 }}>
      <h3 style={{ margin: 0, fontSize: 13, fontWeight: 800 }}>Split it across categories</h3>
      <div style={{ fontSize: 12, color: "var(--ink-2)" }}>
        The parts add up to {money(row.amount, { cents: true })}, to the cent. Type each part as a plain amount{moneyIn ? "; this is money in, so every part is money in too." : "."}
      </div>
      {parts.map((p, i) => (
        <fieldset key={p.key} style={{ border: 0, padding: 0, margin: 0, display: "flex", flexWrap: "wrap", gap: 6, alignItems: "flex-end" }}>
          <legend className="sr-only">Part {i + 1}</legend>
          <label style={{ ...FIELD, flex: "1 1 130px" }}>
            <span className="lab">Category</span>
            <input className="input" list="tx-categories" value={p.category} onChange={(e) => change(p.key, "category", e.target.value)} style={{ minHeight: 34, padding: "4px 10px" }} />
          </label>
          <label style={{ ...FIELD, flex: "0 1 110px" }}>
            <span className="lab">Amount</span>
            <input className="input num" inputMode="decimal" value={p.amount} onChange={(e) => change(p.key, "amount", e.target.value)} placeholder="40.00" style={{ minHeight: 34, padding: "4px 10px", width: "100%" }} />
          </label>
          <label style={{ ...FIELD, flex: "1 1 130px" }}>
            <span className="lab">Note</span>
            <input className="input" value={p.note} onChange={(e) => change(p.key, "note", e.target.value)} maxLength={200} style={{ minHeight: 34, padding: "4px 10px" }} />
          </label>
          <button type="button" className="btn" disabled={parts.length <= 2} title={parts.length <= 2 ? "A split has two parts or more" : undefined} aria-label={`Remove part ${i + 1}`} onClick={() => setParts((all) => all.filter((x) => x.key !== p.key))} style={{ ...SMALL, display: "inline-flex", alignItems: "center", gap: 4 }}>
            <X size={13} aria-hidden="true" /> Remove
          </button>
        </fieldset>
      ))}
      <div aria-live="polite" style={{ fontSize: 13, fontWeight: 700, color: hint.ink }}>{hint.text}</div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <button type="button" className="btn" onClick={() => setParts((all) => [...all, blankPart()])} style={{ ...SMALL, display: "inline-flex", alignItems: "center", gap: 4 }}>
          <Plus size={13} aria-hidden="true" /> Add a part
        </button>
        <button type="submit" className="btn dark" disabled={busy} style={{ minHeight: 34, padding: "4px 12px" }}>{busy ? "Saving…" : "Save the split"}</button>
        {had.length > 0 && <ConfirmButton label="Remove the split" question={`Put all of ${row.merchant} back in ${row.category}?`} yes="Yes, remove the split" no="Keep the split" onConfirm={unsplit} style={SMALL} />}
        <Said text={said?.text ?? null} tone={said?.tone} />
      </div>
    </form>
  );
}

function Editor({ row, onSaved, onClose }: { row: Row; onSaved: (id: number, text: string) => void; onClose: () => void }) {
  return (
    <tr id={`tx-edit-${row.id}`}>
      <td colSpan={6} style={{ background: "var(--surface-2)", padding: 10 }}>
        <div role="group" aria-label={`Change ${row.merchant || "this row"}`} style={{ position: "sticky", left: 0, width: "min(100%, calc(100vw - 48px))", display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", fontSize: 13 }}>
            <strong>{row.merchant || "(no name)"}</strong>
            <span style={{ color: "var(--ink-2)" }}>{shortDate(row.date)} · {row.account} · <span className="num">{money(row.amount, { cents: true })}</span></span>
            <div style={{ flexGrow: 1 }} />
            <button type="button" className="btn" onClick={onClose} style={{ ...SMALL, display: "inline-flex", alignItems: "center", gap: 4 }}><X size={13} aria-hidden="true" /> Close</button>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(300px, 100%), 1fr))", gap: 10, alignItems: "start" }}>
            <ChangeRow row={row} onSaved={onSaved} />
            <SplitRow row={row} onSaved={onSaved} />
          </div>
        </div>
      </td>
    </tr>
  );
}

interface BulkChange {
  category?: string;
  tags_add?: string[];
  tags_remove?: string[];
  hidden?: boolean;
}

export function Transactions() {
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [tagText, setTagText] = useState("");
  const [tag, setTag] = useState("");
  const [everything, setEverything] = useState(false);
  const [limit, setLimit] = useState(100);
  const params = new URLSearchParams({ limit: String(limit) });
  if (query) params.set("text", query);
  if (tag) params.set("tag", tag);
  if (everything) params.set("include_non_spending", "true");
  const path = `/api/v1/transactions/search?${params}`;
  const { data, error, reload } = useApi<Search>(path);
  const rows = data?.transactions ?? NO_ROWS;

  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [openId, setOpenId] = useState<number | null>(null);
  const [said, setSaid] = useState<Note>(null);
  const [bulkCategory, setBulkCategory] = useState("");
  const [bulkAdd, setBulkAdd] = useState("");
  const [bulkRemove, setBulkRemove] = useState("");
  const [busy, setBusy] = useState(false);
  const allBox = useRef<HTMLInputElement>(null);

  // A new search starts with nothing selected, nothing open and nothing said.
  useEffect(() => {
    setSelected(new Set());
    setOpenId(null);
    setSaid(null);
  }, [path]);

  const shownIds = useMemo(() => new Set(rows.map((r) => r.id)), [rows]);
  const categories = useMemo(() => [...new Set(rows.flatMap((r) => [r.category, ...(r.splits ?? []).map((p) => p.category)]).filter(Boolean))].sort(), [rows]);
  const tagsSeen = useMemo(() => [...new Set(rows.flatMap((r) => r.tags ?? []))].sort(), [rows]);
  const allOn = rows.length > 0 && rows.every((r) => selected.has(r.id));
  const someOn = selected.size > 0 && !allOn;
  useEffect(() => {
    if (allBox.current) allBox.current.indeterminate = someOn;
  }, [someOn, data]);

  const toggle = useCallback((id: number) => setSelected((s) => {
    const next = new Set(s);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  }), []);
  const openRow = useCallback((id: number) => setOpenId((o) => (o === id ? null : id)), []);
  function closeEditor() {
    const id = openId;
    setOpenId(null);
    if (id !== null) document.getElementById(`tx-open-${id}`)?.focus();
  }
  function saved(id: number, message: string) {
    setSaid({ id, text: message, tone: "info" });
    reload();
  }
  function pickTag(word: string) {
    setTag(word);
    setTagText(word);
  }

  async function applyBulk(change: BulkChange, what: string) {
    const ids = [...selected];
    setBusy(true);
    try {
      const r = await api<{ changed: number }>("/api/v1/transactions/bulk", { method: "POST", body: JSON.stringify({ ids, tags_add: [], tags_remove: [], ...change }) });
      setSaid({ id: null, tone: "info", text: `Changed ${r.changed} row${r.changed === 1 ? "" : "s"}: ${what}.` });
      setSelected(new Set());
      setBulkCategory("");
      setBulkAdd("");
      setBulkRemove("");
      reload();
    } catch (err) {
      setSaid({ id: null, tone: "error", text: `Couldn't change them: ${(err as Error).message}` });
    } finally {
      setBusy(false);
    }
  }

  // A row's own message shows inside its open editor. It shows here instead for many rows at once, and when the row
  // has left the list (hidden, or now a transfer) or its editor is closed.
  const saidHere = said && (said.id === null || !shownIds.has(said.id) || openId !== said.id) ? said : null;
  const n = selected.size;
  const newCategory = bulkCategory.trim();

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", gap: 12 }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Transactions</h1>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>What you spent, from every account the app reads. Tap a merchant to change its category, note or tags, or to split it.</div>
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(text.trim());
          setTag(tagText.trim().toLowerCase());
        }}
        style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}
      >
        <label style={{ ...FIELD, flex: "3 1 240px" }}>
          <span className="lab">Search</span>
          <input className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="merchant, description, note" />
        </label>
        <label style={{ ...FIELD, flex: "1 1 140px" }}>
          <span className="lab">Tag</span>
          <input className="input" list="tx-tags" value={tagText} onChange={(e) => setTagText(e.target.value)} placeholder="trip" />
        </label>
        <button className="btn dark" type="submit">Find</button>
        {(query || tag) && <button className="btn" type="button" onClick={() => { setText(""); setQuery(""); setTagText(""); setTag(""); }}>Clear the search</button>}
      </form>
      <div role="radiogroup" aria-label="Which rows to list" style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <button type="button" role="radio" aria-checked={!everything} className={everything ? "btn" : "btn dark"} onClick={() => setEverything(false)} style={{ minHeight: 34, padding: "4px 12px" }}>Spending only</button>
        <button type="button" role="radio" aria-checked={everything} className={everything ? "btn dark" : "btn"} onClick={() => setEverything(true)} style={{ minHeight: 34, padding: "4px 12px" }}>Everything: transfers, card payments, income and hidden rows too</button>
      </div>
      <LedgerRules onChanged={reload} />
      <datalist id="tx-categories">{categories.map((c) => <option key={c} value={c} />)}</datalist>
      <datalist id="tx-tags">{tagsSeen.map((w) => <option key={w} value={w} />)}</datalist>

      {n > 0 && (
        <div role="group" aria-label="Change the selected rows" className="card" style={{ position: "sticky", top: 0, zIndex: 2, padding: "10px 14px", display: "flex", flexDirection: "column", gap: 10, border: "2px solid var(--ink)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <strong style={{ fontSize: 14 }}>{n} row{n === 1 ? "" : "s"} selected</strong>
            <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Each change below goes to all of them at once.</span>
            <div style={{ flexGrow: 1 }} />
            <button type="button" className="btn" onClick={() => setSelected(new Set())} style={SMALL}>Clear the selection</button>
          </div>
          <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "flex-end" }}>
            <div style={{ display: "flex", gap: 6, alignItems: "flex-end", flexWrap: "wrap" }}>
              <label style={FIELD}>
                <span className="lab">Set the category</span>
                <input className="input" list="tx-categories" value={bulkCategory} onChange={(e) => setBulkCategory(e.target.value)} style={{ minHeight: 34, padding: "4px 10px", width: "min(100%, 180px)" }} />
              </label>
              {newCategory
                ? <ConfirmButton label="Put them in it" question={`Put ${n} row${n === 1 ? "" : "s"} in ${newCategory}? The categories they have now are not kept.`} yes="Yes, change them" no="Not now" onConfirm={() => applyBulk({ category: newCategory }, `put in ${newCategory}`)} style={SMALL} />
                : <button type="button" className="btn" disabled style={SMALL}>Put them in it</button>}
            </div>
            <form onSubmit={(e) => { e.preventDefault(); const w = tagWords(bulkAdd); if (w.length) applyBulk({ tags_add: w }, `tagged ${w.join(", ")}`); }} style={{ display: "flex", gap: 6, alignItems: "flex-end", flexWrap: "wrap" }}>
              <label style={FIELD}>
                <span className="lab">Add a tag</span>
                <input className="input" list="tx-tags" value={bulkAdd} onChange={(e) => setBulkAdd(e.target.value)} placeholder="trip" style={{ minHeight: 34, padding: "4px 10px", width: "min(100%, 150px)" }} />
              </label>
              <button type="submit" className="btn" disabled={busy || tagWords(bulkAdd).length === 0} style={SMALL}>Add the tag</button>
            </form>
            <form onSubmit={(e) => { e.preventDefault(); const w = tagWords(bulkRemove); if (w.length) applyBulk({ tags_remove: w }, `took off ${w.join(", ")}`); }} style={{ display: "flex", gap: 6, alignItems: "flex-end", flexWrap: "wrap" }}>
              <label style={FIELD}>
                <span className="lab">Remove a tag</span>
                <input className="input" list="tx-tags" value={bulkRemove} onChange={(e) => setBulkRemove(e.target.value)} style={{ minHeight: 34, padding: "4px 10px", width: "min(100%, 150px)" }} />
              </label>
              <button type="submit" className="btn" disabled={busy || tagWords(bulkRemove).length === 0} style={SMALL}>Remove the tag</button>
            </form>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              <button type="button" className="btn" disabled={busy} onClick={() => applyBulk({ hidden: true }, "hidden")} style={{ ...SMALL, display: "inline-flex", alignItems: "center", gap: 4 }}><EyeOff size={13} aria-hidden="true" /> Hide them</button>
              <button type="button" className="btn" disabled={busy} onClick={() => applyBulk({ hidden: false }, "shown again")} style={{ ...SMALL, display: "inline-flex", alignItems: "center", gap: 4 }}><Eye size={13} aria-hidden="true" /> Show them</button>
            </div>
          </div>
        </div>
      )}
      {saidHere && <div><Said text={saidHere.text} tone={saidHere.tone} /></div>}

      <div className="card" style={{ padding: "12px 16px", overflowX: "auto" }}>
        {error && !data && <Failed error={error} what="your transactions" onRetry={reload} card={false} />}
        {!data && !error && <Loading what="your transactions" card={false} />}
        {data && rows.length === 0 && (query || tag
          ? <Empty card={false}>No transactions match{query ? ` “${query}”` : ""}{tag ? ` with the tag “${tag}”` : ""}. Try one word from the merchant's name, or clear the search.</Empty>
          : everything
            ? <Empty card={false} to="/settings/connectors#import" action="Import a file">No transactions yet. Connect a bank or import a statement on Settings › Connections.</Empty>
            : <Empty card={false}>No spending in the newest rows. Pick “Everything” to see transfers, card payments, income and hidden rows.</Empty>)}
        {data && rows.length > 0 && (
          <>
            <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, fontSize: 12, color: "var(--muted)", marginBottom: 8 }}>
              <span>
                {data.returned} shown{tag ? ` · tagged “${tag}”` : ""}
                {everything ? " · everything, including transfers, card payments, income and hidden rows" : ` · spending only; transfers, card payments, income and hidden rows are left out · total ${money(data.total_amount)}`}
              </span>
              {data.returned >= limit && limit < 500 && <button type="button" className="btn" onClick={() => setLimit(500)} style={SMALL}>Show up to 500</button>}
            </div>
            {(tagsSeen.length > 0 || tag) && (
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", marginBottom: 8 }}>
                <span className="lab">{tag ? "Showing the tag" : "Show one tag"}</span>
                {(tag && !tagsSeen.includes(tag) ? [tag, ...tagsSeen] : tagsSeen).map((w) => (
                  <button key={w} type="button" aria-pressed={tag === w} className={tag === w ? "btn dark" : "btn"} onClick={() => pickTag(tag === w ? "" : w)} style={{ ...SMALL, display: "inline-flex", alignItems: "center", gap: 4 }}>
                    <Tag size={12} aria-hidden="true" /> {w}
                  </button>
                ))}
                {tag && <button type="button" className="btn" onClick={() => pickTag("")} style={SMALL}>Show every tag</button>}
              </div>
            )}
            {rows.some((r) => r.pending) && (
              <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", fontSize: 12, color: "var(--ink-2)", marginBottom: 8 }}>
                <Mark Icon={Clock} word="Pending" tone="pending" /> means the bank has not finished it yet. It is already taken off the bank's balance.
              </div>
            )}
            <label style={{ display: "inline-flex", alignItems: "center", gap: 8, minHeight: 32, fontSize: 13, fontWeight: 700, cursor: "pointer", marginBottom: 4 }}>
              <input ref={allBox} type="checkbox" checked={allOn} onChange={() => setSelected(allOn ? new Set() : new Set(rows.map((r) => r.id)))} style={{ width: 18, height: 18, margin: 0 }} />
              Select all shown ({rows.length})
            </label>
            <table className="ledger">
              <thead><tr><th><span className="sr-only">Selected</span></th><th>When</th><th>Merchant</th><th>Category</th><th>Account</th><th style={{ textAlign: "right" }}>Amount</th></tr></thead>
              <tbody>
                {rows.map((t) => (
                  <Fragment key={t.id}>
                    <TxRow t={t} selected={selected.has(t.id)} open={openId === t.id} onSelect={toggle} onOpen={openRow} />
                    {openId === t.id && <Editor row={t} onSaved={saved} onClose={closeEditor} />}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  );
}
