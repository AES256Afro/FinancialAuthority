// Accounts: every balance with where it came from and how fresh it is, and net worth as what you have less
// what you owe. The engine's numbers; nothing computed here.
import { Link } from "react-router-dom";
import { useApi } from "../api";
import { Failed, Loading } from "../components/State";
import { StatusPill } from "../components/Status";
import { money, when } from "../format";

interface Item {
  id: number;
  name: string;
  institution?: string;
  kind: string;
  balance: number | null;
  balance_at?: string | null;
  age_days?: number | null;
  connector?: string;
  apr?: number | null;
  minimum?: number | null;
  source: string;
}

interface Overview {
  as_of: string;
  assets: { total: number; items: Item[] };
  debts: { total: number; items: Item[] };
  net: number;
  stale: string[];
  not_advice: string;
}

const KIND_WORD: Record<string, string> = { checking: "checking", savings: "savings", cash: "cash", investment: "investment", card: "card", loan: "loan", bnpl: "installment plan", advance: "advance", medical: "medical" };
const CONNECTOR_WORD: Record<string, string> = { plaid: "Plaid", teller: "Teller", rocketmoney: "Rocket Money export", demo: "demo data", manual: "entered by hand", mail: "mail" };

function freshness(at: string | null | undefined, days: number | null | undefined): string {
  if (!at || days === null || days === undefined) return "no balance yet";
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  return `${days} days ago`;
}

function Group({ title, items, total, tone }: { title: string; items: Item[]; total: number; tone: string }) {
  return (
    <section className="card" style={{ padding: "6px 14px", overflowX: "auto" }}>
      <table className="ledger">
        <thead><tr><th>{title}</th><th>Kind</th><th>From</th><th>Balance as of</th><th style={{ textAlign: "right" }}>Balance</th></tr></thead>
        <tbody>
          {items.map((a) => (
            <tr key={a.source}>
              <td><strong>{a.name}</strong>{a.institution ? <span style={{ color: "var(--muted)" }}> · {a.institution}</span> : null}</td>
              <td style={{ color: "var(--ink-2)" }}>{KIND_WORD[a.kind] ?? a.kind}</td>
              <td style={{ color: "var(--ink-2)" }}>{a.connector ? CONNECTOR_WORD[a.connector] ?? a.connector : "added in the app"}</td>
              <td style={{ color: "var(--ink-2)" }} title={a.balance_at ? when(a.balance_at) : undefined}>{a.connector ? freshness(a.balance_at, a.age_days) : "—"}</td>
              <td className="num" style={{ textAlign: "right", fontWeight: 700 }}>{a.balance === null || a.balance === undefined ? "—" : money(a.balance)}</td>
            </tr>
          ))}
          {items.length === 0 && <tr><td colSpan={5} style={{ color: "var(--ink-2)" }}>Nothing here yet. <Link to="/settings/connectors">Connect a bank on Settings › Connections</Link>.</td></tr>}
          <tr><td colSpan={4} style={{ fontWeight: 800 }}>Total</td><td className="num" style={{ textAlign: "right", fontWeight: 800, color: tone }}>{money(total)}</td></tr>
        </tbody>
      </table>
    </section>
  );
}

export function Accounts() {
  const { data, error, reload } = useApi<Overview>("/api/v1/accounts/overview");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 980 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Accounts</h1>
      {error && !data && <Failed error={error} what="your accounts" onRetry={reload} />}
      {!data && !error && <Loading what="your accounts" />}
      {data && (
        <>
          <section className="card" style={{ padding: 16, display: "flex", gap: 24, flexWrap: "wrap", alignItems: "baseline" }}>
            <div><div className="lab">Net worth</div><div className="num" style={{ fontSize: 34, fontWeight: 800, letterSpacing: "-0.02em", lineHeight: 1.1, color: data.net < 0 ? "var(--critical-ink)" : "var(--ink)" }}>{money(data.net)}</div></div>
            <div><div className="lab">You have</div><div className="num" style={{ fontSize: 22, fontWeight: 800, color: "var(--safe-ink)" }}>{money(data.assets.total)}</div></div>
            <div><div className="lab">You owe</div><div className="num" style={{ fontSize: 22, fontWeight: 800, color: "var(--debt-ink)" }}>{money(data.debts.total)}</div></div>
            {data.stale.length > 0 && <StatusPill status="warn">Balance may be out of date: {data.stale.join(", ")}</StatusPill>}
          </section>
          <Group title="What you have" items={data.assets.items} total={data.assets.total} tone="var(--safe-ink)" />
          <Group title="What you owe" items={data.debts.items} total={data.debts.total} tone="var(--debt-ink)" />
          <div style={{ fontSize: 12, color: "var(--muted)" }}>{data.not_advice} Live balances come from your bank connections; the rest from what you added in the app and from exports.</div>
        </>
      )}
    </div>
  );
}
