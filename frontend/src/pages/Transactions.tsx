import { useState } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../api";
import { LedgerRules } from "../components/LedgerRules";
import { Empty, Failed, Loading } from "../components/State";
import { money, shortDate } from "../format";
import type { TransactionRow } from "../types";

export function Transactions() {
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const params = new URLSearchParams({ limit: "100" });
  if (query) params.set("text", query);
  const { data, error, reload } = useApi<{ returned: number; total_amount: number; transactions: TransactionRow[] }>(`/api/v1/transactions/search?${params}`);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", gap: 12 }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Transactions</h1>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>What you spent, from every account the app reads.</div>
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(text.trim());
        }}
        style={{ display: "flex", gap: 8, flexWrap: "wrap" }}
      >
        <label htmlFor="q" className="lab" style={{ alignSelf: "center" }}>Search</label>
        <input id="q" className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="merchant, description, note" style={{ flex: "1 1 240px" }} />
        <button className="btn dark" type="submit">Find</button>
        {query && <button className="btn" type="button" onClick={() => { setText(""); setQuery(""); }}>Clear the search</button>}
      </form>
      <LedgerRules onChanged={reload} />
      <div className="card" style={{ padding: "12px 16px", overflowX: "auto" }}>
        {error && !data && <Failed error={error} what="your transactions" onRetry={reload} card={false} />}
        {!data && !error && <Loading what="your transactions" card={false} />}
        {data && data.transactions.length === 0 && (query
          ? <Empty card={false}>No transactions match “{query}”. Try one word from the merchant's name, or clear the search.</Empty>
          : <Empty card={false} to="/settings/connectors#import" action="Import a file">No transactions yet. Connect a bank or import a statement on Settings › Connections.</Empty>)}
        {data && data.transactions.length > 0 && (
          <>
            <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8 }}>{data.returned} shown · spending only; transfers, card payments and income are hidden · total {money(data.total_amount)}</div>
            <table className="ledger">
              <thead><tr><th>When</th><th>Merchant</th><th>Category</th><th>Account</th><th style={{ textAlign: "right" }}>Amount</th></tr></thead>
              <tbody>
                {data.transactions.map((t) => (
                  <tr key={t.id}>
                    <td style={{ whiteSpace: "nowrap" }}>{shortDate(t.date)}</td>
                    <td>{t.merchant}</td>
                    <td style={{ color: "var(--ink-2)" }}>{t.category === "Borrowing" ? <Link to="/borrowing" style={{ fontWeight: 700 }}>Borrowing</Link> : t.category}</td>
                    <td style={{ color: "var(--ink-2)" }}>{t.account}</td>
                    <td className="num" style={{ textAlign: "right" }}>{money(t.amount, { cents: true })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  );
}
