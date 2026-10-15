import { useState } from "react";
import { useApi } from "../api";
import { LedgerRules } from "../components/LedgerRules";
import { money, shortDate } from "../format";
import type { TransactionRow } from "../types";

export function Transactions() {
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const params = new URLSearchParams({ limit: "100" });
  if (query) params.set("text", query);
  const { data, loading, error, reload } = useApi<{ returned: number; total_amount: number; transactions: TransactionRow[] }>(`/api/v1/transactions/search?${params}`);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
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
      </form>
      <LedgerRules onChanged={reload} />
      <div className="card" style={{ padding: "12px 16px", overflowX: "auto" }}>
        {loading && <div style={{ color: "var(--muted)" }}>Loading…</div>}
        {error && <div style={{ color: "var(--critical)" }}>{error.message}</div>}
        {data && (
          <>
            <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8 }}>{data.returned} rows · spending shown; transfers, card payments and income hidden · total {money(data.total_amount)}</div>
            <table className="ledger">
              <thead><tr><th>When</th><th>Merchant</th><th>Category</th><th>Account</th><th style={{ textAlign: "right" }}>Amount</th></tr></thead>
              <tbody>
                {data.transactions.map((t) => (
                  <tr key={t.id}>
                    <td style={{ whiteSpace: "nowrap" }}>{shortDate(t.date)}</td>
                    <td>{t.merchant}</td>
                    <td style={{ color: t.category === "Borrowing" ? "var(--critical)" : "var(--ink-2)", fontWeight: t.category === "Borrowing" ? 800 : 400 }}>{t.category}{t.category === "Borrowing" ? " · avoid" : ""}</td>
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
