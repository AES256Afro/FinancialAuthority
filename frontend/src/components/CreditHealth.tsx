// Credit health from your own data (M48): each card as one bar of its limit with marks at 10% and 30%, the share it
// would report, and what to pay before its statement; then the on-time record as one square per payment. Every
// figure is the engine's; nothing is a credit pull.
import { CircleCheck, CircleX } from "lucide-react";
import { Link } from "react-router-dom";
import { useApi } from "../api";
import { money, shortDate } from "../format";
import { Show } from "./State";
import { StatusPill } from "./Status";

interface CreditCard {
  account_id: number;
  name: string;
  balance: number;
  credit_limit: number | null;
  share: number | null;
  status: "good" | "warn" | "crit" | null;
  word: string;
  next_statement: string | null;
  pay_for_30?: number;
  pay_for_10?: number;
  source: string;
}

interface CreditOut {
  as_of: string;
  cards: CreditCard[];
  overall: { balance: number; limit: number; share: number | null; status: "good" | "warn" | "crit" | null };
  payments: { on_time: number; late: number; not_seen: number; total: number; share_on_time: number | null; streak: number; late_rows: { name: string; due_date: string; status: string; paid_date: string | null; source: string }[] };
  cannot_see: string[];
  word: string;
  not_advice: string;
}

const STATUS_WORD = { good: "Under 10%", warn: "Under 30%", crit: "Over 30%" } as const;

export function CreditHealth() {
  const state = useApi<CreditOut>("/api/v1/credit");
  return (
    <Show state={state} what="your credit health">
      {(c) => (
        <section className="card" aria-labelledby="credit-title" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
            <h2 id="credit-title" style={{ margin: 0, fontSize: 16, fontWeight: 800 }}>Credit health, from your own data</h2>
            {c.overall.status && <StatusPill status={c.overall.status}>{`Cards together: ${STATUS_WORD[c.overall.status].toLowerCase()} of their limits`}</StatusPill>}
          </div>
          <div style={{ fontSize: 14 }}>{c.word}</div>

          {c.cards.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {c.cards.map((card) => (
                <div key={card.source} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: 6, fontSize: 13 }}>
                    <strong>{card.name}</strong>
                    <span className="num" style={{ color: "var(--ink-2)" }}>{card.credit_limit ? `${money(card.balance)} of ${money(card.credit_limit)}` : money(card.balance)}</span>
                  </div>
                  {card.credit_limit && card.share !== null ? (
                    <div role="img" aria-label={`${card.name}: ${Math.round(card.share * 100)}% of its limit used; marks at 10% and 30%`} style={{ position: "relative", height: 14, borderRadius: 7, background: "var(--surface-2)", overflow: "hidden" }}>
                      <div style={{ width: `${Math.min(100, card.share * 100)}%`, height: "100%", background: "var(--debt)", borderRadius: 7 }} />
                      <span aria-hidden="true" style={{ position: "absolute", left: "10%", top: 0, bottom: 0, width: 2, background: "var(--ink-2)" }} />
                      <span aria-hidden="true" style={{ position: "absolute", left: "30%", top: 0, bottom: 0, width: 2, background: "var(--ink)" }} />
                    </div>
                  ) : null}
                  <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, fontSize: 12, color: "var(--ink-2)" }}>
                    {card.status && <StatusPill status={card.status}>{STATUS_WORD[card.status]}</StatusPill>}
                    <span>{card.word}</span>
                  </div>
                </div>
              ))}
              {c.cards.some((card) => card.credit_limit) && <div style={{ fontSize: 12, color: "var(--muted)" }}>The thin marks are 10% and 30% of each limit. A card reports its balance on the day its statement closes; paying before then lowers what it reports.</div>}
            </div>
          )}
          {c.cards.length === 0 && <div style={{ fontSize: 13, color: "var(--ink-2)" }}>No credit card is on file. <Link to="/settings/connectors">Connect one</Link> or add its terms here on Debts.</div>}

          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>Bills paid on time, the last year</h3>
            {c.payments.total > 0 ? (
              <>
                <div role="img" aria-label={`${c.payments.on_time} on time, ${c.payments.late} late, ${c.payments.not_seen} not seen`} style={{ display: "flex", flexWrap: "wrap", gap: 3 }}>
                  {Array.from({ length: c.payments.on_time }, (_, i) => <span key={`on${i}`} style={{ width: 12, height: 12, borderRadius: 3, background: "var(--safe)" }} />)}
                  {Array.from({ length: c.payments.late }, (_, i) => <span key={`late${i}`} style={{ width: 12, height: 12, borderRadius: 3, background: "var(--warning)" }} />)}
                  {Array.from({ length: c.payments.not_seen }, (_, i) => <span key={`miss${i}`} style={{ width: 12, height: 12, borderRadius: 3, background: "var(--critical)" }} />)}
                </div>
                <div style={{ fontSize: 13, color: "var(--ink-2)", display: "flex", flexWrap: "wrap", gap: "4px 14px" }}>
                  <span><CircleCheck size={13} aria-hidden="true" style={{ display: "inline-block", verticalAlign: -2, color: "var(--safe-ink)" }} /> {c.payments.on_time} on time</span>
                  {c.payments.late > 0 && <span><CircleX size={13} aria-hidden="true" style={{ display: "inline-block", verticalAlign: -2, color: "var(--warning-ink)" }} /> {c.payments.late} late</span>}
                  {c.payments.not_seen > 0 && <span><CircleX size={13} aria-hidden="true" style={{ display: "inline-block", verticalAlign: -2, color: "var(--critical-ink)" }} /> {c.payments.not_seen} with no payment seen</span>}
                  <span>{c.payments.streak} in a row on time</span>
                </div>
                {c.payments.late_rows.length > 0 && (
                  <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: "var(--ink-2)" }}>
                    {c.payments.late_rows.map((r) => <li key={r.source}>{r.name}, due {shortDate(r.due_date)}: {r.paid_date ? `paid ${shortDate(r.paid_date)}` : "no payment seen"}</li>)}
                  </ul>
                )}
              </>
            ) : (
              <div style={{ fontSize: 13, color: "var(--ink-2)" }}>No bill payments matched yet.</div>
            )}
          </div>
          <div style={{ fontSize: 12, color: "var(--muted)" }}>The app cannot see {c.cannot_see.join(", ")}. {c.not_advice}</div>
        </section>
      )}
    </Show>
  );
}
