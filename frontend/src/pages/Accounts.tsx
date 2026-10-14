// Accounts: every balance with where it came from and how fresh it is, and net worth as what you have less
// what you owe. The engine's numbers; nothing computed here.
import { useApi } from "../api";
import { money } from "../format";

interface Item {
  id: number;
  name: string;
  institution?: string;
  kind: string;
  balance: number | null;
  balance_at?: string | null;
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

function freshness(at: string | null | undefined): string {
  if (!at) return "no balance yet";
  const days = Math.floor((Date.now() - new Date(at).getTime()) / 86400000);
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
              <td style={{ color: "var(--ink-2)" }}>{a.connector ? CONNECTOR_WORD[a.connector] ?? a.connector : "register"}</td>
              <td style={{ color: "var(--ink-2)" }}>{a.connector ? freshness(a.balance_at) : "—"}</td>
              <td className="num" style={{ textAlign: "right", fontWeight: 700 }}>{a.balance === null || a.balance === undefined ? "—" : money(a.balance)}</td>
            </tr>
          ))}
          {items.length === 0 && <tr><td colSpan={5} style={{ color: "var(--muted)" }}>Nothing here yet.</td></tr>}
          <tr><td colSpan={4} style={{ fontWeight: 800 }}>Total</td><td className="num" style={{ textAlign: "right", fontWeight: 800, color: tone }}>{money(total)}</td></tr>
        </tbody>
      </table>
    </section>
  );
}

export function Accounts() {
  const { data, error } = useApi<Overview>("/api/v1/accounts/overview");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 980 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Accounts</h1>
      {error && <div style={{ color: "var(--critical)" }}>{error.message}</div>}
      {data && (
        <>
          <section className="card" style={{ padding: 16, display: "flex", gap: 24, flexWrap: "wrap", alignItems: "baseline" }}>
            <div><div className="lab">Net worth</div><div className="num" style={{ fontSize: 34, fontWeight: 800, letterSpacing: "-0.02em", lineHeight: 1.1, color: data.net < 0 ? "var(--critical)" : "var(--ink)" }}>{money(data.net)}</div></div>
            <div><div className="lab">You have</div><div className="num" style={{ fontSize: 22, fontWeight: 800, color: "var(--safe)" }}>{money(data.assets.total)}</div></div>
            <div><div className="lab">You owe</div><div className="num" style={{ fontSize: 22, fontWeight: 800, color: "var(--debt)" }}>{money(data.debts.total)}</div></div>
            {data.stale.length > 0 && <div style={{ fontSize: 12, color: "var(--warning)", fontWeight: 700 }}>Stale: {data.stale.join(", ")}</div>}
          </section>
          <Group title="What you have" items={data.assets.items} total={data.assets.total} tone="var(--safe)" />
          <Group title="What you owe" items={data.debts.items} total={data.debts.total} tone="var(--debt)" />
          <div style={{ fontSize: 12, color: "var(--muted)" }}>{data.not_advice} Live balances come from the bank connectors; the rest from the registers and exports.</div>
        </>
      )}
    </div>
  );
}
