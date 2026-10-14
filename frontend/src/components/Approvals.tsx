// Sends waiting for the owner: what would go, to whom, from whom it came; approve, deny, or approve and
// always allow that recipient. Nothing leaves until a tap here, or an allowance made here.
import { useState } from "react";
import { api, useApi } from "../api";

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
}

interface ApprovalsOut {
  approvals: ApprovalRow[];
  pending: number;
  allowances: string[];
}

export function Approvals({ onChanged }: { onChanged?: () => void }) {
  const { data, reload } = useApi<ApprovalsOut>("/api/v1/approvals?status=all&limit=30");
  const [always, setAlways] = useState<Record<number, boolean>>({});
  const [note, setNote] = useState<string | null>(null);
  const pending = (data?.approvals ?? []).filter((a) => a.status === "pending");
  const recent = (data?.approvals ?? []).filter((a) => a.status !== "pending").slice(0, 5);

  async function decide(a: ApprovalRow, action: "approve" | "deny") {
    try {
      const r = await api<ApprovalRow>(`/api/v1/approvals/${a.id}/${action}`, { method: "POST", body: JSON.stringify({ allow_always: Boolean(always[a.id]) }) });
      setNote(r.status === "sent" ? `Sent to ${r.to}.` : r.status === "failed" ? `Not sent: ${r.error}` : r.status === "denied" ? "Denied; nothing was sent." : r.status);
      reload();
      onChanged?.();
    } catch (e) {
      setNote((e as Error).message);
    }
  }
  async function drop(recipient: string) {
    await api(`/api/v1/approvals/allowances/${encodeURIComponent(recipient)}`, { method: "DELETE" });
    reload();
  }

  if (!data) return null;
  if (pending.length === 0 && recent.length === 0 && data.allowances.length === 0) return null;
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div className="lab">Sends waiting for you · {pending.length}</div>
      {pending.map((a) => (
        <div key={a.id} className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Drafted by <strong>{a.created_by}</strong>{a.reason ? ` · ${a.reason}` : ""} · {a.created_at.slice(0, 16).replace("T", " ")}</div>
          <div style={{ fontSize: 13 }}><span style={{ color: "var(--muted)" }}>To</span> <strong>{a.to}</strong></div>
          <div style={{ fontSize: 14, fontWeight: 700 }}>{a.subject}</div>
          <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontFamily: "inherit", fontSize: 13, lineHeight: 1.45, color: "var(--ink-2)", maxHeight: 160, overflow: "auto" }}>{a.body}</pre>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <button className="btn dark" type="button" onClick={() => decide(a, "approve")}>Approve and send</button>
            <button className="btn" type="button" onClick={() => decide(a, "deny")}>Deny</button>
            <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--ink-2)" }}>
              <input type="checkbox" checked={Boolean(always[a.id])} onChange={(e) => setAlways({ ...always, [a.id]: e.target.checked })} /> always allow sends to this address
            </label>
          </div>
        </div>
      ))}
      {note && <div style={{ fontSize: 13, color: "var(--ink-2)" }}>{note}</div>}
      {recent.length > 0 && (
        <div style={{ fontSize: 12, color: "var(--muted)" }}>
          Recently: {recent.map((a) => `${a.status === "sent" ? "sent" : a.status === "failed" ? "failed" : a.status} to ${a.to} (${a.subject})`).join("; ")}.
        </div>
      )}
      {data.allowances.length > 0 && (
        <div style={{ fontSize: 12, color: "var(--muted)", display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          Always allowed:
          {data.allowances.map((r) => (
            <span key={r} style={{ display: "inline-flex", gap: 4, alignItems: "center", padding: "2px 8px", borderRadius: 999, border: "1px solid var(--hairline)" }}>
              {r} <button type="button" onClick={() => drop(r)} aria-label={`Stop allowing ${r}`} style={{ border: 0, background: "none", cursor: "pointer", color: "var(--ink-2)", font: "inherit" }}>×</button>
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
