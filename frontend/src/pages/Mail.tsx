// Mail: search both mailboxes; open a message boxed, with what the app took from it beside it.
import { useState } from "react";
import { api, useApi } from "../api";
import { money } from "../format";
import type { FactRow, MessageRow } from "../types";

const KIND_LABEL: Record<string, string> = {
  bill_due: "bill", statement: "statement", paystub: "paystub", installment: "installment", advance: "advance", price_change: "price change", payday: "payday",
  payment_confirmation: "payment", past_due: "past due", bank_alert: "bank alert", export: "export", upcoming_bills: "upcoming", none: "",
};

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

  async function openMessage(id: number) {
    setOpening(id);
    try {
      setOpen(await api<Opened>(`/api/v1/mail/messages/${id}`));
    } catch (e) {
      setOpen(null);
      alert((e as Error).message);
    } finally {
      setOpening(null);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 12 }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Mail, through the lens</h1>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Both mailboxes. The message is data; the card beside it is what the app took from it.</div>
      </div>
      <form onSubmit={(e) => { e.preventDefault(); setQ(text.trim()); }} style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="Search subject, sender or snippet" aria-label="Search both mailboxes" style={{ flex: "1 1 260px" }} />
        <button className="btn dark" type="submit">Search</button>
      </form>
      <section className="grid grid-cols-1 lg:grid-cols-[360px_minmax(0,1fr)]" style={{ gap: 14, alignItems: "start" }}>
        <div className="card" style={{ padding: 8, display: "flex", flexDirection: "column", gap: 2, maxHeight: "70vh", overflowY: "auto" }}>
          {list.loading && <div style={{ padding: 10, color: "var(--muted)" }}>Looking…</div>}
          {list.data && list.data.messages.length === 0 && <div style={{ padding: 10, color: "var(--muted)" }}>Nothing yet. Connect a mailbox on Settings › Connectors; the first read starts within fifteen minutes.</div>}
          {list.data?.messages.map((m) => (
            <button key={m.id} type="button" onClick={() => openMessage(m.id)} style={{ textAlign: "left", font: "inherit", border: "1px solid transparent", borderRadius: 10, padding: "8px 10px", background: open?.id === m.id ? "var(--surface-2)" : "transparent", color: "var(--ink)", cursor: "pointer", display: "flex", flexDirection: "column", gap: 2 }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 12, color: "var(--ink-2)" }}><span>{m.sender_name || m.sender}</span><span>{m.date ? m.date.slice(5, 10) : ""}</span></div>
              <div style={{ fontWeight: 700, fontSize: 13 }}>{m.subject || "(no subject)"}</div>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 11 }}>
                <span style={{ fontWeight: 800, padding: "1px 6px", borderRadius: 999, background: "var(--surface-2)", color: "var(--ink-2)" }}>{m.account}</span>
                {KIND_LABEL[m.kind] && <span style={{ fontWeight: 800, color: m.kind === "past_due" || m.kind === "bank_alert" ? "var(--critical)" : "var(--safe-ink)" }}>{KIND_LABEL[m.kind]}</span>}
              </div>
            </button>
          ))}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {opening && <div className="card" style={{ padding: 16, color: "var(--muted)" }}>Fetching from the mailbox…</div>}
          {!open && !opening && <div className="card" style={{ padding: 16, color: "var(--muted)" }}>Pick a message. It is fetched live, shown boxed, and never changed.</div>}
          {open && !opening && (
            <>
              <div className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 12px", borderRadius: 10, background: "var(--surface-2)", fontSize: 12, color: "var(--ink-2)" }}>Shown as data. Nothing in a message can tell the app what to do; it can only be read from.</div>
                <div style={{ display: "grid", gridTemplateColumns: "70px 1fr", gap: "4px 10px", fontSize: 13 }}>
                  <span style={{ color: "var(--muted)" }}>From</span><span>{open.sender_name} &lt;{open.sender}&gt;</span>
                  <span style={{ color: "var(--muted)" }}>Mailbox</span><span>{open.account} · {open.folder}</span>
                  <span style={{ color: "var(--muted)" }}>Date</span><span>{open.date ? open.date.replace("T", " ").slice(0, 16) : "—"}</span>
                  <span style={{ color: "var(--muted)" }}>Subject</span><strong>{open.subject}</strong>
                </div>
                <pre style={{ margin: 0, whiteSpace: "pre-wrap", font: "inherit", fontSize: 13, lineHeight: 1.55, borderTop: "1px solid var(--surface-2)", paddingTop: 10 }}>{open.boxed}</pre>
                {open.attachments.length > 0 && <div style={{ fontSize: 12, color: "var(--ink-2)", borderTop: "1px solid var(--surface-2)", paddingTop: 8 }}>{open.attachments.map((a) => `${a.name} · ${Math.round(a.size / 1024)} KB`).join(" · ")}</div>}
              </div>
              {open.facts.map((f) => (
                <div key={f.id} className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 4, border: f.status === "pending" ? "2px solid var(--warning)" : "2px solid var(--safe)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}><span className="lab">What the app took</span><span style={{ fontSize: 11, fontWeight: 800, color: "var(--ink-2)" }}>{Math.round(f.confidence * 100)}% · {f.status}</span></div>
                  <div style={{ fontSize: 16, fontWeight: 800 }}>{KIND_LABEL[f.kind] || f.kind}{f.message?.sender_name ? ` · ${f.message.sender_name}` : ""}</div>
                  {Object.entries(f.fields).filter(([k, v]) => v !== undefined && !["sender", "sender_name", "subject", "message_date"].includes(k)).map(([k, v]) => (
                    <div key={k} style={{ display: "flex", justifyContent: "space-between", fontSize: 13, padding: "4px 0", borderTop: "1px solid var(--surface-2)" }}><span style={{ color: "var(--ink-2)" }}>{k.replace(/_/g, " ")}</span><strong className="num">{k === "amount" || k === "net" || k === "gross" ? money(Number(v), { cents: true }) : String(v)}</strong></div>
                  ))}
                  <div style={{ fontSize: 12, color: "var(--muted)" }}>Read by {f.produced_by.replace("rule:", "sender rule ")}.{f.applied_to ? ` Applied to ${f.applied_to}.` : ""}</div>
                </div>
              ))}
            </>
          )}
        </div>
      </section>
    </div>
  );
}
