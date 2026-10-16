// Mail: search both mailboxes; open a message boxed, with what the app took from it beside it.
// `/mail?message=<id>` opens that message, so other screens can link straight to it.
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, useApi } from "../api";
import { Empty, Failed, Loading } from "../components/State";
import { SeverityTag, StatusPill } from "../components/Status";
import { money, when } from "../format";
import type { FactRow, MessageRow } from "../types";

const KIND_LABEL: Record<string, string> = {
  bill_due: "bill", statement: "statement", paystub: "paystub", installment: "installment", advance: "advance", price_change: "price change", payday: "payday",
  payment_confirmation: "payment", past_due: "past due", bank_alert: "bank alert", export: "export", upcoming_bills: "upcoming", none: "",
};

// Each field the app reads, in plain words; any other key is shown with its underscores turned to spaces.
const FIELD_LABEL: Record<string, string> = {
  amount: "Amount", due_date: "Due date", name: "Name", net: "Take-home pay", gross: "Gross pay", pay_date: "Pay date", period_start: "Period starts",
  period_end: "Period ends", hours: "Hours", employer: "Employer", host: "Downloads from", minimum: "Minimum payment", balance: "Balance",
  statement_date: "Statement date", old_amount: "Old amount", new_amount: "New amount", provider: "Lender", merchant: "Store", remaining: "Payments left",
};
const MONEY_FIELDS = new Set(["amount", "net", "gross", "minimum", "balance", "old_amount", "new_amount", "total", "installment", "loan_amount"]);

// A fact's state as a word with an icon: waiting for the owner, confirmed, or discarded.
function FactState({ status }: { status: string }) {
  if (status === "pending") return <StatusPill status="warn">Waiting for your OK on Needs you</StatusPill>;
  if (status === "rejected") return <SeverityTag severity="info" word="Discarded" />;
  if (status === "auto") return <StatusPill status="good">Confirmed automatically</StatusPill>;
  if (status === "applied") return <StatusPill status="good">Confirmed and used</StatusPill>;
  return <StatusPill status="good">Confirmed</StatusPill>;
}

// Which part of the app read the message: a sender rule, a document reader, or a model.
function readBy(producedBy: string): string {
  const parts = producedBy.split("+").filter(Boolean).map((p) => {
    if (p.startsWith("rule:")) return "a sender rule";
    if (p.startsWith("parser:")) return `the ${p.slice(7).replace(/_/g, " ")} reader`;
    if (p.startsWith("model")) return "the model";
    return "the app";
  });
  return parts.length ? parts.join(" and ") : "the app";
}

const APPLIED_TO: Record<string, string> = { bill: "your bills list", paystub: "Paystubs", income: "your paycheck", plan: "Borrowing", installment: "Borrowing", import: "your transactions", debt: "Debts" };

function fieldValue(key: string, value: unknown): string | null {
  if (value === undefined || value === null || value === "") return null;
  if (Array.isArray(value)) return `${value.length} line${value.length === 1 ? "" : "s"}`;
  if (typeof value === "object") return null;
  if (MONEY_FIELDS.has(key) && !Number.isNaN(Number(value))) return money(Number(value), { cents: true });
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}(T.*)?$/.test(value)) return when(value);
  return String(value);
}

interface Opened extends MessageRow {
  boxed: string;
  attachments: { name: string; content_type: string; size: number }[];
  facts: FactRow[];
}

