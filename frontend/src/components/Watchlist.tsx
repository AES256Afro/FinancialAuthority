// The don't-buy list: what to hold off on and why, from the engine, each with where to look. Beside it, "Worth a
// look": what the app noticed in your transactions, each finding with its rows and one tap to say it is expected.
import { ArrowUpRight, Clock, Copy, Sparkles, TrendingUp } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { money, shortDate } from "../format";
import type { WatchFinding, WatchOut } from "../types";
import { ConfirmButton } from "./ConfirmButton";
import { placeName } from "./OneThing";
import { Failed, Loading, Said } from "./State";
import { SeverityTag } from "./Status";

interface Item {
  kind: string;
  severity: "critical" | "warning" | "info";
  text: string;
  amount: number | null;
  sources: string[];
  link: string;
}

export function Watchlist() {
  const list = useApi<{ items: Item[]; not_advice: string }>("/api/v1/watchlist");
  if (list.error && !list.data) return <Failed error={list.error} what="what to hold off on" onRetry={list.reload} />;
  if (!list.data) return <Loading what="what to hold off on" />;
  const data = list.data;
  return (
    <section className="card" style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 4 }}>
      <h2 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>Hold off on</h2>
      {data.items.length === 0 && <div style={{ fontSize: 13, color: "var(--muted)" }}>Nothing to hold off on right now.</div>}
      {data.items.map((i, k) => (
        <div key={k} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 0", borderTop: k === 0 ? 0 : "1px solid var(--surface-2)", flexWrap: "wrap" }}>
          <SeverityTag severity={i.severity} />
          <div style={{ flex: "1 1 200px", minWidth: 0, fontSize: 13, fontWeight: i.severity === "info" ? 500 : 700 }}>{i.text}</div>
          {i.link && <Link to={i.link} className="btn" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", minHeight: 32, padding: "4px 10px" }} aria-label={`Open ${placeName(i.link)}: ${i.text}`}>→</Link>}
        </div>
      ))}
      <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>{data.not_advice}</div>
    </section>
  );
}

// Each kind of finding has its own icon, so the list can be scanned before it is read, and its amount in words:
// the engine's figure, said for what it is.
const KIND: Record<WatchFinding["kind"], { Icon: typeof Copy; amount: (n: number) => string }> = {
  unusual: { Icon: TrendingUp, amount: (n) => `${money(n, { cents: true })} more than usual` },
  double: { Icon: Copy, amount: (n) => `${money(n, { cents: true })} charged a second time` },
  new: { Icon: Sparkles, amount: (n) => `${money(n, { cents: true })} each time` },
  rise: { Icon: ArrowUpRight, amount: (n) => `${money(n, { cents: true })} more each time` },
};

type Note = { text: string; tone: "info" | "error" } | null;

// Worth a look: a charge far above what a merchant usually takes, the same charge twice, a new regular charge, a
// price that went up. Hidden when there is nothing. "This is expected" keeps a finding off the list for good, so
// it asks first.
export function WorthALook() {
  const state = useApi<WatchOut>("/api/v1/watch");
  const [said, setSaid] = useState<Note>(null);
  if (state.error && !state.data) return <Failed error={state.error} what="what is worth a look" onRetry={state.reload} />;
  if (!state.data) return <Loading what="what is worth a look" />;
  const data = state.data;
  if (data.items.length === 0 && !said) return null;

  async function expected(f: WatchFinding) {
    setSaid(null);
    await api<WatchOut>(`/api/v1/watch/${encodeURIComponent(f.key)}/expected`, { method: "POST" });
    setSaid({ text: `Marked as expected: ${f.title}. It stays off this list.`, tone: "info" });
    state.reload();
  }

  return (
    <section id="worth-a-look" className="card" aria-labelledby="watch-title" style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2 id="watch-title" style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>Worth a look</h2>
        {data.items.length > 0 && <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{data.items.length === 1 ? "1 thing the app noticed in your transactions" : `${data.items.length} things the app noticed in your transactions`}</span>}
      </div>
      {data.items.length === 0 && <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Nothing else to look at right now.</div>}
      {data.items.map((f, k) => {
        const kind = KIND[f.kind] ?? KIND.unusual;
        return (
          <div key={f.key} style={{ display: "flex", flexDirection: "column", gap: 6, padding: "10px 0", borderTop: k === 0 ? 0 : "1px solid var(--surface-2)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span aria-hidden="true" style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 28, height: 28, borderRadius: 8, background: "var(--surface-2)", color: "var(--ink-2)", flexShrink: 0 }}>
                <kind.Icon size={16} />
              </span>
              <SeverityTag severity={f.severity} />
              <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, flex: "1 1 200px", minWidth: 0 }}>{f.title}</h3>
              <strong className="num" style={{ fontSize: 13 }}>{kind.amount(f.amount)}</strong>
            </div>
            <div style={{ fontSize: 13, color: "var(--ink-2)" }}>{f.text}</div>
            {f.rows.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                {f.rows.map((r) => (
                  <div key={r.id} style={{ display: "flex", alignItems: "baseline", gap: 10, padding: "4px 0 4px 10px", borderLeft: "3px solid var(--bill)", fontSize: 13, flexWrap: "wrap" }}>
                    <span style={{ color: "var(--ink-2)", whiteSpace: "nowrap" }}>{shortDate(r.date)}</span>
                    <span style={{ flex: "1 1 140px", minWidth: 0 }}>{r.merchant}{r.account ? <span style={{ color: "var(--muted)" }}> · {r.account}</span> : null}</span>
                    {r.pending && (
                      <span title="The bank has not finished it yet. It is already taken off the bank's balance." style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: "1px 7px", borderRadius: 999, fontSize: 11, fontWeight: 800, whiteSpace: "nowrap", background: "var(--paycheck-soft)", color: "var(--paycheck-ink)" }}>
                        <Clock size={11} strokeWidth={2.4} aria-hidden="true" />Pending
                      </span>
                    )}
                    <strong className="num">{money(r.amount, { cents: true })}</strong>
                  </div>
                ))}
              </div>
            )}
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              {f.link && <Link to={f.link} className="btn" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", minHeight: 32, padding: "4px 10px" }} aria-label={`Open ${placeName(f.link)}: ${f.title}`}>Open {placeName(f.link)}</Link>}
              <ConfirmButton
                label="This is expected"
                question="Keep this off the list? It will not come back."
                yes="Yes, it is expected"
                no="Keep it here"
                onConfirm={() => expected(f)}
                style={{ minHeight: 32, padding: "4px 10px" }}
              />
            </div>
          </div>
        );
      })}
      <Said text={said?.text ?? null} tone={said?.tone} />
      <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>{data.not_advice}</div>
    </section>
  );
}
