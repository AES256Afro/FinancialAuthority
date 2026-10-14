// Ledger rules: when the merchant contains this, call it that, put it in a category, or hide it.
// A rule runs over the rows already there and over everything a connector adds later.
import { useState } from "react";
import { api, useApi } from "../api";

interface RuleRow {
  id: number;
  kind: "rename" | "categorise" | "hide";
  match_field: string;
  pattern: string;
  value: string;
}

const KIND_WORD = { rename: "call it", categorise: "put it in", hide: "hide it" };

export function LedgerRules({ onChanged }: { onChanged: () => void }) {
  const { data, reload } = useApi<{ rules: RuleRow[] }>("/api/v1/ledger/rules");
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<RuleRow["kind"]>("rename");
  const [pattern, setPattern] = useState("");
  const [value, setValue] = useState("");
  const [note, setNote] = useState<string | null>(null);

  async function make(e: React.FormEvent) {
    e.preventDefault();
    try {
      const r = await api<{ changed: number }>("/api/v1/ledger/rules", { method: "POST", body: JSON.stringify({ kind, pattern, value, match_field: "merchant" }) });
      setNote(`Rule kept; ${r.changed} row${r.changed === 1 ? "" : "s"} changed. New rows will follow it.`);
      setPattern("");
      setValue("");
      reload();
      onChanged();
    } catch (err) {
      setNote((err as Error).message);
    }
  }
  async function remove(id: number) {
    await api(`/api/v1/ledger/rules/${id}`, { method: "DELETE" });
    reload();
  }

  const rules = data?.rules ?? [];
  return (
    <div className="card" style={{ padding: "10px 14px", display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <strong style={{ fontSize: 13 }}>Rules</strong>
        <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{rules.length === 0 ? "none yet; a rule cleans a merchant once and keeps it clean" : `${rules.length} standing`}</span>
        <div style={{ flexGrow: 1 }} />
        <button className="btn" type="button" style={{ minHeight: 32, padding: "4px 10px" }} onClick={() => setOpen(!open)}>{open ? "Close" : "Make a rule"}</button>
      </div>
      {open && (
        <form onSubmit={make} style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", fontSize: 13 }}>
          <span>When the merchant contains</span>
          <input className="input" value={pattern} onChange={(e) => setPattern(e.target.value)} placeholder="AMZN Mktp" required style={{ minHeight: 34, padding: "4px 10px", flex: "1 1 140px" }} aria-label="Text to match" />
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value as RuleRow["kind"])} style={{ minHeight: 34, padding: "4px 8px" }} aria-label="What to do">
            <option value="rename">call it</option>
            <option value="categorise">put it in</option>
            <option value="hide">hide it</option>
          </select>
          {kind !== "hide" && <input className="input" value={value} onChange={(e) => setValue(e.target.value)} placeholder={kind === "rename" ? "Amazon" : "Shopping"} required style={{ minHeight: 34, padding: "4px 10px", flex: "1 1 120px" }} aria-label="Value" />}
          <button className="btn dark" type="submit" style={{ minHeight: 34, padding: "4px 10px" }}>Keep the rule</button>
        </form>
      )}
      {note && <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{note}</div>}
      {open && rules.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
          {rules.map((r) => (
            <div key={r.id} style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span style={{ flexGrow: 1 }}>merchant contains <code>{r.pattern}</code> → {KIND_WORD[r.kind]}{r.value ? <> <strong>{r.value}</strong></> : null}</span>
              <button className="btn" type="button" style={{ minHeight: 28, padding: "2px 8px", fontSize: 12 }} onClick={() => remove(r.id)}>Remove</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