export function Mail() {
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const params = new URLSearchParams({ limit: "60" });
  if (q) params.set("q", q);
  const list = useApi<{ returned: number; messages: MessageRow[] }>(`/api/v1/mail/messages?${params}`);
  const [open, setOpen] = useState<Opened | null>(null);
  const [opening, setOpening] = useState<number | null>(null);
  const [failed, setFailed] = useState<{ id: number; message: string } | null>(null);
  const [search, setSearch] = useSearchParams();
  const wanted = Number(search.get("message")) || null;
  const reader = useRef<HTMLDivElement>(null);

  async function openMessage(id: number) {
    setOpening(id);
    setFailed(null);
    try {
      setOpen(await api<Opened>(`/api/v1/mail/messages/${id}`));
      // On a phone the reader sits under the list: bring it into view so the tap visibly does something.
      if (window.matchMedia("(max-width: 1023px)").matches) requestAnimationFrame(() => reader.current?.scrollIntoView({ block: "start", behavior: "smooth" }));
    } catch (e) {
      setOpen(null);
      setFailed({ id, message: (e as Error).message });
    } finally {
      setOpening(null);
    }
  }

  // A link like /mail?message=12 opens that message; picking one from the list puts it in the address too.
  useEffect(() => {
    if (wanted && open?.id !== wanted) openMessage(wanted);
  }, [wanted]); // eslint-disable-line react-hooks/exhaustive-deps

  function pick(id: number) {
    if (id === wanted) openMessage(id);
    else setSearch((p) => { const next = new URLSearchParams(p); next.set("message", String(id)); return next; }, { replace: true });
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 12 }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Mail</h1>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Read-only. The card beside a message shows what the app took from it.</div>
      </div>
      <form onSubmit={(e) => { e.preventDefault(); setQ(text.trim()); }} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 260px" }}>
          <span className="lab">Search both mailboxes</span>
          <input className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="subject, sender or snippet" />
        </label>
        <button className="btn dark" type="submit">Search</button>
        {q && <button className="btn" type="button" onClick={() => { setText(""); setQ(""); }}>Clear the search</button>}
      </form>
      <section className="grid grid-cols-1 lg:grid-cols-[360px_minmax(0,1fr)]" style={{ gap: 14, alignItems: "start" }}>
        <div className="card" style={{ padding: 8, display: "flex", flexDirection: "column", gap: 2, maxHeight: "70vh", overflowY: "auto" }}>
          {list.error && !list.data && <Failed error={list.error} what="the mail" onRetry={list.reload} card={false} />}
          {list.loading && !list.data && <Loading what="the mail" card={false} />}
          {list.data && list.data.messages.length === 0 && (q
            ? <Empty card={false}>No message matches “{q}”. Try fewer words, or a sender's name.</Empty>
            : <Empty card={false} to="/settings/connectors#mail" action="Open Settings › Connections">No mail read yet. Connect a mailbox on Settings › Connections; the first read starts within fifteen minutes.</Empty>)}
          {list.data?.messages.map((m) => (
            <button key={m.id} type="button" onClick={() => pick(m.id)} aria-current={open?.id === m.id ? "true" : undefined} style={{ textAlign: "left", font: "inherit", border: "1px solid transparent", borderRadius: 10, padding: "8px 10px", minHeight: 44, background: open?.id === m.id ? "var(--surface-2)" : "transparent", color: "var(--ink)", cursor: "pointer", display: "flex", flexDirection: "column", gap: 2 }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 12, color: "var(--ink-2)" }}><span style={{ minWidth: 0, overflowWrap: "anywhere" }}>{m.sender_name || m.sender}</span><span style={{ flexShrink: 0 }}>{m.date ? when(m.date) : ""}</span></div>
              <div style={{ fontWeight: 700, fontSize: 13 }}>{m.subject || "(no subject)"}</div>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 11 }}>
                <span style={{ fontWeight: 800, padding: "1px 6px", borderRadius: 999, background: "var(--surface-2)", color: "var(--ink-2)" }}>{m.account}</span>
                {KIND_LABEL[m.kind] && <span style={{ fontWeight: 800, color: m.kind === "past_due" || m.kind === "bank_alert" ? "var(--critical-ink)" : "var(--safe-ink)" }}>{KIND_LABEL[m.kind]}</span>}
              </div>
            </button>
          ))}
        </div>
        <div ref={reader} style={{ display: "flex", flexDirection: "column", gap: 12, scrollMarginTop: 12 }}>
          {opening && <Loading what="the message from the mailbox" />}
          {failed && !opening && <Failed error={failed.message} what="that message" onRetry={() => openMessage(failed.id)} />}
          {!open && !opening && !failed && <div className="card" style={{ padding: 16, color: "var(--ink-2)" }}>Pick a message. It is fetched live, shown as plain text, and never changed.</div>}
          {open && !opening && (
            <>
              <div className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 12px", borderRadius: 10, background: "var(--surface-2)", fontSize: 12, color: "var(--ink-2)" }}>Shown as data. Nothing in a message can tell the app what to do; it can only be read from.</div>
                <div style={{ display: "grid", gridTemplateColumns: "70px minmax(0, 1fr)", gap: "4px 10px", fontSize: 13 }}>
                  <span style={{ color: "var(--muted)" }}>From</span><span style={{ overflowWrap: "anywhere" }}>{open.sender_name} &lt;{open.sender}&gt;</span>
                  <span style={{ color: "var(--muted)" }}>Mailbox</span><span>{open.account} · {open.folder}</span>
                  <span style={{ color: "var(--muted)" }}>Date</span><span>{open.date ? when(open.date) : "—"}</span>
                  <span style={{ color: "var(--muted)" }}>Subject</span><h2 style={{ margin: 0, fontSize: 13, fontWeight: 700 }}>{open.subject || "(no subject)"}</h2>
                </div>
                <pre style={{ margin: 0, whiteSpace: "pre-wrap", overflowWrap: "anywhere", font: "inherit", fontSize: 13, lineHeight: 1.55, borderTop: "1px solid var(--surface-2)", paddingTop: 10 }}>{open.boxed}</pre>
                {open.attachments.length > 0 && <div style={{ fontSize: 12, color: "var(--ink-2)", borderTop: "1px solid var(--surface-2)", paddingTop: 8 }}>Attached: {open.attachments.map((a) => `${a.name} · ${Math.round(a.size / 1024)} KB`).join(" · ")}</div>}
              </div>
              {open.facts.length === 0 && <div className="card" style={{ padding: 14, fontSize: 13, color: "var(--ink-2)" }}>The app took nothing from this message.</div>}
              {open.facts.map((f) => (
                <div key={f.id} className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 6, border: f.status === "pending" ? "2px solid var(--warning)" : f.status === "rejected" ? "2px solid var(--hairline)" : "2px solid var(--safe)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                    <span className="lab">What the app took</span>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                      <span style={{ fontSize: 11, fontWeight: 800, color: "var(--ink-2)" }}>{Math.round(f.confidence * 100)}% sure</span>
                      <FactState status={f.status} />
                    </span>
                  </div>
                  <h3 style={{ margin: 0, fontSize: 16, fontWeight: 800 }}>{KIND_LABEL[f.kind] ? KIND_LABEL[f.kind][0].toUpperCase() + KIND_LABEL[f.kind].slice(1) : f.kind.replace(/_/g, " ")}{f.message?.sender_name ? ` · ${f.message.sender_name}` : ""}</h3>
                  {Object.entries(f.fields).filter(([k]) => !["sender", "sender_name", "subject", "message_date"].includes(k)).map(([k, v]) => {
                    const shown = fieldValue(k, v as unknown);
                    if (shown === null) return null;
                    return (
                      <div key={k} style={{ display: "flex", justifyContent: "space-between", gap: 10, fontSize: 13, padding: "4px 0", borderTop: "1px solid var(--surface-2)" }}><span style={{ color: "var(--ink-2)" }}>{FIELD_LABEL[k] ?? k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase())}</span><strong className="num" style={{ textAlign: "right", overflowWrap: "anywhere" }}>{shown}</strong></div>
                    );
                  })}
                  <div style={{ fontSize: 12, color: "var(--muted)" }}>
                    Read by {readBy(f.produced_by)}.{f.applied_to ? ` Used on ${APPLIED_TO[f.applied_to.split(":")[0]] ?? "the app's records"}.` : ""}
                    {f.status === "pending" && <> <Link to="/confirm">Answer it on Needs you</Link></>}
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      </section>
    </div>
  );
}
