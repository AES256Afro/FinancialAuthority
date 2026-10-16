// Emails waiting for the owner's OK: what would go, to whom, who asked for it; approve, deny, or approve and
// always allow that recipient. Nothing leaves until a tap here, or an allowance made here. Each person approves
// only their own sends: someone else's are listed with whose they are, and no button.
import { User } from "lucide-react";
import { useState } from "react";
import { api, useApi } from "../api";
import { when } from "../format";
import { ConfirmButton } from "./ConfirmButton";
import { Failed, Loading, Said } from "./State";

interface ApprovalRow {
  id: number;
  status: "pending" | "approved" | "denied" | "sent" | "failed";
  to: string;
  subject: string;
  body: string;
  reason: string;
  source: string;
  created_by: string;
  created_kind: string;
  created_at: string;
  decided_by: string;
  sent_at: string | null;
  error: string;
  for_person_id?: number | null;
  mine?: boolean; // left out by an older server: then every send is the caller's
  whose?: string; // "yours", the owner's username, or the person's name
}

interface ApprovalsOut {
  approvals: ApprovalRow[];
  pending: number;
  allowances: string[];
}

const STATUS_WORD: Record<ApprovalRow["status"], string> = { pending: "waiting", approved: "approved", denied: "denied", sent: "sent", failed: "not sent (it failed)" };
const KIND_WORD: Record<string, string> = { key: " (an app using a key)", oauth: " (a connected app)" };

export function Approvals({ onChanged }: { onChanged?: () => void }) {
  const state = useApi<ApprovalsOut>("/api/v1/approvals?status=all&limit=30");
  // A connected app is recorded by its client id; its name comes from the list of connected apps.
  const clients = useApi<{ clients: { client_id: string; name: string }[] }>("/api/v1/oauth/clients");
  const [always, setAlways] = useState<Record<number, boolean>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [note, setNote] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  const data = state.data;
  const mine = (a: ApprovalRow) => a.mine !== false;
  const pending = (data?.approvals ?? []).filter((a) => a.status === "pending" && mine(a));
  const others = (data?.approvals ?? []).filter((a) => a.status === "pending" && !mine(a));
  const recent = (data?.approvals ?? []).filter((a) => a.status !== "pending").slice(0, 5);

  function who(a: ApprovalRow): string {
    if (a.created_kind === "owner" || a.created_kind === "person") return mine(a) ? "you" : a.created_by;
    return clients.data?.clients.find((c) => c.client_id === a.created_by)?.name || a.created_by;
  }
  const whose = (a: ApprovalRow) => a.whose || "someone else";

  async function decide(a: ApprovalRow, action: "approve" | "deny") {
    setBusy(a.id);
    setNote(null);
    try {
      const r = await api<ApprovalRow>(`/api/v1/approvals/${a.id}/${action}`, { method: "POST", body: JSON.stringify({ allow_always: Boolean(always[a.id]) }) });
      setNote(r.status === "sent" ? { text: `Sent to ${r.to}.`, tone: "info" } : r.status === "failed" ? { text: `Not sent: ${r.error}`, tone: "error" } : r.status === "denied" ? { text: "Denied. Nothing was sent.", tone: "info" } : { text: `Now ${STATUS_WORD[r.status] ?? r.status}.`, tone: "info" });
      state.reload();
      onChanged?.();
    } catch (e) {
      setNote({ text: (e as Error).message, tone: "error" });
    } finally {
      setBusy(null);
    }
  }
  async function drop(recipient: string) {
    await api(`/api/v1/approvals/allowances/${encodeURIComponent(recipient)}`, { method: "DELETE" });
    state.reload();
  }

  if (state.error && !data) return <Failed error={state.error} what="the emails waiting for your OK" onRetry={state.reload} />;
  if (!data) return <Loading what="the emails waiting for your OK" card={false} />;
  if (pending.length === 0 && others.length === 0 && recent.length === 0 && data.allowances.length === 0 && !note) return null;
  return (
    <section aria-labelledby="approvals-title" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <h2 id="approvals-title" className="lab" style={{ margin: 0 }}>Emails waiting for your OK · {pending.length}</h2>
      {pending.map((a) => (
        <div key={a.id} className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Asked for by <strong>{who(a)}</strong>{KIND_WORD[a.created_kind] ?? ""}{a.reason ? ` · ${a.reason}` : ""} · {when(a.created_at)}</div>
          <div style={{ fontSize: 13 }}><span style={{ color: "var(--muted)" }}>To</span> <strong>{a.to}</strong></div>
          <div style={{ fontSize: 14, fontWeight: 700 }}>{a.subject}</div>
          <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontFamily: "inherit", fontSize: 13, lineHeight: 1.45, color: "var(--ink-2)", maxHeight: 160, overflow: "auto" }}>{a.body}</pre>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <button className="btn dark" type="button" disabled={busy === a.id} onClick={() => decide(a, "approve")}>{busy === a.id ? "Working…" : "Approve and send"}</button>
            <button className="btn" type="button" disabled={busy === a.id} onClick={() => decide(a, "deny")}>Deny</button>
            <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--ink-2)", minHeight: 32 }}>
              <input type="checkbox" checked={Boolean(always[a.id])} onChange={(e) => setAlways({ ...always, [a.id]: e.target.checked })} /> always allow sends to this address
            </label>
          </div>
        </div>
      ))}
      {note && <Said text={note.text} tone={note.tone} />}
      {others.length > 0 && (
        <>
          <h3 className="lab" style={{ margin: "4px 0 0" }}>Waiting for someone else's OK · {others.length}</h3>
          {others.map((a) => (
            <div key={a.id} className="card" style={{ padding: "10px 14px", display: "flex", flexDirection: "column", gap: 6, borderStyle: "dashed" }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 12, color: "var(--ink-2)" }}>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: "2px 8px", borderRadius: 999, background: "var(--surface-2)", fontWeight: 800 }}><User size={12} aria-hidden="true" />{whose(a)}</span>
                Asked for by <strong>{who(a)}</strong>{KIND_WORD[a.created_kind] ?? ""} · {when(a.created_at)}
              </div>
              <div style={{ fontSize: 13 }}><span style={{ color: "var(--muted)" }}>To</span> <strong>{a.to}</strong> · {a.subject}</div>
              <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{whose(a)} approves this one. Each person approves only their own emails.</div>
            </div>
          ))}
        </>
      )}
      {recent.length > 0 && (
        <div style={{ fontSize: 12, color: "var(--muted)" }}>
          Recently: {recent.map((a) => `${STATUS_WORD[a.status] ?? a.status} to ${a.to} (${a.subject}${mine(a) ? "" : `, ${whose(a)}`})`).join("; ")}.
        </div>
      )}
      {data.allowances.length > 0 && (
        <div style={{ fontSize: 12, color: "var(--muted)", display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          Always allowed, no asking:
          {data.allowances.map((r) => (
            <span key={r} style={{ display: "inline-flex", gap: 6, alignItems: "center", padding: "2px 4px 2px 10px", borderRadius: 999, border: "1px solid var(--hairline)", color: "var(--ink-2)" }}>
              {r}
              <ConfirmButton label="Stop allowing" question={`Ask you again before each email to ${r}?`} yes="Yes, ask me each time" no="Keep allowing" onConfirm={() => drop(r)} style={{ minHeight: 32, padding: "4px 10px", fontSize: 12 }} />
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
